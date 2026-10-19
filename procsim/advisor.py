"""Rule-based process advisor: reads a solved flowsheet and lists improvement opportunities and design checks.

Each finding is a dict {"Level", "Unit", "Finding", "Why / what to do", "Value"}; levels are "Opportunity" (energy or product
value to gain), "Check" (a constraint or a risk) and "Info".  Numbers are screening estimates - they point at the place to
look with a rigorous study."""
from __future__ import annotations

import numpy as np

from .flowsheet import port_edges
from . import debottleneck as DB
from .pinch import collect, analyse

R = 8.314462


def _in(model, sol, uid, port="in"):
    ids = port_edges(model, uid, "in").get(port, [])
    return [sol.streams[i] for i in ids if i in sol.streams and not sol.streams[i].empty]


def _fmt_kw(v):
    return f"{v / 1000.0:.2f} MW" if abs(v) >= 1000 else f"{v:.0f} kW"


# (unit type, result key, low, high, unit, why) - generic screening ranges, not vendor limits
SIZING_RANGES = [
    ("compressor", "Power [kW]", 100.0, 60000.0, "kW", "Centrifugal compressor bodies and drivers are normally limited to a few tens of MW."),
    ("pump", "Power [kW]", 5.0, 8000.0, "kW", "Single pumps above several MW are unusual."),
    ("heater", "Duty [kW]", 10.0, 80000.0, "kW", "Fired and electric heaters above this size are normally split."),
    ("cooler", "Duty removed [kW]", 10.0, 80000.0, "kW", "Shell-and-tube coolers above this size are normally in parallel."),
    ("hx", "Duty [kW]", 10.0, 60000.0, "kW", "A single shell is limited in area; check the UA."),
    ("aircooler", "Duty removed [kW]", 50.0, 40000.0, "kW", "Air-cooler bays are limited by plot and fan count."),
    ("expander", "Power [kW]", 50.0, 40000.0, "kW", "Gas expanders of this size are special machines."),
    ("separator", "Vapour flow [kg/h]", None, 2.0e6, "kg/h", "Check the vessel diameter against the gas load."),
]


def advise(model, sol):
    out = []

    def add(level, uid, title, why, value=None):
        out.append({"Level": level, "Unit": model["units"][uid]["name"] if uid in model["units"] else "—",
                    "Finding": title, "Why / what to do": why, "Value": value})

    # ---- pressure letdown on gas: expander candidate
    for uid, u in model["units"].items():
        r = sol.results.get(uid) or {}
        if sol.status.get(uid) not in ("ok", "warning"):
            continue
        t = u["type"]
        if t == "valve":
            ins = _in(model, sol, uid)
            dP = r.get("ΔP [bar]")
            if ins and dP and ins[0].P and ins[0].P > 0:
                s, r_p = ins[0], (ins[0].P - float(dP)) / ins[0].P
                vf = r.get("Outlet vapour fraction")
                if float(dP) > 8.0 and r_p < 0.6 and vf is not None and float(vf) > 0.9 and abs(float(r.get("ΔT (JT) [°C]", 0.0) or 0.0)) < 80:
                    k = 1.3
                    n_dot = s.F * 1000.0 / 3600.0
                    W = 0.75 * n_dot * (k / (k - 1.0)) * R * s.T * (1.0 - r_p ** ((k - 1.0) / k)) / 1000.0
                    if W > 50:
                        add("Opportunity", uid, f"Gas letdown of {float(dP):.0f} bar across a valve",
                            f"An expander at 75 % efficiency could recover about {_fmt_kw(W)} (check the outlet temperature and hydrate "
                            "margin; keep the valve as the bypass). Ignore it when the Joule-Thomson cooling is the purpose of the valve.", W)
        elif t == "compressor":
            pr = r.get("Pressure ratio")
            if pr and float(pr) > 4.2:
                add("Check", uid, f"Pressure ratio {float(pr):.1f} in one compressor body",
                    "Above about 3.5-4 per stage the discharge temperature and polytropic head get high: consider two stages with an intercooler "
                    "(typically saves 5-10 % power).")
            To = r.get("Outlet T [°C]")
            if To is not None and float(To) > 170:
                add("Check", uid, f"Discharge temperature {float(To):.0f} °C",
                    "Check the seal / lube-oil limits and the material rating; an intercooler or a lower ratio per stage reduces it.")
        elif t == "flare":
            ins = _in(model, sol, uid)
            if ins:
                m = sum(s.F * s.MW for s in ins)
                if m > 50:
                    add("Opportunity", uid, f"Continuous flaring of {m:.0f} kg/h",
                        "Flare-gas recovery (compression to fuel gas or export) avoids the CO₂ and the lost gas.", m)
        elif t == "gas_turbine":
            load = r.get("Load [% of available]")
            exh = r.get("Exhaust heat at 500 °C [kW]") or next((v for k, v in r.items() if k.startswith("Exhaust heat")), None)
            if load is not None and float(load) < 50:
                add("Check", uid, f"Gas turbine at {float(load):.0f} % load",
                    "Efficiency falls at part load: a smaller machine, load-sharing between machines or power from shore gives a better fuel use.")
            if exh and float(exh) > 1000 and not any(x["type"] in ("heater", "hx") and "waste" in x["name"].lower() for x in model["units"].values()):
                add("Opportunity", uid, f"Exhaust heat {_fmt_kw(float(exh))} is not used",
                    "A waste-heat recovery unit can supply the process heating (reboilers, TEG regeneration, heating medium) and cut fuel gas.", float(exh))

    # ---- sizing-range check: results outside what one machine / exchanger normally covers (generic, illustrative limits)
    for uid, u in model["units"].items():
        r = sol.results.get(uid) or {}
        if sol.status.get(uid) not in ("ok", "warning"):
            continue
        for (typ, key, lo, hi, unit, why) in SIZING_RANGES:
            v = r.get(key)
            if u["type"] != typ or not isinstance(v, (int, float)) or isinstance(v, bool):
                continue
            v = abs(float(v))
            if hi is not None and v > hi:
                add("Check", uid, f"{key} {v:,.0f} {unit} is above a typical single unit (about {hi:,.0f} {unit})",
                    f"{why} Split the duty over parallel trains or revisit the specification.", v)
            elif lo is not None and 0 < v < lo:
                add("Check", uid, f"{key} {v:,.1f} {unit} is below a typical size (about {lo:,.0f} {unit})",
                    f"{why} A very small duty may not justify the equipment or may be outside its turn-down.", v)

    # ---- capacity (debottlenecking rows)
    try:
        for row in DB.utilisation(model, sol)[:6]:
            if row["Utilisation [%]"] >= DB.NEAR:
                lvl = "Check"
                add(lvl, row["uid"] if "uid" in row else next((k for k, x in model["units"].items() if x["name"] == row.get("Unit")), ""),
                    f"{row['Check']} at {row['Utilisation [%]']:.0f} % of its limit", row.get("Note", "") + " — see the Equipment tab / debottlenecking sweep.")
    except Exception:    # noqa: BLE001
        pass

    # ---- heat integration
    try:
        rows = collect(model, sol)
        res = analyse(rows, 10.0, ambient=15.0)
        if res["recovery"] > 0.1 * max(res["QH_now"], 1.0) and res["recovery"] > 100:
            add("Opportunity", "", "Process heat can be exchanged against process cooling",
                f"Targets for ΔTmin 10 K: {_fmt_kw(res['recovery'])} of the {_fmt_kw(res['QH_now'])} heating could come from the "
                f"{_fmt_kw(res['QC_now'])} of cooling (pinch at {res['pinch']:.0f} °C shifted). See the Heat integration tab.", res["recovery"])
    except Exception:    # noqa: BLE001
        pass

    # ---- product quality
    for sid, s in model["streams"].items():
        du = model["units"].get(s["dst"][0])
        st_ = sol.streams.get(sid)
        if not du or du["type"] != "product" or st_ is None or st_.empty:
            continue
        fpk = sol.fp.keys
        z = st_.z
        v = st_.flash.phase("V") if st_.flash else None
        if v is None or v.beta < 0.95:
            continue
        if "CO2" in fpk and z[fpk.index("CO2")] > 0.025 and z[fpk.index("C1")] > 0.5:
            add("Check", s["dst"][0], f"{du['name']}: {100 * z[fpk.index('CO2')]:.1f} mol% CO₂",
                "Above a typical 2.5 mol% sales-gas limit: a membrane (new in v7.7) or an amine contactor can bring it down.")
        if "H2O" in fpk and z[fpk.index("H2O")] > 2e-4 and z[fpk.index("C1")] > 0.5:
            add("Info", s["dst"][0], f"{du['name']}: {1e6 * z[fpk.index('H2O')]:.0f} ppmv water",
                "Dehydrate before export or transport: TEG contactor (dew point), molecular sieve (cryogenic).")

    # ---- unit warnings
    for uid, v in sol.status.items():
        if v == "warning" and uid in model["units"]:
            add("Check", uid, "Unit reports a warning", sol.errors.get(uid) or (sol.results.get(uid) or {}).get("Warning", ""))

    order = {"Opportunity": 0, "Check": 1, "Info": 2}
    out.sort(key=lambda x: (order[x["Level"]], -(x["Value"] or 0.0)))
    return out
