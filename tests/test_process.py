"""Process additions (v6.3): TEG contactor, traced phase envelope, column free-water draw, validation harness.

Run:  python tests/test_process.py
"""
import copy
import math
import os
import subprocess
import sys

import numpy as np

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
from check import Checker                                                  # noqa: E402
from procsim import dehydration as DH                                      # noqa: E402
from procsim.envelope import trace_envelope                                # noqa: E402
from procsim.examples import teg_dehydration, WET_GAS, DRY_GAS, WELL_FLUID # noqa: E402
from procsim.flowsheet import solve, port_edges, new_model, add_unit, connect   # noqa: E402
from procsim.thermo import FluidPackage                                    # noqa: E402
from procsim.unitops import WD_FEED, WD_BOTH                               # noqa: E402

c = Checker("process")


def unit_balance(m, sol, uid):
    ins = [sol.streams[s] for lst in port_edges(m, uid, "in").values() for s in lst]
    outs = [sol.streams[s] for lst in port_edges(m, uid, "out").values() for s in lst]
    nin = sum((s.F * s.z for s in ins if not s.empty), np.zeros(sol.fp.n))
    nout = sum((s.F * s.z for s in outs if not s.empty), np.zeros(sol.fp.n))
    return float(np.max(np.abs(nin - nout))) / max(float(nin.sum()), 1e-9), ins, outs


# ---- TEG contactor -----------------------------------------------------------------------------------------
c.close("water vapour pressure at 100 °C = 1.01325 bar", DH.psat_water_bar(373.15), 1.01325, 0.002)
c.close("water vapour pressure at 25 °C = 0.0317 bar", DH.psat_water_bar(298.15), 0.03169, 0.0003)
c.close("ice vapour pressure at −20 °C = 1.03 mbar", DH.psat_water_bar(253.15) * 1000.0, 1.032, 0.02)
c.close("Kremser: A = 1 → N/(N+1)", DH.kremser_fraction(1.0, 3), 0.75, 1e-12)
c.close("Kremser formula", DH.kremser_fraction(2.0, 2), (8 - 2) / (8 - 1), 1e-12)
c.close("99.5 wt% TEG ≈ 4 mol% water", DH.x_water(99.5), 0.0402, 0.0005)
m = teg_dehydration()
s = solve(m)
t = next(u for u, x in m["units"].items() if x["type"] == "teg_contactor")
r = s.results[t]
c.check("TEG example solves", s.status[t] in ("ok", "warning"), s.errors.get(t))
err, ins, outs = unit_balance(m, s, t)
c.close("TEG contactor: component balance (glycol loop closed)", err, 0.0, 1e-9)
c.check("dried gas water content below the inlet", r["Water out [mg/Sm³]"] < 0.1 * r["Water in [mg/Sm³]"], "")
c.check("outlet water content above the equilibrium over lean TEG", r["Water out [mg/Sm³]"] >= r["Equilibrium water over lean TEG [mg/Sm³]"], "")
c.check("dew point of the dried gas below the −10 °C spec", r["Water dew point of dried gas [°C]"] < -10.0,
        str(r["Water dew point of dried gas [°C]"]))
res = {}
for wt in (98.5, 99.5, 99.9):
    m2 = copy.deepcopy(m)
    m2["units"][t]["params"]["teg_wt"] = wt
    res[wt] = solve(m2).results[t]["Equilibrium dew point (infinite stages) [°C]"]
c.check("purer TEG reaches a lower equilibrium dew point", res[98.5] > res[99.5] > res[99.9], str(res))
c.close("GPSA chart, 99.5 wt% at 30 °C: equilibrium dew point about −16 °C", res[99.5], -16.0, 4.0)
m3 = copy.deepcopy(m)
m3["units"][t]["params"].update({"teg_wt": 98.0, "dew_spec": -30.0})
c.check("a dew point specification that is missed gives a warning", "misses" in (solve(m3).results[t].get("Warning") or ""), "")
c.check("reboiler duty charged as heating", r["Reboiler duty [kW]"] > 0 and
        any(e.name.endswith("reboiler") and e.duty_kW > 0 for e in s.energy), "")

# ---- traced envelope -------------------------------------------------------------------------------------------
keys = [k for k, v in DRY_GAS.items() if v > 0 and k != "H2O"]
fp = FluidPackage.from_keys(keys)
z = np.array([DRY_GAS[k] for k in keys])
z = z / z.sum()
e = trace_envelope(fp, z, 200.0)
cbT, cbP = e["cricondenbar"]
two = lambda T, P: sum(1 for ph in fp.pt_flash(z, T, P).phases if ph.kind != "W") > 1   # noqa: E731
c.check("cricondenbar: two phases just below, one just above", two(cbT, cbP - 0.5) and not two(cbT, cbP + 0.5), "")
c.check("cricondentherm is the warmest dew point", e["cricondentherm"][0] >= max(T for T, _ in e["dew"]) - 1e-9, "")
ok = all(two(T + 0.3, P) != two(T - 0.3, P) for T, P in e["dew"][:5] + e["bubble"][:5])
c.check("traced points sit on the phase boundary (±0.3 K)", ok, "")
_lowdew = sorted(e["dew"][:len(e["dew"]) // 3], key=lambda p: p[1])         # the dew line below the cricondentherm
c.check("dew points are warmer than bubble points at the same pressure",
        all(Tb < float(np.interp(Pb, [p[1] for p in _lowdew], [p[0] for p in _lowdew])) for Tb, Pb in e["bubble"][-3:]
            if _lowdew[0][1] <= Pb <= _lowdew[-1][1]), "")

# ---- column free-water draw -------------------------------------------------------------------------------------
wet = {k: v for k, v in WELL_FLUID.items()}
m = new_model(list(wet))
f = add_unit(m, "feed", 0, 0, "Wet condensate", {"T_C": 40.0, "P_bar": 10.0, "flow_basis": "kmol/h", "flow": 300.0,
                                                 "composition": wet})
col = add_unit(m, "column", 200, 0, "T-100", {"n_trays": 8, "feed_stage": 1, "P_top": 9.0, "P_bot": 9.3,
                                               "condenser": "None", "reboiler": "Yes",
                                               "reb_spec": "Reboiler temperature", "reb_value": 150.0,
                                               "water_draw": WD_FEED})
for port, name in (("overhead", "Off-gas"), ("bottoms", "Condensate"), ("water", "Free water")):
    pr = add_unit(m, "product", 400, 0, name)
    connect(m, col, port, pr, "in", name + " stream")
connect(m, f, "out", col, "feed")
sc = solve(m)
fin = sc.streams[port_edges(m, col, "in")["feed"][0]]
has_water = fin.flash.phase("W") is not None
c.check("test feed carries free water", has_water, "")
c.check("column with feed water draw solves", sc.status[col] in ("ok", "warning"), sc.errors.get(col))
err, _, outs_ = unit_balance(m, sc, col)
c.close("column with water draw: component balance", err, 0.0, 1e-8)
wout = sc.streams[port_edges(m, col, "out")["water"][0]]
c.check("free water leaves through the water outlet", not wout.empty and wout.z[sc.fp.iw] > 0.95, "")
c.close("water drawn = the feed's free-water phase", wout.F, fin.F * fin.flash.phase("W").beta, 1e-9)
c.check("no free-water warning once it is decanted", "Free water forms in the bottoms" not in (sc.results[col].get("Warning") or ""), "")

# ---- validation harness -----------------------------------------------------------------------------------------
import tempfile   # noqa: E402
with tempfile.TemporaryDirectory() as d:
    p = os.path.join(d, "cases.csv")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("case,flowsheet,object,quantity,reference,tolerance\n")
        fh.write("teg,Gas dehydration: TEG contactor with a water dew-point specification,T-100 TEG contactor,"
                 "Water removed [kg/h],%.6f,0.001\n" % r["Water removed [kg/h]"])
        fh.write("teg,Gas dehydration: TEG contactor with a water dew-point specification,T-100 TEG contactor,"
                 "Contactor T [°C],-50,1\n")
    out = subprocess.run([sys.executable, os.path.join(ROOT, "tests", "validate_against.py"), p, os.path.join(d, "r.csv")],
                         capture_output=True, text=True)
    c.check("validation harness: passes a matching value and fails a wrong one",
            "1 outside tolerance" in out.stdout and out.returncode == 1 and os.path.exists(os.path.join(d, "r.csv")),
            out.stdout[-300:] + out.stderr[-300:])
sys.exit(c.report())
