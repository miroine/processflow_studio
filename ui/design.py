"""Design tab (v6.7): flowline size sweep, gas-lift allocation, ESP sizing and a looped pipe-network solver."""
from __future__ import annotations

import math

import pandas as pd
import streamlit as st

from procsim import debottleneck as DB
from procsim import design as DS
from procsim import network as NW
from procsim.unitops import UnitError
from procsim.flowsheet import port_edges

from .state import sol_is_current, valid_choice, qfmt
from . import charts
from . import units as U


def _floats(text, lo=None):
    out = []
    for t in str(text).replace(";", ",").split(","):
        t = t.strip()
        if t:
            v = float(t)
            if lo is not None and v <= lo:
                raise ValueError(f"{v:g} must be above {lo:g}")
            out.append(v)
    if not out:
        raise ValueError("enter at least one value")
    return sorted(set(out))


def _frame(rows, drop=("uid", "curve", "points")):
    df = pd.DataFrame([{k: v for k, v in r.items() if k not in drop} for r in rows])
    return U.df_display(df)


def _set_design(key, wkey):
    ss = st.session_state
    ss.model.setdefault("design", {})[key] = ss[wkey]


def design_tab():
    ss = st.session_state
    model = ss.model
    sol = ss.sol if sol_is_current() else None
    st.markdown("#### Design tools")
    st.caption("Screening tools for sizing and allocation decisions on top of the solved flowsheet: a flowline "
               "diameter sweep, a gas-lift allocation, an ESP sizing and a looped pipe-network solver, and a topside "
               "debottlenecking study (utilisation of every unit and the first limit as the rate rises).")
    t_db, t_size, t_gl, t_esp, t_net = st.tabs(["Debottlenecking", "Pipe sizing", "Gas-lift allocation", "ESP sizing",
                                                "Pipe network"])
    with t_db:
        if sol is None:
            st.info("Solve the flowsheet first.")
        else:
            debottleneck_panel(model, sol)
    with t_size:
        if sol is None:
            st.info("Solve the flowsheet first.")
        else:
            sizing_panel(model, sol)
    with t_gl:
        if sol is None:
            st.info("Solve the flowsheet first.")
        else:
            gaslift_panel(model, sol)
    with t_esp:
        if sol is None:
            st.info("Solve the flowsheet first.")
        else:
            esp_panel(model, sol)
    with t_net:
        network_panel(model, sol)


# ------------------------------------------------------------------------------------------- debottlenecking
def debottleneck_panel(model, sol):
    ss = st.session_state
    st.caption("Capacity utilisation of every unit that has a capacity in the model: gas load of scrubbers and of "
               "separators with a vessel diameter, compressor flow against the stonewall of its curve, compressor driver "
               "power against its rating, control-valve opening (design limit 85 %) and the erosional-velocity ratio of "
               "lines. 100 % is the limit. Units without a capacity (coolers, heaters, a separator without a diameter) "
               "cannot be checked: set the diameter, curve, driver rating or rated Cv in the unit's properties.")
    rows = DB.utilisation(model, sol)
    if not rows:
        st.info("Nothing to check yet: give a scrubber / separator a diameter, a compressor a performance curve and a "
                "driver rating, or a valve a rated Cv. Load the *Debottlenecking (topside)* example to see it.")
        return
    top = rows[0]
    k = st.columns(4)
    k[0].metric("Highest utilisation", f"{top['Utilisation [%]']:.0f} %", f"{top['Unit']}", delta_color="off")
    k[1].metric("Over capacity", f"{sum(r['Utilisation [%]'] > 100 for r in rows)}")
    k[2].metric("Near the limit (≥ 90 %)", f"{sum(90 <= r['Utilisation [%]'] <= 100 for r in rows)}")
    k[3].metric("Items checked", f"{len(rows)}")
    st.plotly_chart(charts.utilisation_figure(rows), width="stretch", key="db_fig")
    tab = [{"Unit": r["Unit"], "Check": r["Check"], "Utilisation [%]": round(r["Utilisation [%]"], 1),
            "Status": r["Status"], "Note": r["Note"]} for r in rows]
    st.dataframe(pd.DataFrame(tab), hide_index=True, width="stretch")
    st.markdown("##### Throughput sweep: which limit comes first?")
    feeds = {model["units"][u]["name"]: u for u in model["units"] if model["units"][u]["type"] == "feed"}
    valid_choice("db_feeds", feeds, multi=True)
    c = st.columns([3, 3, 1.5])
    f_txt = c[0].text_input("Throughput factors [× present rate]", value="0.8, 1.0, 1.2, 1.4, 1.6", key="db_f")
    pick = c[1].multiselect("Feeds scaled", list(feeds), default=list(feeds), key="db_feeds")
    c[2].markdown("<div style='padding-top:28px'></div>", unsafe_allow_html=True)
    try:
        fs = _floats(f_txt, 1e-6)
    except ValueError as e:
        st.error(f"Check the factors: {e}")
        return
    sig = (ss.sol_hash, tuple(fs), tuple(sorted(pick)))
    if c[2].button("Run throughput sweep", key="db_run", type="primary", width="stretch"):
        if not pick:
            st.error("Choose at least one feed to scale")
        else:
            bar = st.progress(0.0, text=f"Re-solving the flowsheet at {len(fs)} rates…")
            sw = DB.sweep(model, fs, [feeds[n] for n in pick],
                          progress=lambda a, b: bar.progress(a / b, text=f"{a} of {b} rates solved"))
            bar.empty()
            ss["db_result"] = {"sig": sig, "sw": sw}
    res = ss.get("db_result")
    if not res:
        st.caption(f"{len(fs)} rates; each re-solves the whole flowsheet (a few seconds). Press **Run throughput sweep**.")
        return
    if res["sig"] != sig:
        st.info("The results below are for different inputs or an earlier solution - run the throughput sweep again.")
    sw = res["sw"]
    for line in DB.summary(sw):
        st.markdown(line)
    st.plotly_chart(charts.capacity_sweep_figure(sw), width="stretch", key="db_sweep_fig")
    t = [{"Unit": r["Unit"], "Check": r["Check"],
          "Limit reached at [× present rate]": None if r["Limit reached at (× base)"] is None
          else round(r["Limit reached at (× base)"], 3), "How": r["How"], "Remedy": r["Remedy"]} for r in sw["series"]]
    st.dataframe(pd.DataFrame(t), hide_index=True, width="stretch")
    st.caption("Utilisation is not exactly proportional to the rate (pressures, densities and compressor heads change), "
               "which is why every rate is a full flowsheet solve. A limit beyond the sweep is extrapolated from its last "
               "two points. Surge (turndown) is not part of the debottlenecking check.")


# ------------------------------------------------------------------------------------------- pipe sizing
def sizing_panel(model, sol):
    from .surf import downstream_riser
    ss = st.session_state
    fls = {model["units"][u]["name"]: u for u in model["units"] if model["units"][u]["type"] == "flowline"}
    if not fls:
        st.info("Add a subsea flowline to run a diameter sweep.")
        return
    st.caption("The flowline (and its riser) is re-solved at each inside diameter at the present rate. Limits: arrival "
               "pressure, erosional velocity ratio ≤ 1, hydrate margin ≥ 0 and no severe riser-base slugging. The wall "
               "follows from the design pressure (thin-wall Barlow with a design factor and a corrosion allowance); "
               "steel mass per km is the cost proxy. The recommendation is the smallest diameter that meets every limit.")
    c = st.columns([2, 3, 1.5, 1.5])
    valid_choice("ds_fl", fls)
    nm = c[0].selectbox("Flowline", list(fls), key="ds_fl")
    ids_txt = c[1].text_input("Inside diameters [mm]", value="200, 250, 300, 350, 400, 450", key="ds_ids")
    P_min = c[2].number_input(f"Min. arrival P [{U.uP()}]", value=float(U.P(40.0)), min_value=0.0, key="ds_pmin",
                              format="%.4g")
    P_min_si = U.to_si("bar(a)", P_min)
    with st.expander("Wall thickness basis", expanded=False):
        b = st.columns(4)
        smys = b[0].number_input("SMYS [MPa]", value=450.0, min_value=100.0, key="ds_smys")
        dfac = b[1].number_input("Design factor [-]", value=0.72, min_value=0.3, max_value=1.0, key="ds_df")
        ca = b[2].number_input("Corrosion allowance [mm]", value=3.0, min_value=0.0, key="ds_ca")
        pd_in = b[3].number_input("Design pressure [bar] (0 = 1.1 × inlet)", value=0.0, min_value=0.0, key="ds_pd")
    try:
        ids = _floats(ids_txt, 0.0)
    except ValueError as e:
        st.error(f"Check the diameters: {e}")
        return
    fl = fls[nm]
    rs = downstream_riser(model, fl)
    sig = (ss.sol_hash, fl, tuple(ids), float(P_min_si), float(smys), float(dfac), float(ca), float(pd_in))
    if st.button("Run the sweep", key="ds_run", type="primary"):
        bar = st.progress(0.0, text=f"Pipe sizing: {len(ids)} diameters…")
        inlet = sol.streams[port_edges(model, fl, "in")["in"][0]]
        rows, rec = DS.size_sweep(sol.fp, inlet, model["units"][fl]["params"],
                                  model["units"][rs]["params"] if rs else None, ids, P_min_si,
                                  pd_in or None, smys, dfac, ca, lambda f: bar.progress(min(max(f, 0.0), 1.0)))
        ss["ds_result"] = {"sig": sig, "rows": rows, "rec": rec, "P_min": P_min_si}
    res = ss.get("ds_result")
    if not res:
        st.caption(f"{len(ids)} diameters; press **Run the sweep** (about 1–2 s per diameter).")
        return
    if res["sig"] != sig:
        st.info("The results below are for different inputs or an earlier solution — press **Run the sweep** again.")
    rec = res["rec"]
    if rec:
        k = st.columns(4)
        k[0].metric("Recommended ID", f"{rec['ID [mm]']:.0f} mm")
        k[1].metric("Wall", f"{rec['Wall [mm]']:.1f} mm")
        k[2].metric("Steel", f"{rec['Steel [t/km]']:.0f} t/km")
        k[3].metric("Arrival pressure", qfmt("Arrival P [bar(a)]", rec["Arrival P [bar(a)]"], 1))
    else:
        st.warning("No diameter in the sweep meets every limit: try larger diameters or a lower arrival pressure.")
    st.plotly_chart(charts.sizing_figure(res["rows"], rec, res["P_min"]), width="stretch", key="ds_fig")
    st.dataframe(_frame(res["rows"]), hide_index=True, width="stretch")


# ------------------------------------------------------------------------------------------- gas lift
def gaslift_panel(model, sol):
    ss = st.session_state
    lifted = [u for u in model["units"] if model["units"][u]["type"] == "well" and (port_edges(model, u, "in").get("lift"))]
    if not lifted:
        st.info("No well has a lift-gas stream connected. Load the *Gas lift and water injection (SURF)* example.")
        return
    st.caption("Each lifted well is re-solved (inflow + tubing with gas lift) at several lift-gas rates and its present "
               "wellhead pressure; the oil rate gives its performance curve (upper concave envelope), and the lift "
               "gas is split to maximise the total oil. A point where the well cannot flow, or the lift gas cannot "
               "reach the valve, is left out. Each point takes a few seconds.")
    c = st.columns([3, 2])
    f_txt = c[0].text_input("Lift-gas rates [× present]", value="0, 0.5, 1, 1.5", key="gl_f")
    now_total = sum(float(sol.results[u].get("Gas-lift rate [MSm³/d]", 0.0)) for u in lifted if u in sol.results)
    supply = c[1].number_input("Gas available [MSm³/d]", value=float(round(now_total, 4)), min_value=0.0, key="gl_supply",
                               format="%.4g")
    try:
        fs = _floats(f_txt, -1e-12)
    except ValueError as e:
        st.error(f"Check the rates: {e}")
        return
    sig = (ss.sol_hash, tuple(fs))
    if st.button("Build the performance curves", key="gl_run", type="primary"):
        bar = st.progress(0.0, text="Gas-lift curves…")
        curves = DS.gaslift_curves(model, sol, tuple(fs), lambda f: bar.progress(min(max(f, 0.0), 1.0)))
        ss["gl_result"] = {"sig": sig, "curves": curves}
    res = ss.get("gl_result")
    if not res:
        st.caption("Press the button to build the curves.")
        return
    if res["sig"] != sig:
        st.info("The curves are for different inputs or an earlier solution — build them again.")
    curves = res["curves"]
    if not curves:
        st.warning("No curve could be built: the wells did not flow at any of the lift rates.")
        return
    try:
        rows = DS.allocation_table(curves, supply)
    except ValueError as e:
        st.error(str(e) + " - raise the gas available.")
        return
    k = st.columns(3)
    now = sum(r["Oil now [Sm³/d]"] for r in rows)
    opt = sum(r["Oil at the optimum [Sm³/d]"] for r in rows)
    k[0].metric("Oil now", qfmt("Rate [Sm³/d]", now, 0))
    k[1].metric("Oil at the optimum", qfmt("Rate [Sm³/d]", opt, 0), f"{opt - now:+,.0f} Sm³/d", delta_color="normal")
    k[2].metric("Lift gas used", qfmt("Gas [MSm³/d]", sum(r["Optimal lift gas [MSm³/d]"] for r in rows), 3))
    st.plotly_chart(charts.gaslift_figure(curves, rows), width="stretch", key="gl_fig")
    st.dataframe(_frame(rows), hide_index=True, width="stretch")
    st.caption("Equal marginal gain: at the optimum every well that is not at its end of the curve has the same "
               "slope [Sm³ oil per MSm³ gas]. Gas beyond the peak of a curve gives no oil, so it is not allocated.")


# ------------------------------------------------------------------------------------------- ESP
def esp_panel(model, sol):
    ss = st.session_state
    names = {model["streams"][sid]["name"]: sid for sid in sol.streams
             if sid in model["streams"] and not sol.streams[sid].empty}
    if not names:
        st.info("No stream with flow.")
        return
    st.caption("Electric submersible pump for the fluid of a stream: total dynamic head from the intake and wellhead "
               "pressures, mixture density and tubing friction; free gas at the intake; options from a small "
               "catalogue of GENERIC illustrative pump curves (replace them with vendor data for a real design) at 50 "
               "and 60 Hz by the affinity laws, with stages, shaft power and motor size. Viscosity derating is not applied.")
    valid_choice("esp_stream", names)
    nm = st.selectbox("Fluid (stream)", list(names), key="esp_stream")
    s0 = sol.streams[names[nm]]
    c = st.columns(5)
    pip = c[0].number_input(f"Pump intake P [{U.uP()}]", value=float(U.P(40.0)), min_value=0.5, key="esp_pip", format="%.4g")
    ti = c[1].number_input("Intake T [°C]", value=float(round(s0.T - 273.15)), key="esp_ti")
    pwh = c[2].number_input(f"Wellhead P [{U.uP()}]", value=float(U.P(20.0)), min_value=0.5, key="esp_pwh", format="%.4g")
    tvd = c[3].number_input("Pump setting depth TVD [m]", value=2000.0, min_value=10.0, key="esp_tvd")
    tid = c[4].number_input("Tubing ID [mm]", value=100.0, min_value=20.0, key="esp_tid")
    try:
        r = DS.esp_design(sol.fp, s0, U.to_si("bar(a)", pip), ti, U.to_si("bar(a)", pwh), tvd, tid)
    except (ValueError, UnitError, ZeroDivisionError) as e:
        st.warning(str(e))
        return
    k = st.columns(4)
    k[0].metric("Total dynamic head", f"{r['Total dynamic head [m]']:,.0f} m")
    k[1].metric("Pressure rise", qfmt("Pressure [bar]", r["Required pressure rise [bar]"], 1))
    k[2].metric("Intake flow", f"{r['Intake flow [m³/d]']:,.0f} m³/d")
    k[3].metric("Free gas at the intake", f"{100 * r['Free gas fraction at the intake [-]']:.0f} %")
    gvf = r["Free gas fraction at the intake [-]"]
    (st.success if gvf < 0.05 else st.warning)(r["Gas handling"])
    if not r["Options"]:
        st.warning("No catalogue pump runs within its recommended range at this flow and head: the catalogue is generic, "
                   "choose a vendor series, or adjust the frequency / flow.")
        with st.expander("All combinations", expanded=False):
            st.dataframe(_frame(r["All"]), hide_index=True, width="stretch")
        return
    st.plotly_chart(charts.esp_figure(r["Options"][:6]), width="stretch", key="esp_fig")
    st.dataframe(_frame(r["Options"]), hide_index=True, width="stretch")


# ------------------------------------------------------------------------------------------- network
def _net_default(key, text):
    return (st.session_state.model.get("design") or {}).get(key, text)


def network_panel(model, sol):
    ss = st.session_state
    st.caption("Steady single-phase network of any topology (loops and meshes solved together). Nodes: **P** fixed pressure "
               "[bar(a)], **Q** supply (+) or withdrawal (−) [MSm³/d gas, m³/h liquid], **J** junction. Gas: isothermal, "
               "constant Z, horizontal; liquid: with elevation. Not a multiphase or transient model.")
    c = st.columns([1, 1, 1, 1, 1])
    phase = c[0].selectbox("Fluid", ["Gas", "Liquid"], key="net_phase")
    if phase == "Gas":
        MW = c[1].number_input("Molecular weight", value=18.5, min_value=2.0, key="net_mw")
        T = c[2].number_input("Temperature [°C]", value=30.0, key="net_T")
        Zf = c[3].number_input("Z [-]", value=0.9, min_value=0.2, max_value=1.2, key="net_z")
        mu = c[4].number_input("Viscosity [cP]", value=0.012, min_value=0.001, format="%.4g", key="net_mu")
        fluid = NW.gas_fluid(MW, T, Zf, mu)
        d_nodes, d_pipes = NW.EXAMPLE_NODES, NW.EXAMPLE_PIPES
    else:
        rho = c[1].number_input("Density [kg/m³]", value=850.0, min_value=300.0, key="net_rho")
        mu = c[2].number_input("Viscosity [cP]", value=5.0, min_value=0.05, format="%.4g", key="net_muL")
        fluid = NW.liquid_fluid(rho, mu)
        d_nodes = ("name, kind, value, elevation_m\nSource, P, 12, 0\nA, J, 0, 20\nB, J, 0, 35\n"
                   "Demand-1, Q, -90, 40\nDemand-2, Q, -60, 25\n")
        d_pipes = ("name, from, to, length_m, ID_mm, roughness_mm\nP1, Source, A, 3000, 250, 0.05\nP2, A, B, 2000, 200, 0.05\n"
                   "P3, A, Demand-2, 1500, 150, 0.05\nP4, B, Demand-1, 1800, 150, 0.05\nP5, Source, B, 4500, 200, 0.05\n")
    key_n, key_p = f"net_nodes_{phase}", f"net_pipes_{phase}"
    v = ss.widget_ver
    cn, cp = st.columns(2)
    wn, wp = f"{key_n}_{v}", f"{key_p}_{v}"
    cn.text_area("Nodes: name, kind, value, elevation_m", value=_net_default(key_n, d_nodes), height=230, key=wn,
                 on_change=_set_design, args=(key_n, wn))
    cp.text_area("Pipes: name, from, to, length_m, ID_mm, roughness_mm", value=_net_default(key_p, d_pipes), height=230,
                 key=wp, on_change=_set_design, args=(key_p, wp))
    try:
        nodes = NW.parse_nodes(ss[wn])
        pipes = NW.parse_pipes(ss[wp])
        res = NW.solve_network(nodes, pipes, fluid)
    except NW.NetworkError as e:
        st.error(str(e))
        return
    flow_key = "Flow [MSm³/d]" if phase == "Gas" else "Flow [m³/h]"
    worst = max(res["pipes"], key=lambda r: r["Erosional velocity ratio (C=100)"])
    k = st.columns(3)
    k[0].metric("Lowest node pressure", qfmt("Pressure [bar(a)]", min(n["Pressure [bar(a)]"] for n in res["nodes"]), 1))
    k[1].metric("Largest pressure drop", qfmt("Pressure drop [bar]", max(abs(r["Pressure drop [bar]"]) for r in res["pipes"]), 2))
    k[2].metric("Highest erosional ratio", f"{worst['Erosional velocity ratio (C=100)']:.2f}", worst["Pipe"], delta_color="off")
    if worst["Erosional velocity ratio (C=100)"] > 1.0:
        st.warning(f"{worst['Pipe']} exceeds the erosional velocity (API RP 14E, C = 100).")
    st.plotly_chart(charts.network_figure(res["nodes"], res["pipes"]), width="stretch", key="net_fig")
    st.markdown("##### Pipes (flow is positive from → to)")
    st.dataframe(_frame(res["pipes"]), hide_index=True, width="stretch")
    st.markdown("##### Nodes")
    st.dataframe(_frame(res["nodes"]), hide_index=True, width="stretch")
