"""Traced hydrocarbon phase envelope: bubble and dew lines found by bisection on the phase count at a series of
pressures, with the cricondenbar refined by bisection on pressure and the cricondentherm as the warmest dew point.

Sharper than a contour of a coarse flash grid near the cricondenbar and cricondentherm. Free water is ignored
(only hydrocarbon liquid counts as a second phase)."""
from __future__ import annotations

import math

import numpy as np

from .thermo import FlashError


def _two(fp, z, T, P):
    try:
        fr = fp.pt_flash(z, T, P)
    except FlashError:
        return False
    return sum(1 for ph in fr.phases if ph.kind != "W") > 1


def _bisect_T(fp, z, P, T_single, T_two, tol=0.05):
    a, b = T_single, T_two
    for _ in range(40):
        if abs(b - a) < tol:
            break
        m = 0.5 * (a + b)
        if _two(fp, z, m, P):
            b = m
        else:
            a = m
    return 0.5 * (a + b)


def _scan(fp, z, P, T_lo, T_hi, n):
    Ts = np.linspace(T_lo, T_hi, n)
    flags = [_two(fp, z, T, P) for T in Ts]
    idx = [i for i, f in enumerate(flags) if f]
    return Ts, flags, idx


def trace_envelope(fp, z, P_max=300.0, T_min=120.0, T_max=750.0, nP=26, nT=34):
    """{"bubble": [(T K, P bar)], "dew": [(T, P)], "cricondenbar": (T, P) or None, "cricondentherm": (T, P) or None}."""
    z = np.asarray(z, float)
    z = z / z.sum()
    bub, dew = [], []
    last_two = None
    Ps = np.exp(np.linspace(math.log(1.0), math.log(P_max), nP))
    for P in Ps:
        Ts, flags, idx = _scan(fp, z, P, T_min, T_max, nT)
        if not idx:
            if last_two is not None:
                break
            continue
        i0, i1 = idx[0], idx[-1]
        if i0 > 0:
            bub.append((_bisect_T(fp, z, P, Ts[i0 - 1], Ts[i0]), P))
        if i1 < len(Ts) - 1:
            dew.append((_bisect_T(fp, z, P, Ts[i1 + 1], Ts[i1]), P))
        last_two = (P, Ts[i0], Ts[i1])
    cb = None
    if last_two is not None:
        P_ok, Tlo, Thi = last_two
        nxt = [P for P in Ps if P > P_ok]
        P_bad = nxt[0] if nxt else None
        if P_bad is not None:
            span = max(Thi - Tlo, 10.0)
            best = (0.5 * (Tlo + Thi), P_ok)
            lo_T, hi_T = Tlo - 0.3 * span, Thi + 0.3 * span
            for _ in range(14):
                Pm = 0.5 * (P_ok + P_bad)
                Ts, flags, idx = _scan(fp, z, Pm, lo_T, hi_T, 24)
                if idx:
                    P_ok = Pm
                    best = (0.5 * (Ts[idx[0]] + Ts[idx[-1]]), Pm)
                    lo_T, hi_T = Ts[max(idx[0] - 1, 0)], Ts[min(idx[-1] + 1, len(Ts) - 1)]
                else:
                    P_bad = Pm
                if P_bad - P_ok < 0.05:
                    break
            cb = best
    ct = max(dew, key=lambda p: p[0]) if dew else None
    return {"bubble": bub, "dew": dew, "cricondenbar": cb, "cricondentherm": ct}
