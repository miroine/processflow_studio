"""v7.10: scene description for the 3D plant view.   Run:  python tests/test_scene3d.py"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from check import Checker                                                   # noqa: E402
from procsim import scene3d as SC                                           # noqa: E402
from procsim.examples import EXAMPLES                                       # noqa: E402
from procsim.flowsheet import normalize, solve                              # noqa: E402
from procsim.unitops import CATALOGUE                                       # noqa: E402

c = Checker("scene3d")
ROOT = os.path.join(os.path.dirname(__file__), "..")
ports = json.load(open(os.path.join(ROOT, "pfd_3d", "frontend", "ports2d.json")))

c.check("every unit type has a description", all(t in SC.DESCR for t in CATALOGUE), str([t for t in CATALOGUE if t not in SC.DESCR]))
c.check("every description has the three parts", all(all(isinstance(x, str) and x for x in v) for v in SC.DESCR.values()), "")
c.check("2D port table covers every type with ports", all(t in ports for t in CATALOGUE if CATALOGUE[t]["ports"]["in"] or CATALOGUE[t]["ports"]["out"]), "")
bad = [(t, p) for t, v in CATALOGUE.items() if t in ports for d in ("in", "out") for p in v["ports"][d] if p not in ports[t]["ports"]]
c.check("every catalogue port has a position in the 2D table", not bad, str(bad[:5]))

nps, di, vel = SC.nominal_size(1000.0, "gas")
c.check("indicative size: 1000 m³/h of gas -> about 15 m/s", 6 <= nps <= 12 and 8 < vel < 22, f"{nps} {di:.3f} {vel:.1f}")
c.check("a tiny flow gets the smallest sizes, a huge one the largest", SC.nominal_size(0.01, "liquid")[0] <= 2 and SC.nominal_size(1e6, "gas")[0] == SC.STD_NPS_IN[-1], "")
c.check("service from the outlet port", SC.service_of("vapour", 0.5, "") == "gas" and SC.service_of("water", None, "") == "water" and SC.service_of("out", 0.01, "Aqueous") == "water", "")

names = list(EXAMPLES)
nsolved = 0
for n in names:
    m = EXAMPLES[n]()
    normalize(m)
    try:
        sol = solve(m)
    except Exception:           # noqa: BLE001
        continue
    sc = SC.build_scene(m, sol)
    js = json.dumps(sc)
    nu = len([u for u in m["units"].values() if u["type"] not in ("recycle", "adjust", "platform")])
    ok = len(sc["units"]) == nu and all(s["src"][0] in {u["id"] for u in sc["units"]} for s in sc["streams"])
    c.check(f"{n[:46]}: scene lists every drawable unit and valid pipes ({len(js) // 1024} kB)", ok, f"{len(sc['units'])} vs {nu}")
    if sol.converged:
        nsolved += 1
        flowing = [s for s in sc["streams"] if s.get("solved") and (s.get("mass") or 0) > 0]
        c.check(f"{n[:46]}: flowing pipes have size and velocity", all(s["nps"] > 0 and s["d"] > 0 and s["vel"] >= 0 for s in flowing) and flowing, "")
c.check("most examples solved for the scene test", nsolved >= 20, nsolved)

# not solved: results are absent but the scene is complete
m = EXAMPLES[names[0]]()
normalize(m)
sc = SC.build_scene(m, None)
c.check("without a solution units have no results and pipes no flow", all(not u["results"] for u in sc["units"]) and all(not s["solved"] for s in sc["streams"]), "")

# stage views follow the phasing
pn = next(n for n in names if n.startswith("Phasing (v7.6)"))
mp = EXAMPLES[pn]()
normalize(mp)
mp["stage"] = 0
s0 = SC.build_scene(mp, solve(mp))
mp["stage"] = 1
s1 = SC.build_scene(mp, solve(mp))
c.check("stage 0 has fewer units than stage 1", len(s0["units"]) < len(s1["units"]), f"{len(s0['units'])} {len(s1['units'])}")
c.check("stage name and phase list are passed", s1["meta"]["stageName"].startswith("After") and len(s1["meta"]["phases"]) == 1, str(s1["meta"]))
newu = [u for u in s1["units"] if u["phase"]]
c.check("added equipment carries its phase and colour", newu and all(u["change"] == "add" and u["color"] for u in newu), str(newu[:1]))

# platform frames become deck outlines, not equipment
c.check("platform is a deck outline", len(s1["platforms"]) == 1 and all(u["type"] != "platform" for u in s1["units"]), "")

# visual scale from results
c.check("vessel diameter drives the visual scale", SC.visual_scale("separator", {"Required diameter [mm]": 3600.0}) > SC.visual_scale("separator", {"Required diameter [mm]": 1200.0}), "")
c.check("unknown result leaves the scale at 1", SC.visual_scale("separator", {}) == 1.0 and SC.visual_scale("valve", {"x": 1}) == 1.0, "")


# hand-edited layout (v7.10.2): sanitising, stable origin, injection into the scene, no re-solve
import copy                                                                 # noqa: E402
bad = {"units": {"a": {"x": 1.234567, "z": -2, "rot": 5}, "b": {"x": "q", "z": 1}, "c": {"x": 1e9, "z": 0}},
       "routes": {"s": [[0, 0, 0], [1, 2, 3]], "t": [[0, 0]], "u": "x", "v": [[0, 0, 0]] * 41}, "origin": [10, 20], "ver": "3"}
L = SC.sanitize_layout(bad)
c.check("layout: only valid units kept, rotation wrapped, position rounded", list(L["units"]) == ["a"] and L["units"]["a"] == {"x": 1.235, "z": -2.0, "rot": 1}, str(L["units"]))
c.check("layout: only valid routes kept", list(L["routes"]) == ["s"], str(L["routes"]))
c.check("layout: origin and version survive, garbage becomes empty", L["origin"] == [10.0, 20.0] and L["ver"] == 3 and SC.sanitize_layout("x") == {"units": {}, "routes": {}, "origin": None, "ver": 0, "views": []}, "")
mm = EXAMPLES[list(EXAMPLES)[0]]()
normalize(mm)
sc_a = SC.build_scene(mm)
c.check("scene carries an empty layout and its version", sc_a["layout"] == {"units": {}, "routes": {}, "views": []} and sc_a["meta"]["layoutVer"] == 0, str(sc_a["layout"]))
uid0 = sc_a["units"][0]["id"]
mm["layout3d"] = {"units": {uid0: {"x": 3, "z": 4, "rot": 2}}, "routes": {}, "origin": SC.scene_origin(mm), "ver": 2}
sc_b = SC.build_scene(mm)
c.check("scene passes the stored layout through and the version", sc_b["layout"]["units"][uid0]["rot"] == 2 and sc_b["meta"]["layoutVer"] == 2, "")
c.check("the stored origin keeps unedited units where they were", [(u["x"], u["z"]) for u in sc_a["units"]] == [(u["x"], u["z"]) for u in sc_b["units"]], "")
m3 = copy.deepcopy(mm)
for u in m3["units"].values():
    u["x"] += 500.0
sc_c = SC.build_scene(m3)
c.check("moving everything in 2D does not shift an edited 3D layout", all(abs(a["x"] - b["x"] - 500 * SC.M_PER_PX) < 0.01 for a, b in zip(sc_c["units"], sc_b["units"])), "")
import json as _j                                                           # noqa: E402,F811
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "stubs"))
import ui.state as _S                                                       # noqa: E402
h1 = _S.model_hash(mm)
mm2 = copy.deepcopy(mm)
mm2["layout3d"]["units"][uid0]["x"] = 99
c.check("editing the 3D layout never invalidates the solution", _S.model_hash(mm2) == h1, "")
c.check("the scene stays JSON-serialisable with a layout", bool(_j.dumps(sc_b)), "")

# 3D moves are written back to the 2D drawing; a later 2D move overrides the stored 3D spot
mq = EXAMPLES[list(EXAMPLES)[0]]()
normalize(mq)
org = SC.scene_origin(mq)
ids = list(mq["units"])
u1 = ids[1]
xy2 = (mq["units"][ids[2]]["x"], mq["units"][ids[2]]["y"])
lay = SC.sanitize_layout({"units": {u1: {"x": 10.0, "z": -5.0, "rot": 0}, ids[2]: {"x": 1.0, "z": 1.0}}, "origin": org})
SC.apply_layout_to_2d(mq, lay, [u1], org)
c.check("a touched unit moves in the 2D drawing by the 3D offset", abs(mq["units"][u1]["x"] - (org[0] + 10.0 / SC.M_PER_PX)) < 0.1 and abs(mq["units"][u1]["y"] - (org[1] - 5.0 / SC.M_PER_PX)) < 0.1, str(mq["units"][u1]))
c.check("an untouched unit keeps its 2D position", (mq["units"][ids[2]]["x"], mq["units"][ids[2]]["y"]) == xy2, "")
c.check("every stored spot is stamped with its 2D position", all("p2" in e for e in lay["units"].values()), "")
mq["layout3d"] = dict(lay, ver=0)
sq = SC.build_scene(mq)
c.check("the stamped spot is used while the 2D position is unchanged", u1 in sq["layout"]["units"], "")
mq["units"][u1]["x"] += 40
sq = SC.build_scene(mq)
c.check("a later 2D move wins over the stored 3D spot", u1 not in sq["layout"]["units"] and ids[2] in sq["layout"]["units"], "")

# JS syntax and the vendored library
import subprocess                                                           # noqa: E402
r = subprocess.run(["node", "--check", os.path.join(ROOT, "pfd_3d", "frontend", "plant3d.js")], capture_output=True, text=True) if os.system("which node >/dev/null 2>&1") == 0 else None
c.check("plant3d.js parses", r is None or r.returncode == 0, r.stderr[:200] if r else "")
c.check("three.js and its licence are shipped", os.path.getsize(os.path.join(ROOT, "pfd_3d", "frontend", "three.min.js")) > 400000 and os.path.isfile(os.path.join(ROOT, "pfd_3d", "frontend", "LICENSE_three.txt")), "")
sys.exit(c.report())
