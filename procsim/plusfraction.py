"""Plus-fraction characterisation: split a C7+ (or Cn+) lump into single-carbon-number fractions and group them into a
few pseudo-components.

Distribution - Pedersen et al. exponential decay, ln z_n = A + B n, with molar mass M_n = 14 n - 4 (a CH2 chain with end
               groups).  B is found so that the mean molar mass equals the measured plus-fraction molar mass, A so that
               the mole fractions add up to the plus-fraction mole fraction (tail cut off at ``n_max``).
Density      - Soreide (1989): SG_n = 0.2855 + C (M_n - 66)^0.13, with C chosen so that the plus fraction reproduces its
               measured specific gravity.
Grouping     - equal mass in each pseudo-component; the pseudo-component MW is the mole average, the SG the mass/volume
               average and the boiling point from Soreide's correlation (feeds Kesler-Lee in ``make_hypothetical``).
The split conserves mole fraction, molar mass and density of the plus fraction by construction (see tests)."""
from __future__ import annotations

import math

import numpy as np
from scipy.optimize import brentq


def _mw(n):
    return 14.0 * np.asarray(n, float) - 4.0


def soreide_tb_K(M, sg):
    """Normal boiling point [K] (Soreide 1989)."""
    TbR = 1928.3 - 1.695e5 * M ** -0.03522 * sg ** 3.266 * math.exp(-4.922e-3 * M - 4.7685 * sg + 3.462e-3 * M * sg)
    return TbR / 1.8


def split(z_plus: float, mw_plus: float, sg_plus: float, n_start: int = 7, n_max: int = 80, n_groups: int = 4,
          key_prefix: str = "C") -> dict:
    """Return dict(groups=[{key,name,z,MW,SG,Tb_C,n_lo,n_hi}], scn=[{n,z,MW,SG}], checks={...})."""
    if not (z_plus > 0):
        raise ValueError("The plus-fraction mole fraction must be positive.")
    ns = np.arange(n_start, n_max + 1)
    M = _mw(ns)
    if not (M[0] < mw_plus < M[-1] * 0.8):
        raise ValueError(f"Plus-fraction molar mass {mw_plus:g} is outside the range this split can represent "
                         f"({M[0] + 5:.0f}–{0.8 * M[-1]:.0f} for C{n_start}+).")
    if not (0.6 < sg_plus < 1.1):
        raise ValueError("Plus-fraction SG must be between 0.6 and 1.1.")

    def mean_mw(B):
        w = np.exp(B * (ns - n_start))
        return float((w @ M) / w.sum())
    B = brentq(lambda b: mean_mw(b) - mw_plus, -2.0, 0.5)
    w = np.exp(B * (ns - n_start))
    z = z_plus * w / w.sum()

    def sg_plus_of(C):
        sg = 0.2855 + C * (M - 66.0) ** 0.13
        return float((z @ M) / (z * M / sg).sum())
    C = brentq(lambda c: sg_plus_of(c) - sg_plus, 0.01, 0.9)
    sg = 0.2855 + C * (M - 66.0) ** 0.13
    scn = [{"n": int(n), "z": float(zi), "MW": float(m), "SG": float(s)} for n, zi, m, s in zip(ns, z, M, sg)]
    # equal-mass groups
    mass = z * M
    cum = np.cumsum(mass) / mass.sum()
    ng = max(1, min(int(n_groups), len(ns)))
    edges = [int(np.searchsorted(cum, (g + 1) / ng, side="left")) for g in range(ng - 1)]
    bounds, lo = [], 0
    for e in edges:
        e = max(e, lo)
        if e >= len(ns) - 1:
            break
        bounds.append((lo, e))
        lo = e + 1
    bounds.append((lo, len(ns) - 1))
    groups = []
    for lo, hi in bounds:
        sl = slice(lo, hi + 1)
        zg = float(z[sl].sum())
        mg = float((z[sl] @ M[sl]) / zg)
        sgg = float((z[sl] @ M[sl]) / (z[sl] * M[sl] / sg[sl]).sum())
        tb = soreide_tb_K(mg, sgg)
        nlo, nhi = int(ns[lo]), int(ns[hi])
        groups.append({"key": f"{key_prefix}{nlo}-{nhi}", "name": f"C{nlo}–C{nhi} pseudo", "z": zg, "MW": mg, "SG": sgg,
                       "Tb_C": tb - 273.15, "n_lo": nlo, "n_hi": nhi})
    zt = sum(g["z"] for g in groups)
    mw_g = sum(g["z"] * g["MW"] for g in groups) / zt
    vol = sum(g["z"] * g["MW"] / g["SG"] for g in groups)
    sg_g = sum(g["z"] * g["MW"] for g in groups) / vol
    return {"groups": groups, "scn": scn, "B": float(B), "C": float(C),
            "checks": {"z": zt, "MW": mw_g, "SG": sg_g, "z_target": z_plus, "MW_target": mw_plus,
                       "SG_target": sg_plus}}
