"""Building blocks of the dynamic simulation (v7.3): holdup thermodynamics and flow laws.

* ``uv_flash`` - temperature and pressure of a closed volume from its component inventory N [kmol], volume V [m3]
  and internal energy U [kJ] (Peng-Robinson, built on the package's rigorous PT flash).
* vessel geometry - liquid level from liquid volume (horizontal or vertical cylinder).
* flow laws - control valve (IEC-style, gas or liquid), gas / liquid orifice (API 520 / ISO 4126), pump head curve.

Units: N kmol, V m3, U kJ, T K, P bar(a), H J/mol (= kJ/kmol), flows kmol/h unless stated.
"""
from __future__ import annotations

import math

import numpy as np

R = 8.314462618                    # J/mol/K
G = 9.80665


class UVError(RuntimeError):
    pass


# ---------------------------------------------------------------------------------------------- holdup state

def phase_data(fr, Ntot):
    """{kind: dict(n kmol, vol m3, x, H J/mol, MW, rho kg/m3, k Cp/Cv, Z)} for the phases of a flash result."""
    out = {}
    for p in fr.phases:
        n = p.beta * Ntot
        out[p.kind] = {"n": n, "vol": n * 1000.0 * p.Vs, "x": p.x, "H": p.H, "MW": p.MW,
                       "rho": p.MW / 1000.0 / p.Vs, "k": p.Cp / max(p.Cv, 1e-9), "Z": p.Z, "Vs": p.Vs}
    return out


def vol_energy(fp, z, Ntot, T, P, K0=None):
    """(flash, V [m3], U [kJ]) of Ntot kmol of composition z at (T, P)."""
    fr = fp.pt_flash(z, T, P, K0)
    Vm = sum(p.beta * p.Vs for p in fr.phases)
    V = Ntot * 1000.0 * Vm
    U = Ntot * fr.H - 100.0 * P * V
    return fr, V, U


def uv_flash(fp, N, V, U, T0, P0, K0=None, J0=None, tol_v=1e-9, tol_t=1e-6, max_iter=30):
    """Solve (T, P) for given inventory N [kmol per component], volume V [m3] and internal energy U [kJ].

    Newton / Broyden iteration on (T, ln P), started from the previous state (T0, P0) and, optionally, the previous
    Jacobian J0.  Returns (flash, T, P, J).  Raises UVError when it cannot converge (typically a liquid-full volume,
    where the pressure no longer follows the volume)."""
    N = np.asarray(N, float)
    Ntot = float(N.sum())
    if Ntot <= 0:
        raise UVError("no inventory")
    z = N / Ntot
    kw = [K0]
    ncall = [0]

    def F(x):
        T, lnP = x
        if not (60.0 < T < 1500.0) or not (-6.0 < lnP < 8.0):
            raise UVError(f"state outside range (T = {T:.1f} K, P = {math.exp(min(max(lnP, -50), 50)):.3g} bar)")
        fr, Vc, Uc = vol_energy(fp, z, Ntot, T, math.exp(lnP), kw[0])
        if fr.Kset is not None:
            kw[0] = fr.Kset
        ncall[0] += 1
        sc = Ntot * max(fr.Cp, 20.0)
        return np.array([(Vc - V) / V, (Uc - U) / sc]), fr

    x = np.array([float(T0), math.log(P0)])
    r, fr = F(x)
    J = None if J0 is None else np.array(J0, float)
    for it in range(max_iter):
        if abs(r[0]) < tol_v and abs(r[1]) < tol_t:
            return fr, float(x[0]), float(math.exp(x[1])), J
        if J is None or it >= 8:
            J = np.zeros((2, 2))
            for j, h in enumerate((0.02, 2e-4)):
                xp = x.copy()
                xp[j] += h
                J[:, j] = (F(xp)[0] - r) / h
        try:
            dx = np.linalg.solve(J, -r)
        except np.linalg.LinAlgError:
            J = None
            continue
        # step limits: 25 K and a factor 1.6 in pressure; backtracking on the residual norm
        lim = max(abs(dx[0]) / 25.0, abs(dx[1]) / 0.47, 1.0)
        dx = dx / lim
        lam, r0n = 1.0, max(abs(r[0]) * 1.0, abs(r[1]) * 0.05)
        for _ in range(8):
            xn = x + lam * dx
            try:
                rn, frn = F(xn)
            except UVError:
                lam *= 0.5
                continue
            if max(abs(rn[0]), abs(rn[1]) * 0.05) < r0n * (1 - 1e-4 * lam) or lam < 0.05:
                break
            lam *= 0.5
        else:
            J = None
            continue
        s = xn - x
        y = rn - r
        if float(s @ s) > 0 and J is not None:                       # Broyden update
            J = J + np.outer(y - J @ s, s) / float(s @ s)
        x, r, fr = xn, rn, frn
    if abs(r[0]) < 1e-6 and abs(r[1]) < 1e-4:
        return fr, float(x[0]), float(math.exp(x[1])), J
    raise UVError(f"UV flash did not converge (residual {r[0]:.2e}, {r[1]:.2e}); the volume may be liquid-full")


# ---------------------------------------------------------------------------------------------- geometry

def area_fraction(y):
    """Fraction of a circle's area below the relative height y = h / D (0..1)."""
    y = min(max(y, 0.0), 1.0)
    c = 1.0 - 2.0 * y
    return (math.acos(c) - c * math.sqrt(max(1.0 - c * c, 0.0))) / math.pi


def height_fraction(f):
    """Relative height y = h / D for a liquid area fraction f (inverse of ``area_fraction``)."""
    f = min(max(f, 0.0), 1.0)
    if f <= 0.0 or f >= 1.0:
        return f
    lo, hi = 0.0, 1.0
    for _ in range(50):
        mid = 0.5 * (lo + hi)
        if area_fraction(mid) < f:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def level_pct(orient, V_liq, V_total):
    """Liquid level [% of the diameter / height] for a horizontal or vertical cylinder."""
    f = V_liq / V_total if V_total > 0 else 0.0
    return 100.0 * (height_fraction(f) if str(orient).lower().startswith("h") else min(max(f, 0.0), 1.0))


def volume_fraction_at_level(orient, level):
    """Inverse of ``level_pct``: liquid volume fraction at a level [% of D or height]."""
    y = min(max(level / 100.0, 0.0), 1.0)
    return area_fraction(y) if str(orient).lower().startswith("h") else y


# ---------------------------------------------------------------------------------------------- flow laws

RANGE = 50.0


def valve_char(x, char="Equal percentage"):
    """Relative flow coefficient at stem position x (0..1): equal percentage (rangeability 50, shifted to 0 at
    x = 0) or linear."""
    x = min(max(x, 0.0), 1.0)
    if str(char).lower().startswith("lin"):
        return x
    return (RANGE ** (x - 1.0) - 1.0 / RANGE) / (1.0 - 1.0 / RANGE)


def valve_char_inv(f, char="Equal percentage"):
    f = min(max(f, 0.0), 1.0)
    if str(char).lower().startswith("lin"):
        return f
    return 1.0 + math.log(f * (1.0 - 1.0 / RANGE) + 1.0 / RANGE) / math.log(RANGE)


def valve_driving(Pu, Pd, rho, k, compressible, xT=0.7):
    """sqrt-term of the control-valve equation [sqrt(bar kg/m3)] and whether the flow is choked.  The square root is
    regularised below a pressure drop of ~1 mbar (linear there) so the slope stays finite at zero drop."""
    dP = Pu - Pd
    if dP <= 0.0 or Pu <= 0.0 or rho <= 0.0:
        return 0.0, False
    if not compressible:
        return dP * math.sqrt(rho) / math.sqrt(max(dP, 1e-3)), False
    xc = max(k, 1.0) / 1.4 * xT
    xr = dP / Pu
    choked = xr >= xc
    xe = min(xr, xc)
    Y = 1.0 - xe / (3.0 * xc)
    return Y * xe * math.sqrt(Pu * rho) / math.sqrt(max(xe, 1e-4 / max(Pu, 1.0))), choked


def orifice_mass_flow(Pu, Pd, T, MW, k, Z, rho, A, Cd=0.85, gas=True):
    """Mass flow [kg/s] through an orifice of area A [m2]: gas - choked / subsonic (API 520, ISO 4126), liquid -
    incompressible.  Pu, Pd in bar(a), T in K, MW in g/mol, rho in kg/m3."""
    if A <= 0 or Pu <= Pd or Pu <= 0:
        return 0.0
    if not gas:
        return Cd * A * math.sqrt(2.0 * rho * (Pu - Pd) * 1e5)
    k = max(k, 1.01)
    r = Pd / Pu
    rc = (2.0 / (k + 1.0)) ** (k / (k - 1.0))
    a = MW / 1000.0 / (max(Z, 0.05) * R * T)                   # kg/J
    P1 = Pu * 1e5
    if r <= rc:
        return Cd * A * P1 * math.sqrt(k * a * (2.0 / (k + 1.0)) ** ((k + 1.0) / (k - 1.0)))
    return Cd * A * P1 * math.sqrt(2.0 * k / (k - 1.0) * a * max(r ** (2.0 / k) - r ** ((k + 1.0) / k), 0.0))


def poly_head(r, Z, T, MW, k, eta):
    """Polytropic head [kJ/kg] for a pressure ratio r (real-gas, constant Z and k)."""
    sigma = (k - 1.0) / (k * eta)
    return (Z * R * T / MW) * (r ** sigma - 1.0) / sigma
