"""Run every verification suite: python tests/run_all.py"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
suites = [[sys.executable, os.path.join(HERE, f)] for f in ("test_thermo.py", "test_flowsheet.py", "test_surf.py", "test_ui.py", "test_browser.py")]
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
