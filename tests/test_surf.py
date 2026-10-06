"""Subsea (SURF) equipment: IPR/VLP, choke, template, jumpers, flowline presets and DEH, riser geometry and
slugging screen, SSIV/HIPPS, the catalogue, and balances on the SURF field example.

Run:  python tests/test_surf.py
"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from check import Checker                                                           # noqa: E402
from procsim.flowsheet import new_model, add_unit, connect, solve, port_edges       # noqa: E402
from procsim.examples import WET_GAS, WELL_FLUID, subsea_field                      # noqa: E402
from procsim.unitops import CATALOGUE, PROFILE_TYPES                                 # noqa: E402
from procsim import surf                                                             # noqa: E402

c = Checker("surf")
G = 9.80665


def chain(units, feed, keys=None):
    """feed -> units... -> product. units: [(type, params)]. Returns (model, [uids], sol)."""
    m = new_model(keys or list(feed["composition"].keys()))
    f = add_unit(m, "feed", params=feed)
    ids, prev = [], f
    for t, p in units:
        u = add_unit(m, t, params=p)
        connect(m, prev, "out", u, "in")
        ids.append(u)
        prev = u
    pr = add_unit(m, "product")
    connect(m, prev, "out", pr, "in")
    return m, ids, solve(m)


def balances(m, sol, label):
    fp = sol.fp
    en = {}
    for e in sol.energy:
        en[e.unit] = en.get(e.unit, 0.0) + e.duty_kW
    wm = we = 0.0
    for uid, u in m["units"].items():
        if u["type"] in ("feed", "product") or sol.status.get(uid) not in ("ok", "warning"):
            continue
        ins = [sol.streams[s] for lst in port_edges(m, uid, "in").values() for s in lst]
        outs = [sol.streams[s] for lst in port_edges(m, uid, "out").values() for s in lst]
        nin = sum((s.F * s.z for s in ins if not s.empty), np.zeros(fp.n))
        nout = sum((s.F * s.z for s in outs if not s.empty), np.zeros(fp.n))
        wm = max(wm, float(np.max(np.abs(nin - nout))) / max(nin.sum(), 1e-9))
        hin = sum(s.heat_flow_kW for s in ins if not s.empty)
        hout = sum(s.heat_flow_kW for s in outs if not s.empty)
        pe = 0.0
        if u["type"] in PROFILE_TYPES:
            dz = sol.results[uid].get("Elevation change [m]", u["params"].get("dz", 0.0))
            ins_pe = ins
            if u["type"] == "well":            # lift gas enters and leaves at the wellhead: no net potential energy
                ins_pe = [sol.streams[s] for s in port_edges(m, uid, "in").get("in", [])]
            pe = sum(x.F * x.MW for x in ins_pe if not x.empty) * G * dz / 1000.0 / 3600.0
        if u["type"] == "separator3":
            continue
        we = max(we, abs(hin + en.get(u["name"], 0.0) - pe - hout) / max(abs(hin), abs(hout), 1.0))
    c.close(f"{label}: component balance on every unit (rel.)", wm, 0.0, 1e-8)
    c.close(f"{label}: energy balance incl. reservoir heat, heat loss, DEH and elevation (rel.)", we, 0.0, 2e-5)


GAS = {"T_C": 110.0, "P_bar": 320.0, "flow_basis": "MSm³/d", "flow": 1.2, "composition": dict(WET_GAS)}
OIL = {"T_C": 90.0, "P_bar": 250.0, "flow_basis": "kmol/h", "flow": 600.0, "composition": dict(WELL_FLUID)}

# ---- registration ----------------------------------------------------------------------------------
for t in surf.SURF_TYPES:
    c.check(f"{t} registered in the catalogue with the SURF palette group",
            t in CATALOGUE and CATALOGUE[t]["category"] == "Subsea (SURF)", "")
c.check("profile-producing SURF types are exposed to the solver", set(surf.PROFILE_TYPES) <= set(PROFILE_TYPES), "")

# ---- well: back-pressure IPR + tubing -----------------------------------------------------------------
m, (w,), sol = chain([("well", {"C": 520.0, "n": 0.8, "MD": 3400.0, "TVD": 3000.0, "ID": 125.0})], GAS)
r = sol.results[w]
gas, oil, wat = surf.standard_rates(sol.streams[port_edges(m, w, "in")["in"][0]], sol.fp)
from procsim.thermo import T_STD                                                    # noqa: E402
fin = sol.streams[port_edges(m, w, "in")["in"][0]]
c.rel("well: gas rate = feed rate × vapour fraction at standard conditions (water and condensate drop out)", gas,
      1.2 * sol.fp.pt_flash(fin.z, T_STD, 1.01325).vf, 1e-9)
c.rel("back-pressure IPR: Pwf = √(Pr² − (q/C)^(1/n))", r["Bottomhole flowing P [bar(a)]"],
      math.sqrt(320.0 ** 2 - (gas * 1e6 / 520.0) ** (1 / 0.8)), 1e-10)
c.rel("AOF = C·Pr^2n", r["AOF gas [MSm³/d]"], 520.0 * 320.0 ** 1.6 / 1e6, 1e-10)
out = sol.streams[port_edges(m, w, "out")["out"][0]]
c.close("wellhead P reported = outlet stream P", r["Wellhead P [bar(a)]"], out.P, 1e-12)
c.check("tubing lifts against gravity: WHP < Pwf − 0 and > Pwf − liquid-column",
        r["Bottomhole flowing P [bar(a)]"] - 3000 * 800 * G / 1e5 < out.P < r["Bottomhole flowing P [bar(a)]"], "")
c.check("wellhead cooler than the reservoir with heat loss to the formation",
        out.T - 273.15 < 110.0 and r["Heat loss to formation [kW]"] > 0, "")
prof = sol.profiles.get(w)
c.check("well profile: z runs from 0 at the sandface to the TVD", prof and abs(prof["z"][0]) < 1e-12
        and abs(prof["z"][-1] - 3000.0) < 1e-6, "")
balances(m, sol, "well (gas IPR)")
# same tubing as a plain pipe fed at (Pwf, T_res) with the geothermal ambient: identical wellhead state
m2 = new_model(list(WET_GAS))
f2 = add_unit(m2, "feed", params=dict(GAS, P_bar=r["Bottomhole flowing P [bar(a)]"]))
p2 = add_unit(m2, "pipe", params={"length": 3400.0, "ID": 125.0, "rough": 0.045, "dz": 3000.0, "n_seg": 12,
                                  "heat": "Overall U to ambient", "U": 15.0, "T_amb": 110.0, "T_amb_out": 4.0})
pr2 = add_unit(m2, "product")
connect(m2, f2, "out", p2, "in")
connect(m2, p2, "out", pr2, "in")
s2 = solve(m2)
o2 = s2.streams[port_edges(m2, p2, "out")["out"][0]]
c.close("well VLP = the pipe model on the same tubing (wellhead P)", out.P, o2.P, 1e-6)
c.close("well VLP = the pipe model on the same tubing (wellhead T)", out.T, o2.T, 1e-6)
# rate above the AOF is refused
m, (w,), sol = chain([("well", {"C": 520.0, "n": 0.8})], dict(GAS, flow=6.0))
c.check("rate above the AOF is an error, not a result", sol.status[w] == "error" and "absolute open flow" in sol.errors[w],
        sol.errors.get(w))
# TVD > MD
m, (w,), sol = chain([("well", {"MD": 1000.0, "TVD": 1500.0})], GAS)
c.check("TVD larger than MD is rejected", sol.status[w] == "error", sol.errors.get(w))

# ---- well: PI and Vogel (oil) ---------------------------------------------------------------------------
m, (w,), sol = chain([("well", {"ipr": surf.IPR_PI, "PI": 40.0, "MD": 2600.0, "TVD": 2500.0, "ID": 150.0})], OIL)
r = sol.results[w]
q_liq = r["Oil/condensate rate [Sm³/d]"] + r["Water rate [Sm³/d]"]
c.rel("PI IPR: Pwf = Pr − q_liq/PI", r["Bottomhole flowing P [bar(a)]"], 250.0 - q_liq / 40.0, 1e-10)
c.check("oil well reports GOR and water cut", r.get("GOR [Sm³/Sm³]", 0) > 0 and 0 < r.get("Water cut [%]", 0) < 100, "")
balances(m, sol, "well (PI)")
m, (w,), sol = chain([("well", {"ipr": surf.IPR_VOGEL, "qmax": 3000.0, "MD": 2600.0, "TVD": 2500.0, "ID": 150.0})],
                     OIL)
r = sol.results[w]
x = r["Bottomhole flowing P [bar(a)]"] / 250.0
c.rel("Vogel IPR: q/qmax = 1 − 0.2x − 0.8x²", r["Oil/condensate rate [Sm³/d]"] / 3000.0, 1 - 0.2 * x - 0.8 * x * x,
      1e-9)

# ---- Xmas tree + choke --------------------------------------------------------------------------------
GH = dict(GAS, T_C=70.0, P_bar=180.0)
m, (xt,), sol = chain([("xmas_tree", {"tree": "Vertical Xmas tree (VXT)", "P_out": 120.0})], GH)
r = sol.results[xt]
c.close("tree: outlet pressure spec met", r["Outlet P [bar(a)]"], 120.0, 1e-12)
c.close("tree: catalogue valve ΔP applied (VXT 2.0 bar)", r["Tree valve ΔP [bar]"], 2.0, 1e-12)
c.close("tree: choke ΔP = WHP − tree ΔP − outlet P", r["Choke ΔP [bar]"], 180.0 - 2.0 - 120.0, 1e-9)
c.check("tree: JT cooling across the choke", r["ΔT across tree and choke [°C]"] < 0, "")
c.check("tree: 120/178 is sub-critical for gas", r["Choke flow"].startswith("Sub-critical"), r["Choke flow"])
balances(m, sol, "Xmas tree")
c.close("critical pressure ratio for k = 1.4 is 0.5283", surf.critical_ratio(1.4), 0.528282, 1e-6)
m, (xt,), sol = chain([("xmas_tree", {"spec": "Choke pressure drop", "dP": 120.0})], GH)
c.check("tree: a large choke ΔP gives critical (sonic) flow", sol.results[xt]["Choke flow"].startswith("Critical"),
        sol.results[xt]["Choke flow"])
m, (xt,), sol = chain([("xmas_tree", {"P_out": 179.5})], GH)
c.check("tree: outlet above WHP − tree ΔP is an error", sol.status[xt] == "error", sol.errors.get(xt))
m, (xt,), sol = chain([("xmas_tree", {"spec": "Choke pressure drop", "dP": 150.0})],
                      dict(GAS, T_C=25.0, P_bar=180.0))
c.check("tree: a cold, deep choke-down into hydrates is a warning", sol.status[xt] == "warning" and
        sol.results[xt]["Downstream hydrate margin [°C]"] < 0, sol.results[xt].get("Downstream hydrate margin [°C]"))

# ---- template --------------------------------------------------------------------------------------------
m = new_model(list(WET_GAS))
tmp = add_unit(m, "template", params={"template": "4-slot template"})
Ps = [150.0, 152.0, 149.0]
for P in Ps:
    f = add_unit(m, "feed", params=dict(GAS, T_C=60.0, P_bar=P))
    connect(m, f, "out", tmp, "in")
pr = add_unit(m, "product")
connect(m, tmp, "out", pr, "in")
sol = solve(m)
r = sol.results[tmp]
c.eq("template: slots used", r["Slots used"], "3/4")
c.close("template: inlet spread", r["Inlet pressure spread [bar]"], 3.0, 1e-9)
c.close("template: outlet = lowest inlet − header ΔP", r["Outlet P [bar(a)]"], 149.0 - r["Header ΔP [bar]"], 1e-12)
c.check("template: header ΔP positive", r["Header ΔP [bar]"] > 0, "")
balances(m, sol, "template")
for P in (150.0, 150.0):
    f = add_unit(m, "feed", params=dict(GAS, T_C=60.0, P_bar=P))
    connect(m, f, "out", tmp, "in")
sol = solve(m)
c.check("template: more inlets than slots is an error", sol.status[tmp] == "error" and "slots" in sol.errors[tmp],
        sol.errors.get(tmp))

# ---- jumper / spool / PLET ----------------------------------------------------------------------------------
GL = dict(GAS, T_C=60.0, P_bar=150.0, flow=4.0)
m, (j,), sol = chain([("jumper", {"kind": "Rigid M-shape jumper", "ID": 254.0})], GL)
r = sol.results[j]
c.close("jumper: catalogue K = 4 bends × 0.35 + 1.0", r["Total fittings K [-]"], 4 * 0.35 + 1.0, 1e-12)
c.close("jumper: total ΔP = friction/elevation + fittings", r["Pressure drop [bar]"],
        r["Friction + elevation ΔP [bar]"] + r["Fittings ΔP [bar]"], 1e-9)
c.check("jumper: fittings loss is significant", r["Fittings ΔP [bar]"] > 0.2 * r["Friction + elevation ΔP [bar]"], "")
c.close("jumper: catalogue length used", r["Length [m]"], 50.0, 1e-12)
balances(m, sol, "jumper")
m, (j,), sol = chain([("jumper", {"kind": "Rigid M-shape jumper", "ID": 254.0, "bends": 0.0, "length": 20.0})], GL)
c.close("jumper: overrides (0 bends, 20 m) replace the catalogue", sol.results[j]["Total fittings K [-]"], 1.0, 1e-12)
c.close("jumper: override length", sol.results[j]["Length [m]"], 20.0, 1e-12)

# ---- flowline presets and DEH ------------------------------------------------------------------------------
FL = {"length": 15000.0, "ID": 305.0, "n_seg": 8}
GF = dict(GAS, T_C=60.0, P_bar=150.0, flow=3.0)
outT = {}
for design in ("Bare carbon steel", "Wet insulation (multilayer PP)", "Pipe-in-pipe"):
    m, (fl,), sol = chain([("flowline", dict(FL, design=design))], GF)
    outT[design] = sol.results[fl]["Outlet T [°C]"]
c.check("flowline: better insulation, warmer arrival (bare < wet < PiP)",
        outT["Bare carbon steel"] < outT["Wet insulation (multilayer PP)"] < outT["Pipe-in-pipe"], str(outT))
m, (fl,), sol = chain([("flowline", dict(FL, design="Pipe-in-pipe", U=3.0))], GF)
c.close("flowline: a U override replaces the catalogue value", sol.results[fl]["U used [W/m²·K]"], 3.0, 1e-12)
m0, (fl0,), s0 = chain([("flowline", dict(FL, design="Electrically heated (DEH)"))], GF)
m1, (fl1,), s1 = chain([("flowline", dict(FL, design="Electrically heated (DEH)", deh="On"))], GF)
c.check("flowline: DEH on raises the arrival temperature",
        s1.results[fl1]["Outlet T [°C]"] > s0.results[fl0]["Outlet T [°C]"] + 1.0, "")
c.close("flowline: DEH power = W/m × length", s1.results[fl1]["DEH power [kW]"], 100.0 * 15000.0 / 1000.0, 1e-9)
balances(m1, s1, "flowline with DEH")
m, (fl,), sol = chain([("flowline", dict(FL, design="Pipe-in-pipe", deh="On"))], GF)
c.check("flowline: DEH on without any heat input is an error", sol.status[fl] == "error", sol.errors.get(fl))

# ---- riser geometry --------------------------------------------------------------------------------------
row_lw = surf.item("riser", "Lazy-wave (flexible)")
secs = surf.riser_sections(row_lw, 400.0, 2.2)
c.eq("lazy-wave riser has three sections (up to the hog bend, down to the sag bend, up)", len(secs), 3)
c.close("lazy-wave sections rise by the water depth in total", sum(dz for _, dz in secs), 400.0, 1e-9)
c.close("lazy-wave length = depth × length factor", sum(ln for ln, _ in secs), 880.0, 1e-9)
c.check("lazy-wave hog-to-sag section runs downhill", secs[1][1] < 0, str(secs))
c.eq("vertical riser is one section", surf.riser_sections(surf.item("riser", "Vertical (top-tensioned)"), 300, 1.0),
     [(300.0, 300.0)])
try:
    surf.riser_sections(row_lw, 400.0, 1.0)
    c.check("too-short lazy-wave is refused", False, "no error")
except Exception as e:
    c.check("too-short lazy-wave is refused", "length factor" in str(e), str(e))
GR = dict(GAS, T_C=45.0, P_bar=120.0, flow=3.0)
m, (rs,), sol = chain([("riser", {"rtype": "Lazy-wave (flexible)", "depth": 350.0, "ID": 254.0})], GR)
r = sol.results[rs]
pz = sol.profiles[rs]["z"]
c.close("riser profile ends at the water depth", pz[-1], 350.0, 1e-6)
c.check("lazy-wave profile has a dip (sag bend)", any(b < a - 1e-9 for a, b in zip(pz, pz[1:])), "")
c.close("riser length from the catalogue factor", r["Riser length [m]"], 350.0 * 2.2, 1e-9)
c.eq("profile arrays line up", len({len(sol.profiles[rs][k]) for k in ("L", "P", "T", "z", "HL", "vm", "regime")}), 1)
balances(m, sol, "lazy-wave riser")
m, (rs,), sol = chain([("riser", {"rtype": "Steel catenary riser (SCR)", "depth": 350.0, "ID": 254.0})], GR)
c.close("SCR length = 1.6 × depth", sol.results[rs]["Riser length [m]"], 560.0, 1e-9)

# ---- riser-base slugging screen ----------------------------------------------------------------------------
OILR = {"T_C": 40.0, "P_bar": 30.0, "flow_basis": "kmol/h", "flow": 200.0, "composition": dict(WELL_FLUID)}
RS = {"rtype": "Steel catenary riser (SCR)", "depth": 300.0, "ID": 203.0, "fl_len": 10000.0, "fl_incl": -1.0,
      "n_seg": 8}
m, (rs,), sol = chain([("riser", RS)], OILR)
r = sol.results[rs]
b = r["Bøe number (< 1: severe slugging possible) [-]"]
c.check("low-rate oil into a downhill flowline: stratified riser base, Bøe < 1, slugging flagged",
        r["Riser-base flow regime"] == "Segregated" and b < 1 and r["Riser-base slugging risk"].startswith("High")
        and sol.status[rs] == "warning", f"{r['Riser-base flow regime']} Bøe={b}")
# the Bøe number from its definition
fp = sol.fp
base = sol.streams[port_edges(m, rs, "in")["in"][0]]
top = sol.streams[port_edges(m, rs, "out")["out"][0]]
from procsim.unitops import pipe_gradient, _phase_split          # noqa: E402
_, d = pipe_gradient(fp, base, 0.203, 0.045e-3, math.radians(-1.0), "Beggs & Brill", 0.02)
_, dt = pipe_gradient(fp, top, 0.203, 0.045e-3, 0.0, "Beggs & Brill", 0.02)
rho_l = _phase_split(fp, base)[3]
c.rel("Bøe number = u_sg0 · P0 / (ρ_l g α L u_sl)", b,
      dt["vsg"] * top.P * 1e5 / (rho_l * G * (1 - d["HL"]) * 10000.0 * d["vsl"]), 1e-9)
m, (rs,), sol = chain([("riser", dict(RS, fl_incl=1.0))], OILR)
c.check("uphill flowline at the riser base: severe slugging not flagged",
        sol.results[rs]["Riser-base slugging risk"] == "Low", sol.results[rs]["Riser-base slugging risk"])

# ---- SSIV / HIPPS -------------------------------------------------------------------------------------------
m, (v,), sol = chain([("subsea_valve", {"kind": "SSIV"})], GL)
c.close("SSIV open: catalogue ΔP 0.3 bar", sol.results[v]["ΔP [bar]"], 0.3, 1e-12)
balances(m, sol, "SSIV open")
m, (v,), sol = chain([("subsea_valve", {"kind": "SSIV", "state": "Closed"})], GL)
c.check("SSIV closed: no flow downstream", sol.streams[port_edges(m, v, "out")["out"][0]].empty, "")
m, (v,), sol = chain([("subsea_valve", {"kind": "HIPPS valve set", "hipps": "On", "P_trip": 140.0})], GL)
c.check("HIPPS trips above its set pressure (warning, no flow)",
        sol.status[v] == "warning" and sol.streams[port_edges(m, v, "out")["out"][0]].empty, sol.results[v])
m, (v,), sol = chain([("subsea_valve", {"kind": "HIPPS valve set", "hipps": "On", "P_trip": 160.0})], GL)
c.close("HIPPS below trip: open with a margin", sol.results[v]["Margin to trip [bar]"], 10.0, 1e-9)

# ---- catalogue ----------------------------------------------------------------------------------------------
rows = surf.default_rows()
c.check("built-in catalogue covers every category", all(any(r["category"] == k for r in rows) for k in surf.CATEGORIES), "")
c.check("built-in catalogue is labelled illustrative", all("llustrative" in r["note"] for r in rows), "")
c.eq("catalogue CSV round-trips", surf.parse_csv(surf.to_csv(rows)), rows)
for bad, why in (("category,item\nwidget,X\n", "unknown category"), ("item,U\nA,1\n", "category"),
                 ("category,item\nflowline,A\n", "no rows"),
                 (surf.to_csv(rows) + "flowline,Pipe-in-pipe,,1\n", "twice"),
                 (surf.to_csv(rows).replace("Pipe-in-pipe,Carrier pipe with dry insulation in the annulus,1",
                                            "Pipe-in-pipe,Carrier pipe with dry insulation in the annulus,abc"),
                  "not a number")):
    try:
        surf.parse_csv(bad)
        c.check(f"bad catalogue rejected ({why})", False, "accepted")
    except ValueError as e:
        c.check(f"bad catalogue rejected ({why})", why in str(e), str(e))
custom = [dict(r) for r in rows]
next(r for r in custom if r["item"] == "Pipe-in-pipe")["U_W_m2K"] = 2.0
custom.append(dict(next(r for r in custom if r["item"] == "Pipe-in-pipe"), item="PiP high-spec", U_W_m2K=0.5))
m, (fl,), sol = chain([("flowline", dict(FL, design="Pipe-in-pipe"))], GF)
m["surf_catalogue"] = custom
sol = solve(m)
c.close("a catalogue stored in the model drives the solve", sol.results[fl]["U used [W/m²·K]"], 2.0, 1e-12)
c.check("new catalogue items appear in the property-view options",
        "PiP high-spec" in next(s for s in CATALOGUE["flowline"]["params"] if s["key"] == "design")["options"], "")
m["units"][fl]["params"]["design"] = "PiP high-spec"
c.close("a new catalogue item can be selected", solve(m).results[fl]["U used [W/m²·K]"], 0.5, 1e-12)
m.pop("surf_catalogue")
sol = solve(m)
c.check("back on the built-in catalogue the custom item is reported missing",
        sol.status[fl] == "error" and "not in the active SURF catalogue" in sol.errors[fl], sol.errors.get(fl))
c.check("built-in options restored",
        "PiP high-spec" not in next(s for s in CATALOGUE["flowline"]["params"] if s["key"] == "design")["options"], "")

# ---- SURF field example -------------------------------------------------------------------------------------
m = subsea_field()
sol = solve(m)
bad = {m["units"][u]["name"]: sol.errors.get(u) for u, s in sol.status.items() if s not in ("ok", "warning")}
c.check("SURF field example solves", not bad, str(bad))
balances(m, sol, "SURF field")
nin = sum(sol.streams[s].F * sol.streams[s].z for s, x in m["streams"].items() if m["units"][x["src"][0]]["type"] == "feed")
nout = sum(sol.streams[s].F * sol.streams[s].z for s, x in m["streams"].items() if m["units"][x["dst"][0]]["type"] == "product")
c.close("SURF field: overall component balance", float(np.max(np.abs(nin - nout)) / nin.sum()), 0.0, 1e-8)
tmp = next(u for u, x in m["units"].items() if x["type"] == "template")
c.eq("SURF field: 4 wells on the 4-slot template", sol.results[tmp]["Slots used"], "4/4")
arr = sol.streams[next(s for s, x in m["streams"].items() if x["name"] == "Topside arrival")]
c.check("SURF field: arrival above 60 bar and above the hydrate region (PiP)",
        arr.P > 60 and all(v.get("Min. hydrate margin along line [°C]", 1.0) > 0 for v in sol.results.values()), "")

# ==== Phase 2 ================================================================================================
from procsim import subsea_design as sd                                             # noqa: E402
from procsim.examples import subsea_field_boosted                                   # noqa: E402

# ---- subsea booster --------------------------------------------------------------------------------------------
MPF = {"T_C": 60.0, "P_bar": 40.0, "flow_basis": "kmol/h", "flow": 2000.0, "composition": dict(WELL_FLUID)}
m, (b,), sol = chain([("subsea_booster", {"btype": "Helico-axial multiphase pump", "dP": 40.0})], MPF)
r = sol.results[b]
fin = sol.streams[port_edges(m, b, "in")["in"][0]]
fout = sol.streams[port_edges(m, b, "out")["out"][0]]
c.close("booster: pressure boost met", fout.P - fin.P, 40.0, 1e-9)
c.close("booster: shaft power = F·ΔH", r["Shaft power [kW]"], fin.F * (fout.H - fin.H) / 3600.0, 1e-9)
c.close("booster: catalogue efficiency used (HAP 50 %)", r["Efficiency used [%]"], 50.0, 1e-12)
c.close("booster: electrical = shaft / motor efficiency", r["Electrical power [kW]"], r["Shaft power [kW]"] / 0.93, 1e-9)
gv = sum(ph.beta * ph.Vs for ph in fin.flash.phases if ph.kind == "V") / sum(ph.beta * ph.Vs for ph in fin.flash.phases)
c.close("booster: inlet GVF from the phase volumes", r["Inlet GVF [%]"], 100 * gv, 1e-9)
c.check("booster: multiphase feed inside the HAP window is not flagged", sol.status[b] == "ok", r.get("Warning"))
balances(m, sol, "subsea booster")
m, (b,), sol = chain([("subsea_booster", {"btype": "Helico-axial multiphase pump", "dP": 40.0, "eff": 100.0})], MPF)
fin = sol.streams[port_edges(m, b, "in")["in"][0]]
fout = sol.streams[port_edges(m, b, "out")["out"][0]]
c.close("booster at 100 % efficiency is isentropic", fout.S, fin.S, 1e-6 * max(1.0, abs(fin.S)))
GW = dict(GAS, T_C=50.0, P_bar=70.0, flow=3.0)
m, (b,), sol = chain([("subsea_booster", {"btype": "Helico-axial multiphase pump", "dP": 40.0})], GW)
c.check("booster: wet gas (GVF 99 %) outside the HAP window is a warning",
        sol.status[b] == "warning" and "GVF" in sol.results[b]["Warning"], sol.results[b].get("Warning"))
m, (b,), sol = chain([("subsea_booster", {"btype": "Wet-gas compressor", "dP": 40.0})], GW)
c.check("booster: the same gas suits a wet-gas compressor", sol.status[b] == "ok", sol.results[b].get("Warning"))
m, (b,), sol = chain([("subsea_booster", {"btype": "Wet-gas compressor", "dP": 90.0})], dict(GW, flow=12.0))
w_ = sol.results[b].get("Warning", "")
c.check("booster: boost above the machine maximum and power above rating are flagged",
        "exceeds the 60 bar" in w_ and "rated" in w_, w_)
m, (b,), sol = chain([("subsea_booster", {"spec": "Outlet pressure", "P_out": 30.0})], MPF)
c.check("booster: an outlet below the inlet is an error", sol.status[b] == "error", sol.errors.get(b))

# ---- equipment list & CAPEX -----------------------------------------------------------------------------------
m = subsea_field()
sol = solve(m)
items, tot = sd.equipment_list(m, sol)
cp = sd.capex_params(m)
fl_item = next(i for i in items if i["Item"] == "FL-100 Flowline")
c.close("CAPEX: flowline = cost/km × km × (ID/254)^0.7", fl_item["Equipment [MUSD]"], 4.0 * 25.0 * (305 / 254) ** 0.7, 1e-9)
c.close("CAPEX: installation = 60 % of equipment", fl_item["Installation [MUSD]"], 0.6 * fl_item["Equipment [MUSD]"], 1e-9)
c.eq("CAPEX: one well line per well at the D&C allowance", [i["Total [MUSD]"] for i in items if i["Group"] == "Wells"],
     [70.0] * 4)
base = tot["Subsea equipment & lines [MUSD]"] + tot["Installation [MUSD]"]
c.close("CAPEX: engineering = 12 % of equipment + installation", tot["Engineering & management [MUSD]"], 0.12 * base, 1e-9)
c.close("CAPEX: total = base + engineering + wells + contingency", tot["Total CAPEX [MUSD]"],
        (base + tot["Engineering & management [MUSD]"] + tot["Wells [MUSD]"]) * 1.25, 1e-9)
c.close("CAPEX: groups add up to equipment + installation + wells", sum(tot["by_group"].values()),
        base + tot["Wells [MUSD]"], 1e-9)
umb = next(i for i in items if i["Item"] == "Main umbilical")
c.close("CAPEX: umbilical length = flowline + riser", umb["Qty"], 25.0 + 0.77, 1e-9)
c.check("CAPEX: no power cable without boosters", not any(i["Item"] == "Power cable" for i in items), "")
m["capex"] = {"install_pct": 0.0, "cont_pct": 0.0, "eng_pct": 0.0}
_, t0 = sd.equipment_list(m, sol)
c.close("CAPEX: settings in the model are used (no installation, engineering, contingency)", t0["Total CAPEX [MUSD]"],
        t0["Subsea equipment & lines [MUSD]"] + t0["Wells [MUSD]"], 1e-9)
cust = [dict(r_) for r_ in surf.default_rows()]
next(r_ for r_ in cust if r_["item"] == "SSIV")["cost_MUSD"] = None
m["surf_catalogue"] = cust
surf.activate(cust)
_, t1 = sd.equipment_list(m, sol)
c.eq("CAPEX: items without a catalogue cost are listed", t1["missing_costs"], ["XV-100 SSIV"])
surf.activate(None)

# ---- umbilical & power ------------------------------------------------------------------------------------------
dp, v, Re = sd.tube_dp(20.0, 0.00953, 25000.0, 1100.0, 5.0)
hp = 128 * 5e-3 * 25000.0 * (20.0 / 1000 / 3600) / (math.pi * 0.00953 ** 4) / 1e5
c.check("tube: laminar flow (Re < 2000)", Re < 2000, f"Re={Re}")
c.rel("tube: friction ΔP = Hagen-Poiseuille in laminar flow", dp, hp, 2e-3)
m = subsea_field()
sol = solve(m)
um = sd.umbilical_design(m, sol)
meg = um["services"][0]
whp = max(sol.results[u]["Wellhead P [bar(a)]"] for u, x in m["units"].items() if x["type"] == "well")
c.close("umbilical: automatic delivery P = highest wellhead P + 10 bar", meg["Delivery P [bar(a)]"], whp + 10.0, 1e-9)
c.close("umbilical: topside P = delivery + friction − head", meg["Topside pump P [bar(a)]"],
        meg["Delivery P [bar(a)]"] + meg["Friction ΔP [bar]"] - meg["Hydrostatic head [bar]"], 1e-9)
c.close("umbilical: head = ρ g depth", meg["Hydrostatic head [bar]"], 1110.0 * G * 350.0 / 1e5, 1e-9)
k = [n for n, _ in sd.TUBES_MM].index(meg["Tube"])
smaller = sd.tube_dp(2000.0, sd.TUBES_MM[k - 1][1] / 1000.0, um["length_km"] * 1000.0, 1110.0, 30.0)
c.check("umbilical: the selected tube is the smallest that meets ΔP and velocity",
        meg["Friction ΔP [bar]"] <= 100.0 and (smaller[0] > 100.0 or smaller[1] > 3.0), str(smaller))
c.check("umbilical: no cable without boosters", um["cable"] is None, "")
up = sd.umbilical_params(m)
cab = sd.cable_design(3000.0, 30.0, up)
c.close("cable: I = P / (√3 V cos φ)", cab["Current [A]"], 3000e3 / (math.sqrt(3) * cab["Voltage [kV]"] * 1e3 * 0.9), 1e-9)
c.check("cable: selection meets ampacity and voltage drop",
        cab["ok"] and cab["Current [A]"] <= cab["Ampacity [A]"] and cab["Voltage drop [%]"] <= 8.0, str(cab))
c.check("cable: an impossible step-out is reported, not hidden", not sd.cable_design(40000.0, 150.0, up)["ok"], "")
m["umbilical"] = {"services": [{"Service": "MeOH", "Fluid": "Methanol", "Flow [L/h]": 100.0,
                                "Delivery P [bar(a)]": 250.0}], "length_km": 10.0}
um = sd.umbilical_design(m, sol)
c.check("umbilical: user services and length are used", len(um["services"]) == 1 and um["length_km"] == 10.0 and
        um["services"][0]["Delivery P basis"] == "specified", "")

# ---- boosted field example ---------------------------------------------------------------------------------------
m = subsea_field_boosted()
sol = solve(m)
bad = {m["units"][u]["name"]: sol.errors.get(u) for u, s_ in sol.status.items() if s_ not in ("ok", "warning")}
c.check("boosted field example solves", not bad, str(bad))
balances(m, sol, "boosted field")
bu = next(u for u, x in m["units"].items() if x["type"] == "subsea_booster")
c.check("boosted field: wet-gas compressor inside its window and rating", sol.status[bu] == "ok",
        sol.results[bu].get("Warning"))
items, tot = sd.equipment_list(m, sol)
c.check("boosted field: CAPEX has the booster, power cable and topside power",
        tot["by_group"]["Subsea boosting"] > 0 and any(i["Item"] == "Power cable" for i in items)
        and any(i["Item"] == "Topside power" for i in items), "")
um = sd.umbilical_design(m, sol)
c.check("boosted field: a power cable is sized for the booster", um["cable"] is not None and um["cable"]["ok"],
        str(um["cable"]))
c.close("boosted field: cable load = booster electrical power", um["booster_kW"],
        sol.results[bu]["Electrical power [kW]"], 1e-9)

# ---- tie-back screening ---------------------------------------------------------------------------------------
m = subsea_field()
sol = solve(m)
flu = next(u for u, x in m["units"].items() if x["type"] == "flowline")
rsu = next(u for u, x in m["units"].items() if x["type"] == "riser")
inlet = sol.streams[port_edges(m, flu, "in")["in"][0]]
rows = sd.tieback_screen(sol.fp, inlet, m["units"][flu]["params"], m["units"][rsu]["params"], (10.0, 25.0, 40.0),
                         (1.0,))
arr = sol.streams[next(s_ for s_, x in m["streams"].items() if x["name"] == "Topside arrival")]
p25 = next(r_["Arrival P [bar(a)]"] for r_ in rows if r_["Distance [km]"] == 25.0)
c.close("screening at the real distance and rate reproduces the flowsheet arrival (within 1 bar)", p25, arr.P, 1.0)
ps = [r_["Arrival P [bar(a)]"] for r_ in rows]
c.check("screening: arrival pressure falls with distance", ps[0] > ps[1] > ps[2], str(ps))
rows2 = sd.tieback_screen(sol.fp, inlet, m["units"][flu]["params"], m["units"][rsu]["params"], (25.0,), (1.4,))
c.check("screening: a higher rate arrives at a lower pressure", rows2[0]["Arrival P [bar(a)]"] < p25, "")
rows3 = sd.tieback_screen(sol.fp, inlet, m["units"][flu]["params"], m["units"][rsu]["params"], (25.0,), (1.0,),
                          boost_bar=30.0)
c.check("screening: a boost raises the arrival pressure", rows3[0]["Arrival P [bar(a)]"] > p25 + 10.0, "")
syn = [{"Rate factor": 1.0, "Distance [km]": d_, "Arrival P [bar(a)]": p_} for d_, p_ in ((10, 100.0), (20, 80.0), (30, 50.0))]
c.eq("max distance: linear interpolation to the minimum arrival P", sd.max_distance(syn, 1.0, 65.0), (25.0, "interpolated"))
c.eq("max distance: beyond the grid when every point passes", sd.max_distance(syn, 1.0, 40.0), (30, "beyond grid"))
syn[2]["Arrival P [bar(a)]"] = None
c.eq("max distance: lower bound before an infeasible point", sd.max_distance(syn, 1.0, 65.0), (20, "at least"))
c.eq("max distance: not feasible at the first point", sd.max_distance(syn, 1.0, 120.0), (None, "not feasible"))

# ---- catalogue: older files without boosters --------------------------------------------------------------------
old_rows = [r_ for r_ in surf.default_rows() if r_["category"] != "booster"]
txt = surf.to_csv(old_rows)
filled = surf.parse_csv(txt, fill_missing=True)
c.check("a catalogue without boosters is completed from the built-in one", any(r_["category"] == "booster" for r_ in filled)
        and surf.filled_categories(txt) == ["booster"], "")

# ==== Phase 3 ================================================================================================
from procsim import subsea_ops as so                                                # noqa: E402

# ---- slug correlations --------------------------------------------------------------------------------------------
c.close("slug-body holdup: 1 at rest", so.slug_body_holdup(0.0), 1.0, 1e-12)
c.close("slug-body holdup: 0.5 at vm = 8.66 m/s (Gregory et al.)", so.slug_body_holdup(8.66), 0.5, 1e-12)
c.close("Gregory & Scott slug frequency", so.slug_frequency(0.5, 2.0, 0.2),
        0.0226 * ((0.5 / (G * 0.2)) * (19.75 / 2.0 + 2.0)) ** 1.2, 1e-12)
c.close("Norris mean slug length for a 12-inch line", so.mean_slug_length(12 * 0.0254),
        math.exp(-26.8 + 28.5 * math.log(12.0) ** 0.1) * 0.3048, 1e-9)
c.close("mean slug length is at least 12 D", so.mean_slug_length(0.02), 0.24, 1e-12)
m, (fl,), sol = chain([("flowline", {"design": "Wet insulation (multilayer PP)", "length": 5000.0, "ID": 203.0})],
                      {"T_C": 40.0, "P_bar": 30.0, "flow_basis": "kmol/h", "flow": 1500.0, "composition": dict(WELL_FLUID)})
base = sol.streams[port_edges(m, fl, "out")["out"][0]]
h = so.hydrodynamic_slugs(sol.fp, base, 0.203, 4.5e-5, 0.0)
A_ = math.pi * 0.203 ** 2 / 4
c.close("1-in-1000 slug = mean × exp(3.09 × 0.5)", h["1-in-1000 slug length [m]"], h["Mean slug length [m]"] * math.exp(1.545), 1e-9)
c.close("slug volume = length × area × slug-body holdup", h["1-in-1000 slug volume [m³]"],
        h["1-in-1000 slug length [m]"] * A_ * h["Slug body holdup [-]"], 1e-9)
c.check("oil at 1500 kmol/h in 8 inch slugs (intermittent)", h["Slugging"], h["Flow regime"])

# ---- severe slug on a slugging oil riser ---------------------------------------------------------------------------
OILS = {"T_C": 40.0, "P_bar": 45.0, "flow_basis": "kmol/h", "flow": 200.0, "composition": dict(WELL_FLUID)}
m, (fl, rs), sol = chain([("flowline", {"design": "Wet insulation (multilayer PP)", "length": 10000.0, "ID": 203.0,
                                        "dz": -150.0}),
                          ("riser", {"rtype": "Steel catenary riser (SCR)", "depth": 300.0, "ID": 203.0,
                                     "fl_len": 10000.0, "fl_incl": -0.86})], OILS)
sa = so.slug_assessment(m, sol, 1.2)
c.eq("slug assessment: one flowline with its riser", [(r_["Flowline"], r_["Riser"]) for r_ in sa],
     [(m["units"][fl]["name"], m["units"][rs]["name"])])
sev = sa[0]["severe"]
c.check("low-rate oil: severe slug governs", sev["Risk"].startswith("High") and sa[0]["Governing slug"] == "severe riser-base slug",
        sa[0]["Governing slug"])
c.close("severe slug = riser fill + flowline penetration", sev["Severe slug volume [m³]"],
        sev["Riser fill volume [m³]"] + sev["Flowline penetration volume [m³]"], 1e-9)
c.close("riser fill = riser area × riser length", sev["Riser fill volume [m³]"],
        A_ * sol.results[rs]["Riser length [m]"], 1e-9)
c.close("design surge = 1.2 × governing slug", sa[0]["Design surge volume [m³]"], 1.2 * sev["Severe slug volume [m³]"], 1e-9)
# the penetration satisfies the gas balance
top = sol.streams[port_edges(m, rs, "out")["out"][0]]
_, d_ = pipe_gradient(sol.fp, base := sol.streams[port_edges(m, fl, "out")["out"][0]], 0.203, 4.5e-5, 0.0, "Beggs & Brill", 0.02)
_, dt_ = pipe_gradient(sol.fp, top, 0.203, 4.5e-5, 0.0, "Beggs & Brill", 0.02)
rho_l = _phase_split(sol.fp, base)[3]
P0, dP = top.P * 1e5, rho_l * G * 300.0
Vg = (1 - sol.results[fl]["Average liquid holdup [-]"]) * A_ * 10000.0
x_, Vr, ql, qg = sev["Flowline penetration volume [m³]"], sev["Riser fill volume [m³]"], d_["vsl"] * A_, dt_["vsg"] * A_
c.rel("penetration closes the flowline gas balance", (P0 + dP) * (Vg - x_), P0 * (Vg + qg * (Vr + x_) / ql), 1e-9)
c.close("build-up time = slug volume / liquid inflow", sev["Build-up time [min]"], sev["Severe slug volume [m³]"] / ql / 60.0, 1e-9)
m = subsea_field()
sol = solve(m)
sa = so.slug_assessment(m, sol)
c.check("gas-condensate field: gas inflow keeps up, no penetration, no governing slug",
        sa[0]["severe"]["Flowline penetration volume [m³]"] == 0.0 and sa[0]["Governing volume [m³]"] == 0.0, str(sa[0]["Governing slug"]))

# ---- turndown ---------------------------------------------------------------------------------------------------------
flu = next(u for u, x in m["units"].items() if x["type"] == "flowline")
rsu = next(u for u, x in m["units"].items() if x["type"] == "riser")
inlet = sol.streams[port_edges(m, flu, "in")["in"][0]]
rows, win = so.turndown_envelope(sol.fp, inlet, m["units"][flu]["params"], m["units"][rsu]["params"],
                                 (0.05, 0.3, 1.0, 1.3), P_min=60.0)
arr = sol.streams[next(s_ for s_, x in m["streams"].items() if x["name"] == "Topside arrival")]
r1 = next(r_ for r_ in rows if r_["Rate factor"] == 1.0)
ssiv_dp = next(sol.results[u]["ΔP [bar]"] for u, x in m["units"].items() if x["type"] == "subsea_valve")
c.close("turndown at the current rate reproduces the flowsheet arrival (+ the SSIV ΔP it leaves out)",
        r1["Arrival P [bar(a)]"] - ssiv_dp, arr.P, 0.05)
ps_ = [r_["Arrival P [bar(a)]"] for r_ in rows if r_["Rate factor"] >= 0.3]
c.check("turndown: above 30 % arrival P falls as the rate rises (friction-dominated)", ps_ == sorted(ps_, reverse=True),
        str(ps_))
c.check("turndown: at 5 % the riser's liquid head makes the arrival P lower than at 30 % (the U-curve)",
        rows[0]["Arrival P [bar(a)]"] < rows[1]["Arrival P [bar(a)]"], str([r_["Arrival P [bar(a)]"] for r_ in rows]))
lowest = rows[0]
c.check("turndown: at 5 % of the rate the line cools into the hydrate region",
        not lowest["Feasible"] and "hydrate margin" in lowest["Limits"], lowest["Limits"])
c.check("turndown: window contains the current rate and excludes 5 %",
        win["feasible"] and win["min"] == 0.3 and win["max"] >= 1.0 and "hydrate" in win["low limit"], str(win))
syn = [{"Rate factor": f_, "Gas rate [MSm³/d]": f_, "Feasible": ok_, "Limits": lim_, "Liquid inventory [m³]": inv_}
       for f_, ok_, lim_, inv_ in ((0.2, False, "hydrate margin", 90.0), (0.5, True, "", 80.0), (1.0, True, "", 60.0),
                                   (1.5, False, "erosional velocity", 50.0))]
w_ = so.operating_window(syn)
c.eq("operating window: limits and ramp-up sweep-out", (w_["min"], w_["max"], w_["low limit"], w_["high limit"],
                                                        w_["ramp-up surge [m³]"]), (0.5, 1.0, "hydrate margin", "erosional velocity", 20.0))
c.check("operating window: none feasible", not so.operating_window([dict(syn[0])])["feasible"], "")

# ---- field layout -------------------------------------------------------------------------------------------------
lay = so.field_layout(m, sol)
host = next(n_ for n_ in lay["nodes"] if n_["type"] == "host")
tmpl = next(n_ for n_ in lay["nodes"] if n_["type"] == "template")
Lr = sol.results[rsu]["Riser length [m]"]
d0 = math.sqrt(Lr ** 2 - 350.0 ** 2) / 1000.0
dist = math.hypot(tmpl["x"], tmpl["y"])
c.check("layout: host at the origin", (host["x"], host["y"]) == (0.0, 0.0), "")
c.close("layout: template at riser footprint + flowline + spool from the host", dist, d0 + 25.0 + 0.030, 1e-9)
wells_ = [n_ for n_ in lay["nodes"] if n_["type"] == "well"]
c.eq("layout: four well slots around the template", len(wells_), 4)
c.check("layout: slots on a ring around the template", len({round(math.hypot(w["x"] - tmpl["x"], w["y"] - tmpl["y"]), 9)
                                                          for w in wells_}) == 1, "")
c.check("layout: umbilical drawn", any(e["kind"] == "umbilical" for e in lay["edges"]), "")
m["layout"] = {"bearing": {rsu: 90.0}}
t2 = next(n_ for n_ in so.field_layout(m, sol)["nodes"] if n_["type"] == "template")
c.check("layout: a bearing of 90° puts the template due east", abs(t2["y"]) < 1e-9 and t2["x"] > 25.0, str(t2))
mb = subsea_field_boosted()
sb = solve(mb)
lb = so.field_layout(mb, sb)
bn = next(n_ for n_ in lb["nodes"] if n_["type"] == "subsea_booster")
tn = next(n_ for n_ in lb["nodes"] if n_["type"] == "template")
c.check("layout: a booster next to its template is drawn apart from it (the template stays with its wells)",
        "offset" in bn["label"] and "offset" not in tn["label"] and math.hypot(bn["x"] - tn["x"], bn["y"] - tn["y"]) > 0.5, "")

# ==== v5.3: deliverability, cool-down, booster maps, economics of ambient heat, scenarios =======================
from procsim import scenarios as SC                                                 # noqa: E402
from procsim.economics import compute as econ                                       # noqa: E402

# ---- well on a wellhead-pressure specification --------------------------------------------------------------------
m = subsea_field()
w1 = next(u for u, x in m["units"].items() if x["name"] == "W-1")
f1 = next(u for u, x in m["units"].items() if x["name"] == "Reservoir W-1")
m["units"][w1]["params"].update({"rate_spec": surf.RATE_WHP, "WHP": 170.0})
sol = solve(m)
r = sol.results[w1]
c.close("WHP spec: the well delivers the specified wellhead pressure", r["Wellhead P [bar(a)]"], 170.0, 0.05)
fin = sol.streams[port_edges(m, w1, "in")["in"][0]]
c.close("WHP spec: the solved rate is written back to the feed stream", fin.F, r["Molar rate [kmol/h]"], 1e-9)
c.check("WHP spec: the feed reports who set its rate", "W-1" in str(sol.results[f1].get("Rate set by")), str(sol.results[f1]))
c.check("WHP spec: a lower WHP than the feed-rate case means more gas", r["Gas rate [MSm³/d]"] > 1.2, "")
balances(m, sol, "WHP-specified well")
m["units"][w1]["params"]["WHP"] = 400.0
sol = solve(m)
c.check("WHP spec: an unreachable wellhead pressure is an error", sol.status[w1] == "error" and "no rate" in sol.errors[w1],
        sol.errors.get(w1))
m = subsea_field()
sol = solve(m)
w1 = next(u for u, x in m["units"].items() if x["name"] == "W-1")
fin = sol.streams[port_edges(m, w1, "in")["in"][0]]
pts = surf.deliverability_curve(m["units"][w1], fin, sol.fp)
p1 = next(p_ for p_ in pts if abs(p_["Molar rate [kmol/h]"] - fin.F) < 1e-6 * fin.F)
c.close("deliverability curve passes through the operating point (coarse increments, 0.5 bar)", p1["Wellhead P [bar(a)]"],
        sol.results[w1]["Wellhead P [bar(a)]"], 0.5)
okp = [p_["Wellhead P [bar(a)]"] for p_ in pts if p_["Wellhead P [bar(a)]"] is not None and p_["Molar rate [kmol/h]"] >= fin.F]
c.check("deliverability: above the operating rate WHP falls with rate", okp == sorted(okp, reverse=True), str(okp))

# ---- cool-down ----------------------------------------------------------------------------------------------------
cd = {x_["Line"]: x_ for x_ in so.cooldown(m, sol)}
fl_cd = cd["FL-100 Flowline"]
flu = next(u for u, x in m["units"].items() if x["type"] == "flowline")
pt = fl_cd["points"][0]
Th, T0 = pt["Hydrate T [°C]"], pt["Operating T [°C]"]
c.close("cool-down: no-touch = τ ln((T0 − Ta)/(Th − Ta))", pt["No-touch time [h]"],
        pt["Time constant [h]"] * math.log((T0 - 4.0) / (Th - 4.0)), 1e-9)
c.close("cool-down: settle-out pressure = mean operating pressure", pt["Shut-in P [bar(a)]"],
        sum(sol.profiles[flu]["P"]) / len(sol.profiles[flu]["P"]), 1e-9)
c.eq("cool-down: the critical point is the minimum no-touch time", fl_cd["No-touch time [h]"],
     min(q["No-touch time [h]"] for q in fl_cd["points"]))
D_ = 0.305
Cw = 7850 * 480 * math.pi * (D_ + 0.02) * 0.02
Ci = 0.5 * 1.7e6 * math.pi * (D_ + 0.04 + 0.04) * 0.04
c.check("cool-down: τ exceeds the steel + insulation part alone", pt["Time constant [h]"] * 3600 > (Cw + Ci) / (1.0 * math.pi * D_), "")
cv = fl_cd["curve"]
c.close("cool-down curve starts at the operating temperature", cv["T"][0], fl_cd["points"][[q["No-touch time [h]"] for q in fl_cd["points"]].index(fl_cd["No-touch time [h]"])]["Operating T [°C]"], 1e-9)
m2 = subsea_field()
m2["units"][flu]["params"]["design"] = "Wet insulation (multilayer PP)"
s2 = solve(m2)
c.check("cool-down: pipe-in-pipe holds longer than wet insulation",
        so.cooldown(m2, s2)[0]["No-touch time [h]"] < fl_cd["No-touch time [h]"], "")
m["cooldown"] = {"shutin": "Local operating"}
c.check("cool-down: local-pressure basis uses the local operating pressure",
        so.cooldown(m, sol)[0]["points"][0]["Shut-in P [bar(a)]"] == sol.profiles[flu]["P"][0], "")
from procsim.examples import subsea_tieback                                         # noqa: E402
mt = subsea_tieback(meg_kg_h=0.0)
st_ = solve(mt)
c.check("cool-down: an uninhibited line already in the hydrate region has zero no-touch time",
        min(x_["No-touch time [h]"] for x_ in so.cooldown(mt, st_)) == 0.0, "")

# ---- booster performance curve, parallel / series ----------------------------------------------------------------------
cur = surf.typical_booster_curve(1000.0, 40.0, 60.0)
i_ = cur["flow"].index(1000.0)
c.check("typical booster curve passes through the duty point", abs(cur["head"][i_] - 40.0) < 1e-9 and abs(cur["eff"][i_] - 60.0) < 1e-9, "")
c.close("typical booster curve: 30 % rise to shut-off shape", cur["head"][0], 40.0 * (1.3 - 0.3 * 0.45 ** 2), 1e-12)
mb = subsea_field_boosted()
sb = solve(mb)
bu = next(u for u, x in mb["units"].items() if x["type"] == "subsea_booster")
rb = sb.results[bu]
pb = mb["units"][bu]["params"]
pb["curve"] = surf.typical_booster_curve(rb["Flow per machine [m³/h]"], rb["Boost per machine [bar]"], rb["Efficiency used [%]"])
pb["N_design"], pb["speed"] = 3600.0, 3600.0
sb = solve(mb)
c.close("with a curve through the duty point, the speed to meet it is the design speed",
        sb.results[bu]["Speed to meet duty [rpm]"], 3600.0, 1e-3)
pb["spec"] = "Performance curve"
sb = solve(mb)
c.close("performance-curve mode reproduces the boost at design speed", sb.results[bu]["Pressure boost [bar]"], 55.0, 1e-6)
c.check("booster map stored for the solution", sb.maps.get(bu, {}).get("kind") == "booster", "")
pb["speed"] = 3240.0
sb = solve(mb)
r9 = sb.results[bu]
from procsim.unitops import CompressorCurve                                         # noqa: E402
cc = CompressorCurve(pb["curve"], 3600.0)
c.close("fan laws: boost at 90 % speed = curve at Q/0.9 × 0.81", r9["Pressure boost [bar]"],
        cc.at(r9["Flow per machine [m³/h]"], 3240.0)[0], 1e-9)
pb.update({"spec": "Pressure boost", "curve": {}, "n_par": 2, "n_ser": 2, "dP": 110.0})
sb = solve(mb)
r2 = sb.results[bu]
c.close("parallel machines split the flow", r2["Flow per machine [m³/h]"], r2["Actual inlet flow [m³/h]"] / 2, 1e-9)
c.close("series machines share the boost", r2["Boost per machine [bar]"], 55.0, 1e-9)
c.close("power per machine = total / 4", r2["Shaft power per machine [kW]"], r2["Shaft power [kW]"] / 4, 1e-9)
c.check("110 bar over two machines in series stays inside the 60 bar per-machine limit",
        "exceeds the 60 bar" not in r2.get("Warning", ""), r2.get("Warning"))
pb.update({"n_ser": 1})
sb = solve(mb)
c.check("110 bar in one stage is flagged (add a machine in series)", "machine in series" in sb.results[bu].get("Warning", ""),
        sb.results[bu].get("Warning"))

# ---- economics: ambient and reservoir heat are free, DEH is power -------------------------------------------------------
m = subsea_field()
sol = solve(m)
t_ = econ(m, sol)["totals"]
c.check("economics: reservoir heat and heat loss to the sea cost nothing",
        t_["Heating demand [kW]"] == 0 and t_["Cooling demand [kW]"] == 0 and t_["CO₂ emissions [t/y]"] == 0.0, str(t_))
flu = next(u for u, x in m["units"].items() if x["type"] == "flowline")
m["units"][flu]["params"].update({"design": "Electrically heated (DEH)", "deh": "On"})
sol = solve(m)
t_ = econ(m, sol)["totals"]
c.close("economics: DEH is charged as electric power", t_["Power demand [kW]"],
        sol.results[flu]["Electrical heating power [kW]"], 1e-9)
c.close("economics: DEH electrical power = heat / 60 % efficiency", sol.results[flu]["Electrical heating power [kW]"],
        sol.results[flu]["DEH power [kW]"] / 0.6, 1e-9)

# ---- scenarios ------------------------------------------------------------------------------------------------------
m = subsea_field()
sol = solve(m)
SC.save(m, sol, "Base")
k_ = m["scenarios"][0]["kpis"]
c.check("scenario KPIs include CAPEX, arrival P and no-touch time",
        k_["CAPEX [MUSD]"] > 0 and abs(k_["Arrival P [bar(a)]"] - sol.results[next(u for u, x in m["units"].items() if x["type"] == "riser")]["Outlet P [bar(a)]"]) < 1e-9
        and k_["No-touch time [h]"] > 0, str(k_))
c.check("scenario snapshot excludes other scenarios", "scenarios" not in m["scenarios"][0]["model"], "")
flu = next(u for u, x in m["units"].items() if x["type"] == "flowline")
m["units"][flu]["params"]["design"] = "Wet insulation (multilayer PP)"
sol2 = solve(m)
SC.save(m, sol2, "Wet insulation")
labels, cols = SC.comparison(m["scenarios"], SC.scenario_kpis(m, sol2))
c.eq("comparison columns: saved scenarios + current", list(cols), ["Base", "Wet insulation", "Current (unsaved)"])
c.check("comparison: wet insulation is cheaper and cools faster",
        cols["Wet insulation"]["CAPEX [MUSD]"] < cols["Base"]["CAPEX [MUSD]"] and
        cols["Wet insulation"]["No-touch time [h]"] < cols["Base"]["No-touch time [h]"], "")
SC.save(m, sol2, "Base")
c.eq("saving under an existing name replaces it", [x_["name"] for x_ in m["scenarios"]], ["Wet insulation", "Base"])
back = SC.restore(m, "Wet insulation")
c.check("restore gives the scenario's model with the scenario list", back["units"][flu]["params"]["design"] ==
        "Wet insulation (multilayer PP)" and len(back["scenarios"]) == 2, "")
SC.delete(m, "Base")
c.eq("delete a scenario", [x_["name"] for x_ in m["scenarios"]], ["Wet insulation"])

# ==== v5.4: heated flowlines, subsea processing equipment, new examples ===========================================
from procsim.examples import heated_oil_tieback, subsea_compression, subsea_separation      # noqa: E402

HOT = {"T_C": 60.0, "P_bar": 80.0, "flow_basis": "kmol/h", "flow": 1500.0, "composition": dict(WELL_FLUID)}
FLH = {"design": "Electrically heated (DEH)", "length": 20000.0, "ID": 254.0, "n_seg": 10}
# fixed electrical heating
m, (fl,), sol = chain([("flowline", dict(FLH, heating=surf.HEATING[1], heat_ctrl=surf.CTRL_FIXED, deh_W_m=80.0))], HOT)
r = sol.results[fl]
c.close("fixed heating: heat into the fluid = W/m × length", r["Heat into the fluid [kW]"], 80.0 * 20000.0 / 1000.0, 1e-9)
c.close("DEH electrical power = heat / 60 % (typical efficiency)", r["Electrical heating power [kW]"], 1600.0 / 0.6, 1e-9)
balances(m, sol, "flowline with fixed DEH")
c.close("economics: electrical heating charged as power", econ(m, sol)["totals"]["Power demand [kW]"],
        r["Electrical heating power [kW]"], 1e-9)
# hold a minimum temperature
m, (fl,), sol = chain([("flowline", dict(FLH, heating=surf.HEATING[1], heat_ctrl=surf.CTRL_HOLD, T_hold=30.0,
                                         q_max_W_m=150.0))], HOT)
r = sol.results[fl]
prof = sol.profiles[fl]
c.close("hold control: outlet held at the set temperature", r["Outlet T [°C]"], 30.0, 1e-6)
c.check("hold control: no node below the set temperature", min(prof["T"]) >= 30.0 - 1e-6, str(min(prof["T"])))
c.check("hold control: only part of the line needs heat", 0 < r["Heated length [m]"] < 20000.0, str(r["Heated length [m]"]))
c.close("hold control: Σ q_heat·dL = controlled heating", sum(prof["q_heat"]) * 2000.0 / 1000.0, r["Controlled heating [kW]"], 1e-9)
c.check("hold control: peak heat within the installed capacity", r["Peak heating [W/m]"] <= 150.0 + 1e-9, "")
balances(m, sol, "flowline holding 30 °C")
off = so.unheated_profile(m, sol, fl)
c.check("without heating the same line arrives colder than the set temperature", off["T"][-1] < 30.0, str(off["T"][-1]))
hl = so.heated_lines(m, sol)[0]
U_, D_ = r["U used [W/m²·K]"], 0.254
c.close("shut-in hold power = U·π·D·(T_set − T_sea)", hl["Hold power, shut-in [W/m]"], U_ * math.pi * D_ * (30.0 - 4.0), 1e-9)
C_ = hl["Line heat capacity [kJ/(m·K)]"] * 1000.0
c.close("heat-up time = −τ ln(1 − loss/q)", hl["Heat-up time from sea temperature [h]"],
        -C_ / (U_ * math.pi * D_) * math.log(1 - U_ * math.pi * D_ * 26.0 / 150.0) / 3600.0, 1e-9)
c.check("heat-up is impossible when the installed heating is below the hold power",
        math.isinf(so.heat_up_time(C_, U_, D_, 10.0, 4.0, 30.0)), "")
c.close("annual energy (continuous) = flowing power × hours + hold power × shutdown hours",
        hl["Annual heating energy (continuous) [MWh/y]"],
        r["Electrical heating power [kW]"] * 8400.0 / 1000.0 + hl["Electrical power to hold during shut-in [kW]"] * 96.0 / 1000.0, 1e-6)
m, (fl,), sol = chain([("flowline", dict(FLH, heating=surf.HEATING[1], heat_ctrl=surf.CTRL_HOLD, T_hold=30.0,
                                         q_max_W_m=20.0))], HOT)
c.check("hold control: too little installed heating is flagged", sol.status[fl] == "warning" and
        sol.results[fl]["Outlet T [°C]"] < 30.0, sol.results[fl].get("Warning"))
m, (fl,), sol = chain([("flowline", dict(FLH, heating=surf.HEATING[2], heat_ctrl=surf.CTRL_FIXED, deh_W_m=50.0))], HOT)
c.close("heat-traced PiP: 90 % efficiency", sol.results[fl]["Electrical heating power [kW]"], 1000.0 / 0.9, 1e-9)
m, (fl,), sol = chain([("flowline", dict(FLH, heating=surf.HEATING[3], heat_ctrl=surf.CTRL_FIXED, deh_W_m=50.0))], HOT)
t_ = econ(m, sol)
c.check("hot-water bundle: topside heater duty, charged as heating", "Electrical heating power [kW]" not in sol.results[fl]
        and abs(t_["totals"]["Heating demand [kW]"] - 1000.0 / 0.7) < 1e-6, str(t_["totals"]["Heating demand [kW]"]))
m, (fl,), sol = chain([("flowline", dict(FLH, deh="On", deh_W_m=80.0))], HOT)
c.close("v5 flowsheets with the DEH on/off switch still work", sol.results[fl]["DEH power [kW]"], 1600.0, 1e-9)

# ---- subsea separator ------------------------------------------------------------------------------------------
SEPF = {"T_C": 60.0, "P_bar": 40.0, "flow_basis": "kmol/h", "flow": 3000.0, "composition": dict(WELL_FLUID)}
m = new_model(list(WELL_FLUID))
f_ = add_unit(m, "feed", params=SEPF)
sp = add_unit(m, "subsea_separator", params={"sep_type": "Liquid-liquid separator (horizontal)"})
prods = {}
for port in ("vapour", "oil", "water"):
    prods[port] = add_unit(m, "product")
    connect(m, sp, port, prods[port], "in")
connect(m, f_, "out", sp, "feed")
sol = solve(m)
r = sol.results[sp]
wat_ = sol.streams[port_edges(m, sp, "out")["water"][0]]
c.check("liquid-liquid separator: water leaves on the water outlet", not wat_.empty and r["Water to reinjection [m³/d]"] > 0, "")
balances(m, sol, "subsea separator (3 outlets)")
D_m = r["Vessel ID [mm]"] / 1000.0
c.close("separator: horizontal vessel L = 4 D", r["Length (T/T) [m]"], 4.0 * D_m, 1e-9)
Pm = r["Design pressure [bar(a)]"] / 10.0
c.close("separator: ASME wall t = P D / (2 S E − 1.2 P) + 3 mm", r["Wall thickness incl. corrosion [mm]"],
        Pm * D_m / (2 * 138.0 - 1.2 * Pm) * 1000.0 + 3.0, 1e-9)
c.close("separator: design pressure = 1.1 × operating", r["Design pressure [bar(a)]"], 1.1 * r["Vessel P [bar(a)]"], 1e-9)
c.close("separator: liquid volume = liquid flow × residence time", r["Liquid hold-up volume [m³]"],
        r["Liquid flow [m³/h]"] / 60.0 * 5.0, 1e-9)
m["units"][sp]["params"]["sep_type"] = "Gas-liquid separator (vertical)"
sol = solve(m)
c.check("gas-liquid separator: all liquid on one outlet, water outlet empty",
        sol.streams[port_edges(m, sp, "out")["water"][0]].empty and
        sol.streams[port_edges(m, sp, "out")["oil"][0]].z[sol.fp.iw] > 0.1, "")
balances(m, sol, "gas-liquid subsea separator")

# ---- subsea cooler ---------------------------------------------------------------------------------------------
GC = dict(GAS, T_C=70.0, P_bar=100.0, flow=3.0)
m, (cl,), sol = chain([("subsea_cooler", {"spec": "Approach to sea temperature", "approach": 15.0, "T_sea": 4.0})], GC)
r = sol.results[cl]
c.close("cooler: outlet = sea + approach", r["Outlet T [°C]"], 19.0, 1e-9)
balances(m, sol, "subsea cooler")
A_ = r["Required area [m²]"]
m2, (cl2,), s2 = chain([("subsea_cooler", {"spec": "Cooler area", "area": A_, "T_sea": 4.0})], GC)
c.close("cooler: the reported area reproduces the outlet temperature", s2.results[cl2]["Outlet T [°C]"], 19.0, 1e-6)
c.check("cooler: heat to the sea costs nothing", econ(m, sol)["totals"]["Cooling demand [kW]"] == 0, "")
m, (cl,), sol = chain([("subsea_cooler", {"spec": "Outlet temperature", "T_out": 3.0, "T_sea": 4.0})], GC)
c.check("cooler: cannot cool below the sea temperature", sol.status[cl] == "error", sol.errors.get(cl))
m, (cl,), sol = chain([("subsea_cooler", {"spec": "Approach to sea temperature", "approach": 2.0})], GC)
c.check("cooler: cooling wet gas into the hydrate region is flagged", sol.status[cl] == "warning" and
        sol.results[cl]["Outlet hydrate margin [°C]"] < 0, sol.results[cl].get("Warning"))

# ---- pressure intensifier and chemical injection ---------------------------------------------------------------
MEOH = {"T_C": 6.0, "P_bar": 60.0, "flow_basis": "kg/h", "flow": 300.0, "comp_basis": "Mass fractions",
        "composition": {"MeOH": 1.0}}
keys = list(WET_GAS) + ["MeOH"]
m, (pi_,), sol = chain([("intensifier", {"spec": "Area ratio", "ratio": 2.0, "P_hyd": 207.0})], MEOH, keys)
r = sol.results[pi_]
c.close("intensifier: outlet = hydraulic P × ratio × 85 %", r["Outlet P [bar(a)]"], 207.0 * 2.0 * 0.85, 1e-9)
c.close("intensifier: hydraulic fluid = liquid × ratio / 95 %", r["Hydraulic fluid consumption [L/min]"],
        r["Liquid flow [L/h]"] / 60.0 * 2.0 / 0.95, 1e-9)
c.check("intensifier: hydraulic power drawn exceeds the power into the fluid",
        r["Hydraulic power drawn [kW]"] > r["Power into the fluid [kW]"] > 0, "")
balances(m, sol, "intensifier")
m, (pi_,), sol = chain([("intensifier", {"spec": "Outlet pressure", "P_out": 300.0, "P_hyd": 207.0})], MEOH, keys)
c.close("intensifier: area ratio for an outlet pressure", sol.results[pi_]["Area ratio [-]"], 300.0 / (207.0 * 0.85), 1e-9)
m, (pi_,), sol = chain([("intensifier", {"spec": "Outlet pressure", "P_out": 50.0})], MEOH, keys)
c.check("intensifier: outlet below the inlet is an error", sol.status[pi_] == "error", sol.errors.get(pi_))


def cimv_case(chem_flow, chem_P=150.0):
    mm = new_model(keys)
    pf = add_unit(mm, "feed", params=dict(GAS, T_C=8.0, P_bar=100.0, flow=3.0))
    cf = add_unit(mm, "feed", params=dict(MEOH, flow=chem_flow, P_bar=chem_P))
    ci = add_unit(mm, "cimv")
    pr = add_unit(mm, "product")
    connect(mm, pf, "out", ci, "in")
    connect(mm, cf, "out", ci, "chem")
    connect(mm, ci, "out", pr, "in")
    return mm, ci, solve(mm)


m0, ci0, s0 = cimv_case(1e-9)
m1, ci1, s1 = cimv_case(3000.0)
c.check("CIMV: methanol raises the hydrate margin", s1.results[ci1]["Hydrate margin downstream [°C]"] >
        s0.results[ci0]["Hydrate margin downstream [°C]"] + 2.0, "")
c.check("CIMV: methanol shows up in the water", s1.results[ci1]["Inhibitor in water [wt%]"] > 5.0, "")
balances(m1, s1, "chemical injection valve")
m2, ci2, s2 = cimv_case(300.0, chem_P=102.0)
c.check("CIMV: chemical below production P + valve ΔP is an error", s2.status[ci2] == "error" and "below" in s2.errors[ci2],
        s2.errors.get(ci2))

# ---- subsea pump / compressor types ---------------------------------------------------------------------------
c.check("subsea pump offers pumps only", "Wet-gas compressor" not in CATALOGUE["subsea_pump"]["params"][0]["options"]
        and "Water injection pump" in CATALOGUE["subsea_pump"]["params"][0]["options"], "")
c.check("subsea compressor offers compressors only", set(CATALOGUE["subsea_compressor"]["params"][0]["options"]) ==
        {"Wet-gas compressor", "Dry-gas centrifugal compressor"}, "")
m, (k_,), sol = chain([("subsea_compressor", {"btype": "Dry-gas centrifugal compressor", "dP": 40.0})],
                      dict(DRY := {"T_C": 30.0, "P_bar": 60.0, "flow_basis": "MSm³/d", "flow": 3.0,
                                   "composition": {"C1": 0.9, "C2": 0.07, "C3": 0.03}}))
c.check("dry-gas compressor on dry gas: within its window", sol.status[k_] == "ok", sol.results[k_].get("Warning"))

# ---- new examples ----------------------------------------------------------------------------------------------
for fn, name in ((heated_oil_tieback, "heated tie-back"), (subsea_compression, "compression station"),
                 (subsea_separation, "separation station")):
    m = fn()
    sol = solve(m)
    bad = {m["units"][u]["name"]: sol.errors.get(u) for u, s_ in sol.status.items() if s_ not in ("ok", "warning")}
    c.check(f"{name} example solves", not bad, str(bad))
    balances(m, sol, name)
    if fn is heated_oil_tieback:
        fl = next(u for u, x in m["units"].items() if x["type"] == "flowline")
        c.close("heated tie-back: the flowline delivers 25 °C", sol.results[fl]["Outlet T [°C]"], 25.0, 1e-6)
        items_, tot_ = sd.equipment_list(m, sol)
        c.check("heated tie-back: CAPEX has the heating system and topside power",
                tot_["by_group"]["Flowline heating"] > 0 and any(i_["Item"] == "Topside power" for i_ in items_), "")
        c.check("heated tie-back: heated-lines view", so.heated_lines(m, sol)[0]["Can hold the line during shut-in"] == "yes", "")
    if fn is subsea_compression:
        k_ = next(u for u, x in m["units"].items() if x["type"] == "subsea_compressor")
        c.close("compression station: dry gas to the compressor (GVF 100 %)", sol.results[k_]["Inlet GVF [%]"], 100.0, 1e-6)
        lay = so.field_layout(m, sol)
        c.eq("compression station layout: 4 well slots (compressor and pump are not slots)",
             sum(1 for n_ in lay["nodes"] if n_["type"] == "well"), 4)
        c.check("compression station layout: separator, compressor and pump placed",
                {"subsea_separator", "subsea_compressor", "subsea_pump"} <= {n_["type"] for n_ in lay["nodes"]}, "")
    if fn is subsea_separation:
        inj = next(s_ for s_, x in m["streams"].items() if m["units"][x["dst"][0]]["name"] == "Water to injection well")
        c.check("separation station: water to injection at 230 bar", abs(sol.streams[inj].P - 230.0) < 1e-6 and
                sol.streams[inj].z[sol.fp.iw] > 0.95, "")

# ---- import order ------------------------------------------------------------------------------------------
import subprocess                                                                   # noqa: E402
for first in ("procsim.surf", "procsim.unitops", "ui.surf"):
    r_ = subprocess.run([sys.executable, "-c", f"import sys; sys.path[:0] = ['tests/stubs', '.']; import {first}; "
                         "from procsim.unitops import CATALOGUE, CALC; assert 'riser' in CATALOGUE and 'riser' in CALC"],
                        cwd=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."), capture_output=True, text=True)
    c.check(f"SURF units register whichever module is imported first ({first})", r_.returncode == 0, r_.stderr[-300:])

# ---- v6: identical wells in parallel, booster bypass -------------------------------------------------------------
import copy as _copy   # noqa: E402
m = subsea_field()
s1 = solve(m)
w1 = next(u for u, x in m["units"].items() if x["name"] == "W-1")
fid = m["streams"][port_edges(m, w1, "in")["in"][0]]["src"][0]
m2 = _copy.deepcopy(m)
m2["units"][w1]["params"]["n_par"] = 3
m2["units"][fid]["params"]["flow"] = m["units"][fid]["params"]["flow"] * 3
s2 = solve(m2)
r1, r2 = s1.results[w1], s2.results[w1]
c.close("3 identical wells at 3× the rate: same wellhead pressure", r2["Wellhead P [bar(a)]"], r1["Wellhead P [bar(a)]"], 1e-9)
c.close("3 identical wells: same bottomhole pressure", r2["Bottomhole flowing P [bar(a)]"], r1["Bottomhole flowing P [bar(a)]"], 1e-9)
c.close("3 identical wells: 3× the gas rate", r2["Gas rate [MSm³/d]"], 3 * r1["Gas rate [MSm³/d]"], 1e-9)
c.close("3 identical wells: per-well rate reported", r2["Gas rate per well [MSm³/d]"], r1["Gas rate [MSm³/d]"], 1e-9)
balances(m2, s2, "field with a 3-well cluster")
items2 = {i["Item"]: i["Qty"] for i in sd.equipment_list(m2, s2)[0]}
c.eq("equipment list: 3 wells and 3 trees for the cluster", (items2["W-1"], items2["XT-1"]), (3, 3))
mb = subsea_field_boosted()
bu = next(u for u, x in mb["units"].items() if x["type"] in surf.BOOSTER_TYPES)
mb["units"][bu]["params"]["online"] = surf.BYPASSED
sbp = solve(mb)
rb = sbp.results[bu]
sin = sbp.streams[port_edges(mb, bu, "in")["in"][0]]
sout = sbp.streams[port_edges(mb, bu, "out")["out"][0]]
c.check("bypassed booster: status reported", rb["Status"].startswith("Bypassed"), rb.get("Status"))
c.close("bypassed booster: no power", rb["Electrical power [kW]"], 0.0, 0.0)
c.close("bypassed booster: outlet P = inlet P", sout.P, sin.P, 1e-12)
c.close("bypassed booster: outlet T = inlet T", sout.T, sin.T, 1e-12)
balances(mb, sbp, "boosted field, booster bypassed")

# ---- v6: the hydrate model choice reaches every hydrate margin -----------------------------------------------------
from procsim.transport import VDWP   # noqa: E402
m = subsea_tieback()
s_m = solve(m)
m["fluid"]["hydrate_model"] = VDWP
s_v = solve(m)
fl_ = next(u for u, x in m["units"].items() if x["type"] in ("flowline", "pipe"))
hm_m, hm_v = s_m.results[fl_].get("Min. hydrate margin along line [°C]"), s_v.results[fl_].get("Min. hydrate margin along line [°C]")
c.check("vdW-P hydrate model: the line margin is computed", hm_v is not None, "")
c.check("vdW-P vs Motiee line margin within 3 K", hm_m is not None and abs(hm_v - hm_m) < 3.0, f"{hm_m} vs {hm_v}")
c.check("the hydrate model changes the result (not ignored)", hm_v != hm_m, "")
balances(m, s_v, "MEG tie-back with the vdW-P hydrate model")
cd_v = so.cooldown(m, s_v)
c.check("cool-down uses the selected hydrate model", bool(cd_v) and all(r["No-touch time [h]"] >= 0 for r in cd_v), "")

# ---- v6.2: gas lift, injection well, seabed route --------------------------------------------------------------
from procsim.examples import gaslift_injection   # noqa: E402
m = gaslift_injection()
s1 = solve(m)
c.check("gas-lift / injection example solves", all(v in ("ok", "warning") for v in s1.status.values()),
        str({m["units"][k]["name"]: s1.errors.get(k) for k, v in s1.status.items() if v not in ("ok", "warning")}))
balances(m, s1, "gas lift + injection example")
p1 = next(u for u, x in m["units"].items() if x["name"] == "P-1")
r = s1.results[p1]
c.check("lift gas enters above the tubing pressure at the valve", r["Lift gas at the valve [bar(a)]"] > r["Tubing P at the valve [bar(a)]"], "")
m0 = _copy.deepcopy(m)
for x in m0["units"].values():
    if x["name"].startswith("Lift gas"):
        x["params"]["flow"] = 0.0
s0 = solve(m0)
c.check("gas lift raises the wellhead pressure (lighter column)", r["Wellhead P [bar(a)]"] > s0.results[p1]["Wellhead P [bar(a)]"] + 5.0,
        f"{r['Wellhead P [bar(a)]']:.1f} vs {s0.results[p1]['Wellhead P [bar(a)]']:.1f}")
c.close("without lift gas the well is the plain tubing model", s0.results[p1]["Bottomhole flowing P [bar(a)]"],
        r["Bottomhole flowing P [bar(a)]"], 1e-9)
out = s1.streams[port_edges(m, p1, "out")["out"][0]]
lin = s1.streams[port_edges(m, p1, "in")["lift"][0]]
rin = s1.streams[port_edges(m, p1, "in")["in"][0]]
c.close("well outlet = reservoir + lift gas (moles)", out.F, rin.F + lin.F, 1e-9)
m_lo = _copy.deepcopy(m)
lg = next(x for x in m_lo["units"].values() if x["name"] == "Lift gas P-1")
lg["params"]["P_bar"] = 40.0
s_lo = solve(m_lo)
c.check("lift gas below the tubing pressure at the valve is an error", s_lo.status[p1] == "error", s_lo.errors.get(p1))
iw = next(u for u, x in m["units"].items() if x["type"] == "injection_well")
ri = s1.results[iw]
c.close("injector: required BHP = P_res + q / II", ri["Required bottomhole P [bar(a)]"],
        290.0 + ri["Injection rate per well [Sm³/d]"] / 25.0, 1e-9)
c.close("injector: margin = BHP − required", ri["Injection margin [bar]"],
        ri["Bottomhole P [bar(a)]"] - ri["Required bottomhole P [bar(a)]"], 1e-9)
c.check("injector: water gains ~ρgh down the tubing", 240.0 < ri["Hydrostatic + friction gain [bar]"] < 270.0,
        f"{ri['Hydrostatic + friction gain [bar]']:.1f}")
m_w = _copy.deepcopy(m)
next(x for x in m_w["units"].values() if x["type"] == "subsea_pump")["params"]["P_out"] = 40.0
s_w = solve(m_w)
c.check("injector: too low a wellhead pressure gives a warning", "too low" in (s_w.results[iw].get("Warning") or ""), "")
flu = next(u for u, x in m["units"].items() if x["type"] == "flowline")
c.eq("route: low points counted", s1.results[flu]["Low points along the route"], 2)
c.close("route: length = sum of the section lengths", s1.results[flu]["Route length [m]"],
        sum(ln for ln, _ in surf.route_sections(m["units"][flu]["params"])), 1e-9)
c.close("route: elevation change = start depth − end depth", s1.results[flu]["Elevation change [m]"], 310.0 - 290.0, 1e-9)
c.eq("route: low points (deepest first)", surf.route_low_points(m["units"][flu]["params"]), [335.0, 325.0])
c.close("route length used by CAPEX", sd.line_lengths_km(m, s1)[0], s1.results[flu]["Route length [m]"] / 1000.0, 1e-12)
mf = subsea_field()
sf_ = solve(mf)
fl0 = next(u for u, x in mf["units"].items() if x["type"] == "flowline")
pf = mf["units"][fl0]["params"]
L, dz = pf["length"] / 1000.0, pf.get("dz", 0.0)
mf2 = _copy.deepcopy(mf)
mf2["units"][fl0]["params"]["route"] = [[0.0, 400.0], [math.sqrt((L * 1000) ** 2 - dz ** 2) / 1000.0, 400.0 - dz]]
sf2 = solve(mf2)
c.close("a straight two-point route reproduces the flowline", sf2.results[fl0]["Outlet P [bar(a)]"],
        sf_.results[fl0]["Outlet P [bar(a)]"], 1e-6)

# ---- v6.2: choke Cv, power system --------------------------------------------------------------------------------
mc = subsea_field()
for x in mc["units"].values():
    if x["type"] == "xmas_tree":
        x["params"].update({"spec": surf.CHOKE_CV, "Cv_max": 120.0, "opening": 70.0, "rangeability": 50.0})
sc_ = solve(mc)
xt1 = next(u for u, x in mc["units"].items() if x["name"] == "XT-1")
c.close("equal-percentage trim: Cv = Cv_max · R^(opening − 1)", sc_.results[xt1]["Choke Cv at this opening [US gpm/psi½]"],
        120.0 * 50.0 ** (0.7 - 1.0), 1e-9)
c.check("choke Cv spec gives a positive pressure drop", sc_.results[xt1]["Choke ΔP [bar]"] > 0.0, "")
mc2 = _copy.deepcopy(mc)
next(x for x in mc2["units"].values() if x["name"] == "XT-1")["params"]["opening"] = 50.0
sc2 = solve(mc2)
c.check("closing the choke raises its pressure drop", sc2.results[xt1]["Choke ΔP [bar]"] > sc_.results[xt1]["Choke ΔP [bar]"], "")
sin = sc_.streams[port_edges(mc, xt1, "in")["in"][0]]
dp_c, _ = surf.choke_dp(sin, sin.P - 2.0, 1e6)
c.check("a huge Cv gives almost no pressure drop", dp_c < 0.01, f"{dp_c:.4f}")
balances(mc, sc_, "field with Cv-specified chokes")
mb2 = subsea_field_boosted()
sb2 = solve(mb2)
ud = sd.umbilical_design(mb2, sb2)
c.check("22 kV transmission gets a subsea step-down transformer", ud["cable"]["Voltage [kV]"] > 6.6 and
        ud["cable"].get("Subsea step-down transformer [MVA]", 0) > 0, str(ud["cable"]))
c.check("transformer in the CAPEX list", any(i["Item"] == "Subsea transformer" for i in sd.equipment_list(mb2, sb2)[0]), "")
ps = sd.power_supply_options(mb2, sb2)
gt, pfs = ps["rows"]
c.check("power from shore emits less CO₂ than gas turbines", pfs["CO₂ [kt/y]"] < gt["CO₂ [kt/y]"], "")
ann = sum(1.0 / 1.08 ** (k + 0.5) for k in range(20))
c.close("discounted cost = CAPEX + annuity × (energy + CO₂)", gt["Discounted cost over the period [MUSD]"],
        gt["CAPEX [MUSD]"] + ann * (gt["Energy cost [MUSD/y]"] + gt["CO₂ cost [MUSD/y]"]), 1e-9)
mh = heated_oil_tieback()
sh = solve(mh)
uh = sd.umbilical_design(mh, sh)
c.check("electrical heating alone gets a power cable", uh["cable"] is not None and uh["cable"]["Load: electrical heating [kW]"] > 0, "")

sys.exit(c.report())
