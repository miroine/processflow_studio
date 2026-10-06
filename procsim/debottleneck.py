"""Topside debottlenecking: capacity utilisation of every unit and a throughput sweep that finds which limit is
reached first (v7.1).

* **Utilisation** - read from the solved flowsheet: gas load of scrubbers and of separators with a given diameter
  (Souders-Brown), compressor flow against the stonewall point of its curve, compressor driver power against its
  rating, control-valve opening against a 85 % design opening, and the erosional-velocity ratio of lines.  100 %
  is the limit of that item.
* **Sweep** - all feeds are scaled by a set of factors, the flowsheet is re-solved at each, and the factor at which
  each utilisation reaches 100 % is interpolated (extrapolated from the last two points when it is beyond the sweep).
  The smallest factor is the first bottleneck, the next ones follow in order.  A remedy is suggested per kind of limit.

Only items with a capacity in the model can be checked (a cooler or heater has no rating here).  Screening level:
utilisations are not strictly proportional to the rate, which is why the flowsheet is re-solved instead of scaled."""
from __future__ import annotations

import copy

VALVE_MAX_OPEN = 85.0           # % opening taken as the design limit of a control valve
NEAR = 90.0                     # % utilisation flagged as "near the limit"

REMEDY = {
    "Gas load": "larger vessel or a higher-capacity internal (vane pack / axial cyclones), or a parallel vessel",
    "Stonewall": "re-wheel or re-stage the compressor, add a parallel machine, or reduce the flow through it "
                 "(bypass, higher suction pressure)",
    "Driver": "a larger driver or a de-rated duty (lower pressure ratio, colder suction, inlet cooling for a turbine)",
    "Valve opening": "a larger trim / valve size, or a lower pressure drop",
    "Erosional": "a larger line diameter, a loop (parallel line) or a lower velocity",
}


def _row(uid, u, check, kind, value, limit, util, note=""):
    return {"uid": uid, "Unit": u["name"], "Type": u["type"], "Check": check, "kind": kind, "Value": value,
            "Limit": limit, "Utilisation [%]": util, "Note": note}


def utilisation(model, sol):
    """Capacity rows for every checkable unit of a solved flowsheet."""
    rows = []
    for uid, u in model["units"].items():
        r = sol.results.get(uid) or {}
        t, p = u["type"], u.get("params", {})
        if sol.status.get(uid) in ("error", "missing", "unsolved"):
            continue
        if t in ("scrubber", "separator", "separator3"):
            load = r.get("Gas load [% of max]")
            if load is not None and float(p.get("ID", 0.0) or 0.0) > 0:
                rows.append(_row(uid, u, "Gas load (Souders-Brown)", "Gas load", float(load), 100.0, float(load),
                                 f"{p['ID']:.0f} mm; needs ≥ {r.get('Required diameter [mm]', 0):.0f} mm"))
        elif t == "compressor":
            sw = r.get("Stonewall margin [%]")
            if sw is not None:
                rows.append(_row(uid, u, "Compressor flow vs stonewall", "Stonewall", 100.0 - float(sw), 100.0,
                                 100.0 - float(sw), f"surge margin {r.get('Surge margin [%]', float('nan')):.0f} %"))
            dm = r.get("Driver margin [%]")
            if dm is not None:
                avail = r.get("Driver power available [kW]")
                rows.append(_row(uid, u, "Driver power vs rating", "Driver", float(r["Power [kW]"]), float(avail),
                                 100.0 / (1.0 + float(dm) / 100.0), "available power at ambient"))
        elif t == "valve":
            op = r.get("Valve opening [%]")
            if op is not None:
                rows.append(_row(uid, u, "Valve opening", "Valve opening", float(op), VALVE_MAX_OPEN,
                                 100.0 * float(op) / VALVE_MAX_OPEN, f"design limit {VALVE_MAX_OPEN:.0f} % open"))
        elif t in ("pipe", "flowline", "riser"):
            ev = r.get("Erosional velocity ratio (API RP 14E, C=100)")
            if ev is not None:
                rows.append(_row(uid, u, "Erosional velocity ratio", "Erosional", float(ev), 1.0, 100.0 * float(ev)))
    for x in rows:
        x["Status"] = "over capacity" if x["Utilisation [%]"] > 100.0 else (
            "near the limit" if x["Utilisation [%]"] >= NEAR else "ok")
    rows.sort(key=lambda x: -x["Utilisation [%]"])
    return rows


def scaled_model(model, factor, feeds=None):
    """A copy of the flowsheet with the flow of the chosen feeds (default all) multiplied by ``factor``."""
    m = copy.deepcopy(model)
    for uid, u in m["units"].items():
        if u["type"] == "feed" and (feeds is None or uid in feeds):
            u["params"]["flow"] = float(u["params"].get("flow", 0.0)) * factor
    return m


def crossing(factors, util, limit=100.0):
    """(factor where the utilisation reaches the limit, how) by linear interpolation; how is 'interpolated',
    'extrapolated' (beyond the sweep), 'already over' (over at the lowest factor) or 'none' (not rising)."""
    pts = [(f, u) for f, u in zip(factors, util) if u is not None]
    if len(pts) < 2:
        return None, "none"
    if pts[0][1] >= limit:
        return pts[0][0], "already over"
    for (f1, u1), (f2, u2) in zip(pts, pts[1:]):
        if u1 < limit <= u2:
            return f1 + (limit - u1) / (u2 - u1) * (f2 - f1), "interpolated"
    (f1, u1), (f2, u2) = pts[-2], pts[-1]
    if u2 > u1 + 1e-9:
        return f2 + (limit - u2) / (u2 - u1) * (f2 - f1), "extrapolated"
    return None, "none"


def sweep(model, factors=(0.8, 1.0, 1.2, 1.4, 1.6), feeds=None, solver=None, progress=None):
    """Re-solve the flowsheet at each throughput factor.  Returns a dict with the factors, one series per check
    (utilisation per factor, None where the unit did not solve), the crossing factors ranked, and the failures."""
    from .flowsheet import solve
    solver = solver or solve
    factors = sorted({float(f) for f in factors if float(f) > 0})
    series, fails = {}, {}
    for k, f in enumerate(factors):
        m = scaled_model(model, f, feeds)
        try:
            sol = solver(m)
            rows = utilisation(m, sol)
            bad = [m["units"][u]["name"] for u, s in sol.status.items() if s in ("error", "missing", "unsolved")]
            if bad:
                fails[f] = "not solved: " + ", ".join(bad[:3])
        except Exception as e:                                       # noqa: BLE001  (a solver failure at a high rate)
            rows, fails[f] = [], f"{type(e).__name__}: {e}"
        got = {(x["uid"], x["Check"]): x for x in rows}
        for key, x in got.items():
            s = series.setdefault(key, {"Unit": x["Unit"], "Check": x["Check"], "kind": x["kind"], "util": {}})
            s["util"][f] = x["Utilisation [%]"]
        if progress:
            progress(k + 1, len(factors))
    out = []
    for key, s in series.items():
        ut = [s["util"].get(f) for f in factors]
        fx, how = crossing(factors, ut)
        out.append({"Unit": s["Unit"], "Check": s["Check"], "kind": s["kind"], "util": ut, "Limit reached at (× base)": fx,
                    "How": how, "Remedy": REMEDY.get(s["kind"], "")})
    out.sort(key=lambda r: (r["Limit reached at (× base)"] is None, r["Limit reached at (× base)"] or 0.0))
    return {"factors": factors, "series": out, "fails": {str(k): v for k, v in fails.items()}}


def summary(sw, base_factor=1.0, group=0.03):
    """Plain-language findings (list of markdown lines).  Limits within ``group`` (3 %) of the first one are reported
    together: upgrading only one of them gains almost nothing."""
    s = [r for r in sw["series"] if r["Limit reached at (× base)"] is not None]
    if not s:
        return ["No checked item reaches its limit within the sweep (or none of the units has a capacity set)."]
    f0 = s[0]["Limit reached at (× base)"]
    first = [r for r in s if r["Limit reached at (× base)"] <= f0 * (1.0 + group) + 1e-12]
    rest = [r for r in s if r not in first]
    names = "; ".join(f"**{r['Unit']}** ({r['Check'].lower()})" for r in first)
    out = []
    if f0 <= base_factor + 1e-9:
        out.append(f"Already at the limit at the base rate: {names}. Debottleneck before raising the rate: "
                   f"{first[0]['Remedy']}.")
    else:
        out.append(f"The first limit{'s are' if len(first) > 1 else ' is'} {names} at **{100 * f0:.0f} %** of the base "
                   f"rate ({100 * (f0 - 1):.0f} % headroom). To go beyond: {first[0]['Remedy']}"
                   + (" (for each of them)." if len(first) > 1 else "."))
    if rest:
        nxt = rest[0]
        fn = nxt["Limit reached at (× base)"]
        out.append(f"After that the next limit is **{nxt['Unit']}** ({nxt['Check'].lower()}) at {100 * fn:.0f} %, so "
                   f"removing the first {'ones' if len(first) > 1 else 'one'} gains {100 * (fn - f0):.0f} % of the base "
                   f"rate: {nxt['Remedy']}.")
    for k, v in (sw.get("fails") or {}).items():
        out.append(f"At {float(k):.2f} × the base rate the flowsheet did not solve completely ({v}).")
    return out
