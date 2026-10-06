"""Unit operations.

Every unit is a function ``calc(unit, ins, fp) -> (outs, results, energy)``:

* ``unit``  - dict from the flowsheet model (type, name, params)
* ``ins``   - {port: [MaterialStream, ...]} in connection order
* ``outs``  - {port: [MaterialStream, ...]} one entry per connected outlet stream
* ``results`` - ordered dict of display values (metric units)
* ``energy``  - list of EnergyStream

Parameters are stored in display units (degC, bar(a), bar, kW, %).
"""
from __future__ import annotations

import math

import numpy as np

from .thermo import FluidPackage, FlashError, R
from .streams import MaterialStream, EnergyStream, make_stream, zero_stream, phase_stream

K0 = 273.15


class UnitError(RuntimeError):
    pass


# Energy-stream sign convention: duty_kW > 0 flows INTO the process (heater duty,
# compressor/pump work), < 0 flows OUT (cooler duty, expander work).


# --------------------------------------------------------------------------
# Unit catalogue: ports, defaults and the property-view schema
# --------------------------------------------------------------------------

def _f(key, label, unit="", default=0.0, show_if=None, help=None, minv=None, maxv=None):
    return {"key": key, "label": label, "kind": "float", "unit": unit, "default": default,
            "show_if": show_if, "help": help, "min": minv, "max": maxv}


def _s(key, label, options, default, show_if=None, help=None):
    return {"key": key, "label": label, "kind": "select", "options": options, "default": default,
            "show_if": show_if, "help": help}


WD_NONE, WD_FEED, WD_BOTH = "Keep (VLE only)", "Decant from the feeds", "Decant from the feeds and the condenser"

CATALOGUE = {
    "feed": {
        "label": "Material stream (feed)", "prefix": "Feed", "category": "Streams",
        "ports": {"in": {}, "out": {"out": {"multi": False}}},
        "params": [
            _s("spec", "Specification", ["T & P", "P & vapour fraction"], "T & P"),
            _f("T_C", "Temperature", "°C", 30.0, {"spec": "T & P"}),
            _f("P_bar", "Pressure", "bar(a)", 50.0),
            _f("VF", "Vapour fraction", "-", 1.0, {"spec": "P & vapour fraction"}, minv=0.0, maxv=1.0),
            _s("flow_basis", "Flow basis", ["kmol/h", "kg/h", "MSm³/d", "Sm³/h", "lbmol/h", "lb/h", "MMscf/d",
                                                  "bbl/d (std liquid)"], "kmol/h"),
            _f("flow", "Flow", "", 1000.0, minv=0.0),
            _s("comp_basis", "Composition basis", ["Mole fractions", "Mass fractions"], "Mole fractions"),
        ],
    },
    "product": {"label": "Material stream (product)", "prefix": "Product", "category": "Streams",
                "ports": {"in": {"in": {"multi": False}}, "out": {}}, "params": []},
    "valve": {
        "label": "Valve", "prefix": "VLV", "category": "Pressure change",
        "ports": {"in": {"in": {"multi": False}}, "out": {"out": {"multi": False}}},
        "params": [_s("spec", "Specification", ["Outlet pressure", "Pressure drop"], "Outlet pressure"),
                   _f("P_out", "Outlet pressure", "bar(a)", 20.0, {"spec": "Outlet pressure"}),
                   _f("dP", "Pressure drop", "bar", 10.0, {"spec": "Pressure drop"})],
    },
    "mixer": {
        "label": "Mixer", "prefix": "MIX", "category": "Piping",
        "ports": {"in": {"in": {"multi": True}}, "out": {"out": {"multi": False}}},
        "params": [_s("pressure", "Outlet pressure", ["Lowest inlet", "Specified"], "Lowest inlet"),
                   _f("P_out", "Outlet pressure", "bar(a)", 20.0, {"pressure": "Specified"})],
    },
    "splitter": {
        "label": "Tee (splitter)", "prefix": "TEE", "category": "Piping",
        "ports": {"in": {"in": {"multi": False}}, "out": {"out": {"multi": True}}},
        "params": [{"key": "fractions", "label": "Split fractions (comma-separated, last = remainder)",
                    "kind": "text", "default": "0.5", "show_if": None, "help": None}],
    },
    "separator": {
        "label": "2-phase separator", "prefix": "V", "category": "Separation",
        "ports": {"in": {"feed": {"multi": True}}, "out": {"vapour": {"multi": False}, "liquid": {"multi": False}}},
        "params": [_f("dP", "Pressure drop", "bar", 0.0), _f("duty", "Heat input", "kW", 0.0)],
    },
    "scrubber": {
        "label": "Gas scrubber", "prefix": "V", "category": "Separation",
        "ports": {"in": {"feed": {"multi": True}}, "out": {"vapour": {"multi": False}, "liquid": {"multi": False}}},
        "params": [_f("dP", "Pressure drop", "bar", 0.0),
                   _s("internal", "Demisting internal", ["Mesh pad", "Vane pack", "Axial cyclones", "No internals"],
                      "Mesh pad"),
                   _f("K", "K-factor override (0 = default for the internal)", "m/s", 0.0, minv=0.0),
                   _f("ID", "Inner diameter (0 = size it)", "mm", 0.0, minv=0.0),
                   _f("margin", "Design margin on gas flow", "%", 20.0, minv=0.0, maxv=200.0)],
    },
    "separator3": {
        "label": "3-phase separator", "prefix": "V", "category": "Separation",
        "ports": {"in": {"feed": {"multi": True}},
                  "out": {"vapour": {"multi": False}, "oil": {"multi": False}, "water": {"multi": False}}},
        "params": [_f("dP", "Pressure drop", "bar", 0.0), _f("duty", "Heat input", "kW", 0.0)],
    },
    "compressor": {
        "label": "Compressor", "prefix": "K", "category": "Rotating",
        "ports": {"in": {"in": {"multi": False}}, "out": {"out": {"multi": False}}},
        "params": [_s("spec", "Specification", ["Outlet pressure", "Pressure ratio", "Performance curve"],
                      "Outlet pressure",
                      help="Performance curve: head and efficiency come from the curve at the actual inlet "
                           "flow and speed, and the outlet pressure is calculated"),
                   _f("P_out", "Outlet pressure", "bar(a)", 60.0, {"spec": "Outlet pressure"}),
                   _f("ratio", "Pressure ratio", "-", 3.0, {"spec": "Pressure ratio"}),
                   _s("eff_type", "Efficiency basis", ["Polytropic", "Adiabatic"], "Polytropic",
                      {"spec": ["Outlet pressure", "Pressure ratio"]}),
                   _f("eff", "Efficiency", "%", 75.0, {"spec": ["Outlet pressure", "Pressure ratio"]},
                      minv=1.0, maxv=100.0),
                   _f("speed", "Speed", "rpm", 10000.0, {"spec": "Performance curve"}, minv=1.0,
                      help="Fan laws scale the design-speed curve: Q ~ N, head ~ N²"),
                   _f("N_design", "Curve (design) speed", "rpm", 10000.0, {"spec": "Performance curve"}, minv=1.0),
                   _s("antisurge", "Anti-surge control", ["Off", "On"], "Off",
                      help="Needs a performance curve. Recycles cooled discharge gas to suction so the machine "
                           "flow stays at least the minimum surge margin right of the surge line"),
                   _f("sm_min", "Minimum surge margin (control line)", "%", 10.0, {"antisurge": "On"},
                      minv=0.0, maxv=100.0)],
    },
    "expander": {
        "label": "Expander", "prefix": "EX", "category": "Rotating",
        "ports": {"in": {"in": {"multi": False}}, "out": {"out": {"multi": False}}},
        "params": [_f("P_out", "Outlet pressure", "bar(a)", 20.0),
                   _f("eff", "Adiabatic efficiency", "%", 80.0, minv=1.0, maxv=100.0)],
    },
    "pump": {
        "label": "Pump", "prefix": "P", "category": "Rotating",
        "ports": {"in": {"in": {"multi": False}}, "out": {"out": {"multi": False}}},
        "params": [_s("spec", "Specification", ["Outlet pressure", "Pressure rise"], "Outlet pressure"),
                   _f("P_out", "Outlet pressure", "bar(a)", 50.0, {"spec": "Outlet pressure"}),
                   _f("dP", "Pressure rise", "bar", 20.0, {"spec": "Pressure rise"}),
                   _f("eff", "Adiabatic efficiency", "%", 75.0, minv=1.0, maxv=100.0)],
    },
    "heater": {
        "label": "Heater", "prefix": "E", "category": "Heat transfer",
        "ports": {"in": {"in": {"multi": False}}, "out": {"out": {"multi": False}}},
        "params": [_s("spec", "Specification", ["Outlet temperature", "Duty", "Outlet vapour fraction"],
                      "Outlet temperature"),
                   _f("T_out", "Outlet temperature", "°C", 80.0, {"spec": "Outlet temperature"}),
                   _f("duty", "Duty", "kW", 1000.0, {"spec": "Duty"}),
                   _f("VF_out", "Outlet vapour fraction", "-", 1.0, {"spec": "Outlet vapour fraction"}, 0.0, 1.0),
                   _f("dP", "Pressure drop", "bar", 0.5)],
    },
    "cooler": {
        "label": "Cooler", "prefix": "E", "category": "Heat transfer",
        "ports": {"in": {"in": {"multi": False}}, "out": {"out": {"multi": False}}},
        "params": [_s("spec", "Specification", ["Outlet temperature", "Duty", "Outlet vapour fraction"],
                      "Outlet temperature"),
                   _f("T_out", "Outlet temperature", "°C", 30.0, {"spec": "Outlet temperature"}),
                   _f("duty", "Duty removed", "kW", 1000.0, {"spec": "Duty"}),
                   _f("VF_out", "Outlet vapour fraction", "-", 0.0, {"spec": "Outlet vapour fraction"}, 0.0, 1.0),
                   _f("dP", "Pressure drop", "bar", 0.5)],
    },
    "hx": {
        "label": "Heat exchanger (shell & tube)", "prefix": "E", "category": "Heat transfer",
        "ports": {"in": {"tube_in": {"multi": False}, "shell_in": {"multi": False}},
                  "out": {"tube_out": {"multi": False}, "shell_out": {"multi": False}}},
        "params": [_s("spec", "Specification",
                      ["Tube outlet temperature", "Shell outlet temperature", "Duty", "Minimum approach", "UA"],
                      "Tube outlet temperature"),
                   _f("T_spec", "Outlet temperature", "°C", 40.0,
                      {"spec": ["Tube outlet temperature", "Shell outlet temperature"]}),
                   _f("duty", "Duty", "kW", 1000.0, {"spec": "Duty"}),
                   _f("dTmin", "Minimum approach", "°C", 5.0, {"spec": "Minimum approach"}, minv=0.01),
                   _f("UA", "Overall UA", "kW/°C", 100.0, {"spec": "UA"}, minv=1e-6,
                      help="Rating mode: the duty is found so that the zoned heat curve gives this UA"),
                   _f("dP_tube", "Tube-side pressure drop", "bar", 0.5),
                   _f("dP_shell", "Shell-side pressure drop", "bar", 0.5)],
    },
    "aircooler": {
        "label": "Air cooler", "prefix": "AC", "category": "Heat transfer",
        "ports": {"in": {"in": {"multi": False}}, "out": {"out": {"multi": False}}},
        "params": [_f("T_out", "Process outlet temperature", "°C", 40.0),
                   _f("dP", "Pressure drop", "bar", 0.5),
                   _f("air_T", "Air inlet temperature", "°C", 15.0),
                   _f("air_dT", "Air temperature rise", "°C", 15.0, minv=0.5),
                   _f("fan_dP", "Fan static pressure", "Pa", 150.0),
                   _f("fan_eff", "Fan efficiency", "%", 65.0, minv=1.0, maxv=100.0)],
    },
    "pipe": {
        "label": "Pipe segment", "prefix": "PIPE", "category": "Piping",
        "ports": {"in": {"in": {"multi": False}}, "out": {"out": {"multi": False}}},
        "params": [_s("method", "Correlation", ["Beggs & Brill", "Homogeneous (no-slip)"], "Beggs & Brill"),
                   _f("length", "Length", "m", 1000.0, minv=0.1),
                   _f("ID", "Inner diameter", "mm", 254.0, minv=1.0),
                   _f("rough", "Absolute roughness", "mm", 0.045, minv=0.0),
                   _f("dz", "Elevation change (outlet - inlet)", "m", 0.0,
                      help="Positive = uphill. |dz| must not exceed the length"),
                   _f("n_seg", "Calculation increments", "-", 10, minv=1, maxv=200),
                   _s("heat", "Heat transfer", ["Adiabatic", "Overall U to ambient"], "Adiabatic"),
                   _f("U", "Overall heat-transfer coefficient", "W/m²·K", 5.0, {"heat": "Overall U to ambient"}, minv=0.0),
                   _f("T_amb", "Ambient temperature", "°C", 4.0, {"heat": "Overall U to ambient"}),
                   _f("sigma", "Gas-liquid surface tension", "N/m", 0.02, {"method": "Beggs & Brill"}, minv=1e-4)],
    },
    "column": {
        "label": "Column (absorber / stabiliser / distillation)", "prefix": "T", "category": "Separation",
        "ports": {"in": {"feed_top": {"multi": False, "optional": True}, "feed": {"multi": True, "optional": True},
                         "feed_bottom": {"multi": False, "optional": True}},
                  "out": {"overhead": {"multi": False}, "bottoms": {"multi": False},
                          "water": {"multi": False, "optional": True}}},
        "params": [_f("n_trays", "Number of trays (theoretical)", "-", 10, minv=1, maxv=60),
                   _f("feed_stage", "Feed tray for the 'feed' port (from top)", "-", 5, minv=1, maxv=60),
                   _f("P_top", "Top pressure", "bar(a)", 10.0, minv=0.05),
                   _f("P_bot", "Bottom pressure", "bar(a)", 10.3, minv=0.05),
                   _s("condenser", "Condenser", ["None", "Partial", "Total"], "None"),
                   _f("RR", "Reflux ratio (L/D)", "-", 2.0, {"condenser": ["Partial", "Total"]}, minv=0.0),
                   _s("reboiler", "Reboiler", ["Yes", "No"], "Yes"),
                   _s("reb_spec", "Second specification",
                      ["Reboiler duty", "Bottoms rate", "Boil-up ratio", "Reboiler temperature", "Distillate rate"],
                      "Reboiler temperature", {"reboiler": "Yes"}),
                   _f("reb_value", "Specification value", "kW | kmol/h | – | °C | kmol/h", 120.0, {"reboiler": "Yes"},
                      help="Units follow the chosen specification: duty kW, bottoms/distillate kmol/h, "
                           "boil-up ratio V/B, reboiler temperature °C"),
                   _s("water_draw", "Free water", [WD_NONE, WD_FEED, WD_BOTH], WD_NONE,
                      help="The stage model is VLE-only. Decant: free water in the feeds is drawn off before the column "
                           "(feed knock-out) and, with a condenser, from the overhead (reflux-drum water boot) to the "
                           "'water' outlet")],
    },
    "recycle": {
        "label": "Recycle", "prefix": "RCY", "category": "Logical",
        "ports": {"in": {"in": {"multi": False}}, "out": {"out": {"multi": False}}},
        "params": [_s("method", "Acceleration", ["Wegstein", "Direct substitution"], "Wegstein"),
                   _f("tol", "Relative flow tolerance", "-", 1e-4, minv=1e-9),
                   _f("tol_T", "Temperature tolerance", "°C", 0.01, minv=1e-6),
                   _f("max_iter", "Maximum iterations", "-", 60, minv=1)],
    },
    "adjust": {
        "label": "Adjust", "prefix": "ADJ", "category": "Logical",
        "ports": {"in": {}, "out": {}},
        "params": [],   # edited through a dedicated panel (variable / target pickers)
    },
}


def default_params(utype):
    p = {}
    for s in CATALOGUE[utype]["params"]:
        p[s["key"]] = s["default"]
    if utype == "feed":
        p["composition"] = {}
    if utype == "adjust":
        p.update({"var_unit": "", "var_param": "", "tgt_kind": "stream", "tgt_obj": "",
                  "tgt_prop": "Temperature [°C]", "tgt_value": 0.0, "var_min": 0.0, "var_max": 100.0,
                  "tol": 1e-3, "max_iter": 40, "active": True})
    return p


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _live(streams):
    return [s for s in streams if s is not None and not s.empty]


def _mix(streams, fp: FluidPackage):
    """Total molar flow, composition, enthalpy flow (J/mol * kmol/h), min pressure, T guess."""
    live = _live(streams)
    if not live:
        return 0.0, None, 0.0, None, None
    nflow = sum(s.F * s.z for s in live)
    F = float(nflow.sum())
    Hflow = sum(s.F * s.H for s in live)
    P = min(s.P for s in live)
    Tg = sum(s.F * s.T for s in live) / F
    return F, nflow / F, Hflow, P, Tg


def _one(ins, port):
    lst = ins.get(port) or []
    if not lst:
        raise UnitError(f"Inlet '{port}' is not connected")
    return lst[0]


def _outs_zero(unit, ins_z, fp, ports, T=None, P=None):
    return {p: [zero_stream("", fp, ins_z, T, P)] for p in ports}


def _ph(fp, z, P, H, T0, K0=None):
    return fp.ph_flash(z, P, H, T0, K0)


def _check_P(P, where):
    if P is None or P <= 0.0:
        raise UnitError(f"{where}: pressure must be positive (got {P})")


def energy_name(unit, kind="heat"):
    return unit.get("energy_name") or (("W-" if kind == "work" else "Q-") + unit["name"])


def _energy(unit, duty_kW, kind="heat"):
    return EnergyStream(energy_name(unit, kind), duty_kW, unit["name"], kind)


# --------------------------------------------------------------------------
# Unit calculations
# --------------------------------------------------------------------------

def calc_valve(unit, ins, fp):
    p = unit["params"]
    s = _one(ins, "in")
    if s.empty:
        return {"out": [zero_stream("", fp, s.z, s.T, s.P)]}, {"Status": "No flow"}, []
    P2 = p["P_out"] if p.get("spec", "Outlet pressure") == "Outlet pressure" else s.P - p["dP"]
    _check_P(P2, unit["name"])
    if P2 > s.P + 1e-9:
        raise UnitError(f"{unit['name']}: outlet pressure {P2:.2f} bar is above inlet {s.P:.2f} bar")
    fr = _ph(fp, s.z, P2, s.H, s.T)
    out = make_stream("", fp, s.F, s.z, fr)
    res = {"Inlet T [°C]": s.T - K0, "Outlet T [°C]": fr.T - K0,
           "ΔP [bar]": s.P - P2, "ΔT (JT) [°C]": fr.T - s.T,
           "Outlet vapour fraction": fr.vf}
    from .process_units import valve_sizing
    res.update(valve_sizing(unit, s, fr, P2))
    return {"out": [out]}, res, []


def calc_mixer(unit, ins, fp):
    p = unit["params"]
    streams = ins.get("in") or []
    F, z, Hf, P, Tg = _mix(streams, fp)
    if F <= 0:
        zz = streams[0].z if streams else None
        return {"out": [zero_stream("", fp, zz)]}, {"Status": "No flow"}, []
    if p.get("pressure") == "Specified":
        P = p["P_out"]
    _check_P(P, unit["name"])
    fr = _ph(fp, z, P, Hf / F, Tg)
    warn = ""
    live = _live(streams)
    if len({round(s.P, 4) for s in live}) > 1 and p.get("pressure") != "Specified":
        warn = "Inlet pressures differ - outlet at the lowest inlet pressure"
    res = {"Outlet T [°C]": fr.T - K0, "Outlet P [bar(a)]": P, "Outlet vapour fraction": fr.vf,
           "Inlets": len(live)}
    if warn:
        res["Note"] = warn
    return {"out": [make_stream("", fp, F, z, fr)]}, res, []


def parse_fractions(txt, n):
    vals = []
    for t in str(txt).replace(";", ",").split(","):
        t = t.strip()
        if t:
            vals.append(float(t))
    vals = vals[: max(n - 1, 0)]
    while len(vals) < n - 1:
        vals.append(0.0)
    if any(v < 0 for v in vals) or sum(vals) > 1 + 1e-12:
        raise UnitError("Split fractions must be non-negative and sum to at most 1")
    vals.append(max(0.0, 1.0 - sum(vals)))
    return vals


def calc_splitter(unit, ins, fp, n_out=1):
    s = _one(ins, "in")
    fr_list = parse_fractions(unit["params"].get("fractions", "0.5"), n_out)
    outs = []
    for f in fr_list:
        if s.empty or f <= 0:
            outs.append(zero_stream("", fp, s.z, s.T, s.P))
        else:
            outs.append(make_stream("", fp, s.F * f, s.z, s.flash))
    res = {f"Fraction to outlet {i + 1}": f for i, f in enumerate(fr_list)}
    return {"out": outs}, res, []


def _separate(unit, ins, fp, three):
    p = unit["params"]
    streams = ins.get("feed") or []
    F, z, Hf, P, Tg = _mix(streams, fp)
    ports = ["vapour", "oil", "water"] if three else ["vapour", "liquid"]
    if F <= 0:
        zz = streams[0].z if streams else None
        return _outs_zero(unit, zz, fp, ports), {"Status": "No flow"}, []
    P = P - p.get("dP", 0.0)
    _check_P(P, unit["name"])
    duty = p.get("duty", 0.0)
    H = Hf / F + duty * 3600.0 / F
    fr = _ph(fp, z, P, H, Tg)
    mix = make_stream("", fp, F, z, fr)
    outs = {"vapour": [phase_stream(mix, fp, ("V",), "")]}
    if three:
        outs["oil"] = [phase_stream(mix, fp, ("L",), "")]
        outs["water"] = [phase_stream(mix, fp, ("W",), "")]
    else:
        outs["liquid"] = [phase_stream(mix, fp, ("L", "W"), "")]
    res = {"Vessel T [°C]": fr.T - K0, "Vessel P [bar(a)]": P, "Vapour fraction": fr.vf,
           "Phases": fr.phase_label}
    if unit.get("type") == "scrubber":
        unit["_mix"] = mix
    if unit.get("type") in ("separator", "separator3"):
        # indicative gas-handling check for a vertical vessel with a mesh pad
        sz = souders_brown(fp, mix)
        for k in ("Souders-Brown K-factor [m/s]", "Max gas velocity [m/s]", "Required diameter [mm]",
                  "Actual gas flow [m³/h]", "Liquid flow [m³/h]"):
            if k in sz:
                res[k] = sz[k]
    for k, v in outs.items():
        res[f"{k.capitalize()} flow [kg/h]"] = v[0].F * v[0].MW
    en = [_energy(unit, duty)] if abs(duty) > 0 else []
    if not three and fr.phase("W") is not None and fr.phase("L") is not None:
        res["Note"] = "Two liquid phases present - consider a 3-phase separator"
    return outs, res, en


K_DEFAULT = {"Mesh pad": 0.107, "Vane pack": 0.15, "Axial cyclones": 0.25, "No internals": 0.05}


def mesh_pressure_factor(P_bar):
    """GPSA K-factor pressure correction for wire-mesh pads (1.0 up to ~1 barg, 0.75 at ~80 barg)."""
    pg = max(P_bar - 1.01325, 0.0) * 14.5038       # psig
    pts = [(0, 1.0), (15, 1.0), (40, 0.98), (150, 0.90), (300, 0.85), (600, 0.80), (1150, 0.75)]
    if pg >= pts[-1][0]:
        return pts[-1][1]
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        if x1 <= pg <= x2:
            return y1 + (y2 - y1) * (pg - x1) / (x2 - x1)
    return 1.0


def souders_brown(fp, mix, internal="Mesh pad", K=0.0, ID_mm=0.0, margin=20.0):
    """Vertical gas-liquid separation check. Returns an ordered dict of sizing results."""
    fr = mix.flash
    v = fr.phase("V")
    liqs = [ph for ph in fr.phases if ph.kind != "V"]
    out = {}
    if v is None:
        out["Sizing note"] = "No gas phase"
        return out
    n = mix.F * 1000.0 / 3600.0
    Qg = n * v.beta * v.Vs                                  # m3/s
    rho_g = v.rho
    if liqs:
        ql = sum(n * ph.beta * ph.Vs for ph in liqs)
        rho_l = sum(n * ph.beta * ph.MW / 1000.0 for ph in liqs) / ql
    else:
        ql, rho_l = 0.0, 800.0                               # dry gas: size on a nominal condensate
    if K <= 0:
        K = K_DEFAULT.get(internal, 0.107)
        if internal == "Mesh pad":
            K *= mesh_pressure_factor(mix.P)
    vmax = K * math.sqrt(max(rho_l - rho_g, 1e-6) / rho_g)
    A_req = Qg * (1.0 + margin / 100.0) / vmax
    D_req = math.sqrt(4.0 * A_req / math.pi) * 1000.0
    D_sel = ID_mm if ID_mm > 0 else math.ceil(D_req / 50.0) * 50.0
    v_act = Qg / (math.pi * (D_sel / 1000.0) ** 2 / 4.0)
    out.update({"Souders-Brown K-factor [m/s]": K, "Max gas velocity [m/s]": vmax,
                "Actual gas velocity [m/s]": v_act, "Gas load [% of max]": 100.0 * v_act / vmax,
                "Required diameter [mm]": D_req, "Selected diameter [mm]": D_sel,
                "Actual gas flow [m³/h]": Qg * 3600.0, "Liquid flow [m³/h]": ql * 3600.0,
                "Gas density [kg/m³]": rho_g, "Liquid density [kg/m³]": rho_l})
    return out


def calc_separator(unit, ins, fp):
    return _separate(unit, ins, fp, False)


def calc_scrubber(unit, ins, fp):
    p = unit["params"]
    outs, res, en = _separate(unit, ins, fp, False)
    if res.get("Status") == "No flow":
        return outs, res, en
    mix = unit.pop("_mix")
    sz = souders_brown(fp, mix, p.get("internal", "Mesh pad"), float(p.get("K", 0.0)),
                       float(p.get("ID", 0.0)), float(p.get("margin", 20.0)))
    res.update(sz)
    res.pop("Note", None)
    load = sz.get("Gas load [% of max]")
    if load is not None and load > 100.0:
        res["Warning"] = (f"Gas load {load:.0f} % of the Souders-Brown limit - liquid carry-over expected "
                          f"(needs ≥ {sz['Required diameter [mm]']:.0f} mm)")
    return outs, res, en


def calc_separator3(unit, ins, fp):
    return _separate(unit, ins, fp, True)


def _vapour_volume(fr):
    """Molar volume of the (mostly vapour) stream m3/mol."""
    return sum(ph.beta * ph.Vs for ph in fr.phases)


# ---- compressor performance curves -------------------------------------------

TYPICAL_CURVE = {   # centrifugal stage, relative to the design point (flow ratio, head ratio, efficiency ratio)
    "r": [0.60, 0.70, 0.80, 0.90, 1.00, 1.10, 1.20, 1.30],
    "h": [1.10, 1.09, 1.07, 1.04, 1.00, 0.94, 0.85, 0.72],
    "e": [0.90, 0.95, 0.98, 0.995, 1.00, 0.99, 0.95, 0.87],
}


def typical_curve(Q_design, head_design, eff_design):
    """A realistic single-speed centrifugal curve passing through (Q, head, eff) at its best-efficiency point."""
    t = TYPICAL_CURVE
    return {"flow": [round(Q_design * r, 3) for r in t["r"]],
            "head": [round(head_design * h, 3) for h in t["h"]],
            "eff": [round(min(eff_design * e, 99.0), 3) for e in t["e"]]}


class CompressorCurve:
    """Head [kJ/kg] and polytropic efficiency [%] vs actual inlet flow [m3/h] at the design speed.

    Monotone cubic (PCHIP) interpolation; linear extrapolation beyond the ends.  The first point is the
    surge point and the last the stonewall (choke) point at design speed; other speeds follow the fan laws
    (Q ~ N, head ~ N^2, efficiency unchanged at equivalent flow)."""

    def __init__(self, curve, N_design):
        q = np.asarray(curve.get("flow", []), float)
        h = np.asarray(curve.get("head", []), float)
        e = np.asarray(curve.get("eff", []), float)
        ok = np.isfinite(q) & np.isfinite(h) & np.isfinite(e)
        q, h, e = q[ok], h[ok], e[ok]
        if q.size < 3:
            raise UnitError("Performance curve needs at least 3 points (flow, head, efficiency)")
        order = np.argsort(q)
        q, h, e = q[order], h[order], e[order]
        if np.any(np.diff(q) <= 0):
            raise UnitError("Performance curve flows must be distinct")
        if np.any(h <= 0) or np.any(e <= 0) or np.any(e > 100):
            raise UnitError("Performance curve heads must be > 0 and efficiencies in (0, 100] %")
        from scipy.interpolate import PchipInterpolator
        self.q, self.h, self.e, self.N0 = q, h, e, float(N_design)
        self._h = PchipInterpolator(q, h, extrapolate=False)
        self._e = PchipInterpolator(q, e, extrapolate=False)

    def _eval(self, f, arr, Qe):
        if Qe < self.q[0]:
            s = (arr[1] - arr[0]) / (self.q[1] - self.q[0])
            return float(arr[0] + s * (Qe - self.q[0]))
        if Qe > self.q[-1]:
            s = (arr[-1] - arr[-2]) / (self.q[-1] - self.q[-2])
            return float(arr[-1] + s * (Qe - self.q[-1]))
        return float(f(Qe))

    def at(self, Q, N):
        """(head kJ/kg, efficiency %, equivalent design-speed flow) at actual flow Q and speed N."""
        r = N / self.N0
        Qe = Q / r
        head = self._eval(self._h, self.h, Qe) * r * r
        eff = self._eval(self._e, self.e, Qe)
        return head, max(eff, 1.0), Qe

    def surge_flow(self, N):
        return self.q[0] * N / self.N0

    def stonewall_flow(self, N):
        return self.q[-1] * N / self.N0


def _poly_path(fp, s, P2, eta):
    """Polytropic compression outlet: stepwise path at constant polytropic efficiency, 4/8-step Richardson."""
    z = s.z

    def stepwise(nstep):
        Ps = np.geomspace(s.P, P2, nstep + 1)
        frk = s.flash
        for k in range(nstep):
            fsk = fp.ps_flash(z, Ps[k + 1], frk.S, frk.T * (Ps[k + 1] / Ps[k]) ** 0.25)
            Hk = frk.H + (fsk.H - frk.H) / eta
            frk = _ph(fp, z, Ps[k + 1], Hk, fsk.T)
        return frk
    f4, f8 = stepwise(4), stepwise(8)
    H2 = 2.0 * f8.H - f4.H
    return _ph(fp, z, P2, H2, f8.T)


def _curve_outlet_pressure(fp, s, head, eta, name):
    """Outlet pressure at which the polytropic head (path at eta) equals `head` [kJ/kg]."""
    MW = s.MW
    dH_target = head * MW / eta                    # J/mol
    v = s.flash.phase("V") or s.flash.phases[0]
    k = v.Cp / v.Cv if v.Cv > 0 else 1.3
    Z = v.Z
    sigma = (k - 1.0) / (k * eta)
    RT = Z * R * s.T / MW                           # J/g
    r0 = (1.0 + head * sigma / RT) ** (1.0 / sigma)
    x1, x2 = math.log(s.P * r0), math.log(s.P * r0 * 1.03)

    def f(lnP):
        return _poly_path(fp, s, math.exp(lnP), eta).H - s.H - dH_target

    f1, f2 = f(x1), f(x2)
    for _ in range(12):
        if abs(f2) < 1e-4 * dH_target:
            return math.exp(x2)
        if f2 == f1:
            break
        x3 = x2 - f2 * (x2 - x1) / (f2 - f1)
        x3 = min(max(x3, math.log(s.P * 1.0001)), math.log(s.P * 200.0))
        x1, f1, x2, f2 = x2, f2, x3, f(x3)
    if abs(f2) < 1e-3 * dH_target:
        return math.exp(x2)
    raise UnitError(f"{name}: could not match the curve head ({head:.1f} kJ/kg)")


def calc_compressor(unit, ins, fp):
    p = unit["params"]
    s = _one(ins, "in")
    if s.empty:
        return {"out": [zero_stream("", fp, s.z, s.T, s.P)]}, {"Status": "No flow"}, []
    spec = p.get("spec", "Outlet pressure")
    z = s.z
    v1 = _vapour_volume(s.flash)
    Q_act = s.F * 1000 * v1                  # m3/h actual inlet
    curve = None
    if p.get("curve"):
        try:
            curve = CompressorCurve(p["curve"], p.get("N_design", 10000.0))
        except UnitError:
            if spec == "Performance curve":
                raise
            curve = None
    curve_info = {}
    asc = p.get("antisurge", "Off") == "On" and curve is not None
    sm_min = float(p.get("sm_min", 10.0))
    Q_m = Q_act                               # machine inlet flow (process + anti-surge recycle)
    if spec == "Performance curve":
        if curve is None:
            raise UnitError(f"{unit['name']}: no performance curve defined (Design tab → Performance curve)")
        N = float(p.get("speed", curve.N0))
        if asc:
            Q_m = max(Q_act, curve.surge_flow(N) * (1.0 + sm_min / 100.0))
        head_c, eff_c, Qe = curve.at(Q_m, N)
        eta = eff_c / 100.0
        P2 = _curve_outlet_pressure(fp, s, head_c, eta, unit["name"])
        curve_info = {"Speed [rpm]": N, "Curve head [kJ/kg]": head_c, "Curve efficiency [%]": eff_c}
    else:
        P2 = p["P_out"] if spec == "Outlet pressure" else s.P * p["ratio"]
        eta = p["eff"] / 100.0
    if P2 <= s.P:
        raise UnitError(f"{unit['name']}: outlet pressure must exceed inlet ({s.P:.2f} bar)")
    # isentropic outlet (for adiabatic efficiency / reporting)
    fs = fp.ps_flash(z, P2, s.S, s.T * (P2 / s.P) ** 0.25)
    dHs = fs.H - s.H
    if spec != "Performance curve" and p.get("eff_type", "Polytropic") == "Adiabatic":
        H2 = s.H + dHs / eta
        fr = _ph(fp, z, P2, H2, fs.T)
        eta_ad, eta_p = eta, None
    else:
        fr = _poly_path(fp, s, P2, eta)
        eta_p = eta
        eta_ad = dHs / (fr.H - s.H)
    dH = fr.H - s.H                         # J/mol
    MW = s.MW
    v2 = _vapour_volume(fr)
    n_poly = math.log(P2 / s.P) / math.log(v1 / v2) if v2 < v1 else float("nan")
    head_poly = (eta_p if eta_p else 1.0) * dH / MW   # kJ/kg (J/g)
    warn = []
    if s.flash.vf < 0.9999:
        warn.append(f"Liquid in compressor feed (vapour fraction {s.flash.vf:.4f})")
    N = None
    if curve is not None:
        if spec == "Performance curve":
            N = float(p.get("speed", curve.N0))
        else:
            # fixed-spec mode: the speed whose fan-law curve delivers the head at the machine flow; with
            # anti-surge the machine flow itself depends on that speed (surge flow ~ N) - fixed point
            from scipy.optimize import brentq
            for _ in range(30):
                def g(n):
                    return curve.at(Q_m, n)[0] - head_poly
                try:
                    N = brentq(g, 0.2 * curve.N0, 2.0 * curve.N0, xtol=0.01)
                except ValueError:
                    N = None
                    break
                if not asc:
                    break
                Q_new = max(Q_act, curve.surge_flow(N) * (1.0 + sm_min / 100.0))
                if abs(Q_new - Q_m) < 1e-6 * Q_act:
                    Q_m = Q_new
                    break
                Q_m = Q_new
    F_m = s.F * Q_m / Q_act                   # recycle returns at suction conditions (cooled, let down)
    F_rec = F_m - s.F
    W = F_m * dH / 3600.0                     # kW, whole machine flow
    Q_asc = F_rec * dH / 3600.0               # heat the anti-surge cooler removes
    res = {"Power [kW]": W, "Inlet T [°C]": s.T - K0, "Outlet T [°C]": fr.T - K0,
           "Pressure ratio": P2 / s.P, "Adiabatic efficiency [%]": 100 * eta_ad,
           "Polytropic efficiency [%]": 100 * eta_p if eta_p else None,
           "Polytropic exponent n": n_poly, "Isentropic outlet T [°C]": fs.T - K0,
           "Head [kJ/kg]": head_poly, "Head [m]": head_poly * 1000 / 9.80665,
           "Actual inlet vol flow [m³/h]": Q_act, "Outlet P [bar(a)]": P2}
    res.update(curve_info)
    if curve is not None and spec != "Performance curve" and N:
        res["Speed to meet duty (fan laws) [rpm]"] = N
    if asc or p.get("antisurge", "Off") == "On":
        res["Machine inlet flow [m³/h]"] = Q_m
        res["Anti-surge recycle [kmol/h]"] = F_rec
        res["Anti-surge recycle [% of throughput]"] = 100.0 * F_rec / s.F
        res["Anti-surge cooler duty [kW]"] = Q_asc
        if F_rec > 1e-9:
            res["Note"] = (f"Anti-surge recycle active: {F_rec:,.1f} kmol/h returned to suction "
                           f"({100 * F_rec / s.F:.1f} % of throughput) to hold {sm_min:.0f} % surge margin")
        if curve is None:
            warn.append("Anti-surge control is on but no performance curve is defined")
    if curve is not None:
        if N:
            qs = curve.surge_flow(N)
            res["Surge flow at speed [m³/h]"] = qs
            res["Surge margin [%]"] = (Q_m / qs - 1.0) * 100.0
            res["Stonewall margin [%]"] = (1.0 - Q_m / curve.stonewall_flow(N)) * 100.0
            if Q_m < qs * (1 - 1e-9):
                warn.append(f"Operating point is left of the surge line ({res['Surge margin [%]']:.1f} %) - "
                            "recycle / anti-surge flow needed")
            elif res["Surge margin [%]"] < 10.0 - 1e-6 and not asc:
                warn.append(f"Surge margin only {res['Surge margin [%]']:.1f} % (< 10 %)")
            if Q_m > curve.stonewall_flow(N):
                warn.append("Operating point beyond stonewall (choke) - curve extrapolated")
        unit["_map"] = {"curve": {"flow": curve.q.tolist(), "head": curve.h.tolist(), "eff": curve.e.tolist()},
                        "N0": curve.N0, "N": N, "Q": Q_m, "Q_process": Q_act, "head": head_poly,
                        "eff": 100 * eta_p if eta_p else None,
                        "control_line": (1.0 + sm_min / 100.0) if asc else None}
    from .process_units import driver_results
    dres, dwarn = driver_results(unit, W)
    res.update(dres)
    if dwarn:
        warn.append(dwarn)
    if warn:
        res["Warning"] = "; ".join(warn)
    en = [_energy(unit, W, "work")]
    if F_rec > 1e-9:
        en.append(EnergyStream(f"Q-{unit['name']} anti-surge", -Q_asc, unit["name"], "heat"))
    return {"out": [make_stream("", fp, s.F, z, fr)]}, res, en


def calc_expander(unit, ins, fp):
    p = unit["params"]
    s = _one(ins, "in")
    if s.empty:
        return {"out": [zero_stream("", fp, s.z, s.T, s.P)]}, {"Status": "No flow"}, []
    P2 = p["P_out"]
    if P2 >= s.P:
        raise UnitError(f"{unit['name']}: outlet pressure must be below inlet ({s.P:.2f} bar)")
    eta = p["eff"] / 100.0
    fs = fp.ps_flash(s.z, P2, s.S, s.T * (P2 / s.P) ** 0.25)
    H2 = s.H - eta * (s.H - fs.H)
    fr = _ph(fp, s.z, P2, H2, fs.T)
    W = s.F * (s.H - fr.H) / 3600.0
    res = {"Power produced [kW]": W, "Outlet T [°C]": fr.T - K0, "Isentropic outlet T [°C]": fs.T - K0,
           "Outlet vapour fraction": fr.vf, "Pressure ratio": s.P / P2}
    return {"out": [make_stream("", fp, s.F, s.z, fr)]}, res, [_energy(unit, -W, "work")]


def calc_pump(unit, ins, fp):
    p = unit["params"]
    s = _one(ins, "in")
    if s.empty:
        return {"out": [zero_stream("", fp, s.z, s.T, s.P)]}, {"Status": "No flow"}, []
    P2 = p["P_out"] if p.get("spec", "Outlet pressure") == "Outlet pressure" else s.P + p["dP"]
    if P2 <= s.P:
        raise UnitError(f"{unit['name']}: outlet pressure must exceed inlet ({s.P:.2f} bar)")
    eta = p["eff"] / 100.0
    # Liquid pump as in commercial simulators: ideal work = V dP with the (volume-shifted)
    # inlet liquid volume, which avoids the PR liquid-volume error in the EOS isentrope.
    Vs = _vapour_volume(s.flash)                          # m3/mol, shifted
    Ws = Vs * (P2 - s.P) * 1e5                            # J/mol
    H2 = s.H + Ws / eta
    fr = _ph(fp, s.z, P2, H2, s.T)
    W = s.F * (fr.H - s.H) / 3600.0
    vol = s.F * 1000 * _vapour_volume(s.flash)
    rho = s.F * s.MW / vol if vol > 0 else 0
    res = {"Power [kW]": W, "Outlet T [°C]": fr.T - K0, "Pressure rise [bar]": P2 - s.P,
           "Actual vol flow [m³/h]": vol, "Head [m]": (P2 - s.P) * 1e5 / (rho * 9.80665) if rho else None}
    if s.flash.vf > 1e-6:
        res["Warning"] = f"Vapour in pump feed (vapour fraction {s.flash.vf:.4f})"
    return {"out": [make_stream("", fp, s.F, s.z, fr)]}, res, [_energy(unit, W, "work")]


def _heat(unit, ins, fp, cooler):
    p = unit["params"]
    s = _one(ins, "in")
    if s.empty:
        return {"out": [zero_stream("", fp, s.z, s.T, s.P)]}, {"Status": "No flow"}, []
    P2 = s.P - p.get("dP", 0.0)
    _check_P(P2, unit["name"])
    spec = p.get("spec", "Outlet temperature")
    if spec == "Outlet temperature":
        fr = fp.pt_flash(s.z, p["T_out"] + K0, P2)
    elif spec == "Duty":
        q = -p["duty"] if cooler else p["duty"]
        fr = _ph(fp, s.z, P2, s.H + q * 3600.0 / s.F, s.T)
    else:
        fr = fp.pvf_flash(s.z, P2, p["VF_out"], s.T)
    Q = s.F * (fr.H - s.H) / 3600.0
    if cooler and Q > 1e-6:
        raise UnitError(f"{unit['name']}: specification requires heating ({Q:.1f} kW) - use a heater")
    if not cooler and Q < -1e-6:
        raise UnitError(f"{unit['name']}: specification requires cooling ({-Q:.1f} kW) - use a cooler")
    res = {("Duty removed [kW]" if cooler else "Duty [kW]"): -Q if cooler else Q,
           "Inlet T [°C]": s.T - K0, "Outlet T [°C]": fr.T - K0, "Outlet P [bar(a)]": P2,
           "Outlet vapour fraction": fr.vf}
    return {"out": [make_stream("", fp, s.F, s.z, fr)]}, res, [_energy(unit, Q)]


def calc_heater(unit, ins, fp):
    return _heat(unit, ins, fp, False)


def calc_cooler(unit, ins, fp):
    return _heat(unit, ins, fp, True)


def calc_aircooler(unit, ins, fp):
    p = unit["params"]
    u2 = dict(unit)
    u2["params"] = {"spec": "Outlet temperature", "T_out": p["T_out"], "dP": p.get("dP", 0.0)}
    outs, res, en = _heat(u2, ins, fp, True)
    Q = res.get("Duty removed [kW]", 0.0) or 0.0
    s = _one(ins, "in")
    if not s.empty and p["T_out"] < p["air_T"] + 1e-9:
        raise UnitError(f"{unit['name']}: outlet {p['T_out']} °C is below air inlet {p['air_T']} °C")
    m_air = Q / (1.006 * p["air_dT"])                    # kg/s
    v_air = m_air / 1.225                                 # m3/s at ~15 degC
    fan = v_air * p["fan_dP"] / (p["fan_eff"] / 100.0) / 1000.0  # kW
    res.update({"Air flow [kg/s]": m_air, "Air outlet T [°C]": p["air_T"] + p["air_dT"],
                "Fan power (estimate) [kW]": fan})
    if not s.empty and p["T_out"] < p["air_T"] + p["air_dT"]:
        res["Note"] = "Process outlet colder than air outlet: needs counter-current passes / check approach"
    en = [_energy(unit, -Q)] + ([EnergyStream(f"W-{unit['name']} fan", fan, unit["name"], "work")] if fan else [])
    return outs, res, en


# ---- shell & tube heat exchanger ------------------------------------------

def _hx_profile(fp, hot, cold, Ph_out, Pc_out, Q_kW, n=10):
    """Counter-current T profiles along the duty. Returns (q_kW, Th, Tc) arrays."""
    qs = np.linspace(0.0, Q_kW, n + 1)
    Th, Tc = [], []
    Hh_in = hot.H
    Hc_out = cold.H + Q_kW * 3600.0 / cold.F
    Th_prev, Tc_prev = hot.T, None
    for q in qs:
        f = q / Q_kW if Q_kW > 0 else 0.0
        Ph = hot.P + (Ph_out - hot.P) * f
        Pc = Pc_out + (cold.P - Pc_out) * f
        rh = fp.ph_flash(hot.z, Ph, Hh_in - q * 3600.0 / hot.F, Th_prev)
        rc = fp.ph_flash(cold.z, Pc, Hc_out - q * 3600.0 / cold.F, Tc_prev or cold.T + 10)
        Th_prev, Tc_prev = rh.T, rc.T
        Th.append(rh.T)
        Tc.append(rc.T)
    return qs, np.array(Th), np.array(Tc)


def _hx_ua(qs, Th, Tc):
    ua = 0.0
    for k in range(len(qs) - 1):
        d1 = Th[k] - Tc[k]
        d2 = Th[k + 1] - Tc[k + 1]
        if d1 <= 0 or d2 <= 0:
            return float("nan")
        lm = d1 if abs(d1 - d2) < 1e-9 else (d1 - d2) / math.log(d1 / d2)
        ua += (qs[k + 1] - qs[k]) / lm
    return ua


def calc_hx(unit, ins, fp):
    p = unit["params"]
    t_in, s_in = _one(ins, "tube_in"), _one(ins, "shell_in")
    Pt_out = (t_in.P or 0) - p.get("dP_tube", 0.0)
    Ps_out = (s_in.P or 0) - p.get("dP_shell", 0.0)
    if t_in.empty or s_in.empty:
        outs = {"tube_out": [t_in.copy("") if not t_in.empty else zero_stream("", fp, t_in.z)],
                "shell_out": [s_in.copy("") if not s_in.empty else zero_stream("", fp, s_in.z)]}
        return outs, {"Status": "One side has no flow - no heat transferred"}, []
    _check_P(Pt_out, unit["name"] + " tube side")
    _check_P(Ps_out, unit["name"] + " shell side")
    tube_hot = t_in.T >= s_in.T
    hot, cold = (t_in, s_in) if tube_hot else (s_in, t_in)
    Ph_out, Pc_out = (Pt_out, Ps_out) if tube_hot else (Ps_out, Pt_out)

    def q_hot_to(T):
        return hot.F * (hot.H - fp.pt_flash(hot.z, T, Ph_out).H) / 3600.0

    def q_cold_to(T):
        return cold.F * (fp.pt_flash(cold.z, T, Pc_out).H - cold.H) / 3600.0

    spec = p.get("spec", "Tube outlet temperature")
    if spec in ("Tube outlet temperature", "Shell outlet temperature"):
        T = p["T_spec"] + K0
        on_tube = spec.startswith("Tube")
        side_hot = on_tube == tube_hot
        Q = q_hot_to(T) if side_hot else q_cold_to(T)
    elif spec == "Duty":
        Q = p["duty"]
    else:
        from scipy.optimize import brentq
        Qmax = min(q_hot_to(cold.T), q_cold_to(hot.T))
        if Qmax <= 0:
            raise UnitError(f"{unit['name']}: no heat can be transferred (inlet temperatures)")
        if spec == "Minimum approach":
            dT = p["dTmin"]

            def g(Q):
                qs, Th, Tc = _hx_profile(fp, hot, cold, Ph_out, Pc_out, Q, 8)
                return float(np.min(Th - Tc)) - dT
        else:   # UA rating: UA grows monotonically with duty
            ua_t = p["UA"]

            def g(Q):
                qs, Th, Tc = _hx_profile(fp, hot, cold, Ph_out, Pc_out, Q, 8)
                ua = _hx_ua(qs, Th, Tc)
                return -1e12 if ua != ua else ua_t - ua    # NaN = temperature cross: UA -> infinity
        if g(Qmax * 0.999) >= 0:
            Q = Qmax * 0.999
        else:
            Q = brentq(g, 1e-6 * Qmax, Qmax * 0.999, xtol=1e-5 * Qmax)
    if Q < -1e-9:
        raise UnitError(f"{unit['name']}: specification implies heat flowing from cold to hot side")
    fh = _ph(fp, hot.z, Ph_out, hot.H - Q * 3600.0 / hot.F, hot.T)
    fc = _ph(fp, cold.z, Pc_out, cold.H + Q * 3600.0 / cold.F, cold.T)
    h_out = make_stream("", fp, hot.F, hot.z, fh)
    c_out = make_stream("", fp, cold.F, cold.z, fc)
    outs = {"tube_out": [h_out if tube_hot else c_out], "shell_out": [c_out if tube_hot else h_out]}
    d1 = hot.T - fc.T
    d2 = fh.T - cold.T
    res = {"Duty [kW]": Q, "Hot side": "Tube" if tube_hot else "Shell",
           "Hot inlet T [°C]": hot.T - K0, "Hot outlet T [°C]": fh.T - K0,
           "Cold inlet T [°C]": cold.T - K0, "Cold outlet T [°C]": fc.T - K0,
           "Terminal approach hot end [°C]": d1, "Terminal approach cold end [°C]": d2}
    if d1 > 0 and d2 > 0:
        lmtd = d1 if abs(d1 - d2) < 1e-9 else (d1 - d2) / math.log(d1 / d2)
        res["LMTD (terminal) [°C]"] = lmtd
    else:
        res["Warning"] = "Temperature cross - infeasible counter-current exchange"
    unit["_post"] = {"hot": hot, "cold": cold, "Ph_out": Ph_out, "Pc_out": Pc_out, "Q": Q,
                     "tube_hot": tube_hot}
    return outs, res, []


def hx_post(unit, fp, n=12):
    """Heat curve + min approach + UA (called once after convergence)."""
    d = unit.get("_post")
    if not d or d["Q"] <= 0:
        return None
    qs, Th, Tc = _hx_profile(fp, d["hot"], d["cold"], d["Ph_out"], d["Pc_out"], d["Q"], n)
    return {"q": qs.tolist(), "Th": (Th - K0).tolist(), "Tc": (Tc - K0).tolist(),
            "min_approach": float(np.min(Th - Tc)), "UA": _hx_ua(qs, Th, Tc) if np.all(Th > Tc) else None,
            "tube_hot": d["tube_hot"]}


# ---- pipe segment ---------------------------------------------------------

G = 9.80665


def churchill_f(Re, rel_rough):
    """Darcy friction factor, Churchill (1977) - laminar, transition and turbulent."""
    Re = max(Re, 1e-3)
    A = (2.457 * math.log(1.0 / ((7.0 / Re) ** 0.9 + 0.27 * rel_rough))) ** 16
    B = (37530.0 / Re) ** 16
    return 8.0 * ((8.0 / Re) ** 12 + 1.0 / (A + B) ** 1.5) ** (1.0 / 12.0)


def _phase_split(fp, st):
    """Gas and (combined) liquid volumetric flows m3/s, densities, viscosities (Pa.s)."""
    from .transport import phase_viscosity_cP
    qg = mg = 0.0
    mu_g = 1e-5
    ql = ml = 0.0
    mu_l_num = 0.0
    for ph in st.flash.phases:
        n = st.F * ph.beta * 1000.0 / 3600.0          # mol/s
        q = n * ph.Vs
        m = n * ph.MW / 1000.0
        mu = phase_viscosity_cP(fp, ph, st.T) * 1e-3
        if ph.kind == "V":
            qg, mg, mu_g = qg + q, mg + m, mu
        else:
            ql, ml = ql + q, ml + m
            mu_l_num += q * mu
    rho_g = mg / qg if qg > 0 else 1.0
    rho_l = ml / ql if ql > 0 else 800.0
    mu_l = mu_l_num / ql if ql > 0 else 1e-3
    return qg, ql, rho_g, rho_l, mu_g, mu_l


def beggs_brill_holdup(lam, NFr, NLv, theta):
    """Beggs & Brill (1973) liquid holdup with Payne et al. (1979) corrections. Returns (HL, regime)."""
    lam = min(max(lam, 1e-9), 1.0)
    L1 = 316.0 * lam ** 0.302
    L2 = 0.0009252 * lam ** -2.4684
    L3 = 0.10 * lam ** -1.4516
    L4 = 0.5 * lam ** -6.738
    if (lam < 0.01 and NFr < L1) or (lam >= 0.01 and NFr < L2):
        regime = "Segregated"
    elif lam >= 0.01 and L2 <= NFr <= L3:
        regime = "Transition"
    elif (0.01 <= lam < 0.4 and L3 < NFr <= L1) or (lam >= 0.4 and L3 < NFr <= L4):
        regime = "Intermittent"
    else:
        regime = "Distributed"
    coef = {"Segregated": (0.98, 0.4846, 0.0868), "Intermittent": (0.845, 0.5351, 0.0173),
            "Distributed": (1.065, 0.5824, 0.0609)}
    uphill = {"Segregated": (0.011, -3.768, 3.539, -1.614), "Intermittent": (2.96, 0.305, -0.4473, 0.0978)}

    def hl(reg):
        a, b, c = coef[reg]
        h0 = max(a * lam ** b / max(NFr, 1e-12) ** c, lam)
        if abs(theta) < 1e-9:
            return h0
        if theta > 0:
            if reg == "Distributed":
                C = 0.0
            else:
                e, f, g, h = uphill[reg]
                arg = e * lam ** f * max(NLv, 1e-12) ** g * max(NFr, 1e-12) ** h
                C = max(0.0, (1 - lam) * math.log(arg)) if arg > 0 else 0.0
        else:
            arg = 4.70 * lam ** -0.3692 * max(NLv, 1e-12) ** 0.1244 * max(NFr, 1e-12) ** -0.5056
            C = max(0.0, (1 - lam) * math.log(arg)) if arg > 0 else 0.0
        psi = 1.0 + C * (math.sin(1.8 * theta) - math.sin(1.8 * theta) ** 3 / 3.0)
        H = h0 * psi
        H *= 0.924 if theta > 0 else 0.685          # Payne et al. corrections
        return min(max(H, 1e-6), 1.0)

    if regime == "Transition":
        A = (L3 - NFr) / (L3 - L2)
        H = A * hl("Segregated") + (1 - A) * hl("Intermittent")
    else:
        H = hl(regime)
    return H, regime


def pipe_gradient(fp, st, D, eps, theta, method, sigma):
    """Pressure gradient (Pa/m, positive = pressure loss) and flow details at state st."""
    A = math.pi * D * D / 4.0
    qg, ql, rho_g, rho_l, mu_g, mu_l = _phase_split(fp, st)
    vsg, vsl = qg / A, ql / A
    vm = vsg + vsl
    lam = vsl / vm if vm > 0 else 0.0
    rho_ns = rho_l * lam + rho_g * (1 - lam)
    mu_ns = mu_l * lam + mu_g * (1 - lam)
    Re = rho_ns * vm * D / mu_ns if mu_ns > 0 else 1e6
    fn = churchill_f(Re, eps / D)
    two_phase = 1e-6 < lam < 1 - 1e-6
    if not two_phase or method.startswith("Homogeneous"):
        HL, regime = (lam, "Homogeneous" if two_phase else ("Single-phase gas" if lam <= 1e-6 else "Single-phase liquid"))
        ftp = fn
    else:
        NFr = vm * vm / (G * D)
        NLv = vsl * (rho_l / (G * sigma)) ** 0.25
        HL, regime = beggs_brill_holdup(lam, NFr, NLv, theta)
        y = lam / (HL * HL)
        if 1.0 < y < 1.2:
            S = math.log(2.2 * y - 1.2)
        else:
            ly = math.log(y)
            S = ly / (-0.0523 + 3.182 * ly - 0.8725 * ly ** 2 + 0.01853 * ly ** 4)
        ftp = fn * math.exp(S)
    rho_s = rho_l * HL + rho_g * (1 - HL)
    dpf = ftp * rho_ns * vm * vm / (2 * D)
    dpe = rho_s * G * math.sin(theta)
    return dpf + dpe, {"vm": vm, "vsg": vsg, "vsl": vsl, "HL": HL, "lambda": lam, "regime": regime,
                       "Re": Re, "rho_ns": rho_ns, "dpf": dpf, "dpe": dpe}


def calc_pipe(unit, ins, fp):
    """Pipe segment (Beggs & Brill or homogeneous), marched in increments with a Heun predictor-corrector.

    Optional private parameters used by the subsea (SURF) units that build on this model:
    ``T_amb_out`` - ambient temperature at the outlet (linear ambient profile, e.g. a geothermal gradient);
    ``q_in_W_m`` - heat input per metre (direct electrical heating); ``z0``/``L0`` - elevation and
    distance at the inlet, for multi-section profiles; ``T_hold`` [°C] with ``q_max_W_m`` - controlled heating
    that keeps the fluid at or above T_hold, limited to q_max per metre (the heat needed is reported per
    increment in the profile, ``q_heat`` [W/m])."""
    p = unit["params"]
    s = _one(ins, "in")
    if s.empty:
        return {"out": [zero_stream("", fp, s.z, s.T, s.P)]}, {"Status": "No flow"}, []
    L = float(p["length"])
    D = float(p["ID"]) / 1000.0
    eps = float(p["rough"]) / 1000.0
    dz = float(p.get("dz", 0.0))
    if abs(dz) > L:
        raise UnitError(f"{unit['name']}: elevation change {dz} m exceeds the length {L} m")
    theta = math.asin(dz / L)
    n = max(1, int(round(p.get("n_seg", 10))))
    dL = L / n
    U = float(p.get("U", 0.0)) if p.get("heat") == "Overall U to ambient" else 0.0
    Tamb_in = float(p.get("T_amb", 4.0)) + K0
    Tamb_out = float(p.get("T_amb_out", p.get("T_amb", 4.0))) + K0
    q_in = float(p.get("q_in_W_m", 0.0) or 0.0)            # W/m heat input (DEH)
    z0, L0 = float(p.get("z0", 0.0)), float(p.get("L0", 0.0))
    T_hold = (float(p["T_hold"]) + K0) if p.get("T_hold") is not None else None
    q_max = float(p.get("q_max_W_m", 0.0) or 0.0)
    method = p.get("method", "Beggs & Brill")
    sigma = float(p.get("sigma", 0.02))
    n_mol = s.F * 1000.0 / 3600.0            # mol/s
    MW = s.MW
    st = s
    prof = {"L": [L0], "P": [s.P], "T": [s.T - K0], "HL": [], "regime": [], "vm": [], "z": [z0],
            "Hm": [_hyd_margin(s, fp)], "q_heat": []}
    Qtot = 0.0
    Qheat = 0.0
    capped = False
    evr = 0.0

    def advance(state, grad, Tamb, h):
        P2 = state.P - grad * h / 1e5
        if P2 <= 0.2:
            raise UnitError(f"{unit['name']}: pressure falls below 0.2 bar - line too small or too long")
        # heat loss: exact exponential approach to ambient over the step (stable for any U)
        ncp = n_mol * max(state.flash.Cp, 1.0)                # W/K
        q = ncp * (state.T - Tamb) * (1.0 - math.exp(-U * math.pi * D * h / ncp)) if U > 0 else 0.0
        q -= q_in * h
        H2 = state.H - q / n_mol - MW / 1000.0 * G * h * math.sin(theta)
        fr = _ph(fp, state.z, P2, H2, state.T, state.flash.Kset)
        return make_stream("", fp, state.F, state.z, fr), q

    h_min = dL / 256.0
    for k in range(n):
        Tamb = Tamb_in + (Tamb_out - Tamb_in) * (k + 0.5) / n
        g1, d1 = pipe_gradient(fp, st, D, eps, theta, method, sigma)
        # Heun predictor-corrector; where the pressure falls fast (gas expanding near the end of a long
        # line) the increment is sub-stepped so that no step loses more than 5 % of the local pressure
        rem, cur, g = dL, st, g1
        heat_inc = 0.0
        while rem > dL * 1e-9:
            h = rem
            if g > 0 and g * h / 1e5 > 0.05 * cur.P:
                h = min(rem, max(0.05 * cur.P * 1e5 / g, h_min))
            while True:
                try:
                    trial, _ = advance(cur, g, Tamb, h)
                    g2, _ = pipe_gradient(fp, trial, D, eps, theta, method, sigma)
                    nxt, q = advance(cur, 0.5 * (g + g2), Tamb, h)
                    break
                except UnitError:
                    if h <= h_min * 1.0001:
                        raise
                    h = max(h / 4.0, h_min)
            Qtot += q
            if T_hold is not None and nxt.T < T_hold - 1e-9:
                # controlled heating: add what it takes to bring the fluid back to T_hold, up to q_max·h
                frt = fp.pt_flash(nxt.z, T_hold, nxt.P, nxt.flash.Kset)
                need = (frt.H - nxt.H) * n_mol                  # W
                cap = q_max * h if q_max > 0 else math.inf
                add = min(need, cap)
                if add >= need - 1e-9:
                    nxt = make_stream("", fp, nxt.F, nxt.z, frt)
                else:
                    capped = True
                    nxt = make_stream("", fp, nxt.F, nxt.z, _ph(fp, nxt.z, nxt.P, nxt.H + add / n_mol, nxt.T,
                                                                nxt.flash.Kset))
                Qheat += add
                Qtot -= add
                heat_inc += add
            rem -= h
            cur = nxt
            if rem > dL * 1e-9:
                g, _ = pipe_gradient(fp, cur, D, eps, theta, method, sigma)
        st_new = cur
        prof["q_heat"].append(heat_inc / dL)
        evr = max(evr, d1["vm"] / (122.0 / math.sqrt(max(d1["rho_ns"], 1e-6))))
        prof["HL"].append(d1["HL"])
        prof["regime"].append(d1["regime"])
        prof["vm"].append(d1["vm"])
        st = st_new
        prof["L"].append(L0 + (k + 1) * dL)
        prof["P"].append(st.P)
        prof["T"].append(st.T - K0)
        prof["z"].append(z0 + (k + 1) * dL * math.sin(theta))
        prof["Hm"].append(_hyd_margin(st, fp))
    _, dl = pipe_gradient(fp, st, D, eps, theta, method, sigma)
    prof["HL"].append(dl["HL"])
    prof["regime"].append(dl["regime"])
    prof["vm"].append(dl["vm"])
    regs = prof["regime"]
    main_regime = max(set(regs), key=regs.count)
    res = {"Pressure drop [bar]": s.P - st.P, "Outlet P [bar(a)]": st.P, "Outlet T [°C]": st.T - K0,
           "Inlet T [°C]": s.T - K0, "Flow regime (dominant)": main_regime,
           "Inlet mixture velocity [m/s]": prof["vm"][0], "Outlet mixture velocity [m/s]": prof["vm"][-1],
           "Average liquid holdup [-]": float(np.mean(prof["HL"])),
           "Liquid inventory [m³]": float(np.mean(prof["HL"])) * math.pi * D * D / 4 * L,
           "Erosional velocity ratio (API RP 14E, C=100)": evr, "Heat loss [kW]": Qtot / 1000.0,
           "Inclination [°]": math.degrees(theta)}
    hm = [x for x in prof["Hm"] if x is not None]
    if hm:
        res["Min. hydrate margin along line [°C]"] = min(hm)
    warns = []
    if T_hold is not None:
        res["Controlled heating [kW]"] = Qheat / 1000.0
        res["Heated length [m]"] = dL * sum(1 for q in prof["q_heat"] if q > 1e-9)
        res["Peak heating [W/m]"] = max(prof["q_heat"] or [0.0])
        if capped:
            warns.append(f"installed heating ({q_max:.0f} W/m) cannot hold {T_hold - K0:.1f} °C along the whole line")
    if evr > 1.0:
        warns.append(f"Mixture velocity exceeds the API RP 14E erosional velocity (ratio {evr:.2f})")
    if warns:
        res["Warning"] = "; ".join(warns)
    unit["_profile"] = prof
    en = [_energy(unit, -Qtot / 1000.0)] if (U > 0 or q_in > 0 or Qheat > 0) else []
    return {"out": [make_stream("", fp, st.F, st.z, st.flash)]}, res, en


def _hyd_margin(st, fp):
    """Hydrate margin (T - inhibited hydrate T, °C) of a stream that carries water, else None."""
    from .streams import hydrate_state
    if st.empty or fp.iw < 0 or st.z[fp.iw] <= 1e-9:
        return None
    try:
        return hydrate_state(st, fp)[2]
    except Exception:
        return None


# ---- column ---------------------------------------------------------------

def calc_column(unit, ins, fp):
    from .column import ColumnSpec, run_column, bubble_pressure, ColumnError
    p = unit["params"]
    n_trays = int(round(p["n_trays"]))
    cond = None if p.get("condenser", "None") == "None" else p["condenser"]
    reb = p.get("reboiler", "Yes") == "Yes"
    c0 = 1 if cond else 0
    feeds, streams_in = [], []
    fs = int(round(p.get("feed_stage", 1)))
    if not 1 <= fs <= n_trays:
        raise UnitError(f"{unit['name']}: feed tray must be between 1 and {n_trays}")
    stage_of = {"feed_top": c0, "feed": c0 + fs - 1, "feed_bottom": c0 + n_trays - 1}
    wd = p.get("water_draw", WD_NONE)
    water_parts = []                                   # (kmol/h, composition, J/mol) of decanted free water
    for port, lst in ins.items():
        for st_ in lst:
            if st_ is not None and not st_.empty:
                streams_in.append(st_)
                F_, z_, H_ = st_.F, st_.z, st_.H
                w = st_.flash.phase("W") if wd != WD_NONE else None
                if w is not None and len(st_.flash.phases) > 1 and w.beta < 0.999999:
                    Fw = st_.F * w.beta
                    water_parts.append((Fw, w.x.copy(), w.H, st_.T, st_.P))
                    F_ = st_.F - Fw
                    z_ = (st_.F * st_.z - Fw * w.x) / F_
                    H_ = (st_.F * st_.H - Fw * w.H) / F_
                feeds.append((stage_of[port], F_, z_, H_))
    if not feeds:
        z0 = next((x.z for lst in ins.values() for x in lst if x is not None), None)
        return ({"overhead": [zero_stream("", fp, z0)], "bottoms": [zero_stream("", fp, z0)],
                 "water": [zero_stream("", fp, z0)]}, {"Status": "No flow"}, [])
    if not cond and not reb and not ({"feed_top", "feed_bottom"} & {k for k, v in ins.items() if v}):
        raise UnitError(f"{unit['name']}: an absorber needs a top (liquid) and/or bottom (gas) feed")
    P_top, P_bot = float(p["P_top"]), float(p["P_bot"])
    if P_bot < P_top:
        raise UnitError(f"{unit['name']}: bottom pressure must be ≥ top pressure")
    specs = {"RR": float(p.get("RR", 2.0)), "reb_spec": p.get("reb_spec") if reb else None,
             "reb_value": float(p.get("reb_value", 0.0))}
    if reb and specs["reb_spec"] == "Distillate rate" and not cond:
        raise UnitError(f"{unit['name']}: 'Distillate rate' needs a condenser")
    cspec = ColumnSpec(n_trays, cond, reb, P_top, P_bot, feeds, specs)
    key = (unit["name"], n_trays, cond, reb, tuple(sorted(f[0] for f in feeds)), fp.n)
    try:
        col, X = run_column(fp, cspec, key)
    except (ColumnError, np.linalg.LinAlgError, FloatingPointError, ValueError) as e:
        raise UnitError(f"{unit['name']}: {e}")
    l, v, T = col.unpack(X)
    top, bot = col.products(X)
    N = cspec.N
    Ftop, Fbot = float(top.sum()), float(bot.sum())
    if Ftop < -1e-9 or Fbot < -1e-9:
        raise UnitError(f"{unit['name']}: negative product flow")
    ztop = top / Ftop if Ftop > 1e-12 else None
    zbot = bot / Fbot if Fbot > 1e-12 else None
    frt = fp.pt_flash(ztop, float(T[0]), float(col.P[0])) if ztop is not None else None
    frb = fp.pt_flash(zbot, float(T[-1]), float(col.P[-1])) if zbot is not None else None
    if wd == WD_BOTH and cond and frt is not None and frt.phase("W") is not None and len(frt.phases) > 1:
        wph = frt.phase("W")
        Fw = Ftop * wph.beta
        water_parts.append((Fw, wph.x.copy(), wph.H, float(T[0]), float(col.P[0])))
        rest = Ftop * ztop - Fw * wph.x
        Ftop = float(rest.sum())
        ztop = rest / Ftop if Ftop > 1e-12 else None
        frt = fp.pt_flash(ztop, float(T[0]), float(col.P[0])) if ztop is not None else None
    out_top = make_stream("", fp, Ftop, ztop, frt) if frt else zero_stream("", fp, None, T[0], col.P[0])
    out_bot = make_stream("", fp, Fbot, zbot, frb) if frb else zero_stream("", fp, None, T[-1], col.P[-1])
    if water_parts:
        Fw = sum(w[0] for w in water_parts)
        zw = sum(w[0] * w[1] for w in water_parts) / Fw
        Hw = sum(w[0] * w[2] for w in water_parts) / Fw
        out_w = make_stream("", fp, Fw, zw, _ph(fp, zw, min(w[4] for w in water_parts), Hw, water_parts[0][3]))
    else:
        out_w = zero_stream("", fp, None, T[0], col.P[0])
    # duties from the converged stage model (rigorous PR at the stage states)
    Qc, Qr = col.duties(X)
    Hin = sum(x.F * x.H for x in streams_in) / 3600.0
    Hout = (out_top.F * out_top.H + out_bot.F * out_bot.H + (out_w.F * out_w.H if not out_w.empty else 0.0)) / 3600.0
    imbalance = Hout - Hin - Qc - Qr
    res = {"Top T [°C]": T[0] - K0, "Bottom T [°C]": T[-1] - K0,
           "Overhead flow [kmol/h]": Ftop, "Bottoms flow [kmol/h]": Fbot,
           "Overhead mass flow [kg/h]": out_top.F * out_top.MW, "Bottoms mass flow [kg/h]": out_bot.F * out_bot.MW}
    if cond:
        res["Condenser duty [kW]"] = -Qc
        res["Reflux ratio"] = specs["RR"]
    if reb:
        res["Reboiler duty [kW]"] = Qr
    if zbot is not None:
        try:
            res["Bottoms TVP @ 37.8 °C [bar(a)]"] = bubble_pressure(fp, zbot, 310.93)
        except Exception:
            pass
    res["Outer iterations"] = col.outer_iterations
    if water_parts:
        res["Free water drawn [kg/h]"] = out_w.F * out_w.MW
    free_w = [nm for nm, fr_ in (("overhead", frt), ("bottoms", frb))
              if fr_ is not None and fr_.phase("W") is not None and len(fr_.phases) > 1]
    if free_w:
        res["Warning"] = (f"Free water forms in the {' and '.join(free_w)} at column conditions - the column is "
                          f"VLE-only (no water draw); remove free water upstream (3-phase separator). "
                          f"Energy closure {imbalance:.1f} kW")
    elif abs(imbalance) > 1e-4 * max(abs(Hin), abs(Qc) + abs(Qr), 1.0):
        res["Note"] = f"Energy closure {imbalance:.2f} kW"
    labels = cspec.stage_labels()
    xs = np.zeros((N, fp.n))
    ys = np.zeros((N, fp.n))
    L, V = l.sum(1), v.sum(1)
    xs[:, col.idx] = l / np.maximum(L, 1e-30)[:, None]
    ys[:, col.idx] = np.where(V[:, None] > 1e-12, v / np.maximum(V, 1e-30)[:, None], 0.0)
    unit["_column"] = {"labels": labels, "T": (T - K0).tolist(), "P": col.P.tolist(), "L": L.tolist(),
                       "V": V.tolist(), "x": xs.tolist(), "y": ys.tolist(), "keys": list(fp.keys)}
    en = []
    if cond:
        en.append(EnergyStream(f"Q-{unit['name']} cond", Qc, unit["name"], "heat"))
    if reb:
        en.append(EnergyStream(f"Q-{unit['name']} reb", Qr, unit["name"], "heat"))
    return {"overhead": [out_top], "bottoms": [out_bot], "water": [out_w]}, res, en


CALC = {
    "valve": calc_valve, "mixer": calc_mixer, "splitter": calc_splitter,
    "separator": calc_separator, "separator3": calc_separator3, "scrubber": calc_scrubber,
    "compressor": calc_compressor, "expander": calc_expander, "pump": calc_pump,
    "heater": calc_heater, "cooler": calc_cooler, "hx": calc_hx, "aircooler": calc_aircooler,
    "pipe": calc_pipe, "column": calc_column,
}


# ---- subsea (SURF) equipment: procsim/surf.py registers itself on import ----
PROFILE_TYPES = ("pipe", "well", "jumper", "flowline", "riser", "injection_well")     # units that leave a line profile
from . import surf as _surf   # noqa: E402,F401  (needs the helpers above; works whichever module loads first)
from . import dehydration as _dehy   # noqa: E402,F401  (TEG contactor registers itself)
from . import process_units as _pu   # noqa: E402,F401  (HYSYS-style units register themselves)
