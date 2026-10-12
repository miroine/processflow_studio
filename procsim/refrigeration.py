"""Vapour-compression refrigeration cycle on the Peng-Robinson equation of state (v7.8).

Single-component refrigerant (propane, propylene, ethylene, ammonia, i-butane ...) in the standard cycle

    1  evaporator outlet, saturated vapour at the evaporating temperature (plus superheat)
    2  isentropic compression to the condensing pressure, real efficiency eta_is
    3  condenser outlet, saturated liquid at the condensing temperature (less sub-cooling)
    4  isenthalpic expansion to the evaporating pressure

optionally with a flash-gas economiser between two compression stages (``stages = 2``, intermediate pressure sqrt(Pe Pc)).
Returns refrigerant flow, compressor power, COP and the Carnot fraction, so the chiller no longer needs an assumed fraction of
Carnot.  Enthalpy and entropy use the ideal-gas Cp polynomials plus the PR departure, the same package as the flowsheet."""
from __future__ import annotations

import math

import numpy as np

from .components import LIBRARY
from .thermo import FluidPackage

K0 = 273.15

REFRIGERANTS = {"Propane (R-290)": "C3", "Propylene (R-1270)": "C3=", "Ethylene (R-1150)": "C2=", "Ammonia (R-717)": "NH3",
                "i-Butane (R-600a)": "iC4", "Ethane (R-170)": "C2"}
_FP = {}


def _fp(key):
    if key not in _FP:
        _FP[key] = FluidPackage([LIBRARY[key]])
    return _FP[key]


class RefrigError(ValueError):
    pass


def _state(fp, T, P, kind):
    """(H [J/mol], S [J/mol/K]) of the pure fluid at T [K], P [bar] in phase 'V' or 'L'."""
    z = np.array([1.0])
    fr = fp.pt_flash(z, T, P)
    ph = fr.phases[0]
    if kind in ("V", "L") and len(fr.phases) == 1 and ph.kind != kind:
        # single-phase result of the other kind: take it (supercritical / subcooled / superheated handled by the caller)
        pass
    return fr.H, fr.S


def _sat(fp, T):
    """(P [bar], Hv, Sv, Hl, Sl) on the saturation line at T [K]."""
    P = fp.psat_pure(0, T)
    if P is None:
        raise RefrigError(f"{T - K0:.1f} °C is above the critical temperature of {fp.keys[0]}")
    z = np.array([1.0])
    fv = fp._pure_two_phase(z, T, P, 1.0)
    fl = fp._pure_two_phase(z, T, P, 0.0)
    return P, fv.H, fv.S, fl.H, fl.S


def _compress(fp, P1, H1, S1, P2, eta, T_guess):
    """Outlet (H, T) after compression from state (P1, H1, S1) to P2 with isentropic efficiency eta."""
    fr_s = fp.ps_flash(np.array([1.0]), P2, S1, T_guess)
    H2s = fr_s.H
    H2 = H1 + (H2s - H1) / eta
    fr = fp.ph_flash(np.array([1.0]), P2, H2, fr_s.T + 10.0)
    return H2, fr.T


def cycle(refrigerant, Te_C, Tc_C, eta_is=0.75, superheat=5.0, subcool=0.0, stages=1, Q_kW=1.0):
    """Cycle for an evaporator duty Q_kW.  Te_C: evaporating temperature, Tc_C: condensing temperature [°C]."""
    key = REFRIGERANTS.get(refrigerant, refrigerant)
    if key not in LIBRARY:
        raise RefrigError(f"unknown refrigerant {refrigerant}")
    fp = _fp(key)
    Te, Tc = Te_C + K0, Tc_C + K0
    if Tc <= Te + 2.0:
        raise RefrigError(f"condensing temperature {Tc_C:.1f} °C must be above the evaporating temperature {Te_C:.1f} °C")
    Pe, Hv_e, Sv_e, Hl_e, _ = _sat(fp, Te)
    if Tc >= fp.Tc[0] * 0.995:
        raise RefrigError(f"condensing temperature {Tc_C:.1f} °C is too close to the critical point of {fp.keys[0]} "
                          f"({fp.Tc[0] - K0:.0f} °C): choose a lighter refrigerant for the condenser or lower T_cond")
    Pc, Hv_c, Sv_c, Hl_c, _ = _sat(fp, Tc)
    z = np.array([1.0])
    mw = float(fp.MW[0])
    # 1: evaporator outlet with superheat
    T1 = Te + max(superheat, 0.0)
    if superheat > 0.0:
        fr1 = fp.pt_flash(z, T1, Pe)
        H1, S1 = fr1.H, fr1.S
    else:
        H1, S1 = Hv_e, Sv_e                              # saturated vapour (a PT flash on the line can return either phase)
    # 3: condenser outlet with subcooling
    T3 = Tc - max(subcool, 0.0)
    H3 = fp.pt_flash(z, T3, Pc).H if subcool > 0.0 else Hl_c
    res = {"refrigerant": fp.comps[0].name, "Pe": Pe, "Pc": Pc, "mw": mw, "T1": T1 - K0, "pressure_ratio": Pc / Pe}
    if stages == 2:
        Pm = math.sqrt(Pe * Pc)
        Tm = fp.tsat_pure(0, Pm)
        # first stage
        H2a, T2a = _compress(fp, Pe, H1, S1, Pm, eta_is, Tm + 20.0)
        # flash-gas economiser: liquid at Pm splits into flash vapour (x) and saturated liquid
        _, Hv_m, _, Hl_m, _ = _sat(fp, Tm)
        x = (H3 - Hl_m) / (Hv_m - Hl_m)                  # vapour fraction after expansion of the condenser liquid to Pm
        x = min(max(x, 0.0), 0.99)
        H4 = Hl_m                                         # liquid to the evaporator valve, isenthalpic to Pe
        q_evap = H1 - H4                                  # J per mol through the evaporator
        n_e = Q_kW * 1000.0 / q_evap                      # mol/s through the evaporator
        n_h = n_e / (1.0 - x)                             # mol/s through the second stage
        # mixing of stage-1 discharge with flash gas before stage 2
        Hmix = ((n_e * H2a) + (n_h - n_e) * Hv_m) / n_h
        fm = fp.ph_flash(z, Pm, Hmix, Tm + 10.0)
        H2b, T2b = _compress(fp, Pm, Hmix, fm.S, Pc, eta_is, fm.T + 20.0)
        W = (n_e * (H2a - H1) + n_h * (H2b - Hmix)) / 1000.0
        res.update({"Pm": Pm, "Tm": Tm - K0, "flash_fraction": x, "T2": T2b - K0, "stages": 2})
        n_ref = n_h
    else:
        H2, T2 = _compress(fp, Pe, H1, S1, Pc, eta_is, Tc + 30.0)
        H4 = H3
        q_evap = H1 - H4
        n_e = Q_kW * 1000.0 / q_evap
        W = n_e * (H2 - H1) / 1000.0
        res.update({"T2": T2 - K0, "stages": 1})
        n_ref = n_e
    cop = Q_kW / W
    cop_c = Te / (Tc - Te)
    res.update({"W_kW": W, "COP": cop, "COP_carnot": cop_c, "carnot_fraction": cop / cop_c, "Q_condenser_kW": Q_kW + W,
                "m_ref_kg_h": n_ref * mw * 3.6, "Q_kW": Q_kW, "vapour_in_after_valve": (H4 - Hl_e) / (Hv_e - Hl_e)})
    if Pe < 1.0:
        res["warning"] = f"evaporating pressure {Pe:.2f} bar(a) is below atmospheric: air ingress risk, consider a lighter refrigerant"
    return res
