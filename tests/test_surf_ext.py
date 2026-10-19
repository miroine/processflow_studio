"""v7.8 subsea equipment: choke module, manifold, multiphase flow meter, pig station, hydrocyclone, electric heater.

Run:  python tests/test_surf_ext.py
"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from check import Checker                                                           # noqa: E402
from procsim.flowsheet import new_model, add_unit, connect, solve, port_edges       # noqa: E402
from procsim.examples import WELL_FLUID, OIL_WC                                      # noqa: E402
from procsim.unitops import CATALOGUE                                                # noqa: E402
from procsim import surf, subsea_design                                              # noqa: E402

c = Checker("surf_ext")
NEW = ("subsea_choke", "subsea_manifold", "mpfm", "pig_station", "subsea_hydrocyclone", "subsea_heater")
OIL = {"T_C": 80.0, "P_bar": 120.0, "flow_basis": "kmol/h", "flow": 800.0, "composition": dict(WELL_FLUID)}


def chain(units, feed=OIL):
    m = new_model(list(feed["composition"]))
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


for t in NEW:
    c.check(f"{t} registered, SURF palette, in SURF_TYPES", t in CATALOGUE and CATALOGUE[t]["category"] == surf.CATEGORY and t in surf.SURF_TYPES, "")

# ---- choke
m, (k,), sol = chain([("subsea_choke", {"spec": "Pressure drop", "dP": 30.0})])
r = sol.results[k]
c.close("choke: pressure drop spec", r["ΔP [bar]"], 30.0, 1e-9)
c.check("choke: isenthalpic (outlet cools or stays)", r["ΔT (Joule-Thomson) [°C]"] <= 1e-6, str(r["ΔT (Joule-Thomson) [°C]"]))
m, (k,), sol = chain([("subsea_choke", {"spec": "Opening (Cv)", "Cv_max": 300.0, "opening": 50.0})])
r1 = sol.results[k]
m, (k,), sol = chain([("subsea_choke", {"spec": "Opening (Cv)", "Cv_max": 300.0, "opening": 80.0})])
r2 = sol.results[k]
c.check("choke: opening more -> smaller ΔP", r2["ΔP [bar]"] < r1["ΔP [bar]"], f"{r1['ΔP [bar]']} {r2['ΔP [bar]']}")
m, (k,), sol = chain([("subsea_choke", {"spec": "Outlet pressure", "P_out": 70.0})])
c.close("choke: outlet pressure spec", sol.results[k]["Outlet P [bar(a)]"], 70.0, 1e-9)

# ---- manifold: two branches in, component balance, erosional check
m = new_model(list(WELL_FLUID))
f1 = add_unit(m, "feed", params=OIL)
f2 = add_unit(m, "feed", params=dict(OIL, T_C=60.0, P_bar=110.0))
mf = add_unit(m, "subsea_manifold", params={"dP": 0.5, "ID": 254.0})
pr = add_unit(m, "product")
connect(m, f1, "out", mf, "in")
connect(m, f2, "out", mf, "in")
connect(m, mf, "out", pr, "in")
sol = solve(m)
r = sol.results[mf]
c.eq("manifold: two branches flowing", r["Branches flowing"], 2)
c.close("manifold: header P = lowest inlet − dP", r["Header P [bar(a)]"], 109.5, 1e-6)
fin = sum(sol.streams[s].F for lst in port_edges(m, mf, "in").values() for s in lst)
fout = sum(sol.streams[s].F for lst in port_edges(m, mf, "out").values() for s in lst)
c.close("manifold: mass (mole) balance", fout, fin, 1e-9)
m2 = new_model(list(WELL_FLUID))
a = add_unit(m2, "feed", params=dict(OIL, flow=40000.0))
b = add_unit(m2, "subsea_manifold", params={"ID": 60.0})
p2 = add_unit(m2, "product")
connect(m2, a, "out", b, "in")
connect(m2, b, "out", p2, "in")
r = solve(m2).results[b]
c.check("manifold: small header gives an erosional warning", "erosional" in str(r.get("Warning", "")), str(r.get("Warning")))

# ---- MPFM
m, (q,), sol = chain([("mpfm", {"dP": 0.4})])
r = sol.results[q]
c.close("mpfm: pressure drop", r["ΔP [bar]"], 0.4, 1e-9)
c.check("mpfm: reports gas, oil and water rates", r["Gas rate [MSm³/d]"] > 0 and r["Oil rate [Sm³/d]"] > 0 and r["Water rate [Sm³/d]"] > 0, str(r))
c.check("mpfm: WLR between 0 and 100", 0 < r["Water-liquid ratio [%]"] < 100, "")
c.close("mpfm: liquid = oil + water", r["Liquid rate [Sm³/d]"], r["Oil rate [Sm³/d]"] + r["Water rate [Sm³/d]"], 1e-9)
m, (q,), sol = chain([("mpfm", {"gvf_max": 50.0})], dict(OIL, P_bar=20.0))
c.check("mpfm: GVF above the window warns", "GVF" in str(sol.results[q].get("Warning", "")), str(sol.results[q].get("GVF at line conditions [%]")))

# ---- pig station
m, (g,), sol = chain([("pig_station", {"L": 20.0, "ID": 254.0, "holdup": 20.0, "capacity": 10.0})])
r = sol.results[g]
A = math.pi / 4 * 0.254 ** 2
c.close("pig: line volume = A L", r["Line volume [m³]"], A * 20000.0, 1e-9)
c.close("pig: swept liquid = holdup × volume", r["Liquid swept ahead of the pig [m³]"], 0.2 * A * 20000.0, 1e-9)
c.close("pig: transit time = L / v", r["Transit time [h]"], 20000.0 / r["Pig speed [m/s]"] / 3600.0, 1e-9)
c.check("pig: slug above the receiver capacity warns", "receiver" in str(r.get("Warning", "")), str(r.get("Slug / capacity [%]")))

# ---- hydrocyclone
WF = {"T_C": 40.0, "P_bar": 250.0, "flow_basis": "kmol/h", "flow": 2000.0, "composition": {"C1": 0.001, "nC8": 0.012, "H2O": 0.987}}
m = new_model(list(WF["composition"]))
f = add_unit(m, "feed", params=WF)
h = add_unit(m, "subsea_hydrocyclone", params={"oil_removal": 90.0, "reject_ratio": 3.0})
pw = add_unit(m, "product")
pj = add_unit(m, "product")
connect(m, f, "out", h, "feed")
connect(m, h, "water", pw, "in")
connect(m, h, "reject", pj, "in")
sol = solve(m)
r = sol.results[h]
fp = sol.fp
nin = sol.streams[port_edges(m, h, "in")["feed"][0]]
tot = sum(sol.streams[s].F * sol.streams[s].z for lst in port_edges(m, h, "out").values() for s in lst)
c.close("hydrocyclone: component balance", float(np.max(np.abs(tot - nin.F * nin.z))) / nin.F, 0.0, 1e-9)
c.close("hydrocyclone: mass balance", r["Reject [kg/h]"] + r["Clean water [kg/h]"], nin.F * nin.MW, 1e-6)
c.check("hydrocyclone: oil in the clean water is below the feed oil content", 0 < r["Oil in clean water [ppm vol]"] < 2e4, str(r["Oil in clean water [ppm vol]"]))
h_lo = r["Oil in clean water [ppm vol]"]
m2 = new_model(list(WF["composition"]))
f = add_unit(m2, "feed", params=WF)
h2 = add_unit(m2, "subsea_hydrocyclone", params={"oil_removal": 50.0})
for port in ("water", "reject"):
    connect(m2, h2, port, add_unit(m2, "product"), "in")
connect(m2, f, "out", h2, "feed")
h_hi = solve(m2).results[h2]["Oil in clean water [ppm vol]"]
c.check("hydrocyclone: lower oil removal -> more oil in the water", h_hi > h_lo, f"{h_lo} {h_hi}")

# ---- heater: duty, electrical power, energy streams, balance
m, (e,), sol = chain([("subsea_heater", {"spec": "Outlet temperature", "T_out": 100.0, "eff": 95.0})])
r = sol.results[e]
c.close("heater: outlet temperature", r["Outlet T [°C]"], 100.0, 1e-6)
c.close("heater: electrical power = duty / η", r["Electrical power [kW]"], r["Heat duty [kW]"] / 0.95, 1e-9)
fin = sol.streams[port_edges(m, e, "in")["in"][0]]
fout = sol.streams[port_edges(m, e, "out")["out"][0]]
c.close("heater: energy balance (process enthalpy)", fout.heat_flow_kW - fin.heat_flow_kW, r["Heat duty [kW]"], 1e-6)
names = {x.unit for x in sol.energy}
c.check("heater: electric supply is a separate work stream", any(n.endswith("electric supply") for n in names) and any(x.kind == "work" and x.duty_kW > 0 for x in sol.energy), str(names))
m, (e,), sol = chain([("subsea_heater", {"spec": "Heat duty", "duty": 400.0, "eff": 100.0})])
c.close("heater: duty spec", sol.results[e]["Heat duty [kW]"], 400.0, 1e-9)
c.check("heater: duty heats the fluid", sol.results[e]["Outlet T [°C]"] > 80.0, "")
c.check("heater power enters the subsea power demand", subsea_design.heating_power_kW(m, sol) > 0, "")

# ---- equipment list / CAPEX
m, ids, sol = chain([("subsea_choke", {}), ("mpfm", {}), ("subsea_heater", {}), ("pig_station", {})])
eq = subsea_design.equipment_list(m, sol)
items = eq["items"] if isinstance(eq, dict) else eq
txt = str(items)
c.check("equipment list prices the new units from the catalogue", all(n in txt for n in ("Subsea choke module", "Multiphase flow meter", "Subsea electric heater", "Pig launcher")), txt[:300])

sys.exit(c.report())
