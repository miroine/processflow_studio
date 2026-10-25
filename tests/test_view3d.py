"""Real-browser test of the 3D plant view (headless Chromium, software WebGL).  Skipped without Playwright / Chromium.

Run:  python tests/test_view3d.py          (screenshots go to tests/_screens/3d_*.png)
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
    print("view3d: playwright not installed - skipped")
    sys.exit(0)

from procsim import scene3d as SC                       # noqa: E402
from procsim.examples import EXAMPLES                   # noqa: E402
from procsim.flowsheet import normalize, solve          # noqa: E402
from procsim.unitops import CATALOGUE                   # noqa: E402

c = Checker("view3d")
FRONT = os.path.abspath(os.path.join(ROOT, "pfd_3d", "frontend", "index.html"))
OUT = os.path.join(ROOT, "tests", "_screens")
os.makedirs(OUT, exist_ok=True)
PORTS = json.load(open(os.path.join(ROOT, "pfd_3d", "frontend", "ports2d.json")))
HARNESS = f"""<!doctype html><html><head><meta charset="utf-8"><style>body{{margin:0}}iframe{{border:0;width:1300px;height:760px}}</style></head><body>
<iframe id="f" src="file://{FRONT}?lite=1"></iframe><script>
window.__ready=false;window.__vals=[];const f=document.getElementById('f');
window.addEventListener('message',(e)=>{{const d=e.data;if(!d||!d.isStreamlitMessage)return;if(d.type==='streamlit:componentReady')window.__ready=true;if(d.type==='streamlit:setComponentValue')window.__vals.push(d.value);
 if(d.type==='streamlit:setFrameHeight')f.style.height=d.height+'px';}});
window.render=(a)=>f.contentWindow.postMessage({{type:'streamlit:render',args:a}},'*');</script></body></html>"""
tmp = tempfile.mkdtemp()
open(os.path.join(tmp, "h.html"), "w").write(HARNESS)

try:
    pw = sync_playwright().start()
    browser = pw.chromium.launch(args=["--allow-file-access-from-files", "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
except Exception as e:     # noqa: BLE001
    print(f"view3d: Chromium not available ({e}) - skipped")
    sys.exit(0)

errs = []
pg = browser.new_page(viewport={"width": 1300, "height": 780})
pg.on("pageerror", lambda e: errs.append(str(e)))
pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
pg.goto("file://" + os.path.join(tmp, "h.html"))
pg.wait_for_function("window.__ready===true", timeout=15000)
fr = pg.frames[1]


def render(sc, wait=1.5):
    pg.evaluate("a=>window.render(a)", {"scene": sc, "ports": PORTS, "height": 760})
    time.sleep(wait)


# ---- 1. one of every unit type (no pipes): every archetype builds without a script error
units = []
for i, t in enumerate(CATALOGUE):
    units.append({"id": f"u{i}", "type": t, "name": f"{t}", "x": (i % 8) * 14.0, "z": (i // 8) * 14.0, "flip": False, "orient": "", "scale": 1.0,
                  "phase": "", "change": "", "color": "", "status": "ok", "error": "", "title": SC.DESCR.get(t, (t, "", ""))[0],
                  "what": SC.DESCR.get(t, (t, "", ""))[1], "model": SC.DESCR.get(t, (t, "", ""))[2], "results": [["Duty [kW]", "1000"]]})
render({"units": units, "streams": [], "platforms": [{"id": "p", "name": "Deck", "x": 20, "z": 20, "w": 30, "d": 18}], "meta": {"stage": 0, "stageName": "Test", "solved": True, "phases": []}}, 2.5)
cnt = fr.evaluate("window.__p3d.counts()")
c.check("one of every unit type is built", cnt["units"] == len(CATALOGUE), str(cnt))
c.check("no script error while building every type", not [e for e in errs if "deprecated" not in e], str(errs[:3]))
for t in ("separator", "separator3", "separator3"):
    pass
# orientation variants and the flip
units2 = [dict(units[0], id="a", orient="h"), dict(units[0], id="b", orient="v", x=14.0), dict(units[2], id="c", orient="v", x=28.0, flip=True)]
units2[0]["type"] = units2[1]["type"] = "separator"
units2[2]["type"] = "separator3"
render({"units": units2, "streams": [], "platforms": [], "meta": {"stage": 0, "stageName": "Test", "solved": True, "phases": []}}, 1.0)
c.check("orientation variants build", fr.evaluate("window.__p3d.counts().units") == 3 and not [e for e in errs if "deprecated" not in e], str(errs[:3]))

# ---- 2. examples
pick = [0, 1, 3, 5, 12, 14, 21, 26, 27, 28]
names = list(EXAMPLES)

def shot(path):
    import base64
    d = fr.evaluate("(()=>{window.__p3d.renderOnce&&window.__p3d.renderOnce();return window.__p3d.renderer.domElement.toDataURL('image/png')})()")
    open(path, "wb").write(base64.b64decode(d.split(",", 1)[1]))

for i in pick:
    n = names[i]
    m = EXAMPLES[n]()
    normalize(m)
    sol = solve(m)
    sc = SC.build_scene(m, sol)
    render(sc, 1.2)
    cnt = fr.evaluate("window.__p3d.counts()")
    nflow = len([s for s in sc["streams"] if s.get("solved") and (s.get("mass") or 0) > 0])
    c.check(f"{n[:44]}: units drawn", cnt["units"] == len(sc["units"]), str(cnt))
    c.check(f"{n[:44]}: a pipe per stream", cnt["pipes"] == len(sc["streams"]), f"{cnt['pipes']} vs {len(sc['streams'])}")
    c.check(f"{n[:44]}: flow particles when something flows", (cnt["particles"] > 0) == (nflow > 0), str(cnt))
    if i in (0, 26, 27):
        fr.evaluate("window.__p3d.view('top')")
        time.sleep(1.0)
        shot(os.path.join(OUT, f"3d_{i}_plan.png"))
        fr.evaluate("window.__p3d.view('iso')")
        time.sleep(1.0)
        shot(os.path.join(OUT, f"3d_{i}.png"))
    if i == 0:
        uid = next(u["id"] for u in sc["units"] if u["type"] == "compressor")
        fr.evaluate(f"window.__p3d.select('unit','{uid}',true)")
        time.sleep(1.2)
        txt = fr.evaluate("window.__p3d.info()")
        c.check("selecting a unit shows its description and results", "what it is" in txt.lower() and "Power" in txt and "how it is modelled" in txt.lower(), txt[:120])
        shot(os.path.join(OUT, "3d_inspect.png"))
        sid = next(s["id"] for s in sc["streams"] if s.get("solved"))
        fr.evaluate(f"window.__p3d.select('stream','{sid}',true)")
        time.sleep(0.8)
        txt = fr.evaluate("window.__p3d.info()")
        c.check("selecting a pipe shows its properties and the sizing note", "Mass flow" in txt and "indicative" in txt, txt[:120])
        fr.evaluate("window.__p3d.select(null)")
        c.check("closing the info panel", fr.evaluate("document.getElementById('info').style.display") == "none", "")
        # toggles
        for b in ("bFlow", "bLabels", "bXray", "bNight", "bRot"):
            fr.evaluate(f"document.getElementById('{b}').click()")
        c.check("toggle buttons switch state", fr.evaluate("document.getElementById('bXray').classList.contains('on') && !document.getElementById('bFlow').classList.contains('on')"), "")
        for b in ("bFlow", "bLabels", "bXray", "bNight", "bRot"):
            fr.evaluate(f"document.getElementById('{b}').click()")
        # real mouse: click on the canvas does not throw; wheel zoom changes the distance
        r0 = fr.evaluate("window.__p3d.cam.radius")
        time.sleep(1.0)
        fr.evaluate("document.querySelector('canvas').dispatchEvent(new WheelEvent('wheel',{deltaY:400,bubbles:true,cancelable:true}))")
        time.sleep(0.8)
        c.check("mouse wheel zooms out", fr.evaluate("window.__p3d.cam.radius") > r0, "")
        th0 = fr.evaluate("window.__p3d.cam.theta")
        pg.mouse.move(500, 400)
        pg.mouse.down()
        pg.mouse.move(600, 400, steps=4)
        pg.mouse.up()
        time.sleep(0.5)
        c.check("dragging rotates the camera", abs(fr.evaluate("window.__p3d.cam.theta") - th0) > 0.1, "")


# ---- 4. performance: static meshes are merged (draw calls), themes, flow stripes
m = EXAMPLES[names[0]]()
normalize(m)
sol = solve(m)
sc0 = SC.build_scene(m, sol)
render(sc0, 1.2)
calls = fr.evaluate("(()=>{const r=window.__p3d.renderer;r.render(window.__p3d.scene,window.__p3d.camera);return r.info.render.calls})()")
c.check("draw calls stay low (static meshes merged)", calls < 320, str(calls))
c.check("flowing pipes carry a stripe texture; no beads by default", fr.evaluate("window.__p3d.state.pipes.filter(p=>p.flowing).every(p=>!!p.tex) && !window.__p3d.state.beads"), "")
off0 = fr.evaluate("window.__p3d.state.pipes.find(p=>p.tex).tex.offset.x"); time.sleep(0.6)
c.check("stripes move with the flow", fr.evaluate("window.__p3d.state.pipes.find(p=>p.tex).tex.offset.x") != off0, "")
fr.evaluate("document.getElementById('bBeads').click()")
c.check("beads mode makes pipes see-through", fr.evaluate("window.__p3d.state.pipes.find(p=>p.flowing).mat.transparent"), "")
fr.evaluate("document.getElementById('bBeads').click()")
fr.evaluate("window.__p3d.setTheme('realistic')"); time.sleep(0.8)
c.check("realistic theme rebuilds the scene", fr.evaluate("window.__p3d.theme()") == "realistic" and fr.evaluate("window.__p3d.counts().units") == len(sc0["units"]), "")
fr.evaluate("window.__p3d.setTheme('studio')"); time.sleep(0.8)
c.check("studio theme back, no error", fr.evaluate("window.__p3d.theme()") == "studio" and not [e for e in errs if "deprecated" not in e], str(errs[:3]))

# ---- 5. layout editing
E = "window.__p3d.edit"
uids = [u["id"] for u in sc0["units"]]
uid = uids[2]
fr.evaluate(f"{E}.setEdit(true)")
c.check("edit tools appear", fr.evaluate("document.getElementById('editTools').style.display") != "none", "")
x0, z0 = fr.evaluate(f"[window.__p3d.state.units['{uid}'].u.x, window.__p3d.state.units['{uid}'].u.z]")
n0 = pg.evaluate("window.__vals.length")
pt = fr.evaluate(f"window.__p3d.screenOf('{uid}')")
fr.evaluate(f"window.__p3d.view('top')"); time.sleep(1.2)
fr.evaluate("window.__p3d.cam.fly=null")
pt = fr.evaluate(f"window.__p3d.screenOf('{uid}', 0.5)")
pg.mouse.move(pt["x"], pt["y"])
pg.mouse.down()
pg.mouse.move(pt["x"] + 90, pt["y"] + 40, steps=6)
time.sleep(0.4)
pg.mouse.up()
time.sleep(0.8)
x1, z1 = fr.evaluate(f"[window.__p3d.state.units['{uid}'].u.x, window.__p3d.state.units['{uid}'].u.z]")
c.check("dragging equipment moves it on the ground", abs(x1 - x0) + abs(z1 - z0) > 1.5, f"{x0:.1f},{z0:.1f} -> {x1:.1f},{z1:.1f}")
c.check("moved position snaps to the 1 m grid", abs(x1 - round(x1)) < 1e-6 and abs(z1 - round(z1)) < 1e-6, f"{x1},{z1}")
vals = pg.evaluate("window.__vals")
c.check("the edit is sent to Python once, with every unit", len(vals) == n0 + 1 and len(vals[-1]["layout"]["units"]) == len(uids) and vals[-1]["layout"]["units"][uid]["x"] == x1, str(len(vals)))
c.check("the pipes were re-routed to the new position", fr.evaluate("window.__p3d.counts().pipes") == len(sc0["streams"]), "")
fr.evaluate(f"window.__p3d.select('unit','{uid}',false)")
fr.evaluate(f"{E}.rotate()"); time.sleep(0.5)
c.check("rotate turns the equipment by 90 degrees", fr.evaluate(f"window.__p3d.state.units['{uid}'].u.rot") == 1 and fr.evaluate(f"{E}.layout().units['{uid}'].rot") == 1, "")
fr.evaluate(f"{E}.undo()"); time.sleep(0.5)
c.check("undo restores the rotation", fr.evaluate(f"window.__p3d.state.units['{uid}'].u.rot") == 0, "")
fr.evaluate(f"{E}.redo()"); time.sleep(0.3)
c.check("redo applies it again", fr.evaluate(f"window.__p3d.state.units['{uid}'].u.rot") == 1, "")
# pipe route editing through the handles
sid = next((s["id"] for s in sc0["streams"] if s.get("solved") and s["src"][0] != uid and s["dst"][0] != uid), sc0["streams"][0]["id"])
fr.evaluate(f"window.__p3d.select('stream','{sid}',false)"); time.sleep(0.5)
hs = fr.evaluate(f"{E}.handles()")
c.check("a selected pipe shows route handles in edit mode", any(h["kind"] == "elev" for h in hs) or len(hs) > 0, str(hs))
n1 = pg.evaluate("window.__vals.length")
if hs:
    hi = next((i for i, h in enumerate(hs) if h["kind"] == "elev"), 0)
    p_before = fr.evaluate(f"window.__p3d.state.pipes.find(p=>p.s.id==='{sid}').path.map(v=>[v.x,v.y,v.z])")
    val = 9.5 if hs[hi]["kind"] == "elev" else p_before[hs[hi]["k"]][("x" if hs[hi]["axes"][0] == "x" else "z") == "x" and 0 or 2] + 2.0
    fr.evaluate(f"{E}.dragHandle({hi}, '{hs[hi]['kind']}', {val})"); time.sleep(0.6)
    p_after = fr.evaluate(f"window.__p3d.state.pipes.find(p=>p.s.id==='{sid}').path.map(v=>[v.x,v.y,v.z])")
    c.check("dragging a handle changes the pipe route", p_after != p_before, "")
    c.check("the route is kept in the layout and sent to Python", sid in fr.evaluate(f"{E}.layout().routes") and pg.evaluate("window.__vals.length") == n1 + 1, "")
    c.check("route ends still sit on the nozzles", abs(p_after[0][0] - p_before[0][0]) < 1e-3 and abs(p_after[-1][2] - p_before[-1][2]) < 1e-3, "")
    fr.evaluate(f"{E}.autoRoute()"); time.sleep(0.5)
    c.check("auto-route forgets the hand route", sid not in fr.evaluate(f"{E}.layout().routes"), "")
fr.evaluate(f"{E}.reset()"); time.sleep(0.8)
last = pg.evaluate("window.__vals")[-1]
c.check("reset layout clears the layout and tells Python", last.get("reset") and not last["layout"]["units"], str(last)[:80])
c.check("reset puts the equipment back", abs(fr.evaluate(f"window.__p3d.state.units['{uid}'].u.x") - x0) < 0.5, "")
# a scene echo from Python (same layout) must not rebuild / lose the edit
fr.evaluate(f"{E}.moveUnit('{uid}', {x0 + 4}, {z0 + 4})"); time.sleep(0.5)
lay = fr.evaluate(f"{E}.layout()")
sc_e = dict(sc0, layout=lay)
render(sc_e, 0.8)
c.check("the echoed layout keeps the edited position", abs(fr.evaluate(f"window.__p3d.state.units['{uid}'].u.x") - (x0 + 4)) < 1e-6, "")
sc_e2 = json.loads(json.dumps(sc0)); sc_e2["meta"]["layoutVer"] = 5
render(sc_e2, 0.8)
c.check("a new layout version from Python resets the edits", abs(fr.evaluate(f"window.__p3d.state.units['{uid}'].u.x") - x0) < 0.5, "")
fr.evaluate(f"{E}.setEdit(false)")
c.check("no script error after editing", not [e for e in errs if "deprecated" not in e], str(errs[:3]))

# ---- 3. unsolved scene and an update of the same scene keeps the camera
m = EXAMPLES[names[0]]()
normalize(m)
render(SC.build_scene(m, None), 1.0)
c.check("an unsolved scene still builds (no flow)", fr.evaluate("window.__p3d.counts().particles") == 0, "")
render({"units": [], "streams": [], "platforms": [], "meta": {"stage": -1, "stageName": "x", "solved": False, "phases": []}}, 0.5)
c.check("an empty scene shows a message", "Nothing to show" in fr.evaluate("document.getElementById('msg').textContent"), "")
c.check("no script error in the whole session", not [e for e in errs if "deprecated" not in e], str(errs[:3]))
browser.close()
pw.stop()
sys.exit(c.report())
