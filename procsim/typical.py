"""Typical-value presets for unit parameters (v7.8).

Vendor-neutral textbook ranges for efficiencies, pressure drops, roughness, permeances ... so a new unit does not start from a
blank number.  Illustrative generic values, not vendor data or a design basis: they only fill the parameters named in the preset
and leave every other parameter (and every specification) untouched."""
from __future__ import annotations

PRESETS = {
    "compressor": {
        "Centrifugal, process gas (polytropic 78 %)": {"eff_type": "Polytropic", "eff": 78.0},
        "Centrifugal, small / high ratio (polytropic 72 %)": {"eff_type": "Polytropic", "eff": 72.0},
        "Reciprocating (adiabatic 82 %)": {"eff_type": "Adiabatic", "eff": 82.0},
        "Screw (adiabatic 70 %)": {"eff_type": "Adiabatic", "eff": 70.0},
    },
    "pump": {
        "Centrifugal, large (80 %)": {"eff": 80.0}, "Centrifugal, small (65 %)": {"eff": 65.0},
        "Reciprocating / plunger (85 %)": {"eff": 85.0},
    },
    "expander": {"Turbo-expander (82 %)": {"eff": 82.0}, "Small expansion turbine (72 %)": {"eff": 72.0}},
    "cooler": {"Seawater / water cooler (ΔP 0.7 bar)": {"dP": 0.7}, "Gas cooler, low pressure drop (ΔP 0.3 bar)": {"dP": 0.3}},
    "heater": {"Hot-oil / steam heater (ΔP 0.7 bar)": {"dP": 0.7}, "Fired heater (ΔP 1.5 bar)": {"dP": 1.5}},
    "hx": {"Shell & tube, gas / liquid (ΔP 0.5 / 0.5 bar)": {"dP_tube": 0.5, "dP_shell": 0.5},
           "Welded plate / PCHE, gas / gas (ΔTmin 5 K, ΔP 0.3 bar)": {"dTmin": 5.0, "dP_tube": 0.3, "dP_shell": 0.3},
           "Shell & tube, process / process (ΔTmin 10 K)": {"dTmin": 10.0}},
    "aircooler": {"Temperate / North Sea (air 10 °C, fans 65 %)": {"air_T": 10.0, "fan_eff": 65.0},
                  "Hot climate (air 35 °C)": {"air_T": 35.0, "fan_eff": 65.0}},
    "gas_turbine": {
        "Aero-derivative (36 %, exhaust 500 °C)": {"eff": 36.0, "T_exh": 500.0, "casing": 2.0},
        "Industrial heavy-duty (33 %, exhaust 540 °C)": {"eff": 33.0, "T_exh": 540.0, "casing": 2.0},
        "Small industrial (28 %, exhaust 520 °C)": {"eff": 28.0, "T_exh": 520.0, "casing": 2.5},
    },
    "separator": {"Typical (ΔP 0.2 bar)": {"dP": 0.2}},
    "scrubber": {"Typical mesh-pad scrubber (ΔP 0.2 bar, margin 20 %)": {"dP": 0.2, "margin": 20.0}},
    "pipe": {"Carbon steel, new (0.045 mm)": {"rough": 0.045}, "Carbon steel, corroded (0.2 mm)": {"rough": 0.2},
             "Internally coated / CRA (0.01 mm)": {"rough": 0.01}},
    "membrane": {
        "Cellulose acetate (100 GPU, CO₂/CH₄ 20)": {"perm_CO2": 100.0, "sel_CH4": 20.0, "sel_H2S": 1.5, "sel_H2O": 6.0, "heavy": 0.8},
        "Polyimide, high selectivity (80 GPU, CO₂/CH₄ 35)": {"perm_CO2": 80.0, "sel_CH4": 35.0, "sel_H2S": 1.2, "sel_H2O": 6.0, "heavy": 0.6},
        "High-flux polymer (250 GPU, CO₂/CH₄ 15)": {"perm_CO2": 250.0, "sel_CH4": 15.0, "sel_H2S": 1.5, "sel_H2O": 8.0, "heavy": 1.0},
    },
    "mol_sieve": {"Cryogenic service (0.1 ppmv)": {"ppmv": 0.1, "capacity": 8.0, "regen_T": 260.0},
                  "Dew-point spec (1 ppmv)": {"ppmv": 1.0, "capacity": 9.0, "regen_T": 260.0},
                  "Pipeline spec (50 ppmv)": {"ppmv": 50.0, "capacity": 10.0, "regen_T": 230.0}},
    "chiller": {"Propane screw loop (55 % of Carnot)": {"model": "Fraction of Carnot COP", "refrigerant": "Propane (R-290)", "carnot": 55.0, "T_cond": 40.0},
                "Propane cycle, single stage (PR cycle model)": {"model": "Cycle (PR equation of state)", "refrigerant": "Propane (R-290)",
                                                                 "stages": "Single stage", "eta_is": 75.0, "T_cond": 40.0},
                "Propane cycle, two stages with economiser": {"model": "Cycle (PR equation of state)", "refrigerant": "Propane (R-290)",
                                                              "stages": "Two stages with economiser", "eta_is": 75.0, "T_cond": 40.0},
                "Ethylene, low-temperature stage": {"model": "Cycle (PR equation of state)", "refrigerant": "Ethylene (R-1150)",
                                                   "stages": "Single stage", "eta_is": 75.0, "T_cond": -10.0}},
    "teg_contactor": {"Lean TEG 99.0 wt%": {"teg_wt": 99.0}, "Lean TEG 99.5 wt%": {"teg_wt": 99.5}, "With stripping gas 99.9 wt%": {"teg_wt": 99.9}},
    "subsea_heater": {"Direct electric (97 %)": {"eff": 97.0}},
}


def presets(utype):
    return PRESETS.get(utype, {})


def apply(unit, name):
    """Write a preset into the unit's parameters; returns the keys changed."""
    pre = presets(unit["type"]).get(name)
    if pre is None:
        raise KeyError(name)
    unit["params"].update(pre)
    return list(pre)
