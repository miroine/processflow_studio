"""v7.8 parameter estimation (calibration to plant data) and optimisation.

Run:  python tests/test_tuning.py
"""
import copy
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from check import Checker                                                   # noqa: E402
from procsim import tuning as T                                             # noqa: E402
from procsim.examples import WET_GAS                                        # noqa: E402
from procsim.flowsheet import new_model, add_unit, connect, solve           # noqa: E402

c = Checker("tuning")


def line(eff=75.0, dp=0.0, flow=2.0, Tin=30.0):
    m = new_model()
    f = add_unit(m, "feed", 0, 0, "Gas", {"T_C": Tin, "P_bar": 20.0, "flow_basis": "MSm³/d", "flow": flow, "composition": dict(WET_GAS)})
    k = add_unit(m, "compressor", 100, 0, "K-1", {"spec": "Outlet pressure", "P_out": 60.0, "eff": eff})
    e = add_unit(m, "cooler", 200, 0, "E-1", {"spec": "Outlet temperature", "T_out": 40.0, "dP": dp})
    o = add_unit(m, "product", 300, 0, "Out")
    connect(m, f, "out", k, "in", "1")
    connect(m, k, "out", e, "in", "2")
    connect(m, e, "out", o, "in", "3")
    return m


truth = line(eff=71.0, dp=0.6)
rng = np.random.default_rng(3)


def plant(flow, Tin):
    m = copy.deepcopy(truth)
    T.set_param(m, "Gas", "flow", flow)
    T.set_param(m, "Gas", "T_C", Tin)
    s = solve(m)
    k = s.results[T.unit_id(m, "K-1")]
    e = s.results[T.unit_id(m, "E-1")]
    return {"Toff": k["Outlet T [°C]"] * (1 + rng.normal(0, 0.002)), "W": k["Power [kW]"] * (1 + rng.normal(0, 0.005)),
            "Pout": e["Outlet P [bar(a)]"] + rng.normal(0, 0.05)}


cases = []
for i, (fl, ti) in enumerate([(1.5, 25.0), (2.0, 30.0), (2.5, 35.0), (1.8, 28.0)]):
    d = plant(fl, ti)
    cases.append({"name": f"day {i + 1}", "validate": i == 3,
                  "set": [{"unit": "Gas", "key": "flow", "value": fl}, {"unit": "Gas", "key": "T_C", "value": ti}],
                  "measure": [{"kind": "unit", "obj": "K-1", "q": "Outlet T [°C]", "value": d["Toff"], "sigma": 0.5},
                              {"kind": "unit", "obj": "K-1", "q": "Power [kW]", "value": d["W"], "sigma": 0.01 * d["W"]},
                              {"kind": "stream", "obj": "Out", "q": "Pressure [bar(a)]", "value": d["Pout"], "sigma": 0.1}]})

model = line(eff=80.0, dp=0.0)
res = T.calibrate(model, {"params": [{"unit": "K-1", "key": "eff", "lo": 50.0, "hi": 90.0},
                                     {"unit": "E-1", "key": "dP", "lo": 0.0, "hi": 3.0}], "cases": cases, "max_nfev": 25})
p = {x["key"]: x for x in res["params"]}
c.check("calibration converged", res["success"], res["message"])
c.close("efficiency recovered (truth 71 %)", p["eff"]["after"], 71.0, 1.5)
c.close("cooler pressure drop recovered (truth 0.6 bar)", p["dP"]["after"], 0.6, 0.15)
c.check("fit error fell sharply", res["rms_z_fit_after"] < 0.3 * res["rms_z_fit_before"], (res["rms_z_fit_before"], res["rms_z_fit_after"]))
c.check("fitted RMS residual is about one sigma", res["rms_z_fit_after"] < 2.5, res["rms_z_fit_after"])
c.check("the validation case improves too (not used in the fit)", res["rms_z_val_after"] is not None and res["rms_z_val_after"] < res["rms_z_val_before"], (res["rms_z_val_before"], res["rms_z_val_after"]))
c.check("validation rows are labelled", any(r["Use"] == "validation" for r in res["rows"]) and any(r["Use"] == "fit" for r in res["rows"]), "")
c.check("confidence intervals are returned and plausible", all(x["ci95"] is not None and 0 < x["ci95"] < 30 for x in res["params"]), [x["ci95"] for x in res["params"]])
c.check("truth lies within three CI", abs(p["eff"]["after"] - 71.0) < 3 * p["eff"]["ci95"] + 0.5, (p["eff"]["after"], p["eff"]["ci95"]))
c.check("the original model is untouched", T.get_param(model, "K-1", "eff") == 80.0, "")
n = T.apply_calibration(model, res)
c.check("apply writes the fitted values and records them", n == 2 and abs(T.get_param(model, "K-1", "eff") - p["eff"]["after"]) < 1e-9 and model["calibration_applied"], "")
after = solve(model)
c.close("the calibrated model reproduces the plant power", after.results[T.unit_id(model, "K-1")]["Power [kW]"] / solve(truth).results[T.unit_id(truth, "K-1")]["Power [kW]"], 1.0, 0.02)

# identifiability: two parameters that act the same way on the measured quantity
r2 = T.calibrate(line(eff=80.0), {"params": [{"unit": "K-1", "key": "eff", "lo": 50.0, "hi": 90.0},
                                              {"unit": "K-1", "key": "driver_eff", "lo": 50.0, "hi": 100.0}],
                                  "cases": cases[:3], "max_nfev": 10})
c.check("an unobservable parameter is reported", any("no effect" in s or "singular" in s or "correlated" in s for s in r2["notes"]), r2["notes"])
try:
    T.calibrate(line(), {"params": [{"unit": "K-1", "key": "eff", "lo": 50, "hi": 90}],
                         "cases": [{"name": "x", "measure": [{"kind": "stream", "obj": "nope", "q": "Temperature [°C]", "value": 1.0}]}]})
    c.check("a measurement that cannot be read is refused", False, "")
except ValueError as e_:
    c.check("a measurement that cannot be read is refused", "cannot be read" in str(e_), str(e_))
try:
    T.calibrate(line(), {"params": [{"unit": "K-1", "key": "eff", "lo": 50, "hi": 90}], "cases": [{"name": "x", "measure": []}]})
    c.check("under-determined problem refused", False, "")
except ValueError:
    c.check("under-determined problem refused", True, "")
bad = copy.deepcopy(cases[:2])
bad[0]["set"] = [{"unit": "Gas", "key": "flow", "value": 1.5}]
try:
    T.calibrate(line(), {"params": [{"unit": "K-1", "key": "nope", "lo": 0, "hi": 1}], "cases": bad})
    c.check("unknown parameter refused", False, "")
except (KeyError, ValueError):
    c.check("unknown parameter refused", True, "")
q = T.quantities(truth, solve(truth), "stream", "Out")
c.check("quantity lists include properties and compositions", "Temperature [°C]" in q and "mol% C1" in q, q[:4])
c.check("composition measurement", abs(T.measure(truth, solve(truth), {"kind": "stream", "obj": "Out", "q": "mol% C1"}) - 78.0) < 3.0, "")

# ---- optimisation -------------------------------------------------------------------------------------------------
def two_stage():
    m = new_model()
    f = add_unit(m, "feed", 0, 0, "Gas", {"T_C": 30.0, "P_bar": 10.0, "flow_basis": "MSm³/d", "flow": 2.0, "composition": dict(WET_GAS)})
    k1 = add_unit(m, "compressor", 100, 0, "K-1", {"spec": "Outlet pressure", "P_out": 30.0, "eff": 75.0})
    e1 = add_unit(m, "cooler", 200, 0, "E-1", {"spec": "Outlet temperature", "T_out": 35.0})
    k2 = add_unit(m, "compressor", 300, 0, "K-2", {"spec": "Outlet pressure", "P_out": 90.0, "eff": 75.0})
    e2 = add_unit(m, "cooler", 400, 0, "E-2", {"spec": "Outlet temperature", "T_out": 35.0})
    o = add_unit(m, "product", 500, 0, "Out")
    connect(m, f, "out", k1, "in", "1")
    connect(m, k1, "out", e1, "in", "2")
    connect(m, e1, "out", k2, "in", "3")
    connect(m, k2, "out", e2, "in", "4")
    connect(m, e2, "out", o, "in", "5")
    return m


m = two_stage()
# total power as a function of the intermediate pressure; the optimum is near the geometric mean sqrt(10 x 90) = 30
spec = {"vars": [{"unit": "K-1", "key": "P_out", "lo": 15.0, "hi": 60.0}],
        "objective": {"kind": "economics", "obj": "", "q": "Power demand [kW]", "sense": "min"}, "constraints": [], "max_iter": 20}
# the second compressor's inlet floats with the first discharge, so the 90 bar outlet stays fixed
r = T.optimise(m, spec)
c.check("optimiser lowers the total power", r["objective_best"] <= r["objective_start"] + 1e-6, (r["objective_start"], r["objective_best"]))
c.close("intermediate pressure near the geometric mean (30 bar)", r["vars"][0]["best"], 30.0, 4.0)
spec2 = copy.deepcopy(spec)
spec2["vars"][0]["lo"], spec2["vars"][0]["hi"] = 40.0, 60.0
r2 = T.optimise(m, spec2)
c.check("a range that excludes the optimum ends on the bound and says so", abs(r2["vars"][0]["best"] - 40.0) < 0.5 and any("bound" in s for s in r2["notes"]), (r2["vars"][0]["best"], r2["notes"]))
spec3 = copy.deepcopy(spec)
spec3["constraints"] = [{"kind": "unit", "obj": "K-2", "q": "Outlet T [°C]", "op": "<=", "value": 100.0}]
r3 = T.optimise(m, spec3)
c.check("constrained optimum respects the limit", r3["constraints_best"][0] is not None and r3["constraints_best"][0] <= 100.0 + 1e-3, r3["constraints_best"])
spec4 = copy.deepcopy(spec)
spec4["constraints"] = [{"kind": "unit", "obj": "K-2", "q": "Outlet T [°C]", "op": "<=", "value": 20.0}]
r4 = T.optimise(m, spec4)
c.check("an impossible limit is reported as infeasible", any("feasible" in s for s in r4["notes"]), r4["notes"])
mm = copy.deepcopy(m)
T.apply_optimum(mm, r)
c.check("apply_optimum writes the variables", abs(T.get_param(mm, "K-1", "P_out") - r["vars"][0]["best"]) < 1e-9, "")
spec5 = copy.deepcopy(spec)
spec5["objective"]["sense"] = "max"
c.check("maximise goes the other way", T.optimise(m, spec5)["objective_best"] >= r["objective_best"], "")

sys.exit(c.report())
