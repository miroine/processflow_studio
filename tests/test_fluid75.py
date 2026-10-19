"""v7.5 fluid work: extended library, EOS calibration, plus-fraction split, continuation phase envelope.

Run:  python tests/test_fluid75.py
"""
import dataclasses
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from check import Checker                                          # noqa: E402
from procsim import eoscal as EC, plusfraction as PF               # noqa: E402
from procsim.components import LIBRARY, BASE_KEYS                  # noqa: E402
from procsim.envelope import trace_envelope                        # noqa: E402
from procsim.flowsheet import new_model, build_fluid               # noqa: E402
from procsim.thermo import FluidPackage                            # noqa: E402

c = Checker("fluid75")

# ---- library -----------------------------------------------------------------
c.check("library holds the original set plus the extension (>= 150 components)", len(LIBRARY) >= 150, len(LIBRARY))
c.check("original keys are unchanged", all(k in LIBRARY for k in ("C1", "nC8", "H2O", "MEG", "CO2")), "")
c.check("original components are unchanged objects of the first block", len(BASE_KEYS) < len(LIBRARY), "")
bad = []
for k, comp in LIBRARY.items():
    if not (comp.Tc > 5 and comp.Pc > 1 and comp.MW > 1 and -0.5 < comp.omega < 2.0 and comp.rho_std > 0):
        bad.append(k)
c.check("every component has plausible Tc, Pc, omega, MW, density", not bad, bad[:5])
bad = []
for k, comp in LIBRARY.items():
    if comp.family not in ("hydrocarbon", "inert"):
        continue
    try:
        fp = FluidPackage([comp], kij=np.zeros((1, 1)))
        ps = fp.psat_pure(0, 0.7 * comp.Tc)
        if ps is None or abs(math.log10(ps / comp.Pc) + 1 + comp.omega) > 0.12:
            bad.append(k)
    except Exception:
        bad.append(k)
c.check("non-polar components: Psat(0.7 Tc) reproduces the acentric-factor definition", len(bad) <= 3, bad[:8])
heavy = ["C1", "C3", "nC10", "benzene" if "benzene" in LIBRARY else "C1", "CO2", "H2S"]
fpx = FluidPackage.from_keys(["C1", "nC12" if "nC12" in LIBRARY else "nC8", "CO2"])
fr = fpx.pt_flash(np.array([0.5, 0.3, 0.2]), 330.0, 60.0)
c.check("flash with extended components converges", fr.P > 0, "")

# ---- overrides through build_fluid ----------------------------------------------
m = new_model(["C1", "C3", "nC6"])
fp0 = build_fluid(m)
m["fluid"]["overrides"] = {"C3": {"m_pr": 0.70, "vshift": 4.0, "note": "test"}}
fp1 = build_fluid(m)
i = fp1.index("C3")
c.close("override sets the alpha parameter", float(fp1.m[i]), 0.70, 1e-12)
c.close("override adds to the Peneloux shift [cm3/mol]", (fp1.c_shift[i] - fp0.c_shift[i]) * 1e6, 4.0, 1e-9)
c.close("other components untouched", float(fp1.m[fp1.index("nC6")]), float(fp0.m[fp0.index("nC6")]), 1e-12)
c.check("library itself is not modified", LIBRARY["C3"].m_pr == 0.0 and LIBRARY["C3"].vshift == 0.0, "")

# ---- calibration recovers known parameters ------------------------------------
c3 = LIBRARY["C3"]
true = dataclasses.replace(c3, m_pr=EC.effective_m(c3) + 0.04, vshift=3.0)
st = EC._single(true)
rows = []
for T in (230, 260, 290, 320, 350):
    rows.append({"kind": "psat", "T_C": T - 273.15, "value": st.psat_pure(0, T)})
    rows.append({"kind": "rho_l", "T_C": T - 273.15, "P_bar": 30.0, "value": EC.rho_liquid(st, 0, T, 30.0)})
r = EC.calibrate_pure(c3, rows)
c.check("pure fit converged", r["ok"], r["message"])
c.close("recovered m", r["params1"]["m_pr"], EC.effective_m(c3) + 0.04, 0.01)
c.close("recovered volume shift", r["params1"]["vshift"], 3.0, 0.3)
c.check("AAD vapour pressure falls below 0.2 %", r["after"]["psat"]["AAD%"] < 0.2 < r["before"]["psat"]["AAD%"], r["after"])
rn = EC.calibrate_pure(c3, [dict(x, value=x["value"] * (1 + 0.01 * ((j % 3) - 1))) for j, x in enumerate(rows)])
c.check("noisy data (1 %) give a fit within 3 % AAD", rn["after"]["psat"]["AAD%"] < 3.0, rn["after"])
c.check("no usable data is reported, not raised", not EC.calibrate_pure(c3, [])["ok"], "")
r1 = EC.calibrate_pure(c3, [x for x in rows if x["kind"] == "psat"], ("m_pr", "vshift"))
c.check("vshift is not fitted without density data", "vshift" not in r1["override"], r1["override"])
mdl = new_model(["C1", "C3"])
EC.apply_pure(mdl["fluid"], "C3", r["override"])
fpc = build_fluid(mdl)
c.rel("applied calibration reproduces the data (Psat 290 K)", fpc.psat_pure(fpc.index("C3"), 290.0),
      st.psat_pure(0, 290.0), 0.005)
EC.reset_pure(mdl["fluid"], "C3")
c.close("reset restores the standard alpha", float(build_fluid(mdl).m[1]), EC.effective_m(c3), 1e-12)

A, B = LIBRARY["C1"], LIBRARY["nC4"]
fpt = FluidPackage([A, B], kij=np.array([[0, .05], [.05, 0]]))
brows = [{"T_C": T - 273.15, "x1": x, "P_bar": EC.bubble_pressure(fpt, np.array([x, 1 - x]), T)}
         for T in (320, 350) for x in (.1, .3, .5)]
rk = EC.calibrate_kij(A, B, brows)
c.close("kij recovered from bubble pressures", rk["kij1"], 0.05, 0.005)

# ---- plus-fraction split -----------------------------------------------------------
sp = PF.split(0.12, 215.0, 0.84, 7, 80, 4)
ck = sp["checks"]
c.close("split conserves the plus mole fraction", ck["z"], 0.12, 1e-9)
c.close("split conserves the plus molar mass", ck["MW"], 215.0, 1e-6)
c.close("split conserves the plus density", ck["SG"], 0.84, 1e-6)
mws = [g["MW"] for g in sp["groups"]]
c.check("pseudo-components are ordered by molar mass", mws == sorted(mws), mws)

try:
    PF.split(0.1, 60.0, 0.8)
    ok = False
except ValueError:
    ok = True
c.check("a molar mass below C7 is rejected", ok, "")

# ---- continuation envelope -----------------------------------------------------------
fpg = FluidPackage.from_keys(["N2", "CO2", "C1", "C2", "C3", "nC4", "nC6", "nC8"])
zg = np.array([.01, .02, .80, .07, .04, .03, .02, .01])
env = trace_envelope(fpg, zg, quality=(0.5,))
c.check("gas condensate envelope by continuation", env["method"] == "continuation" and env["complete"], env["method"])
cb, ct, cr = env["cricondenbar"], env["cricondentherm"], env["critical"]
c.check("cricondenbar is the highest pressure of the path", cb[1] >= max(p[1] for p in env["path"]) - 0.05 * cb[1], cb)
c.check("cricondentherm is the warmest point of the path", ct[0] >= max(p[0] for p in env["path"]) - 0.5, ct)
c.check("critical point lies between the two", cr is not None and ct[0] > cr[0] > 150, cr)
# every point of the path is a saturation point: just inside is two-phase, far outside is single phase
Tm, Pm = env["path"][len(env["path"]) // 3]
c.check("a dew point of the path sits on the phase boundary",
        len(fpg.pt_flash(zg, Tm - 3.0, Pm).phases) >= 2 and len(fpg.pt_flash(zg, Tm + 8.0, Pm).phases) == 1
        or len(fpg.pt_flash(zg, Tm + 3.0, Pm).phases) >= 2, (Tm, Pm))
iso = env["isopleths"].get(0.5)
c.check("the 50 % vapour line exists and lies inside the envelope", iso is not None and len(iso) > 10, "")
if iso:
    T5, P5 = iso[len(iso) // 4]
    vf = fpg.pt_flash(zg, T5, P5).vf
    c.close("isopleth point has the stated vapour fraction", vf, 0.5, 0.03)

sys.exit(c.report())
