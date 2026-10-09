"""v7.6 phasing, platform, gas turbine and phase splitter.

Run:  python tests/test_phasing.py
"""
import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from check import Checker                                                   # noqa: E402
from procsim import phasing as PH                                           # noqa: E402
from procsim.examples import EXAMPLES, WET_GAS                              # noqa: E402
from procsim.examples_phased import phased_hp_compression                   # noqa: E402
from procsim.flowsheet import new_model, add_unit, connect, normalize, solve  # noqa: E402

c = Checker("phasing")


def byname(m, frag):
    return next(k for k, u in m["units"].items() if frag in u["name"])


def W(sol):
    return -sum(e.duty_kW for e in sol.energy if e.kind == "work" and e.duty_kW < 0)


def P_of(m, sol, name):
    sid = next(k for k, s in m["streams"].items() if s["name"] == name)
    return sol.streams[sid].P if sid in sol.streams else None


m = phased_hp_compression()
normalize(m)
ph = m["phases"][0]
k2, k1 = byname(m, "K-101"), byname(m, "K-100")
c.check("example registered", any("Phasing (v7.6)" in k for k in EXAMPLES), "")
c.check("one phase defined", len(PH.phase_list(m)) == 1 and PH.n_phases(m) == 1, "")
c.check("new compressor is absent today and present after phase 1",
        not PH.present(m["units"][k2], m, 0) and PH.present(m["units"][k2], m, 1), "")
c.check("untagged units are always present", PH.present(m["units"][k1], m, 0) and PH.present(m["units"][k1], m, 1), "")
old = next(k for k, s in m["streams"].items() if s["name"] == "4")
c.eq("removed stream is present today only", sorted(PH.presence(m["streams"][old], m)), [0])

s0, s1 = PH.stage_model(dict(m, stage=0)), PH.stage_model(dict(m, stage=1))
c.check("stage 0 drops the future compressor and its streams", k2 not in s0["units"] and len(s0["streams"]) == len(m["streams"]) - 2, len(s0["streams"]))
c.check("stage 1 drops the removed bypass stream", k2 in s1["units"] and old not in s1["streams"], "")
c.check("stage_model does not mutate the original", k2 in m["units"] and old in m["streams"], "")

res = {}
for st in (0, 1, -1):
    sol = solve(dict(m, stage=st))
    res[st] = sol
    c.check(f"stage {st} solves without errors", not sol.errors, sol.errors)
sid_out = next(k for k, s in m["streams"].items() if m["units"][s["dst"][0]]["name"] == "Export gas")
p0, p1, pd = (res[s].streams[sid_out].P for s in (0, 1, -1))
c.close("today the export pressure is the LP compressor discharge", p0, 29.5, 1.0)
c.close("after phase 1 the export pressure is set by the HP compressor", p1, 89.5, 1.0)
c.close("design view solves the final stage", pd, p1, 1e-6)
c.check("future compressor is flagged inactive/absent at stage 0", k2 not in res[0].status, res[0].status.get(k2))

# port sharing: two streams may use one single-connection port only if never present together
c.check("future and removed streams share the cooler inlet without being dropped",
        sum(1 for s in m["streams"].values() if s["dst"][1] == "in" and m["units"][s["dst"][0]]["name"].startswith("E-100")) == 2, "")
m2 = copy.deepcopy(m)
extra = next(k for k, s in m2["streams"].items() if s["name"] == "5")
m2["streams"]["dup"] = dict(m2["streams"][extra], name="dup")
normalize(m2)
c.check("overlapping duplicate on a single-connection port is removed", "dup" not in m2["streams"], "")

# phases API
mm = new_model()
a = PH.add_phase(mm, "A")
b = PH.add_phase(mm, "B")
c.check("phases get distinct colours", a["color"] != b["color"], "")
u = add_unit(mm, "valve", 0, 0, "V-1")
PH.set_phase(mm, [u], b["id"], "add")
c.eq("set_phase tags the element", mm["units"][u]["phase"], b["id"])
PH.remove_phase(mm, a["id"])
c.check("removing a phase keeps tags of the others", mm["units"][u]["phase"] == b["id"] and len(mm["phases"]) == 1, "")
PH.remove_phase(mm, b["id"])
c.check("removing the owning phase clears the tag", "phase" not in mm["units"][u], "")

# ---- gas turbine -------------------------------------------------------------------------------------------------
g = new_model()
f = add_unit(g, "feed", 0, 0, "Fuel", {"T_C": 30.0, "P_bar": 20.0, "flow_basis": "kg/h", "flow": 800.0, "composition": dict(WET_GAS)})
gt = add_unit(g, "gas_turbine", 100, 0, "GT-1", {"rated_kW": 50000.0, "eff": 35.0, "gen_eff": 97.0})
connect(g, f, "out", gt, "fuel", "1")
sg = solve(g)
r = sg.results[gt]
c.check("gas turbine solves", sg.status[gt] == "ok", sg.errors.get(gt))
pw0 = W(sg)
c.check("gas turbine produces a work stream (negative duty = produced)", pw0 > 0, pw0)
c.rel("electric power = fuel heat x eff x gen eff", pw0, r["Fuel heat input [kW]"] * 0.35 * 0.97, 0.01)
fuel_MW = next((v for k, v in r.items() if "Fuel heat" in k or "LHV" in k), None)
c.check("fuel heat result reported", fuel_MW is not None, list(r))
g2 = copy.deepcopy(g)
g2["units"][gt]["params"]["rated_kW"] = 1000.0
sg2 = solve(g2)
w2 = W(sg2)
c.check("rating caps the shaft power", w2 <= 1000.0 * 1.0001, w2)
g3 = copy.deepcopy(g)
g3["units"][gt]["params"]["eff"] = 30.0
w3 = W(solve(g3))
c.rel("power scales with efficiency", w3 / pw0, 30.0 / 35.0, 0.01)
g4 = copy.deepcopy(g)
g4["units"][gt]["params"]["amb_T"] = 40.0
g4["units"][gt]["params"]["rated_kW"] = 3000.0
g5 = copy.deepcopy(g4)
g5["units"][gt]["params"]["amb_T"] = 15.0
c.check("hot day derates the capped machine", W(solve(g4)) < W(solve(g5)), "")
pl = byname(m, "Platform")
c.check("platform is drawn only: status ok, no results", res[0].status.get(pl) == "ok" and not res[0].results.get(pl), res[0].results.get(pl))

# ---- phase splitter ----------------------------------------------------------------------------------------------
from procsim.examples import WELL_FLUID                                   # noqa: E402
q = new_model()
f = add_unit(q, "feed", 0, 0, "Well", {"T_C": 60.0, "P_bar": 20.0, "flow_basis": "kg/h", "flow": 50000.0, "composition": dict(WELL_FLUID)})
ps = add_unit(q, "phase_splitter", 100, 0, "PS", {"dP": 0.0})
gv, ol, wt = (add_unit(q, "product", 200, y, n) for y, n in ((-60, "G"), (0, "O"), (60, "W")))
connect(q, f, "out", ps, "feed", "1")
connect(q, ps, "vapour", gv, "in", "g")
connect(q, ps, "oil", ol, "in", "o")
connect(q, ps, "water", wt, "in", "w")
sq = solve(q)
c.check("phase splitter solves", sq.status[ps] == "ok", sq.errors.get(ps))
tot_in = sum(s.F * s.MW for k, s in sq.streams.items() if q["streams"][k]["name"] == "1")
tot_out = sum(s.F * s.MW for k, s in sq.streams.items() if q["streams"][k]["name"] in ("g", "o", "w"))
c.rel("phase splitter conserves mass", tot_out, tot_in, 1e-6)
q["units"][ps]["params"].update(liq_in_gas=0.1)
sq2 = solve(q)
c.check("carry-over parameters keep the balance", sq2.status[ps] == "ok", sq2.errors.get(ps))
tot_out2 = sum(s.F * s.MW for k, s in sq2.streams.items() if q["streams"][k]["name"] in ("g", "o", "w"))
c.rel("balance holds with carry-over", tot_out2, tot_in, 1e-6)

sys.exit(c.report())
