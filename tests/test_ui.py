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
# PFS_REAL_PLOTLY=site  -> use the installed (real) Plotly instead of the Plotly stub (CI);
# PFS_REAL_PLOTLY=<dir>  -> use a real Plotly from that directory.  Every chart is then fully serialised.
if os.environ.get("PFS_REAL_PLOTLY"):
    if os.environ["PFS_REAL_PLOTLY"] != "site":
        sys.path.insert(0, os.environ["PFS_REAL_PLOTLY"])
    import plotly.graph_objects                # noqa: E402,F401  (cached before the stubs go on the path)
    import plotly.subplots                     # noqa: E402,F401
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
        any(any(str(tr.kw.get("name", "")) == "Dew-point line" for tr in f.data) for f in st.HOOK["charts"]), "")
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
        sum(1 for v in a["catalogue"].values() if v["category"] == "Subsea (SURF)") == 15, "")
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
c.check("umbilical tab: booster power cable sized", any("Subsea power cable" in t for t in st.HOOK["texts"]), "")
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
# field-layout drawing (v6.4): JS/SVG component, edits saved in model["layout"] without a re-solve
fm = [x for x in st.HOOK["component_calls"] if x["name"] == "fieldmap_canvas"]
c.check("field-layout drawing component rendered", len(fm) >= 1, "")
dr = fm[-1]["args"]["drawing"] if fm else {"items": [], "lines": [], "services": {}}
kinds = {i["kind"] for i in dr["items"]}
c.check("drawing has the host, a template with 4 slots and the DUTA", {"host", "template", "duta"} <= kinds and
        any(i["kind"] == "template" and len(i["slots"]) == 4 for i in dr["items"]), str(kinds))
c.check("drawing has production, chemical and power lines", {"production", "chemical", "power"} <= set(dr["services"]), "")
c.check("production line labelled with its length", any(ln["service"] == "production" and ln["label"].startswith("~")
                                                       and ln["label"].endswith(" km") for ln in dr["lines"]), "")
tid = next(i["id"] for i in dr["items"] if i["kind"] == "template")
ss["fieldmap"] = {"session": "t", "rev": 1, "event": "move", "id": tid, "x": -3.0, "y": 4.0, "nonce": 0}
run()
c.eq("drag on the drawing saves the position", ss.model["layout"]["pos"][tid], [-3.0, 4.0])
c.eq("drawing edits do not re-solve", ss.sol_hash, h0)
dr = [x for x in st.HOOK["component_calls"] if x["name"] == "fieldmap_canvas"][-1]["args"]["drawing"]
c.check("moved template drawn at its new position", any(i["id"] == tid and (i["x"], i["y"]) == (-3.0, 4.0)
                                                         for i in dr["items"]), "")
run()
c.eq("an event is applied once", ss.model["layout"]["pos"][tid], [-3.0, 4.0])
ss["fieldmap"] = {"session": "t", "rev": 2, "event": "rotate", "id": tid, "rot": 375.0}
run()
c.close("rotation saved (mod 360)", ss.model["layout"]["rot"][tid], 15.0, 1e-12)
ss["fieldmap"] = {"session": "t", "rev": 3, "event": "bend", "pair": f"host|{tid}", "bend": 2.0}
run()
c.close("route bend saved and clamped", ss.model["layout"]["bend"][f"host|{tid}"], 0.8, 1e-12)
dr = [x for x in st.HOOK["component_calls"] if x["name"] == "fieldmap_canvas"][-1]["args"]["drawing"]
c.close("bend sent back to the drawing", dr["bends"][f"host|{tid}"], 0.8, 1e-12)
ss["fieldmap"] = {"session": "t", "rev": 4, "event": "export_svg", "svg": "<svg xmlns='http://www.w3.org/2000/svg'/>"}
run()
c.check("SVG export offered for download", any(k == "download_button" and lab == "Download drawing (SVG)"
                                               for k, lab, _ in st.HOOK["log"]), "")
ss["fieldmap"] = {"session": "t", "rev": 5, "event": "reset"}
run()
c.check("reset clears positions, rotations and bends", not any(k in ss.model["layout"] for k in ("pos", "rot", "bend"))
        and "bearing" in ss.model["layout"], str(ss.model["layout"].keys()))
pending.append((f"fm_title_{ss.widget_ver}", "Demo field"))
run()
c.eq("drawing title saved", ss.model["layout"]["title"], "Demo field")
c.eq("drawing title sent to the component",
     [x for x in st.HOOK["component_calls"] if x["name"] == "fieldmap_canvas"][-1]["args"]["title"], "Demo field")
c.eq("drawing edits never re-solve", ss.sol_hash, h0)
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
dr = [x for x in st.HOOK["component_calls"] if x["name"] == "fieldmap_canvas"][-1]["args"]["drawing"]
c.check("drawing distances in miles in field units", dr["units"]["len"] == "mi" and
        any(ln["label"].endswith(" mi") for ln in dr["lines"] if ln["service"] == "production"), "")
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

# ---- 16f. v5.4: heated flowline, subsea processing units, new boosters ---------------------------------
st.HOOK["errors"].clear()
st.HOOK["values"]["selectbox:Example:"] = "Heated flowline (SURF): DEH oil tie-back with methanol injection and an intensifier"
st.HOOK["press"].add("Load example")
run()
c.check("heated-line view: metrics rendered", any("Annual heating energy" in t for t in st.HOOK["texts"]), "")
c.check("heated-line chart with the unheated comparison", any("Heated line" in str(f.layout.get("title", "")) or
                                                               "heated" in str(f.layout.get("title", "")).lower()
                                                               for f in st.HOOK["charts"]), "")
fl_u = next(u for u, x in ss.model["units"].items() if x["type"] == "flowline")
c.check("heated flowline reports electrical power", ss.sol.results[fl_u].get("Electrical heating power [kW]", 0) > 0, "")
h0 = ss.sol_hash
pending.append((f"heat_shutdowns_{ss.widget_ver}", 10.0))
run()
c.close("heating basis edit saved with the flowsheet", ss.model["heating"]["shutdowns"], 10.0, 1e-12)
c.eq("heating basis edits do not re-solve", ss.sol_hash, h0)
html = build_report(ss.model, ss.sol, None, "Heated")
c.check("report renders for the heated example", "Subsea system" in html or "Heated" in html, "")
st.HOOK["values"]["selectbox:Example:"] = "Subsea compression station (SURF): cooler, separator, compressor and pump"
st.HOOK["press"].add("Load example")
run()
for nm in [u["name"] for u in ss.model["units"].values() if u["type"] in ("subsea_compressor", "subsea_pump")]:
    ss.selected = [uid_by_name(nm)]
    run()
    st.HOOK["press"].add("Generate a typical curve through the current operating point")
    run()
    run()
    c.check(f"performance curve generated: {nm}", bool(ss.model["units"][uid_by_name(nm)]["params"].get("curve")), "")
ss.selected = []
run()
c.check("v5.4 examples render without UI errors", not st.HOOK["errors"], str(st.HOOK["errors"])[:300])

# ---- 16g. v6: field life tab (run, stale notice, report, well-count comparison, apply) ------------------------
st.HOOK["errors"].clear()
st.HOOK["values"]["selectbox:Example:"] = "Heated flowline (SURF): DEH oil tie-back with methanol injection and an intensifier"
st.HOOK["press"].add("Load example")
run()
c.check("field-life tab invites a run", any("A run takes a minute" in t for t in st.HOOK["texts"]), "")
st.HOOK["press"].add("fl_run")
run()
fl = ss.model.get("fieldlife_result")
c.check("field-life run stored with the flowsheet", fl is not None and fl["summary"]["Recovery factor [%]"] > 0, "")
c.check("production profile and cash flow charts rendered",
        any("Production profile" in str(f.layout.get("title", "")) for f in st.HOOK["charts"]) and
        any("Cash flow" in str(f.layout.get("title", "")) for f in st.HOOK["charts"]), "")
c.check("drainage strategy text shown", any("Drainage strategy" in t for t in st.HOOK["texts"]), "")
h0 = ss.sol_hash
html = build_report(ss.model, ss.sol, None, "Field life")
c.check("report has the field-life section", "Field life (screening)" in html and "Production profile" in html, "")
pending.append((f"fl_disc_{ss.widget_ver}", 10.0))
run()
c.eq("field-life settings do not re-solve the flowsheet", ss.sol_hash, h0)
c.close("discount-rate edit saved with the flowsheet", ss.model["fieldlife"]["disc"], 10.0, 1e-12)
c.check("a settings change marks the stored result as stale",
        any("run again to update" in t for t in st.HOOK["texts"]), "")
c.check("stale results are left out of the report",
        "Field life (screening)" not in build_report(ss.model, ss.sol, None, "Field life"), "")
ss["fl_counts"] = "2, 3"
st.HOOK["press"].add("fl_sweep")
run()
fl = ss.model.get("fieldlife_result")
c.check("well-count comparison: two cases and a best case", fl is not None and len(fl.get("sweep", [])) == 2 and
        fl.get("best") is not None, str(fl.get("sweep") if fl else None))
c.check("well-count chart rendered", any("Well count" in str(f.layout.get("title", "")) for f in st.HOOK["charts"]), "")
best_n = fl["best"][0]
st.HOOK["press"].add("fl_apply")
run()
c.eq("apply the best well count to the flowsheet",
     sum(u["params"].get("n_par", 1) for u in ss.model["units"].values() if u["type"] == "well"), best_n)
c.check("the flowsheet re-solves after applying", ss.sol is not None and S_.sol_is_current(), "")
pending.append(("units_sys", "Field"))
run()
c.check("field-life tab renders in field units", not st.HOOK["errors"], str(st.HOOK["errors"])[:300])
pending.append(("units_sys", "SI (metric)"))
run()
c.check("v6 field-life tab without UI errors", not st.HOOK["errors"], str(st.HOOK["errors"])[:300])

# ---- 16h. v6: hydrate model selector -----------------------------------------------------------------------------
from procsim.transport import VDWP as _VDWP   # noqa: E402
st.HOOK["values"]["selectbox:Example:"] = "Subsea tie-back: MEG injection + flowline + riser (Beggs & Brill, hydrate check)"
st.HOOK["press"].add("Load example")
run()
h_m = ss.sol_hash
pending.append((f"fl_hydmodel_{ss.widget_ver}", _VDWP))
run()
run()
c.eq("hydrate model selector saved in the fluid package", ss.model["fluid"].get("hydrate_model"), _VDWP)
c.check("changing the hydrate model re-solves", ss.sol_hash != h_m and S_.sol_is_current(), "")
c.check("flow-assurance chart labels the vdW-P curve",
        any("vdW-P" in str(tr.kw.get("name", "")) for f in st.HOOK["charts"] for tr in f.data if hasattr(tr, "kw")), "")

# ---- 16i. v6.1: wax & sand view, shut-in U, depressurisation ------------------------------------------------
st.HOOK["errors"].clear()
st.HOOK["values"]["selectbox:Example:"] = "Heated flowline (SURF): DEH oil tie-back with methanol injection and an intensifier"
st.HOOK["press"].add("Load example")
run()
h0 = ss.sol_hash
c.check("depressurisation table rendered", any("Depressurisation below the hydrate pressure" in t for t in st.HOOK["texts"]), "")
c.check("sand erosion chart rendered", any("Sand erosion of bends" in str(f.layout.get("title", "")) for f in st.HOOK["charts"]), "")
pending.append((f"ws_WAT_{ss.widget_ver}", 60.0))
run()
c.close("WAT entry saved with the flowsheet", ss.model["waxsand"]["WAT"], 60.0, 1e-12)
c.eq("wax settings do not re-solve", ss.sol_hash, h0)
c.check("wax deposition chart rendered", any("wax deposition" in str(f.layout.get("title", "")) for f in st.HOOK["charts"]), "")
pending.append((f"cool_U_mode_{ss.widget_ver}", "Natural convection after shut-in"))
run()
c.eq("shut-in U option saved", ss.model["cooldown"]["U_mode"], "Natural convection after shut-in")
c.check("v6.1 views render without UI errors", not st.HOOK["errors"], str(st.HOOK["errors"])[:300])

# ---- 16j. v6.2: gas lift / injection example, route tab, choke Cv, power supply ---------------------------------
st.HOOK["errors"].clear()
st.HOOK["values"]["selectbox:Example:"] = "Gas lift and water injection (SURF): lifted oil wells, hilly route, seawater injectors"
st.HOOK["press"].add("Load example")
run()
c.check("gas-lift example solved", ss.sol is not None and all(v in ("ok", "warning") for v in ss.sol.status.values()), "")
flu = uid_by_name("FL-100 Flowline")
ss.selected = [flu]
run()
c.check("route tab: seabed chart rendered", any("route" in str(f.layout.get("title", "")) for f in st.HOOK["charts"]), "")
ss.selected = [uid_by_name("IW-1 Water injectors")]
run()
c.check("injection well property view opens", not st.HOOK["errors"], str(st.HOOK["errors"])[:200])
lab = canvas_args()["results"]["units"][uid_by_name("IW-1 Water injectors")]["label"]
c.check("injection well label shows rate and margin", "margin" in lab, lab)
c.check("power supply options table shown", any("Power supply" in t for t in st.HOOK["texts"]), "")
xt = uid_by_name("XT-1")
ss.model["units"][xt]["params"].update({"spec": "Choke opening (Cv)", "Cv_max": 150.0, "opening": 80.0})
ss.selected = [xt]
run()
c.check("choke Cv spec solves in the app", ss.sol.status.get(xt) in ("ok", "warning") and
        ss.sol.results[xt].get("Choke Cv at this opening [US gpm/psi½]") is not None, str(ss.sol.errors.get(xt)))
ss.selected = []
run()
c.check("v6.2 without UI errors", not st.HOOK["errors"], str(st.HOOK["errors"])[:300])

# ---- 16k. v6.3: undo edit, TEG example, traced envelope ---------------------------------------------------------
st.HOOK["errors"].clear()
st.HOOK["values"]["selectbox:Example:"] = "Gas dehydration: TEG contactor with a water dew-point specification"
st.HOOK["press"].add("Load example")
run()
teg = uid_by_name("T-100 TEG contactor")
c.check("TEG example solved", ss.sol.status.get(teg) in ("ok", "warning"), str(ss.sol.errors.get(teg)))
c.eq("loading a flowsheet clears the edit history", ss.get("edit_hist"), [])
ss.selected = [teg]
run()
key = f"w{ss.widget_ver}_{teg}_teg_wt"
c.check("TEG purity widget present", key in ss, key)
pending.append((key, 99.0))
run()
c.close("purity edit applied", ss.model["units"][teg]["params"]["teg_wt"], 99.0, 1e-12)
c.eq("edit recorded for undo", len(ss.get("edit_hist") or []), 1)
st.HOOK["press"].add("undo_edit")
run()
c.close("undo restores the previous purity", ss.model["units"][teg]["params"]["teg_wt"], 99.7, 1e-12)
c.eq("undo consumed the history", len(ss.get("edit_hist") or []), 0)
c.check("undo re-solves (or reuses the cached solution)", S_.sol_is_current(), "")
dry = next(sid for sid, x in ss.model["streams"].items() if x["src"][0] == teg and x["src"][1] == "dry")
ss.selected = [dry]
run()
st.HOOK["press"].add(f"envgo_{dry}")
run()
c.check("traced envelope: cricondenbar shown", any("Cricondenbar" in t for t in st.HOOK["texts"]), "")
ss.selected = []
run()
c.check("v6.3 without UI errors", not st.HOOK["errors"], str(st.HOOK["errors"])[:300])

# ---- 16l. v6.6: regime & corrosion tab (OLGA-style screening) ----------------------------------------------------
st.HOOK["errors"].clear()
st.HOOK["values"]["selectbox:Example:"] = next(n for n in EXAMPLES if n.startswith("Subsea field (SURF)"))
st.HOOK["press"].add("Load example")
run()
h0 = ss.sol_hash
c.check("regime & corrosion tab renders without errors", not st.HOOK["errors"], str(st.HOOK["errors"])[:300])
c.check("emulsion viscosity chart rendered", any("oil-water viscosity" in str(f.layout.get("title", "")) for f in st.HOOK["charts"]), "")
c.check("corrosion inputs and PVT button present", any(k_ == "fa2_pvt_run" for _, _, k_ in st.HOOK["log"]) and
        any(str(k_).startswith("fa2_life_y") for _, _, k_ in st.HOOK["log"]), "")
pending.append((f"fa2_life_y_{ss.widget_ver}", 10.0))
run()
c.close("corrosion life saved with the flowsheet", ss.model["fa2"]["life_y"], 10.0, 1e-12)
c.eq("flow-assurance settings do not re-solve", ss.sol_hash, h0)
st.HOOK["press"].add("Build the PVT table")
run()
c.check("PVT table built and stored", bool(ss.get("fa2_pvt")) and len(ss["fa2_pvt"]["rows"]) == 48, str(len((ss.get("fa2_pvt") or {}).get("rows", []))))
c.check("regime tab: no UI errors after the edits", not st.HOOK["errors"], str(st.HOOK["errors"])[:300])

# ---- 16m. v6.7: Design tab (size sweep, gas-lift allocation, ESP, pipe network) -----------------------------------
st.HOOK["errors"].clear()
st.HOOK["values"]["selectbox:Example:"] = next(n for n in EXAMPLES if n.startswith("Subsea field (SURF)"))
st.HOOK["press"].add("Load example")
run()
c.check("design tab renders without errors", not st.HOOK["errors"], str(st.HOOK["errors"])[:300])
c.check("pipe network solved and charted", any("Node pressures" in str(f.layout.get("title", "")) for f in st.HOOK["charts"]), "")
pending.append(("ds_ids", "250, 300, 400"))
st.HOOK["press"].add("Run the sweep")
run()
c.check("size sweep stored", bool(ss.get("ds_result")) and len(ss["ds_result"]["rows"]) == 3, str(ss.get("ds_result", {}).get("rows", [])[:1]))
c.check("size sweep chart rendered", any("Flowline inside diameter" in str(f.layout.get("title", "")) for f in st.HOOK["charts"]), "")
c.check("size sweep recommends a diameter", (ss["ds_result"]["rec"] or {}).get("ID [mm]") in (250.0, 300.0, 400.0), str(ss["ds_result"]["rec"]))
c.check("ESP panel: no UI errors", not st.HOOK["errors"], str(st.HOOK["errors"])[:300])
h0 = ss.sol_hash
pending.append((f"net_nodes_Gas_{ss.widget_ver}", "Plant, P, 70, 0\nJ, J, 0, 0\nF, Q, 1.0, 0\n"))
pending.append((f"net_pipes_Gas_{ss.widget_ver}", "name, from, to, length_m, ID_mm, roughness_mm\nT, J, Plant, 20000, 250, 0.05\nF1, F, J, 5000, 200, 0.05\n"))
run()
c.check("network text saved with the flowsheet", "net_nodes_Gas" in ss.model.get("design", {}), str(ss.model.get("design")))
c.eq("design settings do not re-solve", ss.sol_hash, h0)
pending.append(("net_phase", "Liquid"))
run()
c.check("liquid network renders", not st.HOOK["errors"], str(st.HOOK["errors"])[:300])
pending.append(("net_phase", "Gas"))
run()
c.check("gas network renders again", not st.HOOK["errors"], str(st.HOOK["errors"])[:300])
pending.append((f"net_nodes_Gas_{ss.widget_ver}", "a, J, 0, 0\n"))
st.HOOK["errors"].clear()
run()
c.check("a bad network is reported in the app (an error message), not raised", len(st.HOOK["errors"]) > 0, str(st.HOOK["errors"])[:200])
st.HOOK["errors"].clear()
pending.append((f"net_nodes_Gas_{ss.widget_ver}", "Plant, P, 70, 0\nJ, J, 0, 0\nF, Q, 1.0, 0\n"))
run()

# ---- 16n. v7.0: Prognosis tab (strategy comparison, uncertainty, summary) - stored results seeded, no long runs ----
from ui.fieldlife import settings_key as _flk            # noqa: E402
st.HOOK["errors"].clear()
st.HOOK["values"]["selectbox:Example:"] = next(n for n in EXAMPLES if n.startswith("Subsea field (SURF)"))
st.HOOK["press"].add("Load example")
run()
c.check("prognosis tab renders without errors (nothing run yet)", not st.HOOK["errors"], str(st.HOOK["errors"])[:300])
c.check("prognosis run buttons present", any(k_ == "pg_run_strat" for _, _, k_ in st.HOOK["log"]) and
        any(k_ == "pg_run_unc" for _, _, k_ in st.HOOK["log"]), "")
h0 = ss.sol_hash
_key = [ss.sol_hash, _flk(ss.model)]
_ann = [{"Year": y, "Gas [MSm³/d]": 4.5 - 0.3 * y, "Oil/condensate [Sm³/d]": 0.0} for y in range(1, 11)]
_rows = [{"Strategy": "Plateau 100 % (as in the flowsheet)", "Best wells": 4, "Recovery factor [%]": 51.0, "Plateau [years]": 3.0,
          "Production years": 14, "NPV [MUSD]": 1000.0, "CAPEX [MUSD]": 900.0, "Unit cost [USD/boe]": 18.0,
          "Boosting starts (year)": None},
         {"Strategy": "Plateau 75 % (smaller facility)", "Best wells": 3, "Recovery factor [%]": 50.0, "Plateau [years]": 4.0,
          "Production years": 15, "NPV [MUSD]": 900.0, "CAPEX [MUSD]": 800.0, "Unit cost [USD/boe]": 19.0,
          "Boosting starts (year)": None}]
_unc_rows = [{"Wells": n_, "Expected NPV [MUSD]": 900.0 + 50 * n_, "NPV P90 [MUSD]": 600.0 + 40 * n_, "NPV P50 [MUSD]": 880.0 + 50 * n_,
              "NPV P10 [MUSD]": 1300.0 + 60 * n_, "P(NPV<0) [%]": 0.0, "RF P90 [%]": 50.0, "RF P50 [%]": 51.0, "RF P10 [%]": 52.0,
              "Plateau P50 [years]": 3.0, "CAPEX [MUSD]": 900.0 + 50 * n_} for n_ in (3, 4)]
_b = {"P90": [x["Gas [MSm³/d]"] * 0.8 for x in _ann], "P50": [x["Gas [MSm³/d]"] for x in _ann],
      "P10": [x["Gas [MSm³/d]"] * 1.2 for x in _ann]}
ss.model["prognosis_result"] = {
    "strategy": {"rows": _rows, "best": ["Plateau 100 % (as in the flowsheet)", 4], "key": _key, "pset": "x",
                 "profiles": {_rows[0]["Strategy"]: _ann, _rows[1]["Strategy"]: _ann}},
    "unc": {"rows": _unc_rows, "samples": [], "per_run": [{"Wells": 3, "Sample": "s0", "NPV [MUSD]": 1.0}], "best": 4,
            "robust": 3, "bands": _b, "bands_all": {}, "kind": "Gas", "n": 10, "ranges": {}, "active": [],
            "in_place_auto": True, "rate_key": "Gas [MSm³/d]", "key": _key, "pset": "x"}}
st.HOOK["errors"].clear()
run()
c.check("prognosis tab renders stored results without errors", not st.HOOK["errors"], str(st.HOOK["errors"])[:300])
c.check("strategy chart rendered", any("Drainage strategies" in str(f.layout.get("title", "")) for f in st.HOOK["charts"]), "")
c.check("uncertainty chart rendered", any("Uncertainty" in str(f.layout.get("title", "")) for f in st.HOOK["charts"]), "")
c.eq("prognosis results do not re-solve", ss.sol_hash, h0)
html = build_report(ss.model, ss.sol, None, "Prognosis")
c.check("report has the prognosis section", "Prognosis (screening)" in html and "Drainage strategies" in html and "most robust" in html, "")
ss.model["fieldlife"] = dict(ss.model.get("fieldlife") or {}, disc=9.0)
c.check("prognosis results go stale when the field-life settings change",
        "Prognosis (screening)" not in build_report(ss.model, ss.sol, None, "Prognosis"), "")
ss.model["fieldlife"]["disc"] = 8.0
ss.model.pop("prognosis_result", None)

# ---- 16o. v7.1: topside examples and the debottlenecking panel ----------------------------------------------------
st.HOOK["errors"].clear()
st.HOOK["values"]["selectbox:Example:"] = next(n for n in EXAMPLES if n.startswith("Debottlenecking (topside)"))
st.HOOK["press"].add("Load example")
run()
c.check("debottlenecking example loads and renders without errors", not st.HOOK["errors"], str(st.HOOK["errors"])[:300])
c.check("utilisation chart rendered", any("Capacity utilisation" in str(f.layout.get("title", "")) for f in st.HOOK["charts"]), "")
c.check("sweep button present", any(k_ == "db_run" for _, _, k_ in st.HOOK["log"]), "")
h0 = ss.sol_hash
ss["db_f"] = "1.0, 1.3"
st.HOOK["press"].add("db_run")
run()
c.check("throughput sweep stored", bool(ss.get("db_result")) and len(ss["db_result"]["sw"]["series"]) >= 6, str(ss.get("db_result", {}).keys()))
c.check("throughput sweep chart rendered", any("Throughput sweep" in str(f.layout.get("title", "")) for f in st.HOOK["charts"]), "")
c.check("sweep summary shows the first limit", any("headroom" in t or "Already at the limit" in t for t in st.HOOK["texts"]), str(st.HOOK["texts"][-3:]))
c.eq("debottlenecking does not re-solve or change the flowsheet", ss.sol_hash, h0)
c.check("debottlenecking: no UI errors after the sweep", not st.HOOK["errors"], str(st.HOOK["errors"])[:300])
for nm_ in [n for n in EXAMPLES if "(topside)" in n]:
    st.HOOK["errors"].clear()
    st.HOOK["values"]["selectbox:Example:"] = nm_
    st.HOOK["press"].add("Load example")
    run()
    bad_ = [u for u, v in ss.sol.status.items() if v in ("error", "missing", "unsolved")]
    c.check(f"topside example loads and solves in the app: {nm_[:30]}", not st.HOOK["errors"] and not bad_, str(st.HOOK["errors"])[:200] + str(bad_))
    if "Gas mixing" in nm_:
        from procsim.streams import stream_properties as _sp
        _sid = next(k_ for k_, d_ in ss.model["streams"].items() if d_["name"] == "Sales gas")
        _w = _sp(ss.sol.streams[_sid], ss.sol.fp)["Wobbe index [MJ/Sm³]"]
        c.close("gas mixing: the Adjust brings the sales gas to a Wobbe index of 50.5", _w, 50.5, 0.01)

# ---- 16p. v7.2: Profile tab (one steady-state solve per time step) --------------------------------------------------
st.HOOK["errors"].clear()
st.HOOK["values"]["selectbox:Example:"] = next(n for n in EXAMPLES if n.startswith("Oil stabilisation"))
st.HOOK["press"].add("Load example")
run()
c.check("profile tab renders on an example without errors", not st.HOOK["errors"], str(st.HOOK["errors"])[:300])
c.check("profile tab offers the table, the fill and the run buttons",
        {"prof_fill", "prof_run"} <= {k_ for _, _, k_ in st.HOOK["log"]}, "")
h0 = ss.sol_hash
ss["prof_n"] = 4
ss["prof_fe"] = 0.6
st.HOOK["press"].add("prof_fill")
run()
rows_ = ss.model.get("profile", {}).get("rows") or []
c.eq("filling the table creates the requested steps", len(rows_), 4)
c.close("last step rate = factor x flowsheet rate", rows_[-1]["Well fluid | flow"], 1800.0, 1e-9)
c.check("the table is shown in an editor", any((k_ or "").startswith("prof_ed_") for _, _, k_ in st.HOOK["log"]), "")
st.HOOK["errors"].clear()
st.HOOK["press"].add("prof_run")
run()
res_ = (ss.get("prof_result") or {}).get("res")
c.check("profile run stored", bool(res_) and len(res_["steps"]) == 4, str(ss.get("prof_result", {}).keys()))
c.eq("every step solved", [s_["status"] for s_ in res_["steps"]], ["ok"] * 4)
c.check("profile chart rendered", any("Profile results" in str(f.layout.get("title", "")) for f in st.HOOK["charts"]), "")
c.eq("running a profile does not re-solve or change the flowsheet", ss.sol_hash, h0)
c.check("profile: no UI errors", not st.HOOK["errors"], str(st.HOOK["errors"])[:300])
c.check("profile survives in the flowsheet but is not part of the solve hash", "profile" in ss.model and
        __import__("ui.state", fromlist=["x"]).model_hash(ss.model) == h0, "")
ss["prof_n"] = 5
st.HOOK["press"].add("prof_fill")
run()
c.check("results are flagged as out of date after the table changes",
        any("run the profile again" in t_ for t_ in st.HOOK["texts"]), str(st.HOOK["texts"][-4:]))
st.HOOK["errors"].clear()
st.HOOK["press"].add("New (blank)")
run()
c.check("profile tab on a blank flowsheet shows a hint, not an error", not st.HOOK["errors"], str(st.HOOK["errors"])[:200])

# ---- 16r. v7.3: Dynamic tab ------------------------------------------------------------------------------------------
st.HOOK["errors"].clear()
st.HOOK["values"]["selectbox:Example:"] = next(n for n in EXAMPLES if n.startswith("Dynamic: HP separator"))
st.HOOK["press"].add("Load example")
run()
c.check("dynamic tab renders on a dynamic example without errors", not st.HOOK["errors"], str(st.HOOK["errors"])[:300])
c.check("dynamic tab offers a run button", "dyn_run" in {k_ for _, _, k_ in st.HOOK["log"]}, "")
c.check("the example carries its dynamic settings", bool(ss.model.get("dynamics", {}).get("events")), "")
h0 = ss.sol_hash
st.HOOK["press"].add("dyn_run")
run()
res_ = (ss.get("dyn_result") or {}).get("res")
c.check("dynamic run stored", bool(res_) and res_["status"] == "ok", str(res_ and res_["message"]))
c.check("dynamic chart rendered", any("Dynamic results" in str(f.layout.get("title", "")) for f in st.HOOK["charts"]), "")
c.check("dynamic: no UI errors", not st.HOOK["errors"], str(st.HOOK["errors"])[:300])
c.eq("running a dynamic simulation does not re-solve", ss.sol_hash, h0)
c.check("dynamic settings are saved with the flowsheet but are not part of the solve hash", "dynamics" in ss.model and
        __import__("ui.state", fromlist=["x"]).model_hash(ss.model) == h0, "")
st.HOOK["errors"].clear()
st.HOOK["values"]["selectbox:Example:"] = next(n for n in EXAMPLES if n.startswith("Dynamic: compressor"))
st.HOOK["press"].add("Load example")
run()
c.check("dynamic tab renders on the compressor example", not st.HOOK["errors"], str(st.HOOK["errors"])[:300])
st.HOOK["errors"].clear()
st.HOOK["values"]["selectbox:Example:"] = next(n for n in EXAMPLES if n.startswith("Oil stabilisation"))
st.HOOK["press"].add("Load example")
run()
c.check("dynamic tab on a flowsheet it cannot handle shows a hint, not an error", not st.HOOK["errors"], str(st.HOOK["errors"])[:300])
st.HOOK["errors"].clear()
st.HOOK["press"].add("New (blank)")
run()
c.check("dynamic tab on a blank flowsheet shows a hint, not an error", not st.HOOK["errors"], str(st.HOOK["errors"])[:200])

# ---- 16s. v7.4: Data tab (import, batch edit, export, Python editor) ----------------------------------------------------
os.environ["PFS_PYTHON_EDITOR"] = "on"


class _Up:
    def __init__(self, name, data):
        self.name, self._d, self.size = name, data, len(data)

    def getvalue(self):
        return self._d


def _uid(name):
    return next(k_ for k_, u_ in ss.model["units"].items() if u_["name"] == name)


st.HOOK["errors"].clear()
st.HOOK["values"]["selectbox:Example:"] = next(n for n in EXAMPLES if n.startswith("Oil stabilisation"))
st.HOOK["press"].add("Load example")
run()
c.check("data tab renders without errors", not st.HOOK["errors"], str(st.HOOK["errors"])[:300])
keys_ = {k_ for _, _, k_ in st.HOOK["log"]}
c.check("data tab offers the uploader, the downloads and the editor", {"dat_up", "dat_run", "dat_dl_xl", "dat_dl_zip", "dat_exp_csv"} <= keys_, str(sorted(k for k in keys_ if k and k.startswith("dat_"))))
h0 = ss.sol_hash
p0 = ss.model["units"][_uid("VLV-100")]["params"]["P_out"]
n0 = len(ss.get("edit_hist") or [])
st.HOOK["values"]["dat_up"] = _Up("changes.csv", b"Unit,Parameter,Value\nVLV-100,P_out,22.5\nE-300,T_out,35\n")
st.HOOK["errors"].clear()
run()
c.check("importing a CSV shows the plan without changing the flowsheet", not st.HOOK["errors"] and ss.model["units"][_uid("VLV-100")]["params"]["P_out"] == p0, str(st.HOOK["errors"])[:300])
c.check("the apply button is offered", "dat_imp_apply" in {k_ for _, _, k_ in st.HOOK["log"]}, "")
st.HOOK["press"].add("dat_imp_apply")
run()
c.close("applying the import writes the value", ss.model["units"][_uid("VLV-100")]["params"]["P_out"], 22.5, 0)
c.eq("and records one undo step", len(ss.get("edit_hist") or []), n0 + 1)
c.check("the flowsheet was re-solved", ss.sol_hash != h0 and ss.sol is not None, "")
S_ = __import__("ui.state", fromlist=["x"])
S_.undo_edit()
c.close("undo restores the value", ss.model["units"][_uid("VLV-100")]["params"]["P_out"], p0, 0)
st.HOOK["values"]["dat_up"] = _Up("bad.csv", b"Unit,Parameter,Value\nNOPE,P_out,1\n")
st.HOOK["errors"].clear()
run()
c.check("a file with an error is shown as a message, not raised", any("cannot be applied" in e_ for e_ in st.HOOK["errors"]), str(st.HOOK["errors"])[:300])
st.HOOK["errors"].clear()
st.HOOK["values"]["dat_up"] = _Up("c.yaml", b"units:\n  K-300 LP comp: {eff: 82}\n")
run()
st.HOOK["press"].add("dat_imp_apply")
run()
c.close("a YAML import applies", ss.model["units"][_uid("K-300 LP comp")]["params"]["eff"], 82.0, 0)
st.HOOK["values"].pop("dat_up", None)
# batch operation
ss["dat_btypes"] = ["cooler"]
ss["dat_bparam"] = "dP"
ss["dat_bop"] = "Multiply by"
ss["dat_bval"] = "2"
st.HOOK["values"]["radio:Edit:"] = "One operation on many units"
ss["dat_bmode"] = "One operation on many units"
d0 = ss.model["units"][_uid("E-300")]["params"]["dP"]
run()
c.check("batch operation offers its apply button", "dat_bop_plan_apply" in {k_ for _, _, k_ in st.HOOK["log"]}, str(sorted(k for _, _, k in st.HOOK["log"] if k and "dat_b" in k)))
st.HOOK["press"].add("dat_bop_plan_apply")
run()
c.close("batch multiply applied to the cooler", ss.model["units"][_uid("E-300")]["params"]["dP"], d0 * 2, 1e-12)
ss["dat_bmode"] = "A table of one unit type"
ss["dat_ttype"] = "cooler"
st.HOOK["errors"].clear()
run()
c.check("the unit-type table editor renders", not st.HOOK["errors"] and any((k_ or "").startswith("dat_tbl_") for _, _, k_ in st.HOOK["log"]), str(st.HOOK["errors"])[:300])
ss["dat_bmode"] = "Feed compositions"
run()
c.check("the composition editor renders", not st.HOOK["errors"] and any((k_ or "").startswith("dat_comp_") for _, _, k_ in st.HOOK["log"]), str(st.HOOK["errors"])[:300])
# python editor
ss["dat_code"] = "df = tables['Unit results']\ntables['Power only'] = df[df['Quantity'].str.contains('Power')]\nprint('ok', len(tables))\n"
st.HOOK["press"].add("dat_run")
run()
ed_ = ss.get("data_edited")
c.check("the script ran and its tables are kept", bool(ed_) and "Power only" in ed_["tables"], str(ss.get("_data_run", {}).get("error")))
c.check("print output is available", "ok" in (ss.get("_data_run") or {}).get("stdout", ""), "")
c.check("no UI errors after the script", not st.HOOK["errors"], str(st.HOOK["errors"])[:300])
ss["dat_code"] = "import os\n"
st.HOOK["press"].add("dat_run")
run()
c.check("a forbidden script is refused with a message", not (ss.get("_data_run") or {}).get("ok", True) and "not allowed" in (ss.get("_data_run") or {}).get("error", ""), str((ss.get("_data_run") or {}).get("error")))
st.HOOK["errors"].clear()
os.environ.pop("PFS_PYTHON_EDITOR", None)
run()
c.check("with the editor switched off the tab explains how to enable it", not st.HOOK["errors"] and "dat_run" not in {k_ for _, _, k_ in st.HOOK["log"]}, "")
st.HOOK["errors"].clear()
st.HOOK["press"].add("New (blank)")
run()
c.check("data tab on a blank flowsheet shows hints, not errors", not st.HOOK["errors"], str(st.HOOK["errors"])[:300])

# ---- 16z. remembered choices from a previous example (Streamlit Cloud KeyError in compositions_panel) -------
st.HOOK["values"]["selectbox:Example:"] = next(n for n in EXAMPLES if "Gas lift" in n)
st.HOOK["press"].add("Load example")
run()
for k_, v_ in (("ch_env_stream", "No such stream"), ("an_comp_sel", ["No such stream"]), ("fa_path", ["Gone"]),
               ("dose_st", "Gone"), ("tb_fl", "Gone"), ("td_fl", "Gone"), ("surf_budget_well", "Gone"),
               ("hy_rm", "Gone"), ("sc_base", "Gone"), ("sc_chart", "Gone"), ("sc_pick", "Gone"),
               ("fa2_emu", "Gone"), ("fa2_pvt_stream", "Gone"), ("ds_fl", "Gone"), ("esp_stream", "Gone"), ("pg_opts", ["Gone"]), ("db_feeds", ["Gone"])):
    ss[k_] = v_
for uid_ in list(ss.model["units"])[:6]:
    ss[f"cs_key_{uid_}"] = "no_such_param"
st.HOOK["errors"].clear()
run()
c.check("stale choices from another example do not crash the app", not st.HOOK["errors"], str(st.HOOK["errors"])[:400])
c.check("stale phase-envelope stream replaced by a valid one", ss.get("ch_env_stream") in
        {s_["name"] for s_ in ss.model["streams"].values()}, str(ss.get("ch_env_stream")))

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

# ---- 16t. v7.4.1: loading a flowsheet forgets the Profile / Dynamic / Data tab state -------------------------------------
ss["dyn_unit"] = "h"
ss["dyn_tend"] = 7.0
ss["_data_run"] = {"ok": True, "tables": {}, "stdout": "", "error": ""}
ss["data_edited"] = {"tables": {}}
ss["dyn_result"] = {"sig": "x", "res": {}, "unit": "h"}
v0_ = (ss.get("dyn_ver", 0), ss.get("dat_ver", 0))
S_.load_model(EXAMPLES[next(n for n in EXAMPLES if n.startswith("Dynamic: HP separator"))]())
c.check("load_model clears dynamic / data widget state", all(k_ not in ss for k_ in ("dyn_unit", "dyn_tend", "_data_run", "data_edited", "dyn_result")), "")
c.check("load_model bumps the frozen-table versions", ss.get("dyn_ver", 0) > v0_[0] and ss.get("dat_ver", 0) > v0_[1], "")
st.HOOK["errors"].clear()
run()
c.check("the app still renders after a load", not st.HOOK["errors"], str(st.HOOK["errors"])[:300])
c.check("dynamic: untouched vessel volumes are not pinned into the saved settings", not any("volume" in v_ for v_ in (ss.model.get("dynamics", {}).get("nodes") or {}).values()), str(ss.model.get("dynamics", {}).get("nodes"))[:200])

# ---- 18. blank flowsheet -----------------------------------------------------------------------
st.HOOK["press"].add("New (blank)")
run()
c.eq("blank flowsheet", len(ss.model["units"]), 0)
c.check("blank flowsheet status", "0 unit ops" in canvas_args()["status"], canvas_args()["status"])

sys.exit(c.report())
