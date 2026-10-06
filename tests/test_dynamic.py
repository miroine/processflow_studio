"""Dynamic-simulation checks (v7.3): UV flash, steady hold at t = 0, mole closure, blowdown against an analytic
isentropic reference, line-pack balance, level / pressure control, compressor trip and anti-surge, events, errors.

Run:  python tests/test_dynamic.py      (about 1 minute)
"""
import math
import os
import sys

import numpy as np

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
from check import Checker                                                    # noqa: E402
from procsim import dynamics as DY                                           # noqa: E402
from procsim import dyn_thermo as DT                                         # noqa: E402
from procsim import examples as EX                                           # noqa: E402
from procsim.flowsheet import new_model, add_unit, connect, solve            # noqa: E402

c = Checker("dynamic")


def ex(prefix):
    nm = next(n for n in EX.EXAMPLES if n.startswith(prefix))
    return EX.EXAMPLES[nm]()


def run(m, cfg=None, **kw):
    sol = solve(m)
    d = DY.build(m, sol, cfg)
    return d, d.run(**kw)


# ---- 1. UV flash round trip ---------------------------------------------------------------------------------------
m = ex("Dynamic: HP separator")
sol = solve(m)
d = DY.build(m, sol)
n = next(x for x in d.nodes if x.kind == "vessel")
fr, T, P, J = DT.uv_flash(sol.fp, n.N, n.V, n.U, n.T, n.P)
c.close("UV flash reproduces the vessel pressure", P, n.P_ss, 1e-3)
c.close("UV flash reproduces the vessel temperature", T, n.T_ss, 1e-3)
c.check("level geometry: 50 % level is half of the volume of a horizontal vessel",
        abs(DT.volume_fraction_at_level("Horizontal", 50.0) - 0.5) < 1e-6, "")
c.check("level geometry round trip", abs(DT.level_pct("Horizontal", 0.3 * 10.0, 10.0) - DT.level_pct("Horizontal", 3.0, 10.0)) < 1e-12 and
        abs(DT.level_pct("Vertical", 4.0, 10.0) - 40.0) < 1e-6, "")

# ---- 2. steady hold: no events, nothing moves ---------------------------------------------------------------------------
m0 = ex("Dynamic: HP separator")
d, r = run(m0, {"events": []}, t_end=300, dt_out=30)
S = r["series"]
c.eq("status ok", r["status"], "ok")
for k in ("V-100 HP separator | Pressure [bar(a)]", "V-100 HP separator | Level [%]", "V-100 HP separator | Temperature [°C]"):
    col = np.array(S[k], float)
    c.check(f"steady state holds: {k}", np.ptp(col) < 0.02 * max(abs(col[0]), 1.0), f"{col.min()} .. {col.max()}")
c.check("mole balance closes (steady)", abs(r["balance_error_kmol"]) / r["inventory0"] < 1e-8, str(r["balance_error_kmol"]))

# ---- 3. separator: feed step, controllers respond ----------------------------------------------------------------------------
d, r = run(m0, None, t_end=1800, dt_out=10)
S = r["series"]
P_ = np.array(S["V-100 HP separator | Pressure [bar(a)]"], float)
L_ = np.array(S["V-100 HP separator | Level [%]"], float)
c.check("feed step: level rises, then the level controller brings it back", L_.max() > 50.5 and abs(L_[-1] - 50.0) < 1.0,
        f"max {L_.max():.2f}, end {L_[-1]:.2f}")
c.check("pressure controller keeps the pressure within 5 % of the set-point", np.abs(P_ - 40.0).max() < 2.0 and abs(P_[-1] - 40.0) < 0.3,
        f"{np.abs(P_ - 40.0).max():.2f}")
c.check("feed temperature drop cools the vessel", S["V-100 HP separator | Temperature [°C]"][-1] < 50.0, "")
c.check("mole balance closes over a disturbance", abs(r["balance_error_kmol"]) / r["inventory0"] < 1e-7, str(r["balance_error_kmol"]))
c.check("controllers were created (PC and LC)", sorted(x.name[:2] for x in d.controllers) == ["LC", "PC"], str([x.name for x in d.controllers]))
d2, r2 = run(m0, {"controllers": {"LC-LCV-100 liquid outlet": {"enabled": False}}, "events": m0["dynamics"]["events"]}, t_end=900, dt_out=10)
L2 = np.array(r2["series"]["V-100 HP separator | Level [%]"], float)
c.check("without the level controller the level keeps rising after the feed step", L2[-1] > L_[np.searchsorted(r["t"], 900)] + 3.0,
        f"{L2[-1]:.1f}")

# ---- 4. blowdown against an analytic isentropic reference ----------------------------------------------------------------------
mb = new_model()
f_ = add_unit(mb, "feed", 0, 0, "Gas", {"T_C": 30.0, "P_bar": 20.0, "flow_basis": "kmol/h", "flow": 100.0, "composition": {"C1": 1.0}})
s_ = add_unit(mb, "scrubber", 100, 0, "V", {})
v_ = add_unit(mb, "valve", 200, 0, "XV", {"P_out": 18.0})
p_ = add_unit(mb, "product", 300, 0, "Out")
connect(mb, f_, "out", s_, "feed"); connect(mb, s_, "vapour", v_, "in"); connect(mb, v_, "out", p_, "in")
cfg = {"nodes": {"V": {"volume": 20.0, "orient": "Vertical", "wall": False}},
       "relief": [{"name": "BDV", "node": "V", "type": "BDV", "D_mm": 25.0}],
       "events": [{"t": 5, "kind": "feed_flow", "target": "Gas", "value": 0.0}, {"t": 5, "kind": "valve_op", "target": "XV", "value": 0.0},
                  {"t": 10, "kind": "relief_open", "target": "BDV"}]}
d, r = run(mb, cfg, t_end=120, dt_out=10)
S = r["series"]
P = np.array(S["V | Pressure [bar(a)]"], float)
T = np.array(S["V | Temperature [°C]"], float) + 273.15
R_, MW, k, Z = 8.314, 16.043e-3, 1.31, 0.95
A, Cd, V = math.pi / 4 * 0.025 ** 2, 0.85, 20.0
Cc = (2 / (k + 1)) ** ((k + 1) / (2 * (k - 1)))
P0, T0 = P[2] * 1e5, T[2]
a = Cd * A * Cc * math.sqrt(k * MW / (Z * R_ * T0)) * P0 / (P0 * MW / (Z * R_ * T0) * V)
pp = (k + 1) / 2
t = np.arange(0, 110, 10.0)
Pa = P[2] * (1 + (pp - 1) * a * t) ** (-k / (pp - 1))
c.check("blowdown pressure follows the analytic isentropic choked-orifice decay within 4 %", np.abs(P[2:] / Pa - 1).max() < 0.04,
        f"{np.abs(P[2:] / Pa - 1).max():.3f}")
Tis = T[2] * (P / P[2]) ** ((k - 1) / k)
c.check("blowdown temperature follows the isentropic expansion within 6 K", np.abs(T[2:] - Tis[2:]).max() < 6.0, f"{np.abs(T[2:] - Tis[2:]).max():.1f}")
c.check("mole balance closes in a blowdown", abs(r["balance_error_kmol"]) / r["inventory0"] < 1e-7, "")
c.check("the event log records the three events", len(r["events"]) == 3, str(r["events"]))

# ---- 5. pipeline line-pack ---------------------------------------------------------------------------------------------------------------------
mp = ex("Dynamic: pipeline")
sol = solve(mp)
d = DY.build(mp, sol)
c.eq("pipe becomes 10 cells", len(d.pipes["PL-1 Export line"]), 10)
d, r = run(mp, {"events": [{"t": 60, "kind": "feed_flow", "target": "Gas in", "value": 3.6, "ramp": 5}], "dt_max": 20.0}, t_end=2400, dt_out=60)
S = r["series"]
lp = np.array(S["PL-1 Export line | Line pack [kmol]"], float)
pi = np.array(S["PL-1 Export line | Inlet pressure [bar(a)]"], float)
c.check("a rate step builds up line pack and inlet pressure", lp[-1] > lp[0] * 1.02 and pi[-1] > pi[0] + 1.0, f"{lp[0]:.0f} -> {lp[-1]:.0f}")
c.check("line-pack mass balance closes", abs(r["balance_error_kmol"]) / r["inventory0"] < 1e-7, str(r["balance_error_kmol"]))
c.check("outlet lags inlet", S["PL-1 Export line | Outlet pressure [bar(a)]"][3] < pi[3], "")

# ---- 6. compressor: trip, coast-down, anti-surge ---------------------------------------------------------------------------
mc = ex("Dynamic: compressor")
d, r = run(mc, None, t_end=300, dt_out=5)
S = r["series"]
sp_ = np.array(S["K-100 | Speed [rpm]"], float)
c.close("compressor at design speed before the trip", sp_[5], 10000.0, 1e-3)
c.check("compressor coasts down after the trip", sp_[-1] < 0.1 * sp_[0] and np.all(np.diff(sp_[13:]) <= 1e-9), f"{sp_[-1]:.0f}")
c.check("anti-surge valve opens on the trip", max(S["K-100 | Anti-surge valve [%]"]) > 99.0, "")
c.check("the machine flow stops, the pressures approach one another", S["K-100 | Pressure ratio"][-1] < 1.15, f"{S['K-100 | Pressure ratio'][-1]:.2f}")
c.check("mole balance closes through a trip", abs(r["balance_error_kmol"]) / r["inventory0"] < 1e-7, "")
mc2 = ex("Dynamic: compressor")
ev = [{"t": 60, "kind": "feed_flow", "target": "Gas in", "value": 2500.0, "ramp": 20}]
res = {}
for asc in (False, True):
    d, r = run(mc2, {"compressors": {"K-100": {"antisurge": asc}}, "events": ev}, t_end=400, dt_out=5)
    res[asc] = np.array(r["series"]["K-100 | Surge margin [%]"], float)
    c.eq(f"feed turn-down run completes (anti-surge {asc})", r["status"], "ok")
c.check("without anti-surge a turn-down erodes the surge margin", res[False].min() < 20.0, f"{res[False].min():.1f}")
c.check("with the anti-surge valve the margin is protected", res[True].min() > res[False].min() + 5.0 and res[True][-1] > 10.0, f"{res[True].min():.1f}")

# ---- 7. events, validation, errors -------------------------------------------------------------------------------
d = DY.build(mc2, solve(mc2))
c.eq("event targets are listed", DY.targets(d)["compressor"], ["K-100"])
c.check("a bad target is reported", len(DY.check_events(d, [{"t": 1, "kind": "comp_trip", "target": "nonsense"}])) == 1, "")
c.check("a missing value is reported", len(DY.check_events(d, [{"t": 1, "kind": "feed_flow", "target": "Gas in"}])) == 1, "")
c.eq("a valid event table has no problem", DY.check_events(d, mc2["dynamics"]["events"]), [])
mu = new_model()
f_ = add_unit(mu, "feed", 0, 0, "F", {"T_C": 30.0, "P_bar": 20.0, "flow_basis": "kmol/h", "flow": 100.0, "composition": {"C1": 0.9, "C3": 0.1}})
h_ = add_unit(mu, "heater", 100, 0, "E", {"T_out": 80.0, "dP": 0.2})
p_ = add_unit(mu, "product", 300, 0, "Out")
connect(mu, f_, "out", h_, "in"); connect(mu, h_, "out", p_, "in")
c.check("a flowsheet with no holdup cannot be run dynamically but does not crash", isinstance(DY.readiness(mu, solve(mu)), (str, type(None))), "")
mm = ex("Dynamic: HP separator")
solu = solve(mm)
try:
    DY.build(mm, solu, {"nodes": {"V-100 HP separator": {"volume": -1.0}}})
    ok_ = False
except DY.DynError:
    ok_ = True
c.check("a negative volume is rejected", ok_, "")

# ---- 8. the examples all build and run ---------------------------------------------------------------------------------------------------
for nm, fn in EX.EXAMPLES.items():
    if not nm.startswith("Dynamic"):
        continue
    mm = fn()
    d, r = run(mm, None)
    s_ = DY.summary(r)
    c.check(f"example runs to the end: {nm[:48]}", r["status"] == "ok" and s_["balance_rel"] < 1e-7, f"{r['status']} {r['message']} {s_['balance_rel']:.1e}")

c.report()
