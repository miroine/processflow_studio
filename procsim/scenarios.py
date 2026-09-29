"""Scenarios: named snapshots of a flowsheet with their key results, for side-by-side comparison.

A scenario stores a copy of the model (without the other scenarios) and the KPIs of its solution at the time it
was saved, so the comparison is instant and survives in the saved flowsheet file (``model["scenarios"]``).
"""
from __future__ import annotations

import copy
import datetime as _dt
import math

from .streams import stream_properties
from .flowsheet import port_edges

MAX_SCENARIOS = 12


def snapshot(model):
    m = copy.deepcopy(model)
    m.pop("scenarios", None)
    return m


def _arrival(model, sol):
    """Stream entering the unit downstream of the first riser (the topside arrival), if any."""
    for uid, u in sorted(model["units"].items(), key=lambda kv: kv[1]["name"]):
        if u["type"] == "riser":
            outs = [s for lst in port_edges(model, uid, "out").values() for s in lst]
            if outs and outs[0] in sol.streams and not sol.streams[outs[0]].empty:
                return sol.streams[outs[0]]
    return None


def scenario_kpis(model, sol):
    """Ordered KPI dict (labels carry SI units in brackets; None where not applicable)."""
    from . import economics, subsea_design as sd, subsea_ops as so
    k = {}
    bad = [u for u, v in sol.status.items() if v in ("error", "missing", "unsolved")]
    k["Solver status"] = "converged" if sol.converged and not bad else f"{len(bad)} object(s) not solved"
    gas = liq = 0.0
    for sid, s in model["streams"].items():
        if model["units"][s["dst"][0]]["type"] != "product" or sid not in sol.streams or sol.streams[sid].empty:
            continue
        p = stream_properties(sol.streams[sid], sol.fp)
        if p["Vapour fraction"] >= 0.999:
            gas += p["Std gas flow [MSm³/d]"]
        elif p["Vapour fraction"] <= 1e-6:
            liq += p["Std liq vol flow [m³/h]"] * 24.0
    k["Gas products [MSm³/d]"] = gas
    k["Liquid products [Sm³/d]"] = liq
    try:
        t = economics.compute(model, sol)["totals"]
        k["Power demand [kW]"] = t["Power demand [kW]"]
        k["Heating demand [kW]"] = t["Heating demand [kW]"]
        k["Cooling demand [kW]"] = t["Cooling demand [kW]"]
        k["CO₂ emissions [t/y]"] = t["CO₂ emissions [t/y]"]
        k["CO₂ intensity [kg/boe]"] = t["CO₂ intensity [kg/boe]"]
    except Exception:
        pass
    wells = [u for u, x in model["units"].items() if x["type"] == "well" and u in sol.results]
    surf_units = any(x["type"] in ("well", "flowline", "riser", "template", "subsea_booster") for x in model["units"].values())
    if surf_units:
        k["Wells"] = len(wells)
        k["Well gas rate [MSm³/d]"] = sum(sol.results[u].get("Gas rate [MSm³/d]", 0.0) for u in wells) if wells else None
        whp = [sol.results[u]["Wellhead P [bar(a)]"] for u in wells if sol.results[u].get("Wellhead P [bar(a)]")]
        k["Lowest wellhead P [bar(a)]"] = min(whp) if whp else None
        arr = _arrival(model, sol)
        k["Arrival P [bar(a)]"] = arr.P if arr else None
        k["Arrival T [°C]"] = (arr.T - 273.15) if arr else None
        margins = [r.get("Min. hydrate margin along line [°C]") for r in sol.results.values()]
        margins = [m for m in margins if m is not None]
        k["Min. hydrate margin [°C]"] = min(margins) if margins else None
        _, el = sd.booster_power_kW(model, sol)
        k["Subsea booster power [kW]"] = el
        try:
            k["CAPEX [MUSD]"] = sd.equipment_list(model, sol)[1]["Total CAPEX [MUSD]"]
        except Exception:
            k["CAPEX [MUSD]"] = None
        try:
            cd = so.cooldown(model, sol)
            nt = min((r["No-touch time [h]"] for r in cd), default=None)
            k["No-touch time [h]"] = None if nt is None or math.isinf(nt) else nt
        except Exception:
            k["No-touch time [h]"] = None
        try:
            sl = so.slug_assessment(model, sol)
            k["Design surge volume [m³]"] = max((r["Design surge volume [m³]"] for r in sl), default=None)
        except Exception:
            k["Design surge volume [m³]"] = None
    return k


def save(model, sol, name):
    """Add (or replace, by name) a scenario built from the current model and its solution."""
    name = (name or "").strip() or f"Case {len(model.get('scenarios') or []) + 1}"
    sc = {"name": name, "saved": _dt.datetime.now().strftime("%d %b %Y %H:%M"), "model": snapshot(model),
          "kpis": scenario_kpis(model, sol)}
    lst = [s for s in (model.get("scenarios") or []) if s["name"] != name]
    if len(lst) >= MAX_SCENARIOS:
        raise ValueError(f"at most {MAX_SCENARIOS} scenarios - delete one first")
    lst.append(sc)
    model["scenarios"] = lst
    return sc


def restore(model, name):
    """A copy of the scenario's model carrying the current scenario list (to load into the session)."""
    sc = next(s for s in model.get("scenarios") or [] if s["name"] == name)
    m = copy.deepcopy(sc["model"])
    m["scenarios"] = copy.deepcopy(model.get("scenarios") or [])
    return m


def delete(model, name):
    model["scenarios"] = [s for s in (model.get("scenarios") or []) if s["name"] != name]


def comparison(scenarios, current=None):
    """(KPI labels, {column name: {label: value}}) for the saved scenarios plus, optionally, the current case."""
    cols = {}
    for s in scenarios:
        cols[s["name"]] = s["kpis"]
    if current is not None:
        cols["Current (unsaved)"] = current
    labels = []
    for kp in cols.values():
        for lab in kp:
            if lab not in labels:
                labels.append(lab)
    return labels, cols
