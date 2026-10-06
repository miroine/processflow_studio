"""Unit-operation and flowsheet verification: balances, analytic limits, specs met.

Run:  python tests/test_flowsheet.py
"""
import copy
import math
import os
import sys

import numpy as np
from scipy.integrate import solve_ivp

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from check import Checker                                                     # noqa: E402
from procsim.flowsheet import (new_model, add_unit, connect, solve, build_fluid, normalize,   # noqa: E402
                               port_edges, rename, delete)
from procsim.examples import EXAMPLES, WET_GAS, DRY_GAS                        # noqa: E402
from procsim.streams import stream_properties                                   # noqa: E402
from procsim.thermo import FluidPackage, R                                      # noqa: E402
from procsim.unitops import CATALOGUE, PROFILE_TYPES                             # noqa: E402

c = Checker("flowsheet")


def atoms(keys):
    """Element matrix (components × C, H, O, N, S) for the library component keys."""
    fixed = {"N2": (0, 0, 0, 2, 0), "CO2": (1, 0, 2, 0, 0), "H2S": (0, 2, 0, 0, 1), "H2O": (0, 2, 1, 0, 0), "O2": (0, 0, 2, 0, 0),
             "H2": (0, 2, 0, 0, 0), "MeOH": (1, 4, 1, 0, 0), "MEG": (2, 6, 2, 0, 0), "SO2": (0, 0, 2, 0, 1)}
    rows = []
    for k in keys:
        if k in fixed:
            rows.append(fixed[k])
        else:                                   # alkanes: C1, C2, iC4, nC5 ...
            n = int("".join(ch for ch in k if ch.isdigit()))
            rows.append((n, 2 * n + 2, 0, 0, 0))
    return np.array(rows, float)


def unit_balances(model, sol, label):
    """Component and energy balance around every solved unit."""
    fp = sol.fp
    en_by_unit = {}
    for e in sol.energy:
        en_by_unit[e.unit] = en_by_unit.get(e.unit, 0.0) + (0.0 if "fan" in e.name else e.duty_kW)
    worst_m, worst_e = 0.0, 0.0
    for uid, u in model["units"].items():
        if u["type"] in ("feed", "product", "adjust", "flare") or sol.status.get(uid) not in ("ok", "warning"):
            continue                              # (a flare burns its gas: nothing leaves as a stream)
        if u["type"] == "recycle":
            # tear stream: inlet and outlet agree to the recycle tolerance, not exactly
            i_ = sol.streams[port_edges(model, uid, "in")["in"][0]]
            o_ = sol.streams[port_edges(model, uid, "out")["out"][0]]
            err = float(np.max(np.abs(i_.F * i_.z - o_.F * o_.z))) / max(i_.F, 1e-9)
            c.check(f"{label}: recycle {u['name']} closed within its tolerance", err <= u["params"]["tol"], f"{err:.2e}")
            continue
        ins = [sol.streams[s] for lst in port_edges(model, uid, "in").values() for s in lst]
        outs = [sol.streams[s] for lst in port_edges(model, uid, "out").values() for s in lst]
        nin = sum((s.F * s.z for s in ins if not s.empty), np.zeros(fp.n))
        nout = sum((s.F * s.z for s in outs if not s.empty), np.zeros(fp.n))
        scale = max(nin.sum(), 1e-9)
        reactor = u["type"] in ("conv_reactor", "eq_reactor")
        if reactor:                               # moles change: the atoms balance instead
            A = atoms(fp.keys)
            worst_m = max(worst_m, float(np.max(np.abs((nin - nout) @ A))) / float((nin @ A).sum()))
        else:
            worst_m = max(worst_m, float(np.max(np.abs(nin - nout))) / scale)
        if u["type"] == "recycle":
            continue
        hin = sum(s.heat_flow_kW for s in ins if not s.empty)
        hout = sum(s.heat_flow_kW for s in outs if not s.empty)
        if reactor:                               # enthalpies include the heat of formation
            from procsim.process_units import formation_flow_kW
            hin += sum(formation_flow_kW(fp, s) for s in ins)
            hout += sum(formation_flow_kW(fp, s) for s in outs)
        q = en_by_unit.get(u["name"], 0.0)
        if u["type"] == "expander":
            pass   # expander energy stream is negative (work out) -> hin + q = hout
        ref = max(abs(hin), abs(hout), 1.0)
        err = abs(hin + q - hout) / ref
        if u["type"] in PROFILE_TYPES:
            # potential energy of the elevation change leaves the enthalpy balance
            dz = sol.results[uid].get("Elevation change [m]", u["params"].get("dz", 0.0))
            ins_pe = ins
            if u["type"] == "well":          # lift gas enters and leaves at the wellhead
                ins_pe = [sol.streams[s_] for s_ in port_edges(model, uid, "in").get("in", [])]
            pe = sum(x.F * x.MW for x in ins_pe) * 9.80665 * dz / 1000.0 / 3600.0
            err = abs(hin + q - pe - hout) / ref
        if u["type"] == "separator" or u["type"] == "separator3":
            err = abs(hin + u["params"].get("duty", 0.0) - hout) / ref
        worst_e = max(worst_e, err)
    c.close(f"{label}: component balance on every unit (rel.)", worst_m, 0.0, 1e-8)
    c.close(f"{label}: energy balance on every unit (rel.)", worst_e, 0.0, 2e-5)


def overall_balance(model, sol, label):
    fp = sol.fp
    feeds = [sid for sid, s in model["streams"].items() if model["units"][s["src"][0]]["type"] == "feed"]
    prods = [sid for sid, s in model["streams"].items() if model["units"][s["dst"][0]]["type"] in ("product", "flare")]
    nin = sum(sol.streams[s].F * sol.streams[s].z for s in feeds)
    nout = sum(sol.streams[s].F * sol.streams[s].z for s in prods if not sol.streams[s].empty)
    A = atoms(fp.keys)                            # element balance (reactors change the moles, not the atoms)
    c.close(f"{label}: overall component balance feeds = products", float(np.max(np.abs((nin - nout) @ A)) / (nin @ A).sum()),
            0.0, 2e-4)


# ---- examples -----------------------------------------------------------------
for name, fn in EXAMPLES.items():
    m = fn()
    sol = solve(m)
    bad = {m["units"][k]["name"]: (v, sol.errors.get(k)) for k, v in sol.status.items()
           if k in m["units"] and v not in ("ok",)}
    c.check(f"{name}: all objects solved", sol.converged and not bad, str(bad))
    unit_balances(m, sol, name)
    overall_balance(m, sol, name)

# JT example: adjust met the LTS temperature; heat exchanger duty balance and approach
m = EXAMPLES["JT dew-point control: gas/gas exchanger + LTS + Adjust"]()
sol = solve(m)
lts = sol.stream_by_name(m, "3")
c.close("Adjust drives LTS inlet to -20 °C", lts.T - 273.15, -20.0, 0.011)
hx = next(k for k, u in m["units"].items() if u["type"] == "hx")
res = sol.results[hx]
c.check("gas/gas exchanger has positive UA and min approach", res.get("UA [kW/°C]", 0) > 0 and
        res.get("Minimum approach [°C]", 0) > 0, str(res))
t_out = sol.stream_by_name(m, "2")
c.close("HX tube outlet meets 0 °C spec", t_out.T - 273.15, 0.0, 1e-4)
adj = next(k for k, u in m["units"].items() if u["type"] == "adjust")
c.check("adjusted value reported for write-back", adj in sol.adjusted, str(sol.adjusted))

# ---- single-unit analytic checks ------------------------------------------------


def one_unit(utype, params, comp, T_C, P, flow=100.0, keys=None, n_out=1):
    m = new_model(keys or list(comp.keys()))
    f = add_unit(m, "feed", params={"T_C": T_C, "P_bar": P, "flow_basis": "kmol/h", "flow": flow,
                                    "composition": comp})
    u = add_unit(m, utype, params=params)
    connect(m, f, "out", u, list(CATALOGUE[utype]["ports"]["in"])[0])
    outs = list(CATALOGUE[utype]["ports"]["out"])
    for port in outs:
        for k in range(n_out if port == "out" and utype == "splitter" else 1):
            p = add_unit(m, "product")
            connect(m, u, port, p, "in")
    return m, u, solve(m)


# ideal-gas compressor (N2 at very low pressure): polytropic T2 from dT/dlnP = R T /(Cp eta_p)
fpN = FluidPackage.from_keys(["N2"])
m, u, sol = one_unit("compressor", {"P_out": 0.04, "eff": 80.0, "eff_type": "Polytropic"}, {"N2": 1.0}, 26.85, 0.01)
T2 = sol.results[u]["Outlet T [°C]"] + 273.15
idx = np.array([0])
ode = solve_ivp(lambda lnp, T: [R * T[0] / (float(fpN.cp_ig(T[0], idx)[0]) * 0.80)], [0, math.log(4.0)], [300.0],
                rtol=1e-10, atol=1e-10)
c.close("polytropic compressor (ideal N2) vs ODE integral", T2, ode.y[0, -1], 0.3)
m, u, sol = one_unit("compressor", {"P_out": 0.04, "eff": 80.0, "eff_type": "Adiabatic"}, {"N2": 1.0}, 26.85, 0.01)
r = sol.results[u]
c.close("adiabatic efficiency reproduced", r["Adiabatic efficiency [%]"], 80.0, 1e-6)
cp_mean = 29.13
c.rel("compressor power = F (H2 - H1)", r["Power [kW]"],
      100.0 / 3600.0 * (r["Outlet T [°C]"] - 26.85) * cp_mean, 0.01)

# valve: isenthalpic
comp = {k: v for k, v in DRY_GAS.items() if v > 0}
m, u, sol = one_unit("valve", {"P_out": 20.0}, comp, 30.0, 100.0)
sin = sol.streams[port_edges(m, u, "in")["in"][0]]
sout = sol.streams[port_edges(m, u, "out")["out"][0]]
c.close("valve is isenthalpic", sout.H - sin.H, 0.0, 1e-3)
c.check("natural gas cools on expansion (JT)", sout.T < sin.T, f"{sin.T} -> {sout.T}")

# expander vs isentropic
m, u, sol = one_unit("expander", {"P_out": 30.0, "eff": 100.0}, comp, 30.0, 90.0)
sin = sol.streams[port_edges(m, u, "in")["in"][0]]
sout = sol.streams[port_edges(m, u, "out")["out"][0]]
c.close("100 % expander is isentropic", sout.S - sin.S, 0.0, 1e-3)
mv, uv, solv = one_unit("valve", {"P_out": 30.0}, comp, 30.0, 90.0)
t_valve = solv.streams[port_edges(mv, uv, "out")["out"][0]].T
c.check("expander outlet colder than JT valve outlet", sout.T < t_valve - 10, f"{sout.T:.2f} vs {t_valve:.2f}")

# pump: water, W = V dP / eta
m, u, sol = one_unit("pump", {"P_out": 51.0, "eff": 70.0}, {"H2O": 1.0}, 20.0, 1.0, flow=1000.0)
r = sol.results[u]
V = 18.015 / 998.2 / 1000   # m3/mol at 20 degC
W_hand = 1000.0 / 3600.0 * 1000 * V * 50e5 / 0.70 / 1000   # kW
c.rel("pump power vs V·ΔP/η for water", r["Power [kW]"], W_hand, 0.04)

# heater with duty spec reproduces duty; cooler T spec met
m, u, sol = one_unit("heater", {"spec": "Duty", "duty": 500.0, "dP": 0.0}, comp, 10.0, 50.0)
c.close("heater duty spec", sol.results[u]["Duty [kW]"], 500.0, 1e-3)
m, u, sol = one_unit("cooler", {"spec": "Outlet temperature", "T_out": 5.0, "dP": 1.0}, comp, 60.0, 50.0)
c.close("cooler outlet T spec", sol.results[u]["Outlet T [°C]"], 5.0, 1e-6)
c.close("cooler outlet P = inlet - dP", sol.results[u]["Outlet P [bar(a)]"], 49.0, 1e-9)
m, u, sol = one_unit("cooler", {"spec": "Outlet vapour fraction", "VF_out": 0.9, "dP": 0.0},
                     {"C1": 0.6, "C3": 0.25, "nC5": 0.15}, 80.0, 20.0)
c.close("cooler outlet vapour-fraction spec", sol.results[u]["Outlet vapour fraction"], 0.9, 1e-4)

# splitter fractions
m, u, sol = one_unit("splitter", {"fractions": "0.2, 0.3"}, comp, 20.0, 30.0, n_out=3)
fl = [sol.streams[s].F for s in port_edges(m, u, "out")["out"]]
c.close("splitter outlet 1", fl[0], 20.0, 1e-9)
c.close("splitter outlet 2", fl[1], 30.0, 1e-9)
c.close("splitter remainder", fl[2], 50.0, 1e-9)

# three-phase separator splits water out
wg = dict(WET_GAS)
m, u, sol = one_unit("separator3", {}, wg, 25.0, 60.0, flow=1000.0, keys=list(wg.keys()))
outs = port_edges(m, u, "out")
water = sol.streams[outs["water"][0]]
c.check("3-phase separator water outlet is mostly water",
        not water.empty and water.z[list(wg.keys()).index("H2O")] > 0.99,
        "" if water.empty else f"{water.z[list(wg.keys()).index('H2O')]:.5f}")

# heat exchanger: minimum-approach spec
m = new_model(list(comp.keys()))
fh = add_unit(m, "feed", params={"T_C": 120.0, "P_bar": 40.0, "flow": 500.0, "composition": comp})
fc = add_unit(m, "feed", params={"T_C": 10.0, "P_bar": 60.0, "flow": 400.0, "composition": comp})
h = add_unit(m, "hx", params={"spec": "Minimum approach", "dTmin": 8.0, "dP_tube": 0.3, "dP_shell": 0.2})
p1, p2 = add_unit(m, "product"), add_unit(m, "product")
connect(m, fh, "out", h, "tube_in")
connect(m, fc, "out", h, "shell_in")
connect(m, h, "tube_out", p1, "in")
connect(m, h, "shell_out", p2, "in")
sol = solve(m)
c.close("HX minimum-approach spec met (8-zone search, 12-zone check)", sol.results[h]["Minimum approach [°C]"],
        8.0, 0.35)
unit_balances(m, sol, "HX min-approach")

# HX rating: UA from a design run must reproduce its duty
m2 = copy.deepcopy(m)
q_design = 0.7 * sol.results[h]["Duty [kW]"]
m2["units"][h]["params"].update({"spec": "Duty", "duty": q_design})
s2 = solve(m2)
ua = s2.results[h]["UA [kW/°C]"]
m2["units"][h]["params"].update({"spec": "UA", "UA": ua})
s3 = solve(m2)
c.rel("HX UA rating reproduces the design duty (8- vs 12-zone UA)", s3.results[h]["Duty [kW]"], q_design, 0.01)

# ---- pipe segment -----------------------------------------------------------------------
from scipy.optimize import brentq                      # noqa: E402
from procsim.unitops import churchill_f, beggs_brill_holdup   # noqa: E402

c.rel("Churchill friction factor = 64/Re in laminar flow", churchill_f(500.0, 1e-4), 64 / 500.0, 0.01)
for Re, rr in [(1e5, 1e-4), (1e6, 1e-3), (5e4, 0.0)]:
    col = brentq(lambda f: 1 / math.sqrt(f) + 2 * math.log10(rr / 3.7 + 2.51 / (Re * math.sqrt(f))), 1e-4, 0.2)
    c.rel(f"Churchill vs Colebrook at Re={Re:.0e}, e/D={rr}", churchill_f(Re, rr), col, 0.02)
# Beggs & Brill textbook horizontal case (vSL 3.97, vSG 3.86 ft/s, 6 in): lambda 0.507, NFr 5.87
lam, NFr = 3.97 / 7.83, (7.83 * 0.3048) ** 2 / (9.80665 * 0.1524)
H, reg = beggs_brill_holdup(lam, NFr, 11.87, 0.0)
c.eq("Beggs & Brill textbook case is intermittent", reg, "Intermittent")
c.close("Beggs & Brill horizontal holdup vs textbook HL(0) = 0.574", H, 0.574, 0.003)

# single-phase water: pressure drop vs Darcy-Weisbach with Colebrook
m, u, sol = one_unit("pipe", {"length": 2000.0, "ID": 150.0, "rough": 0.045, "dz": 0.0, "n_seg": 5},
                     {"H2O": 1.0}, 20.0, 30.0, flow=10000.0)
st_in = sol.streams[port_edges(m, u, "in")["in"][0]]
rho = st_in.flash.phases[0].rho
v = 10000.0 * 18.015 / 3600.0 / rho / (math.pi * 0.15 ** 2 / 4)
Re = rho * v * 0.15 / 1.002e-3
f = brentq(lambda f: 1 / math.sqrt(f) + 2 * math.log10(0.045 / 150 / 3.7 + 2.51 / (Re * math.sqrt(f))), 1e-4, 0.2)
dp_hand = f * rho * v * v / (2 * 0.15) * 2000.0 / 1e5
c.rel("water pipe ΔP vs Darcy-Weisbach/Colebrook", sol.results[u]["Pressure drop [bar]"], dp_hand, 0.03)
c.eq("water pipe reported as single-phase liquid", sol.results[u]["Flow regime (dominant)"], "Single-phase liquid")
# static head: slow water flow up 100 m
m, u, sol = one_unit("pipe", {"length": 100.0, "ID": 300.0, "dz": 100.0, "n_seg": 4}, {"H2O": 1.0}, 20.0, 30.0,
                     flow=10.0)
c.rel("vertical water column: ΔP = ρ g h", sol.results[u]["Pressure drop [bar]"], rho * 9.80665 * 100 / 1e5, 0.005)
# heat loss drives the outlet towards ambient; energy balance checked by unit_balances
m, u, sol = one_unit("pipe", {"length": 50000.0, "ID": 200.0, "heat": "Overall U to ambient", "U": 50.0,
                              "T_amb": 5.0, "n_seg": 20}, comp, 60.0, 80.0, flow=500.0)
c.close("long uninsulated line reaches ambient temperature", sol.results[u]["Outlet T [°C]"], 5.0, 1.0)
unit_balances(m, sol, "pipe with heat loss")
# two-phase flowline example from the library
from procsim.examples import subsea_tieback   # noqa: E402
m = subsea_tieback(meg_kg_h=0.0)
sol = solve(m)
arr = sol.stream_by_name(m, "Topside arrival")
gas = sol.stream_by_name(m, "Gas to process")
c.close("separator inlet and vapour outlet at the same T (consistent 3-phase flash)", gas.T, arr.T, 1e-6)
from procsim.streams import hydrate_risk   # noqa: E402
c.check("tie-back arrival flagged below hydrate temperature", hydrate_risk(arr, sol.fp), "")
c.check("riser holdup higher than flowline holdup (uphill slip)",
        sol.results[next(k for k, x in m["units"].items() if x["name"] == "PIPE-101 Riser")]["Average liquid holdup [-]"] >
        sol.results[next(k for k, x in m["units"].items() if x["name"] == "PIPE-100 Flowline")]["Average liquid holdup [-]"], "")

# ---- MEG inhibition in the tie-back -----------------------------------------------------------
from procsim.streams import hydrate_state   # noqa: E402
m = subsea_tieback()
sol = solve(m)
arr = sol.stream_by_name(m, "Topside arrival")
t_u, t_i, margin, wt = hydrate_state(arr, sol.fp)
c.check("with 2.5 t/h lean MEG the arrival is protected", not hydrate_risk(arr, sol.fp) and margin > 0, f"margin {margin}")
c.within("MEG ends up at 30-40 wt% in the produced water", wt, 30.0, 40.0)
aq = arr.flash.phase("W")
imeg = sol.fp.index("MEG")
c.check("MEG stays in the aqueous phase (>99.5 %)", aq.beta * aq.x[imeg] / arr.z[imeg] > 0.995, "")
c.check("MEG loss to gas below 10 ppm mol", arr.flash.phase("V").x[imeg] < 1e-5, f"{arr.flash.phase('V').x[imeg]:.2e}")

# ---- compressor performance curve ---------------------------------------------------------------
from procsim.unitops import CompressorCurve, typical_curve   # noqa: E402
m = EXAMPLES["Two-stage gas compression with liquid recycle"]()
sol = solve(m)
k1 = next(k for k, u in m["units"].items() if u["name"] == "K-100")
r = sol.results[k1]
c.close("fixed-spec compressor with a curve reports the speed that meets the duty (curve design ≈ 10 000 rpm)",
        r["Speed to meet duty (fan laws) [rpm]"], 10000.0, 100.0)
m["units"][k1]["params"]["curve"] = typical_curve(r["Actual inlet vol flow [m³/h]"], r["Head [kJ/kg]"], 76.0)
m["units"][k1]["params"].update({"spec": "Performance curve", "speed": 10000.0, "N_design": 10000.0})
s1 = solve(m)
c.close("curve through the operating point reproduces its outlet pressure", s1.results[k1]["Outlet P [bar(a)]"],
        30.0, 0.02)
c.rel("curve mode: delivered polytropic head equals the curve head", s1.results[k1]["Head [kJ/kg]"],
      s1.results[k1]["Curve head [kJ/kg]"], 5e-4)
m["units"][k1]["params"]["speed"] = 9000.0
s2 = solve(m)
cv = CompressorCurve(m["units"][k1]["params"]["curve"], 10000.0)
Q = s2.results[k1]["Actual inlet vol flow [m³/h]"]
h_expect = cv.at(Q, 9000.0)[0]
c.rel("fan laws: at 90 % speed the delivered head matches the scaled curve", s2.results[k1]["Head [kJ/kg]"], h_expect, 5e-4)
c.check("lower speed gives lower discharge pressure", s2.results[k1]["Outlet P [bar(a)]"] < 30.0, "")
c.close("fan laws: head at equivalent flow scales with N²", cv.at(0.9 * 8000, 9000)[0], 0.81 * cv.at(8000, 10000)[0], 1e-9)
c.close("surge flow scales with speed", cv.surge_flow(9000), 0.9 * cv.surge_flow(10000), 1e-9)
# drive the machine into surge: shrink the curve's flows so the operating point sits left of surge
m["units"][k1]["params"]["speed"] = 10000.0
m["units"][k1]["params"]["antisurge"] = "Off"
m["units"][k1]["params"]["curve"] = typical_curve(Q * 1.8, s1.results[k1]["Head [kJ/kg]"], 76.0)
s3 = solve(m)
c.check("operating left of the surge line raises a surge warning",
        "surge" in (s3.results[k1].get("Warning") or "").lower() and s3.results[k1]["Surge margin [%]"] < 0,
        str(s3.results[k1].get("Warning")))
try:
    CompressorCurve({"flow": [1, 2], "head": [3, 4], "eff": [70, 70]}, 1000)
    c.check("curve with < 3 points rejected", False, "")
except Exception:
    c.check("curve with < 3 points rejected", True, "")

# ---- scrubber sizing ------------------------------------------------------------------------------------
from procsim.unitops import souders_brown, mesh_pressure_factor   # noqa: E402
m = EXAMPLES["Two-stage gas compression with liquid recycle"]()
sol = solve(m)
v101 = next(k for k, u in m["units"].items() if u["name"] == "V-101 Scrubber")
r = sol.results[v101]
vmax = r["Souders-Brown K-factor [m/s]"] * math.sqrt((r["Liquid density [kg/m³]"] - r["Gas density [kg/m³]"]) /
                                                      r["Gas density [kg/m³]"])
c.rel("scrubber max gas velocity = K·√((ρL−ρG)/ρG)", r["Max gas velocity [m/s]"], vmax, 1e-9)
area = r["Actual gas flow [m³/h]"] / 3600 * 1.2 / vmax
c.rel("scrubber required diameter includes the 20 % margin", r["Required diameter [mm]"],
      math.sqrt(4 * area / math.pi) * 1000, 1e-6)
c.rel("scrubber mesh-pad K corrected for pressure (GPSA)", r["Souders-Brown K-factor [m/s]"],
      0.107 * mesh_pressure_factor(r["Vessel P [bar(a)]"]), 1e-9)
c.check("GPSA pressure factor: 1.0 at atmospheric, 0.80 at 600 psig",
        abs(mesh_pressure_factor(1.01325) - 1) < 1e-12 and abs(mesh_pressure_factor(600 / 14.5038 + 1.01325) - 0.8) < 1e-9, "")
m["units"][v101]["params"]["ID"] = 600.0
s4 = solve(m)
c.check("undersized scrubber warns of liquid carry-over", "carry-over" in (s4.results[v101].get("Warning") or ""), "")

# ---- column ------------------------------------------------------------------------------------------------
from procsim.column import bubble_pressure   # noqa: E402


def column_rigour(m, sol, label):
    fp = sol.fp
    for uid, u in m["units"].items():
        if u["type"] != "column":
            continue
        pr = sol.columns[uid]
        x, y = np.array(pr["x"]), np.array(pr["y"])
        T, P, V = np.array(pr["T"]) + 273.15, np.array(pr["P"]), np.array(pr["V"])
        worst = 0.0
        for j in range(len(T)):
            if V[j] < 1e-9:
                continue
            idx = np.nonzero(x[j] > 1e-14)[0]
            lpl, _, _ = fp.lnphi(x[j][idx] / x[j][idx].sum(), T[j], P[j], idx, "L")
            lpv, _, _ = fp.lnphi(y[j][idx] / y[j][idx].sum(), T[j], P[j], idx, "V")
            worst = max(worst, float(np.max(np.abs(y[j][idx] - np.exp(lpl - lpv) * x[j][idx]))))
        c.close(f"{label}: every stage in PR equilibrium (max |y − Kx|)", worst, 0.0, 1e-7)


m = EXAMPLES["Condensate stabiliser column with TVP spec (Adjust)"]()
sol = solve(m)
col = next(k for k, u in m["units"].items() if u["type"] == "column")
c.close("stabiliser Adjust meets TVP 0.80 bar", sol.results[col]["Bottoms TVP @ 37.8 °C [bar(a)]"], 0.80, 0.0021)
column_rigour(m, sol, "stabiliser")
bot = sol.stream_by_name(m, "Stabiliser bottoms")
c.rel("reported TVP = PR bubble pressure of the bottoms at 37.8 °C", bubble_pressure(sol.fp, bot.z, 310.93),
      sol.results[col]["Bottoms TVP @ 37.8 °C [bar(a)]"], 1e-6)

keys = ["C3", "iC4", "nC4"]
for cond in ("Total", "Partial"):
    m = new_model(keys)
    f = add_unit(m, "feed", params={"T_C": 60.0, "P_bar": 16.0, "flow": 100.0,
                                    "composition": {"C3": 0.4, "iC4": 0.2, "nC4": 0.4}})
    t = add_unit(m, "column", params={"n_trays": 20, "feed_stage": 10, "P_top": 15.0, "P_bot": 15.5,
                                      "condenser": cond, "RR": 3.0, "reboiler": "Yes",
                                      "reb_spec": "Distillate rate", "reb_value": 40.0})
    a, b = add_unit(m, "product"), add_unit(m, "product")
    connect(m, f, "out", t, "feed")
    connect(m, t, "overhead", a, "in")
    connect(m, t, "bottoms", b, "in")
    sol = solve(m)
    c.eq(f"C3/C4 splitter ({cond.lower()} condenser) solves", sol.status.get(t), "ok")
    if sol.status.get(t) != "ok":
        continue
    top = sol.streams[port_edges(m, t, "out")["overhead"][0]]
    c.close(f"{cond} condenser: distillate rate spec met", top.F, 40.0, 1e-6)
    column_rigour(m, sol, f"C3/C4 splitter ({cond})")
    unit_balances(m, sol, f"C3/C4 splitter ({cond})")
    pr = sol.columns[t]
    c.check(f"{cond}: temperature rises monotonically down the column",
            all(b2 >= b1 - 1e-6 for b1, b2 in zip(pr["T"], pr["T"][1:])), str(np.round(pr["T"], 2)))
    if cond == "Total":
        c.rel("total condenser: distillate leaves at its bubble point", bubble_pressure(sol.fp, top.z, top.T), 15.0, 1e-4)
        c.check("propane purity > 95 % in the distillate", top.z[0] > 0.95, f"{top.z[0]:.4f}")
    else:
        c.close("partial condenser: vapour product (VF = 1)", top.flash.vf, 1.0, 1e-6)
    # more reflux -> purer distillate
    m["units"][t]["params"]["RR"] = 6.0
    s6 = solve(m)
    top6 = s6.streams[port_edges(m, t, "out")["overhead"][0]]
    c.check(f"{cond}: higher reflux gives a purer distillate", top6.z[0] > top.z[0], f"{top6.z[0]:.4f} vs {top.z[0]:.4f}")

# absorber: lean oil absorbs C3
keys = ["N2", "CO2", "C1", "C2", "C3", "iC4", "nC4", "nC10"]
gas = {"N2": 0.01, "CO2": 0.02, "C1": 0.85, "C2": 0.06, "C3": 0.04, "iC4": 0.01, "nC4": 0.01}


def absorber(oil_rate, trays=8):
    m = new_model(keys)
    g = add_unit(m, "feed", params={"T_C": 30.0, "P_bar": 60.0, "flow": 1000.0, "composition": gas})
    o = add_unit(m, "feed", params={"T_C": 30.0, "P_bar": 60.0, "flow": oil_rate, "composition": {"nC10": 1.0}})
    t = add_unit(m, "column", params={"n_trays": trays, "P_top": 60.0, "P_bot": 60.0, "condenser": "None",
                                      "reboiler": "No"})
    a, b = add_unit(m, "product"), add_unit(m, "product")
    connect(m, g, "out", t, "feed_bottom")
    connect(m, o, "out", t, "feed_top")
    connect(m, t, "overhead", a, "in")
    connect(m, t, "bottoms", b, "in")
    sol = solve(m)
    top = sol.streams[port_edges(m, t, "out")["overhead"][0]]
    return m, t, sol, 1 - top.F * top.z[4] / 40.0


m, t, sol, rec1 = absorber(150.0)
c.eq("absorber solves", sol.status.get(t), "ok")
column_rigour(m, sol, "absorber")
unit_balances(m, sol, "absorber")
_, _, _, rec2 = absorber(300.0)
c.check("more lean oil absorbs more propane", rec2 > rec1, f"{rec1:.3f} -> {rec2:.3f}")

# ---- anti-surge control --------------------------------------------------------------------------
m = EXAMPLES["Two-stage gas compression with liquid recycle"]()
k1 = next(k for k, u in m["units"].items() if u["name"] == "K-100")
feed = next(k for k, u in m["units"].items() if u["type"] == "feed")
res = {}
for flow in (2.0, 1.2, 0.8):
    m["units"][feed]["params"]["flow"] = flow
    s_ = solve(m)
    res[flow] = (s_, s_.results[k1])
    unit_balances(m, s_, f"anti-surge turndown {flow} MSm³/d")
r20, r12, r08 = res[2.0][1], res[1.2][1], res[0.8][1]
c.close("no recycle at design flow", r20["Anti-surge recycle [kmol/h]"], 0.0, 1e-9)
c.check("recycle opens at turndown and grows as flow falls",
        0 < r12["Anti-surge recycle [kmol/h]"] < r08["Anti-surge recycle [kmol/h]"], "")
c.close("controller holds the machine on the control line (10 % margin)", r12["Surge margin [%]"], 10.0, 1e-3)
c.close("control line held at deeper turndown too", r08["Surge margin [%]"], 10.0, 1e-3)
c.rel("fixed discharge P: power floors once recycling (same machine flow and head)", r08["Power [kW]"],
      r12["Power [kW]"], 1e-6)
s08 = res[0.8][0]
qasc = next(e.duty_kW for e in s08.energy if e.name == "Q-K-100 anti-surge")
F = s08.streams[port_edges(m, k1, "in")["in"][0]].F
c.rel("anti-surge cooler removes the recycle's compression heat", -qasc,
      r08["Power [kW]"] * r08["Anti-surge recycle [kmol/h]"] / (F + r08["Anti-surge recycle [kmol/h]"]), 1e-9)
c.check("no surge warning while the controller is active", "surge line" not in (r08.get("Warning") or ""), "")
m["units"][k1]["params"]["antisurge"] = "Off"
s_off = solve(m)
c.check("with the controller off the same turndown is flagged as surge",
        "surge" in (s_off.results[k1].get("Warning") or "").lower(), str(s_off.results[k1].get("Warning")))
# curve (speed) mode with anti-surge
m["units"][k1]["params"].update({"antisurge": "On", "spec": "Performance curve", "speed": 9000.0, "N_design": 10500.0})
s_c = solve(m)
rc_ = s_c.results[k1]
c.close("curve mode: machine flow on the control line at 9000 rpm", rc_["Surge margin [%]"], 10.0, 1e-6)
c.rel("curve mode: delivered head = curve head at the machine flow", rc_["Head [kJ/kg]"], rc_["Curve head [kJ/kg]"], 5e-4)
unit_balances(m, s_c, "anti-surge curve mode")

# ---- economics ------------------------------------------------------------------------------------
from procsim.economics import compute, DEFAULTS   # noqa: E402
m = EXAMPLES["Two-stage gas compression with liquid recycle"]()
sol = solve(m)
ec = compute(m, sol)
t = ec["totals"]
W = sum(e.duty_kW for e in sol.energy if e.kind == "work")
Qc = -sum(e.duty_kW for e in sol.energy if e.kind == "heat" and e.duty_kW < 0)
pump = Qc * DEFAULTS["sw_frac"] / 100
mwh = (W + pump) * DEFAULTS["hours"] / 1000
c.rel("grid power: cost = (shaft + seawater pumping) × hours × price", t["Energy cost [cur/y]"], mwh * DEFAULTS["el_price"], 1e-9)
c.rel("grid power: CO₂ = MWh × grid factor", t["CO₂ emissions [t/y]"], mwh * DEFAULTS["grid_co2"], 1e-9)
m["economics"] = {"driver": "Gas turbine"}
t2 = compute(m, sol)["totals"]
fuel = (W + pump) / 0.35 * 3.6 / DEFAULTS["fuel_lhv"]
c.rel("gas turbine: fuel = power / η × 3.6 / LHV", t2["Fuel gas [Sm³/h]"], fuel, 1e-9)
c.rel("gas turbine: CO₂ = fuel × 2.34 kg/Sm³", t2["CO₂ emissions [t/y]"], fuel * DEFAULTS["hours"] * 2.34 / 1000, 1e-9)
prod = t2["Production [boe/d]"] * DEFAULTS["hours"] / 24
c.rel("CO₂ intensity = CO₂ / boe produced", t2["CO₂ intensity [kg/boe]"], t2["CO₂ emissions [t/y]"] * 1000 / prod, 1e-9)
c.within("gas-turbine driven compression: 5-15 kg CO₂/boe (typical NCS range)", t2["CO₂ intensity [kg/boe]"], 5.0, 15.0)
gasp = sum(stream_properties(sol.streams[sid], sol.fp)["Std gas flow [MSm³/d]"] for sid, s_ in m["streams"].items()
           if m["units"][s_["dst"][0]]["type"] == "product" and sol.streams[sid].flash.vf >= 0.999)
c.check("production counts gas at 1000 Sm³ per Sm³ o.e. plus hydrocarbon liquids only",
        t2["Production [Sm³ o.e./d]"] > gasp * 1000 and t2["Production [Sm³ o.e./d]"] < gasp * 1000 + 200, "")
m = EXAMPLES["Condensate stabiliser column with TVP spec (Adjust)"]()
sol = solve(m)
m["economics"] = {"heating": "Waste heat (free)"}
tw = compute(m, sol)["totals"]
m["economics"] = {"heating": "Gas-fired heater"}
tg = compute(m, sol)["totals"]
c.check("fired heating costs fuel and CO₂; waste heat does not", tg["CO₂ emissions [t/y]"] > tw["CO₂ emissions [t/y]"]
        and tw["Fuel gas [Sm³/h]"] == 0, "")
qh = sum(e.duty_kW for e in sol.energy if e.kind == "heat" and e.duty_kW > 0)
c.rel("fired heater fuel = duty / 85 % × 3.6 / LHV", tg["Fuel gas [Sm³/h]"], qh / 0.85 * 3.6 / 36.0, 1e-9)

# ---- model editing helpers ---------------------------------------------------------
m = EXAMPLES["Two-stage gas compression with liquid recycle"]()
n0 = len(m["streams"])
k1 = next(k for k, u in m["units"].items() if u["name"] == "K-100")
c.check("rename a unit", rename(m, k1, "K-1st"), "")
c.check("rename rejects duplicates", not rename(m, k1, "K-101"), "")
f = next(k for k, u in m["units"].items() if u["type"] == "feed")
rename(m, f, "Inlet gas")
sid = port_edges(m, f, "out")["out"][0]
c.eq("renaming a feed renames its stream", m["streams"][sid]["name"], "Inlet gas")
delete(m, [k1])
c.eq("deleting a unit removes its connections", len(m["streams"]), n0 - 2)
m2 = copy.deepcopy(m)
m2["streams"]["bad"] = {"name": "x", "src": ["nope", "out"], "dst": [f, "in"]}
normalize(m2)
c.check("normalize drops dangling connections", "bad" not in m2["streams"], "")
sol = solve(m)
c.check("flowsheet with a deleted compressor reports missing connection, not a crash",
        any(v == "missing" for v in sol.status.values()), str(sol.status))

sys.exit(c.report())
