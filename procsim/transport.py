"""Transport properties and hydrate screening.

* Viscosity of hydrocarbon phases: Lohrenz, Bray & Clark (1964) with Stiel-Thodos
  dilute-gas viscosities and Herning-Zipperer mixing; densities from the
  Peneloux-shifted PR volume.  LBC is accurate for gases and light liquids; it is
  the usual EOS-consistent choice in reservoir/process work but is known to be
  rough for heavy oils (it is normally tuned to lab data).
* Aqueous phase: pure-water viscosity (Vogel equation), dissolved gas ignored.
* Hydrate formation temperature: Motiee (1991) gas-gravity correlation - a
  screening estimate for natural gas; salts and sour gas are not accounted for.
* Thermodynamic inhibitors: depression of the hydrate temperature from the
  inhibitor content of the aqueous phase - Hammerschmidt (1934) for MEG
  (K = 1297 in degC units, GPSA) and Nielsen & Bucklin (1983) for methanol,
  which stays valid to high concentrations.  Contributions are added when both
  are present (approximation).
"""
from __future__ import annotations

import math

import numpy as np

_LBC_A = (0.1023, 0.023364, 0.058533, -0.040758, 0.0093324)


def _vc(fp):
    v = getattr(fp, "_vc_cache", None)
    if v is None:
        v = np.array([c.Vc_est for c in fp.comps], float)   # cm3/mol
        fp._vc_cache = v
    return v


def water_viscosity_cP(T):
    """Vogel equation for liquid water, mPa.s (valid ~0-150 degC)."""
    return 0.02414 * 10 ** (247.8 / (T - 140.0))


def lbc_viscosity_cP(fp, x, T, V_m3_per_mol):
    """LBC viscosity (cP) of a phase with mole fractions x at T [K], molar volume [m3/mol]."""
    x = np.asarray(x, float)
    m = x > 0
    Tc, Pc_atm, MW = fp.Tc[m], fp.Pc[m] / 1.01325, fp.MW[m]
    xi_i = Tc ** (1 / 6) / (np.sqrt(MW) * Pc_atm ** (2 / 3))
    Tr = T / Tc
    mu_i = np.where(Tr <= 1.5, 34e-5 * Tr ** 0.94, 17.78e-5 * np.maximum(4.58 * Tr - 1.67, 1e-12) ** 0.625) / xi_i
    xs = x[m]
    sq = np.sqrt(MW)
    mu0 = float((xs * mu_i * sq).sum() / (xs * sq).sum())
    Tpc = float(xs @ Tc)
    Ppc = float(xs @ Pc_atm)
    MWm = float(xs @ MW)
    xi = Tpc ** (1 / 6) / (math.sqrt(MWm) * Ppc ** (2 / 3))
    Vpc = float(xs @ _vc(fp)[m])                  # cm3/mol
    rho_r = Vpc / (V_m3_per_mol * 1e6)
    a = _LBC_A
    poly = a[0] + a[1] * rho_r + a[2] * rho_r ** 2 + a[3] * rho_r ** 3 + a[4] * rho_r ** 4
    return mu0 + (poly ** 4 - 1e-4) / xi


def phase_viscosity_cP(fp, phase, T):
    if phase.kind == "W":
        return water_viscosity_cP(T)
    return lbc_viscosity_cP(fp, phase.x, T, phase.Vs)


def hydrate_T_motiee(gas_gravity, P_bar):
    """Hydrate formation temperature [degC] from Motiee (1991); None outside ~3.5-280 bar / SG 0.55-1."""
    if not (3.5 <= P_bar <= 280.0) or not (0.55 <= gas_gravity <= 1.0):
        return None
    lp = math.log10(P_bar * 14.5038)
    tf = (-238.24469 + 78.99667 * lp - 5.352544 * lp ** 2 + 349.473877 * gas_gravity
          - 150.854675 * gas_gravity ** 2 - 27.604065 * gas_gravity * lp)
    return (tf - 32.0) / 1.8


MOTIEE, VDWP = "Motiee (gas-gravity correlation)", "van der Waals–Platteeuw (statistical, EOS fugacities)"
HYDRATE_MODELS = (MOTIEE, VDWP)


def hydrate_T(fp, x, P_bar):
    """Uninhibited hydrate formation temperature [°C] of a gas composition at P with the fluid package's hydrate
    model (Motiee by default; van der Waals-Platteeuw when selected). None when not applicable."""
    if getattr(fp, "hydrate_model", MOTIEE) == VDWP:
        from .hydrate import hydrate_T_C
        return hydrate_T_C(fp, x, P_bar)
    sg = gas_gravity_dry(fp, x)
    return hydrate_T_motiee(sg, P_bar) if sg else None


def gas_gravity_dry(fp, x):
    """Gas gravity of a vapour composition on a water-free basis."""
    x = np.asarray(x, float).copy()
    if fp.iw >= 0:
        x[fp.iw] = 0.0
    s = x.sum()
    if s <= 0:
        return None
    return float((x / s) @ fp.MW) / 28.9647


HAMMERSCHMIDT_K = 1297.0      # degC * (g/mol), GPSA (2335 in degF units)
INHIBITORS = {"MEG": 62.068, "MeOH": 32.042}


def aqueous_inhibitor_wt(fp, x_aq):
    """{inhibitor: wt% in the salt-free aqueous phase (inhibitor + water basis)}."""
    if fp.iw < 0:
        return {}
    m_w = x_aq[fp.iw] * fp.MW[fp.iw]
    out = {}
    for k in INHIBITORS:
        if k in fp.keys:
            i = fp.index(k)
            m = x_aq[i] * fp.MW[i]
            if m > 0:
                out[k] = 100.0 * m / (m + m_w) if (m + m_w) > 0 else 0.0
    return out


def hydrate_depression(fp, x_aq):
    """Hydrate temperature depression [degC] from the inhibitors in an aqueous phase."""
    dT = 0.0
    if fp.iw < 0:
        return 0.0
    for k, w in aqueous_inhibitor_wt(fp, x_aq).items():
        if w <= 0:
            continue
        if k == "MeOH":
            i = fp.index(k)
            xw = x_aq[fp.iw] / (x_aq[fp.iw] + x_aq[i])
            dT += -72.0 * math.log(max(xw, 1e-6))              # Nielsen-Bucklin, degC
        else:
            w = min(w, 99.0)
            dT += HAMMERSCHMIDT_K * w / (INHIBITORS[k] * (100.0 - w))
    return dT


def required_inhibitor_wt(inhibitor, dT):
    """Rich-solution wt% (inhibitor + water basis) that depresses the hydrate T by dT [degC]."""
    if dT <= 0:
        return 0.0
    M = INHIBITORS[inhibitor]
    if inhibitor == "MeOH":
        x_meoh = 1.0 - math.exp(-dT / 72.0)
        return 100.0 * x_meoh * M / (x_meoh * M + (1 - x_meoh) * 18.015)
    return 100.0 * dT * M / (HAMMERSCHMIDT_K + dT * M)


def injection_rate(water_kg_h, rich_wt, lean_wt):
    """Lean-inhibitor injection [kg/h] to reach rich_wt (%) given free water [kg/h] and lean purity (%)."""
    if rich_wt <= 0:
        return 0.0
    if lean_wt <= rich_wt:
        return float("inf")
    return water_kg_h * rich_wt / (lean_wt - rich_wt)
