"""TEG gas dehydration contactor (screening).

The wet gas enters an inlet scrubber (free liquids leave with the water outlet), then contacts lean triethylene
glycol in N theoretical stages:

* equilibrium water mole fraction over lean TEG:   y_eq = γ_w x_w P_sat,w(T) / P
  (x_w from the lean TEG purity in wt %, γ_w = 1.0, which reproduces the GPSA equilibrium dew-point chart within ~3 K);
* Kremser absorption with A = L / (K V), K = γ_w P_sat / P:   (y_in − y_out)/(y_in − y_eq) = (A^(N+1) − A)/(A^(N+1) − 1);
* TEG circulation from the specific rate [L TEG per kg of water removed]; rich-TEG water loading is checked;
* water dew point of the dried gas from the Peng-Robinson EOS (temperature where free water first appears at the
  contactor pressure);
* regenerator reboiler duty: water vaporisation + heating the circulating TEG from the lean/rich exchanger outlet
  (150 °C) to the reboiler (204 °C) + 10 % losses.

TEG itself is not a component of the fluid package: the water outlet carries the water removed (the glycol loop is
closed) and TEG losses are not modelled.
"""
from __future__ import annotations

import math

import numpy as np

from .streams import make_stream, zero_stream, EnergyStream
from .unitops import UnitError, K0, _f, _s, _ph

MW_TEG, MW_W, RHO_TEG = 150.17, 18.015, 1120.0
GAMMA_W = 1.0       # activity coefficient of water in lean TEG (matches the GPSA equilibrium-dew-point chart)


def psat_water_bar(T_K):
    """Saturation pressure of water [bar] (Wagner-Pruss style fit, 273-473 K; Buck over ice below 273.15 K)."""
    T = T_K
    if T < 273.15:
        t = T - 273.15
        return 0.0061115 * math.exp((23.036 - t / 333.7) * (t / (279.82 + t)))
    Tc, Pc = 647.096, 220.64
    tau = 1.0 - T / Tc
    a = (-7.85951783, 1.84408259, -11.7866497, 22.6807411, -15.9618719, 1.80122502)
    s = (a[0] * tau + a[1] * tau ** 1.5 + a[2] * tau ** 3 + a[3] * tau ** 3.5 + a[4] * tau ** 4 + a[5] * tau ** 7.5)
    return Pc * math.exp(Tc / T * s)


def x_water(teg_wt):
    w = max(0.0, min(100.0, teg_wt)) / 100.0
    nw, nt = (1.0 - w) / MW_W, w / MW_TEG
    return nw / (nw + nt) if (nw + nt) > 0 else 0.0


def kremser_fraction(A, N):
    """Fraction of the possible absorption achieved by N theoretical stages at absorption factor A."""
    if abs(A - 1.0) < 1e-9:
        return N / (N + 1.0)
    return (A ** (N + 1) - A) / (A ** (N + 1) - 1.0)


def water_dew_point(fp, z, P, T_hi=330.0, T_lo=200.0):
    """Water dew point [°C] of a gas at P from the EOS (highest T where an aqueous phase forms); None if none."""
    iw = fp.iw
    if iw < 0 or z[iw] <= 0:
        return None

    def wet(T):
        try:
            fr = fp.pt_flash(z, T, P)
        except Exception:                                     # noqa: BLE001
            return False
        return fr.phase("W") is not None
    if not wet(T_lo):
        return None
    if wet(T_hi):
        return T_hi - K0
    lo, hi = T_lo, T_hi
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        if wet(mid):
            lo = mid
        else:
            hi = mid
        if hi - lo < 0.02:
            break
    return 0.5 * (lo + hi) - K0


SCHEMA = {
    "teg_contactor": {
        "label": "TEG dehydration contactor", "prefix": "T", "category": "Separation",
        "ports": {"in": {"feed": {"multi": True}}, "out": {"dry": {"multi": False}, "water": {"multi": False}}},
        "params": [
            _f("teg_wt", "Lean TEG purity", "wt%", 99.5, minv=95.0, maxv=99.99,
               help="Glycol purity after regeneration: ~98.5-99.1 % atmospheric reboiler, 99.5-99.9 % with stripping gas"),
            _f("circ", "TEG circulation", "L TEG / kg water", 25.0, minv=5.0, maxv=100.0,
               help="Typical 15-40 L/kg (2-5 US gal/lb)"),
            _f("stages", "Theoretical stages", "-", 2.0, minv=1.0, maxv=6.0,
               help="About 3-4 actual bubble-cap trays (or ~1.5 m structured packing) per theoretical stage"),
            _f("dP", "Pressure drop", "bar", 0.3, minv=0.0),
            _f("dew_spec", "Water dew point specification (0 = none)", "°C", 0.0,
               help="Warns when the dried gas does not meet it (at the contactor pressure)"),
        ],
    },
}


def calc_teg_contactor(unit, ins, fp):
    p = unit["params"]
    live = [s for s in ins.get("feed", []) if not s.empty]
    if not live:
        z = ins["feed"][0].z if ins.get("feed") else None
        return {"dry": [zero_stream("", fp, z)], "water": [zero_stream("", fp, z)]}, {"Status": "No flow"}, []
    if fp.iw < 0:
        raise UnitError(f"{unit['name']}: the fluid package has no water")
    F = sum(s.F for s in live)
    z = sum(s.F * s.z for s in live) / F
    H = sum(s.F * s.H for s in live) / F
    P = min(s.P for s in live) - float(p.get("dP", 0.0))
    if P <= 1.0:
        raise UnitError(f"{unit['name']}: contactor pressure {P:.2f} bar is too low")
    fr = _ph(fp, z, P, H, live[0].T)
    T = fr.T
    v = fr.phase("V")
    if v is None:
        raise UnitError(f"{unit['name']}: no gas to dehydrate")
    iw = fp.iw
    Fv = F * v.beta
    yv = v.x.copy()
    free = F * z - Fv * yv                                  # liquids knocked out in the inlet scrubber (kmol/h)
    y_in = yv[iw]
    xw = x_water(float(p["teg_wt"]))
    Psat = psat_water_bar(T)
    K = GAMMA_W * Psat / P
    y_eq = K * xw
    N = float(p["stages"])
    W_in = Fv * y_in * MW_W                                  # kg/h of water in the gas
    L_teg = float(p["circ"]) * max(W_in, 1e-9) / 1000.0 * RHO_TEG / MW_TEG      # kmol/h of TEG
    A = L_teg / (K * Fv) if K > 0 else 1e9
    frac = kremser_fraction(A, N)
    y_out = max(y_eq, y_in - (y_in - y_eq) * frac) if y_in > y_eq else y_in
    n_rem = Fv * (y_in - y_out) / max(1.0 - y_out, 1e-12)   # kmol/h water absorbed
    dry_n = Fv * yv
    dry_n[iw] -= n_rem
    dry_n = np.maximum(dry_n, 0.0)
    wat_n = free.copy()
    wat_n[iw] += n_rem
    wat_n = np.maximum(wat_n, 0.0)
    Fd, Fw = float(dry_n.sum()), float(wat_n.sum())
    zd = dry_n / Fd
    # energy: the dried gas leaves at the contactor T; the water stream carries the rest of the enthalpy
    frd = fp.pt_flash(zd, T, P)
    dry = make_stream("", fp, Fd, zd, frd)
    outs = {"dry": [dry]}
    if Fw > 1e-12:
        zw = wat_n / Fw
        Hw = (F * H - Fd * frd.H) / Fw
        frw = _ph(fp, zw, P, Hw, T)
        outs["water"] = [make_stream("", fp, Fw, zw, frw)]
    else:
        outs["water"] = [zero_stream("", fp, z, T, P)]
    W_rem = n_rem * MW_W                                     # kg/h
    L_m3h = float(p["circ"]) * W_rem / 1000.0
    Q = (W_rem * 2260.0 + L_m3h * RHO_TEG * 2.5 * (204.0 - 150.0)) / 3600.0 * 1.10   # kW
    rich_wt = 100.0 * (L_m3h * RHO_TEG * float(p["teg_wt"]) / 100.0) / (L_m3h * RHO_TEG + W_rem) if L_m3h > 0 else None
    V_std = Fv * 23.645 * 24.0 / 1e6                         # MSm³/d
    dp = water_dew_point(fp, zd, P)
    dp_eq = water_dew_point(fp, np.where(np.arange(fp.n) == iw, y_eq, yv * (1 - y_eq) / max(1 - y_in, 1e-12)), P)
    res = {"Contactor P [bar(a)]": P, "Contactor T [°C]": T - K0, "Gas flow [MSm³/d]": V_std,
           "Water in [mg/Sm³]": y_in * MW_W * 1e6 / 23.645, "Water out [mg/Sm³]": y_out * MW_W * 1e6 / 23.645,
           "Equilibrium water over lean TEG [mg/Sm³]": y_eq * MW_W * 1e6 / 23.645,
           "Absorption factor A [-]": A, "Approach to equilibrium [%]": 100.0 * frac,
           "Water removed [kg/h]": W_rem, "TEG circulation [m³/h]": L_m3h, "Rich TEG purity [wt%]": rich_wt,
           "Free liquid knocked out [kmol/h]": float(free.sum()),
           "Water dew point of dried gas [°C]": dp, "Equilibrium dew point (infinite stages) [°C]": dp_eq,
           "Reboiler duty [kW]": Q}
    spec = float(p.get("dew_spec", 0.0) or 0.0)
    if spec and dp is not None and dp > spec:
        res["Warning"] = (f"dried gas water dew point {dp:.1f} °C misses the {spec:.1f} °C specification: "
                          "raise the TEG purity, the circulation or the stages")
    return outs, res, [EnergyStream(f"Q-{unit['name']} reboiler", Q, f"{unit['name']} regenerator", "heat")]


def register():
    from .unitops import CATALOGUE, CALC
    CATALOGUE.update(SCHEMA)
    CALC.update({"teg_contactor": calc_teg_contactor})


register()
