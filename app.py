"""ProcessFlow Studio — steady-state process simulator with a drag-and-drop PFD.

Run:  streamlit run app.py
"""
from __future__ import annotations

import json

import streamlit as st

st.set_page_config(page_title="ProcessFlow Studio", page_icon="⚙️", layout="wide",
                   menu_items={"About": "ProcessFlow Studio — made by Merouane Hamdani. For educational purposes only."})

from pfd_canvas import pfd_canvas                     # noqa: E402
from procsim.examples import EXAMPLES                  # noqa: E402
from procsim.flowsheet import new_model, normalize     # noqa: E402
from ui import state as S                              # noqa: E402
from ui import panels                                  # noqa: E402
from ui.fluid import fluid_tab, workbook_tab           # noqa: E402
from ui import theme                                   # noqa: E402

theme.apply_theme()

S.init_state()
ss = st.session_state
from ui import units as UN                             # noqa: E402

UN.set_system(ss.get("units_sys", UN.SI))
S.process_canvas_value("pfd")

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.markdown("### ProcessFlow Studio")
    st.caption("Steady-state simulation · Peng-Robinson · sequential-modular with recycles")
    c1, c2 = st.columns(2)
    if c1.button("▶ Solve", type="primary", width="stretch"):
        S.ensure_solved(force=True)
    ss.auto_solve = c2.toggle("Auto-solve", value=ss.auto_solve,
                              help="Re-solve whenever a specification or connection changes")
    st.divider()
    st.markdown("**Flowsheet**")
    ex = st.selectbox("Example", list(EXAMPLES.keys()), label_visibility="collapsed")
    c1, c2 = st.columns(2)
    if c1.button("Load example", width="stretch"):
        S.load_model(EXAMPLES[ex]())
        st.rerun()
    if c2.button("New (blank)", width="stretch"):
        S.load_model(new_model())
        st.rerun()
    up = st.file_uploader("Open flowsheet (.json)", type=["json"])
    if up is not None and ss.get("_loaded_file") != (up.name, up.size):
        try:
            m = json.loads(up.getvalue().decode("utf-8"))
            if "units" not in m or "streams" not in m or "fluid" not in m:
                raise ValueError("not a ProcessFlow Studio flowsheet")
            ss["_loaded_file"] = (up.name, up.size)
            S.load_model(m)
            st.rerun()
        except Exception as e:
            st.error(f"Could not open file: {e}")
    st.download_button("Save flowsheet (.json)", data=json.dumps(ss.model, indent=1, default=float),
                       file_name="flowsheet.json", mime="application/json", width="stretch")
    st.divider()

    def _units_changed():
        UN.set_system(ss["units_sys"])
        ss.widget_ver += 1

    st.radio("Display units", UN.SYSTEMS, key="units_sys", horizontal=True, on_change=_units_changed,
             help="The engine always calculates in SI; Field converts every display (°F, psia, MMscf/d, bbl/d, "
                  "hp, MMBtu/h, lb/h …)")
    height = st.slider("Diagram height", 420, 1000, 620, 20)
    st.caption("Tips: wheel = zoom · drag background = pan · Shift-drag = box select · "
               "Del = delete · F = flip · Ctrl+D / Ctrl+C·V = duplicate with specs · Ctrl+Z = undo · "
               "double-click = property view")
    theme.sidebar_credit()

S.ensure_solved()
theme.header()

# ------------------------------------------------------------------ main
tab_pfd, tab_an, tab_wb, tab_case, tab_fluid, tab_help = st.tabs(
    ["🧩 Flowsheet", "📈 Analysis", "📋 Workbook", "🔬 Case study", "🧪 Fluid package", "ℹ️ Help & methods"])

with tab_pfd:
    if ss.solve_error:
        st.error(ss.solve_error)
    pfd_canvas(S.canvas_structure(ss.model), S.catalogue_payload(), S.results_payload(), ss.nonce,
               selected=ss.selected, height=height, status=S.status_line(), fit=ss.fit, key="pfd")
    ss.fit = False
    if ss.svg:
        st.download_button("Download diagram (SVG)", data=ss.svg, file_name="pfd.svg", mime="image/svg+xml")
    bad = S.attention_items()
    if bad:
        with st.expander(f"⚠ {len(bad)} item(s) need attention", expanded=False):
            for n, v, e in bad:
                st.markdown(f"- **{n}** — {panels.STATUS_TXT.get(v, v)}: {e}")
    st.markdown("---")
    panels.selection_view()

with tab_wb:
    workbook_tab()

with tab_case:
    from ui.casestudy import case_study_tab
    case_study_tab()

with tab_fluid:
    fluid_tab()

with tab_an:
    from ui.analysis import analysis_tab
    analysis_tab()

with tab_help:
    from ui.help import HELP
    st.markdown(HELP)

theme.footer()
