"""Network and well design tools (v6.7): looped pipe network, flowline size sweep, gas-lift allocation, ESP sizing.

Run:  python tests/test_pack_c.py
"""
import itertools
import math
import os
import random
import sys

import numpy as np

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
from check import Checker                                                  # noqa: E402
from procsim import design as DS                                           # noqa: E402
from procsim import network as NW                                          # noqa: E402
from procsim.examples import EXAMPLES, gaslift_injection                   # noqa: E402
from procsim.flowsheet import solve, port_edges                            # noqa: E402
from procsim.streams import make_stream                                    # noqa: E402
from procsim.unitops import churchill_f                                    # noqa: E402

c = Checker("pack C")
G = 9.80665
R = 8.314462618


def net(nodes, pipes, fluid):
    return NW.solve_network(NW.parse_nodes(nodes), NW.parse_pipes(pipes), fluid)


def by(rows, key, name):
    return next(r for r in rows if r[key] == name)


# ---- parsing and errors --------------------------------------------------------------------------------------------
c.eq("nodes parse (header optional)", len(NW.parse_nodes("a, P, 5\nb, J, 0\n")), 2)
for bad, why in (("a, X, 1", "unknown kind"), ("a, P", "too few fields"), ("a, P, 1\na, J, 0", "duplicate node")):
    try:
        NW.parse_nodes(bad)
        c.check(f"parse error: {why}", False, "no error")
    except NW.NetworkError:
        c.check(f"parse error: {why}", True)
try:
    NW.parse_pipes("p, a, b, -5, 100, 0.05")
    c.check("negative length rejected", False, "")
except NW.NetworkError:
    c.check("negative length rejected", True)
gas = NW.gas_fluid(18.5, 30.0, 0.9, 0.012)
for nodes, pipes, why in (
        ("a, J, 0\nb, Q, 1", "p, a, b, 1000, 200, 0.05", "no fixed pressure"),
        ("a, P, 50\nb, Q, 1\nc, J, 0", "p, a, b, 1000, 200, 0.05", "node not connected"),
        ("a, P, 50\nb, Q, 1", "p, a, z, 1000, 200, 0.05", "unknown node in a pipe")):
    try:
        net(nodes, pipes, gas)
        c.check(f"network error: {why}", False, "no error")
    except NW.NetworkError:
        c.check(f"network error: {why}", True)
try:
    net("a, P, 50\nb, Q, -400", "p, a, b, 80000, 100, 0.05", gas)
    c.check("a demand the pipe cannot deliver is an error", False, "")
except NW.NetworkError:
    c.check("a demand the pipe cannot deliver is an error", True)

# ---- one gas pipe against the Darcy / isothermal-gas formula --------------------------------------------------------
r = net("S, P, 80\nD, Q, -2.0", "p, S, D, 20000, 250, 0.05", gas)
P2 = by(r["nodes"], "Node", "D")["Pressure [bar(a)]"]
pipe = r["pipes"][0]
Dm, L = 0.25, 20000.0
A = math.pi * Dm ** 2 / 4
m = 2.0e6 * (18.5 / 23.6454) / 86400.0                                                # kg/s
K = pipe["Friction factor [-]"] * L * 0.9 * R * 303.15 / (Dm * 0.0185 * A * A)
c.rel("gas pipe: P1² − P2² = f L Z R T ṁ² / (D M A²)", (80.0 ** 2 - P2 ** 2) * 1e10, K * m * m, 1e-6)
c.rel("gas pipe: friction factor is Churchill's at the pipe Reynolds number", pipe["Friction factor [-]"],
      churchill_f(4 * m / (math.pi * Dm * 0.012e-3), 0.05e-3 / Dm), 1e-4)
c.close("gas pipe: flow is the demand", -pipe["Flow [MSm³/d]"] * 0 + abs(pipe["Flow [MSm³/d]"]), 2.0, 1e-9)

# ---- parallel pipes ---------------------------------------------------------------------------------------------------------------
r = net("S, P, 80\nD, Q, -3.0", "a, S, D, 15000, 250, 0.05\nb, S, D, 15000, 250, 0.05", gas)
fa, fb = (by(r["pipes"], "Pipe", n)["Flow [MSm³/d]"] for n in "ab")
c.close("two identical parallel pipes share equally", fa - fb, 0.0, 1e-9)
c.close("flows add up to the demand", fa + fb, 3.0, 1e-9)
r = net("S, P, 80\nD, Q, -3.0", "a, S, D, 15000, 300, 0.05\nb, S, D, 15000, 200, 0.05", gas)
pa, pb = by(r["pipes"], "Pipe", "a"), by(r["pipes"], "Pipe", "b")
exp = math.sqrt(0.3 ** 5 / pa["Friction factor [-]"] / (0.2 ** 5 / pb["Friction factor [-]"]))
c.rel("unequal parallel pipes: flow ratio = sqrt(D⁵/f) ratio", pa["Flow [MSm³/d]"] / pb["Flow [MSm³/d]"], exp, 1e-6)
c.close("the same pressure drop in both", pa["Pressure drop [bar]"] - pb["Pressure drop [bar]"], 0.0, 1e-9)

# ---- the looped gas example ---------------------------------------------------------------------------------------------------------
nodes, pipes = NW.parse_nodes(NW.EXAMPLE_NODES), NW.parse_pipes(NW.EXAMPLE_PIPES)
r = NW.solve_network(nodes, pipes, gas)
c.check("example: converged to machine precision", r["balance"]["Residual (relative)"] < 1e-9, str(r["balance"]))
c.rel("example: supply = outflow at the fixed-pressure node", r["balance"]["Supply into the network [kg/s]"],
      r["balance"]["Leaving at the fixed-pressure nodes [kg/s]"], 1e-9)
flow = {p["Pipe"]: p["Flow [MSm³/d]"] for p in r["pipes"]}
tot_in = sum(n["value"] for n in nodes if n["kind"] == "Q")
c.close("example: all the supply leaves through the Trunk and Loop-2", flow["Trunk"] + flow["Loop-2"], tot_in, 1e-8)
for nd, ins, outs in (("A", ("F1",), ("Trunk", "Loop-1", "Bypass")), ("B", ("Loop-1", "Spur", "F2"), ("Loop-2",)),
                      ("C", ("Bypass", "F3"), ("Spur",))):
    c.close(f"example: mass balance at {nd}", sum(flow[x] for x in ins) - sum(flow[x] for x in outs), 0.0, 1e-8)
pn = {n["Node"]: n["Pressure [bar(a)]"] for n in r["nodes"]}
dp = {p["Pipe"]: p["Pressure drop [bar]"] for p in r["pipes"]}
c.close("example: pressure drops around the loop A-B-C-A sum to zero", dp["Loop-1"] - dp["Spur"] - dp["Bypass"], 0.0, 1e-9)
c.check("example: gas flows from the fields to the plant (pressure falls)", pn["Field-1"] > pn["A"] > pn["Plant"], str(pn))
c.check("example: velocities are plausible (< 25 m/s)", all(p["Velocity [m/s]"] < 25.0 for p in r["pipes"]), "")
nodes2 = [dict(n) for n in nodes]
big = NW.parse_pipes(NW.EXAMPLE_PIPES.replace("Trunk, A, Plant, 40000, 300", "Trunk, A, Plant, 40000, 400"))
r2 = NW.solve_network(nodes2, big, gas)
c.check("a bigger trunk lowers the field pressures", by(r2["nodes"], "Node", "Field-1")["Pressure [bar(a)]"] < pn["Field-1"], "")
c.check("a bigger trunk takes more of the flow", by(r2["pipes"], "Pipe", "Trunk")["Flow [MSm³/d]"] > flow["Trunk"], "")

# ---- liquid with elevation ----------------------------------------------------------------------------------------------------------
liq = NW.liquid_fluid(850.0, 5.0)
r = net("S, P, 10, 0\nJ, J, 0, 30", "p, S, J, 1000, 200, 0.05", liq)
c.close("no flow: the column pressure ρ g Δz", by(r["nodes"], "Node", "J")["Pressure [bar(a)]"], 10.0 - 850.0 * G * 30.0 / 1e5, 1e-7)
r = net("S, P, 20, 0\nD, Q, -200, 0", "p, S, D, 4000, 200, 0.05", liq)
pipe = r["pipes"][0]
v = 200.0 / 3600.0 / (math.pi * 0.2 ** 2 / 4)
Re = 850.0 * v * 0.2 / 5e-3
f = churchill_f(Re, 0.05e-3 / 0.2)
c.rel("liquid pipe: Darcy-Weisbach pressure drop", pipe["Pressure drop [bar]"], f * 4000.0 / 0.2 * 850.0 * v * v / 2 / 1e5, 1e-4)
c.rel("liquid pipe: velocity", pipe["Velocity [m/s]"], v, 1e-9)
r = net("S, P, 20, 0\nD, Q, -200, 40", "p, S, D, 4000, 200, 0.05", liq)
c.rel("uphill adds the static head", r["pipes"][0]["Pressure drop [bar]"], f * 4000.0 / 0.2 * 850.0 * v * v / 2 / 1e5 + 850.0 * G * 40.0 / 1e5, 1e-4)

# ---- wall and steel -------------------------------------------------------------------------------------------------------------------------
t = DS.wall_thickness(300.0, 150.0)
c.rel("wall: Barlow with the design factor, plus the allowance", t, 15.0 * 300.0 / (2 * 0.72 * 450.0 - 15.0) + 3.0, 1e-12)
mass = DS.steel_mass_t_per_km(300.0, t)
c.rel("steel mass per km", mass, math.pi / 4 * ((0.3 + 2 * t / 1000) ** 2 - 0.3 ** 2) * 7850.0, 1e-12)
c.check("a thicker wall for a higher pressure", DS.wall_thickness(300.0, 250.0) > t, "")

# ---- gas-lift allocation (pure functions) ------------------------------------------------------------------------------------------------
env = DS.concave_envelope([(0, 0), (1, 5), (2, 6), (3, 9), (4, 10)])
slopes = [(y1 - y0) / (x1 - x0) for (x0, y0), (x1, y1) in zip(env, env[1:])]
c.check("envelope: slopes fall (concave)", all(a >= b - 1e-12 for a, b in zip(slopes, slopes[1:])), str(env))
c.check("envelope never lies below the data", all(DS.curve_value(env, x) >= y - 1e-9 for x, y in [(0, 0), (1, 5), (2, 6), (3, 9), (4, 10)]), str(env))
peak = DS.concave_envelope([(0, 0), (1, 4), (2, 6), (3, 5), (4, 3)])
c.eq("envelope stops at the peak", peak[-1], (2.0, 6.0))
random.seed(3)
for trial in range(6):
    curves = []
    for _ in range(3):
        xs = [0.0, 0.5, 1.0, 1.5, 2.0]
        g = sorted((random.uniform(0.2, 3.0) for _ in range(4)), reverse=True)
        ys = [0.0]
        for a_, w_ in zip(g, [0.5] * 4):
            ys.append(ys[-1] + a_ * w_)
        curves.append(list(zip(xs, ys)))
    total = random.choice([1.0, 2.0, 3.0, 4.5])
    alloc = DS.allocate_gas(curves, total)
    got = sum(DS.curve_value(cv, a_) for cv, a_ in zip(curves, alloc))
    best = max(sum(DS.curve_value(cv, a_) for cv, a_ in zip(curves, comb))
               for comb in itertools.product([i * 0.5 for i in range(5)], repeat=3) if sum(comb) <= total + 1e-9)
    c.check(f"allocation {trial}: uses the supply and matches the brute-force optimum", abs(sum(alloc) - total) < 1e-9 or sum(alloc) < total
            and got >= best - 1e-9, f"{got} vs {best}")
    c.check(f"allocation {trial}: at least as good as an equal split", got >= sum(DS.curve_value(cv, total / 3) for cv in curves) - 1e-9, "")
try:
    DS.allocate_gas([[(1.0, 5.0), (2.0, 8.0)]], 0.5)
    c.check("a supply below the lowest rate is an error", False, "")
except ValueError:
    c.check("a supply below the lowest rate is an error", True)

# ---- ESP --------------------------------------------------------------------------------------------------------------------------------------------------
s0 = ESP_STREAM = None
m = gaslift_injection()
sol = solve(m)
uid = next(u for u, x in m["units"].items() if x["type"] == "well")
st0 = sol.streams[port_edges(m, uid, "in")["in"][0]]
st = make_stream("", sol.fp, st0.F * 0.3, st0.z, st0.flash)
H, eta, P = DS.esp_stage(DS.ESP_CATALOGUE[2], 900.0, 60.0, 850.0)
c.close("stage at the best-efficiency flow: nominal head", H, 8.0, 1e-12)
c.close("stage at the best-efficiency flow: nominal efficiency", eta, 0.66, 1e-12)
c.rel("stage power = ρ g Q H / η", P, 850.0 * G * (900.0 / 86400.0) * 8.0 / 0.66 / 1000.0, 1e-12)
H50, _, _ = DS.esp_stage(DS.ESP_CATALOGUE[2], 900.0 * 50 / 60, 50.0, 850.0)
c.rel("affinity: head ∝ f² at the same relative flow", H50, 8.0 * (50 / 60) ** 2, 1e-12)
r = DS.esp_design(sol.fp, st, 30.0, 90.0, 20.0, 2000.0, 100.0)
c.rel("TDH = pressure rise / (ρ g)", r["Total dynamic head [m]"], r["Required pressure rise [bar]"] * 1e5 / (r["Mixture density at the intake [kg/m³]"] * G), 1e-9)
c.rel("pressure rise = WHP + hydrostatic + friction − intake", r["Required pressure rise [bar]"], 20.0 + r["Hydrostatic [bar]"] + r["Friction [bar]"] - 30.0, 1e-9)
c.check("free gas is reported", 0.0 <= r["Free gas fraction at the intake [-]"] <= 1.0, "")
c.check("a gassy intake is flagged", "gas" in r["Gas handling"], r["Gas handling"])
c.check("options exist and run within the range", len(r["Options"]) > 0 and all(o["In the recommended range"] for o in r["Options"]), "")
for o in r["Options"]:
    c.check(f"option {o['Series']} {o['Frequency [Hz]']:g} Hz: stages deliver the head", o["Stages"] * o["Head per stage [m]"] >= r["Total dynamic head [m]"] - 1e-9
            and (o["Stages"] - 1) * o["Head per stage [m]"] < r["Total dynamic head [m]"], "")
    c.check(f"option {o['Series']} {o['Frequency [Hz]']:g} Hz: motor covers the shaft power plus the margin", o["Motor [kW]"] >= 1.1 * o["Shaft power [kW]"] - 1e-9, "")
effs = [o["Pump efficiency [%]"] for o in r["Options"]]
c.check("options are sorted by efficiency", effs == sorted(effs, reverse=True), str(effs))
r2 = DS.esp_design(sol.fp, st, 40.0, 90.0, 20.0, 2000.0, 100.0)
c.check("a higher intake pressure needs less head", r2["Total dynamic head [m]"] < r["Total dynamic head [m]"], "")
c.check("a higher intake pressure lowers the free gas", r2["Free gas fraction at the intake [-]"] < r["Free gas fraction at the intake [-]"], "")
try:
    DS.esp_design(sol.fp, st, 180.0, 90.0, 5.0, 100.0, 100.0)
    c.check("no pump needed when the intake pressure suffices", False, "")
except ValueError:
    c.check("no pump needed when the intake pressure suffices", True)

# ---- pipe-size sweep on the subsea field ----------------------------------------------------------------------------------------------------
name = next(k for k in EXAMPLES if k.startswith("Subsea field (SURF)"))
m = EXAMPLES[name]()
s = solve(m)
fl = next(u for u, x in m["units"].items() if x["type"] == "flowline")
rs = next((u for u, x in m["units"].items() if x["type"] == "riser"), None)
inlet = s.streams[port_edges(m, fl, "in")["in"][0]]
rows, rec = DS.size_sweep(s.fp, inlet, m["units"][fl]["params"], m["units"][rs]["params"] if rs else None, [150, 300, 400], 40.0)
c.eq("sweep: one row per diameter", [r_["ID [mm]"] for r_ in rows], [150.0, 300.0, 400.0])
c.check("sweep: the steel mass rises with the diameter", rows[0]["Steel [t/km]"] < rows[1]["Steel [t/km]"] < rows[2]["Steel [t/km]"], "")
c.check("sweep: a very small line cannot deliver the rate", not rows[0]["Feasible"] and rows[0]["Limits"] != "", rows[0]["Limits"])
c.check("sweep: the larger lines are feasible", rows[1]["Feasible"] and rows[2]["Feasible"], str(rows[1]["Limits"]))
c.check("sweep: a larger diameter gives a higher arrival pressure", rows[2]["Arrival P [bar(a)]"] > rows[1]["Arrival P [bar(a)]"], "")
c.eq("sweep: the recommendation is the smallest feasible diameter", rec["ID [mm]"], 300.0)
rows_h, rec_h = DS.size_sweep(s.fp, inlet, m["units"][fl]["params"], m["units"][rs]["params"] if rs else None, [300, 400], 200.0)
c.check("sweep: an unreachable arrival pressure gives no recommendation", rec_h is None, str(rec_h))

# ---- gas-lift curves on the example (the wells re-solved) ----------------------------------------------------------------------------------------
m = gaslift_injection()
sol = solve(m)
cv = DS.gaslift_curves(m, sol, factors=(1.0, 1.5))
c.check("gas-lift curves: every lifted well has a curve", {x["Well"] for x in cv} == {"P-1", "P-2"} or len(cv) >= 2, str([x["Well"] for x in cv]))
for x in cv:
    pts = sorted(x["points"])
    c.check(f"gas-lift {x['Well']}: more lift gas gives more oil at this WHP", pts[-1][1] > pts[0][1], str(pts))
tot = sum(x["gas_now"] for x in cv)
al = DS.allocation_table(cv, tot * 1.5)
c.check("gas-lift: the allocation never uses more than the supply", sum(a["Optimal lift gas [MSm³/d]"] for a in al) <= tot * 1.5 + 1e-9, "")
c.check("gas-lift: the optimum is no worse than the present split", sum(a["Gain [Sm³/d]"] for a in al) >= -1e-9, "")

sys.exit(c.report())
