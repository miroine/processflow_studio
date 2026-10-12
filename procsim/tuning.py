"""Parameter estimation (calibration to plant data) and optimisation on the whole flowsheet.

Both work the same way: a vector of unit parameters is written into a copy of the model, the flowsheet is solved, and numbers
are read back from the solution - unit results, stream properties or the energy / CO2 totals.

* ``calibrate`` - weighted non-linear least squares of the chosen parameters (compressor efficiency, heat-transfer
  coefficient, roughness, pressure drop, permeance ...) against measurements taken in one or several operating cases.  A case sets
  the operating point (feed rate, temperatures, set-points) and lists what was measured.  Cases flagged ``validate`` are not
  used in the fit; they show how well the calibrated model predicts conditions it has not seen.  The result carries before / after
  residuals, parameter confidence intervals from the Jacobian and a warning for parameters the data cannot separate.
* ``optimise`` - minimise or maximise one result with respect to a few unit parameters, subject to limits on other results
  (SLSQP on the scaled variables; every evaluation is a flowsheet solve, so keep the number of variables small).

References are by *name*: unit name + parameter key, and (kind, object name, quantity) for results.
"""
from __future__ import annotations

import copy
import math

import numpy as np

from .flowsheet import solve, build_fluid
from .streams import stream_properties
from .unitops import CATALOGUE

PENALTY = 1e3


def unit_id(model, name):
    for uid, u in model["units"].items():
        if u["name"] == name:
            return uid
    raise KeyError(f"no unit named {name!r}")


def stream_id(model, name):
    for sid, s in model["streams"].items():
        if s["name"] == name:
            return sid
    raise KeyError(f"no stream named {name!r}")


def float_params(utype):
    """[(key, label, unit, min, max)] of the numeric parameters of a unit type."""
    return [(s["key"], s["label"], s.get("unit", ""), s.get("min"), s.get("max"))
            for s in CATALOGUE[utype]["params"] if s["kind"] == "float"]


def get_param(model, uname, key):
    return float(model["units"][unit_id(model, uname)]["params"][key])


def set_param(model, uname, key, value):
    model["units"][unit_id(model, uname)]["params"][key] = float(value)


# --------------------------------------------------------------------------------------------------------- reading results
def measure(model, sol, ref):
    """Value of ref = {"kind": "unit" | "stream" | "economics", "obj": name, "q": quantity} in a solved model, or None."""
    kind, q = ref["kind"], ref["q"]
    try:
        if kind == "unit":
            v = (sol.results.get(unit_id(model, ref["obj"])) or {}).get(q)
        elif kind == "stream":
            sid = stream_id(model, ref["obj"])
            st = sol.streams.get(sid)
            if st is None or st.empty:
                return None
            if q.startswith("mol% "):                       # composition, e.g. "mol% CO2"
                k = q[5:].strip()
                v = 100.0 * float(st.z[sol.fp.keys.index(k)])
            elif q.startswith("ppmv "):
                k = q[5:].strip()
                v = 1e6 * float(st.z[sol.fp.keys.index(k)])
            else:
                v = stream_properties(st, sol.fp).get(q)
        elif kind == "economics":
            from . import economics
            v = economics.compute(model, sol)["totals"].get(q)
        else:
            return None
    except (KeyError, ValueError, IndexError):
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def quantities(model, sol, kind, obj):
    """Numeric quantities that can be measured for a unit / stream of a solved model."""
    out = []
    try:
        if kind == "unit":
            out = [k for k, v in (sol.results.get(unit_id(model, obj)) or {}).items() if isinstance(v, (int, float, np.floating)) and not isinstance(v, bool)]
        elif kind == "stream":
            st = sol.streams.get(stream_id(model, obj))
            if st is not None and not st.empty:
                props = stream_properties(st, sol.fp)
                out = [k for k, v in props.items() if isinstance(v, (int, float, np.floating)) and not isinstance(v, bool)]
                out += [f"mol% {k}" for k in sol.fp.keys]
        elif kind == "economics":
            from . import economics
            out = list(economics.compute(model, sol)["totals"].keys())
    except KeyError:
        pass
    return out


def _apply(model, assigns):
    m = copy.deepcopy(model)
    for uname, key, val in assigns:
        uid = unit_id(m, uname)
        if key not in m["units"][uid]["params"]:
            raise KeyError(f"{uname} has no parameter {key!r}")
        m["units"][uid]["params"][key] = float(val) if isinstance(m["units"][uid]["params"][key], (int, float)) and not isinstance(m["units"][uid]["params"][key], bool) else val
    return m


class _Evaluator:
    """Solves the cases for a parameter vector, with a small cache (finite differences revisit points)."""

    def __init__(self, model, cases, params, fp):
        self.model, self.cases, self.params, self.fp = model, cases, params, fp
        self.cache = {}
        self.n_solves = 0
        self.failed = set()

    def predictions(self, theta):
        key = tuple(round(float(t), 12) for t in theta)
        if key in self.cache:
            return self.cache[key]
        base = [(p["unit"], p["key"], v) for p, v in zip(self.params, theta)]
        out = []
        for ci, case in enumerate(self.cases):
            ov = [(o["unit"], o["key"], o["value"]) for o in case.get("set", [])]
            m = _apply(self.model, base + ov)
            try:
                sol = solve(m, self.fp)
                self.n_solves += 1
                ok = bool(sol.converged) and not any(v in ("error", "missing") for v in sol.status.values())
            except Exception:           # noqa: BLE001
                sol, ok = None, False
            if not ok:
                self.failed.add(ci)
            out.append([measure(m, sol, ms) if sol is not None else None for ms in case.get("measure", [])])
        self.cache[key] = out
        return out


def _sigma(ms, value):
    s = ms.get("sigma")
    if s is not None and float(s) > 0:
        return float(s)
    return max(0.01 * abs(value), 1e-6)


# ------------------------------------------------------------------------------------------------------------ calibration
def calibrate(model, spec, progress=None):
    """Fit ``spec["params"]`` to ``spec["cases"]``.

    params: [{"unit", "key", "lo", "hi"}]   cases: [{"name", "validate": bool, "set": [{"unit","key","value"}],
    "measure": [{"kind","obj","q","value","sigma"}]}]   returns a result dict (see keys below)."""
    from scipy.optimize import least_squares
    params = spec["params"]
    cases = spec["cases"]
    if not params:
        raise ValueError("choose at least one parameter to calibrate")
    fp = build_fluid(model)
    x0 = np.array([get_param(model, p["unit"], p["key"]) for p in params], float)
    lo = np.array([float(p["lo"]) if p.get("lo") is not None else -np.inf for p in params])
    hi = np.array([float(p["hi"]) if p.get("hi") is not None else np.inf for p in params])
    x_orig = np.minimum(np.maximum(x0, lo), hi)
    span = np.where(np.isfinite(lo) & np.isfinite(hi), hi - lo, 0.0)
    x0 = np.minimum(np.maximum(x0, lo + 1e-3 * span), hi - 1e-3 * span)     # strictly inside: a start on a bound gives no gradient
    fit_cases = [c for c in cases if not c.get("validate")]
    n_meas = sum(len(c.get("measure", [])) for c in fit_cases)
    if n_meas < len(params):
        raise ValueError(f"{n_meas} measurements in the fit cases for {len(params)} parameters: the problem is under-determined")
    ev_fit = _Evaluator(model, fit_cases, params, fp)
    scale = np.where(np.abs(x0) > 0, np.abs(x0), 1.0)

    def residuals(xs):
        theta = xs * scale
        preds = ev_fit.predictions(theta)
        r = []
        for case, pr in zip(fit_cases, preds):
            for ms, v in zip(case.get("measure", []), pr):
                r.append(PENALTY if v is None else (v - float(ms["value"])) / _sigma(ms, float(ms["value"])))
        if progress:
            progress(ev_fit.n_solves)
        return np.array(r)

    first = ev_fit.predictions(x0)
    missing = [f"{c.get('name', '')}: {ms['kind']} {ms['obj']!r} / {ms['q']!r}" for c, pr in zip(fit_cases, first)
               for ms, v in zip(c.get("measure", []), pr) if v is None]
    if missing:
        raise ValueError("these measurements cannot be read from the solved model (wrong name or quantity, or the case does not "
                         "solve): " + "; ".join(missing[:6]))
    r0 = residuals(x0 / scale)
    res = least_squares(residuals, x0 / scale, bounds=(lo / scale, hi / scale), diff_step=1e-3,
                        max_nfev=int(spec.get("max_nfev", 30)), x_scale=1.0)
    xf = res.x * scale
    rf = residuals(res.x)
    n, k = len(rf), len(params)
    dof = max(n - k, 1)
    chi2 = float(rf @ rf)
    J = res.jac
    notes = []
    ci = [None] * k
    sv = np.linalg.svd(J, compute_uv=False) if J.size else np.array([])
    cond = float(sv[0] / sv[-1]) if len(sv) and sv[-1] > 0 else float("inf")
    try:
        cov_s = np.linalg.inv(J.T @ J) * max(chi2 / dof, 1.0)           # inflate when the fit is worse than the stated sigmas
        cov = cov_s * np.outer(scale, scale)
        ci = [1.96 * math.sqrt(max(cov[i, i], 0.0)) for i in range(k)]
        corr = cov / np.sqrt(np.outer(np.diag(cov), np.diag(cov)))
        for i in range(k):
            for j in range(i + 1, k):
                if abs(corr[i, j]) > 0.95:
                    notes.append(f"{params[i]['unit']}.{params[i]['key']} and {params[j]['unit']}.{params[j]['key']} are strongly "
                                 f"correlated ({corr[i, j]:+.2f}): the data cannot separate them - fit one and fix the other")
    except np.linalg.LinAlgError:
        notes.append("the sensitivity matrix is singular: at least one parameter has no effect on the measured quantities")
    for i, p in enumerate(params):
        if np.isclose(xf[i], lo[i], rtol=1e-6) or np.isclose(xf[i], hi[i], rtol=1e-6):
            notes.append(f"{p['unit']}.{p['key']} ended on its bound ({xf[i]:.4g}): widen the range or check the data")
        if J.size and np.max(np.abs(J[:, i])) < 1e-9:
            notes.append(f"{p['unit']}.{p['key']} has no effect on the measured quantities")
    if ev_fit.failed:
        notes.append("the flowsheet did not solve in case(s): " + ", ".join(fit_cases[i].get("name", str(i)) for i in sorted(ev_fit.failed)))
    # all cases (fit and validate) before / after
    all_before = _Evaluator(model, cases, params, fp).predictions(x_orig)
    ev_all = _Evaluator(model, cases, params, fp)
    all_after = ev_all.predictions(xf)
    rows = []
    for case, pb, pa in zip(cases, all_before, all_after):
        for ms, vb, va in zip(case.get("measure", []), pb, pa):
            meas = float(ms["value"])
            s = _sigma(ms, meas)
            rows.append({"Case": case.get("name", ""), "Use": "validation" if case.get("validate") else "fit",
                         "Kind": ms["kind"], "Object": ms["obj"], "Quantity": ms["q"], "Measured": meas, "σ": s,
                         "Before": vb, "After": va,
                         "Error before": None if vb is None else vb - meas, "Error after": None if va is None else va - meas,
                         "z before": None if vb is None else (vb - meas) / s, "z after": None if va is None else (va - meas) / s})

    def rmse(use, col):
        v = [r[col] for r in rows if r["Use"] == use and r[col] is not None]
        return math.sqrt(sum(e * e for e in v) / len(v)) if v else None

    def zr(use, col):
        v = [r[col] for r in rows if r["Use"] == use and r[col] is not None]
        return math.sqrt(sum(e * e for e in v) / len(v)) if v else None
    if cond > 1e6 and not any("singular" in s for s in notes):
        notes.append(f"the parameters are nearly indistinguishable in this data (condition number {cond:.1e})")
    return {"params": [{"unit": p["unit"], "key": p["key"], "before": float(x_orig[i]), "after": float(xf[i]), "ci95": ci[i],
                        "lo": None if not np.isfinite(lo[i]) else float(lo[i]), "hi": None if not np.isfinite(hi[i]) else float(hi[i])}
                       for i, p in enumerate(params)],
            "rows": rows, "chi2_before": float(r0 @ r0), "chi2_after": chi2, "n": n, "dof": dof,
            "rms_z_fit_before": zr("fit", "z before"), "rms_z_fit_after": zr("fit", "z after"),
            "rms_z_val_before": zr("validation", "z before"), "rms_z_val_after": zr("validation", "z after"),
            "nfev": int(res.nfev), "solves": ev_fit.n_solves + ev_all.n_solves, "success": bool(res.success),
            "message": str(res.message), "notes": notes, "cond": cond}


def apply_calibration(model, result):
    """Write the fitted values into the model (returns the number of parameters written)."""
    for p in result["params"]:
        set_param(model, p["unit"], p["key"], p["after"])
    model["calibration_applied"] = [{"unit": p["unit"], "key": p["key"], "before": p["before"], "value": p["after"], "ci95": p["ci95"]} for p in result["params"]]
    model["calibration_summary"] = {"rms_fit_before": result.get("rms_z_fit_before"), "rms_fit_after": result.get("rms_z_fit_after"),
                                    "rms_val_before": result.get("rms_z_val_before"), "rms_val_after": result.get("rms_z_val_after"),
                                    "n_cases": len({r.get("Case") for r in result.get("rows", [])}), "notes": list(result.get("notes", []))}
    return len(result["params"])


# ------------------------------------------------------------------------------------------------------------ optimisation
def optimise(model, spec, progress=None):
    """Minimise / maximise ``spec["objective"]`` over ``spec["vars"]`` subject to ``spec["constraints"]``.

    vars: [{"unit","key","lo","hi"}]   objective: {"kind","obj","q","sense": "min" | "max"}
    constraints: [{"kind","obj","q","op": "<=" | ">=","value"}]"""
    from scipy.optimize import minimize
    vars_ = spec["vars"]
    if not vars_:
        raise ValueError("choose at least one variable")
    fp = build_fluid(model)
    lo = np.array([float(v["lo"]) for v in vars_])
    hi = np.array([float(v["hi"]) for v in vars_])
    if np.any(hi <= lo):
        raise ValueError("every variable needs hi > lo")
    x0 = np.array([get_param(model, v["unit"], v["key"]) for v in vars_], float)
    x0 = np.minimum(np.maximum(x0, lo), hi)
    obj, cons = spec["objective"], spec.get("constraints", [])
    sign = 1.0 if obj.get("sense", "min") == "min" else -1.0
    cache, history = {}, []
    n_solves = [0]

    def run(u):
        key = tuple(round(float(t), 10) for t in u)
        if key in cache:
            return cache[key]
        x = lo + np.asarray(u) * (hi - lo)
        m = _apply(model, [(v["unit"], v["key"], xi) for v, xi in zip(vars_, x)])
        try:
            sol = solve(m, fp)
            n_solves[0] += 1
            ok = bool(sol.converged) and not any(s in ("error", "missing") for s in sol.status.values())
        except Exception:         # noqa: BLE001
            sol, ok = None, False
        if not ok:
            out = (None, [None] * len(cons), x)
        else:
            out = (measure(m, sol, obj), [measure(m, sol, c) for c in cons], x)
        cache[key] = out
        history.append({"x": [float(t) for t in x], "objective": out[0], "constraints": out[1]})
        if progress:
            progress(n_solves[0])
        return out

    f0, c0, _ = run((x0 - lo) / (hi - lo))
    if f0 is None:
        raise ValueError("the flowsheet does not solve at the starting point")
    fscale = max(abs(f0), 1e-9)

    def fun(u):
        v = run(u)[0]
        return PENALTY if v is None else sign * v / fscale

    def mk(i, c):
        sgn = 1.0 if c["op"] == ">=" else -1.0
        ref = max(abs(float(c["value"])), 1e-9)

        def g(u):
            v = run(u)[1][i]
            return -1.0 if v is None else sgn * (v - float(c["value"])) / ref
        return g
    constraints = [{"type": "ineq", "fun": mk(i, c)} for i, c in enumerate(cons)]
    res = minimize(fun, (x0 - lo) / (hi - lo), method="SLSQP", bounds=[(0.0, 1.0)] * len(vars_), constraints=constraints,
                   options={"maxiter": int(spec.get("max_iter", 25)), "eps": 2e-3, "ftol": 1e-7})
    # best feasible point seen (SLSQP can end on an infeasible iterate)
    best = None
    for h in history:
        if h["objective"] is None or any(c is None for c in h["constraints"]):
            continue
        ok = all((h["constraints"][i] >= float(c["value"]) - 1e-6 * max(abs(float(c["value"])), 1.0)) if c["op"] == ">=" else
                 (h["constraints"][i] <= float(c["value"]) + 1e-6 * max(abs(float(c["value"])), 1.0)) for i, c in enumerate(cons))
        if ok and (best is None or sign * h["objective"] < sign * best["objective"]):
            best = h
    notes = []
    if best is None:
        notes.append("no feasible point was found: relax a constraint or widen the variable ranges")
        best = min((h for h in history if h["objective"] is not None), key=lambda h: sign * h["objective"], default=history[0])
    for v, xv in zip(vars_, best["x"]):
        if np.isclose(xv, v["lo"], rtol=1e-6) or np.isclose(xv, v["hi"], rtol=1e-6):
            notes.append(f"{v['unit']}.{v['key']} is on its bound ({xv:.4g}): a wider range may do better")
    return {"vars": [{"unit": v["unit"], "key": v["key"], "start": float(x0[i]), "best": float(best["x"][i])} for i, v in enumerate(vars_)],
            "objective_start": f0, "objective_best": best["objective"],
            "constraints_start": c0, "constraints_best": best["constraints"], "history": history,
            "solves": n_solves[0], "success": bool(res.success), "message": str(res.message), "notes": notes}


def apply_optimum(model, result):
    for v in result["vars"]:
        set_param(model, v["unit"], v["key"], v["best"])
    return len(result["vars"])
