"""Gas hydrate equilibrium: van der Waals & Platteeuw (1959) statistical model with Kihara cell potentials
(McKoy & Sinanoğlu 1963), reference properties of Holder et al. (1980) and guest fugacities from the
Peng-Robinson EOS (Parrish & Prausnitz 1972 framework).

Structures sI and sII are both evaluated; the hydrate that is stable at the higher temperature (for a given
pressure) is reported.  Water activity is taken as 1 (free water); thermodynamic inhibitors (MEG, methanol) are
applied on top as a temperature depression, as for the Motiee correlation.

    Δμ_w^(β−H)/RT = −Σ_m ν_m ln(1 − Σ_j θ_mj),   θ_mj = C_mj f_j / (1 + Σ_k C_mk f_k)
    Δμ_w^(β−L)/RT = Δμ0/RT0 − ∫_{T0}^{T} ΔH_w/RT² dT + ΔV_w P/RT − ln a_w
    C_mj = 4π/kT ∫ exp(−w(r)/kT) r² dr      (spherical Kihara cell potential w)

The Kihara energy parameters ε/k were fitted in this app to pure-gas hydrate equilibria (Deaton & Frost 1946 and
compilations in Sloan & Koh 2008) with the core radius a and σ kept at literature values; see ``FIT_NOTE``.
"""
from __future__ import annotations

import math

import numpy as np

KB = 1.380649e-23          # J/K
R = 8.314462618
T0 = 273.15
# structure: (Δμ0 J/mol, ΔH0 ice J/mol, ΔV0 ice cm³/mol), cavities: (R Å, z, ν)
STRUCT = {      # reference properties of Dharmawardhana, Parrish & Sloan (1980)
    "sI": {"ref": (1264.0, 1389.0, 3.0), "cav": [(3.95, 20, 2.0 / 46.0), (4.33, 24, 6.0 / 46.0)]},
    "sII": {"ref": (883.0, 1025.0, 3.4), "cav": [(3.91, 20, 16.0 / 136.0), (4.73, 28, 8.0 / 136.0)]},
}
DH_FUS = 6011.0            # J/mol, ice -> water at T0
DV_FUS = 1.6               # cm³/mol, ice is less dense than water
DCP = (-38.12, 0.141)      # J/mol K, ΔCp(L) = a + b (T - T0)  (Holder et al. 1980)

# Kihara parameters: a [Å], σ [Å], ε/k [K]; cavities the guest fits: (sI small, sI large, sII small, sII large)
GUESTS = {
    "C1": (0.3834, 3.17667, 155.461, (1, 1, 1, 1)),
    "C2": (0.5651, 3.14824, 180.157, (0, 1, 0, 1)),
    "C3": (0.6502, 2.84461, 321.030, (0, 0, 0, 1)),
    "iC4": (0.8706, 3.08220, 218.588, (0, 0, 0, 1)),
    "nC4": (0.9379, 2.94830, 209.270, (0, 0, 0, 1)),
    "N2": (0.3526, 3.13512, 124.917, (1, 1, 1, 1)),
    "CO2": (0.6805, 3.07086, 167.180, (1, 1, 1, 1)),
    "H2S": (0.3600, 3.37784, 194.847, (1, 1, 1, 1)),
}
FIT_NOTE = ("Kihara core radii a from Sloan (1998). σ and ε/k fitted in this app (σ kept at the literature value "
            "where fewer than 3 points exist) to pure-gas equilibria from Deaton & Frost (1946) and the Sloan & Koh "
            "(2008) compilations: CH4 273.7-285.9 K (sI), C2H6 273.7-285.9 K (sI), C3H8 273.7-277.6 K (sII), "
            "CO2 273.7-282.9 K (sI), i-C4H10 273.2-274.5 K (sII), N2 273.2-277 K (sII), H2S 273.2-293.2 K (sI); "
            "rms 0.0-0.3 K. n-C4H10 (no simple hydrate) keeps its literature values.")
_R_PTS = 120
_CELL = {}


def _delta(N, r, Rc, a):
    return ((1.0 - r / Rc - a / Rc) ** (-N) - (1.0 + r / Rc - a / Rc) ** (-N)) / N


def langmuir(T, guests=None, structs=None):
    """{(structure, cavity index, guest): C [1/bar]} at temperature T [K]."""
    out = {}
    for s, d in STRUCT.items():
        if structs is not None and s not in structs:
            continue
        for m, (Rc, z, _) in enumerate(d["cav"]):
            for g, (a, sig, eps, fits) in GUESTS.items():
                if guests is not None and g not in guests:
                    continue
                if not fits[(0 if s == "sI" else 2) + m]:
                    continue
                ck = (Rc, z, a, sig, eps)
                hit = _CELL.get(ck)
                if hit is None:              # the cell potential's shape does not depend on T: compute it once
                    rmax = Rc - a
                    r = np.linspace(1e-4, rmax * (1.0 - 1e-6), _R_PTS)
                    w = 2.0 * z * eps * (sig ** 12 / (Rc ** 11 * r) * (_delta(10, r, Rc, a) + a / Rc * _delta(11, r, Rc, a))
                                         - sig ** 6 / (Rc ** 5 * r) * (_delta(4, r, Rc, a) + a / Rc * _delta(5, r, Rc, a)))
                    dr = np.diff(r)
                    hit = _CELL[ck] = (w, r * r, dr)
                w, r2, dr = hit
                yv = np.exp(np.clip(-w / T, -700.0, 700.0)) * r2
                integ = float(np.sum(0.5 * (yv[1:] + yv[:-1]) * dr))
                out[(s, m, g)] = 4.0 * math.pi / (KB * T) * integ * 1e-30 * 1e5     # Å³ -> m³ ; 1/Pa -> 1/bar
    return out


def dmu_L(T, P_bar, s, a_w=1.0):
    """Δμ_w^(β−L or ice)/RT of the empty lattice relative to water (liquid above T0, ice below)."""
    mu0, h0, v0 = STRUCT[s]["ref"]
    P = P_bar * 1e5
    if T >= T0:
        hL = h0 - DH_FUS
        a, b = DCP
        # ∫_{T0}^{T} [hL + a (t - T0) + b/2 (t - T0)²] / (R t²) dt, analytic
        x = T
        integ = (hL - a * T0 + b / 2 * T0 * T0) * (1.0 / T0 - 1.0 / x) + (a - b * T0) * math.log(x / T0) + b / 2 * (x - T0)
        integ /= R
        dv = (v0 + DV_FUS) * 1e-6
    else:
        integ = h0 * (1.0 / T0 - 1.0 / T) / R
        dv = v0 * 1e-6
    return mu0 / (R * T0) - integ + dv * P / (R * T) - math.log(a_w)


def dmu_H(C, f, s):
    """Δμ_w^(β−H)/RT from the Langmuir constants and the guest fugacities [bar]."""
    tot = 0.0
    for m, (_, _, nu) in enumerate(STRUCT[s]["cav"]):
        sc = sum(C.get((s, m, g), 0.0) * fg for g, fg in f.items())
        theta = sc / (1.0 + sc)
        tot += -nu * math.log(max(1.0 - theta, 1e-300))
    return tot


def _fugacities(fp, y, T, P):
    """Guest fugacities [bar] of a (water-free) vapour composition from the PR EOS."""
    idx = np.nonzero(y > 1e-12)[0]
    yl = y[idx] / y[idx].sum()
    lp, _, _ = fp.lnphi(yl, T, P, idx, "V")
    f = {}
    for k, i in enumerate(idx):
        key = fp.keys[i]
        if key in GUESTS:
            f[key] = float(yl[k] * math.exp(lp[k]) * P)
    return f


def _dry(fp, x):
    y = np.asarray(x, float).copy()
    for k in ("H2O", "MeOH", "MEG"):
        if k in fp.keys:
            y[fp.index(k)] = 0.0
    s = y.sum()
    return y / s if s > 0 else y


def equilibrium_T(fp, x, P_bar, a_w=1.0, T_guess=None):
    """(hydrate formation temperature [K], structure) of a gas at P [bar]; (None, None) if it forms no hydrate
    in 230-320 K."""
    y = _dry(fp, x)
    present = {fp.keys[i] for i in np.nonzero(y > 1e-12)[0]} & set(GUESTS)
    if not present:
        return None, None
    from scipy.optimize import brentq
    best = (None, None)
    for s in STRUCT:
        def g(T):
            C = langmuir(T, present, (s,))
            f = _fugacities(fp, y, T, P_bar)
            return dmu_H(C, f, s) - dmu_L(T, P_bar, s, a_w)
        lo, hi = 230.0, 320.0
        bracketed = False
        if T_guess is not None:          # bracket around a guess first (cheaper)
            a_, b_ = T_guess - 3.0, T_guess + 3.0
            if g(a_) > 0 > g(b_):
                lo, hi, bracketed = a_, b_, True
        if not bracketed and (g(lo) < 0 or g(hi) > 0):   # never stable, or stable even at 320 K (outside the range)
            continue
        Tm = brentq(g, lo, hi, xtol=2e-3)
        if best[0] is None or Tm > best[0]:
            best = (Tm, s)
    return best


def equilibrium_P(fp, x, T, a_w=1.0, P_lo=0.5, P_hi=2000.0):
    """(hydrate equilibrium pressure [bar], structure) at T [K]; (None, None) outside the range."""
    y = _dry(fp, x)
    present = {fp.keys[i] for i in np.nonzero(y > 1e-12)[0]} & set(GUESTS)
    if not present:
        return None, None
    C = langmuir(T, present)
    best = (None, None)
    for s in STRUCT:
        def g(lnP):
            P = math.exp(lnP)
            return dmu_H(C, _fugacities(fp, y, T, P), s) - dmu_L(T, P, s, a_w)
        lo, hi = math.log(P_lo), math.log(P_hi)
        if g(lo) > 0 or g(hi) < 0:
            continue
        for _ in range(50):
            mid = 0.5 * (lo + hi)
            if g(mid) > 0:
                hi = mid
            else:
                lo = mid
            if hi - lo < 1e-5:
                break
        P = math.exp(0.5 * (lo + hi))
        if best[0] is None or P < best[0]:
            best = (P, s)
    return best


_CURVES = {}
_P_GRID = np.exp(np.linspace(math.log(2.0), math.log(600.0), 18))


def hydrate_T_C(fp, x, P_bar):
    """Hydrate formation temperature [°C] of a gas composition at P (interpolated on a cached curve per
    composition); None outside 2-600 bar or for gases without hydrate formers."""
    if not (2.0 <= P_bar <= 600.0):
        return None
    y = _dry(fp, x)
    key = (tuple(fp.keys), tuple(np.round(y, 3)))
    cur = _CURVES.get(key)
    if cur is None:
        Ts = []
        Tg = None
        for P in _P_GRID:
            T, _ = equilibrium_T(fp, y, float(P), T_guess=Tg)
            Tg = T
            Ts.append(np.nan if T is None else T - T0)
        cur = np.array(Ts)
        if len(_CURVES) > 256:
            _CURVES.clear()
        _CURVES[key] = cur
    lp = np.log(_P_GRID)
    ok = ~np.isnan(cur)
    if ok.sum() < 2:
        return None
    t = float(np.interp(math.log(P_bar), lp[ok], cur[ok]))
    if math.log(P_bar) < lp[ok][0] or math.log(P_bar) > lp[ok][-1]:
        return None
    return t
