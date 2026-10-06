"""Process units added in v6.5: amine sweetening, PSV / flare, component splitter, reactors, valve Cv, drivers.

Run:  python tests/test_pack_a.py
"""
import copy
import math
import os
import sys

import numpy as np

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
from check import Checker                                                  # noqa: E402
from procsim import process_units as PU                                    # noqa: E402
from procsim.examples import amine_sweetening, relief_and_flare, steam_reformer, SOUR_GAS   # noqa: E402
from procsim.flowsheet import solve, new_model, add_unit, connect, port_edges             # noqa: E402
from procsim.thermo import FluidPackage                                    # noqa: E402

c = Checker("pack A")


def find(m, typ):
    return next(u for u, x in m["units"].items() if x["type"] == typ)


def balance(m, sol, uid):
    ins = [sol.streams[s] for lst in port_edges(m, uid, "in").values() for s in lst]
    outs = [sol.streams[s] for lst in port_edges(m, uid, "out").values() for s in lst]
    nin = sum((s.F * s.z for s in ins if not s.empty), np.zeros(sol.fp.n))
    nout = sum((s.F * s.z for s in outs if not s.empty), np.zeros(sol.fp.n))
    return nin, nout


# ---- API 520 / 521 correlations against hand calculations -----------------------------------------------
k, Z, M, T, P1 = 1.11, 1.0, 51.0, 348.0, 838.325
Cc = 520.0 * math.sqrt(k * (2.0 / (k + 1.0)) ** ((k + 1.0) / (k - 1.0)))
A, regime = PU.gas_relief_area(24270.0, T, P1, 101.325, k, Z, M, 0.975)
c.rel("API 520 vapour area (hand calculation of the critical-flow formula)", A,
      13160.0 * 24270.0 / (Cc * 0.975 * P1) * math.sqrt(T * Z / M), 1e-9)
c.eq("critical flow detected", regime, "critical")
c.rel("C coefficient for k = 1.11 is about 328", Cc, 327.8, 0.002)
A2, reg2 = PU.gas_relief_area(24270.0, T, P1, 700.0, k, Z, M, 0.975)
c.check("sub-critical back pressure needs a larger area", reg2 == "sub-critical" and A2 > A, f"{A2} {reg2}")
c.rel("API 520 liquid area", PU.liquid_relief_area(1000.0, 0.9, 300.0), 11.78 * 1000 / 0.65 * math.sqrt(0.9 / 300.0), 1e-9)
c.eq("orifice selection 739 mm² → J", PU.select_orifice(739.0)[0], "J")
c.eq("orifice selection 830 mm² → J (exact)", PU.select_orifice(830.0)[0], "J")
c.eq("orifice selection above T → several valves", PU.select_orifice(40000.0)[2], 3)
Q, Aw, W = PU.fire_load(3.0, 10.0, 50.0, "Horizontal", 1.0, True, 300.0)
c.close("wetted area of a half-full 3 m × 10 m horizontal drum", Aw, math.pi * 3 * 10 / 2 + math.pi * 9 / 4, 1e-6)
c.rel("API 521 fire heat input 43.2·F·A^0.82", Q, 43.2 * Aw ** 0.82, 1e-9)
c.rel("fire vapour rate = Q/λ", W, Q / 300.0 * 3600.0, 1e-9)
c.check("poor drainage raises the fire heat input", PU.fire_load(3, 10, 50, "Horizontal", 1.0, False, 300.0)[0] > Q, "")

# ---- PSV + flare example -----------------------------------------------------------------------------------
m = relief_and_flare()
s = solve(m)
r, fl = find(m, "relief_valve"), find(m, "flare")
c.check("PSV example solves", s.status[r] in ("ok", "warning") and s.status[fl] in ("ok", "warning"), s.errors)
rr = s.results[r]
c.rel("relieving pressure = set·(1 + 10 %)", rr["Relieving pressure [bar(a)]"], (66.0 - 1.01325) * 1.1 + 1.01325, 1e-9)
c.check("orifice has at least the required area", PU.select_orifice(rr["Required area [mm²]"])[1] >= rr["Required area [mm²]"], "")
c.check("outlet is at the back pressure", abs(s.streams[port_edges(m, r, "out")["out"][0]].P - 2.0) < 1e-6, "")
fr = s.results[fl]
fs = s.streams[port_edges(m, fl, "in")["in"][0]]
c.rel("flare heat release = mass × LHV (methane-rich gas ≈ 46-50 MJ/kg)", fr["Heat release [MW]"] / (fr["Flared gas [kg/h]"] / 3600.0), 48.0, 0.12)
c.check("flame length from the Hajek-Ludwig fit", abs(fr["Flame length [m]"] - 0.00326 * (fr["Heat release [MW]"] * 1e6) ** 0.478) < 1e-6, "")
c.check("CO₂ from combustion is about 2.7 t per t of gas for this composition", 2.2 < fr["CO₂ emitted [t/h]"] / (fr["Flared gas [kg/h]"] / 1000.0) < 3.1, str(fr["CO₂ emitted [t/h]"]))
m2 = copy.deepcopy(m)
m2["units"][fl]["params"]["dist"] = 20.0
c.check("a closer receptor sees more radiation", solve(m2).results[fl]["Radiation at the receptor [kW/m²]"] >
        fr["Radiation at the receptor [kW/m²]"], "")
m3 = copy.deepcopy(m)
m3["units"][r]["params"]["P_set"] = 4.0
m3["units"][r]["params"]["P_back"] = 5.0
s3 = solve(m3)
c.check("back pressure above the set pressure is an error", s3.status[r] == "error", s3.status[r])

# ---- amine sweetening ------------------------------------------------------------------------------------------
m = amine_sweetening()
s = solve(m)
a = find(m, "amine_contactor")
ra = s.results[a]
nin, nout = balance(m, s, a)
c.check("amine: component balance closes", float(np.max(np.abs(nin - nout))) / float(nin.sum()) < 1e-9, "")
c.close("amine: CO₂ in the sweet gas at the 2 mol% specification", ra["CO₂ in sweet gas [mol%]"], 2.0, 0.02)
c.close("amine: H₂S in the sweet gas at 4 ppmv", ra["H₂S in sweet gas [ppmv]"], 4.0, 0.05)
c.check("amine: reboiler and pump appear as energy streams",
        any(e.name.endswith("reboiler") and e.duty_kW > 0 for e in s.energy) and any(e.name.endswith("pumps") for e in s.energy), "")
c.check("amine: absorption heat warms the solution", 0.0 < ra["Solution temperature rise [K]"] < 40.0, str(ra["Solution temperature rise [K]"]))
c.check("amine: specific reboiler duty in the textbook range (1-4 MJ/kg acid gas)", 1.0 < ra["Specific reboiler duty [MJ/kg acid gas]"] < 4.5, str(ra["Specific reboiler duty [MJ/kg acid gas]"]))
sweet = s.streams[port_edges(m, a, "out")["sweet"][0]]
c.close("amine: sweet-gas stream CO₂ mole fraction matches the report", 100 * sweet.z[s.fp.keys.index("CO2")], ra["CO₂ in sweet gas [mol%]"], 0.01)
m2 = copy.deepcopy(m)
m2["units"][a]["params"]["co2_spec"] = 0.5
c.check("a tighter CO₂ specification needs more circulation",
        solve(m2).results[a]["Amine circulation [m³/h]"] > ra["Amine circulation [m³/h]"], "")
m3 = copy.deepcopy(m)
m3["units"][a]["params"]["amine"] = "MEA (15 wt%)"
c.check("MEA (15 wt%) circulates more than MDEA (40 wt%)",
        solve(m3).results[a]["Amine circulation [m³/h]"] > ra["Amine circulation [m³/h]"], "")

# ---- component splitter ------------------------------------------------------------------------------------------
G = {"N2": 0.01, "CO2": 0.02, "C1": 0.85, "C2": 0.07, "C3": 0.04}
m = new_model(list(G))
f = add_unit(m, "feed", 0, 0, "Gas", {"T_C": 50.0, "P_bar": 60.0, "flow_basis": "kmol/h", "flow": 2000.0, "composition": dict(G)})
sp = add_unit(m, "comp_splitter", 200, 0, "X-1", {})
t = add_unit(m, "product", 400, -50, "Top")
b = add_unit(m, "product", 400, 50, "Bottom")
connect(m, f, "out", sp, "feed")
connect(m, sp, "top", t, "in")
connect(m, sp, "bottom", b, "in")
s = solve(m)
c.check("splitter solves", s.status[sp] in ("ok", "warning"), s.errors)
nin, nout = balance(m, s, sp)
c.check("splitter: component balance closes", float(np.max(np.abs(nin - nout))) < 1e-6, "")
sd = copy.deepcopy(m)
sd["units"][sp]["params"]["split"] = "C3: 1, C2: 0.5"
ss = solve(sd)
top = ss.streams[port_edges(sd, sp, "out")["top"][0]]
bot = ss.streams[port_edges(sd, sp, "out")["bottom"][0]]
ic = lambda k: s.fp.keys.index(k)                                           # noqa: E731
c.close("splitter: C3 fully to the top", bot.F * bot.z[ic("C3")], 0.0, 1e-9)
c.rel("splitter: half of the C2 to the top", top.F * top.z[ic("C2")], 0.5 * 2000 * 0.07 / sum(G.values()), 1e-9)

# ---- reactors --------------------------------------------------------------------------------------------------
K = ["C1", "O2", "N2", "CO2", "H2O"]
mc = new_model(K)
f = add_unit(mc, "feed", 0, 0, "Mix", {"T_C": 25.0, "P_bar": 0.2, "flow_basis": "kmol/h", "flow": 1000.0,
                                       "composition": {"C1": 0.05, "O2": 0.20, "N2": 0.75}})
rc = add_unit(mc, "conv_reactor", 200, 0, "R-1", {"reactions": "C1 + 2 O2 -> CO2 + 2 H2O", "conv": "1.0",
                                                   "spec": "Outlet temperature", "T_out": 25.0})
o = add_unit(mc, "product", 400, 0, "Out")
connect(mc, f, "out", rc, "feed")
connect(mc, rc, "out", o, "in")
s = solve(mc)
r = s.results[rc]
nin, nout = balance(mc, s, rc)
c.close("combustion: carbon is conserved", (nout[3] + nout[0]) - (nin[3] + nin[0]), 0.0, 1e-9)
c.close("combustion: 50 kmol/h CH₄ burned", r["Extent 1 [kmol/h]"], 50.0, 1e-9)
c.close("combustion: oxygen used 2:1", nin[1] - nout[1], 100.0, 1e-9)
c.rel("combustion at 25 °C, 0.2 bar (no condensation): cooling duty = heat of combustion of CH₄ (≈ 802.3 kJ/mol; water as vapour)", -r["Heat of reaction [kW]"],
      50.0 * 1000.0 * 802.3 / 3600.0, 0.01)
c.close("duty of the isothermal reactor = heat of reaction (energy balance)", r["Duty [kW]"], r["Heat of reaction [kW]"], 5.0)
mc2 = copy.deepcopy(mc)
mc2["units"][rc]["params"]["spec"] = "Adiabatic"
s2 = solve(mc2)
Tad = s2.results[rc]["Outlet T [°C]"]
c.within("adiabatic flame temperature of 5 % CH₄ in air (lean, 25 °C feed) 1100-1300 °C", Tad, 1100.0, 1300.0)
c.close("adiabatic reactor has no duty", s2.results[rc].get("Duty [kW]", 0.0), 0.0, 1e-6)
mc3 = copy.deepcopy(mc)
mc3["units"][rc]["params"]["conv"] = "0.5"
c.rel("50 % conversion burns half", solve(mc3).results[rc]["Extent 1 [kmol/h]"], 25.0, 1e-9)
mc4 = copy.deepcopy(mc)
mc4["units"][rc]["params"]["reactions"] = "C1 + 2 O2 -> CO2 + 2 H2O ; C1 + 0.5 O2 -> XX"
c.check("an unknown component in a reaction is reported", solve(mc4).status[rc] == "error", "")

fp = FluidPackage.from_keys(["C1", "H2O", "H2", "CO2"])
nu = {"C1": -1.0, "H2O": -2.0, "H2": 4.0, "CO2": 1.0}
c.close("reforming ln K at 1073 K (constant-ΔH gives 2.4; with Cp(T) about 5.2; published ≈ 5.2 ± 0.3)", PU.lnK_of_T(fp, nu, 1073.15), 5.2, 0.35)
c.close("ln K at 298.15 K = -ΔG/RT from the formation data", PU.lnK_of_T(fp, nu, 298.15), -113.3e3 / (8.314462618 * 298.15), 0.05)
m = steam_reformer()
s = solve(m)
re = find(m, "eq_reactor")
r = s.results[re]
c.check("reformer example solves", s.status[re] in ("ok", "warning"), s.errors)
c.within("reformer conversion at 800 °C, 3 bar, S/C = 3", r["Conversion of C1 [%]"], 80.0, 95.0)
nin, nout = balance(m, s, re)
c.close("reformer: H atoms conserved", float((4 * nout[0] + 2 * nout[1] + 2 * nout[2]) - (4 * nin[0] + 2 * nin[1] + 2 * nin[2])), 0.0, 1e-6)
mp = copy.deepcopy(m)
mp["units"][re]["params"]["T_out"] = 900.0
c.check("a hotter reformer converts more", solve(mp).results[re]["Conversion of C1 [%]"] > r["Conversion of C1 [%]"], "")
mq = copy.deepcopy(m)
mq["units"][find(mq, "feed")]["params"]["P_bar"] = 20.0
c.check("a higher pressure converts less (Le Chatelier)", solve(mq).results[re]["Conversion of C1 [%]"] < r["Conversion of C1 [%]"], "")
c.check("endothermic reactor needs heat", r["Duty [kW]"] > 0 and any(e.name == "Q-R-100 reformer" and e.duty_kW > 0 for e in s.energy), str(s.energy))
ma = copy.deepcopy(m)
ma["units"][re]["params"]["spec"] = "Adiabatic"
sa = solve(ma)
c.check("adiabatic reforming cools the gas below the feed temperature", sa.results[re]["Outlet T [°C]"] < 400.0, str(sa.results[re]))

# ---- control valve Cv and drivers ------------------------------------------------------------------------------------
Gv = {"C1": 0.9, "C2": 0.1}
mv = new_model(list(Gv))
f = add_unit(mv, "feed", 0, 0, "Gas", {"T_C": 40.0, "P_bar": 60.0, "flow_basis": "MSm³/d", "flow": 2.0, "composition": dict(Gv)})
v = add_unit(mv, "valve", 200, 0, "CV-1", {"spec": "Outlet pressure", "P_out": 40.0, "Cv_rated": 300.0})
o = add_unit(mv, "product", 400, 0, "Out")
connect(mv, f, "out", v, "in")
connect(mv, v, "out", o, "in")
s = solve(mv)
rv = s.results[v]
si = s.streams[port_edges(mv, v, "in")["in"][0]]
ph = si.flash.phase("V")
Qn = si.F * 22.414
x = 20.0 / 60.0
Y = 1.0 - x / (3.0 * (ph.Cp / ph.Cv / 1.4) * 0.7)
Kv_hand = Qn / (24.6 * 6000.0 * Y) * math.sqrt(ph.MW * si.T * ph.Z / x)
c.rel("valve: gas Kv from IEC 60534 (N9 = 24.6)", rv["Required Kv [m³/h]"], Kv_hand, 1e-6)
c.rel("valve: Cv = 1.156 Kv", rv["Required Cv [-]"], 1.156 * rv["Required Kv [m³/h]"], 1e-9)
c.eq("valve: not choked at ΔP/P1 = 0.33", rv["Choked flow"], "No")
c.check("valve: opening from the rated Cv (equal percentage)", 0 < rv["Valve opening [%]"] < 100, str(rv.get("Valve opening [%]")))
mv2 = copy.deepcopy(mv)
mv2["units"][v]["params"]["P_out"] = 5.0
c.eq("valve: large ΔP chokes", solve(mv2).results[v]["Choked flow"], "Yes")
mv3 = copy.deepcopy(mv)
mv3["units"][v]["params"]["Cv_rated"] = 50.0
c.check("valve: too small is flagged", "too small" in (solve(mv3).results[v].get("Warning") or ""), "")
mw = new_model(["H2O"])
f = add_unit(mw, "feed", 0, 0, "Water", {"T_C": 25.0, "P_bar": 10.0, "flow_basis": "kmol/h", "flow": 5550.8, "composition": {"H2O": 1.0}})
v = add_unit(mw, "valve", 200, 0, "CV-2", {"spec": "Pressure drop", "dP": 4.0})
o = add_unit(mw, "product", 400, 0, "Out")
connect(mw, f, "out", v, "in")
connect(mw, v, "out", o, "in")
sw = solve(mw)
q = 5550.8 * 18.015 / 997.0                                                  # m³/h, water ≈ 997 kg/m³
c.rel("valve: liquid Kv = Q·√(SG/ΔP) (≈ 100 m³/h at 4 bar → 50)", sw.results[v]["Required Kv [m³/h]"], q * math.sqrt(0.997 / 4.0), 0.02)

mk = new_model(list(Gv))
f = add_unit(mk, "feed", 0, 0, "Gas", {"T_C": 40.0, "P_bar": 20.0, "flow_basis": "MSm³/d", "flow": 2.0, "composition": dict(Gv)})
k1 = add_unit(mk, "compressor", 200, 0, "K-1", {"P_out": 50.0, "driver": "Gas turbine", "driver_rating": 3000.0, "driver_eff": 32.0})
o = add_unit(mk, "product", 400, 0, "Out")
connect(mk, f, "out", k1, "in")
connect(mk, k1, "out", o, "in")
s = solve(mk)
rk = s.results[k1]
c.rel("driver: fuel heat = shaft / efficiency", rk["Fuel heat [kW]"], rk["Power [kW]"] / 0.32, 1e-9)
c.check("driver: a turbine that is too small is flagged", "turbine" in (rk.get("Warning") or ""), str(rk.get("Warning")))
c.rel("driver: CO₂ from fuel gas (56.1 kg/GJ)", rk["CO₂ from the turbine [t/h]"], rk["Fuel heat [kW]"] * 3.6 * 56.1 / 1e6, 1e-9)
mk2 = copy.deepcopy(mk)
mk2["units"][k1]["params"].update({"driver": "Electric motor", "driver_rating": 5000.0, "driver_eff": 95.0, "amb_T": 40.0})
rk2 = solve(mk2).results[k1]
c.rel("driver: motor electrical input = shaft / 0.95", rk2["Electrical input [kW]"], rk2["Power [kW]"] / 0.95, 1e-9)
c.check("driver: sufficient motor → no warning", not rk2.get("Warning"), str(rk2.get("Warning")))
mk3 = copy.deepcopy(mk)
mk3["units"][k1]["params"].update({"driver_rating": 4000.0, "amb_T": 45.0})
c.close("driver: turbine power falls 0.7 %/K above 15 °C", solve(mk3).results[k1]["Driver power available [kW]"], 4000.0 * (1 - 0.007 * 30.0), 1e-6)
mk4 = copy.deepcopy(mk)
mk4["units"][k1]["params"]["driver"] = "None"
c.check("driver: none → no driver rows", "Fuel heat [kW]" not in solve(mk4).results[k1], "")

sys.exit(c.report())
