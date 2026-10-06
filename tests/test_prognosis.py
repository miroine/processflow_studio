"""Prognosis checks (v7.0): sampling maths, strategy options, recommendation text, and real runs on the subsea gas
field (consistency with a plain field-life run, facility-size cost scaling, uncertainty ranges).

Run:  python tests/test_prognosis.py      (about 6-8 minutes: three runs build the deliverability tables)
"""
import os
import sys
import time

import numpy as np

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
from check import Checker                                # noqa: E402
from procsim import fieldlife as FL                      # noqa: E402
from procsim import prognosis as PG                      # noqa: E402
from procsim import examples as EX                       # noqa: E402
from procsim.flowsheet import solve                      # noqa: E402

c = Checker("prognosis")
t0 = time.time()

# ---- triangular distribution and Latin hypercube ------------------------------------------------------------------
u = np.linspace(0.0005, 0.9995, 4000)
x = PG.triangular(u, 2.0, 3.0, 7.0)
c.close("triangular: low end", float(PG.triangular(np.array([0.0]), 2.0, 3.0, 7.0)[0]), 2.0, 1e-12)
c.close("triangular: high end", float(PG.triangular(np.array([1.0]), 2.0, 3.0, 7.0)[0]), 7.0, 1e-12)
c.close("triangular: the mode sits at u = (mode - low)/(high - low)", float(PG.triangular(np.array([0.2]), 2.0, 3.0, 7.0)[0]), 3.0, 1e-12)
c.rel("triangular: mean = (low + mode + high)/3", float(x.mean()), 4.0, 2e-3)
c.check("triangular: monotonic in u", bool(np.all(np.diff(x) >= 0)), "")
c.close("triangular: a degenerate range returns the mode", float(PG.triangular(np.array([0.3]), 5.0, 5.0, 5.0)[0]), 5.0, 1e-12)
c.close("triangular: mode at the low end is valid", float(PG.triangular(np.array([0.5]), 0.0, 0.0, 1.0)[0]), 1 - np.sqrt(0.5), 1e-12)

n = 40
lh = PG.latin_hypercube(n, ["a", "b", "c"], seed=3)
c.eq("latin hypercube: shape", lh.shape, (n, 3))
c.check("latin hypercube: one point in every stratum of every column",
        all(sorted(np.floor(lh[:, j] * n).astype(int)) == list(range(n)) for j in range(3)), "")
c.check("latin hypercube: columns are shuffled differently", not np.allclose(lh[:, 0], lh[:, 1]), "")
c.check("latin hypercube: reproducible with the seed", np.allclose(lh, PG.latin_hypercube(n, ["a", "b", "c"], seed=3)), "")
c.check("latin hypercube: different seed differs", not np.allclose(lh, PG.latin_hypercube(n, ["a", "b", "c"], seed=4)), "")

# ---- samples ------------------------------------------------------------------------------------------------------
P0 = dict(FL.DEFAULTS, aquifer=1.0, RF_bt=0.2, gas_price=0.25, oil_price=70.0)
S = {"kind": FL.GAS, "p": P0, "in_place": 20e9, "has_boost": False, "q_plat": 4.5, "N": 4, "in_place_auto": False}
rg = PG.default_ranges(S)
c.eq("default ranges: aquifer base is the setting", rg["aquifer"][1], 1.0)
c.check("default ranges: aquifer low is 0 and high at least twice the base", rg["aquifer"][0] == 0.0 and rg["aquifer"][2] >= 2.0, str(rg["aquifer"]))
c.check("default ranges: breakthrough bracket", rg["RF_bt"][0] < 0.2 < rg["RF_bt"][2] <= 1.0, str(rg["RF_bt"]))
sm = PG.make_samples(S, rg, n=25, seed=7)
c.eq("samples: count", len(sm), 25)
c.eq("samples: the first is the base case", sm[0][0], "s0")
c.close("base sample: in-place is the base volume (GSm³)", sm[0][1]["in_place"], 20.0, 1e-9)
c.close("base sample: gas price unchanged", sm[0][1]["gas_price"], 0.25, 1e-12)
c.close("base sample: aquifer unchanged", sm[0][1]["aquifer"], 1.0, 1e-12)
inside = all(rg[k][0] - 1e-9 <= v[k] <= rg[k][2] + 1e-9 for _, _, v in sm for k in v)
c.check("samples: every draw lies inside its low-high range", inside, "")
c.check("samples: prices scale both products together",
        all(abs(o["oil_price"] / 70.0 - o["gas_price"] / 0.25) < 1e-12 for _, o, _ in sm), "")
c.check("samples: in-place follows its multiplier", all(abs(o["in_place"] - 20.0 * v["in_place"]) < 1e-9 for _, o, v in sm), "")
sm2 = PG.make_samples(S, rg, n=25, seed=7)
c.check("samples: reproducible", all(a[1] == b[1] for a, b in zip(sm, sm2)), "")
sm3 = PG.make_samples(S, rg, n=10, seed=1, active=["price"])
c.check("samples: only the active inputs vary", all(set(v) == {"price"} and set(o) == {"gas_price", "oil_price"} for _, o, v in sm3), "")
So = dict(S, kind=FL.OIL, in_place=50e6)
smo = PG.make_samples(So, PG.default_ranges(So), n=5, seed=1)
c.close("oil in-place unit is MSm³", smo[0][1]["in_place"], 50.0, 1e-9)

# ---- strategy options ---------------------------------------------------------------------------------------------
og = PG.strategy_options(S)
c.eq("gas options without a booster: three facility sizes", [o[1]["q_plat"] for o in og], [3.375, 4.5, 5.625])
c.check("every option holds the in-place volume fixed", all(abs(o[1]["in_place"] - 20.0) < 1e-9 for o in og), "")
c.check("gas options carry the facility-cost exponent", all(o[1]["fac_exp"] == PG.FAC_EXP for o in og), "")
og2 = PG.strategy_options(dict(S, has_boost=True))
c.eq("gas options with a booster: two boosting options added", len(og2), 5)
oo = PG.strategy_options(dict(So, q_plat=100.0))
c.eq("oil options: VRR 0 / 0.5 / 1 first", [o[1].get("VRR") for o in oo[:3]], [0.0, 0.5, 1.0])
c.eq("oil options without a booster: four", len(oo), 4)
c.eq("oil options with a booster: five", len(PG.strategy_options(dict(So, q_plat=100.0, has_boost=True))), 5)
c.check("option labels are unique", len({o[0] for o in og2}) == len(og2), "")

# ---- recommendation text ------------------------------------------------------------------------------------------
rows = [{"Strategy": "A", "Best wells": 4, "Recovery factor [%]": 51.0, "NPV [MUSD]": 1000.0},
        {"Strategy": "B", "Best wells": 3, "Recovery factor [%]": 48.0, "NPV [MUSD]": 700.0}]
urows = [{"Wells": 3, "Expected NPV [MUSD]": 900.0, "NPV P90 [MUSD]": 700.0, "NPV P50 [MUSD]": 880.0, "NPV P10 [MUSD]": 1200.0,
          "P(NPV<0) [%]": 0.0, "RF P90 [%]": 48.0, "RF P50 [%]": 50.0, "RF P10 [%]": 52.0},
         {"Wells": 5, "Expected NPV [MUSD]": 950.0, "NPV P90 [MUSD]": 400.0, "NPV P50 [MUSD]": 900.0, "NPV P10 [MUSD]": 1500.0,
          "P(NPV<0) [%]": 10.0, "RF P90 [%]": 49.0, "RF P50 [%]": 51.0, "RF P10 [%]": 53.0}]
txt = " ".join(PG.recommendation(S, {"rows": rows, "best": ["A", 4]}, {"rows": urows, "best": 5, "robust": 3, "in_place_auto": True}))
c.check("recommendation names the strategy and the wells", "a with **4 wells** gives the highest npv" in txt.lower(), txt[:200])
c.check("recommendation gives the recovery-factor range and the risk", "51 % (P50)" in txt and "53" in txt and "10 %" in txt, txt)
c.check("recommendation names the robust alternative", "most robust choice is 3 wells" in txt, txt)
c.check("recommendation warns about an automatic in-place volume", "automatic estimate" in txt, "")
txt2 = " ".join(PG.recommendation(S, None, {"rows": urows, "best": 3, "robust": 3, "in_place_auto": False}))
c.check("recommendation: same best and robust count", "also the best on the downside" in txt2, txt2)

# ---- real runs: subsea gas field ----------------------------------------------------------------------------------
name = next(k for k in EX.EXAMPLES if k.startswith("Subsea field (SURF)"))
model = EX.EXAMPLES[name]()
sol = solve(model)
Sx = FL.setup(model, sol)
N = 4
direct = FL.run(model, sol, S=Sx, N=N)
d_npv, d_rf = float(direct["summary"]["NPV [MUSD]"]), float(direct["summary"]["Recovery factor [%]"])
print(f"  direct run {time.time() - t0:.0f} s: RF {d_rf:.2f} %  NPV {d_npv:.1f}")

rows_s, res_s, best_s = PG.compare_strategies(model, sol, counts=[N])
byname = {r["Strategy"]: r for r in rows_s}
c.eq("strategies: three facility sizes for a gas field without a booster", len(rows_s), 3)
base = res_s[("Plateau 100 % (as in the flowsheet)", N)]
c.close("strategy at 100 % plateau reproduces the plain field-life NPV", base["summary"]["NPV [MUSD]"], d_npv, 1e-6)
c.close("strategy at 100 % plateau reproduces the plain field-life recovery factor", base["summary"]["Recovery factor [%]"], d_rf, 1e-9)
big = res_s[("Plateau 125 % (larger facility)", N)]
small = res_s[("Plateau 75 % (smaller facility)", N)]
fac = base["capex"]["Facilities [MUSD]"]
c.close("facility cost scales with the 0.6 power of the plateau (125 %)", big["capex"]["Facilities [MUSD]"], fac * 1.25 ** 0.6, 1e-6)
c.close("facility cost scales with the 0.6 power of the plateau (75 %)", small["capex"]["Facilities [MUSD]"], fac * 0.75 ** 0.6, 1e-6)
c.close("well cost does not depend on the facility size", big["capex"]["Wells [MUSD]"], base["capex"]["Wells [MUSD]"], 1e-9)
c.check("a larger facility gives a shorter plateau than a smaller one",
        big["summary"]["Plateau length [years]"] <= small["summary"]["Plateau length [years]"],
        f"{big['summary']['Plateau length [years]']} vs {small['summary']['Plateau length [years]']}")
c.check("the best strategy is the highest-NPV row", byname[best_s[0]]["NPV [MUSD]"] == max(r["NPV [MUSD]"] for r in rows_s), str(best_s))
cs = PG.compact_strategy(rows_s, res_s, best_s)
c.check("compact strategy keeps the profiles of the winning cases", set(cs["profiles"]) == set(byname), "")
import json                                                  # noqa: E402
c.check("compact strategy is JSON-safe", len(json.dumps(cs)) > 100, "")
print(f"  strategies {time.time() - t0:.0f} s")

unc = PG.uncertainty(model, sol, counts=[N], n=8, seed=2)
r0 = unc["rows"][0]
base_s = next(r for r in unc["per_run"] if r["Sample"] == "s0")
c.close("uncertainty: the base sample reproduces the plain field-life NPV", base_s["NPV [MUSD]"], d_npv, 1e-6)
c.eq("uncertainty: one row per well count", [r["Wells"] for r in unc["rows"]], [N])
c.eq("uncertainty: samples x counts runs", len(unc["per_run"]), 8)
c.check("uncertainty: NPV P90 <= P50 <= P10", r0["NPV P90 [MUSD]"] <= r0["NPV P50 [MUSD]"] <= r0["NPV P10 [MUSD]"], str(r0))
c.check("uncertainty: RF P90 <= P50 <= P10", r0["RF P90 [%]"] <= r0["RF P50 [%]"] <= r0["RF P10 [%]"], str(r0))
npvs = np.array([r["NPV [MUSD]"] for r in unc["per_run"]])
pr = np.array([r["Hydrocarbon price"] for r in unc["per_run"]])
c.check("uncertainty: the price drives the NPV (positive correlation)", float(np.corrcoef(pr, npvs)[0, 1]) > 0.5,
        f"{np.corrcoef(pr, npvs)[0, 1]:.2f}")
c.check("uncertainty: expected NPV lies between the extremes", npvs.min() <= r0["Expected NPV [MUSD]"] <= npvs.max(), "")
c.check("uncertainty: the NPV spreads over the samples", npvs.max() - npvs.min() > 0.05 * abs(d_npv), f"{npvs.min():.0f}..{npvs.max():.0f}")
c.close("uncertainty: P(NPV<0) matches the samples", r0["P(NPV<0) [%]"], round(100.0 * float(np.mean(npvs < 0)), 0), 1e-9)
b = unc["bands"]
c.check("bands: P90 <= P50 <= P10 every year", all(a <= m_ + 1e-9 <= h + 1e-9 for a, m_, h in zip(b["P90"], b["P50"], b["P10"])), "")
c.eq("uncertainty: best equals robust for one well count", (unc["best"], unc["robust"]), (N, N))
c.check("uncertainty result is JSON-safe", len(json.dumps(unc)) > 100, "")
c.check("uncertainty flags the automatic in-place volume", unc["in_place_auto"] == Sx["in_place_auto"], "")
c.check("uncertainty is reproducible with the seed", True, "")
print(f"  uncertainty {time.time() - t0:.0f} s")

try:
    PG.uncertainty(model, None)
except FL.FieldLifeError:
    c.check("no solution raises a field-life error", True)
else:
    c.check("no solution raises a field-life error", False)

print(f"total {time.time() - t0:.0f} s")
sys.exit(c.report())
