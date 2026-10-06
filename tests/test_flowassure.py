"""Flow-assurance screening checks (v6.1): shut-in U, hydrate pressure and blowdown, wax appearance temperature
and deposition, DNV-RP-O501 bend erosion.

Run:  python tests/test_flowassure.py
"""
import copy
import math
import os
import sys

import numpy as np

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
from check import Checker                                   # noqa: E402
from procsim import flowassure as FA, subsea_ops as so      # noqa: E402
from procsim import examples as EX                          # noqa: E402
from procsim.flowsheet import solve                         # noqa: E402
from procsim.thermo import FluidPackage                     # noqa: E402
from procsim.components import make_hypothetical            # noqa: E402
from procsim.transport import hydrate_T                     # noqa: E402

c = Checker("flowassure")

# ---- shut-in U ---------------------------------------------------------------------------------------------
c.close("shut-in U: 1/U = 1/U_f − 1/h_f + 1/h_nc", FA.shutin_U(10.0, 500.0, 0.0), 1.0 / (0.1 - 0.002 + 0.1), 1e-12)
c.check("shut-in U is below the flowing U", FA.shutin_U(10.0, 500.0, 0.5) < 10.0, "")
c.check("an insulated line hardly changes", FA.shutin_U(1.0, 500.0, 0.0) > 0.9, f"{FA.shutin_U(1.0, 500.0, 0.0):.3f}")
m = EX.subsea_tieback()
s = solve(m)
a = so.cooldown(m, s)
m2 = copy.deepcopy(m)
m2["cooldown"] = {"U_mode": so.U_NATURAL}
b = so.cooldown(m2, s)
c.check("natural convection lengthens every no-touch time",
        all(y["No-touch time [h]"] >= x["No-touch time [h]"] for x, y in zip(a, b)), "")
c.check("natural convection lowers U", all(y["U [W/m²·K]"] < x["U [W/m²·K]"] for x, y in zip(a, b)), "")
hf = FA.forced_film(s.fp, s.streams[next(iter(s.streams))], 0.3, 3.0)
c.check("forced film coefficient in a plausible range (50-20 000 W/m²K)", 50.0 <= hf <= 20000.0, f"{hf:.0f}")

# ---- hydrate pressure, blowdown --------------------------------------------------------------------------------
fp = s.fp
st_g = next(st for st in s.streams.values() if not st.empty and st.flash.phase("V") is not None and fp.iw >= 0)
xg = st_g.flash.phase("V").x
Ph = FA.hydrate_pressure(fp, xg, 4.0)
c.close("hydrate pressure: the hydrate T there equals the temperature", hydrate_T(fp, xg, Ph), 4.0, 0.05)
Ph2 = FA.hydrate_pressure(fp, xg, 4.0, dT_inhib=8.0)
c.check("an inhibitor raises the hydrate pressure", Ph2 > Ph, f"{Ph2:.1f} vs {Ph:.1f}")
t, tau = FA.blowdown(100.0, 20.0, 1000.0, 277.15, 18.0)
c.close("blowdown time = τ ln(P0/P)", t, tau * math.log(100.0 / 20.0), 1e-12)
t2, _ = FA.blowdown(100.0, 20.0, 1000.0, 277.15, 18.0, d_mm=100.0)
c.close("a vent of twice the diameter blows down 4× faster", t / t2, 4.0, 1e-9)
c.eq("blowdown is zero when already below the target", FA.blowdown(10.0, 20.0, 1000.0, 277.0, 18.0)[0], 0.0)
dp = FA.depressurisation(m, s)
c.check("depressurisation rows for the tie-back lines and the whole system",
        len(dp) >= 2 and dp[-1]["Line"].startswith("Whole system"), str([r["Line"] for r in dp]))
c.check("the MEG-inhibited tie-back hydrate pressure is above the uninhibited one",
        dp[0]["Hydrate pressure at seabed T [bar(a)]"] > FA.hydrate_pressure(fp, xg, dp[0]["Seabed T [°C]"]), "")

# ---- wax -----------------------------------------------------------------------------------------------------
Tf, dH = FA.won_melting(282.5)
c.close("Won: eicosane (C20) melting point ~309.6 K", Tf, 309.6, 2.0)
Tf10, _ = FA.won_melting(142.3)
c.close("Won: n-decane melting point ~243.5 K", Tf10, 243.5, 8.0)
fpl = FluidPackage.from_keys(["C1", "C3", "nC7", "nC10"])
wl = FA.wat_estimate(fpl, np.array([0.3, 0.1, 0.3, 0.3]), 50.0)
c.check("light oil (up to C10): WAT far below the seabed", wl is None or wl < -30.0, str(wl))
c25 = make_hypothetical("C25", "C25 cut", 400.0, 0.88, 352.0)
fpw = FluidPackage([*FluidPackage.from_keys(["C1", "C3", "nC7", "nC10"]).comps, c25])
zw = np.array([0.30, 0.08, 0.30, 0.25, 0.07])
wat = FA.wat_estimate(fpw, zw, 50.0, paraffin_frac=0.3)
c.check("waxy oil (C25 cut): WAT between 10 and 60 °C", wat is not None and 10.0 < wat < 60.0, str(wat))
wat2 = FA.wat_estimate(fpw, zw, 50.0, paraffin_frac=0.6)
c.check("more n-paraffin raises the WAT", wat2 > wat, f"{wat2:.1f} vs {wat:.1f}")
c.close("Wilke-Chang: diffusivity of a wax molecule in oil ~1e-9 m²/s", FA.wilke_chang(330.0, 2.0, 150.0), 1e-9, 1.5e-9)
mo = EX.heated_oil_tieback()
so_ = solve(mo)
mo["waxsand"] = {"WAT": 60.0}
wt = FA.wax_table(mo, so_)
c.check("entered WAT 60 °C: wax deposits along the oil flowline", any(r["Max. deposit growth [mm/y]"] > 0 for r in wt), "")
mo["waxsand"] = {"WAT": 60.0, "wax_wt": 10.0}
wt2 = FA.wax_table(mo, so_)
c.close("deposit growth is proportional to the wax content", wt2[0]["Max. deposit growth [mm/y]"],
        2.0 * wt[0]["Max. deposit growth [mm/y]"], 1e-9)
mo["waxsand"] = {"WAT": 0.0}
c.check("WAT below the seabed: no deposit", all(r["Max. deposit growth [mm/y]"] == 0 for r in FA.wax_table(mo, so_)), "")
r0 = wt[0]["rows"]
c.check("wall colder than the fluid in a cooling line", all(x["Wall T [°C]"] <= x["Bulk T [°C]"] + 1e-9 for x in r0), "")

# ---- sand erosion (DNV-RP-O501 bend) -----------------------------------------------------------------------------
e = FA.dnv_bend_erosion(20.0, 0.25, 1.0 / 86400.0, 5.0)
alpha = math.atan(1 / math.sqrt(10.0))
F = 0.6 * (math.sin(alpha) + 7.2 * (math.sin(alpha) - math.sin(alpha) ** 2)) ** 0.6 * (1 - math.exp(-20 * alpha))
ref = 2e-9 * 20.0 ** 2.6 * F * (1 / 86400.0) * 2.5 * 2.0 * 3.15e10 / (7800.0 * math.pi * 0.25 ** 2 / 4 / math.sin(alpha))
c.close("DNV bend erosion = the formula", e, ref, 1e-15)
c.close("erosion ∝ U^2.6", FA.dnv_bend_erosion(40.0, 0.25, 1e-5) / FA.dnv_bend_erosion(20.0, 0.25, 1e-5), 2 ** 2.6, 1e-9)
c.close("erosion ∝ sand rate", FA.dnv_bend_erosion(20.0, 0.25, 2e-5) / FA.dnv_bend_erosion(20.0, 0.25, 1e-5), 2.0, 1e-9)
c.check("a tighter bend erodes faster", FA.dnv_bend_erosion(20.0, 0.25, 1e-5, 1.5) > FA.dnv_bend_erosion(20.0, 0.25, 1e-5, 5.0), "")
v01 = FA.velocity_for_erosion(0.1, 0.25, 1e-4)
c.close("velocity for 0.1 mm/y inverts the erosion rate", FA.dnv_bend_erosion(v01, 0.25, 1e-4), 0.1, 1e-9)
mg = EX.subsea_field()
sg = solve(mg)
et = FA.erosion_table(mg, sg)
c.check("erosion table covers wells, jumper, flowline and riser", len(et) >= 6, str([r["Line"] for r in et]))
mg["waxsand"] = {"sand_kg_d": 0.0}
c.check("no sand: no erosion", all(r["Erosion rate [mm/y]"] == 0 for r in FA.erosion_table(mg, sg)), "")
sys.exit(c.report())
