"""Sales-gas quality of a stream: gross calorific value, relative density and Wobbe index (ideal-gas basis, dry).

GCV is computed from the heats of combustion of the library components (ΔHf data of ``process_units``) plus the latent
heat of the water formed (44.0 kJ/mol); hypothetical cuts use 44 MJ/kg LHV and 6 % more for the water.  Standard
conditions are the app's 15 °C / 1.01325 bar.  Water, glycol and methanol are left out (dry basis)."""
from __future__ import annotations

import math

import numpy as np

H2O_LATENT = 44.01e3          # J/mol at 25 °C
M_AIR = 28.9647
DRY_EXCLUDE = ("H2O", "MEG", "MeOH")


def gcv_J_mol(key, MW=None):
    """Gross (higher) heating value of a component [J/mol]."""
    from .process_units import FORMULA, lhv_J_mol
    lhv = lhv_J_mol(key, MW)
    if key in FORMULA:
        return lhv + FORMULA[key][1] / 2.0 * H2O_LATENT
    return lhv * 1.06


def quality(fp, z):
    """dict of GCV [MJ/Sm³], relative density, Wobbe index [MJ/Sm³], CO₂ [mol% dry], H₂S [ppmv dry], or None for an
    empty / non-hydrocarbon composition."""
    from .thermo import V_STD_GAS
    z = np.asarray(z, float).copy()
    for k in DRY_EXCLUDE:
        if k in fp.keys:
            z[fp.keys.index(k)] = 0.0
    tot = float(z.sum())
    if tot <= 1e-12:
        return None
    y = z / tot
    gcv_mol = sum(float(y[i]) * gcv_J_mol(k, float(fp.MW[i])) for i, k in enumerate(fp.keys) if y[i] > 0)   # J/mol
    gcv = gcv_mol * 1000.0 / V_STD_GAS / 1e6                                                                  # MJ/Sm3
    MW = float(y @ fp.MW)
    rd = MW / M_AIR
    co2 = 100.0 * float(y[fp.keys.index("CO2")]) if "CO2" in fp.keys else 0.0
    h2s = 1e6 * float(y[fp.keys.index("H2S")]) if "H2S" in fp.keys else 0.0
    return {"GCV (dry) [MJ/Sm³]": gcv, "Relative density (air = 1)": rd,
            "Wobbe index [MJ/Sm³]": gcv / math.sqrt(rd) if rd > 0 else None,
            "CO₂ (dry) [mol%]": co2, "H₂S (dry) [ppmv]": h2s}
