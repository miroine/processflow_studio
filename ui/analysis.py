"""Analysis dashboard: KPIs, mass-flow Sankey, flow assurance (P-T path vs hydrate curves, dosing),
equipment charts and stream compositions."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import streamlit as st

from procsim.streams import stream_properties, hydrate_risk, hydrate_state
from procsim.transport import (hydrate_T_motiee, gas_gravity_dry, hydrate_T, MOTIEE, VDWP, required_inhibitor_wt, injection_rate,
                               aqueous_inhibitor_wt, hydrate_depression, INHIBITORS)

from .state import sol_is_current, fmt, attention_items, valid_choice
from . import charts
from . import units as U
from .state import qfmt

LEAN_RHO = {"MEG": 1105.0, "MeOH": 792.0}      # kg/m3 of the lean solution (90 wt% MEG / pure MeOH)


def _kpis(model, sol):
    fp = sol.fp
    from procsim.economics import ENV_TYPES
    env = {u["name"] for u in model["units"].values() if u["type"] in ENV_TYPES}   # heat to/from sea or formation
    work = sum(e.duty_kW for e in sol.energy if e.kind == "work" and e.duty_kW > 0)
    heat = sum(e.duty_kW for e in sol.energy if e.kind == "heat" and e.duty_kW > 0 and e.unit not in env)
    cool = -sum(e.duty_kW for e in sol.energy if e.kind == "heat" and e.duty_kW < 0 and e.unit not in env)
    gas = liq = 0.0
    for sid, s in model["streams"].items():
        if model["units"][s["dst"][0]]["type"] != "product":
            continue
        st_ = sol.streams.get(sid)
        if st_ is None or st_.empty:
            continue
        p = stream_properties(st_, fp)
        if p["Vapour fraction"] >= 0.999:
            gas += p["Std gas flow [MSm³/d]"]
        elif p["Vapour fraction"] <= 1e-6:
            liq += p["Std liq vol flow [m³/h]"] * 24.0
    sm = [r.get("Surge margin [%]") for u, r in sol.results.items()
          if u in model["units"] and model["units"][u]["type"] == "compressor" and r.get("Surge margin [%]") is not None]
    risks = sum(1 for sid in model["streams"] if sid in sol.streams and hydrate_risk(sol.streams[sid], fp))
    return [("Shaft power in", qfmt("Power [kW]", work, 0 if not U.field() else 0)),
            ("Heating", qfmt("Duty [kW]", heat, 0 if not U.field() else 2)),
            ("Cooling", qfmt("Duty [kW]", cool, 0 if not U.field() else 2)),
            ("Gas products", qfmt("Gas [MSm³/d]", gas, 3)), ("Liquid products", qfmt("Liquid [Sm³/d]", liq, 0)),
            ("Min surge margin", f"{min(sm):.0f} %" if sm else "—"),
            ("Hydrate-risk streams", str(risks)), ("Items needing attention", str(len(attention_items())))]


def default_path(model, sol):
    """Follow the heaviest stream from the largest feed through the flowsheet."""
    feeds = [(sid, sol.streams[sid].F * sol.streams[sid].MW) for sid, s in model["streams"].items()
             if model["units"][s["src"][0]]["type"] == "feed" and sid in sol.streams and not sol.streams[sid].empty]
    if not feeds:
        return []
    cur = max(feeds, key=lambda t: t[1])[0]
    path, seen = [cur], {cur}
    for _ in range(60):
        u = model["streams"][cur]["dst"][0]
        if model["units"][u]["type"] == "product":
            break
        outs = [(sid, sol.streams[sid].F * sol.streams[sid].MW) for sid, s in model["streams"].items()
                if s["src"][0] == u and sid in sol.streams and not sol.streams[sid].empty]
        if not outs:
            break
        cur = max(outs, key=lambda t: t[1])[0]
        if cur in seen:
            break
        path.append(cur)
        seen.add(cur)
    return path


def flow_assurance(model, sol):
    fp = sol.fp
    names = {s["name"]: sid for sid, s in model["streams"].items() if sid in sol.streams and not sol.streams[sid].empty}
    dp = [model["streams"][s]["name"] for s in default_path(model, sol)]
    st.markdown("##### P–T path against the hydrate curve")
    valid_choice("fa_path", names, multi=True)
    sel = st.multiselect("Streams along the path (in order)", list(names.keys()), default=dp, key="fa_path")
    traj, gas_x, inh_x, Pmax = [], None, None, 10.0
    for n in sel:
        s = sol.streams[names[n]]
        traj.append((n, s.T - 273.15, s.P, hydrate_risk(s, fp)))
        Pmax = max(Pmax, s.P)
        v = s.flash.phase("V")
        if v is not None and gas_x is None:
            gas_x = v.x
        aq = s.flash.phase("W")
        if aq is not None:
            inh_x = aq.x
    curves = []
    if gas_x is not None and fp.iw >= 0:
        sg = gas_gravity_dry(fp, gas_x)
        rig = getattr(fp, "hydrate_model", MOTIEE) == VDWP
        Ps = np.linspace(5.0, min(max(Pmax * 1.2, 20.0), 400.0 if rig else 280.0), 40)
        Tu = np.array([hydrate_T(fp, gas_x, p) for p in Ps], dtype=float)
        ok = np.isfinite(Tu)
        if ok.any():
            lab = "vdW-P" if rig else f"Motiee, SG {sg:.3f}"
            curves.append((f"Hydrate curve, uninhibited ({lab})", Ps[ok], Tu[ok], True))
            dT = hydrate_depression(fp, inh_x) if inh_x is not None else 0.0
            if dT > 0.05:
                wt = sum(aqueous_inhibitor_wt(fp, inh_x).values())
                curves.append((f"Hydrate curve, inhibited ({wt:.0f} wt% in water, −{qfmt('ΔT [°C]', dT, 1)})", Ps[ok],
                               Tu[ok] - dT, False))
    env = None
    c1, c2 = st.columns([3, 1])
    with c2:
        want_env = st.toggle("Overlay phase envelope", value=False, key="fa_env",
                             help="Envelope of the first stream in the path (traced, about a second)")
    if want_env and sel:
        from .panels import _cached_envelope
        s0 = sol.streams[names[sel[0]]]
        with st.spinner("Mapping the phase envelope…"):
            env = _cached_envelope(json.dumps(fp.to_dict()), tuple(np.round(s0.z, 12)), -60.0, 200.0,
                                   float(min(max(Pmax * 1.3, 50.0), 300.0)), False)
    if not sel:
        st.caption("Pick the streams that make up the path.")
    else:
        st.plotly_chart(charts.flow_assurance_figure(env, traj, curves), width="stretch", key="fa_fig")
        rows = []
        for n in sel:
            s = sol.streams[names[n]]
            t_u, t_i, margin, wt = hydrate_state(s, fp)
            rows.append({"Stream": n, "T [°C]": s.T - 273.15, "P [bar(a)]": s.P,
                         "Hydrate T [°C]": t_u, "Inhibited hydrate T [°C]": t_i, "Margin [°C]": margin,
                         "Inhibitor [wt%]": wt, "Status": "⚠ hydrate risk" if hydrate_risk(s, fp) else "ok"})
        st.dataframe(U.df_display(pd.DataFrame(rows)).map(lambda v: fmt(v) if not isinstance(v, str) else v), hide_index=True,
                     width="stretch")
    dosing_calculator(model, sol, names, sel)


def dosing_calculator(model, sol, names, path):
    fp = sol.fp
    st.markdown("##### Inhibitor dosing")
    if fp.iw < 0:
        st.caption("Add water to the component list to use the dosing calculator.")
        return
    cands = [n for n in names if sol.streams[names[n]].z[fp.iw] > 1e-9 and sol.streams[names[n]].flash.phase("V")]
    if not cands:
        st.caption("No gas stream carries water.")
        return
    coldest = min((n for n in (path or cands) if n in cands), key=lambda n: sol.streams[names[n]].T, default=cands[0])
    c1, c2, c3, c4 = st.columns(4)
    valid_choice("dose_st", cands)
    sname = c1.selectbox("Design point (coldest point of the line)", cands, index=cands.index(coldest), key="dose_st")
    inh = c2.selectbox("Inhibitor", ["MEG", "MeOH"], key="dose_inh")
    lean = c3.number_input("Lean inhibitor purity [wt%]", value=90.0 if inh == "MEG" else 100.0, min_value=10.0,
                           max_value=100.0, key=f"dose_lean_{inh}")
    uT = U.uT()
    margin = U.to_si("°C", c4.number_input(f"Design margin [{uT}]", value=float(U.value("°C", 3.0, delta=True)),
                                           min_value=0.0, max_value=float(U.value("°C", 20.0, delta=True)),
                                           key=f"dose_margin_{U.system()[:2]}"), delta=True)
    s = sol.streams[names[sname]]
    v = s.flash.phase("V")
    t_h = hydrate_T(fp, v.x, s.P)
    if t_h is None:
        st.warning("Outside the hydrate model's range (Motiee: 3.5–280 bar, gas gravity 0.55–1.0; "
                   "van der Waals–Platteeuw: 2–600 bar with hydrate formers in the gas).")
        return
    T = s.T - 273.15
    need = t_h + margin - T
    aq = s.flash.phase("W")
    if aq is not None:
        water = s.F * aq.beta * aq.x[fp.iw] * fp.MW[fp.iw]
        water_note = "free water in the aqueous phase at the design point"
    else:
        water = s.F * s.z[fp.iw] * fp.MW[fp.iw]
        water_note = "all water in the stream (no aqueous phase at the design point)"
    have = 0.0
    if inh in fp.keys:
        have = s.F * s.z[fp.index(inh)] * INHIBITORS[inh]
    k = st.columns(4)
    k[0].metric("Hydrate T (uninhibited)", qfmt("T [°C]", t_h, 1))
    k[1].metric("Required depression", qfmt("ΔT [°C]", max(need, 0), 1), help=f"Hydrate T + margin − {qfmt('T [°C]', T, 1)}")
    if need <= 0:
        k[2].metric("Required inhibitor", "none")
        st.success(f"{sname} is {qfmt('ΔT [°C]', -need, 1)} warmer than hydrate T + margin — "
                   "no thermodynamic inhibitor needed.")
        return
    rich = required_inhibitor_wt(inh, need)
    rate = injection_rate(water, rich, lean)
    k[2].metric("Rich inhibitor in water", f"{rich:.1f} wt%")
    k[3].metric(f"Lean {inh} injection", "∞" if not np.isfinite(rate) else qfmt("Rate [kg/h]", rate, 0),
                help=(f"{lean:.0f} wt% lean solution; {qfmt('Volume [m³/d]', rate / LEAN_RHO[inh] * 24, 1)}"
                      if np.isfinite(rate) else None))
    st.caption(f"Water basis: {qfmt('W [kg/h]', water, 0)} ({water_note}). Currently injected in this stream: "
               f"{qfmt('I [kg/h]', have, 0)} "
               f"of {inh}. {'Hammerschmidt (K = 1297 °C·g/mol)' if inh == 'MEG' else 'Nielsen–Bucklin'}; "
               "methanol losses to the gas and condensate are not included — add 30–50 % for methanol, "
               "and check high MEG concentrations against a rigorous hydrate model.")
    if not np.isfinite(rate):
        st.error("Lean purity must exceed the required rich concentration.")


def analysis_tab():
    ss = st.session_state
    if not sol_is_current():
        st.info("Solve the flowsheet to see the analysis.")
        return
    model, sol = ss.model, ss.sol
    k = _kpis(model, sol)
    cols = st.columns(4)
    for i, (lab, val) in enumerate(k):
        cols[i % 4].metric(lab, val)
    t1, t2, t3, t4, t5, t6 = st.tabs(["Mass balance", "Flow assurance", "Equipment", "Economics & CO₂", "Compositions",
                                      "Before / after"])
    with t6:
        from .phasing import compare_panel
        compare_panel()
    with t5:
        compositions_panel(model, sol)
    with t4:
        economics_panel(model, sol)
    with t1:
        st.plotly_chart(charts.sankey_figure(model, sol), width="stretch", key="an_sankey")
        st.plotly_chart(charts.tp_figure(model, sol), width="stretch", key="an_tp")
    with t2:
        flow_assurance(model, sol)
    with t3:
        c1, c2 = st.columns(2)
        with c1:
            st.plotly_chart(charts.energy_figure(sol), width="stretch", key="an_energy")
        with c2:
            if any(u["type"] == "compressor" for u in model["units"].values()):
                st.plotly_chart(charts.compressor_figure(model, sol), width="stretch", key="an_comp")
        for uid, mp in sol.maps.items():
            fig = (charts.booster_map if mp.get("kind") == "booster" else charts.compressor_map)(
                mp, model["units"][uid]["name"])
            st.plotly_chart(fig, width="stretch", key=f"an_map_{uid}")
        for uid, c in sol.hx_curves.items():
            st.plotly_chart(charts.hx_figure(c, model["units"][uid]["name"]), width="stretch", key=f"an_hx_{uid}")
        for uid, pr in sol.columns.items():
            st.plotly_chart(charts.column_figure(pr, model["units"][uid]["name"]), width="stretch",
                            key=f"an_col_{uid}")
        for uid, pr in sol.profiles.items():
            st.plotly_chart(charts.pipe_figure(pr, model["units"][uid]["name"]), width="stretch",
                            key=f"an_pipe_{uid}")
        if sol.recycle_log:
            st.plotly_chart(charts.recycle_figure(sol.recycle_log), width="stretch", key="an_rcy")
        if sol.adjust_log:
            st.plotly_chart(charts.adjust_figure(sol.adjust_log), width="stretch", key="an_adj")


def compositions_panel(model, sol):
    names = {s["name"]: sid for sid, s in model["streams"].items()
             if sid in sol.streams and not sol.streams[sid].empty}
    prods = [s["name"] for sid, s in model["streams"].items()
             if model["units"][s["dst"][0]]["type"] in ("product",) and s["name"] in names]
    feeds = [s["name"] for sid, s in model["streams"].items()
             if model["units"][s["src"][0]]["type"] == "feed" and s["name"] in names]
    valid_choice("an_comp_sel", names, multi=True)
    sel = st.multiselect("Streams", list(names.keys()), default=(feeds + prods)[:8], key="an_comp_sel")
    basis = st.radio("Basis", ["mole", "mass"], horizontal=True, key="an_comp_basis")
    if sel:
        st.plotly_chart(charts.composition_figure(model, sol, [names[n] for n in sel], basis), width="stretch",
                        key="an_compfig")
    st.markdown("##### Phase envelope")
    if not names:
        return
    valid_choice("ch_env_stream", names)
    pick = st.selectbox("Stream", list(names.keys()), key="ch_env_stream")
    from .panels import env_panel
    env_panel(sol.streams[names[pick]], sol.fp, "charts")


def _econ_set(key, wkey):
    ss = st.session_state
    ss.model.setdefault("economics", {})[key] = ss[wkey]


def economics_panel(model, sol):
    from procsim.economics import compute, params, DEFAULTS
    ss = st.session_state
    p = params(model)
    v = ss.widget_ver
    st.caption("Screening energy OPEX and CO₂ for the current solution. All defaults are illustrative placeholders — "
               "enter your own prices, emission factors and taxes. Settings are saved with the flowsheet.")

    def num(col, label, key, fmt_="%.4g", **kw):
        col.number_input(label, value=float(p[key]), key=f"ec_{key}_{v}", format=fmt_, on_change=_econ_set,
                         args=(key, f"ec_{key}_{v}"), **kw)

    def sel(col, label, key, options):
        col.selectbox(label, options, index=options.index(p[key]) if p[key] in options else 0, key=f"ec_{key}_{v}",
                      on_change=_econ_set, args=(key, f"ec_{key}_{v}"))
    cur = p["currency"]
    c = st.columns(4)
    sel(c[0], "Currency", "currency", ["NOK", "USD", "EUR", "GBP"])
    num(c[1], "Operating hours [h/y]", "hours", "%.0f", min_value=1.0, max_value=8784.0)
    sel(c[2], "Power source", "driver", ["Grid electricity", "Gas turbine"])
    sel(c[3], "Heating medium", "heating", ["Gas-fired heater", "Electric", "Waste heat (free)"])
    c = st.columns(4)
    num(c[0], f"Electricity price [{cur}/MWh]", "el_price", min_value=0.0)
    num(c[1], "Grid emission factor [t CO₂/MWh]", "grid_co2", min_value=0.0)
    num(c[2], "Gas-turbine efficiency [%]", "gt_eff", min_value=5.0, max_value=65.0)
    num(c[3], "Heater efficiency [%]", "heater_eff", min_value=10.0, max_value=100.0)
    c = st.columns(4)
    num(c[0], "Fuel gas LHV [MJ/Sm³]", "fuel_lhv", min_value=1.0)
    num(c[1], f"Fuel gas value [{cur}/Sm³]", "fuel_price", min_value=0.0)
    num(c[2], "Fuel CO₂ factor [kg/Sm³]", "fuel_co2", min_value=0.0)
    num(c[3], f"CO₂ tax + quota [{cur}/t]", "co2_tax", min_value=0.0)
    c = st.columns(4)
    num(c[0], "Seawater pumping [% of cooling duty]", "sw_frac", min_value=0.0, max_value=20.0)
    if c[3].button("Reset to defaults", key=f"ec_reset_{v}", width="stretch"):
        model["economics"] = dict(DEFAULTS)
        ss.widget_ver += 1
        st.rerun()
    r = compute(model, sol)
    t = r["totals"]

    def money(x):
        if x is None:
            return "—"
        a = abs(x)
        return (f"{x / 1e6:,.2f} M{cur}" if a >= 1e6 else f"{x / 1e3:,.1f} k{cur}" if a >= 1e3 else f"{x:,.2f} {cur}")
    k = st.columns(4)
    k[0].metric("Energy cost", money(t["Energy cost [cur/y]"]) + "/y")
    k[1].metric("CO₂ emissions", qfmt("CO₂ [t/y]", t["CO₂ emissions [t/y]"], 0))
    k[2].metric("CO₂ cost", money(t["CO₂ cost [cur/y]"]) + "/y")
    k[3].metric("Energy + CO₂ cost", money(t["Energy + CO₂ cost [cur/y]"]) + "/y")
    k = st.columns(4)
    k[0].metric("CO₂ intensity", "—" if t["CO₂ intensity [kg/boe]"] is None else f"{t['CO₂ intensity [kg/boe]']:.2f} kg/boe",
                help="Product streams only: gas at 1000 Sm³ = 1 Sm³ o.e., hydrocarbon liquids 1:1; 1 Sm³ o.e. = 6.29 boe")
    k[1].metric("Cost per boe", "—" if t["Cost per boe [cur/boe]"] is None else f"{t['Cost per boe [cur/boe]']:.2f} {cur}/boe")
    k[2].metric("Fuel gas", qfmt("Fuel gas [Sm³/h]", t["Fuel gas [Sm³/h]"], 0))
    k[3].metric("Production", f"{t['Production [boe/d]']:,.0f} boe/d")
    if r["rows"]:
        st.plotly_chart(charts.economics_figure(r["rows"], cur), width="stretch", key="an_econ")
        df = pd.DataFrame(r["rows"])
        df["Duty"] = [qfmt("Power [kW]" if row["Category"].startswith("Power") else "Duty [kW]", row["Duty [kW]"], 1)
                      for row in r["rows"]]
        df = df.drop(columns=["Duty [kW]"])
        df = U.df_display(df).rename(columns=lambda c_: c_.replace("cur/y", f"{cur}/y"))
        st.dataframe(df.map(lambda x: fmt(x) if not isinstance(x, str) else x), hide_index=True, width="stretch")
    else:
        st.caption("No energy streams in this flowsheet.")
