"""Flowsheet model, sequential-modular solver, recycle and adjust convergence.

Model (JSON-serialisable dict)::

    {
      "version": 1,
      "fluid": {"components": ["C1", ...], "hypos": {key: component-dict},
                "kij": {"A|B": value}},           # kij overrides of the PR defaults
      "units":   {uid: {"type", "name", "x", "y", "params": {...}}},
      "streams": {sid: {"name", "src": [uid, port], "dst": [uid, port]}},
    }

Material streams are the connections.  Feed and product "units" are the stream
terminals (the arrow icons on the PFD); their name is the stream's name.
"""
from __future__ import annotations

import copy
import math
import time
from dataclasses import dataclass, field

import numpy as np

from .components import Component, LIBRARY
from .thermo import FluidPackage, FlashError, V_STD_GAS
from .streams import MaterialStream, EnergyStream, make_stream, zero_stream, stream_properties
from .unitops import CATALOGUE, CALC, UnitError, default_params, hx_post, K0, PROFILE_TYPES
from . import surf

LOGICAL = ("feed", "product", "recycle", "adjust")
EXTRAS = ("_post", "_profile", "_map", "_column", "_inlet_F")     # per-unit post-processing data kept with cached results


# --------------------------------------------------------------------------
# Model helpers
# --------------------------------------------------------------------------

def new_model(components=None):
    return {"version": 1,
            "fluid": {"components": components or ["N2", "CO2", "C1", "C2", "C3", "iC4", "nC4",
                                                   "iC5", "nC5", "nC6", "nC7", "nC8", "H2O"],
                      "hypos": {}, "kij": {}},
            "units": {}, "streams": {}, "counter": 1}


def build_fluid(model) -> FluidPackage:
    fl = model["fluid"]
    hypos = {k: Component.from_dict(v) for k, v in fl.get("hypos", {}).items()}
    import dataclasses
    for k, ov in (fl.get("overrides") or {}).items():       # EOS calibration: library component with changed fields
        if k in LIBRARY and k not in hypos:
            ok = {f: v for f, v in ov.items() if f in Component.__dataclass_fields__ and f != "key"}
            if "cp" in ok:
                ok["cp"] = tuple(ok["cp"])
            hypos[k] = dataclasses.replace(LIBRARY[k], **ok)
    keys = [k for k in fl["components"] if k in LIBRARY or k in hypos]
    if not keys:
        raise ValueError("The component list is empty")
    fp = FluidPackage.from_keys(keys, hypos)
    for pair, v in (fl.get("kij") or {}).items():
        a, b = pair.split("|")
        if a in fp.keys and b in fp.keys:
            i, j = fp.index(a), fp.index(b)
            fp.kij[i, j] = fp.kij[j, i] = float(v)
    if fl.get("hydrate_model"):
        fp.hydrate_model = fl["hydrate_model"]
    return fp


def next_name(model, utype):
    prefix = CATALOGUE[utype]["prefix"]
    used = {u["name"] for u in model["units"].values()} | {s["name"] for s in model["streams"].values()}
    if utype in ("feed", "product"):
        n = 1
        while f"{prefix} {n}" in used:
            n += 1
        return f"{prefix} {n}"
    n = 100
    while f"{prefix}-{n}" in used:
        n += 1
    return f"{prefix}-{n}"


def next_stream_name(model):
    used = {u["name"] for u in model["units"].values()} | {s["name"] for s in model["streams"].values()}
    n = 1
    while str(n) in used:
        n += 1
    return str(n)


def add_unit(model, utype, x=100, y=100, name=None, params=None):
    uid = f"u{model.get('counter', 1)}"
    model["counter"] = model.get("counter", 1) + 1
    p = default_params(utype)
    if params:
        p.update(params)
    model["units"][uid] = {"type": utype, "name": name or next_name(model, utype), "x": x, "y": y, "params": p}
    return uid


def connect(model, src_uid, src_port, dst_uid, dst_port, name=None):
    sid = f"s{model.get('counter', 1)}"
    model["counter"] = model.get("counter", 1) + 1
    model["streams"][sid] = {"name": name or next_stream_name(model), "src": [src_uid, src_port],
                             "dst": [dst_uid, dst_port]}
    normalize(model)
    return sid


def port_edges(model, uid, direction):
    """{port: [sid, ...]} for inlets ('in') or outlets ('out'), in connection order."""
    key = "dst" if direction == "in" else "src"
    out = {}
    for sid, s in model["streams"].items():
        if s[key][0] == uid:
            out.setdefault(s[key][1], []).append(sid)
    return out


def normalize(model):
    """Drop dangling / invalid connections and keep terminal-stream names in sync."""
    units = model["units"]
    bad = []
    used_in = {}
    used_out = {}
    for sid, s in list(model["streams"].items()):
        su, sp = s["src"]
        du, dp = s["dst"]
        if su not in units or du not in units or su == du:
            bad.append(sid)
            continue
        cs = CATALOGUE[units[su]["type"]]["ports"]["out"]
        cd = CATALOGUE[units[du]["type"]]["ports"]["in"]
        if sp not in cs or dp not in cd:
            bad.append(sid)
            continue
        ko, ki = (su, sp), (du, dp)
        if ko in used_out and not cs[sp]["multi"]:
            bad.append(sid)
            continue
        if ki in used_in and not cd[dp]["multi"]:
            bad.append(sid)
            continue
        used_out[ko] = sid
        used_in[ki] = sid
    for sid in bad:
        model["streams"].pop(sid, None)
    for sid, s in model["streams"].items():
        su, du = units[s["src"][0]], units[s["dst"][0]]
        if su["type"] == "feed":
            s["name"] = su["name"]
        elif du["type"] == "product":
            s["name"] = du["name"]
    return model


def rename(model, obj_id, new):
    new = new.strip()
    if not new:
        return False
    taken = {u["name"] for k, u in model["units"].items() if k != obj_id} | \
            {s["name"] for k, s in model["streams"].items() if k != obj_id}
    # a terminal and its own stream legitimately share a name
    own = set()
    if obj_id in model["units"]:
        for sid, s in model["streams"].items():
            if obj_id in (s["src"][0], s["dst"][0]) and model["units"][obj_id]["type"] in ("feed", "product"):
                own.add(sid)
    taken -= {model["streams"][s]["name"] for s in own}
    if obj_id in model["streams"]:
        s = model["streams"][obj_id]
        for end in ("src", "dst"):
            u = model["units"].get(s[end][0])
            if u and u["type"] in ("feed", "product"):
                taken.discard(u["name"])
    if new in taken:
        return False
    if obj_id in model["units"]:
        model["units"][obj_id]["name"] = new
    elif obj_id in model["streams"]:
        s = model["streams"][obj_id]
        s["name"] = new
        for end in ("src", "dst"):
            u = model["units"].get(s[end][0])
            if u and u["type"] in ("feed", "product"):
                u["name"] = new
    normalize(model)
    return True


def delete(model, ids):
    for i in ids:
        if i in model["units"]:
            model["units"].pop(i)
        model["streams"].pop(i, None)
    normalize(model)


# --------------------------------------------------------------------------
# Feeds
# --------------------------------------------------------------------------

def feed_composition(unit, fp):
    p = unit["params"]
    comp = p.get("composition") or {}
    z = np.array([float(comp.get(k, 0.0) or 0.0) for k in fp.keys])
    if z.sum() <= 0:
        raise UnitError(f"{unit['name']}: composition not specified")
    if np.any(z < 0):
        raise UnitError(f"{unit['name']}: negative composition")
    if p.get("comp_basis") == "Mass fractions":
        z = z / fp.MW
    return z / z.sum()


def feed_stream(unit, fp, name):
    p = unit["params"]
    z = feed_composition(unit, fp)
    MW = float(z @ fp.MW)
    basis = p.get("flow_basis", "kmol/h")
    q = float(p.get("flow", 0.0))
    if q < 0:
        raise UnitError(f"{unit['name']}: negative flow")
    SCF_PER_SM3 = 35.383                                   # 60 degF / 14.696 psia vs 15 degC / 1.01325 bar
    if basis == "bbl/d (std liquid)":
        vol_per_mol = float(z @ (fp.MW / fp.rho_std)) / 1000.0          # m3 std liquid per mol
        F = q / 6.28981 / 24.0 / vol_per_mol / 1000.0                    # kmol/h
    else:
        F = {"kmol/h": q, "kg/h": q / MW, "MSm³/d": q * 1e6 / 24.0 / V_STD_GAS, "Sm³/h": q / V_STD_GAS,
             "lbmol/h": q / 2.20462, "lb/h": q / 2.20462 / MW,
             "MMscf/d": q * 1e6 / SCF_PER_SM3 * 1e0 / 24.0 / V_STD_GAS}[basis]
    P = float(p["P_bar"])
    if P <= 0:
        raise UnitError(f"{unit['name']}: pressure must be positive")
    if p.get("spec", "T & P") == "T & P":
        fr = fp.pt_flash(z, float(p["T_C"]) + K0, P)
    else:
        fr = fp.pvf_flash(z, P, float(p["VF"]))
    if F <= 0:
        return zero_stream(name, fp, z, fr.T, fr.P)
    return make_stream(name, fp, F, z, fr)


# --------------------------------------------------------------------------
# Solution container
# --------------------------------------------------------------------------

@dataclass
class Solution:
    fp: FluidPackage
    streams: dict = field(default_factory=dict)          # sid -> MaterialStream
    results: dict = field(default_factory=dict)          # uid -> dict
    status: dict = field(default_factory=dict)           # uid -> ok | warning | error | unsolved | missing
    errors: dict = field(default_factory=dict)           # uid -> message
    energy: list = field(default_factory=list)           # EnergyStream
    recycle_log: list = field(default_factory=list)      # dicts: iter, recycle, err_flow, err_T
    adjust_log: list = field(default_factory=list)
    adjusted: dict = field(default_factory=dict)         # adjust uid -> (unit uid, param, value)
    hx_curves: dict = field(default_factory=dict)
    profiles: dict = field(default_factory=dict)         # pipe uid -> profile dict
    maps: dict = field(default_factory=dict)             # compressor uid -> performance-map data
    columns: dict = field(default_factory=dict)          # column uid -> stage profiles
    converged: bool = True
    messages: list = field(default_factory=list)
    seconds: float = 0.0

    def stream_by_name(self, model, name):
        for sid, s in model["streams"].items():
            if s["name"] == name:
                return self.streams.get(sid)
        return None


# --------------------------------------------------------------------------
# Solver
# --------------------------------------------------------------------------

def _signature(u, ins):
    sig = [repr(sorted((k, v) for k, v in u["params"].items() if not k.startswith("_")))]
    for port in sorted(ins):
        for st in ins[port]:
            if st is None or st.empty:
                sig.append((port, "empty"))
            else:
                sig.append((port, round(st.F, 9), round(st.T, 7), round(st.P, 9),
                            tuple(np.round(st.z, 12))))
    return tuple(sig)


def _single_pass(model, fp, values, sol, cache=None):
    units = model["units"]
    pending = [u for u, d in units.items() if d["type"] not in LOGICAL]
    in_map = {u: port_edges(model, u, "in") for u in pending}
    out_map = {u: port_edges(model, u, "out") for u in pending}
    for uid in list(pending):
        cat = CATALOGUE[units[uid]["type"]]["ports"]["in"]
        missing = [p for p, d in cat.items() if not in_map[uid].get(p) and not d.get("optional")]
        if not missing and not in_map[uid]:
            missing = ["any feed"]
        if missing:
            sol.status[uid] = "missing"
            sol.errors[uid] = "Inlet not connected: " + ", ".join(missing)
            pending.remove(uid)
    progress = True
    while pending and progress:
        progress = False
        for uid in list(pending):
            ins_sid = in_map[uid]
            if not all(sid in values for lst in ins_sid.values() for sid in lst):
                continue
            u = units[uid]
            ins = {p: [values[sid] for sid in lst] for p, lst in ins_sid.items()}
            pending.remove(uid)
            progress = True
            n_out = len(out_map[uid].get("out", [])) or 1
            sig = (_signature(u, ins), n_out) if cache is not None else None
            hit = cache.get(uid) if cache is not None else None
            try:
                if hit is not None and hit[0] == sig:
                    outs, res, en = hit[1]
                    u.update(hit[2])
                elif u["type"] == "splitter":
                    outs, res, en = CALC["splitter"](u, ins, fp, n_out)
                else:
                    outs, res, en = CALC[u["type"]](u, ins, fp)
                if cache is not None and (hit is None or hit[0] != sig):
                    cache[uid] = (sig, (outs, res, en), {k: u[k] for k in EXTRAS if k in u})
            except (UnitError, FlashError, ValueError, ZeroDivisionError, FloatingPointError) as e:
                sol.status[uid] = "error"
                sol.errors[uid] = str(e)
                continue
            except Exception as e:   # numerical failures surface as unit errors, not app crashes
                sol.status[uid] = "error"
                sol.errors[uid] = f"{type(e).__name__}: {e}"
                continue
            if u.get("_inlet_F") is not None:
                # a unit that sets its own rate (a well on a wellhead-pressure spec) writes it back to its inlet
                for sid in ins_sid.get("in", []):
                    if sid in values and not values[sid].empty:
                        st_in = values[sid].copy()
                        st_in.F = float(u["_inlet_F"])
                        values[sid] = st_in
            for port, lst in out_map[uid].items():
                produced = outs.get(port, [])
                for k, sid in enumerate(lst):
                    if k < len(produced):
                        st = produced[k].copy(model["streams"][sid]["name"])
                        values[sid] = st
            sol.results[uid] = res
            sol.status[uid] = "warning" if ("Warning" in res) else "ok"
            sol.energy.extend(en)
    for uid in pending:
        sol.status.setdefault(uid, "unsolved")
        sol.errors.setdefault(uid, "Not calculated (upstream stream unknown)")


def _tear_vector(st: MaterialStream, n):
    if st is None or st.empty:
        return np.zeros(n + 2)
    return np.concatenate([st.F * st.z, [st.T, st.P]])


def _vector_stream(x, fp, name):
    n = fp.n
    nf = np.maximum(x[:n], 0.0)
    F = float(nf.sum())
    if F <= 1e-12:
        return zero_stream(name, fp, None)
    z = nf / F
    fr = fp.pt_flash(z, float(x[n]), float(x[n + 1]))
    return make_stream(name, fp, F, z, fr)


def _solve_recycles(model, fp, feeds, sol, log_prefix="", cache=None):
    units = model["units"]
    recycles = [u for u, d in units.items() if d["type"] == "recycle"]
    rec_io = {}
    for r in recycles:
        ins, outs = port_edges(model, r, "in"), port_edges(model, r, "out")
        rec_io[r] = ((ins.get("in") or [None])[0], (outs.get("out") or [None])[0])
    guesses = {r: None for r in recycles}
    hist = {r: [] for r in recycles}      # (x, g) pairs for Wegstein
    max_iter = max([int(units[r]["params"].get("max_iter", 60)) for r in recycles] or [1])
    n = fp.n
    for it in range(1, max_iter + 1):
        values = dict(feeds)
        sol.results, sol.status, sol.errors, sol.energy = {}, {}, {}, []
        for r in recycles:
            sin, sout = rec_io[r]
            if sout is not None:
                g = guesses[r]
                values[sout] = (g.copy(model["streams"][sout]["name"]) if g is not None
                                else zero_stream(model["streams"][sout]["name"], fp, None))
        _single_pass(model, fp, values, sol, cache)
        all_ok = True
        for r in recycles:
            sin, sout = rec_io[r]
            p = units[r]["params"]
            if sin is None or sout is None:
                sol.status[r] = "missing"
                sol.errors[r] = "Recycle needs both an inlet and an outlet stream"
                continue
            new = values.get(sin)
            if new is None:
                sol.status[r] = "unsolved"
                sol.errors[r] = "Recycle inlet not calculated"
                all_ok = False
                continue
            x_old = _tear_vector(guesses[r], n)
            g_new = _tear_vector(new, n)
            Ftot = max(g_new[:n].sum(), x_old[:n].sum(), 1e-9)
            err_f = float(np.max(np.abs(g_new[:n] - x_old[:n])) / Ftot)
            err_T = abs(g_new[n] - x_old[n]) if guesses[r] is not None and not new.empty else 0.0
            if guesses[r] is None and not new.empty:
                err_f = 1.0
            sol.recycle_log.append({"iteration": it, "recycle": units[r]["name"], "flow error": err_f,
                                    "T error [°C]": err_T, "stage": log_prefix})
            conv = err_f < float(p.get("tol", 1e-4)) and err_T < float(p.get("tol_T", 0.01))
            sol.results[r] = {"Iterations": it, "Flow error (rel.)": err_f, "T error [°C]": err_T,
                              "Converged": "Yes" if conv else "No"}
            sol.status[r] = "ok" if conv else "unsolved"
            if conv:
                continue
            all_ok = False
            # update guess
            hist[r].append((x_old, g_new))
            x_next = g_new
            if p.get("method", "Wegstein") == "Wegstein" and len(hist[r]) >= 3 and guesses[r] is not None:
                (x1, g1), (x2, g2) = hist[r][-2], hist[r][-1]
                dx = x2 - x1
                with np.errstate(divide="ignore", invalid="ignore"):
                    s = np.where(np.abs(dx) > 1e-12, (g2 - g1) / dx, 0.0)
                    q = np.where(np.abs(s - 1) > 1e-9, s / (s - 1), 0.0)
                q = np.clip(np.nan_to_num(q), -5.0, 0.0)
                q[n + 1] = 0.0                    # pressure: direct substitution
                x_next = q * x2 + (1 - q) * g2
            try:
                guesses[r] = _vector_stream(x_next, fp, model["streams"][sout]["name"]) if not new.empty \
                    else new.copy()
            except Exception:
                guesses[r] = new.copy()
        if all_ok:
            sol.streams = values
            return True
        sol.streams = values
    for r in recycles:
        if sol.status.get(r) != "ok":
            sol.status[r] = "error"
            sol.errors[r] = f"Not converged in {max_iter} iterations"
    return False


def _get_path(model, uid, key):
    return float(model["units"][uid]["params"][key])


def target_value(model, sol, adj_params):
    kind, obj, prop = adj_params["tgt_kind"], adj_params["tgt_obj"], adj_params["tgt_prop"]
    if kind == "stream":
        st = sol.stream_by_name(model, obj)
        if st is None:
            raise UnitError(f"Adjust target stream '{obj}' not found / not solved")
        v = stream_properties(st, sol.fp).get(prop)
    else:
        uid = next((u for u, d in model["units"].items() if d["name"] == obj), None)
        v = (sol.results.get(uid) or {}).get(prop) if uid else None
    if v is None or isinstance(v, str):
        raise UnitError(f"Adjust target '{obj}: {prop}' has no value")
    return float(v)


def solve(model_in, fp: FluidPackage | None = None) -> Solution:
    t0 = time.time()
    model = copy.deepcopy(model_in)
    normalize(model)
    surf.activate(model.get("surf_catalogue"))
    fp = fp or build_fluid(model)
    sol = Solution(fp)
    units = model["units"]
    cache = {}

    def run_once(stage=""):
        feeds = {}
        feed_err = {}
        for uid, u in units.items():
            if u["type"] != "feed":
                continue
            outs = port_edges(model, uid, "out").get("out", [])
            try:
                st = feed_stream(u, fp, u["name"])
            except (UnitError, FlashError, ValueError) as e:
                feed_err[uid] = str(e)
                continue
            for sid in outs:
                feeds[sid] = st.copy(model["streams"][sid]["name"])
            feed_err.pop(uid, None)
            feeds.setdefault(f"__feed_{uid}", st)
        s = Solution(fp)
        ok = _solve_recycles(model, fp, {k: v for k, v in feeds.items() if not k.startswith("__")}, s, stage,
                             cache)
        for uid, u in units.items():
            if u["type"] == "feed":
                if uid in feed_err:
                    s.status[uid], s.errors[uid] = "error", feed_err[uid]
                else:
                    st = feeds.get(f"__feed_{uid}")
                    s.status[uid] = "ok"
                    s.results[uid] = {"Molar flow [kmol/h]": st.F, "Phase": st.flash.phase_label if st.flash else "-"}
                    outs_ = port_edges(model, uid, "out").get("out", [])
                    if outs_ and outs_[0] in s.streams and abs(s.streams[outs_[0]].F - st.F) > 1e-9 * max(st.F, 1.0):
                        dst = units[model["streams"][outs_[0]]["dst"][0]]["name"]
                        s.results[uid] = {"Molar flow [kmol/h]": s.streams[outs_[0]].F,
                                          "Specified flow [kmol/h]": st.F,
                                          "Rate set by": f"{dst} (wellhead-pressure specification)",
                                          "Phase": st.flash.phase_label if st.flash else "-"}
                    if not port_edges(model, uid, "out"):
                        s.status[uid] = "missing"
                        s.errors[uid] = "Feed not connected"
            elif u["type"] == "product":
                ins = port_edges(model, uid, "in").get("in", [])
                s.status[uid] = "ok" if ins and ins[0] in s.streams else ("missing" if not ins else "unsolved")
        s.converged = ok
        return s

    adjusts = [u for u, d in units.items() if d["type"] == "adjust" and d["params"].get("active", True)
               and d["params"].get("var_unit") in units and d["params"].get("var_param")]
    if not adjusts:
        sol = run_once()
    else:
        # simultaneous secant with bounds (each adjust uses its own history)
        xs = {a: [] for a in adjusts}
        es = {a: [] for a in adjusts}
        s = None
        max_it = max(int(units[a]["params"].get("max_iter", 40)) for a in adjusts)
        done = False
        log = []
        for it in range(1, max_it + 1):
            s = run_once(f"adjust {it}")
            all_conv = True
            for a in adjusts:
                ap = units[a]["params"]
                vu, vp = ap["var_unit"], ap["var_param"]
                x = _get_path(model, vu, vp)
                try:
                    e = target_value(model, s, ap) - float(ap["tgt_value"])
                except UnitError as ex:
                    s.status[a], s.errors[a] = "error", str(ex)
                    all_conv = False
                    done = True
                    continue
                xs[a].append(x)
                es[a].append(e)
                log.append({"iteration": it, "adjust": units[a]["name"], "variable": x, "error": e})
                tol = float(ap.get("tol", 1e-3))
                if abs(e) <= tol:
                    s.status[a] = "ok"
                    s.results[a] = {"Variable": f"{units[vu]['name']} . {vp}", "Value": x,
                                    "Target": ap["tgt_value"], "Error": e, "Iterations": it}
                    continue
                all_conv = False
                lo, hi = float(ap["var_min"]), float(ap["var_max"])
                if len(xs[a]) < 2 or es[a][-1] == es[a][-2]:
                    step = 0.02 * (hi - lo) if hi > lo else 0.1 * (abs(x) + 1)
                    xn = x + step if x + step <= hi else x - step
                else:
                    # secant, falling back to bisection if a bracket exists and secant leaves it
                    x1, x2, e1, e2 = xs[a][-2], xs[a][-1], es[a][-2], es[a][-1]
                    xn = x2 - e2 * (x2 - x1) / (e2 - e1)
                    br = [(xx, ee) for xx, ee in zip(xs[a], es[a])]
                    neg = [b for b in br if b[1] < 0]
                    pos = [b for b in br if b[1] > 0]
                    if neg and pos:
                        xa = max(neg, key=lambda b: b[1])[0]
                        xb = min(pos, key=lambda b: b[1])[0]
                        lo_b, hi_b = min(xa, xb), max(xa, xb)
                        if not (lo_b < xn < hi_b):
                            xn = 0.5 * (lo_b + hi_b)
                xn = min(max(xn, lo), hi)
                if abs(xn - x) < 1e-12 and (xn in (lo, hi)):
                    s.status[a] = "error"
                    s.errors[a] = f"Variable at bound {xn:g} without meeting the target (error {e:.4g})"
                    done = True
                model["units"][vu]["params"][vp] = xn
            if all_conv or done:
                break
        sol = s
        sol.adjust_log = log
        for a in adjusts:
            ap = units[a]["params"]
            sol.adjusted[a] = (ap["var_unit"], ap["var_param"], _get_path(model, ap["var_unit"], ap["var_param"]))
            if sol.status.get(a) not in ("ok", "error"):
                sol.status[a] = "error"
                sol.errors[a] = f"Not converged in {max_it} iterations"
    # post-processing (heat curves) once, on the final state
    for uid, u in units.items():
        if u["type"] == "hx" and "_post" in u:
            try:
                c = hx_post(u, fp)
                if c:
                    sol.hx_curves[uid] = c
                    sol.results.setdefault(uid, {})["Minimum approach [°C]"] = c["min_approach"]
                    if c["UA"]:
                        sol.results[uid]["UA [kW/°C]"] = c["UA"]
            except Exception as e:
                sol.messages.append(f"{u['name']}: heat curve failed ({e})")
    for uid, u in units.items():
        if sol.status.get(uid) not in ("ok", "warning"):
            continue
        if u["type"] in PROFILE_TYPES and "_profile" in u:
            sol.profiles[uid] = u["_profile"]
        if u["type"] in ("compressor", "subsea_booster", "subsea_pump", "subsea_compressor") and "_map" in u:
            sol.maps[uid] = u["_map"]
        if u["type"] == "column" and "_column" in u:
            sol.columns[uid] = u["_column"]
    for uid, u in units.items():
        if u["type"] == "adjust" and uid not in sol.status:
            sol.status[uid] = "missing"
            sol.errors[uid] = "Adjust variable/target not set (or inactive)"
    sol.seconds = time.time() - t0
    return sol
