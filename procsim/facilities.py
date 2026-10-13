"""Facilities added in v7.6: offshore platform (drawing area), gas turbine (power generation / mechanical drive) and an ideal
phase splitter.

* ``platform``       - a labelled area on the flowsheet that groups the topside equipment of one installation (fixed jacket,
                       FPSO, semi-submersible, subsea ...).  No ports, never solved; moving it moves the equipment inside.
* ``gas_turbine``    - burns the fuel gas stream connected to ``fuel``; shaft power = fuel heat (LHV) x efficiency, limited by the
                       rating derated for ambient temperature; reports electric power, exhaust heat and CO2.  Power produced is
                       an energy stream (work out), so it shows in the energy balance; the plant shaft demand is compared with the
                       total generation in the results.
* ``phase_splitter`` - ideal split of the feed into vapour, oil and water at the vessel conditions with optional carry-over; no
                       vessel sizing (use the separators for that)."""
from __future__ import annotations

import numpy as np

from .streams import make_stream, zero_stream, phase_stream
from .unitops import UnitError, K0, _f, _s, _ph, _mix, _live, _check_P, _energy, _outs_zero

PLATFORM_KINDS = ["Fixed jacket platform", "Gravity-based platform", "Jack-up", "Semi-submersible", "FPSO", "Spar / TLP",
                  "Subsea / seabed area", "Onshore plant"]

SCHEMA = {
    "platform": {
        "label": "Offshore platform / installation (area)", "prefix": "PLT", "category": "Facilities",
        "ports": {"in": {}, "out": {}},
        "params": [
            _s("kind", "Installation type", PLATFORM_KINDS, PLATFORM_KINDS[0]),
            _f("w", "Frame width", "", 520.0, minv=120.0, maxv=3000.0,
               help="Drawing units; the frame can be resized here and scaled with the element zoom"),
            _f("h", "Frame height", "", 320.0, minv=100.0, maxv=3000.0),
            {"key": "note", "label": "Note (shown under the name)", "kind": "text", "default": "", "show_if": None,
             "help": None},
        ],
    },
    "gas_turbine": {
        "label": "Gas turbine (generator / mechanical drive)", "prefix": "GT", "category": "Rotating",
        "ports": {"in": {"fuel": {"multi": False}}, "out": {}},
        "params": [
            _s("use", "Service", ["Power generation", "Mechanical drive"], "Power generation"),
            _f("eff", "Thermal efficiency at the fuel rate (shaft power / fuel LHV)", "%", 35.0, minv=5.0, maxv=60.0),
            _f("gen_eff", "Generator efficiency", "%", 97.0, {"use": "Power generation"}, minv=50.0, maxv=100.0),
            _f("rated_kW", "Rated shaft power at ISO conditions (0 = no limit)", "kW", 0.0, minv=0.0),
            _f("amb_T", "Ambient air temperature", "°C", 15.0, help="Available power falls about 0.7 % per K above 15 °C"),
            _f("T_exh", "Exhaust gas temperature", "°C", 500.0, minv=200.0, maxv=700.0),
            _f("casing", "Casing / other losses", "% of fuel heat", 2.0, minv=0.0, maxv=10.0),
        ],
    },
    "deliquifier": {
        "label": "Deliquifier (gas-liquid coalescer / filter separator)", "prefix": "DQ", "category": "Separation",
        "ports": {"in": {"feed": {"multi": True}}, "out": {"gas": {"multi": False}, "liquid": {"multi": False}}},
        "params": [
            _s("spec", "Specification", ["Removal efficiency", "Liquid carry-over in the gas"], "Removal efficiency",
               help="Either the share of the entrained liquid that is removed, or the liquid left in the outlet gas"),
            _f("eff", "Liquid removal efficiency", "% of liquid", 99.5, {"spec": "Removal efficiency"}, minv=0.0, maxv=100.0),
            _f("carry", "Liquid left in the outlet gas", "mg/Sm³", 5.0, {"spec": "Liquid carry-over in the gas"}, minv=0.0),
            _f("dP", "Pressure drop (clean elements)", "bar", 0.3, minv=0.0),
            _f("K", "Souders-Brown K for the vessel / elements", "m/s", 0.15, minv=0.01, maxv=0.6,
               help="Vane / mesh ~0.1-0.2, coalescing cartridges ~0.05-0.1, cyclones ~0.3-0.5"),
            _f("gas_in_liq", "Gas carried under with the liquid", "% of gas", 0.0, minv=0.0, maxv=20.0),
        ],
    },
    "phase_splitter": {
        "label": "Phase splitter (ideal)", "prefix": "PS", "category": "Separation",
        "ports": {"in": {"feed": {"multi": True}},
                  "out": {"vapour": {"multi": False}, "oil": {"multi": False}, "water": {"multi": False}}},
        "params": [
            _f("dP", "Pressure drop", "bar", 0.0), _f("duty", "Heat input", "kW", 0.0),
            _f("liq_in_gas", "Liquid carried over with the gas", "% of liquid", 0.0, minv=0.0, maxv=100.0),
            _f("gas_in_liq", "Gas carried under with the oil", "% of gas", 0.0, minv=0.0, maxv=100.0),
            _f("water_in_oil", "Water phase leaving with the oil", "% of water", 0.0, minv=0.0, maxv=100.0),
            _f("oil_in_water", "Oil phase leaving with the water", "% of oil", 0.0, minv=0.0, maxv=100.0),
        ],
    },
}


# ---------------------------------------------------------------------------------------------- gas turbine
def fuel_properties(fp, s):
    """LHV [J/mol] and carbon content [kmol C per kmol] of a stream's composition."""
    from .process_units import lhv_J_mol, FORMULA          # lazy: process_units imports unitops, which imports this module
    lhv = 0.0
    carbon = 0.0
    sulphur = 0.0
    for i, k in enumerate(fp.keys):
        lhv += s.z[i] * lhv_J_mol(k, float(fp.MW[i]))
        c, _h, sp = FORMULA.get(k, (0, 0, 0))
        if k not in FORMULA and k not in ("N2", "O2", "H2O"):
            c = float(fp.MW[i]) * 0.85 / 12.011                    # petroleum cut: ~85 wt% carbon
        if k == "CO2":
            c = 1                                                    # CO2 in the fuel leaves with the exhaust
        carbon += s.z[i] * c
        sulphur += s.z[i] * sp
    return lhv, carbon, sulphur


def calc_gas_turbine(unit, ins, fp):
    p = unit["params"]
    s = ins["fuel"][0] if ins.get("fuel") else None
    if s is None:
        raise UnitError("Fuel inlet is not connected")
    if s.empty:
        return {}, {"Status": "No fuel flow"}, []
    lhv, carbon, sulphur = fuel_properties(fp, s)          # J/mol, kmol C / kmol
    if lhv <= 0:
        raise UnitError(f"{unit['name']}: the fuel gas has no heating value (inerts only)")
    Q_kW = s.F * lhv / 3600.0 * 1.0                         # kmol/h * J/mol = kJ/h x 1000/1000 -> /3600 = kW
    eff = float(p["eff"]) / 100.0
    avail = None
    rated = float(p.get("rated_kW", 0.0) or 0.0)
    if rated > 0:
        avail = rated * (1.0 - 0.007 * (float(p.get("amb_T", 15.0)) - 15.0))
    shaft = Q_kW * eff
    warn = None
    if avail is not None and shaft > avail:
        warn = (f"fuel supports {shaft:,.0f} kW but the turbine can give {avail:,.0f} kW at "
                f"{float(p.get('amb_T', 15.0)):.0f} °C: the surplus fuel heat is not converted")
        shaft = avail
    gen = float(p.get("gen_eff", 97.0)) / 100.0 if p.get("use", "Power generation") == "Power generation" else 1.0
    elec = shaft * gen
    used_fuel_kW = Q_kW if (avail is None or Q_kW * eff <= avail) else avail / eff
    losses = used_fuel_kW * float(p.get("casing", 2.0)) / 100.0
    exh = max(used_fuel_kW - shaft - losses, 0.0)
    from .process_units import STD_VOL
    mass = s.F * s.MW
    co2_t_h = s.F * carbon * 44.01 / 1000.0
    out_kW = elec if p.get("use", "Power generation") == "Power generation" else shaft
    res = {"Fuel gas [kg/h]": mass, "Fuel gas [Sm³/h]": s.F * STD_VOL, "Fuel LHV [MJ/kg]": lhv / s.MW / 1000.0,
           "Fuel LHV [MJ/Sm³]": lhv / STD_VOL / 1000.0, "Fuel heat input [kW]": Q_kW,
           "Shaft power [kW]": shaft, "Net efficiency (LHV) [%]": 100.0 * out_kW / Q_kW,
           "Exhaust heat at " + f"{float(p.get('T_exh', 500.0)):.0f} °C [kW]": exh,
           "CO₂ emitted [t/h]": co2_t_h, "CO₂ intensity [kg/MWh]": 1000.0 * co2_t_h / max(out_kW / 1000.0, 1e-9)}
    if p.get("use", "Power generation") == "Power generation":
        res["Electric power [kW]"] = elec
    if sulphur > 0:
        res["SO₂ emitted [kg/h]"] = s.F * sulphur * 64.07
    if avail is not None:
        res["Available power at ambient [kW]"] = avail
        res["Load [% of available]"] = 100.0 * shaft / max(avail, 1e-9)
    if warn:
        res["Warning"] = warn
    en = [_energy(unit, -out_kW, "work")]
    return {}, res, en


def post_gas_turbines(model, sol):
    """Plant power balance after the solve: generation of all turbines against the shaft/electric demand of the machines."""
    gens = [(uid, u) for uid, u in model["units"].items()
            if u["type"] == "gas_turbine" and sol.status.get(uid) in ("ok", "warning")]
    if not gens:
        return
    demand = sum(e.duty_kW for e in sol.energy if e.kind == "work" and e.duty_kW > 0)
    produced = sum(-e.duty_kW for e in sol.energy if e.kind == "work" and e.duty_kW < 0)
    for uid, _u in gens:
        r = sol.results.setdefault(uid, {})
        r["Plant work demand [kW]"] = demand
        r["Total work produced in the flowsheet [kW]"] = produced
        r["Production − demand [kW]"] = produced - demand
        if demand > produced * 1.0 + 1e-6 and sum(1 for e in sol.energy if e.kind == "work" and e.duty_kW < 0) >= 1:
            r.setdefault("Note", f"the flowsheet's machines need {demand:,.0f} kW but the turbines/expanders give "
                                 f"{produced:,.0f} kW - the rest comes from the grid or other generation")


# ---------------------------------------------------------------------------------------------- phase splitter
def calc_phase_splitter(unit, ins, fp):
    p = unit["params"]
    streams = ins.get("feed") or []
    F, z, Hf, P, Tg = _mix(streams, fp)
    ports = ["vapour", "oil", "water"]
    if F <= 0:
        zz = streams[0].z if streams else None
        return _outs_zero(unit, zz, fp, ports), {"Status": "No flow"}, []
    P = P - float(p.get("dP", 0.0))
    _check_P(P, unit["name"])
    duty = float(p.get("duty", 0.0))
    H = Hf / F + duty * 3600.0 / F
    fr = _ph(fp, z, P, H, Tg)
    mix = make_stream("", fp, F, z, fr)
    cg, cu = float(p["liq_in_gas"]) / 100.0, float(p["gas_in_liq"]) / 100.0
    cw, co = float(p["water_in_oil"]) / 100.0, float(p["oil_in_water"]) / 100.0
    if max(cg, cu, cw, co) <= 0.0:
        outs = {"vapour": [phase_stream(mix, fp, ("V",), "")], "oil": [phase_stream(mix, fp, ("L",), "")],
                "water": [phase_stream(mix, fp, ("W",), "")]}
    else:
        n = {k: np.zeros(fp.n) for k in "VLW"}
        for ph in fr.phases:
            n[ph.kind] = n[ph.kind] + F * ph.beta * ph.x
        v = n["V"] * (1 - cu) + cg * (n["L"] + n["W"])
        o = n["L"] * (1 - cg - co) + cu * n["V"] + cw * n["W"]
        w = n["W"] * (1 - cw - cg) + co * n["L"]
        # the carry-over fractions come off the same phase: keep every phase flow non-negative
        v, o, w = (np.maximum(a, 0.0) for a in (v, o, w))
        outs = {}
        for port, flow in (("vapour", v), ("oil", o), ("water", w)):
            Fo = float(flow.sum())
            if Fo <= 1e-12:
                outs[port] = [zero_stream("", fp, z, fr.T, P)]
                continue
            zo = flow / Fo
            outs[port] = [make_stream("", fp, Fo, zo, fp.pt_flash(zo, fr.T, P))]
    res = {"Vessel T [°C]": fr.T - K0, "Vessel P [bar(a)]": P, "Vapour fraction": fr.vf, "Phases": fr.phase_label}
    for k, v_ in outs.items():
        res[f"{k.capitalize()} flow [kg/h]"] = v_[0].F * v_[0].MW
    en = [_energy(unit, duty)] if abs(duty) > 0 else []
    return outs, res, en


# ---------------------------------------------------------------------------------------------- deliquifier
def calc_deliquifier(unit, ins, fp):
    """Removes the free liquid (hydrocarbon and water) from a gas stream.  The liquid leaves on ``liquid``; a part stays in the gas
    as carry-over (efficiency, or a mg/Sm3 specification).  Reports the Souders-Brown vessel diameter, the liquid load and a
    carry-over warning when the specification cannot be met by the efficiency given."""
    from .process_units import STD_VOL
    p = unit["params"]
    streams = ins.get("feed") or []
    F, z, Hf, P, Tg = _mix(streams, fp)
    if F <= 0:
        zz = streams[0].z if streams else None
        return _outs_zero(unit, zz, fp, ["gas", "liquid"]), {"Status": "No flow"}, []
    P = P - float(p.get("dP", 0.0))
    _check_P(P, unit["name"])
    fr = _ph(fp, z, P, Hf / F, Tg)
    n = {k: np.zeros(fp.n) for k in "VLW"}
    for ph in fr.phases:
        n[ph.kind] = n[ph.kind] + F * ph.beta * ph.x
    liq = n["L"] + n["W"]
    liq_kg = float((liq * fp.MW).sum())                       # kg/h of free liquid (kmol/h x kg/kmol)
    gas_sm3 = float(n["V"].sum()) * STD_VOL
    if gas_sm3 <= 0:
        raise UnitError(f"{unit['name']}: there is no gas in the feed - a deliquifier needs a gas stream")
    in_mg = liq_kg * 1e6 / gas_sm3
    if p.get("spec") == "Liquid carry-over in the gas":
        eff = 1.0 - min(float(p["carry"]) / in_mg, 1.0) if in_mg > 0 else 1.0
    else:
        eff = float(p["eff"]) / 100.0
    cu = float(p.get("gas_in_liq", 0.0)) / 100.0
    gas_n = n["V"] * (1 - cu) + liq * (1 - eff)
    liq_n = liq * eff + n["V"] * cu
    outs = {}
    for port, flow in (("gas", gas_n), ("liquid", liq_n)):
        Fo = float(flow.sum())
        if Fo <= 1e-12:
            outs[port] = [zero_stream("", fp, z, fr.T, P)]
        else:
            zo = flow / Fo
            outs[port] = [make_stream("", fp, Fo, zo, fp.pt_flash(zo, fr.T, P))]
    out_mg = liq_kg * (1 - eff) * 1e6 / gas_sm3
    v = fr.phase("V")
    rho_g = float(v.rho) if v is not None else 1.0
    lq = fr.phase("L") or fr.phase("W")
    rho_l = float(lq.rho) if lq is not None else 700.0
    q_act = float(n["V"].sum()) * 1000.0 * (v.Vs if v is not None else 0.0) / 3600.0      # m3/s actual gas
    vmax = float(p["K"]) * ((max(rho_l - rho_g, 1.0)) / max(rho_g, 1e-6)) ** 0.5
    D = (4.0 * q_act / (np.pi * vmax)) ** 0.5 * 1000.0 if q_act > 0 else 0.0
    res = {"Vessel P [bar(a)]": P, "Vessel T [°C]": fr.T - K0, "Liquid in the feed [kg/h]": liq_kg,
           "Liquid in the feed [mg/Sm³ gas]": in_mg, "Removal efficiency [%]": 100.0 * eff,
           "Liquid left in the gas [mg/Sm³]": out_mg, "Liquid removed [kg/h]": liq_kg * eff,
           "Gas flow [MSm³/d]": gas_sm3 * 24.0 / 1e6, "Allowable gas velocity [m/s]": vmax,
           "Required vessel / element diameter [mm]": D, "Gas density [kg/m³]": rho_g, "Liquid density [kg/m³]": rho_l}
    if p.get("spec") == "Removal efficiency" and out_mg > 50.0:
        res["Warning"] = f"{out_mg:.0f} mg/Sm³ of liquid stays in the gas: raise the removal efficiency or add a stage"
    return outs, res, []


def register():
    from .unitops import CATALOGUE, CALC
    CATALOGUE.update(SCHEMA)
    CALC.update({"gas_turbine": calc_gas_turbine, "phase_splitter": calc_phase_splitter, "deliquifier": calc_deliquifier})


register()
