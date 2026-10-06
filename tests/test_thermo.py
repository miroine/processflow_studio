"""Thermodynamics verification: PR EOS against NIST data and self-consistency checks.

Run:  python tests/test_thermo.py
"""
import math
import os
import sys

import numpy as np
from scipy.optimize import brentq

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from check import Checker                                  # noqa: E402
from procsim.thermo import FluidPackage, R                  # noqa: E402
from procsim.components import LIBRARY, make_hypothetical   # noqa: E402

c = Checker("thermo")

# ---- vapour pressures vs NIST WebBook --------------------------------------
fp = FluidPackage.from_keys(["C3"])
c.rel("propane Psat 300 K vs NIST 9.974 bar", fp.psat_pure(0, 300.0), 9.974, 0.01)
fp = FluidPackage.from_keys(["nC4"])
c.rel("n-butane Psat 300 K vs NIST 2.583 bar", fp.psat_pure(0, 300.0), 2.583, 0.02)
fp = FluidPackage.from_keys(["C2"])
c.rel("ethane Psat 250 K vs NIST 13.01 bar", fp.psat_pure(0, 250.0), 13.01, 0.02)
fw = FluidPackage.from_keys(["H2O"])
c.close("water Tsat 1 atm within 2 K of 100 °C", fw.tsat_pure(0, 1.01325) - 273.15, 100.0, 2.0)
c.rel("CO2 Psat 280 K vs NIST 41.6 bar", FluidPackage.from_keys(["CO2"]).psat_pure(0, 280.0), 41.6, 0.02)

# ---- densities --------------------------------------------------------------
fm = FluidPackage.from_keys(["C1"])
r = fm.pt_flash(np.array([1.0]), 300.0, 100.0)
# NIST WebBook, methane 300 K / 100 bar: rho 75.175 kg/m3, Cp 3.0023 J/g/K, mu_JT 0.32606 K/bar
c.rel("methane density 300 K, 100 bar vs NIST 75.18 kg/m3", r.phases[0].rho, 75.175, 0.035)
c.rel("methane Cp 300 K, 100 bar vs NIST 48.17 J/mol/K", r.phases[0].Cp, 3.0023 * 16.043, 0.05)
h0 = r.H
Tj = fm.ph_flash(np.array([1.0]), 99.0, h0, 300.0).T
c.rel("methane Joule-Thomson coefficient vs NIST 0.326 K/bar", (300.0 - Tj) / 1.0, 0.32606, 0.08)
c.eq("methane at 300 K / 100 bar is vapour", r.phases[0].kind, "V")
r = FluidPackage.from_keys(["nC7"]).pt_flash(np.array([1.0]), 288.15, 1.01325)
c.rel("n-heptane liquid density 15 °C vs 688 kg/m3 (Peneloux)", r.phases[0].rho, 688.0, 0.03)
r = fw.pt_flash(np.array([1.0]), 288.15, 1.01325)
c.rel("water density 15 °C (calibrated shift)", r.phases[0].rho, 999.1, 0.002)
c.eq("water at 15 °C is aqueous", r.phases[0].kind, "W")

# ---- ideal-gas limit --------------------------------------------------------
fn = FluidPackage.from_keys(["N2"])
r = fn.pt_flash(np.array([1.0]), 400.0, 1e-4)
c.close("N2 Z -> 1 at vanishing pressure", r.phases[0].Z, 1.0, 1e-5)
cp_ig = float(fn.cp_ig(400.0, np.array([0]))[0])
c.rel("Cp -> ideal-gas Cp at vanishing pressure", r.phases[0].Cp, cp_ig, 1e-4)
c.rel("Cp - Cv -> R at vanishing pressure", r.phases[0].Cp - r.phases[0].Cv, R, 1e-3)

# ---- thermodynamic consistency (real fluid) ----------------------------------
keys = ["N2", "CO2", "C1", "C2", "C3", "iC4", "nC4", "iC5", "nC5", "nC6", "nC7", "nC8", "H2O"]
gas = np.array([0.01, 0.02, 0.84, 0.065, 0.035, 0.007, 0.01, 0.004, 0.003, 0.003, 0.002, 0.001, 0.0])
fg = FluidPackage.from_keys(keys)
T, P, dT = 320.0, 80.0, 0.01
h1, h2 = fg.pt_flash(gas, T - dT, P).H, fg.pt_flash(gas, T + dT, P).H
s1, s2 = fg.pt_flash(gas, T - dT, P).S, fg.pt_flash(gas, T + dT, P).S
cp = fg.pt_flash(gas, T, P).Cp
c.rel("dH/dT|P equals analytic Cp (single-phase gas, 80 bar)", (h2 - h1) / (2 * dT), cp, 2e-4)
c.rel("T dS/dT|P equals Cp", T * (s2 - s1) / (2 * dT), cp, 2e-4)
# Maxwell/Gibbs: (dH/dP)_T = V - T (dV/dT)_P
dP = 0.01
v = lambda T_, P_: fg.pt_flash(gas, T_, P_).phases[0].V   # unshifted EOS volume
dHdP = (fg.pt_flash(gas, T, P + dP).H - fg.pt_flash(gas, T, P - dP).H) / (2 * dP)   # J/mol/bar
rhs = (v(T, P) - T * (v(T + dT, P) - v(T - dT, P)) / (2 * dT)) * 1e5
c.rel("(dH/dP)_T = V - T(dV/dT)_P", dHdP, rhs, 1e-3)

# ---- flash round trips ------------------------------------------------------
wet = np.array([0.012, 0.021, 0.78, 0.071, 0.043, 0.008, 0.013, 0.005, 0.005, 0.006, 0.007, 0.005, 0.024])
for T0, P0 in [(300.0, 70.0), (260.0, 40.0), (360.0, 10.0)]:
    r = fg.pt_flash(wet, T0, P0)
    rh = fg.ph_flash(wet, P0, r.H, T0 + 25)
    rs = fg.ps_flash(wet, P0, r.S, T0 - 20)
    c.close(f"PH flash round trip T at {T0} K / {P0} bar ({r.phase_label})", rh.T, T0, 1e-4)
    c.close(f"PS flash round trip T at {T0} K / {P0} bar", rs.T, T0, 1e-4)
    c.close(f"phase fractions sum to 1 at {T0} K", sum(p.beta for p in r.phases), 1.0, 1e-10)
    comp = sum(p.beta * p.x for p in r.phases)
    c.close(f"component balance across phases at {T0} K", float(np.max(np.abs(comp - wet / wet.sum()))), 0.0, 1e-9)

# regression: the HC liquid must not vanish on cooling (a false V+W split once did this)
betas = []
for T_ in np.arange(240.0, 300.1, 5.0):
    rr = fg.pt_flash(wet, T_, 40.0)
    pl = rr.phase("L")
    betas.append(pl.beta if pl else 0.0)
c.check("HC liquid fraction falls monotonically with T at 40 bar (240-300 K)",
        all(b1 >= b2 - 1e-9 for b1, b2 in zip(betas, betas[1:])) and betas[0] > 0.1, str(np.round(betas, 4)))
H_series = [fg.pt_flash(wet, T_, 40.0).H for T_ in np.arange(240.0, 300.1, 2.0)]
c.check("enthalpy strictly increasing with T through the three-phase region",
        all(b > a for a, b in zip(H_series, H_series[1:])), "")

r = fg.pt_flash(wet, 300.0, 70.0)
c.eq("wet gas at 27 °C / 70 bar gives three phases", [p.kind for p in r.phases], ["V", "L", "W"])
# equal fugacities between all phases
idx = np.nonzero(wet > 0)[0]
fug = []
for p in r.phases:
    x = p.x[idx]
    lp, Z, _ = fg.lnphi(x / x.sum(), 300.0, 70.0, idx)
    fug.append(np.log(np.maximum(x, 1e-300)) + lp)
c.close("iso-fugacity vapour/liquid", float(np.max(np.abs(fug[0] - fug[1]))), 0.0, 1e-6)
c.close("iso-fugacity vapour/aqueous", float(np.max(np.abs(fug[0] - fug[2]))), 0.0, 1e-6)

# water content of gas: ~ 0.55 g/Sm3 at 30 degC, 70 bar (McKetta-Wehe chart order of magnitude)
r = fg.pt_flash(wet, 303.15, 70.0)
yw = r.phase("V").x[keys.index("H2O")]
gsm3 = yw * 18.015 * 1000.0 / 23.645   # g water per Sm3 of gas
c.within("water content of saturated gas 30 °C / 70 bar ~0.55 g/Sm3 (±50 %)", gsm3, 0.3, 0.85)

# stability: dry gas above cricondentherm must be single phase; the flash must not create phases
r = fg.pt_flash(gas, 350.0, 50.0)
c.eq("lean gas at 77 °C / 50 bar is single-phase vapour", [p.kind for p in r.phases], ["V"])
r = fg.pt_flash(gas, 230.0, 30.0)
c.check("lean gas at -43 °C / 30 bar is two-phase", len(r.phases) == 2, str([p.kind for p in r.phases]))

# ---- dew/bubble point flashes ------------------------------------------------
dew = fg.pvf_flash(gas, 50.0, 1.0)
c.check("dew point: VF = 1 just above, < 1 just below",
        fg.pt_flash(gas, dew.T + 0.05, 50.0).vf > 0.999999 and fg.pt_flash(gas, dew.T - 0.3, 50.0).vf < 1.0,
        f"Tdew={dew.T - 273.15:.2f} °C")
oil = np.array([0.0, 0.0, 0.25, 0.08, 0.08, 0.03, 0.05, 0.03, 0.04, 0.06, 0.12, 0.26, 0.0])
bub = fg.pvf_flash(oil, 50.0, 0.0)
c.check("bubble point: VF = 0 below, > 0 above",
        fg.pt_flash(oil, bub.T - 0.3, 50.0).vf < 1e-9 and fg.pt_flash(oil, bub.T + 0.3, 50.0).vf > 0,
        f"Tbub={bub.T - 273.15:.2f} °C")
half = fg.pvf_flash(oil, 20.0, 0.5)
c.close("P-VF flash VF = 0.5", half.vf, 0.5, 1e-5)

# ---- pure-component PH flash through the dome ----------------------------------
fw = FluidPackage.from_keys(["H2O"])
Ts = fw.tsat_pure(0, 5.0)
l = fw._pure_two_phase(np.array([1.0]), Ts, 5.0, 0.0)
v = fw._pure_two_phase(np.array([1.0]), Ts, 5.0, 1.0)
mid = fw.ph_flash(np.array([1.0]), 5.0, 0.3 * l.H + 0.7 * v.H, 400.0)
c.close("pure water PH flash inside the dome: VF", mid.vf, 0.7, 1e-6)
c.close("pure water PH flash inside the dome: T = Tsat", mid.T, Ts, 1e-6)

# ---- isentropic compression of an ideal gas vs analytic integral ----------------
fn = FluidPackage.from_keys(["N2"])
z = np.array([1.0])
T1, P1, P2 = 300.0, 0.01, 0.03
s0 = fn.pt_flash(z, T1, P1).S
T2 = fn.ps_flash(z, P2, s0, 350.0).T
idx0 = np.array([0])
T2a = brentq(lambda t: float(fn.s_ig_T(t, idx0)[0] - fn.s_ig_T(T1, idx0)[0]) - R * math.log(P2 / P1), 300, 600)
c.close("isentropic T2 (ideal N2) vs integral of Cp/T", T2, T2a, 0.01)

# ---- hypothetical component correlations -------------------------------------
h = make_hypothetical("X", "nC10-like", 174.15, 0.734)
d = LIBRARY["nC10"]
c.rel("Kesler-Lee Tc for n-decane-like cut", h.Tc, d.Tc, 0.02)
c.rel("Kesler-Lee Pc for n-decane-like cut", h.Pc, d.Pc, 0.05)
c.close("Kesler-Lee omega for n-decane-like cut", h.omega, d.omega, 0.03)
c.rel("Riazi-Daubert MW for n-decane-like cut", h.MW, d.MW, 0.05)
cp_h = h.cp[0] + h.cp[1] * 400 + h.cp[2] * 400 ** 2 + h.cp[3] * 400 ** 3
cp_d = d.cp[0] + d.cp[1] * 400 + d.cp[2] * 400 ** 2 + d.cp[3] * 400 ** 3
c.rel("hypo ideal-gas Cp at 400 K vs n-decane polynomial", cp_h, cp_d, 0.03)

# ---- transport properties / hydrate screening ------------------------------------------
from procsim.transport import phase_viscosity_cP, hydrate_T_motiee, water_viscosity_cP   # noqa: E402
r = fm.pt_flash(np.array([1.0]), 300.0, 100.0)
c.rel("LBC methane viscosity 300 K / 100 bar vs NIST 13.75 µPa·s", phase_viscosity_cP(fm, r.phases[0], 300.0), 0.013753, 0.03)
r = fm.pt_flash(np.array([1.0]), 300.0, 1.01325)
c.rel("LBC methane dilute-gas viscosity vs NIST 11.19 µPa·s", phase_viscosity_cP(fm, r.phases[0], 300.0), 0.01119, 0.03)
c.rel("water viscosity 25 °C (Vogel) vs 0.890 cP", water_viscosity_cP(298.15), 0.890, 0.01)
c.rel("water viscosity 80 °C (Vogel) vs 0.355 cP", water_viscosity_cP(353.15), 0.355, 0.03)
fh7 = FluidPackage.from_keys(["nC7"])
r = fh7.pt_flash(np.array([1.0]), 298.15, 1.01325)
c.within("LBC n-heptane liquid within untuned-LBC range of 0.387 cP", phase_viscosity_cP(fh7, r.phases[0], 298.15), 0.22, 0.55)
c.close("Motiee hydrate T, SG 0.6 at 1000 psia (Katz chart ≈ 16 °C)", hydrate_T_motiee(0.6, 1000 / 14.5038), 15.0, 3.0)
c.check("hydrate T rises with pressure", hydrate_T_motiee(0.65, 150) > hydrate_T_motiee(0.65, 50), "")
c.check("hydrate correlation refuses out-of-range input", hydrate_T_motiee(0.3, 50) is None and
        hydrate_T_motiee(0.65, 1.0) is None, "")

# ---- inhibitors ----------------------------------------------------------------------------------------------
from procsim.transport import required_inhibitor_wt, hydrate_depression, injection_rate   # noqa: E402
fmeg = FluidPackage.from_keys(["MEG"])
c.close("MEG normal boiling point vs 197.3 °C", fmeg.tsat_pure(0, 1.01325) - 273.15, 197.3, 3.0)
c.rel("MEG liquid density 15 °C", fmeg.pt_flash(np.array([1.0]), 288.15, 1.01325).phases[0].rho, 1116.0, 0.002)
fi = FluidPackage.from_keys(["H2O", "MEG", "MeOH"])
w = 30.0                                                    # wt% MEG, GPSA Hammerschmidt
x = np.array([(100 - w) / 18.015, w / 62.068, 0.0])
x = x / x.sum()
c.close("Hammerschmidt, 30 wt% MEG: 1297·30/(62.07·70) = 8.96 °C", hydrate_depression(fi, x), 1297 * 30 / (62.068 * 70), 1e-9)
x = np.array([80 / 18.015, 0.0, 20 / 32.042])
x = x / x.sum()
c.close("Nielsen-Bucklin, 20 wt% MeOH: −72·ln(x_w) = 9.46 °C", hydrate_depression(fi, x), -72 * math.log(x[0]), 1e-9)
for inh in ("MEG", "MeOH"):
    wreq = required_inhibitor_wt(inh, 12.0)
    M = 62.068 if inh == "MEG" else 32.042
    xx = np.array([(100 - wreq) / 18.015, wreq / M if inh == "MEG" else 0.0, wreq / M if inh == "MeOH" else 0.0])
    c.close(f"{inh}: required concentration inverts the depression (12 °C)", hydrate_depression(fi, xx / xx.sum()),
            12.0, 1e-9)
c.close("injection mass balance: 1000 kg/h water, 40 wt% rich from 90 wt% lean → 800 kg/h",
        injection_rate(1000.0, 40.0, 90.0), 800.0, 1e-9)

# ---- display units ---------------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "stubs"))
from ui import units as UN   # noqa: E402
UN.set_system(UN.FIELD)
c.close("100 °C = 212 °F", UN.value("°C", 100.0, "Temperature [°C]"), 212.0, 1e-9)
c.close("a 10 °C approach is 18 °F (no offset)", UN.value("°C", 10.0, "Minimum approach [°C]"), 18.0, 1e-9)
c.close("hydrate margin converts as a difference", UN.kv("Hydrate margin [°C]", 5.0)[1], 9.0, 1e-9)
c.close("1 bar = 14.5038 psi", UN.value("bar(a)", 1.0), 14.5038, 1e-9)
c.close("1 MSm³/d = 35.383 MMscf/d", UN.value("MSm³/d", 1.0), 35.383, 1e-9)
c.close("1 m³ = 6.28981 bbl; std liquid m³/h -> bbl/d", UN.value("m³/h", 1.0, "Std liq vol flow"), 6.28981 * 24, 1e-9)
c.close("power kW -> hp", UN.value("kW", 1000.0, "Power [kW]"), 1341.02, 1e-6)
c.close("heat duty kW -> MMBtu/h", UN.value("kW", 1000.0, "Duty [kW]"), 3.41214, 1e-9)
c.eq("labels are relabelled", UN.key("Outlet P [bar(a)]"), "Outlet P [psia]")
c.eq("string values keep their label", UN.kv("Phase [x]", "Vapour"), ("Phase [x]", "Vapour"))
ok = all(abs(UN.to_si(u_, UN.value(u_, 37.0, lab), lab) - 37.0) < 1e-9 for u_, lab in
         [("°C", "T"), ("°C", "approach"), ("bar(a)", ""), ("kW", "Power"), ("kW", "Duty"), ("m³/h", "Std liq"),
          ("mm", ""), ("kJ/kg", ""), ("W/m²·K", "")])
c.check("every conversion inverts exactly (display -> SI)", ok, "")
UN.set_system(UN.SI)
c.close("SI system leaves values untouched", UN.value("°C", 100.0), 100.0, 0.0)

# ---- warm starts across a disappearing phase (v5 regression) ------------------------------------------
from procsim.examples import WET_GAS                                                # noqa: E402
import time as _time                                                                # noqa: E402
fpw = FluidPackage.from_keys(list(WET_GAS))
zw = np.array([WET_GAS[k] for k in fpw.keys])
zw = zw / zw.sum()
cold = {T: fpw.pt_flash(zw, T + 273.15, 143.0) for T in (30.0, 36.8)}
c.eq("wet gas: V+L+W at 30 °C and V+W at 36.8 °C (143 bar)",
     ([p.kind for p in cold[30.0].phases], [p.kind for p in cold[36.8].phases]), (["V", "L", "W"], ["V", "W"]))
ok, worst = True, 0.0
for seedT in (30.0, 36.8):
    for T in (34.0, 36.7, 37.0, 40.0):
        ref = fpw.pt_flash(zw, T + 273.15, 143.0)
        warm = fpw.pt_flash(zw, T + 273.15, 143.0, cold[seedT].Kset if cold[seedT].Kset is not None else cold[seedT].K)
        d = abs(warm.H - ref.H) + sum(abs(a.beta - b.beta) for a, b in zip(warm.phases, ref.phases))
        ok = ok and [p.kind for p in warm.phases] == [p.kind for p in ref.phases]
        worst = max(worst, d)
c.check("warm-started flashes find the same phases as cold flashes near the dew point", ok, "")
c.close("warm-started flashes match cold flashes (H + phase fractions)", worst, 0.0, 1e-6)
c.check("a two-phase (V+W) result never carries a three-row seed with a vanished reference phase",
        cold[36.8].Kset is None or cold[36.8].Kset.shape == (1, fpw.n),
        str(None if cold[36.8].Kset is None else cold[36.8].Kset.shape))

# ---- v6 speed-ups: two-phase seeds in three-phase systems, GDEM, scalar 3-phase Rachford-Rice, Newton PH step ----
from procsim import thermo as TH                                                     # noqa: E402
c.eq("a V+W result carries a one-row (two-phase) warm-start seed", None if cold[36.8].Kset is None else cold[36.8].Kset.shape,
     (1, fpw.n))
worst = 0.0
same = True
for T in (36.9, 38.0, 45.0, 60.0):
    ref = fpw.pt_flash(zw, T + 273.15, 143.0)
    warm = fpw.pt_flash(zw, T + 273.15, 143.0, cold[36.8].Kset)
    same = same and [p.kind for p in warm.phases] == [p.kind for p in ref.phases]
    worst = max(worst, abs(warm.H - ref.H) + sum(abs(a.beta - b.beta) for a, b in zip(warm.phases, ref.phases)))
c.check("two-phase seeds reproduce the cold flash (phases)", same, "")
c.close("two-phase seeds reproduce the cold flash (H + fractions)", worst, 0.0, 1e-6)
w30 = fpw.pt_flash(zw, 30.0 + 273.15, 143.0, cold[36.8].Kset)
c.eq("a two-phase seed below the dew point still finds the third phase", [p.kind for p in w30.phases], ["V", "L", "W"])
rng = np.random.default_rng(3)
dev = 0.0
for _ in range(25):
    K = np.vstack([np.ones(fpw.n), np.exp(rng.normal(0, 2, fpw.n)), np.exp(rng.normal(-3, 3, fpw.n))])
    b_new, _ = FluidPackage._rr3(zw, K)
    TH_save = FluidPackage._rr3
    FluidPackage._rr3 = staticmethod(lambda z, K_, b=None: None)
    try:
        # the generic path (n-phase Newton) as the reference
        beta = np.full(3, 1.0 / 3.0)
        for _i in range(200):
            E = K.T @ beta
            g = 1.0 - K @ (zw / E)
            H = (K * (zw / E ** 2)) @ K.T + np.eye(3) * 1e-14
            free = (beta > 0) | (g < 0)
            if np.all(np.abs(g[free]) < 1e-13):
                break
            d = np.zeros(3)
            f = np.nonzero(free)[0]
            d[f] = np.linalg.solve(H[np.ix_(f, f)], -g[f])
            alpha, hit = 1.0, -1
            for k in range(3):
                if d[k] < 0 and beta[k] + alpha * d[k] < 0:
                    alpha, hit = beta[k] / -d[k], k
            Q0 = beta.sum() - float(zw @ np.log(E))
            for _ls in range(30):
                nb = beta + alpha * d
                if hit >= 0 and alpha == beta[hit] / -d[hit]:
                    nb[hit] = 0.0
                nb = np.maximum(nb, 0.0)
                En = K.T @ nb
                if np.all(En > 0) and nb.sum() - float(zw @ np.log(En)) <= Q0 + 1e-15:
                    break
                alpha *= 0.5
                hit = -1
            beta = nb
            if np.max(np.abs(alpha * d)) < 1e-15:
                break
    finally:
        FluidPackage._rr3 = TH_save
    dev = max(dev, float(np.max(np.abs(b_new - beta))))
c.close("scalar 3-phase Rachford-Rice = matrix Newton (25 random K sets)", dev, 0.0, 1e-9)
TH.GDEM = False
off = fpw.pt_flash(zw, 25.0 + 273.15, 120.0)
TH.GDEM = True
on = fpw.pt_flash(zw, 25.0 + 273.15, 120.0)
c.close("GDEM acceleration converges to the same split", sum(abs(a.beta - b.beta) for a, b in zip(on.phases, off.phases))
        + abs(on.H - off.H), 0.0, 1e-6)
ph = fpw.ph_flash(zw, 60.0, cold[30.0].H, 300.0)
c.close("PH flash (Newton first step) returns the target enthalpy", ph.H, cold[30.0].H, 1e-4 * max(1.0, abs(cold[30.0].H)))
t0 = _time.time()
for T in np.linspace(34.0, 44.0, 11):
    fpw.pt_flash(zw, T + 273.15, 146.0)
c.check("flashes just above the hydrocarbon dew point stay fast (re-referenced SS)", _time.time() - t0 < 2.0,
        f"{_time.time() - t0:.2f} s for 11 flashes")

# ---- EOS mixing cache (v5.3): cached a_ij(T) must not leak between compositions --------------------------------
fc = FluidPackage.from_keys(list(WET_GAS))
z1 = np.array([WET_GAS[k] for k in fc.keys]); z1 = z1 / z1.sum()
z2 = np.roll(z1, 3); z2 = z2 / z2.sum()
a1 = fc.pt_flash(z1, 300.0, 50.0)
a2 = fc.pt_flash(z2, 300.0, 50.0)                        # same T: served from the cache
b2 = FluidPackage.from_keys(list(WET_GAS)).pt_flash(z2, 300.0, 50.0)
c.close("cached mixing matrices reproduce a fresh package (H)", a2.H, b2.H, 1e-9 * max(1.0, abs(b2.H)))
c.eq("cached mixing matrices reproduce a fresh package (phases)", [p.kind for p in a2.phases], [p.kind for p in b2.phases])

# ---- v6: van der Waals-Platteeuw hydrate model ---------------------------------------------------------------
from procsim import hydrate as HY                                                    # noqa: E402
from procsim.transport import hydrate_T, VDWP, MOTIEE, hydrate_T_motiee              # noqa: E402
HYD_DATA = {"C1": ("sI", [(273.7, 27.7), (277.6, 41.4), (283.2, 71.0), (285.9, 96.8)]),
            "C2": ("sI", [(273.7, 5.3), (280.4, 11.5), (285.9, 24.6)]),
            "C3": ("sII", [(273.7, 1.83), (277.6, 3.8)]),
            "CO2": ("sI", [(273.7, 13.2), (280.0, 27.0), (282.9, 41.1)]),
            "N2": ("sII", [(273.2, 160.0)])}
worst = 0.0
for g, (struct, pts) in HYD_DATA.items():
    fpg = FluidPackage.from_keys([g, "H2O"])
    for T, P in pts:
        Tm, s_ = HY.equilibrium_T(fpg, np.array([1.0, 0.0]), P)
        worst = max(worst, abs(Tm - T))
c.close("vdW-P: pure-gas hydrate temperatures within 0.5 K of the data (CH4, C2H6, C3H8, CO2, N2)", worst, 0.0, 0.5)
fpg = FluidPackage.from_keys(["C3", "H2O"])
c.eq("vdW-P: propane forms structure II", HY.equilibrium_T(fpg, np.array([1.0, 0.0]), 2.0)[1], "sII")
fpg = FluidPackage.from_keys(["CO2", "H2O"])
c.eq("vdW-P: CO2 forms structure I", HY.equilibrium_T(fpg, np.array([1.0, 0.0]), 20.0)[1], "sI")
fpg = FluidPackage.from_keys(["C1", "C2", "C3"])
zk = np.array([0.92, 0.05, 0.03])
Tk = HY.equilibrium_T(fpg, zk, 69.0)
c.close("vdW-P: 0.6-gravity gas at 1000 psia vs the Katz chart (~16.7 °C)", Tk[0] - 273.15, 16.7, 1.0)
c.eq("vdW-P: a little propane switches natural gas to structure II", Tk[1], "sII")
Pk, sP = HY.equilibrium_P(fpg, zk, Tk[0])
c.close("vdW-P: equilibrium P at the equilibrium T returns the pressure", Pk, 69.0, 0.3)
c.check("vdW-P: hydrate T rises with pressure",
        HY.equilibrium_T(fpg, zk, 30.0)[0] < Tk[0] < HY.equilibrium_T(fpg, zk, 150.0)[0], "")
zc = np.array([0.80, 0.0, 0.0])
fpc = FluidPackage.from_keys(["C1", "CO2", "N2"])
t_c1 = HY.equilibrium_T(fpc, np.array([1.0, 0.0, 0.0]), 50.0)[0]
c.check("vdW-P: CO2 raises and N2 lowers the methane hydrate temperature",
        HY.equilibrium_T(fpc, np.array([0.8, 0.2, 0.0]), 50.0)[0] > t_c1 > HY.equilibrium_T(fpc, np.array([0.8, 0.0, 0.2]), 50.0)[0], "")
fpw.hydrate_model = VDWP
vd = hydrate_T(fpw, zw, 100.0)
c.close("dispatcher: vdW-P interpolated curve = direct solve", vd, HY.equilibrium_T(fpw, zw, 100.0)[0] - 273.15, 0.05)
fpw.hydrate_model = MOTIEE
from procsim.transport import gas_gravity_dry as _sg                                  # noqa: E402
c.close("dispatcher: Motiee by default", hydrate_T(fpw, zw, 100.0), hydrate_T_motiee(_sg(fpw, zw), 100.0), 1e-12)
c.close("vdW-P within 3 K of Motiee for the wet gas at 100 bar", vd, hydrate_T(fpw, zw, 100.0), 3.0)

sys.exit(c.report())
