"""Heat-integration targets from a solved flowsheet (pinch analysis, screening level).

The heaters, coolers, air coolers and (above ambient) chillers of the flowsheet are the process streams: a cooler is a hot
stream from its inlet to its outlet temperature, a heater a cold stream.  Heat exchangers are already integrated and are left
out.  The problem-table algorithm gives the minimum hot and cold utility for a chosen minimum approach temperature; the gap to
the utilities the flowsheet uses today is the heat-recovery potential.  A unit that changes phase over a small temperature
range is given a minimum temperature span of 1 K.  The targets assume that any hot stream can exchange with any cold stream
(no distance, safety or operability constraints) - they show where to look, not what to build."""
from __future__ import annotations

import numpy as np

MIN_SPAN = 1.0


def collect(model, sol):
    """Process streams [{unit, name, kind, Ts, Tt, duty}] with temperatures in °C."""
    rows = []
    for uid, u in model["units"].items():
        t = u["type"]
        r = sol.results.get(uid) or {}
        if sol.status.get(uid) not in ("ok", "warning") or t not in ("heater", "cooler", "aircooler", "chiller"):
            continue
        Ti, To = r.get("Inlet T [°C]"), r.get("Outlet T [°C]")
        q = r.get("Duty removed [kW]") if t != "heater" else r.get("Duty [kW]")
        if Ti is None or To is None or q is None or abs(float(q)) < 1e-6:
            continue
        Ti, To, q = float(Ti), float(To), abs(float(q))
        if abs(Ti - To) < MIN_SPAN:
            To = Ti - MIN_SPAN if t != "heater" else Ti + MIN_SPAN
        rows.append({"unit": uid, "name": u["name"], "type": t, "kind": "hot" if t != "heater" else "cold",
                     "Ts": Ti, "Tt": To, "duty": q, "CP": q / abs(Ti - To)})
    return rows


def _problem_table(streams, dT):
    pts = set()
    for s in streams:
        sh = dT / 2.0
        pts.add(s["Ts"] - sh if s["kind"] == "hot" else s["Ts"] + sh)
        pts.add(s["Tt"] - sh if s["kind"] == "hot" else s["Tt"] + sh)
    T = sorted(pts, reverse=True)
    cas, cum = [], 0.0
    for hi, lo in zip(T[:-1], T[1:]):
        net = 0.0
        for s in streams:
            sh = dT / 2.0 if s["kind"] == "cold" else -dT / 2.0
            a, b = sorted((s["Ts"] + sh, s["Tt"] + sh), reverse=True)
            if a >= hi - 1e-9 and b <= lo + 1e-9:
                net += (s["CP"] if s["kind"] == "hot" else -s["CP"]) * (hi - lo)
        cum += net
        cas.append((lo, cum))
    return T, cas


def analyse(rows, dTmin=10.0, ambient=None):
    """Targets for the process streams. ``ambient``: chillers colder than it are left out (refrigeration is not a heat sink)."""
    streams = [s for s in rows if not (s["type"] == "chiller" and ambient is not None and s["Tt"] < ambient)]
    skipped = [s for s in rows if s not in streams]
    hot_now = sum(s["duty"] for s in streams if s["kind"] == "cold")        # heaters = hot utility in use
    cold_now = sum(s["duty"] for s in streams if s["kind"] == "hot")        # coolers = cold utility in use
    if not any(s["kind"] == "hot" for s in streams) or not any(s["kind"] == "cold" for s in streams):
        return {"streams": streams, "skipped": skipped, "QH_min": hot_now, "QC_min": cold_now, "QH_now": hot_now,
                "QC_now": cold_now, "recovery": 0.0, "pinch": None, "dTmin": dTmin, "hot_curve": None, "cold_curve": None,
                "grand": None, "note": "Heat recovery needs at least one hot stream (cooler) and one cold stream (heater)."}
    T, cas = _problem_table(streams, dT=dTmin)
    cum = np.array([c for _, c in cas])
    QH = max(0.0, -float(cum.min()))
    QC = float(cum[-1]) + QH
    shifted = np.array([t for t, _ in cas])
    k0 = int(np.argmin(np.abs(cum + QH))) if len(cum) else 0
    pinch_T = float(shifted[k0]) if len(cum) else None
    grand = {"T": [float(T[0])] + [float(t) for t in shifted], "Q": [QH] + [float(c + QH) for c in cum]}
    # composite curves on actual temperatures, hot curve from the cold end
    def curve(kind):
        ss = [s for s in streams if s["kind"] == kind]
        pts = sorted({s["Ts"] for s in ss} | {s["Tt"] for s in ss})
        qs = []
        for t in pts:
            q = 0.0
            for s in ss:
                lo, hi = sorted((s["Ts"], s["Tt"]))
                q += s["CP"] * max(0.0, min(t, hi) - lo)
            qs.append(q)
        return pts, qs
    ht, hq = curve("hot")
    ct, cq = curve("cold")
    cq = [q + QC for q in cq]
    # the cold curve starts where the cold utility ends: shift in Q so that it sits right of the hot curve by QC
    rec = max(0.0, min(hot_now - QH, cold_now - QC))
    return {"streams": streams, "skipped": skipped, "QH_min": QH, "QC_min": QC, "QH_now": hot_now, "QC_now": cold_now,
            "recovery": rec, "pinch": pinch_T, "dTmin": dTmin,
            "hot_curve": {"T": ht, "Q": hq}, "cold_curve": {"T": ct, "Q": cq}, "grand": grand, "note": ""}


def savings(res, p, U=500.0, cost_m2=3000.0):
    """Annual value of the heat-recovery potential found by ``analyse`` with the utility prices of procsim.economics (``p``).

    Heating saved: gas-fired (fuel and its CO₂), electric (grid) or free waste heat per the heating choice.  Cooling saved: the
    seawater pumping power of the cooling duty no longer needed.  A rough exchanger area uses the overall U and a mean temperature
    difference of twice ΔTmin; its cost is ``cost_m2`` per m² installed.  Screening numbers only."""
    from . import economics as EC
    p = {**EC.DEFAULTS, **(p or {})}
    hours = float(p["hours"])
    dQH = max(res["QH_now"] - res["QH_min"], 0.0)
    dQC = max(res["QC_now"] - res["QC_min"], 0.0)
    cost = co2 = fuel = 0.0
    if p["heating"] == "Gas-fired heater":
        fuel = dQH / (float(p["heater_eff"]) / 100.0) * 3.6 / float(p["fuel_lhv"])           # Sm³/h
        cost, co2 = fuel * hours * float(p["fuel_price"]), fuel * hours * float(p["fuel_co2"]) / 1000.0
    elif p["heating"] == "Electric":
        mwh = dQH * hours / 1000.0
        cost, co2 = mwh * float(p["el_price"]), mwh * float(p["grid_co2"])
    pump = dQC * float(p["sw_frac"]) / 100.0
    if p["driver"] == "Gas turbine":
        f2 = pump / (float(p["gt_eff"]) / 100.0) * 3.6 / float(p["fuel_lhv"])
        cost += f2 * hours * float(p["fuel_price"])
        co2 += f2 * hours * float(p["fuel_co2"]) / 1000.0
        fuel += f2
    else:
        mwh = pump * hours / 1000.0
        cost += mwh * float(p["el_price"])
        co2 += mwh * float(p["grid_co2"])
    co2_cost = co2 * float(p["co2_tax"])
    area = res["recovery"] * 1000.0 / (U * max(2.0 * float(res["dTmin"]), 1.0)) if res["recovery"] > 0 else 0.0
    capex = area * cost_m2
    total = cost + co2_cost
    return {"heating_saved_kW": dQH, "cooling_saved_kW": dQC, "fuel_saved_Sm3_h": fuel, "energy_cost_saved": cost,
            "co2_saved_t_y": co2, "co2_cost_saved": co2_cost, "total_saved": total, "area_m2": area, "capex": capex,
            "payback_y": capex / total if total > 0 else None, "currency": p["currency"]}
