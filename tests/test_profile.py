"""Profile (time-series) simulation checks (v7.2): table generation, CSV round trip, row application, one steady-state
solve per step, cumulative integration, failed steps, and agreement of a profile step with a plain solve.

Run:  python tests/test_profile.py      (about 1 minute)
"""
import copy
import math
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
from check import Checker                                # noqa: E402
from procsim import examples as EX                       # noqa: E402
from procsim import timeseries as TS                     # noqa: E402
from procsim.flowsheet import solve                      # noqa: E402

c = Checker("profile")
NAME = next(n for n in EX.EXAMPLES if n.startswith("Oil stabilisation"))
m = EX.EXAMPLES[NAME]()
F, P_, T_ = "Well fluid | flow", "Well fluid | P_bar", "Well fluid | T_C"
GAS, LIQ = "Overall | Gas products [MSm³/d]", "Overall | Liquid products [Sm³/d]"
before = copy.deepcopy(m)


def raises(fn, *a, **k):
    try:
        fn(*a, **k)
    except TS.ProfileError:
        return True
    return False


# ---- feeds and columns ------------------------------------------------------------------------------------------
c.eq("feeds found", [(n, b) for _, n, b in TS.feeds(m)], [("Well fluid", "kmol/h")])
c.eq("feed columns", TS.feed_columns(m), [F, P_, T_])
c.eq("one variable only", TS.feed_columns(m, ("P",)), [P_])
c.eq("column header carries the unit", TS.column_label(m, F), "Well fluid | flow [kmol/h]")
c.eq("base row reads the flowsheet", TS.base_row(m, [F, P_, T_]), {F: 3000.0, P_: 70.0, T_: 75.0})

# ---- generate ---------------------------------------------------------------------------------------------------
g = TS.generate(m, n=5, dt=2.0, t0=1.0, flow_end=0.5, P_end=0.8, T_end_delta=-10)
c.eq("generate: five rows", len(g), 5)
c.eq("generate: times", [r["Time"] for r in g], [1.0, 3.0, 5.0, 7.0, 9.0])
c.close("generate: first row is the flowsheet", g[0][F], 3000.0, 1e-9)
c.close("generate: last flow = base x factor", g[-1][F], 1500.0, 1e-9)
c.close("generate: last pressure", g[-1][P_], 56.0, 1e-9)
c.close("generate: temperature change", g[-1][T_], 65.0, 1e-9)
c.close("generate: linear midpoint", g[2][F], 2250.0, 1e-9)
ge = TS.generate(m, n=3, flow_end=0.25, shape="exponential")
c.close("generate: exponential midpoint = base x sqrt(factor)", ge[1][F], 1500.0, 1e-9)
c.check("generate: exponential rejects a zero end factor", raises(TS.generate, m, 3, 1, 0, 0.0, 1, 0, "exponential"), "")
c.check("generate: rejects zero steps", raises(TS.generate, m, 0), "")
c.check("generate: single step", len(TS.generate(m, n=1)) == 1, "")
c.eq("generate: only the chosen variable", [k for k in TS.generate(m, 2, variables=("flow",))[0] if k != "Time"], [F])

# ---- CSV --------------------------------------------------------------------------------------------------------
txt = TS.to_csv(m, g)
back = TS.parse_csv(txt)
c.eq("csv round trip: columns", list(back[0]), ["Time", F, P_, T_])
c.close("csv round trip: value", back[3][P_], g[3][P_], 1e-9)
c.check("csv header carries units", "Well fluid | flow [kmol/h]" in txt.splitlines()[0], txt.splitlines()[0])
semi = "Time;Well fluid | flow;Well fluid | P_bar\n0;3000,5;70\n1;;65\n"
sp = TS.parse_csv(semi)
c.close("csv: semicolon and decimal comma", sp[0][F], 3000.5, 1e-9)
c.check("csv: blank cell is None", sp[1][F] is None, str(sp[1]))
c.check("csv: needs Time first", raises(TS.parse_csv, "x,a\n1,2\n"), "")
c.check("csv: non-number reported", raises(TS.parse_csv, "Time,Well fluid | flow\n0,abc\n"), "")
c.check("csv: header only rejected", raises(TS.parse_csv, "Time,a\n"), "")

# ---- validation -------------------------------------------------------------------------------------------------
c.check("clean: non-increasing time", raises(TS.clean_rows, [{"Time": 1}, {"Time": 1}]), "")
c.check("clean: missing time", raises(TS.clean_rows, [{"Time": None}]), "")
c.check("clean: empty", raises(TS.clean_rows, []), "")
c.check("clean: too many steps", raises(TS.clean_rows, [{"Time": i} for i in range(TS.MAX_STEPS + 1)]), "")
c.check("clean: blank string becomes None", TS.clean_rows([{"Time": 0, F: ""}])[0][F] is None, "")
c.eq("check_columns: valid", TS.check_columns(m, [{"Time": 0, F: 1, "VLV-100 | P_out": 1}]), [])
c.check("check_columns: unknown unit", bool(TS.check_columns(m, [{"Time": 0, "Nope | flow": 1}])), "")
c.check("check_columns: unknown parameter", bool(TS.check_columns(m, [{"Time": 0, "Well fluid | nope": 1}])), "")
c.check("check_columns: non-numeric parameter", bool(TS.check_columns(m, [{"Time": 0, "Well fluid | flow_basis": 1}])), "")
dm = copy.deepcopy(m)
for d in dm["units"].values():
    if d["type"] == "scrubber":
        d["name"] = "Scrubber"
c.check("check_columns: duplicate unit names refused", any("rename" in b for b in TS.check_columns(dm, [{"Time": 0, "Scrubber | K": 1}])), "")

# ---- apply_row --------------------------------------------------------------------------------------------------
am = TS.apply_row(m, {"Time": 0, F: 1234.0, P_: None, T_: 60.0})
fid = next(u for u, d in m["units"].items() if d["type"] == "feed")
c.eq("apply_row: override set", am["units"][fid]["params"]["flow"], 1234.0)
c.eq("apply_row: blank keeps the flowsheet value", am["units"][fid]["params"]["P_bar"], 70.0)
c.eq("apply_row: original untouched", m["units"][fid]["params"]["flow"], 3000.0)
c.check("apply_row: post-processing results are not copied", "profile" not in am and "scenarios" not in am, "")

# ---- run: three steps ---------------------------------------------------------------------------------------------
base_sol = solve(copy.deepcopy(m))
rows = [{"Time": 0, F: 3000.0, P_: 70.0, T_: 75.0},
        {"Time": 1, F: 2000.0, P_: None, T_: None},
        {"Time": 2, F: 3000.0, P_: 60.0, T_: 75.0}]
seen = []
res = TS.run(m, rows, progress=lambda a, b: seen.append((a, b)), time_unit="y")
c.eq("run: progress reported", seen, [(1, 3), (2, 3), (3, 3)])
c.eq("run: all steps solved", [s["status"] for s in res["steps"]], ["ok", "ok", "ok"])
c.eq("run: summary", {k: v for k, v in TS.summary(res).items() if k != "seconds"}, {"steps": 3, "ok": 3, "warning": 0, "failed": 0})
c.eq("run: model unchanged", m["units"][fid]["params"], before["units"][fid]["params"])
from procsim import scenarios as SC                      # noqa: E402
kp = SC.scenario_kpis(m, base_sol)
for lab, key in ((LIQ, "Liquid products [Sm³/d]"), (GAS, "Gas products [MSm³/d]")):
    c.rel(f"run: step 1 equals a plain solve ({key})", res["steps"][0]["values"][lab], kp[key], 1e-9) if kp[key] else None
c.rel("run: liquid scales with the rate (steady, same P and T)", res["steps"][1]["values"][LIQ] / res["steps"][0]["values"][LIQ], 2 / 3, 1e-6)
sp = "Stream Well fluid | Pressure [bar(a)]"
c.eq("run: pressure step reaches the feed", [round(v, 6) for v in TS.series(res, sp)], [70.0, 70.0, 60.0])
lab_t = "Stream Well fluid | Temperature [°C]"
c.close("run: temperature step", TS.series(res, lab_t)[0], 75.0, 1e-6)
c.check("run: a different pressure gives a different liquid rate", abs(res["steps"][2]["values"][LIQ] - res["steps"][0]["values"][LIQ]) > 1e-6 * res["steps"][0]["values"][LIQ], "")
c.check("run: unit results collected", any(k.startswith("Unit K-300 LP comp") for k in TS.labels(res)), "")
c.check("run: stream properties collected", any(k.startswith("Stream Stabilised oil") for k in TS.labels(res)), "")
c.eq("run: series length", len(TS.series(res, LIQ)), 3)

# cumulative: liquid [Sm3/d] over years - rectangle rule, the last step repeats the previous duration
days = TS.durations_days([0, 1, 2], "y")
c.close("durations: years to days", days[0], 365.25, 1e-9)
c.eq("durations: last repeats previous", days[2], days[1])
c.close("durations: single row is one unit", TS.durations_days([5.0], "d")[0], 1.0, 1e-12)
v = [s["values"][LIQ] for s in res["steps"]]
c.rel("cumulative liquid", res["cumulative"]["Cumulative liquid [Sm³]"][-1], sum(v) * 365.25, 1e-9)
c.check("cumulative is non-decreasing", all(b >= a for a, b in zip(*[iter(res["cumulative"]["Cumulative liquid [Sm³]"])] * 2)), "")
fr = TS.frame_rows(res, [LIQ])
c.check("frame rows carry time, status, quantity and cumulative", set(fr[0]) >= {"Time", "Status", LIQ, "Cumulative liquid [Sm³]"}, str(list(fr[0])))
c.check("time unit validated", raises(TS.run, m, rows, None, "fortnight"), "")
c.check("unknown column stops the run before solving", raises(TS.run, m, [{"Time": 0, "Nope | x": 1}]), "")

# ---- a step that cannot be solved does not stop the run ---------------------------------------------------------------
bad = TS.run(m, [{"Time": 0, F: 3000.0}, {"Time": 1, F: -5.0}, {"Time": 2, F: 2500.0}], time_unit="d")
c.eq("failed step marked, the others solved", [s["status"] for s in bad["steps"]], ["ok", "failed", "ok"])
c.check("failed step explains itself", "negative" in bad["steps"][1]["message"].lower() or bad["steps"][1]["message"], bad["steps"][1]["message"])
c.check("failed step has no values and a gap in the series", TS.series(bad, LIQ)[1] is None, "")
c.eq("summary counts the failure", TS.summary(bad)["failed"], 1)
c.rel("cumulative skips the failed step", bad["cumulative"]["Cumulative liquid [Sm³]"][1], bad["cumulative"]["Cumulative liquid [Sm³]"][0], 1e-12)

# ---- another unit's parameter in the table ----------------------------------------------------------------------------
ex = TS.run(m, [{"Time": 0, "VLV-100 | P_out": 20.0}, {"Time": 1, "VLV-100 | P_out": 30.0}], time_unit="d")
pw = [s["values"].get("Overall | Power demand [kW]") for s in ex["steps"]]
c.check("a unit parameter in the table changes the result", pw[0] is not None and pw[1] is not None and abs(pw[0] - pw[1]) > 1e-3 * abs(pw[0]), str(pw))
c.eq("blank flow keeps the feed rate", TS.run(m, [{"Time": 0, "VLV-100 | P_out": 20.0}])["steps"][0]["status"], "ok")

# ---- single step equals the plain solve exactly (profile mode is an addition, not a change) ------------------------------------
one = TS.run(m, [{"Time": 0.0}])
c.eq("single empty row = the flowsheet", one["steps"][0]["values"].get(LIQ), res["steps"][0]["values"].get(LIQ))

# ---- adjust loops are solved inside every step ----------------------------------------------------------------------------
JT = next(n for n in EX.EXAMPLES if n.startswith("JT dew"))
jm = EX.EXAMPLES[JT]()
jf = TS.feeds(jm)[0][1]
jr = TS.run(jm, [{"Time": 0, TS.column(jf, "flow"): jm["units"][TS.feeds(jm)[0][0]]["params"]["flow"]},
                 {"Time": 1, TS.column(jf, "flow"): jm["units"][TS.feeds(jm)[0][0]]["params"]["flow"] * 0.8}])
c.eq("flowsheet with an Adjust: both steps solved", [s["status"] for s in jr["steps"]], ["ok", "ok"])

sys.exit(c.report())
