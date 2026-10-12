"""v7.8: refrigeration cycle, two-stage membrane, typical values, phase economics, heat-integration savings.

Run:  python tests/test_v78.py
"""
import copy
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from check import Checker                                                           # noqa: E402
from procsim import refrigeration as RF, typical as TY, phasecost as PCo, pinch as PI, economics as EC   # noqa: E402
from procsim.flowsheet import new_model, add_unit, connect, solve, port_edges        # noqa: E402
from procsim.examples import WET_GAS, EXAMPLES                                       # noqa: E402
from procsim.unitops import CATALOGUE                                                # noqa: E402

c = Checker("v78")

# ---- refrigeration cycle -------------------------------------------------------------------------------------------
r = RF.cycle("Propane (R-290)", -25.0, 40.0, 0.75, 5.0, 0.0, 1, 1000.0)
c.close("propane: Psat(-25 °C) ≈ 2.03 bar (NIST 2.03)", r["Pe"], 2.03, 0.03)
c.close("propane: Psat(40 °C) ≈ 13.7 bar (NIST 13.69)", r["Pc"], 13.69, 0.06)
c.check("propane: COP at η 0.75 in the textbook range 1.8-2.3", 1.8 < r["COP"] < 2.3, str(r["COP"]))
c.close("propane: condenser heat = evaporator duty + compressor work", r["Q_condenser_kW"], 1000.0 + r["W_kW"], 1e-9)
c.check("COP below Carnot", r["COP"] < r["COP_carnot"], "")
r_hi = RF.cycle("Propane (R-290)", -25.0, 40.0, 0.9, 5.0, 0.0, 1, 1000.0)
c.check("higher compressor efficiency -> less power", r_hi["W_kW"] < r["W_kW"], "")
r_sc = RF.cycle("Propane (R-290)", -25.0, 40.0, 0.75, 5.0, 5.0, 1, 1000.0)
c.check("sub-cooling -> better COP", r_sc["COP"] > r["COP"], "")
r2 = RF.cycle("Propane (R-290)", -25.0, 40.0, 0.75, 5.0, 0.0, 2, 1000.0)
c.check("economiser: COP up 5-30 % against single stage", 1.05 < r2["COP"] / r["COP"] < 1.30, str(r2["COP"] / r["COP"]))
c.check("economiser: intermediate pressure = √(Pe Pc)", abs(r2["Pm"] - (r["Pe"] * r["Pc"]) ** 0.5) < 1e-6, "")
r_a = RF.cycle("Ammonia (R-717)", -25.0, 40.0, 0.75, 5.0, 0.0, 1, 1000.0)
c.check("ammonia: positive power, COP 1.8-2.6", 1.8 < r_a["COP"] < 2.6, str(r_a["COP"]))
c.close("ammonia: Psat(-25 °C) ≈ 1.52 bar", r_a["Pe"], 1.516, 0.02)
try:
    RF.cycle("Propane (R-290)", 60.0, 40.0)
    c.check("condensing below evaporating is rejected", False, "")
except RF.RefrigError:
    c.check("condensing below evaporating is rejected", True, "")
try:
    RF.cycle("Propane (R-290)", -25.0, 95.0)
    c.check("condensing near the critical point is rejected", False, "")
except RF.RefrigError:
    c.check("condensing near the critical point is rejected", True, "")

m = new_model()
f = add_unit(m, "feed", params={"T_C": 20.0, "P_bar": 60.0, "flow_basis": "MSm³/d", "flow": 2.0, "composition": dict(WET_GAS)})
ch = add_unit(m, "chiller", params={"T_out": -15.0, "model": "Cycle (PR equation of state)", "refrigerant": "Propane (R-290)", "T_cond": 40.0})
pr = add_unit(m, "product")
connect(m, f, "out", ch, "in")
connect(m, ch, "out", pr, "in")
sol = solve(m)
rc = sol.results[ch]
c.check("chiller with the cycle model solves", sol.status[ch] == "ok", str(sol.errors))
c.close("chiller: compressor power = cycle power", rc["Refrigeration power [kW]"], rc["Duty removed [kW]"] / rc["COP [-]"], 1e-9)
c.check("chiller reports refrigerant flow and discharge T", rc["Refrigerant circulation [kg/h]"] > 0 and rc["Compressor discharge T [°C]"] > 40, str(rc))
m_c = copy.deepcopy(m)
m_c["units"][ch]["params"]["model"] = "Fraction of Carnot COP"
rk = solve(m_c).results[ch]
c.check("Carnot-fraction model still works", rk["COP [-]"] > 0, "")

# ---- two-stage membrane --------------------------------------------------------------------------------------------
comp = dict(WET_GAS)
comp["CO2"], comp["C1"] = 0.10, comp["C1"] - 0.079
tot = sum(comp.values())
comp = {k: v / tot for k, v in comp.items()}


def mem(cfg, **kw):
    mm = new_model()
    ff = add_unit(mm, "feed", params={"T_C": 40.0, "P_bar": 70.0, "flow_basis": "MSm³/d", "flow": 3.0, "composition": comp})
    mb = add_unit(mm, "membrane", params=dict({"spec": "CO2 in the retentate", "co2_spec": 2.5, "P_perm": 3.0, "config": cfg}, **kw))
    a, b = add_unit(mm, "product"), add_unit(mm, "product")
    connect(mm, ff, "out", mb, "feed")
    connect(mm, mb, "retentate", a, "in")
    connect(mm, mb, "permeate", b, "in")
    s = solve(mm)
    return mm, mb, s


m1, mb1, s1 = mem("Single stage")
TWO = "Two stages: permeate recompressed, 2nd retentate recycled"
m2, mb2, s2 = mem(TWO)
r1, r2_ = s1.results[mb1], s2.results[mb2]
c.close("two-stage: same CO₂ specification reached", r2_["CO₂ in the retentate [mol%]"], 2.5, 1e-4)
c.check("two-stage: methane loss clearly lower", r2_["Methane loss to the permeate [%]"] < 0.6 * r1["Methane loss to the permeate [%]"],
        f"{r1['Methane loss to the permeate [%]']} {r2_['Methane loss to the permeate [%]']}")
c.check("two-stage: recompressor power reported and a work stream exists", r2_["Recompressor power [kW]"] > 0 and any(e.kind == "work" and "recompressor" in e.unit for e in s2.energy), "")
fin = s2.streams[port_edges(m2, mb2, "in")["feed"][0]]
tot_out = sum(s2.streams[x].F * s2.streams[x].z for lst in port_edges(m2, mb2, "out").values() for x in lst)
c.close("two-stage: component balance over the unit (rel.)", float(np.max(np.abs(tot_out - fin.F * fin.z))) / fin.F, 0.0, 1e-6)
en = {}
for e in s2.energy:
    en[e.unit] = en.get(e.unit, 0.0) + e.duty_kW
hin = fin.heat_flow_kW
hout = sum(s2.streams[x].heat_flow_kW for lst in port_edges(m2, mb2, "out").values() for x in lst)
c.close("two-stage: process energy balance (rel.)", (hin + en.get(m2["units"][mb2]["name"], 0.0) - hout) / max(abs(hin), abs(hout), 1.0), 0.0, 1e-6)

# ---- typical values -------------------------------------------------------------------------------------------------
bad = []
for t, d in TY.PRESETS.items():
    spec = {p["key"]: p for p in CATALOGUE[t]["params"]}
    for n, v in d.items():
        for k, x in v.items():
            if k not in spec or (spec[k].get("options") and x not in spec[k]["options"]):
                bad.append((t, n, k))
c.check("every preset names real parameters and valid options", not bad, str(bad))
u = {"type": "compressor", "params": {"spec": "Outlet pressure", "P_out": 60.0, "eff": 70.0}}
keys = TY.apply(u, "Centrifugal, process gas (polytropic 78 %)")
c.check("preset changes only its own parameters", u["params"]["eff"] == 78.0 and u["params"]["P_out"] == 60.0 and set(keys) == {"eff_type", "eff"}, str(u))

# ---- pinch savings ---------------------------------------------------------------------------------------------------
rows = [{"name": "H", "type": "heater", "kind": "cold", "Ts": 20, "Tt": 120, "duty": 1000.0, "CP": 10.0},
        {"name": "C", "type": "cooler", "kind": "hot", "Ts": 150, "Tt": 40, "duty": 900.0, "CP": 900 / 110}]
res = PI.analyse(rows, 10, 15)
sv = PI.savings(res, {"heating": "Gas-fired heater", "driver": "Grid electricity"})
fuel = 900.0 / 0.85 * 3.6 / 36.0
c.close("savings: fuel saved = ΔQH / η × 3.6 / LHV", sv["fuel_saved_Sm3_h"], fuel, 1e-9)
c.check("savings: CO₂ and money positive, payback finite", sv["co2_saved_t_y"] > 0 and sv["total_saved"] > 0 and sv["payback_y"] > 0, str(sv))
sv0 = PI.savings(res, {"heating": "Waste heat (free)"})
c.check("free heat saves only the cooling-water pumping", sv0["energy_cost_saved"] < sv["energy_cost_saved"], "")

# ---- phase economics -------------------------------------------------------------------------------------------------
k = next(n for n in EXAMPLES if n.startswith("Phasing (v7.6)"))
mp = EXAMPLES[k]()
out = PCo.rollup(mp)
c.eq("one row per phase", len(out["phases"]), 1)
c.eq("stages = phases + 1", len(out["stages"]), 2)
ph = out["phases"][0]
comp_ = [e for e in out["elements"] if e["Phase"] == ph["id"]]
c.check("CAPEX of the phase = installed cost of the added units", abs(ph["capex_Mcur"] - sum(e["Installed [Mcur]"] for e in comp_)) < 1e-9, "")
cmp_ = next(e for e in comp_ if e["Type"] == "compressor")
p0 = PCo.cost_params(mp)
c.close("compressor equipment cost = C_ref (S/S_ref)^n", cmp_["Equipment [Mcur]"], 20.0 * (cmp_["Size"] / 5000.0) ** 0.75, 1e-9)
c.close("installed = equipment × install × (1 + contingency)", cmp_["Installed [Mcur]"], cmp_["Equipment [Mcur]"] * p0["install"] * (1 + p0["contingency"] / 100.0), 1e-9)
mp["phasecost"] = {"overrides": {cmp_["Unit"]: 10.0}}
out2 = PCo.rollup(mp)
c.close("override replaces the cost model", out2["phases"][0]["capex_Mcur"], 10.0, 1e-9)
c.close("NPV = −CAPEX + net × annuity", out2["phases"][0]["npv_Mcur"],
        -10.0 + out2["phases"][0]["net_Mcur_y"] * PCo._annuity(8.0, 15.0), 1e-9)
try:
    PCo.rollup(new_model())
    c.check("no phases is an error", False, "")
except ValueError:
    c.check("no phases is an error", True, "")

sys.exit(c.report())
