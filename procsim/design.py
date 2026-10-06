"""Well and flowline design tools (v6.7): pipe-size sweep, gas-lift allocation and ESP sizing.

* **Pipe-size sweep** - the flowline (and its riser) re-solved at a list of inside diameters at the current rate:
  arrival pressure, erosional ratio, hydrate margin, severe-slugging flag and liquid inventory, with the wall
  thickness (thin-wall Barlow with a design factor and a corrosion allowance) and steel mass per kilometre as the
  cost proxy. The recommendation is the smallest diameter that meets every limit.
* **Gas-lift allocation** - the oil rate of each lifted well at the current wellhead pressure for several lift-gas
  rates (the well model, inflow and tubing, is re-solved at each point), the upper concave envelope of each curve,
  and the split of a limited gas supply that maximises the total oil (equal marginal gain).
* **ESP sizing** - total dynamic head from the intake and wellhead pressures, the mixture density and the tubing
  friction; gas at the pump intake (free-gas fraction and a gas-handling verdict); pump choice from a small catalogue
  of GENERIC illustrative curves with affinity-law frequency scaling; stages, shaft power, motor size and the
  electrical power. Replace the catalogue with vendor data for a real design.
"""
from __future__ import annotations

import math

import numpy as np

from .unitops import churchill_f, K0, UnitError

G = 9.80665
STEEL_RHO = 7850.0
STD_VOL = 0.0236454


# =================================================================================== pipe-size sweep
def wall_thickness(ID_mm, P_design_bar, smys_MPa=450.0, design_factor=0.72, corrosion_mm=3.0):
    """Wall [mm] by the thin-wall (inside-diameter) form  t = P·D / (2·f·SMYS − P)  plus the corrosion allowance."""
    s_allow = design_factor * smys_MPa                              # MPa
    p = P_design_bar * 0.1                                          # MPa
    return p * ID_mm / max(2.0 * s_allow - p, 1e-6) + corrosion_mm


def steel_mass_t_per_km(ID_mm, wall_mm):
    od = ID_mm + 2.0 * wall_mm
    return math.pi / 4.0 * ((od / 1000.0) ** 2 - (ID_mm / 1000.0) ** 2) * STEEL_RHO * 1000.0 / 1000.0


def size_sweep(fp, inlet, fl_params, riser_params, IDs_mm, P_min, P_design=None, smys=450.0, design_factor=0.72,
               corrosion_mm=3.0, progress=None):
    """Re-solve the flowline (+ riser) at each inside diameter. Returns (rows, recommended_row_or_None)."""
    from .subsea_ops import turndown_envelope
    from . import surf
    L = surf.flowline_length(fl_params)
    Pd = float(P_design) if P_design else 1.1 * inlet.P
    rows = []
    for k, ID in enumerate(sorted(set(float(x) for x in IDs_mm)), start=1):
        fl = dict(fl_params)
        fl["ID"] = ID
        r, _ = turndown_envelope(fp, inlet, fl, riser_params, rate_factors=(1.0,), P_min=P_min)
        r = r[0]
        t = wall_thickness(ID, Pd, smys, design_factor, corrosion_mm)
        mass = steel_mass_t_per_km(ID, t)
        row = {"ID [mm]": ID, "Wall [mm]": t, "Steel [t/km]": mass, "Steel in the flowline [kt]": mass * L / 1000.0 / 1000.0,
               "Arrival P [bar(a)]": r.get("Arrival P [bar(a)]"),
               "Pressure drop [bar]": (inlet.P - r["Arrival P [bar(a)]"]) if r.get("Arrival P [bar(a)]") is not None else None,
               "Erosional ratio [-]": r.get("Erosional ratio [-]"),
               "Min. hydrate margin [°C]": r.get("Min. hydrate margin [°C]"),
               "Liquid inventory [m³]": r.get("Liquid inventory [m³]"),
               "Flowline regime": r.get("Flowline regime"),
               "Riser-base slugging": r.get("Riser-base slugging"),
               "Feasible": bool(r.get("Feasible")), "Limits": r.get("Limits", "")}
        rows.append(row)
        if progress:
            progress(k / len(IDs_mm))
    ok = [r for r in rows if r["Feasible"]]
    rec = min(ok, key=lambda r: r["ID [mm]"]) if ok else None
    return rows, rec


# =================================================================================== gas-lift allocation
def concave_envelope(points):
    """Upper concave envelope of (x, y) points (x sorted): a monotone, diminishing-returns performance curve."""
    pts = sorted((float(x), float(y)) for x, y in points)
    hull = []
    for p in pts:
        while len(hull) >= 2:
            (x1, y1), (x2, y2) = hull[-2], hull[-1]
            if (y2 - y1) * (p[0] - x1) <= (p[1] - y1) * (x2 - x1):      # hull[-1] lies on or below the chord: drop it
                hull.pop()
            else:
                break
        hull.append(p)
    # no benefit past the peak: keep the curve non-decreasing
    out = []
    for p in hull:
        out.append(p)
        if len(out) >= 2 and p[1] <= out[-2][1]:
            out.pop()
            break
    return out


def allocate_gas(curves, total, min_each=None):
    """Split `total` lift gas among wells to maximise the sum of oil rates. curves[i] = concave [(gas, oil), ...]
    starting at the smallest gas rate considered. Greedy on the segment slopes (exact for concave piecewise-linear
    curves). Returns the gas allocated to each well."""
    n = len(curves)
    alloc = [c[0][0] for c in curves]
    used = sum(alloc)
    if used > total + 1e-12:
        raise ValueError("the supply is smaller than the lowest rate of the curves")
    segs = []
    for i, c in enumerate(curves):
        for (x0, y0), (x1, y1) in zip(c, c[1:]):
            if x1 > x0:
                segs.append(((y1 - y0) / (x1 - x0), i, x1 - x0, x0))
    segs.sort(key=lambda t: -t[0])
    left = total - used
    for slope, i, width, x0 in segs:
        if slope <= 0 or left <= 1e-15:
            break
        if abs(alloc[i] - x0) > 1e-9:                    # segments of one well are taken in order (concave: slopes fall)
            continue
        take = min(width, left)
        alloc[i] += take
        left -= take
    return alloc


def curve_value(curve, x):
    """Linear interpolation on a curve (flat beyond its ends)."""
    xs = [p[0] for p in curve]
    ys = [p[1] for p in curve]
    return float(np.interp(x, xs, ys))


def gaslift_curves(model, sol, factors=(0.0, 0.5, 1.0, 1.5, 2.0), progress=None):
    """Oil-rate performance curve of every lifted well at its present wellhead pressure.
    Returns a list of {"Well", "points": [(gas MSm³/d, oil Sm³/d, water Sm³/d, formation gas MSm³/d)], "curve"
    (concave envelope of gas, oil), "gas_now", "oil_now"}; a well unit with identical parallel wells counts as one."""
    from . import surf
    from .flowsheet import port_edges
    from .streams import make_stream
    fp = sol.fp
    wells = []
    for uid, u in model["units"].items():
        if u["type"] != "well":
            continue
        ins = port_edges(model, uid, "in")
        lifts = ins.get("lift") or []
        sins = ins.get("in") or []
        if not lifts or not sins or lifts[0] not in sol.streams or sins[0] not in sol.streams:
            continue
        lift, res_s = sol.streams[lifts[0]], sol.streams[sins[0]]
        if lift.empty or res_s.empty or uid not in sol.results:
            continue
        wells.append((uid, u, res_s, lift, sol.results[uid]))
    out = []
    total = max(len(wells) * len(factors), 1)
    k = 0
    for uid, u, res_s, lift, r in wells:
        whp = float(r["Wellhead P [bar(a)]"])
        gas_now = float(r.get("Gas-lift rate [MSm³/d]", 0.0))               # the whole group of identical wells
        nw = surf.n_wells(u)
        pts = []
        for f in factors:
            k += 1
            try:
                if f <= 0:
                    F = surf.solve_rate_for_whp(u, res_s, fp, whp, lift=None)
                    g_l = 0.0
                else:
                    sc = make_stream("", fp, lift.F * f, lift.z, lift.flash)
                    F = surf.solve_rate_for_whp(u, res_s, fp, whp, lift=sc)
                    g_l = gas_now * f
                gas, oil, wat = surf.standard_rates(make_stream("", fp, F, res_s.z, res_s.flash), fp)
                pts.append((g_l, oil, wat, gas))
            except (UnitError, ValueError, ZeroDivisionError):
                pass
            if progress:
                progress(k / total)
        if not pts:
            continue
        env = concave_envelope([(p[0], p[1]) for p in pts])
        out.append({"Well": u["name"], "uid": uid, "WHP [bar(a)]": whp, "gas_now": gas_now,
                    "oil_now": float(r.get("Oil/condensate rate [Sm³/d]", 0.0)), "points": pts, "curve": env,
                    "n_wells": nw})
    return out


def allocation_table(curves, total):
    """Optimal split of `total` MSm³/d of lift gas (per well, as in the curves) and the gain over the present split."""
    if not curves:
        return []
    env = [c["curve"] for c in curves]
    alloc = allocate_gas(env, total)
    rows = []
    for c, a in zip(curves, alloc):
        oil = curve_value(c["curve"], a)
        now = curve_value(c["curve"], c["gas_now"])
        rows.append({"Well": c["Well"], "Lift gas now [MSm³/d]": c["gas_now"], "Optimal lift gas [MSm³/d]": a,
                     "Oil now [Sm³/d]": now, "Oil at the optimum [Sm³/d]": oil, "Gain [Sm³/d]": oil - now,
                     "Marginal gain at the optimum [Sm³/MSm³]": _slope(c["curve"], a)})
    return rows


def _slope(curve, x):
    for (x0, y0), (x1, y1) in zip(curve, curve[1:]):
        if x0 <= x <= x1 and x1 > x0:
            return (y1 - y0) / (x1 - x0)
    return 0.0


# =================================================================================== ESP
# GENERIC illustrative pump series (not vendor data): best-efficiency flow [m³/d] and head per stage [m] at 60 Hz,
# best efficiency, and the rated range of the series.
ESP_CATALOGUE = [
    {"Series": "A (small)", "Q_bep": 200.0, "H_stage": 6.0, "eta": 0.52, "Q_min": 120.0, "Q_max": 280.0, "D_mm": 101.6},
    {"Series": "B", "Q_bep": 500.0, "H_stage": 7.5, "eta": 0.62, "Q_min": 300.0, "Q_max": 700.0, "D_mm": 114.3},
    {"Series": "C", "Q_bep": 900.0, "H_stage": 8.0, "eta": 0.66, "Q_min": 550.0, "Q_max": 1250.0, "D_mm": 138.7},
    {"Series": "D", "Q_bep": 1500.0, "H_stage": 9.0, "eta": 0.68, "Q_min": 900.0, "Q_max": 2100.0, "D_mm": 138.7},
    {"Series": "E", "Q_bep": 2500.0, "H_stage": 10.0, "eta": 0.70, "Q_min": 1500.0, "Q_max": 3500.0, "D_mm": 172.7},
    {"Series": "F (large)", "Q_bep": 4000.0, "H_stage": 11.0, "eta": 0.72, "Q_min": 2400.0, "Q_max": 5600.0, "D_mm": 203.2},
]
MOTOR_KW = [15, 22, 30, 37, 45, 56, 75, 93, 112, 149, 186, 224, 260, 298, 373, 450, 560, 750, 900, 1100]


def esp_stage(series, Q_m3d, freq_hz, rho):
    """(head per stage [m], efficiency, shaft power per stage [kW]) at the intake flow and frequency (affinity laws:
    Q ∝ f, H ∝ f²). Generic curves: H/Hbep = 1.2 − 0.2 (Q/Qbep)², η/ηbep = 2x − x²."""
    k = freq_hz / 60.0
    x = Q_m3d / max(series["Q_bep"] * k, 1e-9)
    H = series["H_stage"] * k * k * (1.2 - 0.2 * x * x)
    eta = max(series["eta"] * (2.0 * x - x * x), 0.05)
    P = rho * G * (Q_m3d / 86400.0) * H / eta / 1000.0
    return H, eta, P


def esp_design(fp, stream, P_intake, T_intake_C, P_wh, TVD, tubing_ID_mm, rough_mm=0.045, freqs=(50.0, 60.0),
               gvf_limit=0.10, margin_pct=10.0, catalogue=None):
    """ESP sizing for the fluid of `stream` (composition, as flowing at the intake). Returns a dict with the
    duty and a list of pump options sorted by efficiency."""
    from .streams import make_stream
    from .unitops import _phase_split
    st_in = make_stream("", fp, stream.F, stream.z, fp.pt_flash(stream.z, T_intake_C + K0, P_intake, stream.flash.Kset))
    st_out = make_stream("", fp, stream.F, stream.z, fp.pt_flash(stream.z, T_intake_C + K0 - 20.0, P_wh, st_in.flash.Kset))
    D = tubing_ID_mm / 1000.0
    A = math.pi * D * D / 4.0

    def state(st):
        qg, ql, rg, rl, mg, ml = _phase_split(fp, st)
        mass = qg * rg + ql * rl
        qt = qg + ql
        return qg, ql, rg, rl, mg, ml, (mass / qt if qt > 0 else rl), qt

    qg1, ql1, rg1, rl1, mg1, ml1, rho1, qt1 = state(st_in)
    qg2, ql2, rg2, rl2, mg2, ml2, rho2, qt2 = state(st_out)
    gvf = qg1 / qt1 if qt1 > 0 else 0.0
    rho_avg = 0.5 * (rho1 + rho2)
    q_avg = 0.5 * (qt1 + qt2)
    v = q_avg / A
    mu = 0.5 * ((qg1 * mg1 + ql1 * ml1) / max(qt1, 1e-12) + (qg2 * mg2 + ql2 * ml2) / max(qt2, 1e-12))
    Re = rho_avg * v * D / max(mu, 1e-6)
    f = churchill_f(Re, rough_mm / tubing_ID_mm)
    dp_hyd = rho_avg * G * TVD / 1e5
    dp_fric = f * TVD / D * rho_avg * v * v / 2.0 / 1e5
    dP = P_wh + dp_hyd + dp_fric - P_intake
    if dP <= 0:
        raise ValueError("the intake pressure already lifts the fluid to the wellhead pressure: no pump needed")
    TDH = dP * 1e5 / (rho1 * G)                             # head in metres of the fluid at the pump intake
    Q_d = qt1 * 86400.0                                      # m³/d at the intake (liquid + free gas)
    opts = []
    cat = catalogue or ESP_CATALOGUE
    for s in cat:
        for fq in freqs:
            k = fq / 60.0
            x = Q_d / (s["Q_bep"] * k)
            lo, hi = s["Q_min"] / s["Q_bep"], s["Q_max"] / s["Q_bep"]
            H, eta, P = esp_stage(s, Q_d, fq, rho1)
            n = int(math.ceil(TDH / H)) if H > 0 else 0
            kw = n * P
            need = kw * (1.0 + margin_pct / 100.0)
            motor = next((m for m in MOTOR_KW if m >= need), None)
            opts.append({"Series": s["Series"], "Frequency [Hz]": fq, "Q / Q_bep [-]": x,
                         "In the recommended range": lo <= x / 1.0 <= hi, "Stages": n,
                         "Head per stage [m]": H, "Pump efficiency [%]": 100.0 * eta, "Shaft power [kW]": kw,
                         "Motor [kW]": motor, "Housing OD [mm]": s["D_mm"],
                         "Electrical power [kW]": kw / 0.88 if kw else 0.0})
    good = [o for o in opts if o["In the recommended range"] and o["Motor [kW]"] and o["Stages"] > 0]
    good.sort(key=lambda o: -o["Pump efficiency [%]"])
    verdict = ("no gas handling needed" if gvf < 0.05 else
               "gas handler or separator advisable" if gvf <= gvf_limit else
               "above the gas-handling limit: add a separator / gas handler or raise the intake pressure"
               if gvf <= 0.45 else "free gas too high for an ESP: consider gas lift")
    return {"Intake flow [m³/d]": Q_d, "Intake liquid [m³/d]": ql1 * 86400.0, "Free gas fraction at the intake [-]": gvf,
            "Gas handling": verdict, "Mixture density at the intake [kg/m³]": rho1,
            "Mean mixture density in the tubing [kg/m³]": rho_avg, "Hydrostatic [bar]": dp_hyd, "Friction [bar]": dp_fric,
            "Required pressure rise [bar]": dP, "Total dynamic head [m]": TDH, "Options": good, "All": opts}
