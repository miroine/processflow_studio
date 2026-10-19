"""Data exchange checks (v7.4): import from CSV / Excel / YAML, validation and the change plan, batch edits, result tables,
export files, round trips, and the restricted Python runner.

Run:  python tests/test_datatools.py      (about 1 minute)
"""
import copy
import io
import os
import sys
import zipfile

import numpy as np
import pandas as pd

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
from check import Checker                                    # noqa: E402
from procsim import datatools as D                           # noqa: E402
from procsim import examples as EX                           # noqa: E402
from procsim.flowsheet import solve                          # noqa: E402

c = Checker("datatools")
NAME = next(n for n in EX.EXAMPLES if n.startswith("Oil stabilisation"))
m = EX.EXAMPLES[NAME]()
sol = solve(m)


def uid(name):
    return D.unit_names(m)[0][name]


def csv_plan(text, model=None, name="x.csv"):
    model = model or m
    r = D.read_file(name, text.encode(), model)
    return D.plan(model, r["requests"]), r


# ---- 1. tables and the Excel round trip ---------------------------------------------------------------------------------------
t = D.result_tables(m, sol)
c.check("parameter and composition tables are always there", {"Parameters", "Feed compositions"} <= set(t), str(list(t)))
c.check("result tables with a solution", {"Streams", "Unit results", "Overall", "Energy streams"} <= set(t), str(list(t)))
c.eq("one row per catalogue parameter", len(t["Parameters"]), sum(len(D.CATALOGUE[u["type"]]["params"]) for u in m["units"].values()))
xb = D.to_excel_bytes(t, "test")
back = pd.read_excel(io.BytesIO(xb), sheet_name=None)
c.eq("every table is a sheet", list(back), D.sheet_names(t))
c.check("sheet content survives", back["Streams"].shape == t["Streams"].shape, "")
r = D.read_file("book.xlsx", xb, m)
kinds = {s[0]: s[1] for s in r["sheets"]}
c.check("the Parameters and composition sheets are recognised", kinds["Parameters"] == D.KINDS[0] and kinds["Feed compositions"] == D.KINDS[2], str(kinds))
c.check("result sheets are ignored, not read as parameters", kinds["Unit results"] is None and kinds["Streams"] is None and kinds["Energy streams"] is None, str(kinds))
pl = D.plan(m, r["requests"])
cnt = D.plan_summary(pl)
c.eq("re-importing an export changes nothing and has no error", (cnt["ok"], cnt["error"]), (0, 0))
c.check("most rows are 'unchanged'", cnt["unchanged"] > 80, str(cnt))

# ---- 2. long CSV: set values, plan, apply -----------------------------------------------------------------------------------------
csvtxt = "Unit,Parameter,Value\nVLV-100,P_out,22.5\nK-300 LP comp,eff,80\nE-300,T_out,35\n"
pl, r = csv_plan(csvtxt)
c.eq("three changes planned", [x["status"] for x in pl], ["ok"] * 3)
c.close("old value is read from the flowsheet", pl[0]["old"], m["units"][uid("VLV-100")]["params"]["P_out"], 1e-12)
m2 = copy.deepcopy(m)
pl2 = D.plan(m2, r["requests"])
c.eq("apply returns the number of changes", D.apply_plan(m2, pl2), 3)
c.close("the value is written", m2["units"][uid("VLV-100")]["params"]["P_out"], 22.5, 0)
c.close("other values are untouched", m2["units"][uid("VLV-200")]["params"]["P_out"], m["units"][uid("VLV-200")]["params"]["P_out"], 0)
c.check("the original model is untouched by planning", m["units"][uid("VLV-100")]["params"]["P_out"] != 22.5, "")
s2 = solve(m2)
bad = [u for u, v in s2.status.items() if v in ("error", "missing", "unsolved")]
c.check("the changed flowsheet still solves", not bad, str(bad))

# ---- 3. wide table, semicolons, decimal commas, units in headers ---------------------------------------------------------------
wide = "Unit;P_out [bar(a)];Cv_rated\nVLV-100;21,5;\nVLV-200;9,25;150\n"
pl, r = csv_plan(wide)
c.eq("wide table: blank cells are skipped, not read as zero", [(x["unit"], x["key"]) for x in pl],
     [("VLV-100", "P_out"), ("VLV-200", "P_out"), ("VLV-200", "Cv_rated")])
c.close("decimal comma", pl[0]["new"], 21.5, 1e-12)
c.eq("kind detected", r["sheets"][0][1], D.KINDS[1])
pl, _ = csv_plan("Name,Pressure drop,Specification\nE-300,0.35,Outlet temperature\n")
c.eq("a parameter can be given by its label", [(x["key"], x["status"]) for x in pl], [("dP", "ok"), ("spec", "unchanged")])

# ---- 4. validation ---------------------------------------------------------------------------------------------------------------------------
pl, _ = csv_plan("Unit,Parameter,Value\nVLV-1000,P_out,5\nVLV-100,P_outt,5\nK-300 LP comp,eff,abc\nK-300 LP comp,eff,150\n"
                 "K-300 LP comp,spec,outlet pressure\nK-300 LP comp,spec,Nonsense\nVLV-100,P_out,\n")
st_ = [x["status"] for x in pl]
c.eq("unknown unit, unknown parameter, text, above max, bad option are errors; a blank is skipped",
     st_, ["error", "error", "error", "error", "unchanged", "error", "skipped"])
c.check("unit typo gets a suggestion", "VLV-100" in pl[0]["message"], pl[0]["message"])
c.check("parameter typo gets a suggestion", "P_out" in pl[1]["message"], pl[1]["message"])
c.check("max is reported", "maximum" in pl[3]["message"], pl[3]["message"])
c.check("a select value is matched case-insensitively", pl[4]["new"] == "Outlet pressure", str(pl[4]["new"]))
c.check("a bad option lists the choices", "Pressure ratio" in pl[5]["message"], pl[5]["message"])
c.eq("errors are not applied", D.apply_plan(copy.deepcopy(m), pl), 0)
m3 = copy.deepcopy(m)
nm_ = m3["units"][uid("VLV-100")]
m3["units"]["u99"] = {"type": "valve", "name": "VLV-100", "x": 0, "y": 0, "params": {}}
pl = D.plan(m3, [D._req("VLV-100", "P_out", 5.0)])
c.check("two units with the same name are refused", pl[0]["status"] == "error" and "several" in pl[0]["message"], pl[0]["message"])
pl, _ = csv_plan("Unit,Parameter,Value\nVLV-100,spec,Outlet pressure\nVLV-100,dP,3\n")
c.check("a parameter hidden by the specification is flagged", pl[1]["status"] == "warning" and "not used" in pl[1]["message"], str(pl[1]))
pl, _ = csv_plan("Unit,Parameter,Value\nVLV-100,P_out,20\nVLV-100,P_out,21\n")
c.check("a repeated cell replaces the earlier one with a warning", pl[1]["status"] == "warning" and "replaces" in pl[1]["message"], str(pl))

# ---- 5. compositions -------------------------------------------------------------------------------------------------------------------------
feed = m["units"][uid("Well fluid")]["params"]["composition"]
pl, r = csv_plan("Feed,Component,Fraction\nWell fluid,C1,0.5\nWell fluid,C2,0.2\nWell fluid,Zz,0.1\nWell fluid,C3,-1\nVLV-100,C1,0.3\n")
c.eq("composition rows", [x["status"] for x in pl], ["ok", "ok", "error", "error", "error"])
c.check("unknown component and non-feed are explained", "fluid package" in pl[2]["message"] and "feeds" in pl[4]["message"], str([x["message"] for x in pl]))
pl, r = csv_plan("Feed,C1,C2,C3\nWell fluid,0.6,0.3,0.1\n")
c.eq("wide compositions are recognised", r["sheets"][0][1], D.KINDS[3])
m4 = copy.deepcopy(m)
D.apply_plan(m4, D.plan(m4, r["requests"]))
c.close("composition written", m4["units"][uid("Well fluid")]["params"]["composition"]["C1"], 0.6, 0)
c.close("components that are not mentioned keep their value", m4["units"][uid("Well fluid")]["params"]["composition"].get("nC4", 0.0), feed.get("nC4", 0.0), 0)

# ---- 6. YAML ----------------------------------------------------------------------------------------------------------------------------
y = """
units:
  VLV-100: {P_out: 23.0}
  K-300 LP comp:
    eff: 81
  Well fluid:
    composition: {C1: 0.55}
"""
r = D.read_file("c.yaml", y.encode(), m)
pl = D.plan(m, r["requests"])
c.eq("YAML units", sorted(x["key"] for x in pl), ["P_out", "composition.C1", "eff"])
c.eq("YAML applies", (lambda mm: (D.apply_plan(mm, D.plan(mm, r["requests"])), mm["units"][uid("VLV-100")]["params"]["P_out"]))(copy.deepcopy(m)), (3, 23.0))
ys = D.model_to_yaml(m)
r = D.read_file("f.yaml", ys.encode(), m)
c.check("a whole flowsheet in YAML is recognised", "flowsheet" in r["extras"], "")
fs = D.check_flowsheet(r["extras"]["flowsheet"])
s3 = solve(fs)
c.check("the flowsheet round-tripped through YAML solves to the same stream temperatures",
        all(abs((sol.streams[k].T or 0) - (s3.streams[k].T or 0)) < 1e-6 for k in sol.streams if k in s3.streams and not sol.streams[k].empty), "")
c.check("YAML parameters export is importable", len(D.read_file("p.yaml", D.parameters_to_yaml(m).encode(), m)["requests"]) > 50, "")
pl = D.plan(m, D.read_file("p.yaml", D.parameters_to_yaml(m).encode(), m)["requests"])
c.eq("and changes nothing", D.plan_summary(pl)["ok"] + D.plan_summary(pl)["error"], 0)
for bad_ in ("units: [1, 2]", "foo: 1", "[1, 2", "units: {A: 3}"):
    try:
        D.read_file("b.yaml", bad_.encode(), m)
        ok_ = False
    except D.DataError:
        ok_ = True
    c.check(f"bad YAML is refused: {bad_!r}", ok_, "")
try:
    D.check_flowsheet({"fluid": {}, "units": {"a": {"type": "zzz", "name": "a"}}, "streams": {}})
    ok_ = False
except D.DataError:
    ok_ = True
c.check("unknown unit types in a flowsheet file are refused", ok_, "")

# ---- 7. profile and events --------------------------------------------------------------------------------------------------------------------
pr = "Time,Well fluid | flow [kmol/h],Well fluid | P_bar\n0,3000,70\n1,2800,66\n2,2600,\n"
r = D.read_file("p.csv", pr.encode(), m)
c.eq("profile table recognised", r["sheets"][0][1], D.KINDS[4])
ps = D.profile_settings(m, r["extras"]["profile_rows"])
c.eq("three rows, blank cell is None", (len(ps["rows"]), ps["rows"][2]["Well fluid | P_bar"]), (3, None))
c.eq("feed and variables found", (ps["feeds"], ps["vars"]), (["Well fluid"], ["flow", "P"]))
try:
    D.profile_settings(m, [{"Time": 0.0, "Nope | P_bar": 1.0}])
    ok_ = False
except D.DataError:
    ok_ = True
c.check("a profile column for a unit that does not exist is refused", ok_, "")
ev = "Time [s],Event,Target,Value,Ramp [s]\n60,Feed rate,Well fluid,3600,10\n120,comp_trip,K-300 LP comp,,\n"
r = D.read_file("e.csv", ev.encode(), m)
evs = r["extras"]["events"]
c.eq("events: kinds by label or key", [e["kind"] for e in evs], ["feed_flow", "comp_trip"])
c.eq("event values", (evs[0]["value"], evs[0]["ramp"], evs[0]["t"]), (3600.0, 10.0, 60.0))
m5 = copy.deepcopy(m)
D.apply_events(m5, evs)
c.eq("events are stored for the Dynamic tab", len(m5["dynamics"]["events"]), 2)
try:
    D.read_file("e.csv", "Time,Event,Target,Value\n1,Explode,X,1\n".encode(), m)
    ok_ = False
except D.DataError:
    ok_ = True
c.check("an unknown event is refused", ok_, "")

# ---- 8. batch edits --------------------------------------------------------------------------------------------------------------------------------
coolers = D.select_units(m, ["cooler"])
c.eq("select by type", sorted(m["units"][u]["name"] for u in coolers), ["E-201", "E-300"])
c.eq("select by wildcard", sorted(m["units"][u]["name"] for u in D.select_units(m, None, "V-?00*")), ["V-100 HP sep", "V-200 MP sep", "V-300 LP sep"])
c.eq("select by 'contains' and several patterns", sorted(m["units"][u]["name"] for u in D.select_units(m, ["compressor"], "LP;MP")), ["K-200 MP comp", "K-300 LP comp"])
pl = D.plan_batch(m, coolers, "dP", "Multiply by", 2)
c.eq("multiply", [(x["old"] * 2, x["new"]) for x in pl], [(x["new"], x["new"]) for x in pl])
mb = copy.deepcopy(m)
D.apply_plan(mb, D.plan_batch(mb, D.select_units(mb, ["cooler"]), "dP", "Add", 0.1))
c.close("add", mb["units"][uid("E-300")]["params"]["dP"], m["units"][uid("E-300")]["params"]["dP"] + 0.1, 1e-12)
D.apply_plan(mb, D.plan_batch(mb, D.select_units(mb, ["cooler"]), "dP", "Reset to default"))
c.close("reset to default", mb["units"][uid("E-300")]["params"]["dP"], 0.5, 0)
pl = D.plan_batch(m, D.select_units(m, ["cooler", "valve"]), "T_out", "Set to", 30)
c.eq("units without the parameter are skipped, the others set", sorted(set(x["status"] for x in pl)), ["ok", "skipped", "unchanged"][:3] if any(x["status"] == "unchanged" for x in pl) else ["ok", "skipped"])
c.check("valves are reported as skipped", all(x["status"] == "skipped" for x in pl if x["unit"].startswith("VLV")), "")
pl = D.plan_batch(m, D.select_units(m, ["cooler"]), "spec", "Multiply by", 2)
c.check("a text parameter cannot be multiplied", all(x["status"] == "error" for x in pl), str([x["message"] for x in pl]))
try:
    D.plan_batch(m, coolers, "dP", "Multiply by", "abc")
    ok_ = False
except D.DataError:
    ok_ = True
c.check("a non-number operand is refused", ok_, "")
before = D.wide_parameters(m, "cooler")
after = before.copy()
after.loc[0, "dP"] = 0.77
after.loc[1, "spec"] = "Duty"
reqs = D.requests_from_edited(m, before, after)
c.eq("only edited cells become requests", sorted((r_["unit"], r_["param"]) for r_ in reqs), sorted([(before.loc[0, "Unit"], "dP"), (before.loc[1, "Unit"], "spec")]))
mw = copy.deepcopy(m)
D.apply_plan(mw, D.plan(mw, reqs))
c.close("edited table value applied", mw["units"][uid(before.loc[0, "Unit"])]["params"]["dP"], 0.77, 0)

# ---- 9. export files -------------------------------------------------------------------------------------------------------------------------------
c.eq("sheet names are legal and unique", D.sheet_names({"a/b": 1, "A/B": 2, "x" * 50: 3}), ["a-b", "A-B (2)", "x" * 31])
z = zipfile.ZipFile(io.BytesIO(D.to_csv_zip(t)))
c.eq("CSV zip has one file per table", sorted(z.namelist()), sorted(n + ".csv" for n in D.sheet_names(t)))
c.check("CSV text", D.to_csv_text(t["Overall"]).splitlines()[0] == "Quantity,Value", "")
prof = {"steps": [{"t": 0, "status": "ok", "message": "", "seconds": 0.1, "values": {"Overall | Gas [MSm³/d]": 1.0}},
                  {"t": 1, "status": "ok", "message": "", "seconds": 0.1, "values": {"Overall | Gas [MSm³/d]": 0.9}}],
        "cumulative": {}, "time_unit": "y"}
dyn = {"t": [0.0, 1.0], "series": {"V | Pressure [bar(a)]": [10.0, 9.0]}}
t2 = D.result_tables(m, sol, profile=prof, dynamic=dyn)
c.check("profile and dynamic results are tables when given", "Profile results" in t2 and t2["Dynamic results"].shape == (2, 2), str(list(t2)))
c.check("export without a solution has only the parameter tables", list(D.result_tables(m)) == ["Parameters", "Feed compositions"], "")

# ---- 10. restricted Python runner --------------------------------------------------------------------------------------------------------------
small = {"Overall": t["Overall"].copy(), "Parameters": t["Parameters"].copy()}
res = D.run_script("df = tables['Overall']\nprint(len(df))\ndf['Value'] = 0\ntables['New'] = pd.DataFrame({'a': np.arange(3)})\n", small)
c.check("script runs", res["ok"], res["error"])
c.check("print is captured", res["stdout"].strip() == str(len(small["Overall"])), res["stdout"])
c.check("the table was edited and a table added", (res["tables"]["Overall"]["Value"] == 0).all() and list(res["tables"]["New"]["a"]) == [0, 1, 2], "")
c.check("the caller's tables are not changed", (small["Overall"]["Value"] != 0).any(), "")
dl = {k: s for k, s, _ in D.diff_tables(small, res["tables"])}
c.eq("diff statuses", dl, {"Overall": "changed", "Parameters": "unchanged", "New": "added"})
res = D.run_script("tables.pop('Parameters')\n", small)
c.check("a table can be dropped", res["ok"] and "Parameters" not in res["tables"], res["error"])
res = D.run_script("x = 1 / 0\n", small)
c.check("an error is reported with its line", not res["ok"] and "ZeroDivisionError" in res["error"] and "line 1" in res["error"], res["error"])
res = D.run_script("tables = 5\n", small)
c.check("tables must stay a dict", not res["ok"] and "dict" in res["error"], res["error"])
res = D.run_script("tables['x'] = 5\n", small)
c.check("every table must be a DataFrame", not res["ok"] and "DataFrame" in res["error"], res["error"])
res = D.run_script("import math, statistics\nprint(statistics.mean([1, 2, 3]) + math.pi)\n", small)
c.check("allowed imports work", res["ok"] and res["stdout"].startswith("5.14"), res["error"] + res["stdout"])
for code_, why in (("import os\n", "import os"), ("import subprocess\n", "subprocess"), ("from os import path\n", "from os"),
                   ("import numpy.lib\n", "submodule"), ("open('/etc/passwd')\n", "open"), ("eval('1')\n", "eval"),
                   ("x = ().__class__\n", "dunder attribute"), ("x = '__class__'\n", "dunder string"),
                   ("pd.read_csv('/etc/passwd')\n", "read_csv"), ("tables['Overall'].to_csv('/tmp/x.csv')\n", "to_csv"),
                   ("getattr(pd, 'x')\n", "getattr"), ("np.save('/tmp/x', [1])\n", "np.save"), ("__import__('os')\n", "__import__"),
                   ("global x\n", "global"), ("x = type(1)\n", "type")):
    try:
        D.run_script(code_, small)
        ok_ = False
    except D.DataError:
        ok_ = True
    c.check(f"refused: {why}", ok_, code_)
try:
    D.run_script("def f(:\n", small)
    ok_ = False
except D.DataError as e:
    ok_ = "syntax" in str(e)
c.check("a syntax error is reported", ok_, "")
res = D.run_script("while True:\n    pass\n", small, timeout=3)
c.check("an endless loop is stopped", not res["ok"] and res["seconds"] < 12, f"{res['error']} {res['seconds']:.1f}")
res = D.run_script("x = [0] * (10 ** 10)\n", small, timeout=10)
c.check("a runaway allocation is stopped", not res["ok"], res["error"])
# the Parameters table can be edited by a script and re-imported
res = D.run_script("p = tables['Parameters']\nmk = (p['Unit'] == 'VLV-100') & (p['Parameter'] == 'P_out')\np.loc[mk, 'Value'] = 19.0\n", small)
pl = D.plan(m, D.requests_from_frame(res["tables"]["Parameters"], m, D.KINDS[0])[0])
ch = [x for x in pl if x["status"] == "ok"]
c.eq("an edited Parameters table re-imports as exactly that change", [(x["unit"], x["key"], x["new"]) for x in ch], [("VLV-100", "P_out", 19.0)])

# ---- security review (v7.4.1): pandas / numpy must not be a door to os, io or sys
for code_ in ["pd.io.common.os.listdir('/')", "pd.io.common.io.open('/etc/hostname').read()", "np.os.system('id')",
              "df = tables['Overall']; df.style", "tables['Overall'].to_csv('/tmp/x')", "'{0._mgr}'.format(tables['Overall'])[0]; x = tables['Overall']._mgr",
              "(i for i in []).gi_frame.f_globals", "import string", "import os", "import pandas.io", "from numpy import lib"]:
    try:
        D.run_script(code_, small)
        ok_ = False
    except D.DataError:
        ok_ = True
    c.check("blocked by the code check: " + code_[:45], ok_, "")
_nocheck = D.check_code
D.check_code = lambda code: None          # bypass the AST rules to test the run-time layer on its own
try:
    for code_, what in [("pd.io", "pd.io hidden at run time"), ("np.os", "np.os hidden at run time"), ("from numpy import core", "numpy.core import refused"),
                        ("pd.read_csv('/etc/passwd')", "pd.read_csv refused at run time"), ("np.x = 1", "modules are read-only"),
                        ("import json; json.codecs", "stdlib sub-modules hidden")]:
        r_ = D.run_script(code_, small)
        c.check(what, not r_["ok"], r_["error"])
    r_ = D.run_script("print(np.random.default_rng(1).random() < 1, np.linalg.norm([3, 4]), pd.api.types.is_numeric_dtype(tables['Overall']['Value']))", small)
    c.check("legit numpy/pandas sub-modules still work", r_["ok"] and "True 5.0" in r_["stdout"], r_["error"] + r_["stdout"])
finally:
    D.check_code = _nocheck
r_ = D.run_script("import json\nprint(json.dumps({'a': 1}))\nprint('{:.2f}'.format(3.14159))\ntables['Overall'].info()", small)
c.check("json, str.format and df.info still work", r_["ok"], r_["error"])

c.report()
