"""Run every verification suite: python tests/run_all.py"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
suites = [[sys.executable, os.path.join(HERE, f)] for f in ("test_thermo.py", "test_flowsheet.py", "test_surf.py", "test_fieldlife.py", "test_flowassure.py", "test_process.py", "test_pack_a.py", "test_pack_b.py", "test_pack_c.py", "test_prognosis.py", "test_topside.py", "test_profile.py", "test_dynamic.py", "test_fluid75.py", "test_datatools.py", "test_fieldmap.py", "test_ui.py", "test_browser.py",
                                                         "test_streamlit_real.py")]
try:                                   # real Plotly installed (CI): run the UI suite on it as well
    import plotly.graph_objects as _go     # noqa: F401
    suites.append(["env", "PFS_REAL_PLOTLY=site", sys.executable, os.path.join(HERE, "test_ui.py")])
except ImportError:
    if os.environ.get("PFS_REAL_PLOTLY"):   # a real Plotly in a directory (build environment)
        suites.append([sys.executable, os.path.join(HERE, "test_ui.py")])
if shutil.which("node"):
    suites.append(["node", os.path.join(HERE, "test_canvas.js")])
else:
    print("node not found - skipping canvas tests")
bad = 0
for cmd in suites:
    r = subprocess.run(cmd)
    bad += r.returncode != 0
print("ALL SUITES PASSED" if not bad else f"{bad} suite(s) failed")
sys.exit(1 if bad else 0)
