"""Looped / branched pipe-network solver (v6.7): steady, single-phase gas or liquid, any topology.

Unknowns are the node pressures (π = P² for gas, P for liquid) at every node without a fixed pressure; the equations
are the mass balances at those nodes, with each pipe obeying the Darcy-Weisbach law,

    gas      π₁ − π₂ = K ṁ|ṁ|,  K = f L Z R T / (D M A²)         (isothermal, horizontal)
    liquid   P₁ − P₂ − ρ g (z₂ − z₁) = K ṁ|ṁ|,  K = f L / (2 D ρ A²)

with f from Churchill (1977) at the pipe's own Reynolds number. The system is solved together (Newton / dogleg), so
loops, parallel pipes and meshes need no loop-by-loop iteration. Gas elevation, temperature change and compositional
tracking are ignored: this is a gathering / distribution screening tool for one fluid, not a transient or multiphase
network simulator (multiphase lines belong to the flowsheet).

Text format (CSV, header optional):
    nodes   name, kind, value, elevation_m     kind: P (fixed pressure, bar(a)), Q (supply/withdrawal) or J (junction)
                                               Q value: MSm³/d for gas, m³/h for liquid; positive = into the network
    pipes   name, from, to, length_m, ID_mm, roughness_mm
"""
from __future__ import annotations

import math

import numpy as np
from scipy.optimize import root

from .unitops import churchill_f

R_GAS = 8.314462618
G = 9.80665
STD_VOL = 0.0236454          # m³/mol at 15 °C, 1.01325 bar


class NetworkError(ValueError):
    pass


EXAMPLE_NODES = """name, kind, value, elevation_m
Plant, P, 70, 0
A, J, 0, 0
B, J, 0, 0
C, J, 0, 0
Field-1, Q, 1.5, 0
Field-2, Q, 1.0, 0
Field-3, Q, 0.8, 0
"""
EXAMPLE_PIPES = """name, from, to, length_m, ID_mm, roughness_mm
Trunk, A, Plant, 40000, 300, 0.05
Loop-1, A, B, 30000, 250, 0.05
Loop-2, B, Plant, 30000, 250, 0.05
Bypass, A, C, 20000, 200, 0.05
Spur, C, B, 20000, 200, 0.05
F1, Field-1, A, 15000, 200, 0.05
F2, Field-2, B, 15000, 200, 0.05
F3, Field-3, C, 10000, 150, 0.05
"""


def _rows(text):
    out = []
    for ln in str(text).splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        out.append([c.strip() for c in ln.split(",")])
    return out


def parse_nodes(text):
    rows = _rows(text)
    if rows and rows[0][0].lower() in ("name", "node"):
        rows = rows[1:]
    nodes = []
    for r in rows:
        if len(r) < 3:
            raise NetworkError(f"node line needs name, kind, value: {', '.join(r)}")
        kind = r[1].upper()
        if kind not in ("P", "Q", "J"):
            raise NetworkError(f"node {r[0]}: kind must be P, Q or J (got {r[1]})")
        try:
            val = float(r[2]) if r[2] != "" else 0.0
            z = float(r[3]) if len(r) > 3 and r[3] != "" else 0.0
        except ValueError:
            raise NetworkError(f"node {r[0]}: numbers expected in {', '.join(r)}")
        nodes.append({"name": r[0], "kind": kind, "value": val, "z": z})
    names = [n["name"] for n in nodes]
    if len(set(names)) != len(names):
        raise NetworkError("node names must be unique")
    return nodes


def parse_pipes(text):
    rows = _rows(text)
    if rows and rows[0][0].lower() in ("name", "pipe"):
        rows = rows[1:]
    pipes = []
    for r in rows:
        if len(r) < 6:
            raise NetworkError(f"pipe line needs name, from, to, length_m, ID_mm, roughness_mm: {', '.join(r)}")
        try:
            L, D, e = float(r[3]), float(r[4]), float(r[5])
        except ValueError:
            raise NetworkError(f"pipe {r[0]}: numbers expected in {', '.join(r)}")
        if L <= 0 or D <= 0 or e < 0:
            raise NetworkError(f"pipe {r[0]}: length and diameter must be positive")
        pipes.append({"name": r[0], "from": r[1], "to": r[2], "L": L, "D": D / 1000.0, "eps": e / 1000.0})
    names = [p["name"] for p in pipes]
    if len(set(names)) != len(names):
        raise NetworkError("pipe names must be unique")
    return pipes


def gas_fluid(MW, T_C, Z=0.9, mu_cP=0.012):
    return {"phase": "gas", "MW": MW, "T": T_C + 273.15, "Z": Z, "mu": mu_cP * 1e-3}


def liquid_fluid(rho, mu_cP):
    return {"phase": "liquid", "rho": rho, "mu": mu_cP * 1e-3}


def _flow(delta, K0, D, L, eps, mu, gas, ref):
    """Mass flow [kg/s] for the driving difference delta (Pa² for gas, Pa for liquid): ṁ = Δ / √(K·√(Δ² + ε²))
    with K from Churchill at the pipe's own Reynolds number (fixed-point on f)."""
    A = math.pi * D * D / 4.0
    f = 0.02
    reg = (1e-9 * ref) ** 2
    m = 0.0
    for _ in range(8):
        K = f * K0
        m = delta / math.sqrt(K * math.sqrt(delta * delta + reg))
        Re = 4.0 * abs(m) / (math.pi * D * mu) if mu > 0 else 1e7
        f = churchill_f(max(Re, 10.0), eps / D)
    return m, f, A


def solve_network(nodes, pipes, fluid, tol=1e-8):
    """Solve the network. Returns {"nodes": [...], "pipes": [...], "balance": {...}}."""
    idx = {n["name"]: i for i, n in enumerate(nodes)}
    if not nodes or not pipes:
        raise NetworkError("the network needs nodes and pipes")
    for p in pipes:
        for k in ("from", "to"):
            if p[k] not in idx:
                raise NetworkError(f"pipe {p['name']}: unknown node {p[k]}")
        if p["from"] == p["to"]:
            raise NetworkError(f"pipe {p['name']}: both ends are the same node")
    fixed = [i for i, n in enumerate(nodes) if n["kind"] == "P"]
    if not fixed:
        raise NetworkError("at least one node must have a fixed pressure (kind P)")
    gas = fluid["phase"] == "gas"
    mu = float(fluid["mu"])
    if gas:
        M = float(fluid["MW"]) / 1000.0
        T, Z = float(fluid["T"]), float(fluid["Z"])
        rho_std = float(fluid["MW"]) / (STD_VOL * 1000.0)            # kg/Sm³
    else:
        rho = float(fluid["rho"])
    # every node must reach a fixed-pressure node
    adj = {i: set() for i in range(len(nodes))}
    for p in pipes:
        a, b = idx[p["from"]], idx[p["to"]]
        adj[a].add(b)
        adj[b].add(a)
    seen, stack = set(fixed), list(fixed)
    while stack:
        for j in adj[stack.pop()]:
            if j not in seen:
                seen.add(j)
                stack.append(j)
    lost = [nodes[i]["name"] for i in range(len(nodes)) if i not in seen]
    if lost:
        raise NetworkError(f"not connected to a fixed-pressure node: {', '.join(lost)}")
    free = [i for i in range(len(nodes)) if nodes[i]["kind"] != "P"]
    P_fix = {i: float(nodes[i]["value"]) for i in fixed}
    for i, pv in P_fix.items():
        if pv <= 0:
            raise NetworkError(f"node {nodes[i]['name']}: pressure must be positive")
    Pmax, Pmin = max(P_fix.values()), min(P_fix.values())
    # injections [kg/s]
    inj = np.zeros(len(nodes))
    for i, n in enumerate(nodes):
        if n["kind"] == "Q":
            inj[i] = (n["value"] * 1e6 * rho_std / 86400.0) if gas else (n["value"] * rho / 3600.0)
    K0 = []
    for p in pipes:
        A = math.pi * p["D"] ** 2 / 4.0
        if gas:
            K0.append(p["L"] * Z * R_GAS * T / (p["D"] * M * A * A))
        else:
            K0.append(p["L"] / (2.0 * p["D"] * rho * A * A))
    ref = (Pmax * 1e5) ** 2 if gas else Pmax * 1e5

    def to_state(x):
        v = np.zeros(len(nodes))
        for i in fixed:
            v[i] = P_fix[i] ** 2 if gas else P_fix[i]
        for k, i in enumerate(free):
            v[i] = x[k]
        return v

    def pipe_flows(v):
        out = []
        for p, k0 in zip(pipes, K0):
            a, b = idx[p["from"]], idx[p["to"]]
            if gas:
                delta = (v[a] - v[b]) * 1e10                                   # Pa²
            else:
                delta = (v[a] - v[b]) * 1e5 - rho * G * (nodes[b]["z"] - nodes[a]["z"])
            out.append(_flow(delta, k0, p["D"], p["L"], p["eps"], mu, gas, ref))
        return out

    def residual(x):
        v = to_state(x)
        fl = pipe_flows(v)
        net = inj.copy()
        for p, (m, _, _) in zip(pipes, fl):
            net[idx[p["from"]]] -= m
            net[idx[p["to"]]] += m
        return np.array([net[i] for i in free]) / max(np.abs(inj).sum(), 1e-9)

    if free:
        x0 = np.full(len(free), (0.5 * (Pmax ** 2 + Pmin ** 2)) if gas else 0.5 * (Pmax + Pmin))
        sol = root(residual, x0, method="hybr", tol=1e-13)
        x = sol.x
        if not sol.success and np.max(np.abs(residual(x))) > tol:
            sol = root(residual, x, method="lm", tol=1e-14)
            x = sol.x
        res = float(np.max(np.abs(residual(x))))
        if res > 1e-6:
            raise NetworkError(f"the network did not converge (residual {res:.2e}): check the flows and the pressures")
        if gas and np.any(x <= 0):
            raise NetworkError("a node pressure fell to zero: the demand exceeds what the pipes can deliver")
        if not gas and np.any(x <= 0):
            raise NetworkError("a node pressure fell to zero: the demand exceeds what the pipes can deliver")
    else:
        x, res = np.array([]), 0.0
    v = to_state(x)
    fl = pipe_flows(v)
    P_node = [math.sqrt(v[i]) if gas else v[i] for i in range(len(nodes))]
    out_nodes = []
    net = inj.copy()
    for p, (m, _, _) in zip(pipes, fl):
        net[idx[p["from"]]] -= m
        net[idx[p["to"]]] += m
    for i, n in enumerate(nodes):
        d = {"Node": n["name"], "Kind": n["kind"], "Pressure [bar(a)]": P_node[i]}
        if n["kind"] == "P":                       # flow leaving (+) or entering (−) the network at a fixed-pressure node
            q = net[i]
            d["Outflow of the network [MSm³/d]" if gas else "Outflow of the network [m³/h]"] = (
                q * 86400.0 / rho_std / 1e6 if gas else q * 3600.0 / rho)
        out_nodes.append(d)
    out_pipes = []
    for p, (m, f, A) in zip(pipes, fl):
        a, b = idx[p["from"]], idx[p["to"]]
        P1, P2 = P_node[a], P_node[b]
        if gas:
            Pm = 2.0 / 3.0 * (P1 * P1 + P1 * P2 + P2 * P2) / (P1 + P2)
            rho_m = Pm * 1e5 * M / (Z * R_GAS * T)
            q = m * 86400.0 / rho_std / 1e6
        else:
            rho_m = rho
            q = m * 3600.0 / rho
        v_m = abs(m) / (rho_m * A)
        v_e = 122.0 / math.sqrt(rho_m)
        row = {"Pipe": p["name"], "From": p["from"], "To": p["to"],
               ("Flow [MSm³/d]" if gas else "Flow [m³/h]"): q, "Pressure drop [bar]": P1 - P2,
               "Velocity [m/s]": v_m, "Erosional velocity ratio (C=100)": v_m / v_e,
               "Friction factor [-]": f, "Reynolds number [-]": 4.0 * abs(m) / (math.pi * p["D"] * mu) if mu > 0 else None}
        out_pipes.append(row)
    supply = sum(n for n in inj if n > 0)
    return {"nodes": out_nodes, "pipes": out_pipes,
            "balance": {"Residual (relative)": res, "Supply into the network [kg/s]": float(supply),
                        "Leaving at the fixed-pressure nodes [kg/s]": float(sum(net[i] for i in fixed))}}
