"""EOS calibration: tune the Peng-Robinson parameters of one component (or one binary pair) to measured data.

Pure component  - vapour pressures ('psat': T, P) and saturated/compressed liquid densities ('rho_l': T, P, rho) are
                  regressed on the alpha-function parameter ``m_pr`` (vapour pressure), the Peneloux shift ``vshift``
                  (liquid density) and, optionally, omega, Tc and Pc.  Weak priors keep the parameters near the
                  starting values when the data are few.
Binary pair     - bubble pressures ('pb': T, x of the first component, P) are regressed on the kij of the pair.

The result carries the before/after deviations so the improvement can be judged before it is applied; applying it only
writes ``model["fluid"]["overrides"][key]`` / ``["kij"]`` (see ``flowsheet.build_fluid``)."""
from __future__ import annotations

import dataclasses
import math

import numpy as np

from .components import LIBRARY, Component
from .thermo import FluidPackage, R_BAR

PURE_PARAMS = {
    "m_pr": "alpha-function parameter m",
    "vshift": "volume shift [cm3/mol]",
    "omega": "acentric factor",
    "Tc": "critical temperature [K]",
    "Pc": "critical pressure [bar]",
}
BOUNDS = {"m_pr": (0.1, 2.5), "vshift": (-60.0, 60.0), "omega": (-0.3, 2.0), "Tc": (0.7, 1.3), "Pc": (0.6, 1.5)}
PRIOR_SCALE = {"m_pr": 0.15, "vshift": 8.0, "omega": 0.08, "Tc": 0.03, "Pc": 0.08}   # 1-sigma of the weak prior


def base_component(fluid, key) -> Component:
    """The component as the model currently has it (library, hypothetical or already calibrated)."""
    h = (fluid.get("hypos") or {}).get(key)
    if h is not None:
        return Component.from_dict(h)
    c = LIBRARY[key]
    ov = (fluid.get("overrides") or {}).get(key)
    if ov:
        ok = {f: v for f, v in ov.items() if f in Component.__dataclass_fields__ and f != "key"}
        if "cp" in ok:
            ok["cp"] = tuple(ok["cp"])
        c = dataclasses.replace(c, **ok)
    return c


def effective_m(c: Component) -> float:
    fp = FluidPackage([c], kij=np.zeros((1, 1)))
    return float(fp.m[0])


def effective_params(c: Component) -> dict:
    return {"m_pr": effective_m(c), "vshift": float(c.vshift), "omega": float(c.omega), "Tc": float(c.Tc),
            "Pc": float(c.Pc)}


def _with(c: Component, p: dict) -> Component:
    kw = {}
    for k in ("m_pr", "vshift", "omega", "Tc", "Pc"):
        if k in p:
            kw[k] = float(p[k])
    return dataclasses.replace(c, **kw)


# ------------------------------------------------------------------ model values
def _single(c):
    return FluidPackage([c], kij=np.zeros((1, 1)))


def rho_liquid(fp, i, T, P):
    """Shifted liquid density [kg/m3] of pure component i (smallest EOS root)."""
    idx = np.array([i])
    _, Z, _ = fp.lnphi(np.array([1.0]), T, P, idx, "L")
    V = Z * R_BAR * T / P - fp.c_shift[i]
    return fp.MW[i] / 1000.0 / V


def bubble_pressure(fp, x, T, P0=None):
    """Bubble pressure [bar] of liquid composition x at T by successive substitution; None if it does not exist."""
    x = np.asarray(x, float)
    idx = np.arange(fp.n)
    sel = x > 0
    idx = idx[sel]
    xs = x[sel] / x[sel].sum()
    P = P0 or float(np.sum(xs * fp.Pc[idx] * np.exp(5.373 * (1 + fp.w[idx]) * (1 - fp.Tc[idx] / T))))
    P = max(P, 1e-4)
    K = fp.Pc[idx] / P * np.exp(5.373 * (1 + fp.w[idx]) * (1 - fp.Tc[idx] / T))
    for _ in range(200):
        y = K * xs
        s = y.sum()
        y = y / s
        lpl, Zl, _ = fp.lnphi(xs, T, P, idx, "L")
        lpv, Zv, _ = fp.lnphi(y, T, P, idx, "V")
        Kn = np.exp(lpl - lpv)
        sn = float(np.sum(Kn * xs))
        if not np.isfinite(sn) or sn <= 0:
            return None
        P_new = P * sn
        K = Kn
        if abs(sn - 1.0) < 1e-9 and abs(Zl - Zv) > 1e-6:
            return P
        if abs(Zl - Zv) < 1e-7:           # trivial solution: lost the two-phase region
            P_new = P * (0.7 if sn > 1 else 1.3)
        P = min(max(P_new, 1e-5), 2e3)
    return P if abs(sn - 1.0) < 1e-5 else None


# ------------------------------------------------------------------ data
def clean_pure_data(rows):
    """rows: list of dicts with kind ('psat' | 'rho_l'), T_C, P_bar, value (bar for psat is in P_bar; rho in kg/m3)."""
    out = []
    for r in rows:
        k = str(r.get("kind", "")).strip().lower()
        try:
            T = float(r["T_C"]) + 273.15
        except (KeyError, TypeError, ValueError):
            continue
        if k == "psat":
            try:
                P = float(r.get("value", r.get("P_bar")))
            except (TypeError, ValueError):
                continue
            if T > 0 and P > 0:
                out.append({"kind": "psat", "T": T, "P": P, "y": P})
        elif k == "rho_l":
            try:
                rho = float(r["value"])
                P = float(r.get("P_bar") or 1.01325)
            except (KeyError, TypeError, ValueError):
                continue
            if T > 0 and rho > 0 and P > 0:
                out.append({"kind": "rho_l", "T": T, "P": P, "y": rho})
    return out


def _pure_residuals(c, data):
    fp = _single(c)
    res = []
    for d in data:
        try:
            if d["kind"] == "psat":
                ps = fp.psat_pure(0, d["T"])
                r = 0.5 if ps is None or ps <= 0 else math.log(ps / d["y"])
            else:
                r = rho_liquid(fp, 0, d["T"], d["P"]) / d["y"] - 1.0
        except Exception:
            r = 0.5
        res.append(r if np.isfinite(r) else 0.5)
    return np.array(res)


def _aad(res_kind):
    return float(np.mean(np.abs(res_kind)) * 100.0) if len(res_kind) else float("nan")


def _summary(c, data):
    r = _pure_residuals(c, data)
    kinds = np.array([d["kind"] for d in data])
    out = {}
    for k in ("psat", "rho_l"):
        m = kinds == k
        if m.any():
            out[k] = {"AAD%": _aad(np.expm1(np.abs(r[m])) if k == "psat" else r[m]), "n": int(m.sum())}
    return out, r


def calibrate_pure(base: Component, data_rows, params=("m_pr", "vshift"), prior=True) -> dict:
    """Fit the chosen parameters of ``base``.  Returns dict(ok, message, before, after, params0, params1, table,
    override) where ``override`` is the dict of Component fields to store."""
    from scipy.optimize import least_squares
    data = clean_pure_data(data_rows)
    params = [p for p in params if p in PURE_PARAMS]
    if not data:
        return {"ok": False, "message": "No usable data rows (need kind, T_C and a value)."}
    if not params:
        return {"ok": False, "message": "Select at least one parameter to fit."}
    kinds = {d["kind"] for d in data}
    notes = []
    if "m_pr" in params and "omega" in params:
        params.remove("omega")
        notes.append("omega was dropped because m is fitted directly")
    if "vshift" in params and "rho_l" not in kinds:
        params.remove("vshift")
        notes.append("vshift was not fitted: no liquid-density data")
    if not params:
        return {"ok": False, "message": "No fit parameter is constrained by the data supplied."}
    if "m_pr" in params and "psat" not in kinds:
        params.remove("m_pr")
        notes.append("m was not fitted: no vapour-pressure data")
        if not params:
            return {"ok": False, "message": "No fit parameter is constrained by the data supplied."}
    p0 = effective_params(base)
    # Tc and Pc are fitted as ratios to their start values
    scale = {k: (p0[k] if k in ("Tc", "Pc") else 1.0) for k in params}
    x0 = np.array([p0[k] / scale[k] for k in params])
    lo = np.array([BOUNDS[k][0] * (p0[k] if k in ("Tc", "Pc") else 1.0) / scale[k] for k in params])
    hi = np.array([BOUNDS[k][1] * (p0[k] if k in ("Tc", "Pc") else 1.0) / scale[k] for k in params])
    sig = np.array([PRIOR_SCALE[k] / scale[k] for k in params])
    w_prior = 0.02 if prior else 0.0          # weight of the prior relative to a 100 % data error

    def make(x):
        p = dict(p0)
        for k, v in zip(params, x):
            p[k] = v * scale[k]
        if "omega" in params:
            p["m_pr"] = 0.0           # omega drives m again
        elif "m_pr" not in params:
            p["m_pr"] = base.m_pr
        return p

    def fun(x):
        r = _pure_residuals(_with(base, make(x)), data)
        pr = w_prior * (x - x0) / sig
        return np.concatenate([r, pr])

    before, _ = _summary(base, data)
    sol = least_squares(fun, np.clip(x0, lo + 1e-9, hi - 1e-9), bounds=(lo, hi), x_scale=np.maximum(sig, 1e-6),
                        diff_step=1e-4, max_nfev=120)
    p1 = make(sol.x)
    c1 = _with(base, p1)
    after, r1 = _summary(c1, data)
    _, r0 = _summary(base, data)
    table = []
    for d, a, b in zip(data, r0, r1):
        table.append({"kind": d["kind"], "T_C": d["T"] - 273.15, "P_bar": d["P"], "measured": d["y"],
                      "before_dev%": 100 * (math.expm1(a) if d["kind"] == "psat" else a),
                      "after_dev%": 100 * (math.expm1(b) if d["kind"] == "psat" else b)})
    ov = {}
    for k in params:
        ov[k] = float(p1[k])
    if "omega" in params:
        ov["m_pr"] = 0.0
    ov["note"] = f"calibrated on {len(data)} points ({', '.join(sorted(kinds))}); fitted {', '.join(params)}"
    for k, d in after.items():
        d["AAD%"] = float(np.mean([abs(t["after_dev%"]) for t in table if t["kind"] == k]))
        before[k]["AAD%"] = float(np.mean([abs(t["before_dev%"]) for t in table if t["kind"] == k]))
    return {"ok": bool(sol.success or sol.status > 0), "message": "; ".join(notes), "fitted": params,
            "before": before, "after": after, "params0": {k: p0[k] for k in PURE_PARAMS},
            "params1": effective_params(c1), "table": table, "override": ov, "n": len(data)}


# ------------------------------------------------------------------ binary kij
def calibrate_kij(compA: Component, compB: Component, rows, kij0: float | None = None) -> dict:
    """rows: dicts with T_C, x1 (mole fraction of A in the liquid) and P_bar (bubble pressure)."""
    from scipy.optimize import least_squares
    data = []
    for r in rows:
        try:
            T = float(r["T_C"]) + 273.15
            x1 = float(r["x1"])
            P = float(r["P_bar"])
        except (KeyError, TypeError, ValueError):
            continue
        if T > 0 and 0 < x1 < 1 and P > 0:
            data.append((T, x1, P))
    if len(data) < 1:
        return {"ok": False, "message": "No usable rows (need T_C, x1 strictly between 0 and 1, and P_bar)."}
    from .components import default_kij
    k0 = default_kij(compA, compB) if kij0 is None else float(kij0)
    fp = FluidPackage([compA, compB], kij=np.zeros((2, 2)))

    def devs(k):
        fp.kij[0, 1] = fp.kij[1, 0] = k
        fp._kcache.clear()
        fp._tcache.clear()
        out = []
        for T, x1, P in data:
            try:
                pb = bubble_pressure(fp, np.array([x1, 1 - x1]), T)
            except Exception:
                pb = None
            out.append(0.5 if pb is None or pb <= 0 else math.log(pb / P))
        return np.array(out)

    d0 = devs(k0)
    sol = least_squares(lambda v: np.concatenate([devs(v[0]), [0.02 * (v[0] - k0) / 0.05]]), [k0],
                        bounds=([-0.5], [0.8]), diff_step=1e-3, max_nfev=60)
    k1 = float(sol.x[0])
    d1 = devs(k1)
    table = [{"T_C": T - 273.15, "x1": x1, "P_bar": P, "before_dev%": 100 * math.expm1(a),
              "after_dev%": 100 * math.expm1(b)} for (T, x1, P), a, b in zip(data, d0, d1)]
    return {"ok": True, "message": "", "kij0": k0, "kij1": k1, "n": len(data), "table": table,
            "before": {"pb": {"AAD%": float(np.mean(np.abs(np.expm1(np.abs(d0)))) * 100), "n": len(data)}},
            "after": {"pb": {"AAD%": float(np.mean(np.abs(np.expm1(np.abs(d1)))) * 100), "n": len(data)}}}


# ------------------------------------------------------------------ apply
def apply_pure(fluid: dict, key: str, override: dict):
    if key in (fluid.get("hypos") or {}):
        d = fluid["hypos"][key]
        d.update({k: v for k, v in override.items()})
    else:
        fluid.setdefault("overrides", {}).setdefault(key, {}).update(override)


def apply_kij(fluid: dict, a: str, b: str, value: float):
    kij = fluid.setdefault("kij", {})
    kij[f"{a}|{b}"] = float(value)
    kij.pop(f"{b}|{a}", None)


def reset_pure(fluid: dict, key: str):
    (fluid.get("overrides") or {}).pop(key, None)
