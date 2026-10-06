"""Field-life checks: the EOS tank material balance against its definition, aquifer / compressibility / injection
effects, produced composition below the saturation point, water rise, well distribution, IRR, and full runs on the
SURF examples (deliverability root, plateau, pressure support, economics consistency, well count, boosting).

Run:  python tests/test_fieldlife.py      (about 5-8 minutes: the runs solve the flowsheet many times)
"""
import copy
import os
import sys
import time

import numpy as np

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
from check import Checker                                # noqa: E402
from procsim.thermo import FluidPackage, R_BAR           # noqa: E402
from procsim import fieldlife as FL                      # noqa: E402
from procsim.flowsheet import solve                      # noqa: E402
from procsim import examples as EX                       # noqa: E402

c = Checker("fieldlife")
t0 = time.time()
P0 = dict(FL.DEFAULTS)


def tank(keys, z, T_C, Pi, kind, in_place, **over):
    fp = FluidPackage.from_keys(keys)
    p = FL.resolved(dict(P0, **over), kind)
    return fp, FL.Tank(fp, np.array(z, float), T_C + 273.15, Pi, kind, in_place, p)


def residual(tk):
    v = tk._vol(tk.flash(tk.p, tk.z))
    W = (tk.We(tk.p) + tk.Winj - tk.Wp) * FL.BW
    return (tk.n * v + W - tk.HCPV_i * (1.0 - tk.ce * (tk.Pi - tk.p))) / tk.HCPV_i


# ---- gas tank: p/z (with the shifted EOS volume) proportional to the moles left -------------------------------
DRY = (["N2", "CO2", "C1", "C2", "C3"], [0.01, 0.01, 0.90, 0.05, 0.03])
fp, tk = tank(*DRY, 90.0, 300.0, FL.GAS, 20e9, cf=0.0, cw=0.0)
c.close("tank starts at the initial pressure (volume definition)", residual(tk), 0.0, 1e-12)
vi = tk.v_i
for frac in (0.2, 0.5, 0.8):
    tk.n = tk.n_i * (1.0 - frac)
    P = tk.solve_pressure()
    v = tk._vol(tk.flash(P, tk.z))
    zs_ratio = (P * v) / (tk.Pi * vi)                  # (p/z_s) / (p_i/z_si) = v_i / v ... with z_s = P v / R T
    c.close(f"gas tank: n·v(p) = HCPV after producing {frac:.0%} (residual)", residual(tk), 0.0, 1e-7)
    c.close(f"gas tank: p/z ∝ moles left ({frac:.0%} produced)", (P / (P * v / (R_BAR * tk.T))) /
            (tk.Pi / (tk.Pi * vi / (R_BAR * tk.T))), 1.0 - frac, 1e-6)
c.check("gas tank: no saturation pressure for a dry gas", tk.Psat is None, str(tk.Psat))

# rock / water compressibility and a pot aquifer support the pressure
fp, a = tank(*DRY, 90.0, 300.0, FL.GAS, 20e9, cf=0.0, cw=0.0)
fp, b = tank(*DRY, 90.0, 300.0, FL.GAS, 20e9, cf=2e-4, cw=1e-4)
fp, q = tank(*DRY, 90.0, 300.0, FL.GAS, 20e9, cf=2e-4, cw=1e-4, aquifer=20.0)
for x in (a, b, q):
    x.n = x.n_i * 0.6
    x.solve_pressure()
c.check("compressibility supports the pressure", b.p > a.p, f"{b.p:.2f} vs {a.p:.2f}")
c.check("a pot aquifer supports the pressure", q.p > b.p + 1.0, f"{q.p:.2f} vs {b.p:.2f}")
c.close("pot aquifer: material balance residual", residual(q), 0.0, 1e-7)
c.close("pot aquifer influx = W (cw + cf)(pi − p)", q.We(q.p), q.W_aq * (q.cw + q.cf) * (q.Pi - q.p), 1e-9)

# ---- oil tank: above the bubble point, depletion vs voidage replacement -------------------------------------
OIL = (["N2", "CO2", "C1", "C2", "C3", "nC4", "nC5", "nC7", "nC10"],
       [0.003, 0.01, 0.30, 0.06, 0.05, 0.04, 0.04, 0.20, 0.297])
fp, ot = tank(*OIL, 95.0, 320.0, FL.OIL, 10e6)
c.check("oil: bubble point below the initial pressure", ot.Psat is not None and 50.0 < ot.Psat < 320.0, str(ot.Psat))
two = lambda P: len([ph for ph in fp.pt_flash(ot.z, ot.T, P).phases if ph.kind != "W"]) > 1     # noqa: E731
c.check("oil: single phase just above the bubble point, two phases just below",
        (not two(ot.Psat + 0.3)) and two(ot.Psat - 0.3), "")
y, Sg = ot.produced_composition()
c.close("above the bubble point the tank produces its own composition", float(np.max(np.abs(y - ot.z))), 0.0, 1e-12)
ot.n = ot.n_i * 0.99
P_dep = ot.solve_pressure()
c.check("oil depletion above Pb: 1 % produced drops the pressure several bar", 2.0 < 320.0 - P_dep < 120.0,
        f"{320.0 - P_dep:.1f} bar")
c.close("oil depletion: residual", residual(ot), 0.0, 1e-7)
fp, oi = tank(*OIL, 95.0, 320.0, FL.OIL, 10e6)
dn = oi.n_i * 0.01
v_out = dn * oi._vol(oi.flash(oi.p, oi.z))
oi.n -= dn
oi.Winj += v_out / FL.BW                                  # voidage replaced exactly
c.close("voidage replacement keeps the pressure", oi.solve_pressure(), 320.0, 0.05)

# below the bubble point: immobile gas -> the liquid is produced; mobile -> GOR rises
fp, ob = tank(*OIL, 95.0, 320.0, FL.OIL, 10e6, Sgc=0.99)
ob.p = ob.Psat - 40.0
fr = ob.flash(ob.p, ob.z)
liq = fr.phase("L")
y, Sg = ob.produced_composition()
c.check("below Pb: gas present in the tank", Sg > 0.0, f"Sg {Sg:.3f}")
c.close("immobile gas (Sg < Sgc): the tank produces its liquid", float(np.max(np.abs(y - liq.x))), 0.0, 1e-9)
fp, om = tank(*OIL, 95.0, 320.0, FL.OIL, 10e6, Sgc=0.0)
om.p = ob.p
y2, _ = om.produced_composition()
g_mol, o_mol = om.std_per_mol(y2)
g0, o0 = om.std_per_mol(om.z)
c.check("mobile free gas raises the producing GOR", g_mol / o_mol > g0 / o0, f"{g_mol / o_mol:.1f} vs {g0 / o0:.1f}")

# gas condensate below the dew point with immobile condensate: the vapour is produced (CGR falls)
GC = (["N2", "CO2", "C1", "C2", "C3", "nC4", "nC5", "nC7", "nC10"],
      [0.005, 0.02, 0.72, 0.08, 0.05, 0.03, 0.02, 0.045, 0.03])
fp, gc = tank(*GC, 110.0, 420.0, FL.GAS, 30e9)
c.check("condensate: a dew point below the initial pressure", gc.Psat is not None and gc.Psat < 420.0, str(gc.Psat))
gc.p = gc.Psat * 0.7
fr = gc.flash(gc.p, gc.z)
y, Sg = gc.produced_composition()
c.check("condensate drops out below the dew point", fr.phase("L") is not None and Sg < 1.0, "")
c.close("immobile condensate: the tank produces its vapour", float(np.max(np.abs(y - fr.phase("V").x))), 0.0, 1e-9)
g_v, o_v = gc.std_per_mol(y)
g_i, o_i = gc.std_per_mol(gc.z)
c.check("the producing CGR falls below the dew point", o_v / g_v < o_i / g_i, "")

# ---- helpers ------------------------------------------------------------------------------------------------
c.eq("distribute 7 wells over 4 equal units", FL.distribute(7, [1, 1, 1, 1]), [2, 2, 2, 1])
c.eq("distribute keeps proportions", FL.distribute(8, [2, 1, 1]), [4, 2, 2])
c.eq("distribute fewer wells than units", sum(FL.distribute(2, [1, 1, 1])), 2)
wp = dict(FL.resolved(dict(P0), FL.OIL), _kind=FL.OIL)
c.close("water cut stays at its initial value before breakthrough", FL.water_level(0.05, 0.1, wp), 0.1, 1e-12)
c.close("water cut reaches the maximum after the rise", FL.water_level(wp["RF_bt"] + wp["RF_rise"], 0.1, wp),
        wp["w_max"] / 100.0, 1e-12)
ws = [FL.water_level(x, 0.1, wp) for x in np.linspace(0, 0.6, 30)]
c.check("water cut never falls with recovery", all(b_ >= a_ - 1e-12 for a_, b_ in zip(ws, ws[1:])), "")
cash = [-100.0, 30.0, 40.0, 50.0, 20.0]
r_ = FL.irr(cash) / 100.0
c.close("IRR zeroes the NPV", sum(x / (1 + r_) ** k for k, x in enumerate(cash)), 0.0, 1e-6)
c.check("IRR undefined without a sign change", FL.irr([10.0, 5.0]) is None, "")
print(f"  tank and helper checks {time.time() - t0:.1f} s")

# ---- full run: oil tie-back with water injection -------------------------------------------------------------
t1 = time.time()
m = EX.heated_oil_tieback()
sol = solve(m)
S = FL.setup(m, sol)
nets = {}
res = FL.run(m, sol, S=S, networks=nets)
c.eq("oil example detected as an oil reservoir", S["kind"], FL.OIL)
prod = [r for r in res["annual"] if r["Year"] >= 1]
up = S["p"]["uptime"] / 100.0
c.close("plateau years produce the plateau rate × production efficiency", prod[0]["Oil/condensate [Sm³/d]"],
        S["q_plat"] * up, 1e-6 * S["q_plat"])
Pmin_res = min(s["P_res"] for s in res["steps"])
c.check("water injection (VRR 1) holds the reservoir pressure", Pmin_res > 0.98 * S["Pi"], f"{Pmin_res:.1f} of {S['Pi']:.1f}")
wc = [r["Water cut / WGR"] for r in prod]
c.check("water cut rises after breakthrough", wc[-1] > wc[0] + 20.0, f"{wc[0]:.1f} -> {wc[-1]:.1f} %")
c.check("an event records the water breakthrough", any("breakthrough" in e for _, e in res["events"]), "")
rf = [r["Recovery factor [%]"] for r in prod]
c.check("recovery factor increases every year", all(b_ > a_ for a_, b_ in zip(rf, rf[1:])), "")
cum_oil = sum(s["oil_vol"] for s in res["steps"] if s["year"] <= prod[-1]["Year"])
c.close("recovery factor = cumulative oil / STOIIP", res["summary"]["Recovery factor [%]"], 100.0 * cum_oil / S["in_place"],
        1e-6)
c.close("NPV = sum of the discounted cash flows", res["summary"]["NPV [MUSD]"],
        sum(r["Discounted [MUSD]"] for r in res["annual"]), 1e-9)
last = prod[-1]
c.check("economic cut-off: the last year still has a positive operating cash flow",
        last["Revenue [MUSD]"] - last["OPEX [MUSD]"] > 0, "")
c.close("CAPEX in the cash flows = facilities + wells", sum(r["CAPEX [MUSD]"] for r in res["annual"]),
        res["capex"]["Facilities [MUSD]"] + res["capex"]["Wells [MUSD]"], 1e-6)
# the deliverability root: feasible at s*, infeasible 6 % above
nw = next(iter(nets.values()))
exact = [(k, nd) for k, nd in nw.nodes.items() if nd["exact"] and nd["s_star"] and 0.05 < nd["s_star"] < res["steps"][0]["s_plat"]]
c.check("the run solved deliverability below the plateau", bool(exact), "")
if exact:
    (ri, wi), nd = exact[0]
    wgrid = FL._w_grid(S)
    e_ok = nw.evaluate(FL.R_GRID[ri], nd["s_star"], wgrid[wi])
    e_hi = nw.evaluate(FL.R_GRID[ri], nd["s_star"] * 1.06, wgrid[wi])
    c.check("deliverable rate s*: the flowsheet solves and arrives above the minimum", e_ok["ok"], e_ok["why"])
    c.check("6 % above s* is not deliverable", not e_hi["ok"], e_hi["why"])
txt = " ".join(FL.strategy_text(S, res))
c.check("strategy names the drive and the recovery factor", "water injection" in txt and "Recovery factor" in txt, txt[:120])
comp = FL.compact(res, S)
import json   # noqa: E402
c.check("compact result is JSON-serialisable", bool(json.dumps(comp)), "")
print(f"  oil run {time.time() - t1:.1f} s, {res['n_solves']} solves")

# lower minimum delivery pressure -> longer plateau and more recovery
t1 = time.time()
m2 = copy.deepcopy(m)
m2["fieldlife"] = {"P_min": S["P_min"] * 0.5}
res2 = FL.run(m2, sol)
c.check("a lower minimum delivery pressure lengthens the plateau",
        res2["summary"]["Plateau length [years]"] >= res["summary"]["Plateau length [years]"],
        f"{res2['summary']['Plateau length [years]']} vs {res['summary']['Plateau length [years]']}")
c.check("a lower minimum delivery pressure recovers more",
        res2["summary"]["Recovery factor [%]"] > res["summary"]["Recovery factor [%]"] - 1e-9, "")
# depletion (no injection) loses pressure and recovers less
m3 = copy.deepcopy(m)
m3["fieldlife"] = {"VRR": 0.0, "years": 12}
res3 = FL.run(m3, sol)
c.check("without injection the reservoir pressure falls", min(s["P_res"] for s in res3["steps"]) < 0.8 * S["Pi"], "")
c.check("without injection the recovery is lower", res3["summary"]["Recovery factor [%]"] < res["summary"]["Recovery factor [%]"],
        f"{res3['summary']['Recovery factor [%]']:.1f} vs {res['summary']['Recovery factor [%]']:.1f}")
print(f"  oil variants {time.time() - t1:.1f} s")

# apply a well count: the flowsheet then delivers the plateau with N wells
m4 = FL.apply_wells(m, sol, 4)
s4 = solve(m4)
c.eq("apply_wells: 4 wells over the 2 well units", [u["params"]["n_par"] for u in m4["units"].values() if u["type"] == "well"],
     [2, 2])
oil4 = sum(s4.results[u]["Oil/condensate rate [Sm³/d]"] for u, x in m4["units"].items() if x["type"] == "well")
# (the well results flash oil together with the water at standard conditions, the plateau is hydrocarbon-only)
c.close("apply_wells: total rate = plateau rate", oil4, S["q_plat"], 0.01 * S["q_plat"])

# ---- gas: subsea field, depletion; more wells hold the plateau longer (parallel sweep) -----------------------
t1 = time.time()
mg = EX.subsea_field()
mg["fieldlife"] = {"years": 12}
sg = solve(mg)
rows, results, best = FL.sweep(mg, sg, [4, 8])
r4, r8 = results[(4, FL.B_AS_IS)], results[(8, FL.B_AS_IS)]
c.check("gas: the reservoir pressure falls with depletion",
        r4["steps"][-1]["P_res"] < r4["steps"][0]["P_res"] - 50.0, "")
c.check("gas: 8 wells hold the plateau at least as long as 4",
        r8["summary"]["Plateau length [years]"] >= r4["summary"]["Plateau length [years]"],
        f"{r8['summary']['Plateau length [years]']} vs {r4['summary']['Plateau length [years]']}")
c.check("gas: 8 wells cost more", r8["summary"]["CAPEX [MUSD]"] > r4["summary"]["CAPEX [MUSD]"], "")
c.check("gas: the plateau ends on the minimum delivery pressure", any("minimum delivery pressure" in e for _, e in r4["events"]),
        str(r4["events"]))
c.check("best case = highest NPV", results[best]["summary"]["NPV [MUSD]"] == max(r["NPV [MUSD]"] for r in rows), "")
print(f"  gas sweep {time.time() - t1:.1f} s")

# ---- boosting: never vs from the start on the late-life example ------------------------------------------------
t1 = time.time()
mb = EX.subsea_field_boosted()
mb["fieldlife"] = {"years": 8}
sb = solve(mb)
rb = FL.run(mb, sb)
rn = FL.run(mb, sb, boost_mode=FL.B_NEVER)
c.check("boosting recovers more than no boosting", rb["summary"]["Recovery factor [%]"] > rn["summary"]["Recovery factor [%]"],
        f"{rb['summary']['Recovery factor [%]']:.1f} vs {rn['summary']['Recovery factor [%]']:.1f}")
c.check("boosting draws power", max(s["boost_kW"] for s in rb["steps"]) > 100.0, "")
c.check("bypassed boosters draw no power", max(s["boost_kW"] for s in rn["steps"]) < 1e-6, "")
print(f"  boosting {time.time() - t1:.1f} s")
print(f"  total {time.time() - t0:.0f} s")
sys.exit(c.report())
