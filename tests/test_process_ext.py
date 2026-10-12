"""v7.7 process units (chiller, membrane, molecular sieve), pinch targets and the process advisor.

Run:  python tests/test_process_ext.py
"""
import copy
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from check import Checker                                                   # noqa: E402
from procsim import pinch as PI, advisor as AD                              # noqa: E402
from procsim.examples import EXAMPLES, WET_GAS                              # noqa: E402
from procsim.flowsheet import new_model, add_unit, connect, solve           # noqa: E402

c = Checker("process_ext")


def one_unit(kind, params, comp, T=30.0, P=60.0, outs=("out",), inport="in", flow=5.0):
    m = new_model()
    f = add_unit(m, "feed", 0, 0, "Gas", {"T_C": T, "P_bar": P, "flow_basis": "MSm³/d", "flow": flow, "composition": dict(comp)})
    u = add_unit(m, kind, 100, 0, "X", params)
    connect(m, f, "out", u, inport, "1")
    for i, o in enumerate(outs):
        pr = add_unit(m, "product", 200, 50 * i, f"P-{o}")
        connect(m, u, o, pr, "in", f"o{i}")
    return m, f, u


def flows(m, sol, name_frag):
    sid = next(k for k, s in m["streams"].items() if m["units"][s["dst"][0]]["name"] == f"P-{name_frag}")
    s = sol.streams[sid]
    return s.F * s.z


sour = dict(WET_GAS)
sour["CO2"] = 0.12
sour["C1"] -= 0.099
sour["H2O"] = 0.0
t_ = sum(sour.values())
sour = {k: v / t_ for k, v in sour.items()}

# ---- chiller ------------------------------------------------------------------------------------------------------
m, f, u = one_unit("chiller", {"T_out": -20.0, "approach": 5.0, "T_cond": 40.0, "carnot": 55.0}, WET_GAS, T=35.0)
s = solve(m)
r = s.results[u]
c.check("chiller solves", s.status[u] == "ok", s.errors.get(u))
Te, Tc = 253.15 - 5.0, 313.15
c.rel("Carnot COP = Te / (Tc - Te)", r["Carnot COP [-]"], Te / (Tc - Te), 1e-6)
c.rel("COP = Carnot x efficiency", r["COP [-]"], 0.55 * Te / (Tc - Te), 1e-6)
c.rel("power = duty / COP", r["Refrigeration power [kW]"], r["Duty removed [kW]"] / r["COP [-]"], 1e-9)
c.rel("condenser heat = duty + power", r["Condenser heat rejected [kW]"], r["Duty removed [kW]"] + r["Refrigeration power [kW]"], 1e-9)
c.check("chiller duty is the enthalpy change", abs(r["Duty removed [kW]"] - sum(-e.duty_kW for e in s.energy if e.kind == "heat")) < 1e-6, "")
c.check("chiller work shows as an energy stream", any(e.kind == "work" and e.duty_kW > 0 for e in s.energy), "")
m2 = copy.deepcopy(m)
m2["units"][u]["params"]["T_out"] = -40.0
r2 = solve(m2).results[u]
c.check("colder outlet needs more power per kW (lower COP)", r2["COP [-]"] < r["COP [-]"], (r2["COP [-]"], r["COP [-]"]))
m3 = copy.deepcopy(m)
m3["units"][u]["params"]["T_cond"] = -30.0
s3 = solve(m3)
c.check("condensing below the evaporating temperature is refused", s3.status[u] == "error", s3.status[u])
m4 = copy.deepcopy(m)
m4["units"][u]["params"]["T_out"] = 80.0
c.check("a chiller cannot heat", solve(m4).status[u] == "error", "")

# ---- membrane -----------------------------------------------------------------------------------------------------
P_ = {"spec": "Membrane area", "area": 6000.0}
m, f, u = one_unit("membrane", P_, sour, inport="feed", outs=("retentate", "permeate"))
s = solve(m)
r = s.results[u]
c.check("membrane solves", s.status[u] == "ok", s.errors.get(u))
nfeed = next(x for k, x in s.streams.items() if m["units"][m["streams"][k]["src"][0]]["type"] == "feed")
nin = nfeed.F * nfeed.z
nret, nperm = flows(m, s, "retentate"), flows(m, s, "permeate")
c.check("membrane conserves every component", float(np.max(np.abs(nin - nret - nperm))) < 1e-6 * nin.sum(), float(np.max(np.abs(nin - nret - nperm))))
c.check("CO2 is enriched in the permeate and depleted in the retentate", r["CO₂ in the permeate [mol%]"] > r["CO₂ in the feed [mol%]"] > r["CO₂ in the retentate [mol%]"], "")
areas, rem, loss = [], [], []
for a in (2000.0, 6000.0, 15000.0):
    mm = copy.deepcopy(m)
    mm["units"][u]["params"]["area"] = a
    rr = solve(mm).results[u]
    rem.append(rr["CO₂ removal [%]"])
    loss.append(rr["Methane loss to the permeate [%]"])
c.check("more area removes more CO2 and loses more methane", rem == sorted(rem) and loss == sorted(loss), (rem, loss))
mm = copy.deepcopy(m)
mm["units"][u]["params"].update(spec="CO2 in the retentate", co2_spec=2.5)
rr = solve(mm).results[u]
c.close("area found for the CO2 specification meets it", rr["CO₂ in the retentate [mol%]"], 2.5, 0.02)
mm["units"][u]["params"]["co2_spec"] = 0.0001
c.check("an unreachable specification is reported, not looped", solve(mm).status[u] == "error", "")
mm = copy.deepcopy(m)
mm["units"][u]["params"]["sel_CH4"] = 60.0
c.check("higher selectivity loses less methane", solve(mm).results[u]["Methane loss to the permeate [%]"] < loss[1], "")
mm = copy.deepcopy(m)
mm["units"][u]["params"]["P_perm"] = 70.0
c.check("permeate pressure above the feed pressure is refused", solve(mm).status[u] == "error", "")
mm = copy.deepcopy(m)
mm["units"][u]["params"]["P_perm"] = 10.0
c.check("higher permeate pressure removes less CO2", solve(mm).results[u]["CO₂ removal [%]"] < rem[1], "")

# ---- molecular sieve ----------------------------------------------------------------------------------------------
m, f, u = one_unit("mol_sieve", {"ppmv": 1.0, "cycle_h": 8.0, "capacity": 8.0}, WET_GAS, T=30.0, P=40.0, inport="feed", outs=("dry", "water"))
s = solve(m)
r = s.results[u]
c.check("sieve solves", s.status[u] == "ok", s.errors.get(u))
c.close("dried gas meets the ppmv specification", r["Water out [ppmv]"], 1.0, 1e-6)
iw = s.fp.keys.index("H2O")
nfeed = next(x for k, x in s.streams.items() if m["units"][m["streams"][k]["src"][0]]["type"] == "feed")
nin = nfeed.F * nfeed.z
nd, nw = flows(m, s, "dry"), flows(m, s, "water")
c.check("sieve conserves every component", float(np.max(np.abs(nin - nd - nw))) < 1e-6 * nin.sum(), "")
c.rel("sieve mass per bed = water x cycle / loading", r["Sieve mass per bed [kg]"], r["Water removed [kg/h]"] * 8.0 / 0.08, 1e-9)
c.rel("bed volume from the bulk density", r["Bed volume [m³]"], r["Sieve mass per bed [kg]"] / 700.0, 1e-9)
c.check("regeneration heat is positive and shown as an energy stream", r["Regeneration heater duty [kW]"] > 0 and any("regeneration" in e.name for e in s.energy), "")
mm = copy.deepcopy(m)
mm["units"][u]["params"]["cycle_h"] = 16.0
c.check("longer cycle needs more sieve", solve(mm).results[u]["Sieve mass per bed [kg]"] > r["Sieve mass per bed [kg]"], "")

# ---- pinch --------------------------------------------------------------------------------------------------------
rows = [
    {"name": "H1", "type": "cooler", "kind": "hot", "Ts": 180.0, "Tt": 60.0, "duty": 360.0, "CP": 3.0},
    {"name": "H2", "type": "cooler", "kind": "hot", "Ts": 150.0, "Tt": 30.0, "duty": 120.0, "CP": 1.0},
    {"name": "C1", "type": "heater", "kind": "cold", "Ts": 20.0, "Tt": 135.0, "duty": 230.0, "CP": 2.0},
    {"name": "C2", "type": "heater", "kind": "cold", "Ts": 80.0, "Tt": 140.0, "duty": 270.0, "CP": 4.5},
]
res = PI.analyse(rows, 10.0)
c.close("textbook case: minimum hot utility", res["QH_min"], 50.0, 1e-6)
c.close("textbook case: minimum cold utility", res["QC_min"], 30.0, 1e-6)
c.close("textbook case: pinch at 85 °C (shifted)", res["pinch"], 85.0, 1e-6)
c.close("QH - QC equals the stream imbalance", res["QH_min"] - res["QC_min"], 500.0 - 480.0, 1e-6)
c.close("current utilities are the heater / cooler duties", res["QH_now"], 500.0, 1e-9)
c.close("recovery = current - target (hot side)", res["recovery"], 450.0, 1e-6)
big = PI.analyse(rows, 40.0)
c.check("a larger ΔTmin recovers less", big["QH_min"] > res["QH_min"] and big["recovery"] < res["recovery"], (big["QH_min"], res["QH_min"]))
c.check("composite curves end at the same total heat", abs(res["hot_curve"]["Q"][-1] - 480.0) < 1e-6 and abs(res["cold_curve"]["Q"][-1] - res["cold_curve"]["Q"][0] - 500.0) < 1e-6, "")
one = PI.analyse(rows[:2], 10.0)
c.check("only hot streams: no pinch, nothing to recover", one["pinch"] is None and one["recovery"] == 0.0, "")
ex = [dict(rows[0]), dict(rows[2], name="CH", type="chiller", kind="hot", Ts=20.0, Tt=-20.0)]
c.check("chillers below ambient are left out", [s["name"] for s in PI.analyse(ex + [rows[3]], 10.0, ambient=15.0)["skipped"]] == ["CH"], "")
nm = next(k for k in EXAMPLES if k.startswith("Gas conditioning"))
mg = EXAMPLES[nm]()
sg = solve(mg)
c.check("gas conditioning example solves", sg.converged and all(v == "ok" for v in sg.status.values()), str(sg.errors))
cr = PI.collect(mg, sg)
c.check("collect finds the heater and the cooler", {x["name"] for x in cr} >= {"E-100 Feed heater", "E-101 Permeate cooler"}, [x["name"] for x in cr])

# ---- advisor ------------------------------------------------------------------------------------------------------
jt = EXAMPLES[next(k for k in EXAMPLES if k.startswith("JT dew-point"))]()
a = AD.advise(jt, solve(jt))
c.check("advisor: gas letdown valve suggests an expander", any("letdown" in x["Finding"] and x["Level"] == "Opportunity" and x["Value"] > 50 for x in a), [x["Finding"] for x in a])
oil = EXAMPLES[next(k for k in EXAMPLES if k.startswith("Oil stabilisation"))]()
a = AD.advise(oil, solve(oil))
c.check("advisor: very high compression ratio flagged", any("Pressure ratio" in x["Finding"] for x in a), [x["Finding"] for x in a])
c.check("advisor: heat integration opportunity found in the oil train", any("exchanged" in x["Finding"] for x in a), [x["Finding"] for x in a])
c.check("advisor: findings are sorted opportunities first", [x["Level"] for x in a] == sorted([x["Level"] for x in a], key=["Opportunity", "Check", "Info"].index), "")
ph = EXAMPLES[next(k for k in EXAMPLES if k.startswith("Phasing"))]()
a = AD.advise(ph, solve(ph))
c.check("advisor: unused turbine exhaust heat", any("Exhaust heat" in x["Finding"] for x in a), [x["Finding"] for x in a])
fl = EXAMPLES[next(k for k in EXAMPLES if k.startswith("Pressure relief"))]()
c.check("advisor: flare gas", any("flaring" in x["Finding"] for x in AD.advise(fl, solve(fl))), "")
for n, fn in EXAMPLES.items():
    mx = fn()
    try:
        AD.advise(mx, solve(mx))
        PI.analyse(PI.collect(mx, solve(mx)), 10.0, 15.0)
    except Exception as e:     # noqa: BLE001
        c.check(f"advisor and pinch run on {n}", False, repr(e)[:120])
c.check("advisor and pinch ran on every example", True, "")

sys.exit(c.report())
