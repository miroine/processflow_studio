"""v7.9: tornado, sizing ranges, convergence feedback.   Run:  python tests/test_v79.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from check import Checker                                                   # noqa: E402
from procsim import tuning as TU, convdiag, advisor                         # noqa: E402
from procsim.examples import EXAMPLES                                       # noqa: E402
from procsim.flowsheet import normalize, solve                              # noqa: E402

c = Checker("v7.9")
m = EXAMPLES["Two-stage gas compression with liquid recycle"]()
normalize(m)
import copy                                                                 # noqa: E402
m0 = copy.deepcopy(m)
comp = [u["name"] for u in m["units"].values() if u["type"] == "compressor"]
sol = solve(m)
c.check("base solved", sol.converged, str(sol.errors))
ref = {"kind": "economics", "obj": "", "q": "Power demand [kW]"}
tor = TU.tornado(m, [(comp[0], "P_out"), (comp[0], "eff")], ref, 10.0)
c.check("tornado returns a row per parameter, sorted by swing", len(tor["rows"]) == 2 and tor["rows"][0]["swing"] >= tor["rows"][1]["swing"], str(tor["rows"]))
c.check("a higher efficiency lowers power", next(r for r in tor["rows"] if r["key"] == "eff")["high"] < tor["base"], str(tor))
c.check("the model is not changed by the study", m == m0, "")

rows = convdiag.summarise(sol)
c.check("recycle summary lists the recycle and says converged", rows and all(r["verdict"] == "converged" for r in rows), str(rows))
fake = type("S", (), {"recycle_log": [{"iteration": i, "recycle": "R", "flow error": 0.5, "T error [°C]": 1.0} for i in range(1, 10)], "converged": False})()
v = convdiag.summarise(fake)[0]
c.check("a stalled loop is flagged with a hint", "stalled" in v["verdict"] and v["hint"], str(v))

big = EXAMPLES["Two-stage gas compression with liquid recycle"]()
normalize(big)
sol2 = solve(big)
kid = next(k for k, u in big["units"].items() if u["type"] == "compressor")
sol2.results[kid]["Power [kW]"] = 90000.0
adv = advisor.advise(big, sol2)
c.check("sizing range flags a compressor above the typical size", any("above a typical single unit" in a["Finding"] for a in adv), str([a["Finding"] for a in adv]))

# ---- named cases
from procsim import cases as CS                                              # noqa: E402
import json                                                                  # noqa: E402
mc = EXAMPLES["Two-stage gas compression with liquid recycle"]()
normalize(mc)
CS.add_case(mc, "High pressure")
c.check("two cases listed, base open", CS.names(mc) == ["Base", "High pressure"], str(CS.names(mc)))
mh = CS.switch(mc, "High pressure")
kid = next(k for k, u in mh["units"].items() if u["type"] == "compressor")
mh["units"][kid]["params"]["P_out"] = 99.0
c.check("the new case is open and holds Base as snapshot", CS.active(mh) == "High pressure" and "Base" in mh["cases"] and "cases" not in mh["cases"]["Base"], "")
mb = CS.switch(mh, "Base")
c.check("switching back restores the base values and keeps the edit", mb["units"][kid]["params"]["P_out"] != 99.0 and mb["cases"]["High pressure"]["units"][kid]["params"]["P_out"] == 99.0, "")
back = json.loads(json.dumps(mh, default=float))
c.check("cases survive a save and reopen", CS.names(back) == ["High pressure", "Base"] and solve(back).converged, "")
CS.delete(mb, "High pressure")
c.check("deleting the last other case drops the cases section", "cases" not in mb, "")
try:
    CS.delete(mb, "Base")
    ok = False
except ValueError:
    ok = True
c.check("the open case cannot be deleted", ok, "")
sys.exit(c.report())
