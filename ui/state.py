"""Session state, canvas <-> model synchronisation and canvas result payloads."""
from __future__ import annotations

import copy
import hashlib
import json

import streamlit as st

from procsim.flowsheet import new_model, normalize, build_fluid, solve, port_edges
from procsim.unitops import CATALOGUE, default_params, energy_name
from procsim.streams import stream_properties, hydrate_risk
from procsim.examples import EXAMPLES, WET_GAS

ENERGY_DIR = {"compressor": "in", "pump": "in", "heater": "in", "expander": "out", "cooler": "out",
              "aircooler": "out", "subsea_booster": "in", "subsea_pump": "in", "subsea_compressor": "in",
              "intensifier": "in"}
WORK_TYPES = ("compressor", "pump", "expander", "subsea_booster", "subsea_pump", "subsea_compressor", "intensifier")


# --------------------------------------------------------------- formatting

def fmt(v, digits=None):
    if v is None:
        return "—"
    if isinstance(v, str):
        return v
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    if x != x:
        return "—"
    a = abs(x)
    if digits is not None:
        return f"{x:,.{digits}f}".replace(",", " ")
    if a == 0:
        return "0"
    if a >= 1e5:
        return f"{x:,.0f}".replace(",", " ")
    if a >= 100:
        return f"{x:,.1f}".replace(",", " ")
    if a >= 1:
        return f"{x:.3f}"
    if a >= 1e-3:
        return f"{x:.4f}"
    return f"{x:.3e}"


def _sys():
    from . import units as U
    return U.system()


def _duty(v, power):
    from . import units as U
    return f"{fmt(U.value('kW', v, power=power), 0 if (power or not U.field()) else 2)} {U.unit('kW', power=power)}"


def valid_choice(key, options, multi=False):
    """Drop a remembered selectbox / multiselect value that is no longer among the options (e.g. a stream name from
    the previously loaded example). Streamlit returns the stale session value as it is, which then fails as a
    KeyError where it is looked up."""
    ss = st.session_state
    if key not in ss:
        return
    v = ss[key]
    opts = list(options)
    if multi:
        if not isinstance(v, (list, tuple)) or any(x not in opts for x in v):
            del ss[key]
    elif v not in opts:
        del ss[key]


def qfmt(label, v, digits=None):
    """'value unit' in the current display system for a labelled SI quantity."""
    from . import units as U
    import re
    k2, v2 = U.kv(label, v)
    m = re.search(r"\[([^\[\]]+)\]", k2)
    u = m.group(1) if m else ""
    if u.startswith("% "):
        u = "%"
    return f"{fmt(v2, digits)} {u}".strip()


# --------------------------------------------------------------- session

def init_state():
    ss = st.session_state
    if "model" not in ss:
        ss.model = EXAMPLES["Two-stage gas compression with liquid recycle"]()
        ss.nonce = 1
        ss.fit = True
        ss.last_evt = None
        ss.selected = []
        ss.sol = None
        ss.sol_hash = None
        ss.auto_solve = True
        ss.svg = None
        ss.widget_ver = 0
        ss.solve_error = None


EDIT_HISTORY = 25
_NOT_UNDONE = ("scenarios", "fieldlife_result", "prognosis_result", "profile", "dynamics")          # kept as they are when an edit is undone


def push_edit(label):
    """Remember the model before a specification edit (for 'Undo edit')."""
    ss = st.session_state
    m = {k: v for k, v in ss.model.items() if k not in _NOT_UNDONE}
    hist = ss.setdefault("edit_hist", [])
    hist.append((label, copy.deepcopy(m)))
    del hist[:-EDIT_HISTORY]


def undo_edit():
    """Restore the model as it was before the last specification edit. Returns the edit's label or None."""
    ss = st.session_state
    hist = ss.get("edit_hist") or []
    if not hist:
        return None
    label, m = hist.pop()
    for k in _NOT_UNDONE:
        if k in ss.model:
            m[k] = ss.model[k]
    ss.model = m
    ss.widget_ver += 1
    return label


def bump(fit=False):
    """Python changed the structure: make the canvas adopt the model."""
    st.session_state.nonce += 1
    st.session_state.fit = fit
    st.session_state.widget_ver += 1


def load_model(m, fit=True):
    normalize(m)
    st.session_state.auto_paused = False
    st.session_state.model = m
    st.session_state.selected = []
    st.session_state.sol = None
    st.session_state.sol_hash = None
    st.session_state.edit_hist = []
    st.session_state.fm_svg = None
    reset_tab_state()
    bump(fit)


_TAB_PREFIXES = ("dyn_", "prof_", "_dynbase_", "_prof_", "_datbase_", "dat_ed", "dat_tbl", "dat_comp", "dat_prof_", "dat_ev_", "dat_bval",
                 "dat_bparam", "dat_bpat", "dat_btypes", "dat_bop", "dat_bmode")
_TAB_KEYS = ("_data_run", "data_edited", "dyn_result", "prof_result", "_dyn_builds")


def reset_tab_state():
    """A new flowsheet was loaded: forget every value the Profile / Dynamic / Data tabs keep in widgets or caches, so that
    nothing typed for the previous flowsheet (a unit with the same name, a time unit, a run length ...) leaks into this one."""
    ss = st.session_state
    for k in list(ss.keys()):
        if isinstance(k, str) and (k.startswith(_TAB_PREFIXES) or k in _TAB_KEYS) and k not in ("dyn_ver", "dat_ver", "prof_ver"):
            try:
                del ss[k]
            except Exception:
                pass
    for k in ("dyn_ver", "dat_ver", "prof_ver"):
        ss[k] = ss.get(k, 0) + 1


def canvas_structure(model):
    units = {k: {"type": u["type"], "name": u["name"], "x": u["x"], "y": u["y"], "flip": bool(u.get("flip"))}
             for k, u in model["units"].items()}
    streams = {k: {"name": s["name"], "src": list(s["src"]), "dst": list(s["dst"])}
               for k, s in model["streams"].items()}
    return {"units": units, "streams": streams}


SHORT = {"column": "Column", "hx": "Heat exchanger", "separator": "2-phase separator",
         "separator3": "3-phase separator", "feed": "Feed stream", "product": "Product stream", "splitter": "Tee",
         "teg_contactor": "TEG contactor", "amine_contactor": "Amine contactor", "relief_valve": "Relief valve (PSV)", "flare": "Flare", "comp_splitter": "Component splitter", "conv_reactor": "Conversion reactor", "eq_reactor": "Equilibrium reactor", "well": "Well", "injection_well": "Injection well", "xmas_tree": "Xmas tree", "template": "Template", "jumper": "Jumper / PLET",
         "flowline": "Flowline", "riser": "Riser", "subsea_valve": "SSIV / HIPPS", "subsea_booster": "Subsea booster",
         "subsea_pump": "Subsea pump", "subsea_compressor": "Subsea compressor", "subsea_separator": "Subsea separator",
         "subsea_cooler": "Subsea cooler", "intensifier": "Intensifier", "cimv": "Chemical injection"}


def catalogue_payload():
    out = {}
    for t, c in CATALOGUE.items():
        out[t] = {"label": c["label"], "short": SHORT.get(t, c["label"]), "prefix": c["prefix"], "category": c["category"],
                  "ports": {"in": c["ports"]["in"], "out": c["ports"]["out"]}}
    return out


def default_feed_comp(model):
    for u in model["units"].values():
        if u["type"] == "feed" and u["params"].get("composition"):
            return dict(u["params"]["composition"])
    keys = model["fluid"]["components"]
    comp = {k: v for k, v in WET_GAS.items() if k in keys}
    if not comp:
        comp = {keys[0]: 1.0}
    return comp


def merge_canvas_event(ev):
    """Apply a canvas event to the session model. Returns True if the canvas must resync."""
    ss = st.session_state
    model = ss.model
    js = ev.get("model") or {}
    ju, jst = js.get("units", {}), js.get("streams", {})
    for uid in list(model["units"].keys()):
        if uid not in ju:
            model["units"].pop(uid)
    for uid, u in ju.items():
        if u.get("type") not in CATALOGUE:
            continue
        if uid in model["units"]:
            mu = model["units"][uid]
            mu["x"], mu["y"], mu["flip"] = u["x"], u["y"], bool(u.get("flip"))
        else:
            src = (ev.get("copies") or {}).get(uid)
            if src in model["units"] and model["units"][src]["type"] == u["type"]:
                p = copy.deepcopy(model["units"][src]["params"])      # pasted: carry the specs
                if u["type"] == "adjust":
                    p["active"] = False                              # a copied adjust would fight the original
            else:
                p = default_params(u["type"])
            if u["type"] == "feed" and not p.get("composition"):
                p["composition"] = default_feed_comp(model)
            model["units"][uid] = {"type": u["type"], "name": u["name"], "x": u["x"], "y": u["y"],
                                   "flip": bool(u.get("flip")), "params": p}
    # unique unit names (canvas proposes names; Python has the last word)
    seen = set()
    for uid, u in model["units"].items():
        if u["name"] in seen:
            base, n = u["name"], 2
            while f"{base} ({n})" in seen:
                n += 1
            u["name"] = f"{base} ({n})"
        seen.add(u["name"])
    model["streams"] = {sid: {"name": s["name"], "src": list(s["src"]), "dst": list(s["dst"])}
                        for sid, s in jst.items()}
    # unique stream names
    used = {u["name"] for u in model["units"].values()}
    for sid, s in model["streams"].items():
        su, du = model["units"].get(s["src"][0]), model["units"].get(s["dst"][0])
        terminal = (su and su["type"] == "feed") or (du and du["type"] == "product")
        if not terminal:
            if s["name"] in used:
                n = 1
                while str(n) in used:
                    n += 1
                s["name"] = str(n)
            used.add(s["name"])
    normalize(model)
    ss.selected = [i for i in ev.get("selected", []) if i in model["units"] or i in model["streams"]]
    if ev.get("event") == "export_svg" and ev.get("svg"):
        ss.svg = ev["svg"]
    return canvas_structure(model) != _js_normal(js)


def _js_normal(js):
    units = {k: {"type": u["type"], "name": u["name"], "x": u["x"], "y": u["y"], "flip": bool(u.get("flip"))}
             for k, u in js.get("units", {}).items() if u.get("type") in CATALOGUE}
    streams = {k: {"name": s["name"], "src": list(s["src"]), "dst": list(s["dst"])}
               for k, s in js.get("streams", {}).items()}
    return {"units": units, "streams": streams}


def process_canvas_value(key="pfd"):
    ss = st.session_state
    ev = ss.get(key)
    if not ev or not isinstance(ev, dict):
        return
    tag = (ev.get("session"), ev.get("rev"))
    if tag == ss.last_evt:
        return
    ss.last_evt = tag
    if ev.get("nonce") != ss.nonce:
        # event produced against an older model version (e.g. just after loading an example)
        return
    if merge_canvas_event(ev):
        bump()


# --------------------------------------------------------------- solving

def model_hash(model):
    m = copy.deepcopy(model)
    for k in ("economics", "capex", "umbilical", "layout", "cooldown", "scenarios", "heating", "fieldlife",
              "fieldlife_result", "waxsand", "power", "fa2", "design", "prognosis", "prognosis_result", "profile", "profile_result", "dynamics"):   # post-processing settings and results: editing them never re-solves
        m.pop(k, None)
    for u in m["units"].values():
        for k in ("x", "y", "flip"):
            u.pop(k, None)
    return hashlib.sha1(json.dumps(m, sort_keys=True, default=str).encode()).hexdigest()


SOL_CACHE_SIZE = 6


def _cache_put(h, sol):
    ss = st.session_state
    cache = ss.setdefault("sol_cache", {})
    cache.pop(h, None)
    cache[h] = sol
    while len(cache) > SOL_CACHE_SIZE:
        cache.pop(next(iter(cache)))


def ensure_solved(force=False):
    """Solve when the model changed. Recent solutions are kept by model hash, so undo, toggling a specification
    back, or returning to an earlier case is instant. Auto-solve pauses itself after a solve that took longer
    than the time limit (large subsea flowsheets), until the user solves by hand or raises the limit."""
    ss = st.session_state
    h = model_hash(ss.model)
    if h == ss.sol_hash and ss.sol is not None and not force:
        return
    cached = (ss.get("sol_cache") or {}).get(h)
    if cached is not None and not force:
        ss.sol, ss.sol_hash, ss.solve_error = cached, h, None
        return
    if not force and (not ss.auto_solve or ss.get("auto_paused")):
        return
    if not ss.model["units"]:
        ss.sol, ss.sol_hash, ss.solve_error = None, h, None
        return
    try:
        with st.spinner("Solving flowsheet…"):
            fp = build_fluid(ss.model)
            sol = solve(ss.model, fp)
        ss.sol, ss.sol_hash, ss.solve_error = sol, h, None
        _cache_put(h, sol)
        # Adjust writes its converged value back into the variable, as in HYSYS
        changed = False
        for a, (vu, vp, val) in sol.adjusted.items():
            if vu in ss.model["units"] and ss.model["units"][vu]["params"].get(vp) != val:
                ss.model["units"][vu]["params"][vp] = val
                changed = True
        if changed:
            ss.sol_hash = model_hash(ss.model)
            _cache_put(ss.sol_hash, sol)
            ss.widget_ver += 1
        limit = float(ss.get("auto_limit", AUTO_LIMIT_DEFAULT))
        ss.auto_paused = bool(ss.auto_solve and sol.seconds > limit)
    except Exception as e:   # fluid-package problems etc.
        ss.sol, ss.sol_hash, ss.solve_error = None, h, f"{type(e).__name__}: {e}"


AUTO_LIMIT_DEFAULT = 10.0


def sol_is_current():
    ss = st.session_state
    return ss.sol is not None and ss.sol_hash == model_hash(ss.model)


# --------------------------------------------------------------- canvas payload

def _unit_label(u, res):
    t = u["type"]
    if not res:
        return ""
    key = {"compressor": "Power [kW]", "pump": "Power [kW]", "expander": "Power produced [kW]",
           "heater": "Duty [kW]", "cooler": "Duty removed [kW]", "aircooler": "Duty removed [kW]",
           "hx": "Duty [kW]"}.get(t)
    if t == "column" and res.get("Top T [°C]") is not None:
        tv = res.get("Bottoms TVP @ 37.8 °C [bar(a)]")
        return (f"{qfmt('Top T [°C]', res['Top T [°C]'], 0)} / {qfmt('Bottom T [°C]', res['Bottom T [°C]'], 0)}"
                + (f" · TVP {qfmt('TVP [bar(a)]', tv, 2)}" if tv is not None else ""))
    if t == "well" and res.get("Wellhead P [bar(a)]") is not None:
        return (f"WH {qfmt('Wellhead P [bar(a)]', res['Wellhead P [bar(a)]'], 0)} · "
                f"{qfmt('Wellhead T [°C]', res['Wellhead T [°C]'], 0)} · "
                f"{qfmt('Gas [MSm³/d]', res.get('Gas rate [MSm³/d]', 0.0), 2)}")
    if t == "teg_contactor" and res.get("Water dew point of dried gas [°C]") is not None:
        return f"dew point {qfmt('Dew point [°C]', res['Water dew point of dried gas [°C]'], 1)}"
    if t == "amine_contactor" and res.get("CO₂ in sweet gas [mol%]") is not None:
        return f"CO₂ {qfmt('CO₂ in sweet gas [mol%]', res['CO₂ in sweet gas [mol%]'], 2)} · {qfmt('Reboiler duty [kW]', res['Reboiler duty [kW]'], 0)}"
    if t == "relief_valve" and res.get("Selected orifice") is not None:
        return f"orifice {str(res['Selected orifice']).split(' ')[0]} · {qfmt('Relieving load [kg/h]', res['Relieving load [kg/h]'], 0)}"
    if t == "flare" and res.get("Heat release [MW]") is not None:
        return f"{qfmt('Heat release [MW]', res['Heat release [MW]'], 0)} · flame {qfmt('Flame length [m]', res['Flame length [m]'], 0)}"
    if t in ("conv_reactor", "eq_reactor") and res.get("Outlet T [°C]") is not None:
        return f"→ {qfmt('Outlet T [°C]', res['Outlet T [°C]'], 0)}"
    if t == "injection_well" and res.get("Injection rate [Sm³/d]") is not None:
        return (f"{qfmt('Rate [Sm³/d]', res['Injection rate [Sm³/d]'], 0)} · margin "
                f"{qfmt('Margin [bar]', res['Injection margin [bar]'], 0)}")
    if t == "xmas_tree" and res.get("Choke ΔP [bar]") is not None:
        crit = " · critical" if str(res.get("Choke flow", "")).startswith("Critical") else ""
        return f"choke ΔP {qfmt('Choke ΔP [bar]', res['Choke ΔP [bar]'], 1)}{crit}"
    if t == "subsea_separator" and res.get("Vessel ID [mm]") is not None:
        return f"ID {qfmt('ID [mm]', res['Vessel ID [mm]'], 0)} · {qfmt('Vessel P [bar(a)]', res['Vessel P [bar(a)]'], 0)}"
    if t == "subsea_cooler" and res.get("Outlet T [°C]") is not None:
        return f"→ {qfmt('Outlet T [°C]', res['Outlet T [°C]'], 0)} · {qfmt('Duty [kW]', res['Duty rejected to sea [kW]'], 0)}"
    if t == "intensifier" and res.get("Outlet P [bar(a)]") is not None:
        return f"→ {qfmt('Outlet P [bar(a)]', res['Outlet P [bar(a)]'], 0)} · 1:{res['Area ratio [-]']:.1f}"
    if t == "cimv" and res.get("Chemical rate [L/h]") is not None:
        return f"{res['Chemical rate [L/h]']:,.0f} L/h" + (
            f" · margin {qfmt('Margin [°C]', res['Hydrate margin downstream [°C]'], 0)}"
            if res.get("Hydrate margin downstream [°C]") is not None else "")
    if t == "flowline" and res.get("Heating system"):
        k_ = "Electrical heating power [kW]" if "Electrical heating power [kW]" in res else "Topside heater duty for heating [kW]"
        return f"ΔP {qfmt('Pressure drop [bar]', res['Pressure drop [bar]'], 1)} · heat {qfmt('Duty [kW]', res[k_], 0)}"
    if t in ("subsea_booster", "subsea_pump", "subsea_compressor") and res.get("Shaft power [kW]") is not None:
        return (f"{qfmt('Shaft power [kW]', res['Shaft power [kW]'], 0 if 'SI' in _sys() else 0)} · "
                f"GVF {res['Inlet GVF [%]']:.0f} %")
    if t == "template" and res.get("Slots used"):
        return f"{res['Slots used']} slots"
    if t == "subsea_valve" and res.get("Position"):
        return res["Position"]
    if t == "riser" and res.get("Pressure drop [bar]") is not None:
        risk = " · SLUGGING" if str(res.get("Riser-base slugging risk", "")).startswith("High") else ""
        return f"ΔP {qfmt('Pressure drop [bar]', res['Pressure drop [bar]'], 1)}{risk}"
    if t in ("pipe", "flowline", "jumper") and res.get("Pressure drop [bar]") is not None:
        return f"ΔP {qfmt('Pressure drop [bar]', res['Pressure drop [bar]'], 2)} · {res.get('Flow regime (dominant)', '')}"
    if t == "compressor" and res.get("Surge margin [%]") is not None and res.get("Power [kW]") is not None:
        rec = res.get("Anti-surge recycle [% of throughput]") or 0.0
        return (f"{qfmt('Power [kW]', res['Power [kW]'], 0)} · SM {res['Surge margin [%]']:.0f} %"
                + (f" · ASC {rec:.0f} %" if rec > 0.05 else ""))
    if key and res.get(key) is not None:
        return qfmt(key, res[key], 0 if "SI" in _sys() else 2)
    if t == "scrubber" and res.get("Gas load [% of max]") is not None:
        return (f"{qfmt('Selected diameter [mm]', res['Selected diameter [mm]'], 0 if 'SI' in _sys() else 1)} · "
                f"load {res['Gas load [% of max]']:.0f} %")
    if t in ("separator", "separator3") and res.get("Vessel T [°C]") is not None:
        return f"{qfmt('Vessel T [°C]', res['Vessel T [°C]'], 1)} · {qfmt('Vessel P [bar(a)]', res['Vessel P [bar(a)]'], 1)}"
    if t == "recycle":
        return f"{res.get('Iterations', '')} it" if res.get("Converged") == "Yes" else "not converged"
    return ""


def results_payload():
    ss = st.session_state
    model, sol = ss.model, ss.sol
    current = sol_is_current()
    out = {"streams": {}, "units": {}, "links": []}
    for sid, s in model["streams"].items():
        entry = {"solved": False}
        st_ = sol.streams.get(sid) if (sol and current) else None
        if st_ is not None:
            p = stream_properties(st_, sol.fp)
            entry["solved"] = True
            if hydrate_risk(st_, sol.fp):
                entry["warn"] = "hydrate"
            if st_.empty:
                entry["label"] = "no flow"
            else:
                entry["label"] = (f"{qfmt('Temperature [°C]', p['Temperature [°C]'], 1)} · "
                                  f"{qfmt('Pressure [bar(a)]', p['Pressure [bar(a)]'], 1)}")
                entry["tip"] = "\n".join([f"{p['Phase']}  (VF {p['Vapour fraction']:.4f})",
                                          f"T {qfmt('Temperature [°C]', p['Temperature [°C]'], 2)}",
                                          f"P {qfmt('Pressure [bar(a)]', p['Pressure [bar(a)]'], 3)}",
                                          f"Molar flow {fmt(p['Molar flow [kmol/h]'])} kmol/h",
                                          f"Mass flow {qfmt('Mass flow [kg/h]', p['Mass flow [kg/h]'])}",
                                          f"Std gas {qfmt('Std gas flow [MSm³/d]', p['Std gas flow [MSm³/d]'], 4)}"]
                                         + ([f"⚠ Below hydrate T ({qfmt('T [°C]', p['Hydrate T (inhibited) [°C]'], 1)} incl. inhibitor)"]
                                            if entry.get("warn") else []))
        out["streams"][sid] = entry
    for uid, u in model["units"].items():
        e = {}
        if sol and current:
            e["status"] = sol.status.get(uid, "unsolved")
            res = sol.results.get(uid) or {}
            e["label"] = _unit_label(u, res)
            tip = [f"{k}: {fmt(v)}" for k, v in list(res.items())[:8]]
            if uid in sol.errors:
                tip.insert(0, "⚠ " + sol.errors[uid])
            e["tip"] = "\n".join(tip)
        else:
            e["status"] = "unsolved" if u["type"] not in ("feed", "product") else "ok"
        # energy streams
        t = u["type"]
        dirn = ENERGY_DIR.get(t)
        duty_val = None
        if t in ("separator", "separator3") and abs(float(u["params"].get("duty", 0) or 0)) > 0:
            dirn = "in" if u["params"]["duty"] > 0 else "out"
        if t == "pipe" and u["params"].get("heat") == "Overall U to ambient" and float(u["params"].get("U", 0)) > 0:
            dirn = "out"
        if dirn:
            kind = "work" if t in WORK_TYPES else "heat"
            name = energy_name(u, kind)
            if sol and current:
                for en in sol.energy:
                    if en.name == name:
                        duty_val = en.duty_kW
            e["energy"] = [{"name": name, "dir": dirn,
                            "label": _duty(abs(duty_val), kind == "work") if duty_val is not None else ""}]
        if t == "column":
            lst = []
            for slot, (flag, suffix, d) in enumerate(((u["params"].get("condenser", "None") != "None", "cond", "out"),
                                                      (u["params"].get("reboiler", "Yes") == "Yes", "reb", "in"))):
                if not flag:
                    continue
                name = f"Q-{u['name']} {suffix}"
                val = next((en.duty_kW for en in sol.energy if en.name == name), None) if (sol and current) else None
                lst.append({"name": name, "dir": d, "slot": slot, "short": "Condenser" if suffix == "cond" else "Reboiler",
                            "label": _duty(abs(val), False) if val is not None else ""})
            if lst:
                e["energy"] = lst
        out["units"][uid] = e
        if t == "adjust":
            p = u["params"]
            if p.get("var_unit") in model["units"]:
                out["links"].append([uid, p["var_unit"]])
            if p.get("tgt_kind") == "stream":
                sid = next((k for k, s in model["streams"].items() if s["name"] == p.get("tgt_obj")), None)
                if sid:
                    out["links"].append([uid, sid])
            else:
                tu = next((k for k, x in model["units"].items() if x["name"] == p.get("tgt_obj")), None)
                if tu:
                    out["links"].append([uid, tu])
    return out


def status_line():
    ss = st.session_state
    sol = ss.sol
    n_u = sum(1 for u in ss.model["units"].values() if u["type"] not in ("feed", "product"))
    n_s = len(ss.model["streams"])
    if ss.solve_error:
        return f"Fluid package error: {ss.solve_error}"
    if not sol_is_current():
        if ss.get("auto_paused"):
            return f"{n_u} unit ops · {n_s} streams · changed since the last solve (auto-solve paused: press Solve)"
        return f"{n_u} unit ops · {n_s} streams · not solved (press Solve)"
    bad = [k for k, v in sol.status.items() if v in ("error", "missing", "unsolved")]
    flag = "solved" if not bad and sol.converged else f"{len(bad)} object(s) need attention"
    return f"{n_u} unit ops · {n_s} streams · {flag} in {sol.seconds:.1f} s"


def connections(model, uid):
    """Readable inlet/outlet connections for a unit."""
    ins = port_edges(model, uid, "in")
    outs = port_edges(model, uid, "out")
    rows = []
    cat = CATALOGUE[model["units"][uid]["type"]]["ports"]
    for p in cat["in"]:
        names = [model["streams"][s]["name"] for s in ins.get(p, [])]
        rows.append({"Port": p.replace("_", " "), "Direction": "Inlet", "Stream(s)": ", ".join(names) or "— not connected —"})
    for p in cat["out"]:
        names = [model["streams"][s]["name"] for s in outs.get(p, [])]
        rows.append({"Port": p.replace("_", " "), "Direction": "Outlet", "Stream(s)": ", ".join(names) or "— not connected —"})
    return rows


def attention_items():
    """(object name, severity, message) for the 'needs attention' list."""
    ss = st.session_state
    sol = ss.sol
    items = []
    if not sol_is_current():
        return items
    for k, v in sol.status.items():
        if k in ss.model["units"] and v in ("error", "missing", "unsolved", "warning"):
            msg = sol.errors.get(k) or (sol.results.get(k) or {}).get("Warning", "")
            items.append((ss.model["units"][k]["name"], v, msg))
    for sid, s in ss.model["streams"].items():
        st_ = sol.streams.get(sid)
        if st_ is not None and hydrate_risk(st_, sol.fp):
            p = stream_properties(st_, sol.fp)
            if st_.flash.phase("W") is not None:
                advice = "more inhibitor or insulation needed"
            else:
                advice = ("water-saturated gas with no free water here - hydrates form as soon as water condenses; "
                          "dehydrate, inhibit or keep it warm downstream")
            items.append((s["name"], "warning", f"{qfmt('T [°C]', p['Temperature [°C]'], 1)} is below the hydrate "
                          f"formation temperature {qfmt('T [°C]', p['Hydrate T (inhibited) [°C]'], 1)} "
                          f"(Motiee, incl. inhibitor) - {advice}"))
    return items
