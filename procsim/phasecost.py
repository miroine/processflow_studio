"""Cost and payback of the project phases (v7.8).

The flowsheet's phases (procsim.phasing) say *what is added when*.  This module puts a number on each phase:

* CAPEX of the equipment tagged ``add`` in the phase - power-law scaling  C = C_ref (S / S_ref)^n  from the size the solution
  gives (kW, m², m³ ...), then an installation factor and a contingency.  The reference costs are generic, illustrative
  placeholders (see COST_TABLE) - overwrite single items with ``overrides`` (unit name -> installed Mcur; the cost table is read as millions of the model currency) or replace the whole table.
* OPEX: energy + CO₂ of the stage (procsim.economics) and a maintenance charge on the installed CAPEX of the added equipment.
* Benefit: the production gain of the stage times the price per barrel of oil equivalent.
* Result per phase: CAPEX, change in annual cost, change in annual production, net annual benefit, simple payback and NPV
  (annuity over the economic life at the discount rate).

Stages are solved one by one with the same fluid package; nothing in the model is changed."""
from __future__ import annotations

from . import economics as EC
from . import phasing as PH
from .flowsheet import solve

DEFAULTS = {
    "price_boe": 450.0,        # currency per boe of the production gain (enter your own)
    "discount": 8.0,           # % per year
    "life": 15.0,              # years of benefit after the phase is on stream
    "install": 2.2,            # installed cost / equipment cost (bulk, installation, engineering)
    "contingency": 25.0,       # % on the installed cost
    "maint": 3.0,              # % of the installed cost per year
    "overrides": {},           # unit name -> installed cost [Mcur]
}

# type: (result keys tried in order, size unit, reference size, reference equipment cost [Mcur], exponent)
# Illustrative generic values only.
COST_TABLE = {
    "compressor": (("Power [kW]",), "kW", 5000.0, 20.0, 0.75),
    "pump": (("Power [kW]",), "kW", 500.0, 1.5, 0.65),
    "expander": (("Power [kW]", "Shaft power [kW]"), "kW", 2000.0, 8.0, 0.70),
    "gas_turbine": (("Shaft power [kW]",), "kW", 10000.0, 18.0, 0.75),
    "heater": (("Duty [kW]",), "kW", 5000.0, 2.0, 0.65),
    "cooler": (("Duty removed [kW]",), "kW", 5000.0, 2.0, 0.65),
    "hx": (("Duty [kW]",), "kW", 5000.0, 2.5, 0.65),
    "aircooler": (("Duty removed [kW]", "Duty [kW]"), "kW", 5000.0, 3.0, 0.7),
    "chiller": (("Duty removed [kW]",), "kW", 2000.0, 4.0, 0.7),
    "separator": (("Vapour flow [kg/h]+Liquid flow [kg/h]",), "kg/h", 100000.0, 3.0, 0.6),
    "scrubber": (("Vapour flow [kg/h]+Liquid flow [kg/h]",), "kg/h", 100000.0, 2.5, 0.6),
    "separator3": (("Vapour flow [kg/h]+Oil flow [kg/h]+Water flow [kg/h]",), "kg/h", 100000.0, 5.0, 0.6),
    "phase_splitter": (("Vapour flow [kg/h]+Oil flow [kg/h]+Water flow [kg/h]",), "kg/h", 100000.0, 3.0, 0.6),
    "column": (("Reboiler duty [kW]",), "kW", 5000.0, 10.0, 0.65),
    "teg_contactor": (("Water removed [kg/h]",), "kg/h", 2000.0, 6.0, 0.6),
    "amine_contactor": (("Reboiler duty [kW]",), "kW", 10000.0, 25.0, 0.7),
    "mol_sieve": (("Bed volume [m³]",), "m³", 20.0, 5.0, 0.7),
    "membrane": (("Membrane area [m²]",), "m²", 5000.0, 6.0, 0.9),
    "flare": (("Heat release [MW]",), "MW", 50.0, 4.0, 0.6),
    "relief_valve": (None, "", 1.0, 0.2, 1.0),
    "valve": (None, "", 1.0, 0.1, 1.0),
    "mixer": (None, "", 1.0, 0.05, 1.0),
    "splitter": (None, "", 1.0, 0.05, 1.0),
    "pipe": (None, "", 1.0, 0.5, 1.0),
    "conv_reactor": (("Duty [kW]",), "kW", 5000.0, 8.0, 0.65),
    "eq_reactor": (("Duty [kW]",), "kW", 5000.0, 8.0, 0.65),
}
NO_COST = ("feed", "product", "recycle", "adjust", "platform")
FALLBACK_Mcur = 2.0            # types without a cost model


def cost_params(model):
    p = dict(DEFAULTS)
    p.update(model.get("phasecost") or {})
    p["overrides"] = dict(p.get("overrides") or {})
    return p


def _size(res, keys):
    if not keys:
        return 1.0
    for k in keys:
        parts = k.split("+")
        vals = [res.get(x) for x in parts]
        if all(isinstance(v, (int, float)) for v in vals):
            return abs(float(sum(vals)))
    return None


def element_costs(model, sol, p=None):
    """One row per unit of the solved model: size, equipment cost, installed cost (with contingency) [Mcur]."""
    p = cost_params(model) if p is None else p
    rows = []
    for uid, u in model["units"].items():
        t = u["type"]
        if t in NO_COST or t in PH.ANNOTATION_TYPES:
            continue
        res = sol.results.get(uid) or {}
        ph = u.get("phase") or ""
        row = {"Unit": u["name"], "Type": t, "Phase": ph, "Change": u.get("change") or "add" if ph else "", "Size": None, "Size unit": "",
               "Equipment [Mcur]": 0.0, "Installed [Mcur]": 0.0, "Basis": ""}
        ov = p["overrides"].get(u["name"])
        if ov is not None:
            row.update({"Installed [Mcur]": float(ov), "Equipment [Mcur]": float(ov) / max(float(p["install"]) * (1 + float(p["contingency"]) / 100.0), 1e-9),
                        "Basis": "user override"})
            rows.append(row)
            continue
        spec = COST_TABLE.get(t)
        if spec is None:
            eq, basis = FALLBACK_Mcur, "no cost model for this type: placeholder (enter an override)"
        else:
            keys, unit, s_ref, c_ref, n = spec
            s = _size(res, keys)
            row["Size unit"] = unit
            if s is None or s <= 0:
                eq, basis = c_ref * 0.3, "size not available in the solution: 30 % of the reference cost"
            else:
                row["Size"] = s if keys else None
                eq = c_ref * (s / s_ref) ** n if keys else c_ref
                basis = f"{c_ref:g} Mcur at {s_ref:g} {unit}, exponent {n:g}" if keys else f"fixed {c_ref:g} Mcur"
        row["Equipment [Mcur]"] = eq
        row["Installed [Mcur]"] = eq * float(p["install"]) * (1.0 + float(p["contingency"]) / 100.0)
        row["Basis"] = basis
        rows.append(row)
    return rows


def _annuity(rate_pct, years):
    r = rate_pct / 100.0
    if abs(r) < 1e-12:
        return years
    return (1.0 - (1.0 + r) ** (-years)) / r


def rollup(model, fp=None, p=None, eco=None, progress=None):
    """Per-stage and per-phase economics.  Returns {"stages": [...], "phases": [...], "elements": [...], "params", "currency"}."""
    p = cost_params(model) if p is None else p
    n = PH.n_phases(model)
    if n == 0:
        raise ValueError("the flowsheet has no project phases: add a phase and tag the equipment first")
    eco_p = EC.params(model) if eco is None else eco
    hours = float(eco_p["hours"])
    stages = []
    sols = {}
    for s in range(n + 1):
        m = dict(model, stage=s)
        sol = solve(m, fp)
        sols[s] = sol
        sm = PH.stage_model(model, s)
        ec = EC.compute(sm, sol, eco_p)
        t = ec["totals"]
        stages.append({"stage": s, "name": PH.stage_name(model, s), "converged": bool(sol.converged),
                       "boe_d": float(t["Production [boe/d]"] or 0.0), "energy_cur_y": float(t["Energy + CO₂ cost [cur/y]"]),
                       "co2_t_y": float(t["CO₂ emissions [t/y]"]), "power_kW": float(t["Power demand [kW]"]),
                       "problems": [sm["units"][u]["name"] for u, v in sol.status.items() if v in ("error", "missing", "unsolved") and u in sm["units"]]})
        if progress:
            progress(s + 1, n + 1)
    final = sols[n]
    elems = element_costs(PH.stage_model(model, n), final, p)
    phases = []
    cum_capex = 0.0
    for i in range(1, n + 1):
        ph = PH.phase_list(model)[i - 1]
        added = [e for e in elems if e["Phase"] == ph["id"] and e["Change"] != "remove"]
        capex = sum(e["Installed [Mcur]"] for e in added)         # Mcur
        a, b = stages[i - 1], stages[i]
        d_boe_y = (b["boe_d"] - a["boe_d"]) * hours / 24.0
        d_rev = d_boe_y * float(p["price_boe"]) / 1e6               # MCUR/y
        d_energy = (b["energy_cur_y"] - a["energy_cur_y"]) / 1e6
        maint = capex * float(p["maint"]) / 100.0
        net = d_rev - d_energy - maint
        pay = capex / net if net > 1e-9 else None
        npv = -capex + net * _annuity(float(p["discount"]), float(p["life"]))
        cum_capex += capex
        phases.append({"phase": ph["name"], "id": ph["id"], "items": len(added), "capex_Mcur": capex,
                       "d_boe_d": b["boe_d"] - a["boe_d"], "d_revenue_Mcur_y": d_rev, "d_energy_Mcur_y": d_energy,
                       "maint_Mcur_y": maint, "net_Mcur_y": net, "payback_y": pay, "npv_Mcur": npv, "cum_capex_Mcur": cum_capex})
    return {"stages": stages, "phases": phases, "elements": elems, "params": p, "currency": eco_p["currency"],
            "total": {"capex_Mcur": cum_capex, "npv_Mcur": sum(x["npv_Mcur"] for x in phases),
                      "net_Mcur_y": sum(x["net_Mcur_y"] for x in phases)}}
