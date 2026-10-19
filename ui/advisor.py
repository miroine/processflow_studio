"""Analysis tabs of v7.7: heat integration (pinch targets) and the process advisor."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from procsim import pinch as PI, advisor as AD
from . import charts
from . import units as U
from .state import fmt


def heat_integration_panel(model, sol):
    rows = PI.collect(model, sol)
    st.caption("Heaters, coolers, air coolers and chillers are the process streams; heat exchangers are already integrated. "
               "Screening targets: any hot stream may exchange with any cold stream.")
    if not rows:
        st.info("The flowsheet has no heater, cooler or chiller with a duty - nothing to integrate.")
        return
    c1, c2 = st.columns(2)
    dT = c1.slider("Minimum approach temperature ΔTmin [K]", 2, 40, 10, 1, key="pinch_dt",
                   help="Smaller ΔTmin recovers more heat but needs more exchanger area")
    amb = c2.number_input("Ambient / cooling-medium temperature [°C]", value=15.0, step=1.0, key="pinch_amb",
                          help="Chillers colder than this reject their heat to the refrigeration loop, not to a process stream; they are left out")
    res = PI.analyse(rows, float(dT), float(amb))
    m = st.columns(4)
    kw = lambda v: fmt(U.value("kW", v, power=False)) + " " + U.unit("kW", power=False)    # noqa: E731
    m[0].metric("Heating used today", kw(res["QH_now"]))
    m[1].metric("Heating target", kw(res["QH_min"]))
    m[2].metric("Cooling used today", kw(res["QC_now"]))
    m[3].metric("Heat-recovery potential", kw(res["recovery"]))
    if res.get("note"):
        st.info(res["note"])
    if res["hot_curve"]:
        st.plotly_chart(charts.composite_figure(res), width="stretch", key="an_pinch")
        if res["recovery"] > 1.0:
            st.success(f"Targets: heating {kw(res['QH_min'])} (now {kw(res['QH_now'])}), cooling {kw(res['QC_min'])} "
                       f"(now {kw(res['QC_now'])}). Start by exchanging the hottest cooler duties against the heaters above the pinch.")
    if res["recovery"] > 1.0:
        from procsim import economics as EC
        sv = PI.savings(res, EC.params(model))
        cur = sv["currency"]
        v = st.columns(4)
        v[0].metric(f"Energy + CO₂ saved [{cur}/y]", f"{sv['total_saved']:,.0f}")
        v[1].metric("CO₂ avoided [t/y]", f"{sv['co2_saved_t_y']:,.0f}")
        v[2].metric("Exchanger area (rough) [m²]", f"{sv['area_m2']:,.0f}")
        v[3].metric("Simple payback [y]", f"{sv['payback_y']:.1f}" if sv["payback_y"] else "—")
        st.caption("Priced with the utility and CO₂ assumptions of the Economics & CO₂ tab. The exchanger area assumes U = 500 W/m²K and a mean "
                   "ΔT of 2 × ΔTmin at 3000 per m² installed - a screening figure, not a design.")
    df = pd.DataFrame([{"Unit": s["name"], "Type": s["type"], "Role": "hot (cool)" if s["kind"] == "hot" else "cold (heat)",
                        "Supply T [°C]": s["Ts"], "Target T [°C]": s["Tt"], "Duty [kW]": s["duty"], "CP [kW/K]": s["CP"]}
                       for s in res["streams"]])
    if len(df):
        st.dataframe(U.df_display(df.set_index("Unit")).map(lambda v: fmt(v) if isinstance(v, float) else v), width="stretch")
    if res["skipped"]:
        st.caption("Left out (refrigeration below the ambient temperature): " + ", ".join(s["name"] for s in res["skipped"]))


def advisor_panel(model, sol):
    items = AD.advise(model, sol)
    if not items:
        st.success("No improvement opportunities or design checks were found by the rule set.")
        return
    n_opp = sum(1 for i in items if i["Level"] == "Opportunity")
    st.caption(f"{len(items)} findings, {n_opp} opportunities. Rule-based screening of the solved flowsheet: it points at where "
               "to look; the numbers are estimates.")
    for it in items:
        icon = {"Opportunity": "💡", "Check": "⚠️", "Info": "ℹ️"}[it["Level"]]
        unit = f" — {it['Unit']}" if it["Unit"] and it["Unit"] != "—" else ""
        st.markdown(f"{icon} **{it['Finding']}**{unit}  \n{it['Why / what to do']}")
