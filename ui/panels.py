"""HYSYS-style property views for the selected object."""
from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st

from procsim.components import LIBRARY
from procsim.flowsheet import rename, delete, port_edges, build_fluid
from procsim.unitops import CATALOGUE, parse_fractions, PROFILE_TYPES
from procsim.streams import stream_properties, phase_table, composition_table

from .state import fmt, bump, connections, sol_is_current
from . import charts
from . import units as U

DELTA_PARAMS = {"dTmin", "air_dT"}
REB_UNITS = {"Reboiler duty": "kW", "Bottoms rate": "kmol/h", "Boil-up ratio": "", "Reboiler temperature": "°C",
             "Distillate rate": "kmol/h"}


def param_unit(spec, params):
    """SI unit of a parameter (resolving units that depend on another choice)."""
    if spec["key"] == "reb_value":
        return REB_UNITS.get(params.get("reb_spec"), "")
    u = spec.get("unit") or ""
    return "" if u in ("-", "–") else u

STATUS_TXT = {"ok": "🟢 Solved", "warning": "🟠 Solved with warnings", "error": "🔴 Error",
              "missing": "🟡 Incomplete connections / specs", "unsolved": "🟡 Not solved"}


def _wkey(uid, key):
    return f"w{st.session_state.widget_ver}_{uid}_{key}"


def _set_param(uid, key, wkey, conv=None):
    ss = st.session_state
    if uid in ss.model["units"]:
        v = ss[wkey]
        if conv is not None and v is not None:
            si_unit, label, delta = conv
            v = U.to_si(si_unit, v, label, delta)
        ss.model["units"][uid]["params"][key] = v


def _visible(spec, params):
    si = spec.get("show_if")
    if not si:
        return True
    for k, v in si.items():
        cur = params.get(k)
        if isinstance(v, list):
            if cur not in v:
                return False
        elif cur != v:
            return False
    return True


def param_widgets(uid, specs, params, cols=2):
    shown = [s for s in specs if _visible(s, params)]
    grid = st.columns(cols)
    for i, s in enumerate(shown):
        k = s["key"]
        wk = _wkey(uid, k)
        si_u = param_unit(s, params)
        delta = k in DELTA_PARAMS
        disp_u = U.unit(si_u, s["label"], delta)
        label = s["label"] + (f" [{disp_u}]" if disp_u else "")
        if s["key"] == "reb_value":
            label = "Specification value" + (f" [{disp_u}]" if disp_u else "")
        with grid[i % cols]:
            if s["kind"] == "float":
                kw = {}
                if s.get("min") is not None:
                    kw["min_value"] = float(U.value(si_u, float(s["min"]), s["label"], delta))
                if s.get("max") is not None:
                    kw["max_value"] = float(U.value(si_u, float(s["max"]), s["label"], delta))
                v = float(U.value(si_u, float(params.get(k, s["default"])), s["label"], delta))
                if "min_value" in kw:
                    v = max(v, kw["min_value"])
                if "max_value" in kw:
                    v = min(v, kw["max_value"])
                conv = (si_u, s["label"], delta) if disp_u != si_u else None
                st.number_input(label, value=v, key=wk, format="%.6g", on_change=_set_param,
                                args=(uid, k, wk, conv), help=s.get("help"), **kw)
            elif s["kind"] == "select":
                opts = s["options"]
                cur = params.get(k, s["default"])
                st.selectbox(label, opts, index=opts.index(cur) if cur in opts else 0, key=wk,
                             on_change=_set_param, args=(uid, k, wk))
            else:
                st.text_input(label, value=str(params.get(k, s["default"])), key=wk,
                              on_change=_set_param, args=(uid, k, wk))


def results_table(res):
    if not res:
        st.caption("No results yet.")
        return
    rows = [{"Result": k2, "Value": fmt(v2)} for k2, v2 in (U.kv(k, v) for k, v in res.items())]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def header(obj_id, name, typelabel, status=None, err=None):
    ss = st.session_state
    c1, c2, c3 = st.columns([3, 2, 1])
    with c1:
        new = st.text_input(f"{typelabel} name", value=name, key=_wkey(obj_id, "__name"))
        if new != name:
            if rename(ss.model, obj_id, new):
                bump()
                st.rerun()
            else:
                st.error("That name is already used.")
    with c2:
        if status:
            st.markdown(f"<div style='padding-top:30px'>{STATUS_TXT.get(status, status)}</div>",
                        unsafe_allow_html=True)
    with c3:
        st.markdown("<div style='padding-top:28px'></div>", unsafe_allow_html=True)
        if st.button("Delete", key=_wkey(obj_id, "__del"), width="stretch"):
            delete(ss.model, [obj_id])
            ss.selected = []
            bump()
            st.rerun()
    if err:
        st.error(err)


# ------------------------------------------------------------------ streams

def stream_worksheet(st_, fp, key):
    if st_ is None:
        st.info("Stream not calculated yet — press **Solve** or complete the upstream specification.")
        return
    t1, t2, t3, t4 = st.tabs(["Conditions", "Phases", "Composition", "Phase envelope"])
    with t1:
        props = U.convert_dict(stream_properties(st_, fp))
        st.dataframe(pd.DataFrame([{"Property": k, "Value": fmt(v)} for k, v in props.items()]),
                     hide_index=True, width="stretch")
    with t2:
        rows = [U.convert_dict(r) for r in phase_table(st_, fp)]
        if rows:
            df = pd.DataFrame(rows).set_index("Phase").T
            st.dataframe(df.map(fmt), width="stretch")
        else:
            st.caption("No flow.")
    with t3:
        rows = [U.convert_dict(r) for r in composition_table(st_, fp)]
        df = pd.DataFrame(rows)
        st.dataframe(df.set_index("Component").map(lambda v: fmt(v)), width="stretch")
    with t4:
        env_panel(st_, fp, key)


@st.cache_data(show_spinner=False, max_entries=16)
def _cached_envelope(fluid_json, z_tuple, tmin, tmax, pmax):
    from procsim.thermo import FluidPackage
    import json
    fp = FluidPackage.from_dict(json.loads(fluid_json))
    T, P, g = charts.phase_envelope(fp, np.array(z_tuple), tmin, tmax, pmax)
    return T, P, g


def env_panel(st_, fp, key):
    if st_ is None or st_.empty:
        st.caption("No flow.")
        return
    import json
    c1, c2, c3, c4 = st.columns(4)
    sysk = U.system()[:2]
    tmin = U.to_si("°C", c1.number_input(f"T min [{U.uT()}]", value=float(U.T(-120.0)), key=f"envtmin_{key}_{sysk}"))
    tmax = U.to_si("°C", c2.number_input(f"T max [{U.uT()}]", value=float(U.T(300.0)), key=f"envtmax_{key}_{sysk}"))
    pmax = U.to_si("bar(a)", c3.number_input(f"P max [{U.uP()}]", value=float(U.P(max(150.0, 1.3 * st_.P))),
                                             key=f"envpmax_{key}_{sysk}"))
    go_ = c4.button("Compute", key=f"envgo_{key}", width="stretch")
    k = f"env_{key}"
    if go_:
        with st.spinner("Mapping the two-phase region (≈1 500 flashes)…"):
            T, P, g = _cached_envelope(json.dumps(fp.to_dict()), tuple(np.round(st_.z, 12)), tmin, tmax, pmax)
        st.session_state[k] = (T, P, g)
    if k in st.session_state:
        T, P, g = st.session_state[k]
        pts = [(st_.name, st_.T - 273.15, st_.P)]
        st.plotly_chart(charts.envelope_figure(T, P, g, pts, f"Phase envelope — {st_.name}"), width="stretch",
                        key=f"envfig_{key}")
        st.caption("Envelope drawn from a grid of Peng-Robinson flashes (vapour-fraction contours). "
                   "Near the cricondenbar/cricondentherm the grid resolution limits accuracy.")
    else:
        st.caption("Press **Compute** to map the envelope for this composition.")


def stream_view(sid):
    ss = st.session_state
    s = ss.model["streams"][sid]
    header(sid, s["name"], "Material stream")
    su, du = ss.model["units"][s["src"][0]], ss.model["units"][s["dst"][0]]
    st.caption(f"From **{su['name']}** ({s['src'][1]}) → to **{du['name']}** ({s['dst'][1]})")
    sol = ss.sol if sol_is_current() else None
    stream_worksheet(sol.streams.get(sid) if sol else None, sol.fp if sol else None, sid)


# ------------------------------------------------------------------ feed

def feed_view(uid):
    ss = st.session_state
    u = ss.model["units"][uid]
    sol = ss.sol if sol_is_current() else None
    header(uid, u["name"], "Feed stream", sol.status.get(uid) if sol else None,
           sol.errors.get(uid) if sol else None)
    t1, t2, t3 = st.tabs(["Conditions", "Composition", "Worksheet"])
    with t1:
        param_widgets(uid, CATALOGUE["feed"]["params"], u["params"], cols=3)
    with t2:
        comp_editor(uid, u)
    with t3:
        sid = (port_edges(ss.model, uid, "out").get("out") or [None])[0]
        if sid is None:
            st.info("Connect this feed to a unit operation (drag from its port on the diagram).")
        else:
            stream_worksheet(sol.streams.get(sid) if sol else None, sol.fp if sol else None, sid)


def comp_editor(uid, u):
    ss = st.session_state
    keys = ss.model["fluid"]["components"]
    comp = u["params"].get("composition") or {}
    basis = u["params"].get("comp_basis", "Mole fractions")
    df = pd.DataFrame({"Component": keys, "Fraction": [float(comp.get(k, 0.0) or 0.0) for k in keys]})
    total = df["Fraction"].sum()
    st.caption(f"{basis} — sum = {total:.6f}" + ("" if abs(total - 1) < 1e-6 else " (normalised when solving)"))
    ed = st.data_editor(df, key=_wkey(uid, "__comp"), hide_index=True, width="stretch", disabled=["Component"],
                        column_config={"Fraction": st.column_config.NumberColumn(min_value=0.0, format="%.6f")})
    new = {r.Component: float(r.Fraction or 0.0) for r in ed.itertuples()}
    if any(abs(new.get(k, 0) - float(comp.get(k, 0) or 0)) > 1e-15 for k in keys):
        u["params"]["composition"] = new
        st.rerun()
    c1, c2 = st.columns(2)
    if c1.button("Normalise", key=_wkey(uid, "__norm"), width="stretch") and total > 0:
        u["params"]["composition"] = {k: v / total for k, v in new.items()}
        ss.widget_ver += 1
        st.rerun()
    if c2.button("Clear", key=_wkey(uid, "__clr"), width="stretch"):
        u["params"]["composition"] = {k: 0.0 for k in keys}
        ss.widget_ver += 1
        st.rerun()


# ------------------------------------------------------------------ units

def unit_view(uid):
    ss = st.session_state
    u = ss.model["units"][uid]
    t = u["type"]
    if t == "feed":
        return feed_view(uid)
    sol = ss.sol if sol_is_current() else None
    status = sol.status.get(uid) if sol else None
    err = sol.errors.get(uid) if sol else None
    header(uid, u["name"], CATALOGUE[t]["label"], status, err)
    if t == "product":
        sid = (port_edges(ss.model, uid, "in").get("in") or [None])[0]
        if sid is None:
            st.info("Connect a unit outlet to this product stream.")
        else:
            stream_worksheet(sol.streams.get(sid) if sol else None, sol.fp if sol else None, sid)
        return
    if t == "adjust":
        return adjust_view(uid, u, sol)
    res = (sol.results.get(uid) if sol else None) or {}
    tabs = ["Design", "Results"]
    if t == "hx":
        tabs.append("Heat curve")
    if t == "recycle":
        tabs.append("Convergence")
    if t in PROFILE_TYPES:
        tabs.append("Profile")
    if t in ("compressor", "subsea_booster"):
        tabs.append("Performance curve")
    if t == "column":
        tabs.append("Profiles")
    if t in ("separator", "separator3", "scrubber"):
        tabs.append("Sizing")
    tb = st.tabs(tabs)
    with tb[0]:
        st.markdown("**Connections**")
        st.dataframe(pd.DataFrame(connections(ss.model, uid)), hide_index=True, width="stretch")
        st.markdown("**Parameters**")
        param_widgets(uid, CATALOGUE[t]["params"], u["params"])
        if t == "splitter":
            n = len(port_edges(ss.model, uid, "out").get("out", [])) or 1
            try:
                fr = parse_fractions(u["params"].get("fractions", ""), n)
                names = [ss.model["streams"][s]["name"] for s in port_edges(ss.model, uid, "out").get("out", [])]
                st.caption("Split: " + ", ".join(f"{nm} → {f:.4g}" for nm, f in zip(names, fr)))
            except Exception as e:
                st.error(str(e))
    with tb[1]:
        results_table(res)
    if t == "hx":
        with tb[2]:
            c = sol.hx_curves.get(uid) if sol else None
            if c:
                st.plotly_chart(charts.hx_figure(c, u["name"]), width="stretch", key=f"hxfig_{uid}")
            else:
                st.caption("Solve the flowsheet to see the heat curve.")
    if t in PROFILE_TYPES:
        with tb[2]:
            prof = sol.profiles.get(uid) if sol else None
            if prof:
                st.plotly_chart(charts.pipe_figure(prof, u["name"]), width="stretch", key=f"pipefig_{uid}")
                if t != "pipe":
                    st.plotly_chart(charts.surf_profile_figure(prof, u["name"]), width="stretch",
                                    key=f"surffig_{uid}")
                st.plotly_chart(charts.holdup_figure(prof, u["name"]), width="stretch", key=f"pipehl_{uid}")
                if t == "well":
                    well_deliverability_panel(uid, u, sol, res)
                import pandas as _pd
                n = len(prof["L"])
                st.dataframe(U.df_display(_pd.DataFrame({"Distance [m]": prof["L"], "P [bar(a)]": prof["P"],
                                                         "T [°C]": prof["T"], "Holdup [-]": prof["HL"][:n],
                                                         "Regime": prof["regime"][:n],
                                                         "Mixture velocity [m/s]": prof["vm"][:n]})),
                             hide_index=True, width="stretch")
            else:
                st.caption("Solve the flowsheet to see the profile.")
    if t == "compressor":
        with tb[2]:
            compressor_curve_panel(uid, u, sol, res)
    if t == "subsea_booster":
        with tb[2]:
            booster_curve_panel(uid, u, sol, res)
    if t == "column":
        with tb[2]:
            column_profiles_panel(uid, u, sol)
    if t in ("separator", "separator3", "scrubber"):
        with tb[2]:
            sizing_panel(uid, u, res)
    if t == "recycle":
        with tb[2]:
            if sol and sol.recycle_log:
                log = [r for r in sol.recycle_log if r["recycle"] == u["name"]]
                if log:
                    st.plotly_chart(charts.recycle_figure(log), width="stretch", key=f"rcyfig_{uid}")
                    st.dataframe(pd.DataFrame(log), hide_index=True, width="stretch")
            else:
                st.caption("No iterations yet.")


def _numeric_params(utype):
    out = [s["key"] for s in CATALOGUE[utype]["params"] if s["kind"] == "float"]
    return out


def adjust_view(uid, u, sol):
    ss = st.session_state
    p = u["params"]
    model = ss.model
    units = {k: v for k, v in model["units"].items() if v["type"] not in ("product", "adjust", "recycle")}
    unames = {v["name"]: k for k, v in units.items()}
    st.markdown("**Adjusted variable**")
    c1, c2, c3, c4 = st.columns(4)
    names = list(unames.keys())
    cur_u = next((n for n, k in unames.items() if k == p.get("var_unit")), None)
    sel_u = c1.selectbox("Object", ["—"] + names, index=(names.index(cur_u) + 1) if cur_u else 0,
                         key=_wkey(uid, "var_unit"))
    if sel_u != "—" and unames[sel_u] != p.get("var_unit"):
        p["var_unit"] = unames[sel_u]
        p["var_param"] = ""
        st.rerun()
    if p.get("var_unit") in units:
        opts = _numeric_params(units[p["var_unit"]]["type"])
        labels = {s["key"]: s["label"] for s in CATALOGUE[units[p["var_unit"]]["type"]]["params"]}
        cur = p.get("var_param") if p.get("var_param") in opts else None
        sel_p = c2.selectbox("Variable", ["—"] + opts, index=(opts.index(cur) + 1) if cur else 0,
                             format_func=lambda k: labels.get(k, k), key=_wkey(uid, "var_param"))
        if sel_p != "—" and sel_p != p.get("var_param"):
            p["var_param"] = sel_p
            v = float(units[p["var_unit"]]["params"].get(sel_p, 0.0))
            p["var_min"], p["var_max"] = (v - abs(v) * 0.5 - 1, v + abs(v) * 0.5 + 1)
            ss.widget_ver += 1
            st.rerun()
    vconv = None
    vu = ""
    if p.get("var_unit") in units and p.get("var_param"):
        vspec = next((s_ for s_ in CATALOGUE[units[p["var_unit"]]["type"]]["params"] if s_["key"] == p["var_param"]),
                     None)
        if vspec:
            si_u = param_unit(vspec, units[p["var_unit"]]["params"])
            delta = p["var_param"] in DELTA_PARAMS
            vu = U.unit(si_u, vspec["label"], delta)
            vconv = (si_u, vspec["label"], delta)
    lab = f" [{vu}]" if vu else ""

    def disp(x):
        return float(U.value(vconv[0], x, vconv[1], vconv[2])) if vconv else float(x)
    c3.number_input("Minimum" + lab, value=disp(p["var_min"]), key=_wkey(uid, "var_min"), format="%.6g",
                    on_change=_set_param, args=(uid, "var_min", _wkey(uid, "var_min"), vconv))
    c4.number_input("Maximum" + lab, value=disp(p["var_max"]), key=_wkey(uid, "var_max"), format="%.6g",
                    on_change=_set_param, args=(uid, "var_max", _wkey(uid, "var_max"), vconv))
    st.markdown("**Target**")
    d1, d2, d3, d4 = st.columns(4)
    kinds = ["stream", "unit"]
    kind = d1.selectbox("Target type", kinds, index=kinds.index(p.get("tgt_kind", "stream")),
                        format_func=lambda k: "Stream property" if k == "stream" else "Unit result",
                        key=_wkey(uid, "tgt_kind"))
    if kind != p.get("tgt_kind"):
        p["tgt_kind"], p["tgt_obj"], p["tgt_prop"] = kind, "", ""
        st.rerun()
    if kind == "stream":
        objs = [s["name"] for s in model["streams"].values()]
        props = ["Temperature [°C]", "Pressure [bar(a)]", "Vapour fraction", "Molar flow [kmol/h]",
                 "Mass flow [kg/h]", "Std gas flow [MSm³/d]", "Std liq vol flow [m³/h]", "Actual vol flow [m³/h]",
                 "Heat flow [kW]", "Molecular weight", "Mass density [kg/m³]"]
    else:
        objs = [v["name"] for v in model["units"].values() if v["type"] not in ("feed", "product", "adjust")]
        tu = next((k for k, v in model["units"].items() if v["name"] == p.get("tgt_obj")), None)
        res = (sol.results.get(tu) if (sol and tu) else None) or {}
        props = [k for k, v in res.items() if isinstance(v, (int, float, np.floating)) and not isinstance(v, bool)]
        if p.get("tgt_prop") and p["tgt_prop"] not in props:
            props = [p["tgt_prop"]] + props
    o = p.get("tgt_obj") if p.get("tgt_obj") in objs else None
    so = d2.selectbox("Object ", ["—"] + objs, index=(objs.index(o) + 1) if o else 0, key=_wkey(uid, "tgt_obj"))
    if so != "—" and so != p.get("tgt_obj"):
        p["tgt_obj"] = so
        st.rerun()
    pr = p.get("tgt_prop") if p.get("tgt_prop") in props else None
    sp = d3.selectbox("Property", ["—"] + props, index=(props.index(pr) + 1) if pr else 0, key=_wkey(uid, "tgt_prop"),
                      format_func=lambda k: U.key(k) if k != "—" else k)
    if sp != "—" and sp != p.get("tgt_prop"):
        p["tgt_prop"] = sp
        st.rerun()
    import re as _re
    m_ = _re.search(r"\[([^\[\]]+)\]", p.get("tgt_prop") or "")
    tconv = (m_.group(1), p["tgt_prop"], None) if m_ else None
    tv = float(U.value(tconv[0], float(p["tgt_value"]), tconv[1])) if tconv else float(p["tgt_value"])
    d4.number_input("Target value" + (f" [{U.unit(tconv[0], tconv[1])}]" if tconv else ""), value=tv,
                    key=_wkey(uid, "tgt_value"), format="%.6g",
                    on_change=_set_param, args=(uid, "tgt_value", _wkey(uid, "tgt_value"), tconv))
    e1, e2, e3 = st.columns(3)
    e1.number_input("Tolerance", value=float(p["tol"]), min_value=1e-9, key=_wkey(uid, "tol"), format="%.3g",
                    on_change=_set_param, args=(uid, "tol", _wkey(uid, "tol")))
    e2.number_input("Max iterations", value=int(p["max_iter"]), min_value=1, step=1, key=_wkey(uid, "max_iter"),
                    on_change=_set_param, args=(uid, "max_iter", _wkey(uid, "max_iter")))
    e3.toggle("Active", value=bool(p.get("active", True)), key=_wkey(uid, "active"),
              on_change=_set_param, args=(uid, "active", _wkey(uid, "active")))
    if sol:
        results_table(sol.results.get(uid))
        if sol.adjust_log:
            st.plotly_chart(charts.adjust_figure([r for r in sol.adjust_log if r["adjust"] == u["name"]]),
                            width="stretch", key=f"adjfig_{uid}")


def selection_view():
    ss = st.session_state
    sel = [i for i in ss.selected if i in ss.model["units"] or i in ss.model["streams"]]
    if not sel:
        st.info("Select an object on the diagram to open its property view (double-click or tap it). "
                "Drag equipment from the palette on the left; drag from a port to connect; drop a connection "
                "on empty space to create a feed or product stream.")
        return
    if len(sel) > 1:
        st.caption(f"{len(sel)} objects selected — showing the first. Use **Delete** or **Flip** on the diagram "
                   "toolbar for the whole selection.")
    obj = sel[0]
    if obj in ss.model["units"]:
        unit_view(obj)
    else:
        stream_view(obj)


# ------------------------------------------------------------------ compressor curve

def compressor_curve_panel(uid, u, sol, res):
    ss = st.session_state
    from procsim.unitops import typical_curve
    p = u["params"]
    curve = p.get("curve") or {}
    st.caption("Design-speed curve: actual inlet flow [m³/h], polytropic head [kJ/kg], polytropic efficiency [%]. "
               "The first row is the surge point, the last the stonewall. Other speeds follow the fan laws. "
               "Select **Performance curve** as the specification to let the curve set the outlet pressure.")
    si_cols = ["Actual flow [m³/h]", "Head [kJ/kg]", "Efficiency [%]"]
    df = pd.DataFrame({si_cols[0]: curve.get("flow", []), si_cols[1]: curve.get("head", []),
                       si_cols[2]: curve.get("eff", [])})
    dfd = U.df_display(df)
    ed = st.data_editor(dfd, key=_wkey(uid, "__curve"), num_rows="dynamic", width="stretch",
                        column_config={c: st.column_config.NumberColumn(format="%.2f") for c in dfd.columns})
    cols = list(dfd.columns)
    new = {k: [float(x) for x in U.column_to_si(si, ed[c].dropna())]
           for k, si, c in zip(("flow", "head", "eff"), si_cols, cols)}
    old = {k: list(map(float, curve.get(k, []))) for k in new}
    changed = len({len(v) for v in new.values()}) == 1 and (
        [len(v) for v in new.values()] != [len(v) for v in old.values()] or
        any(abs(a - b) > 1e-6 * max(1.0, abs(b)) for k in new for a, b in zip(new[k], old[k])))
    if changed:
        p["curve"] = new if new["flow"] else {}
        st.rerun()
    c1, c2 = st.columns(2)
    q, h = res.get("Actual inlet vol flow [m³/h]"), res.get("Head [kJ/kg]")
    eff = res.get("Polytropic efficiency [%]") or p.get("eff", 75.0)
    if c1.button("Generate a typical curve through the current operating point", key=_wkey(uid, "__gencurve"),
                 disabled=not (q and h), width="stretch"):
        p["curve"] = typical_curve(q, h, eff)
        p["N_design"] = float(p.get("speed", 10000.0) or 10000.0)
        ss.widget_ver += 1
        st.rerun()
    if c2.button("Clear curve", key=_wkey(uid, "__clrcurve"), disabled=not curve, width="stretch"):
        p["curve"] = {}
        if p.get("spec") == "Performance curve":
            p["spec"] = "Outlet pressure"
        ss.widget_ver += 1
        st.rerun()
    mp = sol.maps.get(uid) if sol else None
    if mp:
        st.plotly_chart(charts.compressor_map(mp, u["name"]), width="stretch", key=f"cmap_{uid}")
        k = [x for x in ("Surge margin [%]", "Stonewall margin [%]", "Speed [rpm]",
                         "Speed to meet duty (fan laws) [rpm]") if res.get(x) is not None]
        if k:
            cols = st.columns(len(k))
            for c, key in zip(cols, k):
                c.metric(key, fmt(res[key]))
        if res.get("Anti-surge recycle [kmol/h]") is not None:
            a1, a2, a3 = st.columns(3)
            for c, key in zip((a1, a2, a3), ("Anti-surge recycle [kmol/h]", "Anti-surge recycle [% of throughput]",
                                             "Anti-surge cooler duty [kW]")):
                k2, v2 = U.kv(key, res[key])
                c.metric(k2, fmt(v2))
    elif curve:
        st.caption("Solve the flowsheet to place the operating point on the map.")


# ------------------------------------------------------------------ vessel sizing

def sizing_panel(uid, u, res):
    rows = [(k, v) for k, v in res.items() if any(w in k for w in ("K-factor", "gas load", "diameter", "Gas load",
                                                                    "velocity", "Souders", "Liquid residence",
                                                                    "Required", "Selected"))]
    if not rows:
        st.caption("Solve the flowsheet to see the sizing.")
        return
    load = res.get("Gas load [% of max]")
    if load is not None:
        st.progress(min(load / 100.0, 1.0), text=f"Gas load {load:.0f} % of the Souders-Brown maximum")
    st.dataframe(pd.DataFrame([{"Sizing": k2, "Value": fmt(v2)} for k2, v2 in (U.kv(k, v) for k, v in rows)]),
                 hide_index=True, width="stretch")
    st.caption("Souders-Brown: v_max = K·√((ρL − ρG)/ρG). Vertical vessel, gas area = full cross-section. "
               "K-factors per GPSA for the internal chosen (mesh pad 0.107 m/s, vane 0.15, cyclones 0.25 at "
               "~7 bar; mesh pad K reduced above 7 bar). Screening only.")


def column_profiles_panel(uid, u, sol):
    prof = sol.columns.get(uid) if sol else None
    if not prof:
        st.caption("Solve the flowsheet to see the stage profiles.")
        return
    st.plotly_chart(charts.column_figure(prof, u["name"]), width="stretch", key=f"colfig_{uid}")
    st.plotly_chart(charts.column_comp_figure(prof, u["name"]), width="stretch", key=f"colcomp_{uid}")
    df = U.df_display(pd.DataFrame({"Stage": prof["labels"], "T [°C]": prof["T"], "P [bar(a)]": prof["P"],
                                    "Liquid [kmol/h]": prof["L"], "Vapour [kmol/h]": prof["V"]}))
    st.dataframe(df.map(lambda v: fmt(v) if not isinstance(v, str) else v), hide_index=True, width="stretch")


def well_deliverability_panel(uid, u, sol, res):
    """On-demand wellhead-pressure vs rate curve for a well (nodal-analysis view)."""
    from procsim import surf
    ss = st.session_state
    key = f"deliv_{uid}"
    if st.button("Compute deliverability curve", key=f"{key}_btn",
                 help="Wellhead pressure at 10 %–200 % of the current rate (about 0.5–1 s per point)"):
        sid = (port_edges(ss.model, uid, "in").get("in") or [None])[0]
        s_in = sol.streams.get(sid) if sid else None
        if s_in is not None and not s_in.empty:
            with st.spinner("Computing the deliverability curve…"):
                ss[key] = {"hash": ss.sol_hash, "pts": surf.deliverability_curve(u, s_in, sol.fp)}
    d = ss.get(key)
    if not d or d["hash"] != ss.sol_hash:
        st.caption("Press **Compute deliverability curve** for the wellhead pressure the well delivers at other rates.")
        return
    liquid = u["params"].get("ipr", surf.IPR_GAS) != surf.IPR_GAS
    q = (res.get("Oil/condensate rate [Sm³/d]", 0) + res.get("Water rate [Sm³/d]", 0)) if liquid else res.get("Gas rate [MSm³/d]")
    op = (q, res["Wellhead P [bar(a)]"]) if q is not None and res.get("Wellhead P [bar(a)]") is not None else None
    target = u["params"].get("WHP") if u["params"].get("rate_spec") == surf.RATE_WHP else None
    st.plotly_chart(charts.deliverability_figure(d["pts"], u["name"], op, target, liquid), width="stretch",
                    key=f"{key}_fig")


def booster_curve_panel(uid, u, sol, res):
    """Design-speed curve of one subsea booster machine: boost ΔP and efficiency vs actual flow per machine."""
    from procsim.surf import typical_booster_curve
    ss = st.session_state
    p = u["params"]
    curve = p.get("curve") or {}
    st.caption("Design-speed curve of **one machine**: actual inlet flow [m³/h], boost ΔP [bar], efficiency [%]. "
               "The first row is the minimum-flow limit, the last the run-out. Other speeds follow the fan laws. "
               "With a curve, a boost or outlet-pressure spec reports the speed needed; **Performance curve** as the "
               "specification lets the curve and speed set the boost. Machines in parallel split the flow; in series "
               "they share the boost.")
    si_cols = ["Actual liquid flow [m³/h]", "Boost ΔP [bar]", "Efficiency [%]"]
    df = pd.DataFrame({si_cols[0]: curve.get("flow", []), si_cols[1]: curve.get("head", []),
                       si_cols[2]: curve.get("eff", [])})
    dfd = U.df_display(df)
    ed = st.data_editor(dfd, key=_wkey(uid, "__bcurve"), num_rows="dynamic", width="stretch",
                        column_config={c: st.column_config.NumberColumn(format="%.2f") for c in dfd.columns})
    cols = list(dfd.columns)
    new = {k: [float(x) for x in U.column_to_si(si, ed[c].dropna())]
           for k, si, c in zip(("flow", "head", "eff"), si_cols, cols)}
    old = {k: list(map(float, curve.get(k, []))) for k in new}
    changed = len({len(v) for v in new.values()}) == 1 and (
        [len(v) for v in new.values()] != [len(v) for v in old.values()] or
        any(abs(a - b) > 1e-6 * max(1.0, abs(b)) for k in new for a, b in zip(new[k], old[k])))
    if changed:
        p["curve"] = new if new["flow"] else {}
        st.rerun()
    c1, c2 = st.columns(2)
    q, dp, eff = res.get("Flow per machine [m³/h]"), res.get("Boost per machine [bar]"), res.get("Efficiency used [%]")
    if c1.button("Generate a typical curve through the current operating point", key=_wkey(uid, "__genbcurve"),
                 disabled=not (q and dp and eff), width="stretch"):
        p["curve"] = typical_booster_curve(q, dp, eff)
        p["N_design"] = float(p.get("speed", 3600.0) or 3600.0)
        ss.widget_ver += 1
        st.rerun()
    if c2.button("Clear curve", key=_wkey(uid, "__clrbcurve"), disabled=not curve, width="stretch"):
        p["curve"] = {}
        if p.get("spec") == "Performance curve":
            p["spec"] = "Pressure boost"
        ss.widget_ver += 1
        st.rerun()
    mp = sol.maps.get(uid) if sol else None
    if mp:
        st.plotly_chart(charts.booster_map(mp, u["name"]), width="stretch", key=f"bmap_{uid}")
        k = [x for x in ("Speed [rpm]", "Speed to meet duty [rpm]", "Minimum-flow margin [%]", "Curve efficiency [%]")
             if res.get(x) is not None]
        if k:
            cc = st.columns(len(k))
            for c, key in zip(cc, k):
                c.metric(key, fmt(res[key]))
    elif curve:
        st.caption("Solve the flowsheet to place the operating point on the map.")
