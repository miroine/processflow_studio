"""Headless UI smoke test: runs app.py against the Streamlit/Plotly stubs and drives it
through the main workflows (canvas events, property views, parameter edits, examples,
fluid package, workbook, charts).

Run:  python tests/test_ui.py
"""
import os
import runpy
import sys
import time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "tests", "stubs"))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
os.chdir(ROOT)

import streamlit as st                     # the stub                                    # noqa: E402
S_ = None
from check import Checker                  # noqa: E402

c = Checker("ui")
APP = os.path.join(ROOT, "app.py")
ss = st.session_state
pending = []   # (widget ident, new value) applied before the next run, callbacks fired


def run(max_reruns=6):
    for _ in range(max_reruns):
        for ident, val in pending:
            ss[ident] = val
            cb = st.HOOK["callbacks"].get(ident)
            if cb:
                cb[0](*cb[1], **cb[2])
        pending.clear()
        st.reset_run()
        try:
            runpy.run_path(APP, run_name="__main__")
            return True
        except st.RerunException:
            continue
    return True


def canvas_args():
    calls = [x for x in st.HOOK["component_calls"] if x["name"] == "pfd_canvas"]
    return calls[-1]["args"] if calls else None


_rev = [0]


def canvas_event(event, model_struct, selected=(), **extra):
    _rev[0] += 1
    ss["pfd"] = dict({"session": "js-test", "rev": _rev[0], "event": event, "nonce": ss.nonce,
                      "model": model_struct, "selected": list(selected)}, **extra)


def uid_by_name(name):
    return next(k for k, u in ss.model["units"].items() if u["name"] == name)


# ---- 1. first load ---------------------------------------------------------------
t = time.time()
run()
a = canvas_args()
c.check("app runs and renders the canvas", a is not None, "")
c.check("canvas gets every unit and stream", len(a["model"]["units"]) == len(ss.model["units"]) and
        len(a["model"]["streams"]) == len(ss.model["streams"]), "")
c.check("default example solved on load", ss.sol is not None and ss.sol.converged, ss.get("solve_error"))
c.check("all canvas streams marked solved", all(v["solved"] for v in a["results"]["streams"].values()), "")
c.check("status line reports solved", "solved" in a["status"], a["status"])
c.check("energy streams passed for compressors", any(e.get("energy") for e in a["results"]["units"].values()), "")
c.check("no error messages on first load", not st.HOOK["errors"], str(st.HOOK["errors"]))
c.check("charts rendered on first load", len(st.HOOK["charts"]) >= 3, str(len(st.HOOK["charts"])))
print(f"  first run {time.time() - t:.1f} s")

# ---- 2. every property view -----------------------------------------------------------
for oid in list(ss.model["units"].keys()) + list(ss.model["streams"].keys()):
    ss.selected = [oid]
    try:
        run()
        ok = True
        msg = ""
    except Exception as e:     # noqa: BLE001
        ok, msg = False, f"{type(e).__name__}: {e}"
    name = (ss.model["units"].get(oid) or ss.model["streams"].get(oid) or {}).get("name", oid)
    c.check(f"property view opens: {name}", ok, msg)

# ---- 3. parameter edit through a widget callback -----------------------------------------
cooler = uid_by_name("E-100")
ss.selected = [cooler]
run()
key = f"w{ss.widget_ver}_{cooler}_T_out"
c.check("cooler outlet-T widget present", key in ss, key)
pending.append((key, 20.0))
run()
c.close("callback wrote the new spec to the model", ss.model["units"][cooler]["params"]["T_out"], 20.0, 1e-12)
res = ss.sol.results[cooler]
c.close("auto-solve re-solved with the new spec", res["Outlet T [°C]"], 20.0, 1e-6)

# ---- 4. canvas events ------------------------------------------------------------------------
from ui.state import canvas_structure   # noqa: E402

struct = canvas_structure(ss.model)
struct["units"]["uJS1"] = {"type": "pump", "name": "P-100", "x": 900, "y": 400, "flip": False}
canvas_event("add", struct, ["uJS1"], added="uJS1")
nonce0 = ss.nonce
run()
c.check("canvas 'add' creates the unit with default params", "uJS1" in ss.model["units"] and
        ss.model["units"]["uJS1"]["params"].get("eff") == 75.0, "")
c.eq("no resync needed for a clean add", ss.nonce, nonce0)
c.check("new unit shows as incomplete", ss.sol.status.get("uJS1") == "missing", ss.sol.status.get("uJS1"))
c.check("property view of the new pump opens", ss.selected == ["uJS1"], str(ss.selected))

struct = canvas_structure(ss.model)
v1 = uid_by_name("V-101 Scrubber")
struct["units"]["uJS2"] = {"type": "product", "name": "Product 1", "x": 1000, "y": 400, "flip": False}
# connect: scrubber liquid is already used -> Python must drop the duplicate and resync the canvas
struct["streams"]["sJS1"] = {"name": "99", "src": [v1, "liquid"], "dst": ["uJS1", "in"]}
struct["streams"]["sJS2"] = {"name": "Product 1", "src": ["uJS1", "out"], "dst": ["uJS2", "in"]}
canvas_event("connect", struct, ["sJS1"])
nonce0 = ss.nonce
run()
c.check("invalid second connection on a single outlet is dropped", "sJS1" not in ss.model["streams"], "")
c.check("canvas resync requested after Python corrected the structure", ss.nonce == nonce0 + 1, f"{nonce0}->{ss.nonce}")
c.check("valid terminal connection kept", "sJS2" in ss.model["streams"], "")

# duplicate unit name from the canvas gets renamed
struct = canvas_structure(ss.model)
struct["units"]["uJS3"] = {"type": "valve", "name": "VLV-100", "x": 50, "y": 50, "flip": False}
canvas_event("add", struct, ["uJS3"])
run()
c.check("duplicate name from the canvas is made unique", ss.model["units"]["uJS3"]["name"] != "VLV-100",
        ss.model["units"]["uJS3"]["name"])

# move only -> no re-solve (hash excludes positions)
h0 = ss.sol_hash
struct = canvas_structure(ss.model)
struct["units"]["uJS3"]["x"] = 70
canvas_event("move", struct, ["uJS3"])
run()
c.eq("moving an icon does not trigger a re-solve", ss.sol_hash, h0)
c.eq("move stored in the model", ss.model["units"]["uJS3"]["x"], 70)

# stale event (older nonce) ignored
struct = canvas_structure(ss.model)
struct["units"].pop("uJS3")
_rev[0] += 1
ss["pfd"] = {"session": "js-test", "rev": _rev[0], "event": "delete", "nonce": -5, "model": struct, "selected": []}
run()
c.check("event from an older canvas version is ignored", "uJS3" in ss.model["units"], "")

# delete through canvas
struct = canvas_structure(ss.model)
for k in ("uJS1", "uJS2", "uJS3"):
    struct["units"].pop(k)
struct["streams"] = {k: v for k, v in struct["streams"].items() if k != "sJS2"}
canvas_event("delete", struct, [])
run()
c.check("canvas delete removes units and streams", not any(k in ss.model["units"] for k in ("uJS1", "uJS2", "uJS3"))
        and "sJS2" not in ss.model["streams"], "")

# export svg event
canvas_event("export_svg", canvas_structure(ss.model), [], svg="<svg>test</svg>")
run()
c.eq("SVG export stored for download", ss.svg, "<svg>test</svg>")
c.check("SVG download button shown", any(x[0] == "download_button" and "SVG" in x[1] for x in st.HOOK["log"]), "")

# ---- 5. rename through the property view -----------------------------------------------------
k101 = uid_by_name("K-101")
ss.selected = [k101]
run()
ss[f"w{ss.widget_ver}_{k101}___name"] = "K-101 HP"
run()
c.eq("rename from the property view", ss.model["units"][k101]["name"], "K-101 HP")

# ---- 6. examples -----------------------------------------------------------------------------------
from procsim.examples import EXAMPLES   # noqa: E402

for name in EXAMPLES:
    st.HOOK["values"]["selectbox:Example:"] = name
    st.HOOK["press"].add("Load example")
    run()
    bad = {ss.model["units"][k]["name"]: v for k, v in ss.sol.status.items() if v != "ok"} if ss.sol else "no sol"
    c.check(f"example loads and solves cleanly: {name}", ss.sol is not None and not bad, str(bad))
    c.check(f"canvas asked to fit after loading: {name}", canvas_args()["fit"] is True, "")
    for oid in list(ss.model["units"].keys()):
        ss.selected = [oid]
        try:
            run()
            ok, msg = True, ""
        except Exception as e:     # noqa: BLE001
            ok, msg = False, f"{type(e).__name__}: {e}"
        if not ok:
            c.check(f"{name}: view {ss.model['units'][oid]['name']}", ok, msg)
    ss.selected = []

# JT example: adjust wrote back its value
st.HOOK["values"]["selectbox:Example:"] = "JT dew-point control: gas/gas exchanger + LTS + Adjust"
st.HOOK["press"].add("Load example")
run()
jt = uid_by_name("VLV-100 JT")
c.close("Adjust result written back to the valve spec", ss.model["units"][jt]["params"]["P_out"],
        ss.sol.adjusted[uid_by_name("ADJ-1")][2], 1e-12)
c.check("adjust links drawn on the canvas", len(canvas_args()["results"]["links"]) == 2, "")

# ---- 7. auto-solve off, manual solve ------------------------------------------------------------
st.HOOK["values"]["toggle:Auto-solve:"] = False
run()
lts = uid_by_name("V-100 LTS")
ss.model["units"][lts]["params"]["dP"] = 1.0
run()
c.check("with auto-solve off, edits leave the solution stale", "not solved" in canvas_args()["status"],
        canvas_args()["status"])
st.HOOK["values"]["toggle:Auto-solve:"] = False
st.HOOK["press"].add("▶ Solve")
run()
c.check("Solve button solves", "solved" in canvas_args()["status"], canvas_args()["status"])
st.HOOK["values"]["toggle:Auto-solve:"] = True
run()

# ---- 8. fluid package: hypothetical component --------------------------------------------------
ss["hy_key"] = "C11+"
st.HOOK["press"].add("Add")
run()
c.check("hypothetical component added to the component list", "C11+" in ss.model["fluid"]["components"], "")
c.check("flowsheet still solves with the hypo present", ss.sol is not None and ss.sol.converged, ss.solve_error)

# ---- 9. phase envelope from the charts tab ------------------------------------------------------
st.HOOK["press"].add("envgo_charts")
t = time.time()
run()
c.check("phase envelope computed and plotted", "env_charts" in ss and
        any(any(type(tr).__name__ == "Contour" for tr in f.data) for f in st.HOOK["charts"]), "")
print(f"  envelope {time.time() - t:.1f} s")

# ---- 10. paste from the canvas copies specifications ----------------------------------------------
st.HOOK["values"]["selectbox:Example:"] = "Two-stage gas compression with liquid recycle"
st.HOOK["press"].add("Load example")
run()
k100 = uid_by_name("K-100")
ss.model["units"][k100]["params"]["eff"] = 81.5
struct = canvas_structure(ss.model)
struct["units"]["uCP1"] = {"type": "compressor", "name": "K-102", "x": 500, "y": 500, "flip": False}
canvas_event("paste", struct, ["uCP1"], copies={"uCP1": k100})
run()
c.close("pasted unit carries the source specification", ss.model["units"]["uCP1"]["params"]["eff"], 81.5, 1e-12)
c.check("pasted params are an independent copy",
        ss.model["units"]["uCP1"]["params"] is not ss.model["units"][k100]["params"], "")

# ---- 11. subsea example: pipe views, hydrate warning -----------------------------------------------
st.HOOK["values"]["selectbox:Example:"] = "Subsea tie-back: MEG injection + flowline + riser (Beggs & Brill, hydrate check)"
st.HOOK["press"].add("Load example")
run()
a = canvas_args()
line = [k for k, x in ss.model["streams"].items() if x["name"] in ("Flowline inlet", "Riser base", "Topside arrival")]
c.check("with MEG injected, the flowline, riser and arrival are not flagged",
        not any(a["results"]["streams"][k].get("warn") for k in line), "")
# analysis tab: KPIs, Sankey, flow-assurance plot with inhibited curve, dosing calculator
c.check("analysis KPIs rendered", any("Shaft power in" in t for t in st.HOOK["texts"]), "")
c.check("Sankey mass-flow chart rendered", any(any(type(tr).__name__ == "Sankey" for tr in f.data) for f in st.HOOK["charts"]), "")
fa = [f for f in st.HOOK["charts"] if "P–T" in str(f.layout.get("title", ""))]
c.check("flow-assurance chart shows uninhibited and inhibited hydrate curves",
        bool(fa) and sum(1 for tr in fa[0].data if "Hydrate curve" in str(tr.kw.get("name", ""))) == 2, "")
c.check("dosing calculator reports a MEG injection rate", any("Lean MEG injection" in t for t in st.HOOK["texts"]), "")
# switch the MEG off: the line must now be flagged
meg = uid_by_name("Lean MEG")
ss.model["units"][meg]["params"]["flow"] = 0.0
run()
a = canvas_args()
c.check("hydrate-risk streams flagged for the canvas", any(v.get("warn") for v in a["results"]["streams"].values()), "")
c.check("attention list mentions the hydrate risk", any("hydrate" in t for t in st.HOOK["texts"]), "")
fl = uid_by_name("PIPE-100 Flowline")
ss.selected = [fl]
run()
c.check("pipe property view shows the profile charts",
        sum(1 for f in st.HOOK["charts"] if "PIPE-100 Flowline" in str(f.layout.get("title", ""))) >= 2, "")
c.check("pipe label on canvas shows ΔP and regime", "ΔP" in canvas_args()["results"]["units"][fl]["label"], "")
c.check("pipe heat loss drawn as an outgoing energy stream",
        canvas_args()["results"]["units"][fl]["energy"][0]["dir"] == "out", "")

# ---- 12. case study -----------------------------------------------------------------------------
run()
ss[f"cs_obj_{ss.cs_sig}"] = "PIPE-100 Flowline"
run()
ss[f"cs_key_{fl}"] = "U"
run()
ss[f"cs_lo_{fl}_U"] = 1.0
ss[f"cs_hi_{fl}_U"] = 10.0
ss["cs_n"] = 3
from ui.casestudy import dependent_options   # noqa: E402
opts = dependent_options(ss.model, ss.sol)
want = [o for o in opts if o[1] == "Topside arrival" and o[2] in ("Temperature [°C]", "Hydrate margin [°C]")]
c.eq("case-study result options include stream properties", len(want), 2)
st.HOOK["values"][f"cs_deps_{ss.cs_sig}_{ss.sol_hash[:8]}"] = want
st.HOOK["press"].add("cs_run")
t = time.time()
run()
case = ss.get("case")
c.check("case study ran 3 cases", case is not None and len(case["df"]) == 3, "")
if case is not None:
    col = "Topside arrival · Temperature [°C]"
    ys = list(case["df"][col])
    c.check("arrival temperature falls as flowline U rises", ys[0] > ys[1] > ys[2], str(ys))
    c.check("all cases converged", bool(case["df"]["Converged"].all()), str(case["df"]["Message"].tolist()))
c.eq("case study leaves the base model unchanged", ss.model["units"][fl]["params"]["U"], 8.0)
print(f"  case study {time.time() - t:.1f} s")

# regression (Streamlit Cloud): sweep a feed temperature AND record that same stream's temperature
ws = uid_by_name("Wellstream")
run()
ss[f"cs_obj_{ss.cs_sig}"] = "Wellstream"
run()
ss[f"cs_key_{ws}"] = "T_C"
run()
ss[f"cs_lo_{ws}_T_C_SI"] = 70.0
ss[f"cs_hi_{ws}_T_C_SI"] = 80.0
ss["cs_n"] = 2
same = [o for o in dependent_options(ss.model, ss.sol) if o[1] == "Wellstream" and o[2] == "Temperature [°C]"]
st.HOOK["values"][f"cs_deps_{ss.cs_sig}_{ss.sol_hash[:8]}"] = same + same     # also picked twice
st.HOOK["press"].add("cs_run")
st.HOOK["errors"].clear()
run()
case = ss.get("case")
c.check("sweeping a variable and recording the same quantity no longer duplicates columns",
        case is not None and not case["df"].columns.duplicated().any() and len(case["df"]) == 2, "")
ys = list(case["df"]["Wellstream · Temperature [°C]"]) if case is not None else []
c.check("recorded feed temperature follows the swept input", [round(y, 6) for y in ys] == [70.0, 80.0], str(ys))

# ---- 13. printable report --------------------------------------------------------------------------
from ui.report import build_report   # noqa: E402
ss.svg = "<svg xmlns='http://www.w3.org/2000/svg'><rect width='10' height='10'/></svg>"
html = build_report(ss.model, ss.sol, ss.svg, "Tie-back screening")
c.check("report embeds the PFD SVG", ss.svg in html, "")
c.check("report lists every stream", all(s["name"] in html for s in ss.model["streams"].values()), "")
c.check("report flags the hydrate risk", "hydrate" in html.lower(), "")
c.check("report is a complete HTML document", html.startswith("<!doctype html>") and html.endswith("</html>"), "")
run()
c.check("report download button rendered", any(x[0] == "download_button" and "report" in x[1] for x in st.HOOK["log"]), "")

# ---- 14. compressor curve panel: generate a curve, switch to curve mode ---------------------------------
st.HOOK["values"]["selectbox:Example:"] = "Oil stabilisation: 3-stage separation + recompression"
st.HOOK["press"].add("Load example")
run()
k2 = uid_by_name("K-200 MP comp")
ss.selected = [k2]
run()
st.HOOK["press"].add(f"w{ss.widget_ver}_{k2}___gencurve")
run()
cur = ss.model["units"][k2]["params"].get("curve") or {}
c.eq("typical curve generated with 8 points", len(cur.get("flow", [])), 8)
c.check("compressor map drawn after generating the curve",
        any("performance map" in str(f.layout.get("title", "")) for f in st.HOOK["charts"]), "")
p_before = ss.sol.results[k2]["Outlet P [bar(a)]"]
ss[f"w{ss.widget_ver}_{k2}_spec"] = "Performance curve"
run()
c.close("switching to curve mode keeps the discharge pressure (curve through the duty point)",
        ss.sol.results[k2]["Outlet P [bar(a)]"], p_before, 0.05)
c.check("canvas label shows the surge margin", "SM" in canvas_args()["results"]["units"][k2]["label"],
        canvas_args()["results"]["units"][k2]["label"])
scr = uid_by_name("V-210 Scrubber")
ss.selected = [scr]
run()
c.check("scrubber sizing tab shows the gas-load bar", any("Gas load" in t for t in st.HOOK["texts"]), "")
c.check("scrubber canvas label shows diameter and load", "load" in canvas_args()["results"]["units"][scr]["label"], "")

# ---- 15. stabiliser column example ------------------------------------------------------------------------
st.HOOK["values"]["selectbox:Example:"] = "Condensate stabiliser column with TVP spec (Adjust)"
st.HOOK["press"].add("Load example")
run()
colu = uid_by_name("T-100 Stabiliser")
c.close("stabiliser example meets its TVP target", ss.sol.results[colu]["Bottoms TVP @ 37.8 °C [bar(a)]"], 0.8, 0.0021)
e = canvas_args()["results"]["units"][colu]["energy"]
c.check("column reboiler drawn as an incoming energy stream in slot 1",
        len(e) == 1 and e[0]["dir"] == "in" and e[0]["slot"] == 1, str(e))
ss.selected = [colu]
run()
c.check("column profile charts rendered", any("stage profiles" in str(f.layout.get("title", "")) for f in st.HOOK["charts"]), "")
c.check("theme footer credits the author", any("Merouane Hamdani" in t for t in st.HOOK["texts"]), "")
c.check("header and disclaimer rendered", any("Educational use only" in t for t in st.HOOK["texts"]) and
        any("not affiliated with or endorsed by Equinor" in t for t in st.HOOK["texts"]), "")

# ---- 16. field units, economics, anti-surge in the UI ---------------------------------------------
st.HOOK["values"]["selectbox:Example:"] = "Two-stage gas compression with liquid recycle"
st.HOOK["press"].add("Load example")
run()
pending.append(("units_sys", "Field"))
run()
a = canvas_args()
lab = [v.get("label", "") for v in a["results"]["streams"].values()]
c.check("field units: canvas stream labels in °F and psia", any("°F" in l and "psia" in l for l in lab), str(lab[:3]))
c.check("field units: compressor label in hp", any("hp" in (v.get("label") or "") for v in a["results"]["units"].values()), "")
e100 = uid_by_name("E-100")
ss.selected = [e100]
run()
key = f"w{ss.widget_ver}_{e100}_T_out"
c.close("field units: the cooler outlet-T input shows °F", ss[key], 35.0 * 1.8 + 32.0, 1e-9)
c.check("field units: input label carries °F", any(x[1] == "Outlet temperature [°F]" for x in st.HOOK["log"]), "")
pending.append((key, 86.0))            # 86 °F = 30 °C
run()
c.close("editing in °F stores SI in the model", ss.model["units"][e100]["params"]["T_out"], 30.0, 1e-9)
c.close("…and the solve uses it", ss.sol.results[e100]["Outlet T [°C]"], 30.0, 1e-6)
from ui.report import build_report   # noqa: E402
html = build_report(ss.model, ss.sol, None, "Field report")
c.check("report in field units", "units: Field" in html and "psia" in html and "Economics" in html, "")
# economics panel: change power source to gas turbine through its widget
run()
wk = f"ec_driver_{ss.widget_ver}"
c.check("economics panel rendered", wk in ss, wk)
pending.append((wk, "Gas turbine"))
run()
c.eq("economics settings stored with the flowsheet", ss.model.get("economics", {}).get("driver"), "Gas turbine")
c.check("economics KPIs rendered", any("CO₂ intensity" in t for t in st.HOOK["texts"]), "")
c.check("economics chart rendered", any("CO₂ by energy stream" in str(f.layout.get("title", "")) for f in st.HOOK["charts"]), "")
h0 = ss.sol_hash
run()
c.eq("editing economics does not trigger a re-solve", ss.sol_hash, h0)
from ui.casestudy import dependent_options   # noqa: E402
c.check("economics results selectable in the case study",
        any(o[0] == "econ" and "intensity" in o[2] for o in dependent_options(ss.model, ss.sol)), "")
# turndown: anti-surge recycle shows on the canvas label
feed = next(k for k, u in ss.model["units"].items() if u["type"] == "feed")
ss.model["units"][feed]["params"]["flow"] = 1.0
run()
k100 = uid_by_name("K-100")
c.check("anti-surge recycle shown on the compressor label", "ASC" in canvas_args()["results"]["units"][k100]["label"],
        canvas_args()["results"]["units"][k100]["label"])
pending.append(("units_sys", "SI (metric)"))
run()
c.check("back to SI: labels in °C", any("°C" in (v.get("label") or "") for v in canvas_args()["results"]["streams"].values()), "")

# ---- 16b. subsea (SURF) example, property views, SURF tab and catalogue ------------------------------
import json as _json                                   # noqa: E402
SURF_EX = "Subsea field (SURF): 4 wells, template, pipe-in-pipe flowline, lazy-wave riser"
st.HOOK["values"]["selectbox:Example:"] = SURF_EX
st.HOOK["press"].add("Load example")
run()
a = canvas_args()
surf_ids = [k for k, u in ss.model["units"].items() if u["type"] in
            ("well", "xmas_tree", "template", "jumper", "flowline", "riser", "subsea_valve")]
c.eq("SURF example has 13 subsea units", len(surf_ids), 13)
c.check("SURF example solved, every subsea unit ok",
        ss.sol is not None and all(ss.sol.status.get(k) == "ok" for k in surf_ids),
        str({ss.model["units"][k]["name"]: ss.sol.errors.get(k) for k in surf_ids if ss.sol.status.get(k) != "ok"}))
c.check("canvas palette offers the SURF group",
        sum(1 for v in a["catalogue"].values() if v["category"] == "Subsea (SURF)") == 8, "")
w1, tmp = uid_by_name("W-1"), uid_by_name("TMP-100 Template")
c.check("well label shows wellhead P and T", a["results"]["units"][w1]["label"].startswith("WH"),
        a["results"]["units"][w1]["label"])
c.eq("template label shows slot use", a["results"]["units"][tmp]["label"], "4/4 slots")
c.check("SURF tab: well table and equipment table rendered",
        any("Wells: inflow and lift" in t for t in st.HOOK["texts"]) and
        any("Subsea equipment" in t for t in st.HOOK["texts"]), "")
c.check("SURF tab: pressure budget chart rendered",
        any("Pressure budget" in str(f.layout.get("title", "")) for f in st.HOOK["charts"]), "")
c.check("SURF tab: riser geometry / hydrate-margin chart rendered",
        any("RSR-100 Riser — geometry" in str(f.layout.get("title", "")) for f in st.HOOK["charts"]), "")
for k in surf_ids:
    ss.selected = [k]
    st.HOOK["errors"].clear()
    run()
    nm = ss.model["units"][k]["name"]
    t = ss.model["units"][k]["type"]
    c.check(f"property view renders without errors: {nm}", not st.HOOK["errors"], str(st.HOOK["errors"])[:200])
    if t in ("well", "jumper", "flowline", "riser"):
        c.check(f"profile tab charts for {nm}",
                sum(1 for f in st.HOOK["charts"] if nm in str(f.layout.get("title", ""))) >= 3, "")
from ui.surf import apply_catalogue, budget_steps, reset_catalogue   # noqa: E402
from procsim import surf as _surf                                     # noqa: E402
steps = budget_steps(ss.model, ss.sol, w1)
c.close("pressure budget starts at reservoir pressure", steps[0][1], 320.0, 1e-9)
arr = next(s_ for s_, x in ss.model["streams"].items() if x["name"] == "Topside arrival")
c.close("pressure budget ends at the arrival pressure", steps[-1][2], ss.sol.streams[arr].P, 1e-9)
c.check("pressure budget steps chain (each inlet = previous outlet, template aside)",
        all(abs(a_[2] - b_[1]) < 1e-9 for a_, b_ in zip(steps, steps[1:]) if "Template" not in b_[0]), str(steps))
c.check("bad catalogue CSV is reported, model untouched",
        apply_catalogue(ss.model, "category,item\nwidget,X\n") is not None and "surf_catalogue" not in ss.model, "")
txt = _surf.to_csv(_surf.rows()).replace(
    "Pipe-in-pipe,Carrier pipe with dry insulation in the annulus,1,", "Pipe-in-pipe,Carrier pipe with dry insulation in the annulus,2.5,")
old_hash = ss.sol_hash
c.check("custom catalogue CSV accepted", apply_catalogue(ss.model, txt) is None and "surf_catalogue" in ss.model, "")
run()
fl = uid_by_name("FL-100 Flowline")
c.check("custom catalogue re-solves the flowsheet", ss.sol_hash != old_hash, "")
c.close("custom catalogue value used by the flowline", ss.sol.results[fl]["U used [W/m²·K]"], 2.5, 1e-12)
c.check("custom catalogue travels with the saved flowsheet",
        _json.loads(_json.dumps(ss.model, default=float))["surf_catalogue"][0]["category"] == "flowline", "")
reset_catalogue(ss.model)
run()
c.close("reset brings back the built-in catalogue", ss.sol.results[fl]["U used [W/m²·K]"], 1.0, 1e-12)
pending.append(("units_sys", "Field"))
st.HOOK["errors"].clear()
run()
c.check("SURF tab renders in field units", not st.HOOK["errors"] and
        any("Pressure [psia]" in str(f.layout.get("yaxis", {}).get("title", "")) for f in st.HOOK["charts"]),
        str(st.HOOK["errors"])[:200])
pending.append(("units_sys", "SI (metric)"))
run()
fx = _json.load(open(os.path.join(ROOT, "tests", "canvas_fixture.json"), encoding="utf-8"))
from procsim.unitops import CATALOGUE as _CAT   # noqa: E402
c.eq("canvas test fixture lists every unit type", sorted(fx["catalogue"]), sorted(_CAT))

# ---- 16c. SURF Phase 2: boosting, CAPEX, umbilical, tie-back screening, report ----------------------------
st.HOOK["values"]["selectbox:Example:"] = "Subsea boosting (SURF): late life, wet-gas compressor, 45 km step-out"
st.HOOK["press"].add("Load example")
run()
a = canvas_args()
bu = uid_by_name("P-100 Subsea compressor")
c.check("boosted example solved", ss.sol is not None and ss.sol.status.get(bu) == "ok", ss.sol.errors.get(bu) if ss.sol else "")
c.check("booster label shows power and GVF", "GVF" in a["results"]["units"][bu]["label"], a["results"]["units"][bu]["label"])
c.eq("booster work drawn as an incoming energy stream", a["results"]["units"][bu]["energy"][0]["dir"], "in")
c.check("CAPEX tab: total and chart rendered", any("Total CAPEX" in t for t in st.HOOK["texts"]) and
        any("CAPEX estimate" in str(f.layout.get("title", "")) for f in st.HOOK["charts"]), "")
c.check("umbilical tab: booster power cable sized", any("Booster power cable" in t for t in st.HOOK["texts"]), "")
ss.selected = [bu]
st.HOOK["errors"].clear()
run()
c.check("booster property view renders", not st.HOOK["errors"], str(st.HOOK["errors"])[:200])
# a cost-basis edit is stored in the model and does not re-solve
h0 = ss.sol_hash
pending.append((f"capex_install_pct_{ss.widget_ver}", 40.0))
run()
c.close("cost basis edit saved with the flowsheet", ss.model.get("capex", {}).get("install_pct"), 40.0, 1e-12)
c.eq("cost basis edits do not re-solve", ss.sol_hash, h0)
from procsim import subsea_design as _sd   # noqa: E402
_, _tot = _sd.equipment_list(ss.model, ss.sol)
c.check("CAPEX follows the edited installation %", any(f"{_tot['Total CAPEX [MUSD]']:,.0f} MUSD" in t for t in st.HOOK["texts"]), "")
# edit the injection services in the data editor
import pandas as _pd   # noqa: E402
svc = _pd.DataFrame([{"Service": "MEG", "Fluid": "MEG (90 wt%)", "Flow [L/h]": 3000.0, "Delivery P [bar(a)]": 0.0}])
st.HOOK["values"][f"umb_services_{ss.widget_ver}"] = svc
run()
c.eq("service table edits are saved in the model", ss.model["umbilical"]["services"][0]["Flow [L/h]"], 3000.0)
c.eq("service edits do not re-solve", ss.sol_hash, h0)
# tie-back screening on a small grid
ss["tb_dist"] = "20, 45"
ss["tb_rates"] = "1"
run()
st.HOOK["press"].add("tb_run")
run()
tb = ss.get("tieback")
c.check("tie-back screening ran 2 cases", tb is not None and len(tb["rows"]) == 2, "")
c.check("tie-back charts rendered", any("arrival pressure" in str(f.layout.get("title", "")) for f in st.HOOK["charts"])
        and any("hydrate margin" in str(f.layout.get("title", "")).lower() and "Tie-back" in str(f.layout.get("title", ""))
                for f in st.HOOK["charts"]), "")
c.check("maximum tie-back table rendered", any("Maximum tie-back distance" in t for t in st.HOOK["texts"]), "")
ss["tb_rates"] = "1, 1.2"
run()
c.check("changed screening inputs mark the results as stale", any("different inputs" in t for t in st.HOOK["texts"]), "")
html = build_report(ss.model, ss.sol, None, "Boosted tie-back")
c.check("report has the subsea section with CAPEX and umbilical",
        "Subsea system (SURF" in html and "CAPEX roll-up" in html and "Umbilical" in html and "kV" in html, "")
pending.append(("units_sys", "Field"))
st.HOOK["errors"].clear()
run()
c.check("SURF Phase 2 panels render in field units", not st.HOOK["errors"] and
        any("Tie-back distance [mi]" in str(f.layout.get("xaxis", {}).get("title", "")) for f in st.HOOK["charts"]),
        str(st.HOOK["errors"])[:200])
pending.append(("units_sys", "SI (metric)"))
run()

# ---- 16d. SURF Phase 3: field layout, slugging, turndown --------------------------------------------------
st.HOOK["values"]["selectbox:Example:"] = SURF_EX
st.HOOK["press"].add("Load example")
run()
c.check("field layout plan view rendered", any("Field layout" in str(f.layout.get("title", "")) for f in st.HOOK["charts"]), "")
lay_fig = next(f for f in st.HOOK["charts"] if "Field layout" in str(f.layout.get("title", "")))
c.eq("layout plan view keeps a 1:1 aspect", lay_fig.layout["yaxis"].get("scaleanchor"), "x")
rsu = uid_by_name("RSR-100 Riser")
h0 = ss.sol_hash
pending.append((f"lay_b_{rsu}_{ss.widget_ver}", 90.0))
run()
c.close("bearing edit saved with the flowsheet", ss.model["layout"]["bearing"][rsu], 90.0, 1e-12)
c.eq("bearing edits do not re-solve", ss.sol_hash, h0)
c.check("slugging view: surge volume and table rendered", any("Arrival surge volume" in t for t in st.HOOK["texts"]), "")
ss["td_rates"] = "0.3, 1"
run()
st.HOOK["press"].add("td_run")
run()
td = ss.get("turndown")
c.check("turndown ran 2 rates", td is not None and len(td["rows"]) == 2, "")
c.check("turndown chart and window rendered", any("Turndown envelope" in str(f.layout.get("title", "")) for f in st.HOOK["charts"])
        and any("Turndown ratio" in t for t in st.HOOK["texts"]), "")
run()                                                   # the slugging view sits before the turndown view
c.check("slugging view picks up the ramp-up sweep-out", any("Ramp-up sweep-out (turndown tab)" in t and "run the turndown" not in t
                                                            for t in st.HOOK["texts"]), "")
html = build_report(ss.model, ss.sol, None, "SURF field")
c.check("report has the slugging table", "Slugging (screening" in html, "")
pending.append(("units_sys", "Field"))
st.HOOK["errors"].clear()
run()
c.check("Phase 3 views render in field units", not st.HOOK["errors"] and
        any("North [mi]" in str(f.layout.get("yaxis", {}).get("title", "")) for f in st.HOOK["charts"]),
        str(st.HOOK["errors"])[:200])
pending.append(("units_sys", "SI (metric)"))
run()

from ui import state as S_   # noqa: E402

# ---- 16e. v5.3: solution cache, auto-solve limit, WHP spec, deliverability, booster map, cool-down, scenarios --
st.HOOK["values"]["selectbox:Example:"] = SURF_EX
st.HOOK["press"].add("Load example")
run()
base_sol, base_hash = ss.sol, ss.sol_hash
xv = uid_by_name("XV-100 SSIV")
ss.model["units"][xv]["params"]["dP"] = 0.5
run()
c.check("a changed spec re-solves", ss.sol_hash != base_hash, "")
ss.model["units"][xv]["params"]["dP"] = 0.0
run()
c.check("changing the spec back reuses the cached solution instantly", ss.sol is base_sol and ss.sol_hash == base_hash, "")
pending.append(("auto_limit", 1.0))
run()
ss.model["units"][xv]["params"]["dP"] = 0.4
run()
c.check("a solve slower than the limit pauses auto-solve", ss.get("auto_paused") is True, "")
ss.model["units"][xv]["params"]["dP"] = 0.45
run()
c.check("while paused, edits do not re-solve and the user is told", not S_.sol_is_current() and
        any("Auto-solve is paused" in t for t in st.HOOK["texts"]), "")
st.HOOK["press"].add("▶ Solve")
run()
c.check("Solve still works while paused", S_.sol_is_current(), "")
pending.append(("auto_limit", 600.0))
run()
c.check("raising the limit resumes auto-solve", not ss.get("auto_paused"), "")
w1 = uid_by_name("W-1")
ss.model["units"][w1]["params"].update({"rate_spec": "Wellhead pressure", "WHP": 170.0})
run()
c.close("WHP spec solved in the app", ss.sol.results[w1]["Wellhead P [bar(a)]"], 170.0, 0.05)
ss.selected = [w1]
run()
st.HOOK["press"].add(f"deliv_{w1}_btn")
run()
c.check("deliverability curve rendered in the well view", any("deliverability" in str(f.layout.get("title", ""))
                                                               for f in st.HOOK["charts"]), "")
c.check("cool-down view rendered", any("Shortest no-touch time" in t for t in st.HOOK["texts"]) and
        any("Cool-down after shut-in" in str(f.layout.get("title", "")) for f in st.HOOK["charts"]), "")
# booster curve in the boosted example
st.HOOK["values"]["selectbox:Example:"] = "Subsea boosting (SURF): late life, wet-gas compressor, 45 km step-out"
st.HOOK["press"].add("Load example")
run()
bu = uid_by_name("P-100 Subsea compressor")
ss.selected = [bu]
run()
st.HOOK["press"].add(f"{bu}__genbcurve_{ss.widget_ver}" if False else "Generate a typical curve through the current operating point")
run()
run()
c.check("booster curve generated from the operating point", bool(ss.model["units"][bu]["params"].get("curve")), "")
c.check("booster map rendered", any("booster map" in str(f.layout.get("title", "")) for f in st.HOOK["charts"]), "")
# scenarios
h0 = ss.sol_hash
st.HOOK["press"].add("sc_save")
run()
c.eq("scenario saved", [x["name"] for x in ss.model.get("scenarios", [])], ["Case 1"])
c.eq("saving a scenario does not re-solve", ss.sol_hash, h0)
ss.model["units"][bu]["params"]["dP"] = 60.0
ss.model["units"][bu]["params"]["curve"] = {}
run()
st.HOOK["press"].add("sc_save")
run()
c.eq("second scenario saved", len(ss.model["scenarios"]), 2)
c.check("scenario comparison rendered", any(type(f.data[0]).__name__ == "Bar" and "Saved scenarios" in str(f.layout.get("title", ""))
                                            for f in st.HOOK["charts"] if f.data), "")
ss["sc_diff"] = True
run()
c.check("difference view renders", not st.HOOK["errors"], str(st.HOOK["errors"])[:200])
html = build_report(ss.model, ss.sol, None, "Scenarios")
c.check("report has the scenario comparison and cool-down", "Scenario comparison" in html and "Cool-down after shut-in" in html,
        f"scenarios={'Scenario comparison' in html} cooldown={'Cool-down after shut-in' in html} surf={'Subsea system' in html}")
ss["sc_pick"] = "Case 1"
st.HOOK["press"].add("sc_load")
run()
c.close("loading a scenario restores its specification", ss.model["units"][bu]["params"]["dP"], 55.0, 1e-12)
c.eq("the loaded flowsheet keeps the scenario list", len(ss.model.get("scenarios", [])), 2)
st.HOOK["press"].add("sc_del")
run()
c.eq("delete a scenario from the tab", [x["name"] for x in ss.model["scenarios"]], ["Case 2"])

# ---- 17. stale modules after an update (the Streamlit Cloud ImportError) ---------------------------
import types   # noqa: E402
stale = types.ModuleType("procsim.streams")          # an old streams module without hydrate_state
stale.stream_properties = sys.modules["procsim.streams"].stream_properties
sys.modules["procsim.streams"] = stale
for name in ("ui.analysis", "ui.state", "ui.panels"):
    sys.modules.pop(name, None)
if hasattr(sys, "_pfs_code_fp"):
    del sys._pfs_code_fp                              # first run of a new app.py in an old process
st.HOOK["errors"].clear()
run()
c.check("stale in-memory modules are purged and re-imported instead of raising ImportError",
        hasattr(sys.modules["procsim.streams"], "hydrate_state") and not st.HOOK["errors"], str(st.HOOK["errors"])[:200])
c.check("app renders normally after the recovery", canvas_args() is not None, "")
sys._pfs_code_fp = -1                                 # files changed on disk since the last run
old_sol = ss.sol
run()
c.check("a code change invalidates the cached solution and re-solves", ss.sol is not old_sol and ss.sol is not None, "")

# ---- 18. blank flowsheet -----------------------------------------------------------------------
st.HOOK["press"].add("New (blank)")
run()
c.eq("blank flowsheet", len(ss.model["units"]), 0)
c.check("blank flowsheet status", "0 unit ops" in canvas_args()["status"], canvas_args()["status"])

sys.exit(c.report())
