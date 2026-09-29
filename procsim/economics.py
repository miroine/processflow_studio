"""Energy cost and CO2 emissions of a solved flowsheet (screening OPEX / emissions).

Every energy stream is assigned to a utility:

* shaft work (compressors, pumps, air-cooler fans; expanders credit) -> the power source:
  grid electricity (price, grid emission factor) or gas-turbine drivers (fuel gas from the
  turbine efficiency, fuel LHV and a CO2 factor per Sm3 of fuel);
* positive heat (heaters, reboilers, heated separators) -> gas-fired heater (fuel at a
  thermal efficiency), electric heating, or free waste heat;
* negative heat (coolers, condensers, anti-surge coolers, pipe heat loss) -> seawater cooling
  charged as pumping power (a fraction of the duty); air coolers are charged through their
  fan power only, and heat lost from pipes to the environment costs nothing.

Production for the intensity is the sum of product streams: gas at 1000 Sm3 = 1 Sm3 o.e.,
liquids 1 Sm3 = 1 Sm3 o.e., and 1 Sm3 o.e. = 6.29 boe.

All defaults are illustrative placeholders - enter your own prices and factors.
"""
from __future__ import annotations

DEFAULTS = {
    "currency": "NOK",
    "hours": 8400.0,              # operating hours per year
    "driver": "Grid electricity",  # or "Gas turbine"
    "el_price": 600.0,            # currency / MWh
    "grid_co2": 0.02,             # t CO2 / MWh (hydro-dominated grid; enter your own)
    "gt_eff": 35.0,               # % simple-cycle gas-turbine efficiency
    "fuel_lhv": 36.0,             # MJ / Sm3 fuel gas
    "fuel_price": 2.5,            # currency / Sm3 (value of the gas burnt)
    "fuel_co2": 2.34,             # kg CO2 / Sm3 fuel gas
    "heating": "Gas-fired heater",  # or "Electric", "Waste heat (free)"
    "heater_eff": 85.0,           # %
    "sw_frac": 1.5,               # % of cooling duty needed as seawater pumping power
    "co2_tax": 2000.0,            # currency / t CO2 (tax + quota; enter your own)
}
BOE_PER_SM3OE = 6.29


def params(model):
    p = dict(DEFAULTS)
    p.update(model.get("economics") or {})
    return p


def _unit_types(model):
    return {u["name"]: u["type"] for u in model["units"].values()}


def compute(model, sol, p=None):
    """Return {"rows": [...], "totals": {...}} for the solved flowsheet."""
    from .streams import stream_properties
    p = params(model) if p is None else {**DEFAULTS, **p}
    hours = float(p["hours"])
    types = _unit_types(model)
    rows = []

    def fuel_for(kW):                     # Sm3/h of fuel gas for a thermal input in kW
        return kW * 3.6 / float(p["fuel_lhv"])

    def power_cost(kW_el):
        """(currency/y, t CO2/y, fuel Sm3/h) to supply kW_el of shaft power."""
        if p["driver"] == "Gas turbine":
            fuel = fuel_for(kW_el / (float(p["gt_eff"]) / 100.0))
            return fuel * hours * float(p["fuel_price"]), fuel * hours * float(p["fuel_co2"]) / 1000.0, fuel
        mwh = kW_el * hours / 1000.0
        return mwh * float(p["el_price"]), mwh * float(p["grid_co2"]), 0.0

    for e in sol.energy:
        utype = types.get(e.unit, "")
        duty = e.duty_kW
        if abs(duty) < 1e-9:
            continue
        if e.kind == "work":
            cat = "Power (credit)" if duty < 0 else "Power"
            cost, co2, fuel = power_cost(duty)
            util = p["driver"]
            energy = duty * hours / 1000.0
        elif duty > 0:
            cat = "Heating"
            energy = duty * hours / 1000.0
            if p["heating"] == "Gas-fired heater":
                fuel = fuel_for(duty / (float(p["heater_eff"]) / 100.0))
                cost, co2 = fuel * hours * float(p["fuel_price"]), fuel * hours * float(p["fuel_co2"]) / 1000.0
            elif p["heating"] == "Electric":
                mwh = duty * hours / 1000.0
                cost, co2, fuel = mwh * float(p["el_price"]), mwh * float(p["grid_co2"]), 0.0
            else:
                cost = co2 = fuel = 0.0
            util = p["heating"]
        else:
            cat = "Cooling"
            energy = -duty * hours / 1000.0
            if utype in ("aircooler", "pipe"):
                cost = co2 = fuel = 0.0
                util = "Air (fan power counted as power)" if utype == "aircooler" else "Heat loss to ambient"
            else:
                pump = -duty * float(p["sw_frac"]) / 100.0
                cost, co2, fuel = power_cost(pump)
                util = f"Seawater (pumping {float(p['sw_frac']):g} % of duty)"
        rows.append({"Energy stream": e.name, "Unit": e.unit, "Category": cat, "Utility": util,
                     "Duty [kW]": duty, "Energy [MWh/y]": energy, "Fuel gas [Sm³/h]": fuel,
                     "Energy cost [cur/y]": cost, "CO₂ [t/y]": co2,
                     "CO₂ cost [cur/y]": co2 * float(p["co2_tax"])})

    gas = liq = 0.0
    for sid, s in model["streams"].items():
        if model["units"][s["dst"][0]]["type"] != "product":
            continue
        st_ = sol.streams.get(sid)
        if st_ is None or st_.empty:
            continue
        pr = stream_properties(st_, sol.fp)
        if pr["Vapour fraction"] >= 0.999:
            gas += pr["Std gas flow [MSm³/d]"]
        elif pr["Vapour fraction"] <= 1e-6 and st_.flash.phase("W") is None:
            liq += pr["Std liq vol flow [m³/h]"] * 24.0      # hydrocarbon liquids only (not produced water)
    sm3oe_d = gas * 1e6 / 1000.0 + liq
    boe_y = sm3oe_d * BOE_PER_SM3OE * hours / 24.0
    tot_cost = sum(r["Energy cost [cur/y]"] for r in rows)
    tot_co2 = sum(r["CO₂ [t/y]"] for r in rows)
    tot_co2_cost = tot_co2 * float(p["co2_tax"])
    fuel = sum(r["Fuel gas [Sm³/h]"] for r in rows)
    totals = {
        "Energy cost [cur/y]": tot_cost,
        "CO₂ emissions [t/y]": tot_co2,
        "CO₂ cost [cur/y]": tot_co2_cost,
        "Energy + CO₂ cost [cur/y]": tot_cost + tot_co2_cost,
        "Fuel gas [Sm³/h]": fuel,
        "Fuel gas [MSm³/y]": fuel * hours / 1e6,
        "Production [Sm³ o.e./d]": sm3oe_d,
        "Production [boe/d]": sm3oe_d * BOE_PER_SM3OE,
        "CO₂ intensity [kg/boe]": tot_co2 * 1000.0 / boe_y if boe_y > 0 else None,
        "Cost per boe [cur/boe]": (tot_cost + tot_co2_cost) / boe_y if boe_y > 0 else None,
        "Power demand [kW]": sum(r["Duty [kW]"] for r in rows if r["Category"].startswith("Power")),
        "Heating demand [kW]": sum(r["Duty [kW]"] for r in rows if r["Category"] == "Heating"),
        "Cooling demand [kW]": -sum(r["Duty [kW]"] for r in rows if r["Category"] == "Cooling"),
    }
    return {"rows": rows, "totals": totals, "params": p}
