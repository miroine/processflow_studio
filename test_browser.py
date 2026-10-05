"""Real-browser test of the PFD canvas (headless Chromium via Playwright).

Loads the component page in an iframe inside a small harness page that plays the Streamlit host
(componentReady / render / setComponentValue), renders every example, and drives the canvas with real
mouse and keyboard input.  Skipped automatically when Playwright or Chromium is not available.

Run:  python tests/test_browser.py            (screenshots are written to tests/_screens/)
"""
import json
import os
import sys
import tempfile
import time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "tests", "stubs"))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
from check import Checker   # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("browser: playwright not installed - skipped")
    sys.exit(0)

import streamlit as st                     # stub                                        # noqa: E402
from ui import state as S                  # noqa: E402
from procsim.examples import EXAMPLES      # noqa: E402
from procsim.flowsheet import solve        # noqa: E402

c = Checker("browser")
FRONT = os.path.abspath(os.path.join(ROOT, "pfd_canvas", "frontend", "index.html"))
OUT = os.path.join(ROOT, "tests", "_screens")
os.makedirs(OUT, exist_ok=True)

payload = {}
for n, f in EXAMPLES.items():
    m = f()
    sol = solve(m)
    st.session_state.model, st.session_state.sol = m, sol
    st.session_state.sol_hash, st.session_state.solve_error = S.model_hash(m), None
    payload[n] = {"model": S.canvas_structure(m), "results": S.results_payload(), "status": S.status_line()}
CAT = S.catalogue_payload()
from ui import units as UN   # noqa: E402
UN.set_system(UN.FIELD)
first = next(iter(EXAMPLES))
m = EXAMPLES[first]()
st.session_state.model, st.session_state.sol = m, solve(m)
st.session_state.sol_hash = S.model_hash(m)
FIELD_PAYLOAD = {"model": S.canvas_structure(m), "results": S.results_payload(), "status": S.status_line()}
UN.set_system(UN.SI)

HARNESS = f"""<!doctype html><html><head><meta charset="utf-8"><style>body{{margin:0}}iframe{{border:0;width:1500px}}
</style></head><body><iframe id="f" src="file://{FRONT}"></iframe><script>
window.__values=[]; window.__ready=false; const f=document.getElementById('f');
window.addEventListener('message',(e)=>{{const d=e.data; if(!d||!d.isStreamlitMessage) return;
 if(d.type==='streamlit:componentReady') window.__ready=true;
 if(d.type==='streamlit:setFrameHeight') f.style.height=d.height+'px';
 if(d.type==='streamlit:setComponentValue') window.__values.push(d.value);}});
window.render=(a)=>f.contentWindow.postMessage({{type:'streamlit:render',args:a}},'*');
</script></body></html>"""
tmp = tempfile.mkdtemp()
hpath = os.path.join(tmp, "harness.html")
open(hpath, "w").write(HARNESS)

try:
    pw = sync_playwright().start()
    browser = pw.chromium.launch(args=["--allow-file-access-from-files"])
except Exception as e:     # noqa: BLE001
    print(f"browser: Chromium not available ({e}) - skipped")
    sys.exit(0)

errs = []
pg = browser.new_page(viewport={"width": 1500, "height": 800})
pg.on("pageerror", lambda e: errs.append(str(e)))
pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
pg.goto("file://" + hpath)
pg.wait_for_function("window.__ready===true", timeout=15000)
c.check("component announces componentReady", True, "")
fr = pg.frame_locator("#f")
frame = pg.frames[1]

for i, (n, d) in enumerate(payload.items()):
    pg.evaluate("a=>window.render(a)", {"model": d["model"], "catalogue": CAT, "results": d["results"], "nonce": i + 1,
                                        "height": 720, "status": d["status"], "fit": True, "selected": []})
    time.sleep(0.5)
    nu = frame.evaluate("document.querySelectorAll('g.unit').length")
    ns = frame.evaluate("document.querySelectorAll('path.stream').length")
    c.eq(f"{n}: every unit drawn", nu, len(d["model"]["units"]))
    c.eq(f"{n}: every stream drawn", ns, len(d["model"]["streams"]))
    # labels must stay inside the drawing after 'fit'
    over = frame.evaluate("""(()=>{const r=document.getElementById('svg').getBoundingClientRect(); let bad=0;
        document.querySelectorAll('g.unit').forEach(g=>{const b=g.getBoundingClientRect();
          if(b.left<r.left-1||b.right>r.right+1||b.top<r.top-1||b.bottom>r.bottom+1) bad++;}); return bad;})()""")
    c.eq(f"{n}: all units inside the viewport after zoom-to-fit", over, 0)
    pg.screenshot(path=os.path.join(OUT, f"example_{i}.png"))


pg.evaluate("a=>window.render(a)", {"model": FIELD_PAYLOAD["model"], "catalogue": CAT, "results": FIELD_PAYLOAD["results"],
                                    "nonce": 50, "height": 720, "status": FIELD_PAYLOAD["status"], "fit": True})
time.sleep(0.5)
txt = frame.evaluate("Array.from(document.querySelectorAll('text')).map(t=>t.textContent).join('|')")
c.check("field units rendered on the canvas (°F, psia, hp)", "°F" in txt and "psia" in txt and "hp" in txt, txt[:200])
pg.screenshot(path=os.path.join(OUT, "field_units.png"))


def vals():
    return pg.evaluate("window.__values")


pg.evaluate("a=>window.render(a)", {"model": {"units": {}, "streams": {}}, "catalogue": CAT,
                                    "results": {"streams": {}, "units": {}, "links": []}, "nonce": 99, "height": 700,
                                    "status": "blank"})
time.sleep(0.3)
ib = pg.locator("#f").bounding_box()


def centre(loc):
    b = loc.bounding_box()
    return ib["x"] + b["x"] + b["width"] / 2, ib["y"] + b["y"] + b["height"] / 2


def drop(ptype, x, y):
    sx, sy = centre(fr.locator(f'.pal[data-type="{ptype}"]'))
    pg.mouse.move(sx, sy)
    pg.mouse.down()
    pg.mouse.move(sx + 150, sy + 30, steps=6)
    pg.mouse.move(x, y, steps=8)
    pg.mouse.up()
    time.sleep(0.15)
    return vals()[-1]


def drag_port(uid, port, x, y):
    px, py = centre(fr.locator(f'g[data-id="{uid}"] circle[data-port="{port}"]'))
    pg.mouse.move(px - 15, py)
    pg.mouse.move(px, py)
    pg.mouse.down()
    pg.mouse.move((px + x) / 2, (py + y) / 2, steps=6)
    pg.mouse.move(x, y, steps=6)
    pg.mouse.up()
    time.sleep(0.15)
    return vals()[-1]


v = drop("compressor", 700, 350)
c.check("palette drag-and-drop adds a compressor", v["event"] == "add" and
        v["model"]["units"][v["added"]]["type"] == "compressor", v["event"])
k = v["added"]
v = drag_port(k, "out", 900, 370)
c.check("outlet dragged to empty space creates a product stream",
        any(u["type"] == "product" for u in v["model"]["units"].values()) and len(v["model"]["streams"]) == 1, "")
v = drag_port(k, "in", 520, 350)
c.check("inlet dragged to empty space creates a feed stream",
        any(u["type"] == "feed" for u in v["model"]["units"].values()) and len(v["model"]["streams"]) == 2, "")
e = drop("cooler", 900, 560)["added"]
sc = drop("scrubber", 1150, 520)["added"]
tx, ty = centre(fr.locator(f'g[data-id="{sc}"] circle[data-port="feed"]'))
v = drag_port(e, "out", tx, ty)
c.check("port-to-port connection (cooler outlet → scrubber feed)",
        any(s["src"][0] == e and s["dst"] == [sc, "feed"] for s in v["model"]["streams"].values()), v["event"])
n_before = len(v["model"]["streams"])
tx, ty = centre(fr.locator(f'g[data-id="{k}"] circle[data-port="in"]'))
v = drag_port(e, "in", tx, ty)      # inlet onto an inlet: refused
c.eq("inlet-to-inlet drop is refused", len(vals()[-1]["model"]["streams"]), n_before)
cx, cy = centre(fr.locator(f'g[data-id="{e}"] rect').first)
pg.mouse.click(cx, cy)
pg.mouse.click(cx, cy)
time.sleep(0.15)
c.check("double-click opens the property view", vals()[-1]["event"] == "open" and vals()[-1]["selected"] == [e], "")
pg.mouse.move(cx, cy)
pg.mouse.down()
pg.mouse.move(cx + 60, cy + 40, steps=8)
pg.mouse.up()
time.sleep(0.15)
v = vals()[-1]
c.check("dragging a unit moves it on the 10 px grid", v["event"] == "move" and v["model"]["units"][e]["x"] % 10 == 0, "")
pg.keyboard.press("Delete")
time.sleep(0.15)
c.check("Delete key removes the selected unit (iframe has keyboard focus)", e not in vals()[-1]["model"]["units"], "")
pg.keyboard.press("Control+z")
time.sleep(0.15)
c.check("Ctrl+Z restores it", vals()[-1]["event"] == "undo" and e in vals()[-1]["model"]["units"], "")
pg.mouse.click(cx + 60, cy + 40)
n0 = len(vals()[-1]["model"]["units"])
pg.keyboard.press("Control+d")
time.sleep(0.15)
c.check("Ctrl+D duplicates the selection", vals()[-1]["event"] == "paste" and len(vals()[-1]["model"]["units"]) == n0 + 1, "")
pg.keyboard.press("f")
time.sleep(0.15)
c.check("F flips the selection", vals()[-1]["event"] == "move", vals()[-1]["event"])
pg.mouse.move(800, 400)
pg.mouse.wheel(0, -300)
time.sleep(0.15)
tr = frame.evaluate("document.querySelector('svg#svg > g').getAttribute('transform')")
c.check("mouse wheel zooms", "scale(1)" not in tr, tr)
fr.locator("#bSvg").click()
time.sleep(0.3)
v = vals()[-1]
c.check("Export SVG returns a standalone SVG document", v["event"] == "export_svg" and v["svg"].startswith("<svg")
        and 'xmlns="http://www.w3.org/2000/svg"' in v["svg"][:400], v["svg"][:120])
# the exported SVG must render on its own (as it will inside the report)
svgp = os.path.join(tmp, "export.svg")
open(svgp, "w").write(v["svg"])
p2 = browser.new_page(viewport={"width": 1500, "height": 800})
p2.goto("file://" + svgp)
c.check("exported SVG renders standalone with the drawn units", p2.evaluate("document.querySelectorAll('g.unit').length") >= 4, "")
p2.screenshot(path=os.path.join(OUT, "export_svg.png"))
pg.screenshot(path=os.path.join(OUT, "interaction.png"))
c.check("no JavaScript errors in the page", not errs, errs[:3])
browser.close()
pw.stop()
sys.exit(c.report())
