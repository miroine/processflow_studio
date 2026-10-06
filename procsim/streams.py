"""Material / energy streams and their reported properties."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .thermo import FluidPackage, FlashResult, V_STD_GAS
from .transport import (phase_viscosity_cP, hydrate_T_motiee, gas_gravity_dry, hydrate_depression, hydrate_T,
                        aqueous_inhibitor_wt)

PHASE_NAMES = {"V": "Vapour", "L": "Liquid", "W": "Aqueous"}


@dataclass
class MaterialStream:
    name: str
    F: float = 0.0                    # kmol/h
    z: np.ndarray | None = None
    T: float | None = None            # K
    P: float | None = None            # bar(a)
    flash: FlashResult | None = None

    @property
    def empty(self):
        return self.F <= 1e-12 or self.flash is None

    @property
    def H(self):
        """Molar enthalpy J/mol."""
        return self.flash.H if self.flash else 0.0

    @property
    def S(self):
        return self.flash.S if self.flash else 0.0

    @property
    def heat_flow_kW(self):
        return self.F * self.H / 3600.0

    @property
    def MW(self):
        return 0.0 if self.z is None else float(self.z @ self._fp_mw)

    def copy(self, name=None):
        st = MaterialStream(name or self.name, self.F, None if self.z is None else self.z.copy(),
                            self.T, self.P, self.flash)
        st._fp_mw = self._fp_mw
        return st

    # set by the flowsheet so MW is available without passing the package around
    _fp_mw: np.ndarray = field(default=None, repr=False)

    def component_flows(self):
        return self.F * self.z if self.z is not None else None


def make_stream(name, fp: FluidPackage, F, z, fr: FlashResult | None) -> MaterialStream:
    z = np.asarray(z, float)
    s = z.sum()
    z = z / s if s > 0 else z
    st = MaterialStream(name, float(F), z, fr.T if fr else None, fr.P if fr else None, fr)
    st._fp_mw = fp.MW
    return st


def zero_stream(name, fp: FluidPackage, z=None, T=None, P=None) -> MaterialStream:
    z = np.ones(fp.n) / fp.n if z is None else np.asarray(z, float)
    st = MaterialStream(name, 0.0, z, T, P, None)
    st._fp_mw = fp.MW
    return st


@dataclass
class EnergyStream:
    name: str
    duty_kW: float
    unit: str = ""
    kind: str = "heat"       # heat | work


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def phase_stream(st: MaterialStream, fp: FluidPackage, kinds, name) -> MaterialStream:
    """Sub-stream made of the given phase kinds of st (used by separators)."""
    if st.empty:
        return zero_stream(name, fp, st.z, st.T, st.P)
    ph = [p for p in st.flash.phases if p.kind in kinds]
    beta = sum(p.beta for p in ph)
    if beta <= 1e-14:
        return zero_stream(name, fp, st.z, st.T, st.P)
    x = sum(p.beta * p.x for p in ph) / beta
    F = st.F * beta
    fr = FlashResult(st.T, st.P, x, [])
    for p in ph:
        q = type(p)(**{**p.__dict__})
        q.beta = p.beta / beta
        fr.phases.append(q)
    return make_stream(name, fp, F, x, fr)


def stream_properties(st: MaterialStream, fp: FluidPackage) -> dict:
    """Headline properties in display units (metric)."""
    if st.empty:
        return {"Vapour fraction": None, "Temperature [°C]": None if st.T is None else st.T - 273.15,
                "Pressure [bar(a)]": st.P, "Molar flow [kmol/h]": 0.0, "Mass flow [kg/h]": 0.0,
                "Std gas flow [MSm³/d]": 0.0, "Std liq vol flow [m³/h]": 0.0, "Actual vol flow [m³/h]": 0.0,
                "Molar enthalpy [kJ/kmol]": None, "Heat flow [kW]": 0.0, "Molar entropy [kJ/kmol·K]": None,
                "Molecular weight": st.MW, "Mass density [kg/m³]": None, "Z (vapour)": None,
                "Cp [kJ/kmol·K]": None, "Cp/Cv (vapour)": None, "Viscosity vapour [cP]": None,
                "Viscosity liquid [cP]": None, "Hydrate T (Motiee) [°C]": None, "Hydrate margin [°C]": None,
                "Inhibitor in aqueous [wt%]": None, "Hydrate T (inhibited) [°C]": None, "Phase": "No flow"}
    fr = st.flash
    MW = st.MW
    mass = st.F * MW
    vol_act = sum(st.F * p.beta * 1000.0 * p.Vs for p in fr.phases)   # m3/h
    std_liq = float(st.F * (st.z * fp.MW) @ (1.0 / fp.rho_std))           # m3/h
    v = fr.phase("V")
    mu_v = phase_viscosity_cP(fp, v, st.T) if v else None
    liqs = [p for p in fr.phases if p.kind != "V"]
    mu_l = None
    if liqs:
        # volume-weighted liquid viscosity (hydrocarbon + aqueous, no emulsion effect)
        vols = [p.beta * p.Vs for p in liqs]
        mu_l = sum(w * phase_viscosity_cP(fp, p, st.T) for w, p in zip(vols, liqs)) / sum(vols)
    t_hyd, t_inh, margin, wt = hydrate_state(st, fp)
    return {
        "Phase": fr.phase_label,
        "Vapour fraction": fr.vf,
        "Temperature [°C]": st.T - 273.15,
        "Pressure [bar(a)]": st.P,
        "Molar flow [kmol/h]": st.F,
        "Mass flow [kg/h]": mass,
        "Std gas flow [MSm³/d]": st.F * V_STD_GAS * 24.0 / 1e6,
        "Std liq vol flow [m³/h]": std_liq,
        "Actual vol flow [m³/h]": vol_act,
        "Molar enthalpy [kJ/kmol]": fr.H,
        "Heat flow [kW]": st.heat_flow_kW,
        "Molar entropy [kJ/kmol·K]": fr.S,
        "Molecular weight": MW,
        "Mass density [kg/m³]": mass / vol_act if vol_act > 0 else None,
        "Z (vapour)": v.Z if v else None,
        "Cp [kJ/kmol·K]": fr.Cp,
        "Cp/Cv (vapour)": (v.Cp / v.Cv) if v and v.Cv > 0 else None,
        "Viscosity vapour [cP]": mu_v,
        "Viscosity liquid [cP]": mu_l,
        "Hydrate T (Motiee) [°C]": t_hyd,
        "Inhibitor in aqueous [wt%]": wt,
        "Hydrate T (inhibited) [°C]": t_inh,
        "Hydrate margin [°C]": margin,
    }


def hydrate_state(st: MaterialStream, fp: FluidPackage):
    """(uninhibited hydrate T, inhibited hydrate T, margin = T - inhibited T, inhibitor wt%) or Nones.

    The inhibitor concentration is read from the stream's aqueous phase; if no aqueous phase is present
    at stream conditions the overall inhibitor/water ratio is used (inhibitor that will be with the water
    once it condenses)."""
    if st.empty or fp.iw < 0:
        return None, None, None, None
    v = st.flash.phase("V")
    if v is None:
        return None, None, None, None
    t_hyd = hydrate_T(fp, v.x, st.P)
    if t_hyd is None:
        return None, None, None, None
    aq = st.flash.phase("W")
    x_aq = aq.x if aq is not None else st.z
    dT = hydrate_depression(fp, x_aq)
    wts = aqueous_inhibitor_wt(fp, x_aq)
    wt = sum(wts.values()) if wts else 0.0
    t_inh = t_hyd - dT
    return t_hyd, t_inh, st.T - 273.15 - t_inh, wt


def hydrate_risk(st: MaterialStream, fp: FluidPackage):
    """True when the stream carries water, has a vapour phase and is colder than the (inhibited) hydrate T."""
    if st.empty or fp.iw < 0 or st.z[fp.iw] <= 1e-9 or st.flash.phase("V") is None:
        return False
    _, t_inh, margin, _ = hydrate_state(st, fp)
    return margin is not None and margin < 0


def phase_table(st: MaterialStream, fp: FluidPackage):
    """Per-phase properties rows (list of dicts)."""
    rows = []
    if st.empty:
        return rows
    for p in st.flash.phases:
        F = st.F * p.beta
        rows.append({
            "Phase": PHASE_NAMES[p.kind],
            "Mole fraction": p.beta,
            "Molar flow [kmol/h]": F,
            "Mass flow [kg/h]": F * p.MW,
            "Actual vol flow [m³/h]": F * 1000.0 * p.Vs,
            "Density [kg/m³]": p.rho,
            "MW": p.MW,
            "Z": p.Z,
            "Cp [kJ/kmol·K]": p.Cp,
            "Cp/Cv": p.Cp / p.Cv if p.Cv > 0 else None,
            "Viscosity [cP]": phase_viscosity_cP(fp, p, st.T),
            "Enthalpy [kJ/kmol]": p.H,
        })
    return rows


def composition_table(st: MaterialStream, fp: FluidPackage):
    rows = []
    if st.z is None:
        return rows
    phases = st.flash.phases if not st.empty else []
    mass = st.z * fp.MW
    mass = mass / mass.sum() if mass.sum() > 0 else mass
    for i, k in enumerate(fp.keys):
        r = {"Component": k, "Overall mole frac": st.z[i], "Mass frac": mass[i],
             "Molar flow [kmol/h]": st.F * st.z[i]}
        for p in phases:
            r[f"{PHASE_NAMES[p.kind]} x"] = p.x[i]
        rows.append(r)
    return rows
