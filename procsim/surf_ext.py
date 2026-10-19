"""More subsea (SURF) equipment, v7.8.

* ``subsea_choke``       - stand-alone production choke module (manifold or riser base): opening (Cv), outlet pressure or ΔP.
* ``subsea_manifold``    - production manifold: several branches into one header, header velocity against the erosional limit.
* ``mpfm``               - multiphase flow meter: pass-through with the metered rates, GVF/WLR window and the uncertainty.
* ``pig_station``        - pig launcher / receiver: transit time, sweep-out liquid volume against the receiver capacity.
* ``subsea_hydrocyclone``- de-oiling hydrocyclone: clean water out, oil-rich reject, oil-in-water in ppm.
* ``subsea_heater``      - electric / hydraulic process heater: duty or outlet temperature, with the electrical power demand.

Generic screening models; equipment cost rows are illustrative (``data/surf_catalogue.csv``)."""
from __future__ import annotations

import math

import numpy as np

from .streams import EnergyStream, make_stream, zero_stream
from .unitops import UnitError, K0, _f, _s, _one, _live, _mix, _ph, _check_P, _hyd_margin, _energy

CATEGORY = "Subsea (SURF)"   # = surf.CATEGORY (surf imports this module's neighbours, so it is not imported at the top)
CHOKE_SPECS = ["Opening (Cv)", "Outlet pressure", "Pressure drop"]

SCHEMA = {
    "subsea_choke": {
        "label": "Subsea choke module", "prefix": "CK", "category": CATEGORY,
        "ports": {"in": {"in": {"multi": False}}, "out": {"out": {"multi": False}}},
        "params": [
            _s("spec", "Specification", CHOKE_SPECS, "Opening (Cv)"),
            _f("Cv_max", "Choke Cv fully open", "US gpm/psi½", 400.0, {"spec": "Opening (Cv)"}, minv=1.0),
            _f("opening", "Opening", "%", 60.0, {"spec": "Opening (Cv)"}, minv=1.0, maxv=100.0),
            _f("rangeability", "Rangeability (equal-percentage trim)", "-", 50.0, {"spec": "Opening (Cv)"}, minv=2.0),
            _f("P_out", "Outlet pressure", "bar(a)", 60.0, {"spec": "Outlet pressure"}),
            _f("dP", "Pressure drop", "bar", 10.0, {"spec": "Pressure drop"}, minv=0.0),
        ],
    },
    "subsea_manifold": {
        "label": "Subsea production manifold", "prefix": "MF", "category": CATEGORY,
        "ports": {"in": {"in": {"multi": True}}, "out": {"out": {"multi": False}}},
        "params": [
            _f("dP", "Pressure drop, branch to header", "bar", 0.3, minv=0.0),
            _f("ID", "Header inner diameter", "mm", 254.0, minv=20.0),
            _f("C_ero", "Erosional constant C (API RP 14E)", "-", 100.0, minv=10.0, maxv=300.0,
               help="v_max = C / √ρ with v in ft/s and ρ in lb/ft³; 100 is the conservative value for continuous service"),
            _f("slots", "Slots available", "-", 4, minv=1, maxv=24),
        ],
    },
    "mpfm": {
        "label": "Multiphase flow meter", "prefix": "FT", "category": CATEGORY,
        "ports": {"in": {"in": {"multi": False}}, "out": {"out": {"multi": False}}},
        "params": [
            _f("dP", "Pressure drop (venturi)", "bar", 0.5, minv=0.0),
            _f("u_liq", "Liquid rate uncertainty (±)", "% rel.", 5.0, minv=0.1),
            _f("u_gas", "Gas rate uncertainty (±)", "% rel.", 8.0, minv=0.1),
            _f("u_wlr", "Water-liquid ratio uncertainty (±)", "% abs.", 3.0, minv=0.1),
            _f("gvf_max", "Maximum GVF in the specified window", "%", 95.0, minv=50.0, maxv=100.0),
        ],
    },
    "pig_station": {
        "label": "Pig launcher / receiver", "prefix": "PG", "category": CATEGORY,
        "ports": {"in": {"in": {"multi": False}}, "out": {"out": {"multi": False}}},
        "params": [
            _s("role", "Role", ["Launcher", "Receiver"], "Receiver"),
            _f("dP", "Pressure drop (open, not pigging)", "bar", 0.1, minv=0.0),
            _f("L", "Line length to be pigged", "km", 20.0, minv=0.1),
            _f("ID", "Line inner diameter", "mm", 254.0, minv=20.0),
            _f("holdup", "Liquid holdup in the line before the run", "% of volume", 20.0, minv=0.0, maxv=100.0,
               help="Take it from the flowline result; the pig sweeps this volume ahead of it"),
            _f("capacity", "Receiver slug capacity", "m³", 60.0, minv=0.0),
            _f("v_pig_max", "Maximum pig speed", "m/s", 3.0, minv=0.1),
            _f("interval", "Pigging interval", "days", 30.0, minv=0.1),
        ],
    },
    "subsea_hydrocyclone": {
        "label": "Subsea hydrocyclone (de-oiling)", "prefix": "HC", "category": CATEGORY,
        "ports": {"in": {"feed": {"multi": False}},
                  "out": {"water": {"multi": False}, "reject": {"multi": False}}},
        "params": [
            _f("oil_removal", "Oil removal (to the reject)", "% of oil", 95.0, minv=0.0, maxv=100.0),
            _f("reject_ratio", "Reject ratio (water phase going with the reject)", "% of water", 2.0, minv=0.0, maxv=30.0,
               help="Typical de-oiling cyclones run at 1-3 % reject"),
            _f("dP", "Pressure drop, feed to water outlet", "bar", 3.0, minv=0.0),
            _f("dP_rej", "Pressure drop, feed to reject", "bar", 1.0, minv=0.0),
            _f("max_oil", "Allowed oil in the clean water", "ppm (vol)", 30.0, minv=0.1),
        ],
    },
    "subsea_heater": {
        "label": "Subsea electric heater", "prefix": "H", "category": CATEGORY,
        "ports": {"in": {"in": {"multi": False}}, "out": {"out": {"multi": False}}},
        "params": [
            _s("spec", "Specification", ["Outlet temperature", "Heat duty"], "Outlet temperature"),
            _f("T_out", "Outlet temperature", "°C", 60.0, {"spec": "Outlet temperature"}),
            _f("duty", "Heat duty", "kW", 500.0, {"spec": "Heat duty"}, minv=0.0),
            _f("eff", "Electrical to heat efficiency", "%", 97.0, minv=10.0, maxv=100.0),
            _f("dP", "Pressure drop", "bar", 0.5, minv=0.0),
            _f("rated_kW", "Rated electrical power (0 = no limit)", "kW", 0.0, minv=0.0),
        ],
    },
}


def _noflow(s, extra=None):
    return {"out": [zero_stream("", None, s.z, s.T, s.P)]}


def _rho_q(s):
    q = sum(s.F * ph.beta * ph.Vs for ph in s.flash.phases) * 1000.0           # m³/h actual
    rho = s.F * s.MW / q if q > 0 else 1000.0
    return rho, q


def _zero(fp, s):
    return {"out": [zero_stream("", fp, s.z, s.T, s.P)]}


# ------------------------------------------------------------------------------------------------ choke
def calc_subsea_choke(unit, ins, fp):
    from . import surf as SF          # noqa: F401
    p = unit["params"]
    s = _one(ins, "in")
    if s.empty:
        return _zero(fp, s), {"Status": "No flow"}, []
    spec = p.get("spec", "Opening (Cv)")
    extra = {}
    if spec == "Outlet pressure":
        P2 = float(p["P_out"])
        if P2 > s.P + 1e-9:
            raise UnitError(f"{unit['name']}: outlet {P2:.2f} bar is above the inlet {s.P:.2f} bar")
    elif spec == "Pressure drop":
        P2 = s.P - float(p["dP"])
    else:
        cv = SF.choke_cv(p)
        dp, choked = SF.choke_dp(s, s.P, cv)
        P2 = s.P - dp
        extra = {"Choke Cv at this opening [US gpm/psi½]": cv, "Choke flow regime": "choked (critical)" if choked else "sub-critical"}
    _check_P(P2, unit["name"])
    fr = _ph(fp, s.z, P2, s.H, s.T, s.flash.Kset)
    out = make_stream("", fp, s.F, s.z, fr)
    res = {"Inlet P [bar(a)]": s.P, "Outlet P [bar(a)]": P2, "ΔP [bar]": s.P - P2, "Outlet T [°C]": fr.T - K0,
           "ΔT (Joule-Thomson) [°C]": fr.T - s.T, "Pressure ratio P2/P1 [-]": P2 / s.P}
    res.update(extra)
    v = s.flash.phase("V")
    if v is not None and v.Cv > 0 and s.P - P2 > 1e-9:
        rc = SF.critical_ratio(v.Cp / v.Cv)
        res["Critical pressure ratio [-]"] = rc
        res["Choke flow"] = "Critical (sonic)" if P2 / s.P <= rc else "Sub-critical"
    hm = _hyd_margin(out, fp)
    if hm is not None:
        res["Downstream hydrate margin [°C]"] = hm
        if hm < 0:
            res["Warning"] = "Choke outlet is inside the hydrate region: inhibit, insulate or heat"
    return {"out": [out]}, res, []


# ------------------------------------------------------------------------------------------------ manifold
def calc_subsea_manifold(unit, ins, fp):
    from . import surf as SF          # noqa: F401
    p = unit["params"]
    streams = ins.get("in") or []
    F, z, Hf, P, Tg = _mix(streams, fp)
    n_in = len(_live(streams))
    if F <= 0:
        zz = streams[0].z if streams else None
        return {"out": [zero_stream("", fp, zz, None, None)]}, {"Status": "No flow", "Branches connected": len(streams)}, []
    P = P - float(p.get("dP", 0.0))
    _check_P(P, unit["name"])
    fr = _ph(fp, z, P, Hf / F, Tg)
    out = make_stream("", fp, F, z, fr)
    rho, q = _rho_q(out)
    A = math.pi / 4.0 * (float(p["ID"]) / 1000.0) ** 2
    v = q / 3600.0 / A
    rho_lb = rho * 0.062428
    v_ero = float(p.get("C_ero", 100.0)) / math.sqrt(max(rho_lb, 1e-6)) * 0.3048
    res = {"Branches flowing": n_in, "Branches connected": len(streams), "Slots free": max(int(p.get("slots", 4)) - len(streams), 0),
           "Header P [bar(a)]": P, "Header T [°C]": fr.T - K0, "Header velocity [m/s]": v,
           "Erosional velocity [m/s]": v_ero, "Velocity / erosional [%]": 100.0 * v / v_ero,
           "Mixture density [kg/m³]": rho, "Mass flow [kg/h]": F * out.MW}
    warns = []
    if len(streams) > int(p.get("slots", 4)):
        warns.append(f"{len(streams)} branches on a {int(p.get('slots', 4))}-slot manifold")
    if v > v_ero:
        warns.append(f"header velocity {v:.1f} m/s is above the erosional limit {v_ero:.1f} m/s: use a larger header")
    hm = _hyd_margin(out, fp)
    if hm is not None:
        res["Header hydrate margin [°C]"] = hm
        if hm < 0:
            warns.append("header is inside the hydrate region")
    if warns:
        res["Warning"] = "; ".join(warns)
    return {"out": [out]}, res, []


# ------------------------------------------------------------------------------------------------ MPFM
def calc_mpfm(unit, ins, fp):
    from . import surf as SF          # noqa: F401
    p = unit["params"]
    s = _one(ins, "in")
    if s.empty:
        return _zero(fp, s), {"Status": "No flow"}, []
    P2 = s.P - float(p.get("dP", 0.0))
    _check_P(P2, unit["name"])
    fr = _ph(fp, s.z, P2, s.H, s.T, s.flash.Kset)
    out = make_stream("", fp, s.F, s.z, fr)
    gas, oil, wat = SF.standard_rates(s, fp)               # MSm³/d, Sm³/d, Sm³/d
    liq = oil + wat
    gvf = SF.gas_volume_fraction(s) * 100.0
    wlr = 100.0 * wat / liq if liq > 0 else 0.0
    ul, ug, uw = float(p["u_liq"]), float(p["u_gas"]), float(p["u_wlr"])
    res = {"Gas rate [MSm³/d]": gas, "Oil rate [Sm³/d]": oil, "Water rate [Sm³/d]": wat, "Liquid rate [Sm³/d]": liq,
           "GVF at line conditions [%]": gvf, "Water-liquid ratio [%]": wlr, "GOR [Sm³/Sm³]": gas * 1e6 / oil if oil > 0 else 0.0,
           "Line P [bar(a)]": s.P, "Line T [°C]": s.T - K0, "ΔP [bar]": s.P - P2,
           "Gas rate ± [MSm³/d]": gas * ug / 100.0, "Liquid rate ± [Sm³/d]": liq * ul / 100.0,
           "Water-liquid ratio ± [% abs.]": uw}
    gmax = float(p.get("gvf_max", 95.0))
    if gvf > gmax:
        res["Warning"] = (f"GVF {gvf:.1f} % is above the {gmax:.0f} % of the specified window: the meter reads wet gas and the "
                          "liquid uncertainty grows several-fold - consider a wet-gas meter")
    return {"out": [out]}, res, []


# ------------------------------------------------------------------------------------------------ pig station
def calc_pig_station(unit, ins, fp):
    from . import surf as SF          # noqa: F401
    p = unit["params"]
    s = _one(ins, "in")
    if s.empty:
        return _zero(fp, s), {"Status": "No flow"}, []
    P2 = s.P - float(p.get("dP", 0.0))
    _check_P(P2, unit["name"])
    fr = _ph(fp, s.z, P2, s.H, s.T, s.flash.Kset)
    out = make_stream("", fp, s.F, s.z, fr)
    rho, q = _rho_q(s)
    ID, L = float(p["ID"]) / 1000.0, float(p["L"]) * 1000.0
    A = math.pi / 4.0 * ID ** 2
    v_flow = q / 3600.0 / A
    v_pig = min(max(v_flow, 0.05), float(p["v_pig_max"]))
    t_h = L / v_pig / 3600.0
    vol_line = A * L
    sweep = vol_line * float(p["holdup"]) / 100.0
    cap = float(p["capacity"])
    res = {"Line velocity [m/s]": v_flow, "Pig speed [m/s]": v_pig, "Transit time [h]": t_h, "Line volume [m³]": vol_line,
           "Liquid swept ahead of the pig [m³]": sweep, "Receiver slug capacity [m³]": cap,
           "Pigging interval [days]": float(p["interval"]), "Role": p.get("role", "Receiver")}
    if cap > 0:
        res["Slug / capacity [%]"] = 100.0 * sweep / cap
        if sweep > cap:
            res["Warning"] = (f"the pig pushes {sweep:.0f} m³ of liquid ahead of it but the receiver holds {cap:.0f} m³: "
                              "reduce the gas rate during the run, pig more often, or increase the receiver volume")
    if v_flow > float(p["v_pig_max"]) * 1.5:
        res["Note"] = "line velocity is far above the pig speed limit: bypass flow through the pig is required"
    return {"out": [out]}, res, []


# ------------------------------------------------------------------------------------------------ hydrocyclone
def calc_subsea_hydrocyclone(unit, ins, fp):
    from . import surf as SF          # noqa: F401
    p = unit["params"]
    s = _one(ins, "feed")
    if s.empty:
        return {"water": [zero_stream("", fp, s.z, s.T, s.P)], "reject": [zero_stream("", fp, s.z, s.T, s.P)]}, {"Status": "No flow"}, []
    Pw, Pr = s.P - float(p["dP"]), s.P - float(p["dP_rej"])
    _check_P(Pw, unit["name"])
    _check_P(Pr, unit["name"])
    n = {k: np.zeros(fp.n) for k in "VLW"}
    for ph in s.flash.phases:
        n[ph.kind] = n[ph.kind] + s.F * ph.beta * ph.x
    eo = float(p["oil_removal"]) / 100.0
    rr = float(p["reject_ratio"]) / 100.0
    rej = n["L"] * eo + n["W"] * rr + n["V"]            # free gas leaves with the reject
    wat = n["L"] * (1 - eo) + n["W"] * (1 - rr)
    outs, vols = {}, {}
    for port, flow, P_ in (("water", wat, Pw), ("reject", rej, Pr)):
        Fo = float(flow.sum())
        if Fo <= 1e-12:
            outs[port] = [zero_stream("", fp, s.z, s.T, P_)]
            continue
        zo = flow / Fo
        outs[port] = [make_stream("", fp, Fo, zo, fp.pt_flash(zo, s.T, P_))]
    # oil-in-water by volume in the clean water outlet
    ow = outs["water"][0]
    ppm = 0.0
    if not ow.empty:
        vo = sum(ow.F * ph.beta * ph.Vs for ph in ow.flash.phases if ph.kind == "L")
        vw = sum(ow.F * ph.beta * ph.Vs for ph in ow.flash.phases if ph.kind == "W")
        ppm = 1e6 * vo / (vo + vw) if (vo + vw) > 0 else 0.0
    res = {"Feed P [bar(a)]": s.P, "Feed T [°C]": s.T - K0, "Clean water outlet P [bar(a)]": Pw,
           "Reject outlet P [bar(a)]": Pr, "Clean water [kg/h]": ow.F * ow.MW, "Reject [kg/h]": outs["reject"][0].F * outs["reject"][0].MW,
           "Oil in clean water [ppm vol]": ppm, "Allowed [ppm vol]": float(p["max_oil"]),
           "Oil removal [% of oil]": float(p["oil_removal"])}
    if ppm > float(p["max_oil"]):
        res["Warning"] = f"oil in the clean water {ppm:.0f} ppm is above the allowed {float(p['max_oil']):.0f} ppm"
    return outs, res, []


# ------------------------------------------------------------------------------------------------ heater
def calc_subsea_heater(unit, ins, fp):
    from . import surf as SF          # noqa: F401
    p = unit["params"]
    s = _one(ins, "in")
    if s.empty:
        return _zero(fp, s), {"Status": "No flow"}, []
    P2 = s.P - float(p.get("dP", 0.0))
    _check_P(P2, unit["name"])
    if p.get("spec", "Outlet temperature") == "Outlet temperature":
        T2 = float(p["T_out"]) + K0
        fr = fp.pt_flash(s.z, T2, P2, s.flash.Kset)
        duty = s.F * (fr.H - s.H) / 3600.0
        if duty < -1e-9:
            raise UnitError(f"{unit['name']}: outlet {T2 - K0:.1f} °C is below the inlet {s.T - K0:.1f} °C (a heater cannot cool)")
    else:
        duty = float(p["duty"])
        fr = _ph(fp, s.z, P2, s.H + duty * 3600.0 / s.F, s.T, s.flash.Kset)
    out = make_stream("", fp, s.F, s.z, fr)
    eff = float(p["eff"]) / 100.0
    power = max(duty, 0.0) / eff
    res = {"Outlet T [°C]": fr.T - K0, "Outlet P [bar(a)]": P2, "Heat duty [kW]": duty, "Electrical power [kW]": power,
           "Efficiency [%]": float(p["eff"]), "Vapour fraction": fr.vf}
    rated = float(p.get("rated_kW", 0.0) or 0.0)
    if rated > 0:
        res["Load [% of rated]"] = 100.0 * power / rated
        if power > rated:
            res["Warning"] = f"the heater needs {power:,.0f} kW but is rated {rated:,.0f} kW"
    hm = _hyd_margin(out, fp)
    if hm is not None:
        res["Outlet hydrate margin [°C]"] = hm
    # process heat belongs to the unit; the electrical supply is a separate work stream (not a process enthalpy)
    en = [_energy(unit, duty)]
    if power > 0:
        en.append(EnergyStream(f"{unit['name']} electric supply", power, f"{unit['name']} electric supply", "work"))
    return {"out": [out]}, res, en


CALCS = {"subsea_choke": calc_subsea_choke, "subsea_manifold": calc_subsea_manifold, "mpfm": calc_mpfm,
         "pig_station": calc_pig_station, "subsea_hydrocyclone": calc_subsea_hydrocyclone, "subsea_heater": calc_subsea_heater}
EXT_TYPES = tuple(CALCS)


def register():
    from .unitops import CATALOGUE, CALC
    CATALOGUE.update(SCHEMA)
    CALC.update(CALCS)


register()
