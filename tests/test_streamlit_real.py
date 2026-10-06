"""Smoke test of the app on the REAL Streamlit and Plotly (streamlit.testing.v1.AppTest).

The other UI suite (test_ui.py) runs on stubs because Streamlit cannot be installed in the build environment.
This one runs in GitHub Actions (see .github/workflows/tests.yml) and locally after `pip install -r requirements.txt`.
It loads every example, opens every property view, switches to field units, presses the main SURF buttons
and checks that no exception and no error box appears. Plotly figures are serialised by Streamlit, so an
invalid Plotly property raises here.

Skipped automatically when the real Streamlit is not installed.

Run:  python tests/test_streamlit_real.py
"""
import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
from check import Checker   # noqa: E402

try:
    from streamlit.testing.v1 import AppTest
except Exception:            # noqa: BLE001  (not installed, or the stub is on the path)
    print("streamlit-real: Streamlit not installed - skipped")
    sys.exit(0)

c = Checker("streamlit-real")
TIMEOUT = float(os.environ.get("PFS_APPTEST_TIMEOUT", "240"))
at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=TIMEOUT)


def problems():
    exc = [f"{e.message}\n{''.join(e.stack_trace[-6:]) if e.stack_trace else ''}" for e in at.exception]
    err = [str(e.value)[:300] for e in at.error]
    return exc, err


def ok(label, allow_errors=False):
    exc, err = problems()
    c.check(f"{label}: no exception", not exc, "\n".join(exc)[:1500])
    if not allow_errors:
        c.check(f"{label}: no error box", not err, " | ".join(err))


def widget(kind, label):
    for w in getattr(at, kind):
        if w.label == label:
            return w
    for w in getattr(at.sidebar, kind):
        if w.label == label:
            return w
    raise KeyError(f"{kind} '{label}' not found")


t0 = time.time()
at.run()
ok("first load")
print(f"  first load {time.time() - t0:.1f} s")

from procsim.examples import EXAMPLES   # noqa: E402

for name in EXAMPLES:
    t = time.time()
    widget("selectbox", "Example").set_value(name)
    widget("button", "Load example").click()
    at.run()
    ok(f"example {name[:50]}")
    model = at.session_state["model"]
    sol = at.session_state["sol"]
    bad = {model["units"][k]["name"]: v for k, v in sol.status.items() if v != "ok"} if sol else "no solution"
    c.check(f"example solves cleanly: {name[:50]}", sol is not None and not bad, str(bad))
    for uid in list(model["units"]):
        at.session_state["selected"] = [uid]
        at.run()
        exc, _ = problems()
        if exc:
            c.check(f"{name[:40]}: property view {model['units'][uid]['name']}", False, "\n".join(exc)[:1500])
    for sid in list(model["streams"])[:3]:
        at.session_state["selected"] = [sid]
        at.run()
        exc, _ = problems()
        if exc:
            c.check(f"{name[:40]}: stream view {model['streams'][sid]['name']}", False, "\n".join(exc)[:1500])
    at.session_state["selected"] = []
    print(f"  {name[:60]}: {time.time() - t:.1f} s")

# field units on the SURF example (every chart and table converted)
widget("selectbox", "Example").set_value(next(n for n in EXAMPLES if n.startswith("Subsea field")))
widget("button", "Load example").click()
at.run()
widget("radio", "Display units").set_value("Field")
at.run()
ok("SURF example in field units")
c.check("field-layout drawing renders on real Streamlit",
        not any("drawing unavailable" in str(w.value) for w in at.warning), str([w.value for w in at.warning])[:300])
try:
    widget("text_input", "Drawing title").set_value("Demo field")
    at.run()
    ok("drawing title edit")
    c.eq("drawing title saved with the flowsheet", at.session_state["model"]["layout"].get("title"), "Demo field")
except KeyError:
    c.check("drawing title input present", False, "")
widget("radio", "Display units").set_value("SI (metric)")
at.run()

# a stream picked by hand in one example must not break the next example (KeyError reported from Streamlit Cloud)
try:
    sb = at.selectbox(key="ch_env_stream")
    sb.set_value(sb.options[-1])
    ms = at.multiselect(key="an_comp_sel")
    ms.set_value([ms.options[-1]])
    at.run()
    widget("selectbox", "Example").set_value(next(n for n in EXAMPLES if n.startswith("Gas dehydration")))
    widget("button", "Load example").click()
    at.run()
    ok("hand-picked streams from the previous example")
    widget("selectbox", "Example").set_value(next(n for n in EXAMPLES if n.startswith("Subsea field")))
    widget("button", "Load example").click()
    at.run()
except KeyError as e:
    c.check("phase-envelope stream picker present", False, str(e))

# SURF buttons: tie-back screening and turndown
for key in ("tb_run", "td_run"):
    try:
        at.button(key=key).click()
        at.run()
        ok(f"SURF button {key}")
    except KeyError:
        c.check(f"SURF button {key} present", False, "")

# field-life tab (v6), if present
try:
    at.button(key="fl_run").click()
    at.run()
    ok("field life run")
except KeyError:
    pass

# scenarios
try:
    at.button(key="sc_save").click()
    at.run()
    ok("scenario saved")
except KeyError:
    c.check("scenario save button present", False, "")

# profile tab (v7.2): fill the table, edit a cell in the real data editor, run, and check the results
try:
    widget("selectbox", "Example").set_value(next(n for n in EXAMPLES if n.startswith("Oil stabilisation")))
    widget("button", "Load example").click()
    at.run()
    at.number_input(key="prof_n").set_value(3)
    at.button(key="prof_fill").click()
    at.run()
    ok("profile table filled")
    c.eq("profile table has three rows", len(at.session_state["model"]["profile"]["rows"]), 3)
    at.button(key="prof_run").click()
    at.run()
    ok("profile run")
    res = at.session_state["prof_result"]["res"]
    c.eq("profile: every step solved on real Streamlit", [x["status"] for x in res["steps"]], ["ok"] * 3)
except KeyError as e:
    c.check("profile tab widgets present", False, str(e))

# dynamic tab (v7.3): load a dynamic example, run in the real data editors and check the result
try:
    widget("selectbox", "Example").set_value(next(n for n in EXAMPLES if n.startswith("Dynamic: HP separator")))
    widget("button", "Load example").click()
    at.run()
    ok("dynamic example loaded")
    at.button(key="dyn_run").click()
    at.run()
    ok("dynamic run")
    res = at.session_state["dyn_result"]["res"]
    c.eq("dynamic: run completed on real Streamlit", res["status"], "ok")
    c.check("dynamic: mole balance closes", abs(res["balance_error_kmol"]) / res["inventory0"] < 1e-7, "")
except KeyError as e:
    c.check("dynamic tab widgets present", False, str(e))

# blank flowsheet
widget("button", "New (blank)").click()
at.run()
ok("blank flowsheet")
c.eq("blank flowsheet has no units", len(at.session_state["model"]["units"]), 0)

print(f"  total {time.time() - t0:.0f} s")
sys.exit(c.report())
