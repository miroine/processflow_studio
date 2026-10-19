"""Pure-component library for the Peng-Robinson fluid package.

Sources
-------
* Tc, Pc, omega, MW: Poling, Prausnitz & O'Connell, *The Properties of Gases and
  Liquids*, 5th ed., Appendix A.
* Ideal-gas Cp polynomials Cp = A + B*T + C*T^2 + D*T^3 [J/mol/K, T in K]:
  Reid, Prausnitz & Poling, 4th ed., Appendix A.
* Rackett Z_RA (used for the Peneloux volume shift): Reid, Prausnitz & Poling
  Table 3-11 / Spencer & Danner.
* Standard liquid densities at 15 degC (ideal-mixing "Liq Vol Flow @Std Cond"):
  GPA 2145 table values (C1/C2 are GPA "equivalent liquid" values).
* Hypothetical (petroleum-fraction) components: Kesler & Lee (1976) for Tc, Pc and
  omega; Riazi & Daubert (1980) for MW when not given; ideal-gas Cp from the
  mass-specific n-paraffin series scaled by MW.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict


@dataclass
class Component:
    key: str
    name: str
    Tc: float          # K
    Pc: float          # bar
    omega: float
    MW: float          # kg/kmol
    cp: tuple          # (A, B, C, D) J/mol/K, T in K
    zra: float         # Rackett Z_RA for Peneloux shift
    rho_std: float     # kg/m3 at 15 degC (standard liquid density)
    family: str = "hydrocarbon"   # hydrocarbon | inert | acid | water | alcohol | hypo
    hypo: dict = field(default_factory=dict)  # NBP/SG for hypothetical components
    Vc: float = 0.0    # critical volume cm3/mol (0 -> estimated from Zc = 0.2905 - 0.085 omega)
    cas: str = ""
    formula: str = ""
    # EOS calibration (v7.5): 0 = use the standard alpha-function correlation from omega / no extra shift
    m_pr: float = 0.0       # PR alpha-function parameter m, overrides the omega correlation when > 0
    vshift: float = 0.0     # extra Peneloux volume shift [cm3/mol] added to the Rackett-based shift
    note: str = ""          # e.g. "calibrated 2026-10-07 on 12 points"

    @property
    def Vc_est(self):
        """Critical volume cm3/mol (tabulated, else from Zc = 0.2905 - 0.085*omega)."""
        if self.Vc > 0:
            return self.Vc
        zc = 0.2905 - 0.085 * self.omega
        return zc * 83.14462618 * self.Tc / self.Pc

    def to_dict(self):
        d = asdict(self)
        d["cp"] = list(self.cp)
        return d

    @staticmethod
    def from_dict(d):
        d = dict(d)
        d["cp"] = tuple(d["cp"])
        d.pop("Vc_est", None)
        return Component(**d)


_LIB = [
    # key, name, Tc K, Pc bar, omega, MW, Cp(A,B,C,D), ZRA, rho_std, family
    ("N2", "Nitrogen", 126.20, 33.98, 0.037, 28.014, (31.15, -1.357e-2, 2.680e-5, -1.168e-8), 0.2900, 808.0, "inert"),
    ("CO2", "Carbon dioxide", 304.12, 73.74, 0.225, 44.010, (19.80, 7.344e-2, -5.602e-5, 1.715e-8), 0.2722, 818.0, "acid"),
    ("H2S", "Hydrogen sulphide", 373.20, 89.37, 0.090, 34.082, (31.94, 1.436e-3, 2.432e-5, -1.176e-8), 0.2855, 801.0, "acid"),
    ("H2O", "Water", 647.14, 220.64, 0.344, 18.015, (32.24, 1.924e-3, 1.055e-5, -3.596e-9), 0.2338, 999.1, "water"),
    ("C1", "Methane", 190.56, 45.99, 0.011, 16.043, (19.25, 5.213e-2, 1.197e-5, -1.132e-8), 0.2892, 300.0, "hydrocarbon"),
    ("C2", "Ethane", 305.32, 48.72, 0.099, 30.070, (5.409, 1.781e-1, -6.938e-5, 8.713e-9), 0.2808, 356.0, "hydrocarbon"),
    ("C3", "Propane", 369.83, 42.48, 0.152, 44.097, (-4.224, 3.063e-1, -1.586e-4, 3.215e-8), 0.2766, 507.0, "hydrocarbon"),
    ("iC4", "i-Butane", 408.14, 36.48, 0.177, 58.123, (-1.390, 3.847e-1, -1.846e-4, 2.895e-8), 0.2754, 563.0, "hydrocarbon"),
    ("nC4", "n-Butane", 425.12, 37.96, 0.200, 58.123, (9.487, 3.313e-1, -1.108e-4, -2.822e-9), 0.2730, 584.0, "hydrocarbon"),
    ("iC5", "i-Pentane", 460.43, 33.81, 0.227, 72.150, (-9.525, 5.066e-1, -2.729e-4, 5.723e-8), 0.2717, 625.0, "hydrocarbon"),
    ("nC5", "n-Pentane", 469.70, 33.70, 0.252, 72.150, (-3.626, 4.873e-1, -2.580e-4, 5.305e-8), 0.2684, 631.0, "hydrocarbon"),
    ("nC6", "n-Hexane", 507.60, 30.25, 0.300, 86.177, (-4.413, 5.820e-1, -3.119e-4, 6.494e-8), 0.2635, 664.0, "hydrocarbon"),
    ("nC7", "n-Heptane", 540.20, 27.40, 0.350, 100.204, (-5.146, 6.762e-1, -3.651e-4, 7.658e-8), 0.2604, 688.0, "hydrocarbon"),
    ("nC8", "n-Octane", 568.70, 24.90, 0.399, 114.231, (-6.096, 7.712e-1, -4.195e-4, 8.855e-8), 0.2571, 707.0, "hydrocarbon"),
    ("nC9", "n-Nonane", 594.60, 22.90, 0.445, 128.258, (-8.374, 8.729e-1, -4.823e-4, 1.031e-7), 0.2543, 722.0, "hydrocarbon"),
    ("nC10", "n-Decane", 617.70, 21.10, 0.490, 142.285, (-7.913, 9.609e-1, -5.288e-4, 1.131e-7), 0.2507, 734.0, "hydrocarbon"),
    ("O2", "Oxygen", 154.58, 50.43, 0.022, 31.999, (28.11, -3.680e-6, 1.746e-5, -1.065e-8), 0.2908, 1142.0, "inert"),
    ("H2", "Hydrogen", 33.19, 13.13, -0.216, 2.016, (27.14, 9.274e-3, -1.381e-5, 7.645e-9), 0.3218, 71.0, "inert"),
    ("MeOH", "Methanol", 512.64, 80.97, 0.565, 32.042, (21.15, 7.092e-2, 2.587e-5, -2.852e-8), 0.2334, 796.0, "alcohol"),
    ("MEG", "Ethylene glycol (MEG)", 720.0, 82.0, 0.5068, 62.068, (35.70, 2.483e-1, -1.497e-4, 3.010e-8), 0.2466, 1116.0,
     "alcohol"),
]

# critical volumes cm3/mol (Poling, Prausnitz & O'Connell, Appendix A) - used by the LBC viscosity
_VC = {'N2': 89.2, 'CO2': 94.07, 'H2S': 98.5, 'H2O': 55.95, 'C1': 98.6, 'C2': 145.5, 'C3': 200.0, 'iC4': 262.7, 'nC4': 255.0, 'iC5': 306.0, 'nC5': 313.0, 'nC6': 371.0, 'nC7': 428.0, 'nC8': 486.0, 'nC9': 544.0, 'nC10': 600.0, 'O2': 73.37, 'H2': 64.14, 'MeOH': 118.0, 'MEG': 191.0}

LIBRARY: dict[str, Component] = {
    r[0]: Component(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], Vc=_VC[r[0]]) for r in _LIB
}
BASE_KEYS = tuple(LIBRARY)                  # the original 20 components (kept first in every list)

# ---- extended library (ChemSep-derived, see components_ext.py) -------------------------------------------------------
from . import components_ext as _X          # noqa: E402

for _r in _X.ROWS:
    if _r[0] not in LIBRARY:
        LIBRARY[_r[0]] = Component(_r[0], _r[1], _r[2], _r[3], _r[4], _r[5], tuple(_r[6]), _r[7], _r[8], _r[9], Vc=_r[10],
                                   cas=_r[11], formula=_r[12])
POLAR_FAMILIES = ("water", "alcohol", "glycol", "amine")     # components that go to the aqueous phase when water is present


def library_keys():
    return list(LIBRARY.keys())


# ----------------------------------------------------------------------------
# Hypothetical components (petroleum fractions)
# ----------------------------------------------------------------------------

def riazi_daubert_mw(tb_k: float, sg: float) -> float:
    """Riazi & Daubert (1980) molecular weight from normal boiling point and SG."""
    return 42.965 * math.exp(2.097e-4 * tb_k - 7.78712 * sg + 2.08476e-3 * tb_k * sg) \
        * tb_k ** 1.26007 * sg ** 4.98308


def kesler_lee_critical(tb_k: float, sg: float):
    """Kesler & Lee (1976) Tc [K], Pc [bar], omega from NBP [K] and SG."""
    tb = tb_k * 1.8  # degR
    tc_r = 341.7 + 811.0 * sg + (0.4244 + 0.1174 * sg) * tb + (0.4669 - 3.2623 * sg) * 1e5 / tb
    ln_pc = (8.3634 - 0.0566 / sg
             - (0.24244 + 2.2898 / sg + 0.11857 / sg ** 2) * 1e-3 * tb
             + (1.4685 + 3.648 / sg + 0.47227 / sg ** 2) * 1e-7 * tb ** 2
             - (0.42019 + 1.6977 / sg ** 2) * 1e-10 * tb ** 3)
    pc_psia = math.exp(ln_pc)
    tbr = tb / tc_r
    kw = tb ** (1.0 / 3.0) / sg
    if tbr < 0.8:
        omega = ((-math.log(pc_psia / 14.696) - 5.92714 + 6.09648 / tbr + 1.28862 * math.log(tbr)
                  - 0.169347 * tbr ** 6)
                 / (15.2518 - 15.6875 / tbr - 13.4721 * math.log(tbr) + 0.43577 * tbr ** 6))
    else:
        omega = -7.904 + 0.1352 * kw - 0.007465 * kw ** 2 + 8.359 * tbr + (1.408 - 0.01063 * kw) / tbr
    return tc_r / 1.8, pc_psia * 0.0689476, omega


def paraffin_analogue_cp_poly(mw: float):
    """Ideal-gas Cp polynomial for a petroleum cut from the n-paraffin series.

    Mass-specific ideal-gas Cp of the n-paraffins is nearly independent of
    carbon number, so the cut takes the per-mass polynomial interpolated (in MW)
    between the bracketing library n-paraffins (nC5..nC10, extrapolated with
    nC10 above), multiplied by its own MW.  Aromatic/naphthenic cuts will be
    overestimated by a few percent.
    """
    series = [LIBRARY[k] for k in ("nC5", "nC6", "nC7", "nC8", "nC9", "nC10")]
    if mw <= series[0].MW:
        lo = hi = series[0]
    elif mw >= series[-1].MW:
        lo = hi = series[-1]
    else:
        lo = max((c for c in series if c.MW <= mw), key=lambda c: c.MW)
        hi = min((c for c in series if c.MW >= mw), key=lambda c: c.MW)
    w = 0.0 if hi.MW == lo.MW else (mw - lo.MW) / (hi.MW - lo.MW)
    per_mass = [(1 - w) * a / lo.MW + w * b / hi.MW for a, b in zip(lo.cp, hi.cp)]
    return tuple(float(c * mw) for c in per_mass)


def make_hypothetical(key: str, name: str, tb_c: float, sg: float, mw: float | None = None) -> Component:
    tb_k = tb_c + 273.15
    if not mw:
        mw = riazi_daubert_mw(tb_k, sg)
    tc, pc, omega = kesler_lee_critical(tb_k, sg)
    cp = paraffin_analogue_cp_poly(float(mw))
    zra = 0.29056 - 0.08775 * omega
    return Component(key, name, tc, pc, omega, float(mw), cp, zra, sg * 999.0, "hypo",
                     {"NBP_C": tb_c, "SG": sg})


# ----------------------------------------------------------------------------
# Default binary interaction parameters (PR)
# ----------------------------------------------------------------------------

def default_kij(a: Component, b: Component) -> float:
    fa, fb = a.family, b.family
    pair = {a.key, b.key}
    if a.key == b.key:
        return 0.0
    if "H2O" in pair:
        other = b if a.key == "H2O" else a
        if other.family in ("alcohol", "glycol") and other.key not in ("MeOH", "MEG"):
            return -0.07 if other.family == "alcohol" else -0.06
        if other.family == "amine":
            return -0.10
        if other.key == "CO2":
            return 0.19
        if other.key == "H2S":
            return 0.12
        if other.key == "MeOH":
            return -0.07
        if other.key == "MEG":
            return -0.063
        if other.key == "N2":
            return 0.48
        return 0.50
    if pair == {"CO2", "N2"}:
        return -0.02
    if pair == {"CO2", "H2S"}:
        return 0.10
    if pair == {"N2", "H2S"}:
        return 0.17
    if pair == {"MeOH", "MEG"}:
        return 0.0
    if "MEG" in pair:
        return 0.20      # keeps glycol out of the hydrocarbon liquid, as observed in practice
    if fa in ("glycol",) or fb in ("glycol",):
        return 0.0 if (fa in POLAR_FAMILIES and fb in POLAR_FAMILIES) else 0.20
    if fa in ("alcohol", "amine") or fb in ("alcohol", "amine"):
        if fa in POLAR_FAMILIES and fb in POLAR_FAMILIES:
            return 0.0
        other = b if fa in ("alcohol", "amine") else a
        if other.key in ("CO2", "H2S"):
            return 0.05
        return 0.10
    if "MeOH" in pair:
        return 0.05
    if "CO2" in pair:
        other = b if a.key == "CO2" else a
        return 0.10 if other.key == "C1" else 0.12
    if "H2S" in pair:
        other = b if a.key == "H2S" else a
        return 0.08 if other.key == "C1" else 0.07
    if "N2" in pair:
        other = b if a.key == "N2" else a
        if other.key == "C1":
            return 0.025
        if other.key == "C2":
            return 0.04
        return 0.08
    if fa in ("inert",) or fb in ("inert",):
        return 0.0
    # hydrocarbon-hydrocarbon: small for C1 with heavies (Chueh-Prausnitz style is overkill here)
    if "C1" in pair:
        other = b if a.key == "C1" else a
        if other.family == "hypo" or other.MW > 100:
            return 0.02
    return 0.0
