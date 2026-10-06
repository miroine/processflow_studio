"""Field-layout drawing (v6.4): procsim.fieldmap.field_drawing and the fieldmap_canvas JS/SVG component.

The browser part loads the component in headless Chromium (Playwright) inside a small harness that plays the
Streamlit host, renders every SURF example, drives it with real mouse and keyboard input and writes screenshots to
tests/_screens/.  It is skipped when Playwright or Chromium is not available.

Run:  python tests/test_fieldmap.py
"""
import json
import math
import os
import sys
import tempfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
from check import Checker   # noqa: E402

from procsim import surf                                    # noqa: E402
from procsim.examples import EXAMPLES                       # noqa: E402
from procsim.flowsheet import solve                         # noqa: E402
from procsim.fieldmap import field_drawing, SERVICES        # noqa: E402

c = Checker("fieldmap")
SURF = {n: f for n, f in EXAMPLES.items() if "(SURF)" in n}
c.check("five or more SURF examples to draw", len(SURF) >= 5, str(len(SURF)))

DRAW = {}
for n, f in SURF.items():
    m = f()
    sol = solve(m)
    d = field_drawing(m, sol)
    DRAW[n] = (m, sol, d)
    ids = [i["id"] for i in d["items"]]
    tag = n.split(":")[0]
    c.check(f"{tag}: JSON-serialisable", bool(json.dumps(d)), "")
    c.check(f"{tag}: unique item ids", len(ids) == len(set(ids)), str(ids))
    c.check(f"{tag}: every line joins two drawn items", all(ln["a"] in ids and ln["b"] in ids for ln in d["lines"]),
            str([(ln["a"], ln["b"]) for ln in d["lines"] if ln["a"] not in ids or ln["b"] not in ids]))
    c.check(f"{tag}: services are known", set(d["services"]) <= set(SERVICES), str(d["services"].keys()))
    c.check(f"{tag}: production reaches the host", any(ln["service"] == "production" and ln["b"] == "host"
                                                       for ln in d["lines"]), "")
    c.check(f"{tag}: one length label per flowline route",
            len([ln for ln in d["lines"] if ln["label"]]) == len({tuple(ln["names"]) for ln in d["lines"] if ln["label"]}), "")
    tm = [i for i in d["items"] if i["kind"] == "template"]
    c.check(f"{tag}: template slots match the catalogue", all(
        len(t["slots"]) == max(int(surf.item("template", m["units"][t["id"]]["params"].get("template")).get("slots") or 4),
                               sum(1 for s in t["slots"] if s["kind"] != "spare")) for t in tm), "")
    c.check(f"{tag}: every wet structure has an umbilical and a cable", all(
        any(ln["service"] == "chemical" and ln["b"] == i["id"] for ln in d["lines"]) and
        any(ln["service"] == "power" and ln["b"] == i["id"] for ln in d["lines"])
        for i in d["items"] if i["kind"] in ("template", "satellite", "injector", "booster", "compressor")), "")
    ext = d["extent_km"]
    small = [i for i in d["items"] if i["kind"] not in ("host", "template")]
    close = [(a["name"], b["name"]) for k, a in enumerate(d["items"]) for b in d["items"][k + 1:]
             if math.hypot(a["x"] - b["x"], a["y"] - b["y"]) < 0.05 * ext and (a in small or b in small)]
    c.check(f"{tag}: structures drawn apart", not close, str(close))

# gas lift and water injection
m, sol, d = DRAW[next(n for n in SURF if "Gas lift" in n)]
tm = next(i for i in d["items"] if i["kind"] == "template")
c.check("gas-lifted wells flagged on their slots", sum(s["gl"] for s in tm["slots"]) == 2, str(tm["slots"]))
c.check("gas-lift line host -> template", any(ln["service"] == "gaslift" and ln["a"] == "host" and ln["b"] == tm["id"]
                                              for ln in d["lines"]), "")
inj = next(i for i in d["items"] if i["kind"] == "injector")
c.check("water-injection line to the injector", any(ln["service"] == "water" and ln["b"] == inj["id"] for ln in d["lines"]), "")
c.check("injector template: one injector slot per well, the rest spare",
        [s["kind"] for s in inj["slots"]] == ["injector"] * 2 + ["spare"] * 2 and inj["slots"][1]["name"] == "IW-1-2",
        str(inj["slots"]))
c.check("results in the info line", "bar" in tm["info"] and "Sm³/d" in inj["info"], tm["info"] + " / " + inj["info"])

# subsea hub: two templates daisy-chained, a satellite, a PLEM, gas lift on the north template, injectors
m, sol, d = DRAW[next(n for n in SURF if "hub" in n)]
kinds = [i["kind"] for i in d["items"]]
c.check("hub: two templates, a satellite, a PLEM, an injector and its pump",
        kinds.count("template") == 2 and "satellite" in kinds and "plem" in kinds and "injector" in kinds
        and "booster" in kinds, str(kinds))
nm = {i["id"]: i["name"] for i in d["items"]}
prod = {(nm[ln["a"]], nm[ln["b"]]): ln["label"] for ln in d["lines"] if ln["service"] == "production"}
c.eq("hub: north template daisy-chained 4 km into the central one", prod.get(("TMP-B North", "TMP-A Central")), "~4.0 km")
c.eq("hub: satellite tied in over 3 km", prod.get(("S-1", "TMP-A Central")), "~3.0 km")
c.eq("hub: PLEM to host 7.3 km", prod.get(("PLEM-100", "Host platform")), "~7.3 km")
c.check("hub: gas lift only to the lifted template", [nm[ln["b"]] for ln in d["lines"] if ln["service"] == "gaslift"]
        == ["TMP-B North"], "")
c.check("hub: water from the subsea pump to the injectors", any(
    ln["service"] == "water" and nm[ln["a"]].startswith("P-300") for ln in d["lines"]), "")

# heated line
m, sol, d = DRAW[next(n for n in SURF if "Heated" in n)]
c.check("heated flowline drawn with a dashed DEH line", d["services"].get("heating", {}).get("dashed") is True and
        any(ln["service"] == "heating" for ln in d["lines"]), "")

# compression station: stations in the template's cluster, laid out towards the host, pump joins the compressor
m, sol, d = DRAW[next(n for n in SURF if "compression station" in n)]
items = {i["id"]: i for i in d["items"]}
tm = next(i for i in d["items"] if i["kind"] == "template")
st_ = [i for i in d["items"] if i["kind"] in ("cooler", "separator", "compressor", "booster")]
c.check("stations belong to the template cluster", st_ and all(i.get("cluster") == tm["id"] for i in st_), "")
c.check("stations sit between the template and the host", all(
    math.hypot(i["x"], i["y"]) < math.hypot(tm["x"], tm["y"]) for i in st_), "")
comp = next(i for i in st_ if i["kind"] == "compressor")
pump = next(i for i in st_ if i["kind"] == "booster")
c.check("pump outlet joins the compressor line (no duplicate 40 km line)",
        any(ln["a"] == pump["id"] and ln["b"] == comp["id"] for ln in d["lines"]) and
        sum(1 for ln in d["lines"] if ln["label"]) == 1, "")
c.check("station umbilicals are daisy-chained", all(
    next(ln for ln in d["lines"] if ln["service"] == "power" and ln["b"] == i["id"])["a"] != "duta" for i in st_), "")

# overrides
m, sol, d0 = DRAW[next(n for n in SURF if "4 wells" in n)]
tid = next(i["id"] for i in d0["items"] if i["kind"] == "template")
m["layout"] = dict(m.get("layout") or {}, pos={tid: [-5.0, 2.5]}, rot={tid: 30.0}, bend={"host|" + tid: -0.3},
                   title="X")
d = field_drawing(m, sol)
t = next(i for i in d["items"] if i["id"] == tid)
c.eq("position override", (t["x"], t["y"]), (-5.0, 2.5))
c.eq("rotation override", t["rot"], 30.0)
c.eq("bend passed to the drawing", d["bends"], {"host|" + tid: -0.3})
d = field_drawing(m, sol, length_unit=("mi", 1 / 1.609344))
c.check("length labels in the chosen unit", any(ln["label"].endswith(" mi") for ln in d["lines"]), "")
c.check("no solution: still drawn", bool(field_drawing(m, None)["items"]), "")

# ---------------------------------------------------------------------------- browser
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("fieldmap browser part: playwright not installed - skipped")
    sys.exit(c.report())

FRONT = os.path.abspath(os.path.join(ROOT, "fieldmap_canvas", "frontend", "index.html"))
OUT = os.path.join(ROOT, "tests", "_screens")
os.makedirs(OUT, exist_ok=True)
HARNESS = f"""<!doctype html><html><head><meta charset="utf-8"><style>body{{margin:0}}iframe{{border:0;width:1300px}}
</style></head><body><iframe id="f" src="file://{FRONT}"></iframe><script>
window.__values=[]; window.__ready=false; const f=document.getElementById('f');
window.addEventListener('message',(e)=>{{const d=e.data; if(!d||!d.isStreamlitMessage) return;
 if(d.type==='streamlit:componentReady') window.__ready=true;
 if(d.type==='streamlit:setFrameHeight') f.style.height=d.height+'px';
 if(d.type==='streamlit:setComponentValue') window.__values.push(d.value);}});
window.render=(a)=>f.contentWindow.postMessage({{type:'streamlit:render',args:a}},'*');
</script></body></html>"""
hpath = os.path.join(tempfile.mkdtemp(), "harness.html")
open(hpath, "w").write(HARNESS)
try:
    pw = sync_playwright().start()
    browser = pw.chromium.launch(args=["--allow-file-access-from-files"])
except Exception as e:     # noqa: BLE001
    print(f"fieldmap browser part: Chromium not available ({e}) - skipped")
    sys.exit(c.report())

errs = []
pg = browser.new_page(viewport={"width": 1300, "height": 720})
pg.on("pageerror", lambda e: errs.append(str(e)))
pg.on("console", lambda m_: errs.append(m_.text) if m_.type == "error" else None)
pg.goto("file://" + hpath)
pg.wait_for_function("window.__ready===true", timeout=15000)
c.check("component announces componentReady", True, "")
frame = pg.frames[1]


def show(d, nonce, title="Field layout"):
    pg.evaluate("a=>window.render(a)", {"drawing": d, "nonce": nonce, "height": 680, "title": title})
    pg.wait_for_timeout(350)


def stroke_count(color):
    return frame.evaluate(f"document.querySelectorAll('path[stroke=\"{color}\"]').length")


def page_xy(iid):
    """Screen position (page px) of a drawn item."""
    return frame.evaluate("""(id)=>{const F=window.__fieldmap, it=F.S.d.items.find(i=>i.id===id);
        const r=document.getElementById('svg').getBoundingClientRect(); return [r.left+F.sx(it.x), r.top+F.sy(it.y)];}""", iid)


def values():
    return pg.evaluate("window.__values")


for k, (n, (m, sol, d)) in enumerate(DRAW.items()):
    tag = n.split(":")[0]
    show(d, f"n{k}", tag)
    c.check(f"{tag}: every structure drawn", frame.evaluate("document.querySelectorAll('g.item').length") == len(d["items"]), "")
    n_lines = frame.evaluate("window.__fieldmap.geometry().length")
    c.eq(f"{tag}: every service line drawn", n_lines, len(d["lines"]))
    c.check(f"{tag}: production drawn in green", stroke_count(SERVICES["production"][1]) >= 1, "")
    inside = frame.evaluate("""(()=>{const r=document.getElementById('svg').getBoundingClientRect(); let bad=0;
        document.querySelectorAll('g.item').forEach(g=>{const b=g.getBoundingClientRect();
        if(b.left<r.left-2||b.right>r.right+2||b.top<r.top-2||b.bottom>r.bottom+2) bad++;}); return bad;})()""")
    c.eq(f"{tag}: fit keeps every structure in view", inside, 0)
    pg.screenshot(path=os.path.join(OUT, f"fieldmap_{k}.png"))
txt = frame.evaluate("Array.from(document.querySelectorAll('text')).map(t=>t.textContent).join('|')")
c.check("legend, north arrow and scale bar drawn", "LEGEND" in txt and "|N|" in txt and " km" in txt, txt[:200])

# interaction on the gas-lift example
gl = next(n for n in DRAW if "Gas lift" in n)
m, sol, d = DRAW[gl]
show(d, "gl", "Gas lift demo")
tid = next(i["id"] for i in d["items"] if i["kind"] == "template")
x0, y0 = page_xy(tid)
nv = len(values())
pg.mouse.move(x0, y0)
pg.mouse.down()
pg.mouse.move(x0 + 60, y0 - 40, steps=8)
pg.mouse.up()
pg.wait_for_timeout(100)
ev = values()[nv:]
mv = [e for e in ev if e["event"] == "move"]
c.check("dragging a template sends one move event", len(mv) == 1 and mv[0]["id"] == tid, str(ev)[:200])
if mv:
    t0 = next(i for i in d["items"] if i["id"] == tid)
    k_ = frame.evaluate("window.__fieldmap.S.view.k")
    c.close("move event in km east", mv[0]["x"] - t0["x"], 60 / k_, 0.05)
    c.close("move event in km north", mv[0]["y"] - t0["y"], 40 / k_, 0.05)
    c.check("events carry session and rev", mv[0].get("session") and mv[0].get("rev") >= 1, "")
nv = len(values())
pg.keyboard.press("r")
pg.wait_for_timeout(100)
ev = values()[nv:]
c.check("R rotates the selected template by 15°", any(e["event"] == "rotate" and e["id"] == tid and abs(e["rot"] - 15) < 1e-9
                                                     for e in ev), str(ev))
pg.keyboard.press("Shift+R")
pg.wait_for_timeout(100)
ev = values()[nv:]
c.check("Shift+R rotates back", any(e["event"] == "rotate" and abs(e["rot"]) < 1e-9 for e in ev[1:]), str(ev))
# bend the injector route by dragging its middle
G = frame.evaluate("""(()=>{const F=window.__fieldmap, g=F.geometry().find(x=>x.ln.service==='gaslift');
     const r=document.getElementById('svg').getBoundingClientRect();
     const mx=0.25*g.a0[0]+0.5*g.c[0]+0.25*g.a1[0], my=0.25*g.a0[1]+0.5*g.c[1]+0.25*g.a1[1];
     return [r.left+mx, r.top+my, g.key, g.nx, g.ny];})()""")
hitel = frame.evaluate(f"(()=>{{const e=document.elementFromPoint({G[0]},{G[1]}); return e.tagName+'.'+(e.getAttribute('class')||'')}})()")
nv = len(values())
pg.mouse.move(G[0], G[1])
pg.mouse.down()
pg.mouse.move(G[0] + 50 * G[3], G[1] + 50 * G[4], steps=6)
pg.mouse.up()
pg.wait_for_timeout(100)
ev = values()[nv:]
bd = [e for e in ev if e["event"] == "bend"]
c.check("dragging a line sends a bend for its corridor", len(bd) == 1 and bd[0]["pair"] == G[2], str(ev)[:200] + hitel)
# legend toggles a service
red = stroke_count(SERVICES["gaslift"][1])
c.check("gas-lift line drawn in red", red >= 1, "")
row = frame.evaluate("""(()=>{const t=Array.from(document.querySelectorAll('text')).find(t=>t.textContent==='Gas lift');
     const b=t.getBoundingClientRect(); return [b.left+b.width/2, b.top+b.height/2];})()""")
pg.mouse.click(row[0], row[1])
pg.wait_for_timeout(100)
c.eq("legend click hides the gas-lift line", stroke_count(SERVICES["gaslift"][1]), 0)   # the legend sample is a <line>
pg.mouse.click(row[0], row[1])
c.eq("second click shows it again", stroke_count(SERVICES["gaslift"][1]), red)
# toolbar toggles
frame.click("#bDist")
c.check("Distances button hides the length labels", "km" not in "|".join(
    t for t in frame.evaluate("Array.from(document.querySelectorAll('text')).map(t=>t.textContent)") if t.startswith("~")), "")
frame.click("#bDist")
frame.click("#bSlots")
txt = frame.evaluate("Array.from(document.querySelectorAll('text')).map(t=>t.textContent).join('|')")
c.check("Slot names button labels the wells", "P-1" in txt and "P-2" in txt, txt[:300])
# re-render with the same nonce keeps the view; a new nonce refits
frame.click("#bZin")
k1 = frame.evaluate("window.__fieldmap.S.view.k")
show(d, "gl")
c.close("same nonce keeps the zoom", frame.evaluate("window.__fieldmap.S.view.k"), k1, 1e-9)
show(d, "gl2")
c.check("new nonce refits", abs(frame.evaluate("window.__fieldmap.S.view.k") - k1) > 1e-6, "")
# reset and SVG export
nv = len(values())
frame.click("#bReset")
pg.wait_for_timeout(100)
c.check("Reset layout sends reset", any(e["event"] == "reset" for e in values()[nv:]), "")
nv = len(values())
frame.click("#bSvg")
pg.wait_for_timeout(150)
ex = [e for e in values()[nv:] if e["event"] == "export_svg"]
c.check("Export SVG sends the drawing", len(ex) == 1 and ex[0]["svg"].startswith("<svg") and "LEGEND" in ex[0]["svg"]
        and 'class="hit"' not in ex[0]["svg"], "")
pg.screenshot(path=os.path.join(OUT, "fieldmap_interaction.png"))
# phone width: still renders without errors
pg.set_viewport_size({"width": 420, "height": 720})
pg.evaluate("document.getElementById('f').style.width='420px'")
show(d, "phone")
pg.screenshot(path=os.path.join(OUT, "fieldmap_phone.png"))
c.check("narrow screen renders", frame.evaluate("document.querySelectorAll('g.item').length") == len(d["items"]), "")
c.check("no JavaScript errors", not errs, str(errs[:3]))
browser.close()
pw.stop()
sys.exit(c.report())
