"""Process units added in v6.5 (screening level).

* ``amine_contactor``  - amine sweetening: the acid gas removed to meet CO₂ / H₂S specifications, amine circulation from
  the loading window, reboiler duty and pump power (the amine loop is closed, as for the TEG unit);
* ``relief_valve``     - PSV sizing to API 520 (vapour critical / sub-critical, liquid, two-phase by summation) with the
  API 521 fire-case load and the API 526 orifice letter;
* ``flare``            - flare tip: heat release, flame length (Hajek & Ludwig), radiation and distance to a limit,
  tip diameter for a Mach number, CO₂ produced;
* ``comp_splitter``    - component splitter with specified split fractions;
* ``conv_reactor``     - conversion reactor, several reactions in sequence, enthalpies including heat of formation;
* ``eq_reactor``       - single-reaction equilibrium reactor (ideal-gas K(T) from formation data);
* ``valve_sizing``     - control-valve Cv / Kv and opening (IEC 60534 style) used by the valve unit;
* ``driver_results``   - driver rating / fuel / electrical input for compressors.
"""
from __future__ import annotations

import math
import re

import numpy as np

from .streams import make_stream, zero_stream, EnergyStream
from .thermo import R
from .unitops import UnitError, K0, _f, _s, _ph, _one, _energy, _check_P

# ------------------------------------------------------------------------------------------------ data
# ideal-gas standard formation data at 298.15 K [J/mol] (NIST / Perry): ΔHf and ΔGf
DHF = {"N2": 0.0, "O2": 0.0, "H2": 0.0, "CO2": -393.51e3, "H2O": -241.83e3, "H2S": -20.63e3, "C1": -74.87e3,
       "C2": -84.0e3, "C3": -103.85e3, "iC4": -134.2e3, "nC4": -125.6e3, "iC5": -153.7e3, "nC5": -146.9e3,
       "nC6": -167.2e3, "nC7": -187.8e3, "nC8": -208.6e3, "nC9": -228.7e3, "nC10": -249.5e3, "MeOH": -201.0e3,
       "MEG": -392.2e3}
DGF = {"N2": 0.0, "O2": 0.0, "H2": 0.0, "CO2": -394.36e3, "H2O": -228.57e3, "H2S": -33.4e3, "C1": -50.5e3,
       "C2": -32.0e3, "C3": -23.4e3, "nC4": -17.0e3, "MeOH": -162.3e3}
# elements per molecule (C, H, S) for heats of combustion
FORMULA = {"N2": (0, 0, 0), "O2": (0, 0, 0), "H2": (0, 2, 0), "CO2": (0, 0, 0), "H2O": (0, 0, 0), "H2S": (0, 2, 1),
           "C1": (1, 4, 0), "C2": (2, 6, 0), "C3": (3, 8, 0), "iC4": (4, 10, 0), "nC4": (4, 10, 0), "iC5": (5, 12, 0),
           "nC5": (5, 12, 0), "nC6": (6, 14, 0), "nC7": (7, 16, 0), "nC8": (8, 18, 0), "nC9": (9, 20, 0),
           "nC10": (10, 22, 0), "MeOH": (1, 4, 0), "MEG": (2, 6, 0)}
SO2_HF = -296.84e3
STD_VOL = 23.645              # m³/kmol at 15 °C, 1.01325 bar (the app's standard conditions)


def lhv_J_mol(key, MW=None):
    """Lower heating value [J/mol] of a library component (hypothetical cuts: 44 MJ/kg)."""
    if key in ("N2", "O2", "CO2", "H2O"):
        return 0.0
    if key not in FORMULA:
        return 44.0e6 * (MW or 100.0) / 1000.0
    c, h, s = FORMULA[key]
    prod = c * DHF["CO2"] + (h / 2.0) * DHF["H2O"] + s * SO2_HF
    return DHF.get(key, 0.0) - prod


def formation_flow_kW(fp, s):
    """Σ n_i ΔHf_i of a stream [kW] (zero for components without data): add it to the enthalpy flow when a reaction
    changes the composition."""
    if s is None or s.empty:
        return 0.0
    v = np.array([DHF.get(k, 0.0) for k in fp.keys])
    return float(s.F * (s.z @ v) / 3600.0)


def _t(key, label, default, help=None, show_if=None):
    return {"key": key, "label": label, "kind": "text", "default": default, "show_if": show_if, "help": help}


def _live(streams):
    return [s for s in streams if s is not None and not s.empty]


def _feed_mix(unit, ins, fp, port="feed"):
    live = _live(ins.get(port, []))
    if not live:
        z = ins[port][0].z if ins.get(port) else None
        return None, z
    F = sum(s.F for s in live)
    z = sum(s.F * s.z for s in live) / F
    H = sum(s.F * s.H for s in live) / F
    P = min(s.P for s in live)
    return (F, z, H, P, live[0].T), z


# =================================================================================== amine sweetening
AMINES = {   # name: (wt fraction, MW, density kg/m³, rich-loading cap mol/mol, heat of absorption CO2 kJ/mol,
             #        reboiler kJ per mol acid gas, cp kJ/kg/K)
    "MEA (15 wt%)": (0.15, 61.08, 1010.0, 0.40, 85.0, 150.0, 3.9),
    "DEA (25 wt%)": (0.25, 105.14, 1040.0, 0.40, 70.0, 125.0, 3.7),
    "MDEA (40 wt%)": (0.40, 119.16, 1040.0, 0.50, 55.0, 85.0, 3.7),
    "Activated MDEA (45 wt%)": (0.45, 119.16, 1050.0, 0.60, 60.0, 75.0, 3.6),
}
H2S_ABS_KJ = 60.0

SCHEMA_AMINE = {
    "amine_contactor": {
        "label": "Amine sweetening (contactor + regenerator)", "prefix": "A", "category": "Separation",
        "ports": {"in": {"feed": {"multi": True}}, "out": {"sweet": {"multi": False}, "acid": {"multi": False}}},
        "params": [
            _s("amine", "Amine solution", list(AMINES), "MDEA (40 wt%)"),
            _f("co2_spec", "CO₂ in the sweet gas", "mol%", 2.0, minv=0.0, help="Pipeline ≈ 2–3 %, LNG ≈ 0.005 %"),
            _f("h2s_spec", "H₂S in the sweet gas", "ppmv", 4.0, minv=0.0, help="Sales gas ≈ 4 ppmv (≈ 5.7 mg/Sm³)"),
            _f("lean", "Lean amine loading", "mol/mol", 0.01, minv=0.0, maxv=0.5,
               help="Moles of acid gas per mole of amine leaving the regenerator"),
            _f("rich_pct", "Design rich loading (% of the amine's cap)", "%", 85.0, minv=10.0, maxv=100.0,
               help="Caps: MEA/DEA 0.40, MDEA 0.50, activated MDEA 0.60 mol/mol (corrosion limits)"),
            _f("dP", "Contactor pressure drop", "bar", 0.5, minv=0.0),
            _f("P_acid", "Acid gas pressure (regenerator overhead)", "bar(a)", 1.8, minv=1.0),
        ],
    },
}


def calc_amine(unit, ins, fp):
    p = unit["params"]
    mixed, zin = _feed_mix(unit, ins, fp)
    if mixed is None:
        z0 = ins["feed"][0].z if ins.get("feed") else None
        return {"sweet": [zero_stream("", fp, z0)], "acid": [zero_stream("", fp, z0)]}, {"Status": "No flow"}, []
    F, z, H, P, T0 = mixed
    P = P - float(p.get("dP", 0.0))
    if P <= 1.0:
        raise UnitError(f"{unit['name']}: contactor pressure {P:.2f} bar is too low")
    am = AMINES[p.get("amine", "MDEA (40 wt%)")]
    wt, MWa, rho, cap, q_co2, q_reb, cp = am
    fr = _ph(fp, z, P, H, T0)
    T = fr.T
    v = fr.phase("V")
    if v is None:
        raise UnitError(f"{unit['name']}: no gas to sweeten")
    Fv, yv = F * v.beta, v.x.copy()
    free = F * z - Fv * yv                                   # liquids knocked out in the inlet scrubber
    ic = fp.keys.index("CO2") if "CO2" in fp.keys else -1
    ih = fp.keys.index("H2S") if "H2S" in fp.keys else -1
    nc = Fv * yv[ic] if ic >= 0 else 0.0
    nh = Fv * yv[ih] if ih >= 0 else 0.0
    yc, yh = float(p["co2_spec"]) / 100.0, float(p["h2s_spec"]) * 1e-6
    # r_c(1-yc) - yc r_h = nc - yc Fv ;  -yh r_c + r_h(1-yh) = nh - yh Fv   (removals, kmol/h)
    A = np.array([[1.0 - yc, -yc], [-yh, 1.0 - yh]])
    b = np.array([nc - yc * Fv, nh - yh * Fv])
    rc, rh = np.linalg.solve(A, b)
    rc = min(max(rc, 0.0), nc) if ic >= 0 else 0.0
    rh = min(max(rh, 0.0), nh) if ih >= 0 else 0.0
    rem = np.zeros(fp.n)
    if ic >= 0:
        rem[ic] = rc
    if ih >= 0:
        rem[ih] = rh
    n_acid = rc + rh
    sweet_n = np.maximum(Fv * yv - rem, 0.0)
    acid_n = free + rem
    Fs, Fa = float(sweet_n.sum()), float(acid_n.sum())
    zs = sweet_n / Fs
    frs = fp.pt_flash(zs, T, P)
    sweet = make_stream("", fp, Fs, zs, frs)
    outs = {"sweet": [sweet]}
    if Fa > 1e-12:
        za = acid_n / Fa
        Ha = (F * H - Fs * frs.H) / Fa
        fra = _ph(fp, za, float(p["P_acid"]), Ha, T)
        outs["acid"] = [make_stream("", fp, Fa, za, fra)]
    else:
        outs["acid"] = [zero_stream("", fp, z, T, float(p["P_acid"]))]
    # --- loop
    c_am = wt * rho / MWa                                       # kmol amine / m³ solution
    rich = cap * float(p["rich_pct"]) / 100.0
    lean = float(p["lean"])
    warn = []
    if rich <= lean + 1e-6:
        raise UnitError(f"{unit['name']}: design rich loading {rich:.3f} must exceed the lean loading {lean:.3f}")
    L = n_acid / ((rich - lean) * c_am) if n_acid > 0 else 0.0   # m³/h
    Q_abs = (rc * q_co2 + rh * H2S_ABS_KJ) * 1000.0 / 3600.0      # kW
    dT_sol = Q_abs / (L * rho * cp / 3600.0) if L > 0 else 0.0
    Q_reb = n_acid * q_reb * 1000.0 / 3600.0
    P_pump = L / 3600.0 * max(P - float(p["P_acid"]), 0.0) * 1e5 / 0.70 / 1000.0
    sw_co2 = (sweet_n[ic] / Fs) if (ic >= 0 and Fs > 0) else 0.0
    if p.get("amine", "").startswith("MDEA") and ic >= 0 and sw_co2 < 50e-6:
        warn.append("Conventional MDEA rarely reaches < 50 ppmv CO₂ - use activated MDEA or a hybrid solvent")
    if p.get("amine", "").startswith(("MEA", "DEA")) and ic >= 0 and nc > 0 and n_acid / max(Fv, 1e-9) > 0.2:
        warn.append("High acid-gas content for MEA/DEA - a physical or hybrid solvent is usually used above ~20 %")
    if dT_sol > 25.0:
        warn.append(f"Solution temperature rise {dT_sol:.0f} K - increase circulation or add intercooling")
    res = {"Contactor P [bar(a)]": P, "Contactor T [°C]": T - K0, "Acid gas removed [kmol/h]": n_acid,
           "CO₂ removed [kmol/h]": rc, "H₂S removed [kmol/h]": rh,
           "CO₂ in sweet gas [mol%]": 100.0 * (sweet_n[ic] / Fs if ic >= 0 else 0.0),
           "H₂S in sweet gas [ppmv]": 1e6 * (sweet_n[ih] / Fs if ih >= 0 else 0.0),
           "Amine circulation [m³/h]": L, "Rich loading [mol/mol]": rich, "Lean loading [mol/mol]": lean,
           "Heat of absorption [kW]": Q_abs, "Solution temperature rise [K]": dT_sol,
           "Reboiler duty [kW]": Q_reb, "Rich amine pump [kW]": P_pump,
           "Specific reboiler duty [MJ/kg acid gas]": (Q_reb * 3.6 / max(rc * 44.01 + rh * 34.08, 1e-9))}
    if Fa > 1e-12 and free.sum() > 1e-9:
        res["Free liquid knocked out [kmol/h]"] = float(free.sum())
    if n_acid <= 1e-12:
        res["Note"] = "The feed already meets the specification: nothing to remove"
    if warn:
        res["Warning"] = "; ".join(warn)
    en = [EnergyStream(f"Q-{unit['name']} reboiler", Q_reb, f"{unit['name']} regenerator", "heat")]
    if P_pump > 1e-9:
        en.append(EnergyStream(f"W-{unit['name']} pumps", P_pump, f"{unit['name']} regenerator", "work"))
    return outs, res, en


# =================================================================================== relief valve (PSV)
API_ORIFICES = [("D", 71.0), ("E", 126.0), ("F", 198.0), ("G", 325.0), ("H", 506.0), ("J", 830.0), ("K", 1186.0),
                ("L", 1841.0), ("M", 2323.0), ("N", 2800.0), ("P", 4116.0), ("Q", 7126.0), ("R", 10320.0),
                ("T", 16774.0)]
FIRE_ENV = {"Bare vessel": 1.0, "Insulated (conductivity ≤ 0.07 W/m·K)": 0.3, "Water spray": 1.0, "Buried / earth-covered": 0.03}


def select_orifice(A_mm2):
    """(letter, area, number of valves) for a required area; several valves of the largest orifice if needed."""
    for letter, a in API_ORIFICES:
        if a >= A_mm2:
            return letter, a, 1
    a = API_ORIFICES[-1][1]
    return API_ORIFICES[-1][0], a, int(math.ceil(A_mm2 / a))


def gas_relief_area(W_kg_h, T, P1_kPa, P2_kPa, k, Z, M, Kd=0.975, Kb=1.0, Kc=1.0):
    """Required orifice area [mm²] for a vapour (API 520 Part I, SI)."""
    r_crit = (2.0 / (k + 1.0)) ** (k / (k - 1.0))
    if P2_kPa <= r_crit * P1_kPa:
        C = 520.0 * math.sqrt(k * (2.0 / (k + 1.0)) ** ((k + 1.0) / (k - 1.0)))
        return 13160.0 * W_kg_h / (C * Kd * P1_kPa * Kb * Kc) * math.sqrt(T * Z / M), "critical"
    r = P2_kPa / P1_kPa
    F2 = math.sqrt(k / (k - 1.0) * r ** (2.0 / k) * (1.0 - r ** ((k - 1.0) / k)) / (1.0 - r))
    return 17.9 * W_kg_h / (F2 * Kd * Kc) * math.sqrt(Z * T / (M * P1_kPa * (P1_kPa - P2_kPa))), "sub-critical"


def liquid_relief_area(Q_L_min, G, dP_kPa, Kd=0.65, Kw=1.0, Kc=1.0, Kv=1.0):
    """Required area [mm²] for a liquid (API 520 Part I, SI)."""
    return 11.78 * Q_L_min / (Kd * Kw * Kc * Kv) * math.sqrt(G / max(dP_kPa, 1e-6))


def fire_load(D, L, level_pct, orient, F_env, adequate, lam_kJ_kg):
    """(heat input kW, wetted area m², relieving vapour kg/h) of a fire (API 521: Q = C·F·A^0.82)."""
    h = min(max(level_pct / 100.0, 0.0), 1.0)
    if orient == "Vertical":
        A = math.pi * D * min(L * h, 7.6)                              # wetted wall up to 7.6 m (25 ft)
    else:
        th = 2.0 * math.acos(1.0 - 2.0 * h)
        A = 0.5 * D * th * L + 2.0 * (D * D / 8.0) * (th - math.sin(th))   # shell + two flat ends (segment)
    Q = (43.2 if adequate else 70.9) * F_env * A ** 0.82
    return Q, A, Q / lam_kJ_kg * 3600.0


SCHEMA_RELIEF = {
    "relief_valve": {
        "label": "Relief valve (PSV)", "prefix": "PSV", "category": "Safety",
        "ports": {"in": {"in": {"multi": False}}, "out": {"out": {"multi": False}}},
        "params": [
            _s("scenario", "Relieving load", ["Inlet stream flow", "Fire case (from the vessel)"], "Inlet stream flow",
               help="Inlet stream flow: the stream is the relieving load. Fire case: the load comes from the wetted "
                    "area (API 521); the stream gives the composition and conditions"),
            _f("P_set", "Set pressure", "bar(a)", 60.0, minv=1.5),
            _f("over", "Allowable overpressure", "%", 10.0, minv=3.0, maxv=50.0,
               help="10 % single valve, 16 % multiple valves, 21 % fire"),
            _f("P_back", "Back pressure (flare header)", "bar(a)", 2.0, minv=1.01325),
            _f("Kd", "Discharge coefficient (0 = default)", "-", 0.0, minv=0.0, maxv=1.0,
               help="API default 0.975 vapour, 0.65 liquid"),
            _f("Kb", "Back-pressure correction Kb", "-", 1.0, minv=0.1, maxv=1.0,
               help="1.0 for a conventional valve below ~10 % back pressure; use the manufacturer's value for balanced bellows"),
            _f("D_v", "Vessel diameter", "m", 3.0, {"scenario": "Fire case (from the vessel)"}, minv=0.1),
            _f("L_v", "Vessel tangent length / height", "m", 8.0, {"scenario": "Fire case (from the vessel)"}, minv=0.1),
            _f("level", "Liquid level", "%", 50.0, {"scenario": "Fire case (from the vessel)"}, minv=0.0, maxv=100.0),
            _s("orient", "Orientation", ["Horizontal", "Vertical"], "Horizontal", {"scenario": "Fire case (from the vessel)"}),
            _s("env", "Fire environment", list(FIRE_ENV), "Bare vessel", {"scenario": "Fire case (from the vessel)"}),
            _s("drain", "Drainage and fire fighting", ["Adequate", "Inadequate"], "Adequate",
               {"scenario": "Fire case (from the vessel)"}),
        ],
    },
}


def calc_relief(unit, ins, fp):
    p = unit["params"]
    s = _one(ins, "in")
    if s.empty:
        return {"out": [zero_stream("", fp, s.z, s.T, float(p["P_back"]))]}, {"Status": "No flow"}, []
    Pb = float(p["P_back"])
    P_set = float(p["P_set"])
    if Pb >= P_set:
        raise UnitError(f"{unit['name']}: back pressure {Pb:.2f} bar must be below the set pressure {P_set:.2f} bar")
    P_set_g = P_set - 1.01325
    P1 = P_set_g * (1.0 + float(p["over"]) / 100.0) + 1.01325
    # relieving conditions: the stream composition and temperature at P1
    fr = fp.pt_flash(s.z, s.T, P1) if abs(s.P - P1) > 1e-6 else s.flash
    v, l = fr.phase("V"), fr.phase("L")
    w = fr.phase("W")
    MW = s.MW
    W_stream = s.F * MW                                            # kg/h
    notes = []
    if p.get("scenario") == "Fire case (from the vessel)":
        # latent heat of the stream composition at the relieving pressure
        try:
            f0 = fp.pvf_flash(s.z, P1, 0.0, s.T)
            f1 = fp.pvf_flash(s.z, P1, 1.0, s.T)
            lam = (f1.H - f0.H) / MW                                 # kJ/kg (J/mol ÷ kg/kmol)
        except Exception:                                            # noqa: BLE001
            lam = 300.0
            notes.append("latent heat not found from the EOS: 300 kJ/kg assumed")
        lam = max(lam, 50.0)
        if fr.vf > 0.9999 or l is None:
            lam = 300.0
            notes.append("the stream is a gas at relieving conditions: a fire case needs the liquid inventory - "
                         "latent heat of 300 kJ/kg assumed; use a liquid-containing stream (e.g. the separator liquid)")
        Q, Aw, W = fire_load(float(p["D_v"]), float(p["L_v"]), float(p["level"]), p.get("orient", "Horizontal"),
                             FIRE_ENV.get(p.get("env", "Bare vessel"), 1.0), p.get("drain", "Adequate") == "Adequate", lam)
        extra = {"Fire heat input [kW]": Q, "Wetted area [m²]": Aw, "Latent heat [kJ/kg]": lam}
        if abs(W_stream - W) > 0.1 * max(W, 1.0):
            notes.append(f"stream flow {W_stream:,.0f} kg/h differs from the fire load {W:,.0f} kg/h - set the feed to "
                         "the fire load so the flare sees the right flow")
    else:
        W, extra = W_stream, {}
    vf = fr.vf
    T = s.T
    A_v = A_l = 0.0
    regime = ""
    if v is not None and vf > 1e-3:
        Wv = W * (v.beta * v.MW) / max(v.beta * v.MW + (1 - v.beta) * (l.MW if l else (w.MW if w else v.MW)), 1e-9) \
            if vf < 0.999 else W
        k = v.Cp / max(v.Cv, 1e-9)
        A_v, regime = gas_relief_area(Wv, T, P1 * 100.0, Pb * 100.0, k, v.Z, v.MW,
                                      float(p.get("Kd") or 0.975), float(p.get("Kb", 1.0)))
    if vf < 0.999:
        liq = l or w
        if liq is not None:
            Wl = W - (Wv if v is not None and vf > 1e-3 else 0.0)
            rho = liq.rho
            QL = Wl / rho / 60.0 * 1000.0                                    # L/min
            A_l = liquid_relief_area(QL, rho / 999.0, (P1 - Pb) * 100.0, float(p.get("Kd") or 0.65), 1.0, 1.0, 1.0)
    A = A_v + A_l
    letter, a_sel, n = select_orifice(A)
    if v is not None and 1e-3 < vf < 0.999:
        notes.append("two-phase relief: areas of the vapour and liquid parts are added - verify with the API 520 "
                     "Annex C (ω) method")
    if regime == "sub-critical" and float(p.get("Kb", 1.0)) > 0.99:
        notes.append("flow is sub-critical at this back pressure: check Kb with the valve vendor")
    if Pb / P_set > 0.1 and float(p.get("Kb", 1.0)) > 0.99:
        notes.append(f"back pressure is {100 * Pb / P_set:.0f} % of the set pressure: use a balanced-bellows valve (Kb < 1)")
    if n > 1:
        notes.append(f"{n} × orifice {letter} needed - consider a larger valve (API 526 sizes beyond T) or a pilot-operated valve")
    out = _ph(fp, s.z, Pb, s.H, s.T)
    res = {"Relieving load [kg/h]": W, "Relieving pressure [bar(a)]": P1, "Relieving T [°C]": T - K0,
           "Vapour fraction at relief": vf, "Flow regime": regime or "liquid",
           "Required area [mm²]": A, "Selected orifice": f"{n} × {letter} ({a_sel:,.0f} mm²)" if n > 1
           else f"{letter} ({a_sel:,.0f} mm²)", "Capacity at the selected orifice [% of load]": 100.0 * a_sel * n / max(A, 1e-9),
           "Outlet T [°C]": out.T - K0}
    res.update(extra)
    if notes:
        res["Warning"] = "; ".join(notes)
    return {"out": [make_stream("", fp, s.F, s.z, out)]}, res, []


# =================================================================================== flare
SCHEMA_FLARE = {
    "flare": {
        "label": "Flare / vent stack", "prefix": "FL", "category": "Safety",
        "ports": {"in": {"in": {"multi": True}}, "out": {}},
        "params": [
            _f("H_stack", "Stack height (tip above grade)", "m", 40.0, minv=1.0),
            _f("D_tip", "Tip diameter (0 = size for the Mach number)", "mm", 0.0, minv=0.0),
            _f("mach", "Design tip Mach number", "-", 0.5, minv=0.05, maxv=1.0, help="API 521: up to 0.5 for short-term releases, 0.2 continuous"),
            _f("F_rad", "Fraction of heat radiated", "-", 0.2, minv=0.05, maxv=0.5,
               help="0.15 hydrogen / low-MW gas … 0.3 heavier or sooting gases"),
            _f("tau", "Atmospheric transmissivity", "-", 1.0, minv=0.1, maxv=1.0),
            _f("K_lim", "Allowable radiation", "kW/m²", 4.73, minv=0.5, help="API 521: 1.58 continuous exposure, 4.73 emergency with shelter, 6.31 up to 30 s"),
            _f("dist", "Receptor distance from the stack base", "m", 100.0, minv=1.0),
            _f("P_tip", "Tip pressure", "bar(a)", 1.3, minv=1.0),
        ],
    },
}


def calc_flare(unit, ins, fp):
    p = unit["params"]
    live = _live(ins.get("in", []))
    if not live:
        return {}, {"Status": "No flow"}, []
    n = sum(s.F * s.z for s in live)                       # kmol/h by component
    F = float(n.sum())
    Q = sum(n[i] * 1000.0 * lhv_J_mol(k, float(fp.MW[i])) for i, k in enumerate(fp.keys)) / 3600.0   # W
    mass = float(n @ fp.MW)                                 # kg/h
    MW = mass / F
    Qmw = Q / 1e6
    Lf = 0.00326 * Q ** 0.478 if Q > 0 else 0.0           # Hajek & Ludwig, m (Q in W)
    Hc = float(p["H_stack"]) + 0.5 * Lf                    # flame centre above grade
    Kt = float(p["tau"]) * float(p["F_rad"]) * Q
    R_lim = math.sqrt(Kt / (4.0 * math.pi * float(p["K_lim"]) * 1000.0)) if Kt > 0 else 0.0
    d_lim = math.sqrt(max(R_lim ** 2 - Hc ** 2, 0.0))
    d = float(p["dist"])
    q_rec = Kt / (4.0 * math.pi * (d * d + Hc * Hc)) / 1000.0
    T_tip = float(np.mean([s.T for s in live]))
    k = 1.25
    c = math.sqrt(k * R * T_tip / (MW / 1000.0))
    Ptip = float(p["P_tip"])
    rho = Ptip * 1e5 * MW / 1000.0 / (R * T_tip)
    Qv = mass / rho / 3600.0                               # m³/s at the tip
    Dt = float(p["D_tip"]) / 1000.0 or math.sqrt(4.0 * Qv / (math.pi * float(p["mach"]) * c))
    u = Qv / (math.pi * Dt * Dt / 4.0)
    co2 = sum(n[i] * FORMULA.get(k_, (0, 0, 0))[0] * 44.01 for i, k_ in enumerate(fp.keys)) \
        + (n[fp.keys.index("CO2")] * 44.01 if "CO2" in fp.keys else 0.0)
    so2 = sum(n[i] * FORMULA.get(k_, (0, 0, 0))[2] * 64.07 for i, k_ in enumerate(fp.keys))
    res = {"Flared gas [kg/h]": mass, "Flared gas [MSm³/d]": F * STD_VOL * 24.0 / 1e6, "Molecular weight": MW,
           "Heat release [MW]": Qmw, "Flame length [m]": Lf, "Flame centre height [m]": Hc,
           "Radiation at the receptor [kW/m²]": q_rec,
           "Distance to the radiation limit [m]": d_lim, "Tip diameter [mm]": Dt * 1000.0,
           "Tip velocity [m/s]": u, "Tip Mach number": u / c,
           "CO₂ emitted [t/h]": co2 / 1000.0, "SO₂ emitted [kg/h]": so2}
    warn = []
    if q_rec > float(p["K_lim"]):
        warn.append(f"radiation {q_rec:.1f} kW/m² at {d:.0f} m exceeds {float(p['K_lim']):.2f} kW/m²: raise the stack or "
                    f"keep people beyond {d_lim:.0f} m")
    if u / c > 0.5 + 1e-9:
        warn.append(f"tip Mach number {u / c:.2f} exceeds 0.5 - increase the tip diameter")
    if any(s.flash is not None and s.flash.vf < 0.98 for s in live):
        warn.append("liquid in the flare stream: a knock-out drum is needed upstream")
    if warn:
        res["Warning"] = "; ".join(warn)
    return {}, res, []


# =================================================================================== component splitter
SCHEMA_SPLITTER = {
    "comp_splitter": {
        "label": "Component splitter", "prefix": "CS", "category": "Separation",
        "ports": {"in": {"feed": {"multi": True}}, "out": {"top": {"multi": False}, "bottom": {"multi": False}}},
        "params": [
            _t("split", "Fraction of each component to the top outlet", "C1: 1, C2: 0.9",
               help="Component: fraction pairs separated by commas. Components not listed go to the bottom outlet"),
            _f("T_top", "Top outlet temperature (blank = feed)", "°C", -999.0, help="−999 keeps the feed temperature"),
            _f("T_bot", "Bottom outlet temperature (blank = feed)", "°C", -999.0, help="−999 keeps the feed temperature"),
            _f("dP", "Pressure drop", "bar", 0.0, minv=0.0),
        ],
    },
}


def parse_split(txt, keys):
    out = {}
    for part in str(txt).replace(";", ",").split(","):
        if not part.strip():
            continue
        if ":" not in part:
            raise UnitError(f"split '{part.strip()}': write component: fraction")
        k, v = part.split(":", 1)
        k = k.strip()
        if k not in keys:
            raise UnitError(f"split: component '{k}' is not in the fluid package")
        try:
            f = float(v)
        except ValueError:
            raise UnitError(f"split: '{v.strip()}' is not a number") from None
        if not 0.0 <= f <= 1.0:
            raise UnitError(f"split fraction for {k} must be between 0 and 1")
        out[k] = f
    return out


def calc_comp_splitter(unit, ins, fp):
    p = unit["params"]
    mixed, z0 = _feed_mix(unit, ins, fp)
    if mixed is None:
        return {"top": [zero_stream("", fp, z0)], "bottom": [zero_stream("", fp, z0)]}, {"Status": "No flow"}, []
    F, z, H, P, T0 = mixed
    P2 = P - float(p.get("dP", 0.0))
    _check_P(P2, unit["name"])
    sp = parse_split(p.get("split", ""), fp.keys)
    n = F * z
    f = np.array([sp.get(k, 0.0) for k in fp.keys])
    top, bot = n * f, n * (1.0 - f)
    Ft, Fb = float(top.sum()), float(bot.sum())
    Tt = (float(p["T_top"]) + K0) if float(p.get("T_top", -999)) > -900 else T0
    Tb = (float(p["T_bot"]) + K0) if float(p.get("T_bot", -999)) > -900 else T0
    outs, Hout, res = {}, 0.0, {"Top flow [kmol/h]": Ft, "Bottom flow [kmol/h]": Fb}
    for name, nn, FF, T in (("top", top, Ft, Tt), ("bottom", bot, Fb, Tb)):
        if FF > 1e-12:
            zz = nn / FF
            fr = fp.pt_flash(zz, T, P2)
            outs[name] = [make_stream("", fp, FF, zz, fr)]
            Hout += FF * fr.H
            res[f"{name.capitalize()} T [°C]"] = fr.T - K0
            res[f"{name.capitalize()} vapour fraction"] = fr.vf
        else:
            outs[name] = [zero_stream("", fp, z, T, P2)]
    Q = (Hout - F * H) / 3600.0
    res["Duty [kW]"] = Q
    return outs, res, [_energy(unit, Q)]


# =================================================================================== reactors
_TERM = re.compile(r"^\s*(\d*\.?\d+)?\s*([A-Za-z][A-Za-z0-9]*)\s*$")


def parse_reaction(txt, keys):
    """'C1 + 2 O2 -> CO2 + 2 H2O'  ->  {component: signed stoichiometric coefficient} (reactants negative)."""
    if "->" not in txt and "=" not in txt:
        raise UnitError(f"reaction '{txt.strip()}': write reactants -> products")
    left, right = re.split(r"->|=>|=", txt, maxsplit=1)
    nu, base = {}, None
    for side, sign in ((left, -1.0), (right, 1.0)):
        for term in side.split("+"):
            m = _TERM.match(term)
            if not m:
                raise UnitError(f"reaction term '{term.strip()}' not understood")
            coef = float(m.group(1)) if m.group(1) else 1.0
            k = m.group(2)
            if k not in keys:
                raise UnitError(f"reaction: component '{k}' is not in the fluid package")
            nu[k] = nu.get(k, 0.0) + sign * coef
            if sign < 0 and base is None:
                base = k
    return nu, base


def _reaction_set(txt, conv_txt, keys):
    rxns = [r for r in str(txt).split(";") if r.strip()]
    if not rxns:
        raise UnitError("no reaction defined")
    convs = [c for c in str(conv_txt).replace(";", ",").split(",") if c.strip()]
    out = []
    for i, r in enumerate(rxns):
        nu, base = parse_reaction(r, keys)
        try:
            cv = float(convs[i]) if i < len(convs) else float(convs[-1]) if convs else 1.0
        except ValueError:
            raise UnitError("conversion list must be numbers between 0 and 1") from None
        if not 0.0 <= cv <= 1.0:
            raise UnitError("conversion must be between 0 and 1")
        out.append((nu, base, cv, r.strip()))
    return out


def dH_rxn(nu):
    """Heat of reaction at 298 K [J per mole of reaction as written]."""
    return sum(v * DHF.get(k, 0.0) for k, v in nu.items())


def _enthalpy_total(fp, n, T, P):
    """(total enthalpy flow incl. formation [kW], flash) for mole flows n [kmol/h] at T, P."""
    F = float(n.sum())
    z = n / F
    fr = fp.pt_flash(z, T, P)
    return (F * fr.H + float(n @ np.array([DHF.get(k, 0.0) for k in fp.keys]))) / 3600.0, fr


def _react_outlet(unit, fp, n_out, P2, H_in_tot, T_guess, spec, T_spec, who):
    """Outlet stream and duty for the product mole flows."""
    F = float(n_out.sum())
    z = n_out / F
    hf = float(n_out @ np.array([DHF.get(k, 0.0) for k in fp.keys])) / 3600.0        # kW
    if spec == "Adiabatic":
        Hmol = (H_in_tot - hf) * 3600.0 / F                                           # J/mol
        fr = _ph(fp, z, P2, Hmol, T_guess)
        duty = 0.0
    else:
        fr = fp.pt_flash(z, T_spec, P2)
        duty = F * fr.H / 3600.0 + hf - H_in_tot
    return make_stream("", fp, F, z, fr), duty


SCHEMA_CONV = {
    "conv_reactor": {
        "label": "Conversion reactor", "prefix": "R", "category": "Reactors",
        "ports": {"in": {"feed": {"multi": True}}, "out": {"out": {"multi": False}}},
        "params": [
            _t("reactions", "Reactions (separated by ;)", "C1 + 2 O2 -> CO2 + 2 H2O",
               help="Component keys of the fluid package, e.g. C1 + 2 O2 -> CO2 + 2 H2O ; H2S + 1.5 O2 -> H2O + SO2 needs SO2 in the package"),
            _t("conv", "Conversion of the first reactant (one per reaction)", "1.0",
               help="Fraction of the first reactant of each reaction that reacts, in order. The extent is limited by "
                    "the other reactants"),
            _s("spec", "Specification", ["Outlet temperature", "Adiabatic"], "Outlet temperature"),
            _f("T_out", "Outlet temperature", "°C", 800.0, {"spec": "Outlet temperature"}),
            _f("dP", "Pressure drop", "bar", 0.0, minv=0.0),
        ],
    },
    "eq_reactor": {
        "label": "Equilibrium reactor", "prefix": "R", "category": "Reactors",
        "ports": {"in": {"feed": {"multi": True}}, "out": {"out": {"multi": False}}},
        "params": [
            _t("reaction", "Reaction", "C1 + 2 H2O -> 4 H2 + CO2",
               help="One reaction among components with ΔHf and ΔGf data: N2 O2 H2 CO2 H2O H2S C1 C2 C3 nC4 MeOH"),
            _s("spec", "Specification", ["Outlet temperature", "Adiabatic"], "Outlet temperature"),
            _f("T_out", "Outlet temperature", "°C", 700.0, {"spec": "Outlet temperature"}),
            _f("dP", "Pressure drop", "bar", 0.0, minv=0.0),
        ],
    },
}


def calc_conv_reactor(unit, ins, fp):
    p = unit["params"]
    mixed, z0 = _feed_mix(unit, ins, fp)
    if mixed is None:
        return {"out": [zero_stream("", fp, z0)]}, {"Status": "No flow"}, []
    F, z, H, P, T0 = mixed
    P2 = P - float(p.get("dP", 0.0))
    _check_P(P2, unit["name"])
    n_in = F * z
    H_in_tot = F * H / 3600.0 + float(n_in @ np.array([DHF.get(k, 0.0) for k in fp.keys])) / 3600.0
    rset = _reaction_set(p.get("reactions", ""), p.get("conv", "1.0"), fp.keys)
    n = n_in.copy()
    ext, dHr, notes = [], 0.0, []
    for nu, base, cv, txt in rset:
        ib = fp.keys.index(base)
        xi = cv * n[ib] / -nu[base]
        for k, v in nu.items():                                   # availability of every reactant
            if v < 0 and n[fp.keys.index(k)] / -v < xi - 1e-12:
                xi = n[fp.keys.index(k)] / -v
                notes.append(f"{k} limits '{txt}' (extent reduced)")
        for k, v in nu.items():
            n[fp.keys.index(k)] += v * xi
        n = np.maximum(n, 0.0)
        ext.append(xi)
        dHr += xi * dH_rxn(nu) / 3600.0
    spec = p.get("spec", "Outlet temperature")
    out, duty = _react_outlet(unit, fp, n, P2, H_in_tot, max(T0, 400.0), spec, float(p.get("T_out", 800.0)) + K0, unit["name"])
    res = {"Outlet T [°C]": out.T - K0, "Outlet P [bar(a)]": P2, "Heat of reaction [kW]": dHr,
           "Duty [kW]": duty, "Outlet vapour fraction": out.flash.vf}
    for i, (nu, base, cv, txt) in enumerate(rset):
        res[f"Extent {i + 1} [kmol/h]"] = ext[i]
        res[f"Conversion of {base} (reaction {i + 1}) [%]"] = 100.0 * ext[i] * -nu[base] / max(n_in[fp.keys.index(base)], 1e-12)
    if notes:
        res["Warning"] = "; ".join(notes)
    en = [_energy(unit, duty)] if abs(duty) > 1e-9 else []
    return {"out": [out]}, res, en


def lnK_of_T(fp, nu, T, n_pts=40):
    """ln K(T) of an ideal-gas reaction: ΔG298 and ΔH298 from the formation tables, ΔH(T) from the package's ideal-gas
    heat capacities, integrated d(ΔG/T)/dT = -ΔH/T² (Simpson). Without ``fp`` the constant-ΔH van't Hoff form is used."""
    keys = list(nu)
    dH0 = sum(nu[k] * DHF[k] for k in keys)
    dG0 = sum(nu[k] * DGF[k] for k in keys)
    if fp is None or abs(T - 298.15) < 1e-9:
        return -dG0 / (R * 298.15) - dH0 / R * (1.0 / T - 1.0 / 298.15)
    idx = np.array([fp.keys.index(k) for k in keys])
    v = np.array([nu[k] for k in keys])
    n = n_pts + (n_pts % 2)
    Ts = np.linspace(298.15, T, n + 1)
    f = np.array([(dH0 + float(v @ fp.h_ig(t, idx))) / (R * t * t) for t in Ts])
    h = (T - 298.15) / n
    integral = h / 3.0 * (f[0] + f[-1] + 4.0 * f[1:-1:2].sum() + 2.0 * f[2:-1:2].sum())
    return -dG0 / (R * 298.15) + integral


def equilibrium_extent(nu, n0, T, P, fp=None):
    """Reaction extent [kmol/h] at ideal-gas equilibrium (bisection on the mole-fraction product)."""
    keys = list(nu)
    lnK = lnK_of_T(fp, nu, T)
    dn = sum(nu.values())
    lo = max([-n0.get(k, 0.0) / nu[k] for k in keys if nu[k] > 0] + [-1e9])
    hi = min([n0.get(k, 0.0) / -nu[k] for k in keys if nu[k] < 0] + [1e9])
    if hi <= lo:
        return 0.0, lnK
    ntot0 = sum(n0.values())

    def f(xi):
        nt = ntot0 + dn * xi
        s = 0.0
        for k in keys:
            nk = n0.get(k, 0.0) + nu[k] * xi
            if nk <= 0:
                return -1e30                      # a reactant exhausted (or product absent): push the extent down
            s += nu[k] * math.log(nk / nt * P)
        return s - lnK
    a, b = lo + (hi - lo) * 1e-12, hi - (hi - lo) * 1e-12
    fa, fb = f(a), f(b)
    if fa > 0:
        return a, lnK
    if fb < 0:
        return b, lnK
    for _ in range(200):
        m = 0.5 * (a + b)
        fm = f(m)
        if fm < 0:
            a = m
        else:
            b = m
        if b - a < 1e-14 * max(abs(hi - lo), 1.0):
            break
    return 0.5 * (a + b), lnK


def calc_eq_reactor(unit, ins, fp):
    p = unit["params"]
    mixed, z0 = _feed_mix(unit, ins, fp)
    if mixed is None:
        return {"out": [zero_stream("", fp, z0)]}, {"Status": "No flow"}, []
    F, z, H, P, T0 = mixed
    P2 = P - float(p.get("dP", 0.0))
    _check_P(P2, unit["name"])
    nu, _ = parse_reaction(p.get("reaction", ""), fp.keys)
    for k in nu:
        if k not in DGF:
            raise UnitError(f"{unit['name']}: no ΔGf data for {k} (available: {', '.join(DGF)})")
    n_in = F * z
    keyv = np.array([DHF.get(k, 0.0) for k in fp.keys])
    H_in_tot = F * H / 3600.0 + float(n_in @ keyv) / 3600.0
    n0 = {k: float(n_in[fp.keys.index(k)]) for k in nu}

    def products(T):
        xi, lnK = equilibrium_extent(nu, n0, T, P2, fp)
        n = n_in.copy()
        for k, v in nu.items():
            n[fp.keys.index(k)] += v * xi
        return np.maximum(n, 0.0), xi, lnK

    spec = p.get("spec", "Outlet temperature")
    if spec == "Outlet temperature":
        T = float(p.get("T_out", 700.0)) + K0
        n, xi, lnK = products(T)
        out, duty = _react_outlet(unit, fp, n, P2, H_in_tot, T, spec, T, unit["name"])
    else:
        def g(T):
            n, _, _ = products(T)
            tot, _ = _enthalpy_total(fp, n, T, P2)
            return tot - H_in_tot
        lo, hi = 250.0, 3000.0
        glo, ghi = g(lo), g(hi)
        if glo > 0 or ghi < 0:
            raise UnitError(f"{unit['name']}: no adiabatic outlet temperature between {lo:.0f} and {hi:.0f} K")
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            if g(mid) < 0:
                lo = mid
            else:
                hi = mid
            if hi - lo < 0.01:
                break
        T = 0.5 * (lo + hi)
        n, xi, lnK = products(T)
        out, duty = _react_outlet(unit, fp, n, P2, H_in_tot, T, "Outlet temperature", T, unit["name"])
        duty = 0.0
    base = next((k for k, v in nu.items() if v < 0), None)
    res = {"Outlet T [°C]": T - K0, "Outlet P [bar(a)]": P2, "Extent [kmol/h]": xi,
           "Equilibrium constant K": math.exp(lnK), "Heat of reaction at 298 K [kJ/mol]": dH_rxn(nu) / 1000.0,
           "Duty [kW]": duty, "Outlet vapour fraction": out.flash.vf}
    if base:
        res[f"Conversion of {base} [%]"] = 100.0 * xi * -nu[base] / max(n0[base], 1e-12)
    if out.flash.vf < 0.999:
        res["Warning"] = "liquid in the outlet: the equilibrium model is ideal-gas, treat the result with care"
    en = [_energy(unit, duty)] if abs(duty) > 1e-9 else []
    return {"out": [out]}, res, en


# =================================================================================== control valve Cv
VALVE_EXTRA = [
    _f("Cv_rated", "Valve rated Cv (0 = only report the required Cv)", "-", 0.0, minv=0.0,
       help="Rated (100 % open) Cv of the selected valve; gives the opening"),
    _s("char", "Inherent characteristic", ["Equal percentage", "Linear"], "Equal percentage"),
    _f("xT", "Pressure-drop ratio factor xT", "-", 0.70, minv=0.1, maxv=1.0,
       help="Choked-flow factor of the valve (0.7 typical globe, 0.2-0.4 ball/butterfly)"),
]


def valve_sizing(unit, s_in, fr_out, P2):
    """Required Cv (US), Kv and the opening of a control valve at the actual flow; returns a results dict."""
    p = unit["params"]
    P1 = s_in.P
    dP = P1 - P2
    if dP <= 1e-9 or s_in.empty:
        return {}
    fl = s_in.flash
    v, l, w = fl.phase("V"), fl.phase("L"), fl.phase("W")
    xT = float(p.get("xT", 0.7))
    parts, choked, Kv = [], False, 0.0
    # liquid part (m³/h, SG)
    for ph in (l, w):
        if ph is not None:
            qm3h = s_in.F * ph.beta * ph.MW / ph.rho
            Kv += qm3h / math.sqrt(dP) * math.sqrt(ph.rho / 1000.0)    # Kv = Q sqrt(SG/ΔP[bar])
    if v is not None and v.beta > 1e-3:
        k = v.Cp / max(v.Cv, 1e-9)
        Fk = k / 1.4
        x = dP / P1
        xc = Fk * xT
        if x >= xc:
            choked = True
            x = xc
        Y = max(1.0 - x / (3.0 * xc), 2.0 / 3.0)
        Qn = s_in.F * v.beta * 22.414                                           # Nm³/h (0 °C)
        if x > 0:                                                               # Kv = Qn/(N9 P1 Y) sqrt(M T Z / x), N9 = 24.6
            Kv += Qn / (24.6 * (P1 * 100.0) * Y) * math.sqrt(v.MW * s_in.T * v.Z / x)
    Cv = Kv * 1.156
    res = {"Required Cv [-]": Cv, "Required Kv [m³/h]": Kv}
    if v is not None and v.beta > 1e-3:
        res["Choked flow"] = "Yes" if choked else "No"
    rated = float(p.get("Cv_rated", 0.0) or 0.0)
    if rated > 0.0 and Cv > 0:
        if p.get("char", "Equal percentage") == "Linear":
            x_open = Cv / rated
        else:
            x_open = 1.0 + math.log(max(Cv / rated, 1e-12)) / math.log(50.0)          # rangeability 50
        res["Valve opening [%]"] = 100.0 * x_open
        if x_open > 0.85 or x_open < 0.10:
            res["Warning"] = (f"valve {100 * x_open:.0f} % open: choose a different size (target 20–80 %)"
                              if x_open <= 1.0 else f"valve too small: needs {Cv:.0f} Cv, rated {rated:.0f}")
        if x_open > 1.0:
            res["Warning"] = f"valve too small: needs Cv {Cv:.0f}, rated {rated:.0f}"
    if choked:
        res["Note"] = "Choked gas flow: the outlet pressure no longer controls the flow"
    return res


# =================================================================================== compressor / pump drivers
DRIVER_EXTRA = [
    _s("driver", "Driver", ["None", "Electric motor", "Gas turbine"], "None"),
    _f("driver_rating", "Driver rated power at ISO conditions", "kW", 0.0, {"driver": ["Electric motor", "Gas turbine"]},
       minv=0.0),
    _f("driver_eff", "Driver efficiency (motor electrical / turbine thermal)", "%", 95.0,
       {"driver": ["Electric motor", "Gas turbine"]}, minv=10.0, maxv=100.0,
       help="Motor + VSD ≈ 95 %; gas turbine ≈ 30-38 % (simple cycle)"),
    _f("amb_T", "Ambient air temperature", "°C", 15.0, {"driver": "Gas turbine"},
       help="Turbine power falls about 0.7 % per K above 15 °C"),
]


def driver_results(unit, shaft_kW, fuel_LHV_MJ_Sm3=35.0):
    """(results, warning or None) for a compressor / pump driver."""
    p = unit["params"]
    d = p.get("driver", "None")
    if d == "None" or shaft_kW is None:
        return {}, None
    eff = float(p.get("driver_eff", 95.0)) / 100.0
    rating = float(p.get("driver_rating", 0.0))
    res = {}
    if d == "Gas turbine":
        avail = rating * (1.0 - 0.007 * (float(p.get("amb_T", 15.0)) - 15.0)) if rating > 0 else 0.0
        fuel_kW = shaft_kW / max(eff, 1e-3)
        res["Fuel heat [kW]"] = fuel_kW
        res["Fuel gas [Sm³/h]"] = fuel_kW * 3.6 / fuel_LHV_MJ_Sm3
        res["CO₂ from the turbine [t/h]"] = fuel_kW * 3.6 / 1000.0 * 56.1 / 1000.0
    else:
        avail = rating
        res["Electrical input [kW]"] = shaft_kW / max(eff, 1e-3)
    warn = None
    if rating > 0:
        res["Driver power available [kW]"] = avail
        res["Driver margin [%]"] = 100.0 * (avail / max(shaft_kW, 1e-9) - 1.0)
        if avail < shaft_kW:
            warn = f"{d.lower()} can give {avail:,.0f} kW but the machine needs {shaft_kW:,.0f} kW"
        elif avail < 1.05 * shaft_kW:
            warn = f"driver margin only {res['Driver margin [%]']:.1f} % (< 5 %)"
    return res, warn


# =================================================================================== registration
def register():
    from .unitops import CATALOGUE, CALC
    CATALOGUE.update(SCHEMA_AMINE)
    CATALOGUE.update(SCHEMA_RELIEF)
    CATALOGUE.update(SCHEMA_FLARE)
    CATALOGUE.update(SCHEMA_SPLITTER)
    CATALOGUE.update(SCHEMA_CONV)
    CALC.update({"amine_contactor": calc_amine, "relief_valve": calc_relief, "flare": calc_flare,
                 "comp_splitter": calc_comp_splitter, "conv_reactor": calc_conv_reactor,
                 "eq_reactor": calc_eq_reactor})
    names = {p_["key"] for p_ in CATALOGUE["valve"]["params"]}
    CATALOGUE["valve"]["params"] += [e for e in VALVE_EXTRA if e["key"] not in names]
    names = {p_["key"] for p_ in CATALOGUE["compressor"]["params"]}
    CATALOGUE["compressor"]["params"] += [e for e in DRIVER_EXTRA if e["key"] not in names]


register()
