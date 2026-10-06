"""ProcessFlow Studio — steady-state process simulator with a drag-and-drop PFD.

Run:  streamlit run app.py
"""
from __future__ import annotations

import json

import streamlit as st

st.set_page_config(page_title="ProcessFlow Studio", page_icon="⚙️", layout="wide",
                   menu_items={"About": "ProcessFlow Studio — made by Merouane Hamdani. For educational purposes only."})

# ---------------------------------------------------------------- code-refresh guard
# Streamlit keeps imported modules alive between reruns. After the app's files are replaced (a new
# upload / git push to Streamlit Community Cloud) a half-updated set of modules can stay in memory and
# fail with "ImportError: cannot import name ...". Fingerprint the project's source files on every run
# and drop the project's modules from sys.modules whenever the files change, so they are re-imported
# from disk. Modules of the same name loaded from anywhere else (shadowing packages) are dropped too.
import os                                              # noqa: E402
import sys                                             # noqa: E402
import traceback                                       # noqa: E402

APP_VERSION = "7.3.0"
_ROOT = os.path.dirname(os.path.abspath(__file__))
_PKGS = ("procsim", "ui", "pfd_canvas", "fieldmap_canvas")
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _code_fingerprint():
    items = []
    for pkg in _PKGS:
        for dirpath, _, files in os.walk(os.path.join(_ROOT, pkg)):
            for f in files:
                if f.endswith((".py", ".js", ".html", ".csv")):
                    path = os.path.join(dirpath, f)
                    try:
                        stt = os.stat(path)
                        items.append((path, stt.st_mtime_ns, stt.st_size))
                    except OSError:
                        pass
    return hash(tuple(sorted(items)))


def _purge_project_modules():
    for name in list(sys.modules):
        if name.split(".")[0] in _PKGS:
            del sys.modules[name]


_fp_now = _code_fingerprint()
_code_changed = getattr(sys, "_pfs_code_fp", None) not in (None, _fp_now)
_shadowed = any(n in sys.modules and not os.path.abspath(getattr(sys.modules[n], "__file__", "") or "").startswith(_ROOT)
                for n in _PKGS)
if _code_changed or _shadowed:
    _purge_project_modules()
sys._pfs_code_fp = _fp_now


def _import_project():
    global pfd_canvas, EXAMPLES, new_model, normalize, S, panels, fluid_tab, workbook_tab, theme, ui, procsim
    from pfd_canvas import pfd_canvas
    import fieldmap_canvas   # noqa: F401
    from procsim.examples import EXAMPLES
    from procsim.flowsheet import new_model, normalize
    from ui import state as S
    from ui import panels
    from ui.fluid import fluid_tab, workbook_tab
    from ui import theme
    import ui.analysis, ui.casestudy, ui.help, ui.report, ui.units, ui.surf, ui.scenarios, ui.fieldlife, ui.design, ui.prognosis, ui.timeseries, ui.dynamic   # noqa: E401,F401
    import procsim.surf, procsim.subsea_design, procsim.subsea_ops, procsim.scenarios, procsim.fieldlife, procsim.flowassure, procsim.hydrate, procsim.envelope, procsim.dehydration, procsim.process_units, procsim.flowassure2, procsim.design, procsim.network, procsim.prognosis, procsim.debottleneck, procsim.gasquality, procsim.examples_topside, procsim.fieldmap, procsim.timeseries, procsim.dynamics, procsim.dyn_thermo, procsim.examples_dynamic   # noqa: E401,F401
    import procsim, ui
    if getattr(procsim, "__version__", None) != APP_VERSION or getattr(ui, "__version__", None) != APP_VERSION:
        raise ImportError(f"version mismatch: app.py {APP_VERSION}, procsim {getattr(procsim, '__version__', '?')}, "
                          f"ui {getattr(ui, '__version__', '?')}")


try:
    _import_project()
except ImportError:
    _purge_project_modules()               # one clean retry from disk
    try:
        _import_project()
    except ImportError:
        st.error("**ProcessFlow Studio could not load its own files.** They are probably out of sync after an "
                 "update. Make sure every file from the latest zip is in the repository (including the hidden "
                 "`.streamlit` folder), then reboot the app: on Streamlit Community Cloud open **Manage app → ⋮ → "
                 "Reboot app**; locally, stop and restart `streamlit run app.py`.")
        st.code(traceback.format_exc())
        st.stop()

theme.apply_theme()

S.init_state()
ss = st.session_state
if _code_changed:
    # objects built by the previous code version must not be reused
    ss.sol, ss.sol_hash = None, None
    ss.sol_cache = {}
    ss.widget_ver = ss.get("widget_ver", 0) + 1
from ui import units as UN                             # noqa: E402

UN.set_system(ss.get("units_sys", UN.SI))
procsim.surf.activate(ss.model.get("surf_catalogue"))   # the flowsheet's SURF catalogue feeds the property views
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
    _hist = ss.get("edit_hist") or []
    if st.button("↶ Undo edit" + (f" ({_hist[-1][0]})" if _hist else ""), key="undo_edit", disabled=not _hist,
                 width="stretch", help="Undo the last change made in a property view (canvas changes: Ctrl+Z)"):
        S.undo_edit()
        st.rerun()

    def _limit_changed():
        ss.auto_paused = False

    st.number_input("Auto-solve time limit [s]", min_value=1.0, max_value=600.0, step=1.0, format="%.4g",
                    value=float(ss.get("auto_limit", S.AUTO_LIMIT_DEFAULT)), key="auto_limit", on_change=_limit_changed,
                    help="If a solve takes longer than this, auto-solve pauses until you press Solve — large subsea "
                         "flowsheets stay responsive while you edit several specifications")
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
tab_pfd, tab_an, tab_wb, tab_case, tab_sc, tab_surf, tab_design, tab_life, tab_prog, tab_prof, tab_dyn, tab_fluid, tab_help = st.tabs(
    ["🧩 Flowsheet", "📈 Analysis", "📋 Workbook", "🔬 Case study", "⚖️ Scenarios", "🌊 Subsea (SURF)",
     "🛠️ Design", "📅 Field life", "🎯 Prognosis", "⏱️ Profile", "🌀 Dynamic", "🧪 Fluid package", "ℹ️ Help & methods"])

with tab_pfd:
    if ss.solve_error:
        st.error(ss.solve_error)
    if ss.get("auto_paused") and ss.sol is not None:
        st.info(f"Auto-solve is paused: the last solve took {ss.sol.seconds:.1f} s (limit {ss.get('auto_limit', 10):g} s). "
                "Edit freely, then press **▶ Solve** — or raise the limit in the sidebar.")
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
    ui.casestudy.case_study_tab()

with tab_sc:
    ui.scenarios.scenarios_tab()

with tab_surf:
    ui.surf.surf_tab()

with tab_design:
    ui.design.design_tab()

with tab_life:
    ui.fieldlife.fieldlife_tab()

with tab_prog:
    ui.prognosis.prognosis_tab()

with tab_prof:
    ui.timeseries.profile_tab()

with tab_dyn:
    ui.dynamic.dynamic_tab()

with tab_fluid:
    fluid_tab()

with tab_an:
    ui.analysis.analysis_tab()

with tab_help:
    st.markdown(ui.help.HELP)

theme.footer()
