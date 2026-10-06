"""OLGA-style flow-assurance additions (v6.6): mechanistic regime, CO2 corrosion, emulsions, liquid loading,
pigging, line pack, insulation sizing and the PVT table.

Run:  python tests/test_pack_b.py
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
from procsim import flowassure2 as F                                       # noqa: E402
from procsim.examples import EXAMPLES                                      # noqa: E402
from procsim.flowsheet import solve                                        # noqa: E402

c = Checker("pack B")
from procsim.unitops import G                                               # noqa: E402

# ---- geometry and the stratified momentum balance --------------------------------------------------------------------
AL, AG, SL, SG, Si = F._geom(0.5)
c.close("half-full pipe: AL = AG = π/8", AL, math.pi / 8, 1e-12)
c.close("half-full pipe: SL = SG = π/2, Si = 1", SL + SG - math.pi, 0.0, 1e-12)
AL1, AG1, *_ = F._geom(1.0 - 1e-12)
c.close("full pipe: AL = π/4", AL1, math.pi / 4, 1e-9)
c.close("areas add to π/4", sum(F._geom(0.31)[:2]), math.pi / 4, 1e-12)
h1 = F._stratified_h(1.0, 0.0, 0.2, 0.2)
c.check("X = 1 (equal pressure drops) in a horizontal pipe gives h/D below a half-full pipe", h1 is not None and 0.1 < h1 < 0.5, str(h1))
hs = [F._stratified_h(x2, 0.0, 0.2, 0.2) for x2 in (0.01, 0.1, 1.0, 10.0)]
c.check("more liquid pressure drop (larger X) → thicker layer", all(a < b for a, b in zip(hs, hs[1:])), str(hs))
c.check("downward flow thins the layer", F._stratified_h(1.0, 1.0, 0.2, 0.2) < h1, "")

# ---- regime map: air-water at 1 bar, 5 cm horizontal; 10 cm vertical ------------------------------------------------------------
air = dict(rho_g=1.2, rho_l=1000.0, mu_g=1.8e-5, mu_l=1e-3)
horiz = lambda usg, usl, D=0.05: F.mechanistic_regime(usg, usl, D, 0.0, sigma=0.072, **air)     # noqa: E731
c.eq("horizontal: low rates are stratified", horiz(1.0, 0.01)["regime"], "Stratified")
c.eq("horizontal: high gas rate is annular", horiz(30.0, 0.02)["regime"], "Annular")
c.eq("horizontal: moderate liquid and gas is slug", horiz(4.0, 1.0)["regime"], "Slug (intermittent)")
c.eq("horizontal: high liquid, low gas is dispersed bubble", horiz(0.3, 5.0)["regime"], "Dispersed bubble")
c.eq("single-phase gas", horiz(5.0, 0.0)["regime"], "Single-phase gas")
c.close("single-phase liquid holdup is 1", horiz(0.0, 1.0)["HL"], 1.0, 1e-12)
vert = lambda usg, usl, D=0.1: F.mechanistic_regime(usg, usl, D, math.pi / 2, sigma=0.072, **air)  # noqa: E731
c.eq("vertical: bubble flow at low gas, high liquid", vert(0.1, 1.0)["regime"], "Bubble")
c.eq("vertical: slug / churn at moderate rates", vert(1.5, 0.3)["regime"], "Slug / churn")
c.eq("vertical: annular above the Taitel limit (≈ 3.1·(σgΔρ/ρg²)^0.25 = 20 m/s)", vert(30.0, 0.1)["regime"], "Annular")
c.eq("narrow tube (D below the bubble limit) has no bubble flow", vert(0.1, 1.0, D=0.02)["regime"], "Slug / churn")
r = vert(1.5, 0.3)
c.close("vertical slug holdup from the drift-flux relation (C0 = 1.2, u0 = 0.35√gD)", r["HL"],
        1.0 - 1.5 / (1.2 * 1.8 + 0.35 * math.sqrt(G * 0.1)), 1e-9)
r = vert(30.0, 0.1)
X = r["X"]
c.close("annular holdup from the Butterworth fit", r["HL"], 1.0 - (1.0 + X ** 0.8) ** -0.378, 1e-9)
c.check("holdup stays between no-slip and 1 in slug flow", horiz(4.0, 1.0)["HL"] >= 1.0 / 5.0 and horiz(4.0, 1.0)["HL"] <= 1.0, "")
c.check("more liquid → more holdup (horizontal slug)", horiz(4.0, 1.5)["HL"] > horiz(4.0, 0.5)["HL"], "")

# ---- CO2 corrosion ------------------------------------------------------------------------------------------------
T = 60.0 + 273.15
Vr = 10.0 ** (4.93 - 1119.0 / T)
Vm = 2.45 * 3.0 ** 0.8 / 0.2 ** 0.2
rate, det = F.co2_corrosion(60.0, 1.0, 3.0, 0.2)
c.rel("rate = kinetic and mass transfer in series", rate, 1.0 / (1.0 / Vr + 1.0 / Vm), 1e-9)
c.within("60 °C, 1 bar CO₂, 3 m/s, 0.2 m: a few mm/y (uninhibited carbon steel)", rate, 3.0, 15.0)
c.check("the rate rises with pCO₂", F.co2_corrosion(60.0, 3.0, 3.0, 0.2)[0] > rate, "")
c.check("the rate rises with velocity (mass transfer)", F.co2_corrosion(60.0, 1.0, 6.0, 0.2)[0] > rate, "")
r120 = F.co2_corrosion(120.0, 1.0, 3.0, 0.2)
c.check("scale-temperature correction lowers the rate above Ts", r120[1]["scale factor"] < 1.0, str(r120[1]))
c.close("no correction below Ts", det["scale factor"], 1.0, 1e-12)
c.close("pH of CO₂-saturated water at 25 °C, 1 bar ≈ 3.8", F.co2_corrosion(25.0, 1.0, 1.0, 0.2)[1]["pH of saturated water"], 3.81, 0.03)
r_ph = F.co2_corrosion(60.0, 1.0, 3.0, 0.2, pH=5.5)
c.check("a higher pH lowers the rate by 10^(0.32 ΔpH)", abs(r_ph[0] / rate - 10.0 ** (0.32 * (det["pH of saturated water"] - 5.5))) < 1e-9, "")
c.close("an inhibitor at 80 % removes 80 %", F.co2_corrosion(60.0, 1.0, 3.0, 0.2, inhib_eff=80.0)[0], 0.2 * rate, 1e-9)

# ---- emulsions ---------------------------------------------------------------------------------------------------------
c.close("inversion at μo = 1 cP is 50 %", F.inversion_cut(1.0), 0.5, 1e-12)
c.close("inversion for 100 cP oil (0.5 − 0.1108·2)", F.inversion_cut(100.0), 0.5 - 0.2216, 1e-12)
c.check("more viscous oil inverts at a lower water cut", F.inversion_cut(500.0) < F.inversion_cut(10.0), "")
c.close("no water: the oil viscosity", F.emulsion_viscosity(10.0, 0.6, 0.0)[0], 10.0, 1e-12)
c.close("all water: the water viscosity", F.emulsion_viscosity(10.0, 0.6, 1.0)[0], 0.6, 1e-12)
mu, cont = F.emulsion_viscosity(10.0, 0.6, 0.30)
c.close("Brinkman below the inversion", mu, 10.0 * 0.7 ** -2.5, 1e-9)
c.eq("oil is the continuous phase below the inversion", cont, "oil")
mu2, cont2 = F.emulsion_viscosity(10.0, 0.6, 0.9)
c.close("Brinkman above the inversion (water continuous)", mu2, 0.6 * 0.9 ** 0 * (1 - 0.1) ** -2.5, 1e-9)
c.eq("water is continuous above the inversion", cont2, "water")
cuts = [i / 40 for i in range(41)]
vis = [F.emulsion_viscosity(10.0, 0.6, w)[0] for w in cuts]
inv = F.inversion_cut(10.0)
i_peak = int(np.argmax(vis))
c.check("the viscosity peak sits at the inversion", abs(cuts[i_peak] - inv) < 0.04, f"{cuts[i_peak]} vs {inv}")

# ---- liquid loading ------------------------------------------------------------------------------------------------------
vt, vc = F.liquid_loading(3.0, 55.0, 1000.0, 0.06)
v_field = 1.593 * (60.0 ** 0.25) * ((1000.0 - 55.0) / 16.018) ** 0.25 / (55.0 / 16.018) ** 0.5 * 0.3048
c.rel("Coleman velocity equals the field-unit formula (water, 55 kg/m³ gas)", vc, v_field, 1e-3)
c.rel("Turner = 1.2 × Coleman", vt, 1.2 * vc, 1e-12)
c.within("water droplets in 70 bar gas: Coleman ≈ 2 m/s", vc, 1.8, 2.3)
c.check("lighter gas (lower pressure) needs a higher velocity", F.liquid_loading(3.0, 10.0, 1000.0, 0.06)[1] > vc, "")
c.check("a lower surface tension (condensate) lowers it", F.liquid_loading(3.0, 55.0, 700.0, 0.02)[1] < vc, "")

# ---- on a solved subsea field -------------------------------------------------------------------------------------------------
name = next(k for k in EXAMPLES if k.startswith("Subsea field (SURF)"))
m = EXAMPLES[name]()
s = solve(m)
mt = F.mechanistic_table(m, s)
c.check("mechanistic table: every line has an inlet and outlet row", len(mt) >= 2 * 3, str(len(mt)))
c.check("holdups are physical", all(0.0 <= r["Holdup (mechanistic) [-]"] <= 1.0 for r in mt), "")
rs = [r for r in mt if "Riser" in r["Line"]]
c.check("wet-gas riser: the mechanistic holdup is below Beggs & Brill's", rs and all(r["Holdup difference [-]"] < 0 for r in rs), str(rs[:1]))
ct = F.corrosion_table(m, s)
c.check("corrosion table: lines with free water and CO₂", len(ct) >= 3 and all(r["Free water"] == "yes" for r in ct), str(len(ct)))
c.check("corrosion: allowance needed = rate × life", all(abs(r["Allowance needed over the life [mm]"] - r["Corrosion rate [mm/y]"] * 25.0) < 1e-9 for r in ct), "")
ci = F.corrosion_table(m, s, {"inhib_eff": 95.0, "avail": 100.0})
c.check("an inhibitor lowers every rate", all(a["Corrosion rate [mm/y]"] < b["Corrosion rate [mm/y]"] for a, b in zip(ci, ct)), "")
cp = F.corrosion_table(m, s, {"pH": 6.5})
c.check("pH stabilisation lowers every rate", all(a["Corrosion rate [mm/y]"] < b["Corrosion rate [mm/y]"] for a, b in zip(cp, ct)), "")
et = F.emulsion_table(m, s)
c.check("emulsion table: the riser carries oil and water", any("Riser" in r["Line"] for r in et), str([r["Line"] for r in et]))
for r in et:
    c.check(f"emulsion {r['Line']}: viscosity at least the continuous-phase viscosity", r["Apparent viscosity [cP]"] >= min(r["Oil viscosity [cP]"], r["Water viscosity [cP]"]) - 1e-12, "")
lt = F.loading_table(m, s)
c.check("loading table: wells and riser listed", {r["Line"] for r in lt} >= {"W-1", "RSR-100 Riser"}, str([r["Line"] for r in lt]))
for r in lt:
    c.rel(f"loading {r['Line']}: minimum stable rate scales with the velocity ratio", r["Minimum stable rate (Turner) [MSm³/d]"],
          r["Gas rate [MSm³/d]"] / r["Margin to Turner [-]"], 1e-9)
pt = F.pigging_table(m, s)
fl = next(r for r in pt if r["Line"].startswith("FL-"))
c.rel("pig run time = L / v", fl["Run time [h]"], fl["Length [m]"] / fl["Pig speed [m/s]"] / 3600.0, 1e-9)
c.check("swept liquid does not exceed the inventory", fl["Liquid swept ahead of the pig [m³]"] <= fl["Liquid inventory [m³]"] + 1e-9, "")
pf = F.pigging_table(m, s, {"v_pig": 1.0})
f1 = next(r for r in pf if r["Line"].startswith("FL-"))
c.rel("pig at 1 m/s: run time", f1["Run time [h]"], f1["Length [m]"] / 3600.0, 1e-9)
c.check("pig at 1 m/s: swept liquid still bounded by the inventory", f1["Liquid swept ahead of the pig [m³]"] <= fl["Liquid inventory [m³]"] + 1e-9, "")
c.check("a small receiver is flagged", "receiver" in next(r for r in F.pigging_table(m, s, {"catcher_m3": 1.0}) if r["Line"].startswith("FL-"))["Note"], "")
c.check("a slow pig is flagged", "stall" in f1["Note"] or f1["Pig speed [m/s]"] >= 0.5, f1["Note"])
c.check("a pig below 0.5 m/s is flagged", "stall" in next(r for r in F.pigging_table(m, s, {"v_pig": 0.3}) if r["Line"].startswith("FL-"))["Note"], "")
c.rel("arrival rate = pig speed × area", fl["Peak liquid arrival rate [m³/h]"] * fl["Slug arrival time [h]"], fl["Liquid swept ahead of the pig [m³]"], 1e-9)
lp = F.line_pack_table(m, s)
fp_ = next(r for r in lp if r["Line"].startswith("FL-"))
c.check("line pack: draw-down below the inventory", 0.0 < fp_["Line pack available by draw-down [MSm³]"] < fp_["Gas inventory [MSm³]"], "")
c.close("line pack: pack + minimum inventory = inventory",
        fp_["Line pack available by draw-down [MSm³]"] + fp_["Gas inventory at the minimum pressure [MSm³]"], fp_["Gas inventory [MSm³]"], 1e-9)
z = F.line_pack_table(m, s, {"P_min": 1000.0})
c.close("minimum pressure above the operating pressure: no pack", next(r for r in z if r["Line"].startswith("FL-"))["Line pack available by draw-down [MSm³]"], 0.0, 1e-12)
it = F.insulation_table(m, s, {"target_h": 24.0})
c.check("insulation table lists the flowline and riser", {r["Line"] for r in it} >= {"FL-100 Flowline", "RSR-100 Riser"}, str(it))
rsr = next(r for r in it if "Riser" in r["Line"])
c.check("riser: more insulation needed for 24 h than the 12 h now", rsr["Insulation needed [mm]"] > rsr["Present insulation [mm]"], str(rsr))
c.check("insulation: a longer target needs more", next(r for r in F.insulation_table(m, s, {"target_h": 48.0}) if "Riser" in r["Line"])["Insulation needed [mm]"] > rsr["Insulation needed [mm]"], "")
c.check("insulation: a hopeless target is reported", next(r for r in F.insulation_table(m, s, {"target_h": 2000.0, "t_max": 50.0}) if "Riser" in r["Line"])["Insulation needed [mm]"] is None, "")

# ---- PVT table ------------------------------------------------------------------------------------------------------------------
some = next(sid for sid in s.streams if not s.streams[sid].empty)
st0 = s.streams[some]
rows = F.pvt_table(s.fp, st0.z, [50.0, 100.0, 150.0], [20.0, 60.0])
c.eq("PVT: one row per grid point", len(rows), 6)
c.check("PVT: gas density rises with pressure", rows[4]["Gas density [kg/m³]"] > rows[0]["Gas density [kg/m³]"], "")
c.check("PVT: mass fractions add to one", all(abs(r["Gas mass fraction [-]"] + r["Oil mass fraction [-]"] + r["Water mass fraction [-]"] - 1.0) < 1e-9 for r in rows), "")
csv = F.pvt_csv(rows)
c.eq("PVT: CSV has a header and the rows", len(csv.strip().split("\n")), 7)
c.check("PVT: Bg = Z·T·Psc / (P·Tsc)", abs(rows[0]["Gas Bg [m³/Sm³]"] - rows[0]["Gas Z [-]"] * (20.0 + 273.15) / 50.0 * 1.01325 / 288.15) < 1e-12, "")
cfg = F.settings({"fa2": {"life_y": 10.0, "bogus": 1}})
c.check("settings: overrides applied, unknown keys ignored", cfg["life_y"] == 10.0 and "bogus" not in cfg and cfg["catcher_m3"] == 150.0, str(cfg))

sys.exit(c.report())
