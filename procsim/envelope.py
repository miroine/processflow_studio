"""Traced hydrocarbon phase envelope.

``trace_envelope`` follows the saturation curve by continuation (``satline``: one smooth path over the dew line, the
cricondentherm, the cricondenbar and the critical point to the bubble line, plus optional lines of constant vapour
fraction).  If the continuation cannot be started or finishes badly it falls back to the older method kept here:
bubble and dew lines found by bisection on the phase count at a series of pressures, with the cricondenbar refined by
bisection on pressure and the cricondentherm as the warmest dew point.  Free water is ignored (only hydrocarbon liquid
counts as a second phase)."""
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


def _trace_grid(fp, z, P_max=300.0, T_min=120.0, T_max=750.0, nP=26, nT=34):
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


def trace_envelope(fp, z, P_max=300.0, T_min=120.0, T_max=750.0, nP=26, nT=34, quality=(), dry=True):
    """Phase envelope of the hydrocarbon phases of composition ``z``.

    Returns a dict with ``path`` (ordered [(T K, P bar)]), ``bubble`` and ``dew`` (the two branches), ``critical``,
    ``cricondenbar``, ``cricondentherm`` (each (T K, P bar) or None), ``isopleths`` ({vapour fraction: [(T, P)]}) and
    ``method`` ('continuation' or 'grid')."""
    from . import satline as SL
    z = np.asarray(z, float)
    try:
        r = SL.trace_envelope(fp, z, dry=dry)
        if not r["complete"]:
            raise SL.SatError("the curve did not pass the critical point")
        iso = {}
        for b in quality:
            try:
                line = SL.trace_isopleth(fp, z, float(b), r, dry=dry)
            except Exception:            # an isopleth is a nicety: never lose the envelope because of one
                line = []
            if len(line) > 3:
                iso[float(b)] = line
        r["isopleths"] = iso
        r["method"] = "continuation"
        return r
    except Exception:
        g = _trace_grid(fp, z, P_max, T_min, T_max, nP, nT)
        path = sorted(g["bubble"], key=lambda p: p[1]) + sorted(g["dew"], key=lambda p: -p[1])
        g.update({"path": path, "critical": None, "isopleths": {}, "method": "grid", "complete": False})
        return g
