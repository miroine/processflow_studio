"""Topside checks (v7.1): gas-quality properties against published heating values, separator gas-capacity check,
the debottlenecking engine (utilisation, crossing, sweep) and the six topside examples (HP compressor bypass,
debottlenecking, two trains, water reinjection, gas blending, flare system).

Run:  python tests/test_topside.py      (about 1-2 minutes)
"""
import copy
import math
import os
import sys
import time

import numpy as np

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
from check import Checker                                # noqa: E402
from procsim import examples as EX                       # noqa: E402
from procsim import examples_topside as TS               # noqa: E402
from procsim import debottleneck as DB                   # noqa: E402
from procsim import gasquality as GQ                     # noqa: E402
from procsim.flowsheet import solve, new_model, add_unit, connect   # noqa: E402
from procsim.streams import stream_properties            # noqa: E402

c = Checker("topside")
t0 = time.time()


def unit(m, sol, name):
    uid = next(u for u, d in m["units"].items() if d["name"] == name)
    return uid, sol.results.get(uid) or {}


def stream(m, sol, name):
    sid = next(s for s, d in m["streams"].items() if d["name"] == name)
    return sol.streams[sid]


def clean(m, sol):
    return not [u for u, s in sol.status.items() if s in ("error", "missing", "unsolved")]


# ---- gas quality ------------------------------------------------------------------------------------------------
m0 = solve(TS.gas_blending())
fp = m0.fp


def z_of(comp):
    z = np.zeros(len(fp.keys))
    for k, v in comp.items():
        z[fp.keys.index(k)] = v
    return z


for nm, comp, gcv, rd in (("methane", {"C1": 1.0}, 37.67, 0.5539), ("ethane", {"C2": 1.0}, 66.0, 1.0382),
                          ("propane", {"C3": 1.0}, 93.86, 1.5224)):
    q = GQ.quality(fp, z_of(comp))
    c.rel(f"{nm}: gross calorific value (15 °C, ideal gas) matches the published value", q["GCV (dry) [MJ/Sm³]"], gcv, 0.004)
    c.rel(f"{nm}: relative density = MW / 28.965", q["Relative density (air = 1)"], rd, 0.002)
    c.rel(f"{nm}: Wobbe = GCV / sqrt(relative density)", q["Wobbe index [MJ/Sm³]"], q["GCV (dry) [MJ/Sm³]"] / math.sqrt(q["Relative density (air = 1)"]), 1e-12)
mix = GQ.quality(fp, z_of({"C1": 0.9, "C2": 0.1}))
c.rel("GCV of a mixture is the mole-weighted mean (ideal gas)", mix["GCV (dry) [MJ/Sm³]"],
      0.9 * GQ.quality(fp, z_of({"C1": 1.0}))["GCV (dry) [MJ/Sm³]"] + 0.1 * GQ.quality(fp, z_of({"C2": 1.0}))["GCV (dry) [MJ/Sm³]"], 1e-9)
c.check("inerts lower the GCV", GQ.quality(fp, z_of({"C1": 0.9, "CO2": 0.1}))["GCV (dry) [MJ/Sm³]"] < 0.91 * 37.67, "")
c.close("CO2 content is reported in mol% (dry)", GQ.quality(fp, z_of({"C1": 0.95, "CO2": 0.05}))["CO₂ (dry) [mol%]"], 5.0, 1e-9)
wet = GQ.quality(fp, z_of({"C1": 0.9, "N2": 0.1}))
c.check("an empty / water-only composition has no quality", GQ.quality(fp, np.zeros(len(fp.keys))) is None, "")
st_liq = None
mw = solve(TS.water_reinjection())
c.check("water streams carry no gas-quality values",
        stream_properties(stream(TS.water_reinjection(), mw, "Treated seawater"), mw.fp)["GCV (dry) [MJ/Sm³]"] is None, "")
c.check("a hydrocarbon-gas stream has them",
        stream_properties(stream(TS.gas_blending(), m0, "Sales gas"), fp)["Wobbe index [MJ/Sm³]"] > 40.0, "")

# ---- separator gas-capacity check ---------------------------------------------------------------------------------
def sep_case(ID):
    m = new_model(["C1", "C2", "C3", "H2O"])
    f = add_unit(m, "feed", 0, 0, "F", {"T_C": 30.0, "P_bar": 30.0, "flow_basis": "MSm³/d", "flow": 2.0,
                                       "composition": {"C1": 0.85, "C2": 0.08, "C3": 0.05, "H2O": 0.02}})
    v = add_unit(m, "separator", 150, 0, "V", {"ID": ID} if ID else {})
    connect(m, f, "out", v, "feed")
    connect(m, v, "vapour", add_unit(m, "product", 300, 0, "G"), "in")
    connect(m, v, "liquid", add_unit(m, "product", 300, 100, "L"), "in")
    s = solve(m)
    return unit(m, s, "V")[1]


r0, r1, r2 = sep_case(0.0), sep_case(2000.0), sep_case(1000.0)
c.check("separator without a diameter: no load result (unchanged)", "Gas load [% of max]" not in r0, "")
c.check("separator with a diameter: load and selected diameter reported", "Gas load [% of max]" in r1 and r1["Selected diameter [mm]"] == 2000.0, str(list(r1)))
c.rel("gas load scales with 1/ID²", r2["Gas load [% of max]"], 4.0 * r1["Gas load [% of max]"], 1e-9)
c.rel("load = velocity / maximum velocity", r1["Gas load [% of max]"], 100.0 * r1["Actual gas velocity [m/s]"] / r1["Max gas velocity [m/s]"], 1e-9)
c.check("an undersized separator warns", r2["Gas load [% of max]"] > 100 and "carry-over" in str(r2.get("Warning")), str(r2.get("Warning")))
c.check("a large enough separator does not warn", "Warning" not in r1, str(r1.get("Warning")))

# ---- crossing -----------------------------------------------------------------------------------------------------
f_, how = DB.crossing([0.8, 1.0, 1.2], [60.0, 80.0, 100.0 + 20.0])
c.close("crossing: interpolated factor", f_, 1.1, 1e-12)
c.eq("crossing: how", how, "interpolated")
f_, how = DB.crossing([0.8, 1.0, 1.2], [50.0, 60.0, 70.0])
c.close("crossing: extrapolated from the last two points", f_, 1.2 + (100.0 - 70.0) / 10.0 * 0.2, 1e-12)
c.eq("crossing: how (beyond)", how, "extrapolated")
f_, how = DB.crossing([0.8, 1.0, 1.2], [110.0, 120.0, 130.0])
c.eq("crossing: already over at the lowest factor", (f_, how), (0.8, "already over"))
c.eq("crossing: a falling utilisation never crosses", DB.crossing([0.8, 1.0], [50.0, 40.0]), (None, "none"))
c.close("crossing: None values are skipped", DB.crossing([0.8, 1.0, 1.2, 1.4], [60.0, None, 90.0, 110.0])[0], 1.3, 1e-12)

# ---- debottlenecking example --------------------------------------------------------------------------------------
md = TS.debottlenecking()
sd = solve(md)
rows = DB.utilisation(md, sd)
c.check("debottlenecking example solves cleanly", clean(md, sd) and sd.converged, str(sd.errors))
kinds = {r["kind"] for r in rows}
c.eq("all five kinds of limit are checked", kinds, {"Gas load", "Stonewall", "Driver", "Valve opening", "Erosional"})
c.eq("rows are sorted by utilisation", [r["Utilisation [%]"] for r in rows], sorted((r["Utilisation [%]"] for r in rows), reverse=True))
c.check("everything is below its limit at the base rate", max(r["Utilisation [%]"] for r in rows) < 100.0, str(rows[0]["Utilisation [%]"]))
c.check("the status follows the utilisation", all(r["Status"] == ("over capacity" if r["Utilisation [%]"] > 100 else "near the limit" if r["Utilisation [%]"] >= 90 else "ok") for r in rows), "")
k1, rk1 = unit(md, sd, "K-100 LP compressor")
row = next(r for r in rows if r["Unit"] == "K-100 LP compressor" and r["kind"] == "Driver")
c.rel("driver utilisation = shaft power / available power", row["Utilisation [%]"], 100.0 * rk1["Power [kW]"] / rk1["Driver power available [kW]"], 1e-9)
row = next(r for r in rows if r["Unit"] == "K-100 LP compressor" and r["kind"] == "Stonewall")
c.rel("stonewall utilisation = 100 - stonewall margin", row["Utilisation [%]"], 100.0 - rk1["Stonewall margin [%]"], 1e-9)
kv, rv = unit(md, sd, "VLV-100 Export control valve")
row = next(r for r in rows if r["kind"] == "Valve opening")
c.rel("valve utilisation = opening / 85 %", row["Utilisation [%]"], 100.0 * rv["Valve opening [%]"] / 85.0, 1e-9)
ks, rs = unit(md, sd, "V-101 Interstage scrubber")
row = next(r for r in rows if r["Unit"] == "V-101 Interstage scrubber")
c.rel("scrubber utilisation = gas load", row["Utilisation [%]"], rs["Gas load [% of max]"], 1e-12)
m_nid = copy.deepcopy(md)
next(d for d in m_nid["units"].values() if d["name"] == "V-101 Interstage scrubber")["params"]["ID"] = 0.0
c.check("a scrubber without a diameter is not checked",
        not [r for r in DB.utilisation(m_nid, solve(m_nid)) if r["Unit"] == "V-101 Interstage scrubber"], "")

ms = DB.scaled_model(md, 1.25)
c.rel("scaled model: feed flow multiplied", ms["units"][next(u for u, d in ms["units"].items() if d["type"] == "feed")]["params"]["flow"], 3.75, 1e-12)
c.eq("scaled model: original untouched", md["units"][next(u for u, d in md["units"].items() if d["type"] == "feed")]["params"]["flow"], 3.0)
sw = DB.sweep(md, (1.0, 1.2, 1.4))
base_rows = {(r["Unit"], r["Check"]): r["Utilisation [%]"] for r in rows}
ok = all(abs(s["util"][0] - base_rows[(s["Unit"], s["Check"])]) < 1e-6 for s in sw["series"])
c.check("the sweep at 1.0 reproduces the base utilisation", ok, "")
c.check("utilisation rises with the rate for every check", all(s["util"][0] < s["util"][1] < s["util"][2] for s in sw["series"]), str([s["util"] for s in sw["series"]][:2]))
first = sw["series"][0]
c.check("the first limit is a compressor driver", first["kind"] == "Driver" and 1.0 < first["Limit reached at (× base)"] < 1.3, str(first["Unit"]) + str(first["Limit reached at (× base)"]))
c.check("limits are ranked by factor", [s["Limit reached at (× base)"] for s in sw["series"]] == sorted(s["Limit reached at (× base)"] for s in sw["series"]), "")
# the limit factor is where the interpolated utilisation hits 100 %: re-solve there
fx = first["Limit reached at (× base)"]
sol_x = solve(DB.scaled_model(md, fx))
ux = next(r["Utilisation [%]"] for r in DB.utilisation(DB.scaled_model(md, fx), sol_x) if r["Unit"] == first["Unit"] and r["Check"] == first["Check"])
c.close("re-solving at the predicted factor gives 100 % utilisation (interpolation is accurate)", ux, 100.0, 1.5)
txt = " ".join(DB.summary(sw))
c.check("summary names the first limit and its headroom", first["Unit"] in txt and "headroom" in txt, txt[:200])
c.check("summary groups limits that are within 3 % of each other", "limits are" in txt, txt[:200])
c.check("summary proposes a remedy", "driver" in txt.lower(), txt[:300])


def failing(m):
    if m["units"][next(u for u, d in m["units"].items() if d["type"] == "feed")]["params"]["flow"] > 4.0:
        raise RuntimeError("did not converge")
    return solve(m)


sw2 = DB.sweep(md, (1.0, 1.5), solver=failing)
c.check("a solver failure at one rate is reported and the others still count", "1.5" in sw2["fails"] and len(sw2["series"]) > 0, str(sw2["fails"]))
c.check("failed rate leaves None in the series", all(s["util"][1] is None for s in sw2["series"]), "")
sw_f = DB.sweep(md, (1.0, 1.2), feeds=[])
c.check("scaling no feed: nothing changes, nothing crosses", all(abs(s["util"][0] - s["util"][1]) < 1e-9 for s in sw_f["series"]) and len(sw_f["series"]) > 0, "")
c.check("empty result summary is explicit", "No checked item" in DB.summary({"series": [], "fails": {}})[0], "")

# ---- HP compressor bypass -----------------------------------------------------------------------------------------
mb = TS.hp_compressor_bypass()
sb = solve(mb)
c.check("HP bypass example solves cleanly", clean(mb, sb) and sb.converged, str(sb.errors))
gas = stream(mb, sb, "Gas to split")
kin, kout = stream(mb, sb, "To compressor"), stream(mb, sb, "Discharge")
byp = stream(mb, sb, "Bypass gas to the MP header (70 bar)")
c.rel("70 % of the gas goes to the compressor", kin.F, 0.7 * gas.F, 1e-9)
c.rel("30 % goes through the bypass valve", byp.F, 0.3 * gas.F, 1e-9)
c.close("compressor discharge at 130 bar", kout.P, 130.0, 1e-6)
c.close("bypass gas let down to 70 bar", byp.P, 70.0, 1e-6)
c.check("the bypass cools by Joule-Thomson expansion", byp.T < gas.T, f"{byp.T - gas.T:.1f}")
for frac in ("1.0", "0.0"):
    m2 = copy.deepcopy(mb)
    next(d for d in m2["units"].values() if d["type"] == "splitter")["params"]["fractions"] = frac
    s2 = solve(m2)
    c.check(f"split {frac}: solves without errors", clean(m2, s2), str(s2.errors))
m2 = copy.deepcopy(mb)
next(d for d in m2["units"].values() if d["type"] == "splitter")["params"]["fractions"] = "0.0"
s2 = solve(m2)
c.close("full bypass: the compressor sees no flow", stream(m2, s2, "To compressor").F, 0.0, 1e-9)
c.rel("full bypass: the bypass valve takes everything", stream(m2, s2, "Bypass gas to the MP header (70 bar)").F, stream(m2, s2, "Gas to split").F, 1e-9)
m2 = copy.deepcopy(mb)
next(d for d in m2["units"].values() if d["type"] == "splitter")["params"]["fractions"] = "1.0"
s2 = solve(m2)
c.close("full compression: nothing in the bypass", stream(m2, s2, "Bypass gas to the MP header (70 bar)").F, 0.0, 1e-9)
c.check("full compression needs more power than 70 %", unit(m2, s2, "K-100 HP compressor")[1]["Power [kW]"] > 1.3 * unit(mb, sb, "K-100 HP compressor")[1]["Power [kW]"], "")

# ---- two parallel trains ------------------------------------------------------------------------------------------
mt = TS.two_trains()
stt = solve(mt)
c.check("two-trains example solves cleanly and converges", clean(mt, stt) and stt.converged, str(stt.errors))
fa, fb = stream(mt, stt, "Train A feed"), stream(mt, stt, "Train B feed")
c.rel("55 / 45 split", fa.F / (fa.F + fb.F), 0.55, 1e-9)
c.check("both recycle loops converge", all((unit(mt, stt, n)[1].get("Converged") == "Yes") for n in ("RCY-A", "RCY-B")), "")
ga, gb, ex = stream(mt, stt, "A gas"), stream(mt, stt, "B gas"), stream(mt, stt, "Export gas")
c.rel("export gas is the sum of the two trains", ex.F, ga.F + gb.F, 1e-9)
c.check("train A (larger share) takes more power", unit(mt, stt, "K-A00 Compressor A")[1]["Power [kW]"] > unit(mt, stt, "K-B00 Compressor B")[1]["Power [kW]"], "")
m2 = copy.deepcopy(mt)
next(d for d in m2["units"].values() if d["type"] == "splitter")["params"]["fractions"] = "1.0"
s2 = solve(m2)
c.check("train B out of service (split 1.0): solves", clean(m2, s2), str(s2.errors))
c.close("train B out of service: B gas is zero", stream(m2, s2, "B gas").F, 0.0, 1e-9)
rows_t = DB.utilisation(mt, stt)
c.check("both trains appear in the utilisation table", {r["Unit"] for r in rows_t} >= {"K-A00 Compressor A", "K-B00 Compressor B", "V-A01 Scrubber A", "V-B01 Scrubber B"}, "")

# ---- water handling and reinjection -------------------------------------------------------------------------------
mw = TS.water_reinjection()
sw_ = solve(mw)
c.check("water example solves cleanly", clean(mw, sw_), str(sw_.errors))
deg = stream(mw, sw_, "Degassed water")
reinj, ob = stream(mw, sw_, "To reinjection"), stream(mw, sw_, "Overboard water")
c.rel("85 % of the degassed water is reinjected", reinj.F, 0.85 * deg.F, 1e-9)
c.rel("15 % goes overboard", ob.F, 0.15 * deg.F, 1e-9)
inj = stream(mw, sw_, "Water into the reservoir")
c.rel("water into the reservoir = reinjected + seawater make-up", inj.F, reinj.F + stream(mw, sw_, "Treated seawater").F, 1e-9)
pu = unit(mw, sw_, "P-100 Water injection pump")[1]
c.rel("pump power = volume flow x pressure rise / efficiency",
      pu["Power [kW]"], pu["Actual vol flow [m³/h]"] / 3600.0 * pu["Pressure rise [bar]"] * 1e5 / 0.75 / 1000.0, 0.03)
wi = unit(mw, sw_, "WI-1 Water injectors")[1]
c.check("the wells inject at the pump pressure with margin", wi["Injection margin [bar]"] > 10.0, str(wi["Injection margin [bar]"]))
c.rel("injection rate = water into the reservoir", wi["Injection rate [Sm³/d]"], inj.F * 18.015 / 998.0 * 24.0, 0.03)
m2 = copy.deepcopy(mw)
next(d for d in m2["units"].values() if d["type"] == "pump")["params"]["P_out"] = 40.0
s2 = solve(m2)
c.check("a pump discharge that is too low gives an injection warning", "Warning" in unit(m2, s2, "WI-1 Water injectors")[1], "")
c.check("water-free degasser gas: nothing but traces", stream(mw, sw_, "Degasser flash gas").F < 0.01 * deg.F, "")

# ---- gas blending --------------------------------------------------------------------------------------------------
mg = TS.gas_blending()
sg = solve(mg)
c.check("blending example solves cleanly and the Adjust converges", clean(mg, sg) and sg.converged, str(sg.errors))
adj = next(r for u, r in sg.results.items() if mg["units"][u]["type"] == "adjust")
c.check("the Adjust hit its target", abs(adj["Error"]) <= 0.005, str(adj))
sales = stream(mg, sg, "Sales gas")
pr = stream_properties(sales, sg.fp)
c.close("sales-gas Wobbe index 50.5", pr["Wobbe index [MJ/Sm³]"], 50.5, 0.005)
c.check("sales gas inside the 47-52 window and CO₂ below 2.5 mol%", 47.0 < pr["Wobbe index [MJ/Sm³]"] < 52.0 and pr["CO₂ (dry) [mol%]"] < 2.5, str(pr["CO₂ (dry) [mol%]"]))
fl_in = [stream(mg, sg, n) for n in ("A at header", "B at header", "C at header")]
c.rel("blend flow = sum of the three gases", sales.F, sum(s.F for s in fl_in), 1e-9)
q = [stream_properties(s, sg.fp)["GCV (dry) [MJ/Sm³]"] for s in fl_in]
c.rel("blend GCV = flow-weighted mean of the three gases", pr["GCV (dry) [MJ/Sm³]"], sum(qq * s.F for qq, s in zip(q, fl_in)) / sales.F, 1e-6)
c.close("all three gases are at the header pressure", sales.P, 45.0, 1e-6)
rich0 = next(d for d in mg["units"].values() if d["name"] == "Rich gas (field B)")["params"]["flow"]
c.check("the rich gas is the tuning variable (changed by the Adjust)", abs(sg.adjusted and list(sg.adjusted.values())[0][2] - 0.4) > 0.1, str(sg.adjusted))
m2 = copy.deepcopy(mg)
next(d for d in m2["units"].values() if d["name"] == "CO2-rich gas (field C)")["params"]["flow"] = 1.2
s2 = solve(m2)
c.check("more CO₂-rich gas breaks the CO₂ limit", stream_properties(stream(m2, s2, "Sales gas"), s2.fp)["CO₂ (dry) [mol%]"] > 2.5, "")

# ---- flare system -------------------------------------------------------------------------------------------------
mf = TS.flare_system()
sf = solve(mf)
c.check("flare example solves with no error or warning", clean(mf, sf) and not [u for u, v in sf.status.items() if v == "warning"], str(sf.status))
hp_in = [stream(mf, sf, n).F for n in ("PSV-100 outlet", "PSV-101 outlet", "Purge / pilot gas")]
fl_hp = unit(mf, sf, "FL-HP HP flare")[1]
c.rel("HP flare gas = both relief loads + the purge", fl_hp["Flared gas [kg/h]"], sum(s.F * s.MW for s in [stream(mf, sf, n) for n in ("PSV-100 outlet", "PSV-101 outlet", "Purge / pilot gas")]), 1e-6)
hdr = unit(mf, sf, "PIPE-HP HP header")[1]
c.check("header pressure falls along the line", hdr["Outlet P [bar(a)]"] < 3.5, str(hdr["Outlet P [bar(a)]"]))
c.check("the header velocity is below the erosional limit", hdr["Erosional velocity ratio (API RP 14E, C=100)"] < 1.0, "")
c.check("PSVs report an orifice and a relieving area", all("Selected orifice" in unit(mf, sf, n)[1] for n in ("PSV-100 HP separator", "PSV-101 Compressor discharge", "PSV-200 LP separator")), "")
m2 = copy.deepcopy(mf)
next(d for d in m2["units"].values() if d["name"] == "PSV-200 LP separator")["params"]["Kb"] = 1.0
s2 = solve(m2)
c.check("PSV-200 is balanced bellows: a conventional valve would get the back-pressure note",
        "back pressure" in str(unit(m2, s2, "PSV-200 LP separator")[1].get("Warning")) and "Warning" not in unit(mf, sf, "PSV-200 LP separator")[1], "")
c.check("flares report flame length and radiation", all(k in fl_hp for k in ("Flame length [m]", "Radiation at the receptor [kW/m²]")), "")
rows_f = DB.utilisation(mf, sf)
c.check("both flare headers appear in the utilisation table (erosional)", {r["Unit"] for r in rows_f} == {"PIPE-HP HP header", "PIPE-LP LP header"}, str({r["Unit"] for r in rows_f}))
m2 = copy.deepcopy(mf)
next(d for d in m2["units"].values() if d["name"] == "HP separator relief gas")["params"]["flow"] = 9000.0
s2 = solve(m2)
c.check("a larger HP relief load raises the header erosional ratio", unit(m2, s2, "PIPE-HP HP header")[1]["Erosional velocity ratio (API RP 14E, C=100)"] > hdr["Erosional velocity ratio (API RP 14E, C=100)"] * 2.0, "")

# ---- registry -----------------------------------------------------------------------------------------------------
names = [n for n in EX.EXAMPLES if "(topside)" in n]
c.eq("six topside examples are registered", len(names), 6)
c.check("every topside example builds, solves and has no error", all(clean(EX.EXAMPLES[n](), solve(EX.EXAMPLES[n]())) for n in names), "")

print(f"total {time.time() - t0:.0f} s")
sys.exit(c.report())
