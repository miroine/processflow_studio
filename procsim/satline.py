"""Phase envelope by continuation (Michelsen 1980): the whole saturation curve of a fluid is followed as ONE smooth
path in the variables (ln K_1 ... ln K_n, ln T, ln P) - from the dew (or bubble) point at low pressure, over the
cricondentherm and the cricondenbar and through the critical point to the other branch.

The incipient-phase equations are

    F_i   = ln K_i + ln phi_i(y) - ln phi_i(x) = 0                      i = 1..n   (equal fugacities)
    F_n+1 = sum_i (y_i - x_i)                  = 0                                 (Rachford-Rice at vapour fraction beta)
    F_n+2 = u_spec - S                         = 0                                 (the specified variable)

with x_i = z_i / (1 + beta (K_i - 1)) and y_i = K_i x_i.  beta = 0 is the bubble-point form (x = z); the same equations
describe the dew line after the path has crossed the critical point, where ln K changes sign and the two Peng-Robinson
roots swap their roles.  Lines of constant vapour fraction (beta between 0 and 1) are followed with the same machinery
from a flash at low pressure until they reach the critical point.

Free water and the other aqueous-phase components are left out (hydrocarbon envelope).  The path is returned as ordered
points, so plots show a smooth curve instead of a contour of a coarse flash grid.
"""
from __future__ import annotations

import math

import numpy as np

from .thermo import FlashError

P_START = 1.0            # bar, where the dew / bubble point is first located
P_STOP = 0.7             # bar, end of the path on the far branch
T_FLOOR, T_CAP = 55.0, 1150.0
MAX_DISP = 0.015         # largest change of ln T or ln P in one step


class SatError(RuntimeError):
    pass


def hydrocarbon_basis(fp, z, dry=True):
    """(idx, z) of the components that take part: aqueous / polar components are left out when dry."""
    z = np.asarray(z, float).copy()
    if dry:
        z[fp.polar] = 0.0
    idx = np.nonzero(z > 1e-12)[0]
    if idx.size < 2:
        raise SatError("a phase envelope needs at least two components outside the aqueous phase")
    zz = z[idx] / z[idx].sum()
    return idx, zz


class _System:
    def __init__(self, fp, idx, z, beta=0.0):
        self.fp, self.idx, self.z, self.beta = fp, idx, z, float(beta)
        self.n = len(idx)

    def F(self, u, roles, spec, S):
        n = self.n
        K = np.exp(np.clip(u[:n], -150.0, 150.0))
        T, P = math.exp(u[n]), math.exp(u[n + 1])
        x = self.z / (1.0 + self.beta * (K - 1.0))
        y = K * x
        sx, sy = x.sum(), y.sum()
        try:
            lpx = self.fp.lnphi(x / sx, T, P, self.idx, roles[0])[0]
            lpy = self.fp.lnphi(y / sy, T, P, self.idx, roles[1])[0]
        except (ValueError, FloatingPointError, FlashError, np.linalg.LinAlgError):
            raise SatError("equation of state failed")
        out = np.empty(n + 2)
        out[:n] = u[:n] + lpy - lpx
        out[n] = float((y - x).sum())
        out[n + 1] = u[spec] - S
        if not np.all(np.isfinite(out)):
            raise SatError("non-finite residual")
        return out

    def jac(self, u, roles, spec, S, F0=None):
        n2 = self.n + 2
        if F0 is None:
            F0 = self.F(u, roles, spec, S)
        J = np.empty((n2, n2))
        for j in range(n2):
            h = 1e-6 * max(1.0, abs(u[j]))
            up = u.copy()
            up[j] += h
            J[:, j] = (self.F(up, roles, spec, S) - F0) / h
        return J, F0

    def newton(self, u0, roles, spec, S, J=None, max_it=10, tol=1e-8, fresh=False):
        """Chord / Newton iterations; returns (u, J_used, iterations) or raises SatError."""
        n = self.n
        u = u0.copy()
        Jc = J
        F = self.F(u, roles, spec, S)
        for it in range(1, max_it + 1):
            if np.max(np.abs(F)) < tol:
                return u, Jc, it - 1
            if Jc is None or fresh or it in (4, 8):
                Jc, F = self.jac(u, roles, spec, S, F)
            try:
                du = np.linalg.solve(Jc, -F)
            except np.linalg.LinAlgError:
                raise SatError("singular Jacobian")
            if float(np.max(np.abs(du))) < 1e-9 and np.max(np.abs(F)) < 1e-6:
                return u, Jc, it - 1                  # at the noise floor of the cubic-root solver
            cap = max(np.max(np.abs(du[:n])) / 1.5, abs(du[n]) / 0.25, abs(du[n + 1]) / 0.25, 1.0)
            u = u + du / cap
            F = self.F(u, roles, spec, S)
        if np.max(np.abs(F)) < 1e-7:
            return u, Jc, max_it
        raise SatError("no convergence")


def _wilson_lnK(fp, idx, T, P):
    return np.log(fp.Pc[idx] / P) + 5.373 * (1.0 + fp.w[idx]) * (1.0 - fp.Tc[idx] / T)


def _ss_incipient(fp, idx, z, T, P, phase_z, n_it=60):
    """Successive substitution for the incipient phase of a feed z that is 'V' (dew) or 'L' (bubble) at (T, P)."""
    wz, wy = ("V", "L") if phase_z == "V" else ("L", "V")
    lnK = _wilson_lnK(fp, idx, T, P)
    if phase_z == "V":                        # incipient liquid: y_i / z_i = 1 / K_wilson
        lnK = -lnK
    lpz = fp.lnphi(z, T, P, idx, wz)[0]
    for _ in range(n_it):
        y = z * np.exp(lnK)
        y = y / y.sum()
        lpy = fp.lnphi(y, T, P, idx, wy)[0]
        new = lpz - lpy
        if np.max(np.abs(new - lnK)) < 1e-10:
            lnK = new
            break
        lnK = new
    return lnK


def _start_point(fp, idx, z, P0):
    """(T, branch) of the first saturation point on P0: ('dew' or 'bubble'), found with the stability-based flash."""
    from .envelope import _two, _bisect_T
    full = np.zeros(fp.n)
    full[idx] = z
    Ts = np.linspace(T_FLOOR + 10.0, 1000.0, 48)
    flags = [_two(fp, full, T, P0) for T in Ts]
    ii = [i for i, f in enumerate(flags) if f]
    if not ii:
        return None
    i0, i1 = ii[0], ii[-1]
    cand = []
    if i1 < len(Ts) - 1:
        cand.append(("dew", _bisect_T(fp, full, P0, Ts[i1 + 1], Ts[i1], tol=0.01)))
    if i0 > 0:
        cand.append(("bubble", _bisect_T(fp, full, P0, Ts[i0 - 1], Ts[i0], tol=0.01)))
    if not cand:
        return None
    for br, T in cand:
        if br == "dew":                      # the dew start is preferred: the usual gas / condensate case
            return br, T
    return cand[0]


def _follow(sysm, u, roles, allow_cross, stop, max_points, max_disp=MAX_DISP, debug=None):
    """Continuation along the solution curve from the converged point u.  Returns (points, crossed flag of every point,
    crossing locations (T, P), reason of the end)."""
    n = sysm.n
    spec = n + 1
    ref_sign = u[:n].copy()
    pts, flags, crossings = [u.copy()], [False], []
    crossed, direction, dS, tiny = False, None, 0.01, 0
    reason = "limit"
    for _ in range(max_points):
        try:
            rhs = np.zeros(n + 2)
            rhs[n + 1] = 1.0
            Jf, _F = sysm.jac(u, roles, spec, u[spec])
            t = np.linalg.solve(Jf, rhs)
        except (np.linalg.LinAlgError, SatError):
            reason = "jacobian"
            break
        k = int(np.argmax(np.abs(t)))
        t = t / t[k]
        if direction is None:
            if t[n + 1] < 0:
                t = -t
        elif float(np.dot(t, direction)) < 0:
            t = -t
        spec = k
        disp = max(abs(t[n]), abs(t[n + 1]), 1e-12)
        step = min(dS, max_disp / disp)
        ok, crossing = False, False
        for _try in range(14):
            u_pred = u + t * step
            S = u_pred[spec]
            crossing = float(np.dot(u_pred[:n], u[:n])) < 0.0 and float(np.linalg.norm(u[:n])) < 1.5      # only near the critical point
            if crossing and not allow_cross:
                reason = "critical"
                break
            r = (roles[1], roles[0]) if crossing else roles
            near = float(np.linalg.norm(u[:n])) < 0.4
            try:
                un, Jn, its = sysm.newton(u_pred, r, spec, S, J=None if (crossing or near) else Jf, max_it=12, fresh=near or crossing)
            except SatError:
                step *= 0.5
                continue
            if (float(np.max(np.abs(un[:n]))) < 2e-4 or abs(un[n] - u[n]) > 3 * max_disp + 0.05
                    or abs(un[n + 1] - u[n + 1]) > 3 * max_disp + 0.05):
                step *= 0.5                      # trivial solution, or a jump to another branch
                continue
            ok = True
            break
        if reason == "critical":
            break
        if not ok:
            reason = "no convergence"
            break
        if crossing:
            roles = r
            crossed = not crossed
            na, nb = np.linalg.norm(u[:n]), np.linalg.norm(un[:n])
            f = na / max(na + nb, 1e-12)
            crossings.append((math.exp(u[n] + f * (un[n] - u[n])), math.exp(u[n + 1] + f * (un[n + 1] - u[n + 1]))))
        moved = float(np.linalg.norm(un - u))
        if debug is not None:
            debug.append((spec, step, dS, its, moved))
        tiny = tiny + 1 if moved < 1e-5 else 0
        if tiny >= 3:                           # cannot be followed any further (e.g. a liquid-liquid split at low T)
            reason = "stalled"
            break
        direction = (un - u) / max(moved, 1e-15)
        u = un
        pts.append(u.copy())
        flags.append(crossed)
        dS = min(step * 1.6, 0.5) if its <= 5 else (step * 0.6 if its >= 9 else step)
        if stop(u, crossed):
            reason = "end"
            break
    return pts, flags, crossings, reason


def trace_envelope(fp, z, dry=True, P0=P_START, max_points=3000, debug=None):
    """Follow the saturation curve.  Returns a dict:

    path          ordered [(T K, P bar)] from the low-pressure start to the far branch
    branch        'dew' / 'bubble' for every point of the path
    bubble, dew   the two parts of the path
    critical      (T, P) where ln K changes sign (None if the path never crossed)
    cricondenbar, cricondentherm   (T, P)
    complete      True when the critical point was passed
    """
    idx, zz = hydrocarbon_basis(fp, z, dry)
    st = _start_point(fp, idx, zz, P0)
    if st is None:
        raise SatError("no two-phase region found at %.1f bar" % P0)
    branch0, T0 = st
    sysm = _System(fp, idx, zz, 0.0)
    n = sysm.n
    roles = ("V", "L") if branch0 == "dew" else ("L", "V")      # roots of the feed phase and of the incipient phase
    lnK0 = _ss_incipient(fp, idx, zz, T0, P0, "V" if branch0 == "dew" else "L")
    u = np.concatenate([lnK0, [math.log(T0), math.log(P0)]])
    u, J, _ = sysm.newton(u, roles, n + 1, u[n + 1], max_it=25)

    def stop(u_, crossed):
        T, P = math.exp(u_[n]), math.exp(u_[n + 1])
        if T < T_FLOOR or T > T_CAP or float(np.min(u_[:n])) < -100.0 or float(np.max(u_[:n])) > 100.0:
            return True
        return (crossed and P < P_STOP) or (not crossed and P < 0.5 * P0)

    pts, flags, crossings, reason = _follow(sysm, u, roles, True, stop, max_points, MAX_DISP, debug)
    path = [(math.exp(p[n]), math.exp(p[n + 1])) for p in pts]
    if len(path) < 4:
        raise SatError("the saturation curve could not be followed")
    other = "bubble" if branch0 == "dew" else "dew"
    tags = [other if f else branch0 for f in flags]
    arr = np.array(path)
    cb = _refine_max(arr, int(np.argmax(arr[:, 1])), 1)
    ct = _refine_max(arr, int(np.argmax(arr[:, 0])), 0)
    return {"path": path, "branch": tags, "bubble": [p for p, b in zip(path, tags) if b == "bubble"],
            "dew": [p for p, b in zip(path, tags) if b == "dew"], "critical": crossings[0] if crossings else None,
            "cricondenbar": cb, "cricondentherm": ct, "start": branch0, "complete": bool(crossings), "end": reason,
            "idx": idx, "z": zz}


def trace_isopleth(fp, z, beta, env=None, dry=True, P0=P_START, max_points=1500):
    """Line of constant vapour fraction `beta` (0 < beta < 1) from a flash at P0 up to the critical point.
    Returns [(T K, P bar)] (empty when no start was found)."""
    idx, zz = hydrocarbon_basis(fp, z, dry)
    full = np.zeros(fp.n)
    full[idx] = zz
    try:
        fr = fp.pvf_flash(full, P0, beta, 250.0)
    except (FlashError, ValueError, FloatingPointError):
        return []
    if fr.K is None or sum(1 for ph in fr.phases if ph.kind != "W") < 2:
        return []
    K = np.asarray(fr.K, float)[idx]
    K = np.where(K > 1e-300, K, 1e-300)
    sysm = _System(fp, idx, zz, beta)
    n = sysm.n
    u = np.concatenate([np.log(K), [math.log(fr.T), math.log(P0)]])
    try:
        u, J, _ = sysm.newton(u, ("L", "V"), n + 1, u[n + 1], max_it=25)
    except SatError:
        return []

    def stop(u_, crossed):
        T = math.exp(u_[n])
        return T < T_FLOOR or T > T_CAP or float(np.linalg.norm(u_[:n])) < 0.04

    pts, flags, crossings, reason = _follow(sysm, u, ("L", "V"), False, stop, max_points, 0.03)
    line = [(math.exp(p[n]), math.exp(p[n + 1])) for p in pts]
    if env and env.get("critical") and reason in ("end", "critical") and float(np.linalg.norm(pts[-1][:n])) < 0.6:
        line.append(tuple(env["critical"]))
    return line


def _refine_max(arr, i, col):
    """Parabolic refinement of the maximum of column `col` along the path (using the neighbours of index i)."""
    if i <= 0 or i >= len(arr) - 1:
        return (float(arr[i, 0]), float(arr[i, 1]))
    s = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(np.log(arr[:, 0])), np.diff(np.log(arr[:, 1]))))])
    ss, vv = s[i - 1:i + 2], arr[i - 1:i + 2, col]
    try:
        a, b, c = np.polyfit(ss, vv, 2)
        if a < 0:
            s_m = -b / (2 * a)
            if ss[0] <= s_m <= ss[2]:
                other = np.polyval(np.polyfit(ss, arr[i - 1:i + 2, 1 - col], 2), s_m)
                m = float(np.polyval([a, b, c], s_m))
                return (m, float(other)) if col == 0 else (float(other), m)
    except Exception:
        pass
    return (float(arr[i, 0]), float(arr[i, 1]))
