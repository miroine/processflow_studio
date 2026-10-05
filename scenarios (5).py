"""Scenarios tab: save named cases of the flowsheet with their key results and compare them side by side."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from procsim import scenarios as SC

from .state import sol_is_current, fmt, load_model
from . import charts
from . import units as U

CURRENT = "Current (unsaved)"


def comparison_frame(model, current_kpis, diff_base=None):
    labels, cols = SC.comparison(model.get("scenarios") or [], current_kpis)
    rows = []
    for lab in labels:
        disp = U.key(lab)
        row = {"Result": disp}
        base = cols.get(diff_base, {}).get(lab) if diff_base else None
        for name, kp in cols.items():
            v = kp.get(lab)
            _, dv = U.kv(lab, v) if not isinstance(v, str) else (lab, v)
            if diff_base and name != diff_base and isinstance(v, (int, float)) and isinstance(base, (int, float)):
                _, db = U.kv(lab, base)
                dv = dv - db                                # any unit offset (°F) cancels in the difference
                row[name] = ("+" if dv > 0 else "") + fmt(dv)
            else:
                row[name] = fmt(dv) if not isinstance(dv, str) else dv
        rows.append(row)
    return pd.DataFrame(rows)


def scenarios_tab():
    ss = st.session_state
    model = ss.model
    sol = ss.sol if sol_is_current() else None
    scs = model.get("scenarios") or []
    st.markdown("#### Scenarios")
    st.caption("Save the solved flowsheet as a named case (a copy of every specification plus its key results), "
               "change the design, save again, and compare. Scenarios are stored inside the flowsheet file; loading "
               "one replaces the flowsheet on the canvas (save the current case first if you want to keep it).")
    c1, c2 = st.columns([3, 1])
    name = c1.text_input("Scenario name", value=f"Case {len(scs) + 1}", key=f"sc_name_{len(scs)}")
    c2.markdown("<div style='padding-top:28px'></div>", unsafe_allow_html=True)
    if c2.button("Save current as scenario", key="sc_save", type="primary", disabled=sol is None, width="stretch"):
        try:
            SC.save(model, sol, name)
            st.rerun()
        except ValueError as e:
            st.error(str(e))
    if sol is None:
        st.info("Solve the flowsheet to save it as a scenario.")
    current = SC.scenario_kpis(model, sol) if sol is not None else None
    if not scs:
        st.caption("No scenarios saved yet.")
        return
    names = [s["name"] for s in scs]
    c = st.columns([2, 2])
    diff = c[0].toggle("Show differences to a base case", value=False, key="sc_diff")
    base = c[1].selectbox("Base case", names, key="sc_base") if diff else None
    df = comparison_frame(model, current, base)
    st.dataframe(df, hide_index=True, width="stretch")
    labels, cols = SC.comparison(scs, current)
    numeric = [lab for lab in labels if any(isinstance(kp.get(lab), (int, float)) for kp in cols.values())]
    if numeric:
        pick = st.selectbox("Chart", numeric, key="sc_chart",
                            index=numeric.index("CAPEX [MUSD]") if "CAPEX [MUSD]" in numeric else 0, format_func=U.key)
        vals = []
        for n, kp in cols.items():
            v = kp.get(pick)
            if isinstance(v, (int, float)):
                vals.append((n, U.kv(pick, v)[1]))
        st.plotly_chart(charts.scenario_figure(vals, U.key(pick), CURRENT), width="stretch", key="sc_fig")
    st.download_button("Download comparison (CSV)", data=df.to_csv(index=False), file_name="scenarios.csv",
                       mime="text/csv", key="sc_dl")
    st.markdown("##### Manage")
    m1, m2, m3 = st.columns([2, 1, 1])
    pick_sc = m1.selectbox("Scenario", names, key="sc_pick",
                           format_func=lambda n: f"{n} · saved {next(s['saved'] for s in scs if s['name'] == n)}")
    m2.markdown("<div style='padding-top:28px'></div>", unsafe_allow_html=True)
    m3.markdown("<div style='padding-top:28px'></div>", unsafe_allow_html=True)
    if m2.button("Load into the flowsheet", key="sc_load", width="stretch"):
        load_model(SC.restore(model, pick_sc))
        st.rerun()
    if m3.button("Delete", key="sc_del", width="stretch"):
        SC.delete(model, pick_sc)
        st.rerun()
