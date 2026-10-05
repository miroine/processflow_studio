"""Equilibrium-stage column (absorber, reboiled absorber / stabiliser, distillation).

Method: inside-out style.

* Outer loop - rigorous Peng-Robinson: at the current stage temperatures and
  compositions, K-values are evaluated from fugacity coefficients and fitted to a
  local model ln K_ij(T) = ln K*_ij + a_ij (1/T - 1/T*_j); the enthalpy departures
  of each phase are evaluated and linearised in T.
* Inner loop - simultaneous Newton (Naphtali-Sandholm) on the MESH equations with
  the simple models: unknowns are the component liquid and vapour flows and the
  temperature of every stage.  The Jacobian is built by finite differences with a
  3-colour stage partition (block-tridiagonal structure), then solved densely.
* The outer loop repeats until the PR K-values stop changing, so the converged
  column satisfies rigorous PR equilibrium on every stage.

Stages are numbered from the top; the condenser (if any) is stage 0 and the
reboiler (if any) is the last stage.  Liquid-liquid (free-water) splitting on the
stages is not modelled (VLE only).
"""
from __future__ import annotations

import math

import numpy as np

from .thermo import FluidPackage, R

K0 = 273.15
_WARM = {}          # warm starts between solves: key -> (l, v, T)


class ColumnError(RuntimeError):
    pass


class ColumnSpec:
    def __init__(self, n_trays, cond, reb, P_top, P_bot, feeds, specs):
        self.n_trays = int(n_trays)
        self.cond = cond            # None | "Partial" | "Total"
        self.reb = bool(reb)
        self.P_top, self.P_bot = float(P_top), float(P_bot)
        self.feeds = feeds          # list of (stage_index, F kmol/h, z, H J/mol)
        self.specs = specs          # dict: RR, reb_spec, reb_value
        self.c0 = 1 if cond else 0
        self.N = self.n_trays + self.c0 + (1 if reb else 0)

    def stage_labels(self):
        lab = []
        if self.cond:
            lab.append("Condenser")
        lab += [str(i + 1) for i in range(self.n_trays)]
        if self.reb:
            lab.append("Reboiler")
        return lab


def bubble_pressure(fp: FluidPackage, x, T, P0=None):
    """Bubble-point pressure [bar] of liquid x at T [K] (successive substitution on K)."""
    x = np.asarray(x, float)
    idx = np.nonzero(x > 1e-14)[0]
    xl = x[idx] / x[idx].sum()
    Kw = fp.Pc[idx] * np.exp(5.373 * (1 + fp.w[idx]) * (1 - fp.Tc[idx] / T))
    P = float(P0 or max(xl @ Kw, 0.01))
    y = Kw * xl / (Kw @ xl)
    for _ in range(300):
        lpl, _, _ = fp.lnphi(xl, T, P, idx, "L")
        lpv, _, _ = fp.lnphi(y, T, P, idx, "V")
        K = np.exp(lpl - lpv)
        s = float(K @ xl)
        y_new = K * xl / s
        P_new = P * s
        if abs(P_new - P) < 1e-9 * P and np.max(np.abs(y_new - y)) < 1e-10:
            P = P_new
            break
        P, y = P_new, y_new
        if np.max(np.abs(y - xl)) < 1e-6 and _ > 20:
            break           # trivial solution (supercritical) - report what we have
    return P


class Column:
    def __init__(self, fp: FluidPackage, spec: ColumnSpec, warm_key=None):
        self.fp = fp
        self.s = spec
        zsum = sum(F * z for (_, F, z, _) in spec.feeds)
        self.Ftot = float(zsum.sum())
        if self.Ftot <= 0:
            raise ColumnError("Column has no feed")
        self.idx = np.nonzero(zsum > 1e-12 * self.Ftot)[0]
        self.C = self.idx.size
        self.zf = zsum[self.idx] / self.Ftot
        N, C = spec.N, self.C
        self.P = np.linspace(spec.P_top, spec.P_bot, N)
        self.f = np.zeros((N, C))
        self.hf = np.zeros(N)
        for (j, F, z, H) in spec.feeds:
            self.f[j] += F * z[self.idx]
            self.hf[j] += F * H
        self.warm_key = warm_key
        self.cpc = fp.cpc[self.idx]
        self.nv = 2 * C + 1
        self.hscale = 1e4 * self.Ftot

    # ---------------------------------------------------------------- helpers
    def hig(self, T):
        """Ideal-gas component enthalpies J/mol, shape (N, C) for T array (N,)."""
        c = self.cpc
        t0 = 298.15
        T = T[:, None]
        return (c[:, 0] * (T - t0) + c[:, 1] / 2 * (T ** 2 - t0 ** 2) + c[:, 2] / 3 * (T ** 3 - t0 ** 3)
                + c[:, 3] / 4 * (T ** 4 - t0 ** 4))

    def unpack(self, X):
        N, C = self.s.N, self.C
        Y = X.reshape(N, self.nv)
        return Y[:, :C], Y[:, C:2 * C], Y[:, 2 * C]

    def pack(self, l, v, T):
        return np.hstack([l, v, T[:, None]]).ravel()

    # ------------------------------------------------------- rigorous update
    def update_models(self, l, v, T):
        """PR K-values, their T-sensitivity, and enthalpy departures at the current profile."""
        fp, idx, N = self.fp, self.idx, self.s.N
        L = l.sum(1)
        V = v.sum(1)
        lnK = np.zeros((N, self.C))
        a = np.zeros((N, self.C))
        depL = np.zeros(N)
        depV = np.zeros(N)
        cdL = np.zeros(N)
        cdV = np.zeros(N)
        dT = 0.5
        for j in range(N):
            x = np.maximum(l[j], 1e-16)
            x = x / x.sum()
            if V[j] > 1e-10 * self.Ftot:
                y = np.maximum(v[j], 1e-16)
                y = y / y.sum()
            else:
                # no vapour leaving (total condenser): incipient vapour from the current K
                y = x * np.exp(self.lnK[j]) if hasattr(self, "lnK") else x.copy()
                y = y / y.sum()
            P = self.P[j]
            res = []
            for Tt in (T[j], T[j] + dT):          # exact at T (the solution), forward difference for slopes
                lpl, Zl, mixl = fp.lnphi(x, Tt, P, idx, "L")
                lpv, Zv, mixv = fp.lnphi(y, Tt, P, idx, "V")
                hl = fp.phase_props(x, Tt, P, idx, Zl, mixl)[0] - float(x @ fp.h_ig(Tt, idx))
                hv = fp.phase_props(y, Tt, P, idx, Zv, mixv)[0] - float(y @ fp.h_ig(Tt, idx))
                res.append((lpl - lpv, hl, hv))
            (k1, hl1, hv1), (k2, hl2, hv2) = res
            lnK[j] = k1
            a[j] = (k2 - k1) / (1.0 / (T[j] + dT) - 1.0 / T[j])
            depL[j], depV[j] = hl1, hv1
            cdL[j], cdV[j] = (hl2 - hl1) / dT, (hv2 - hv1) / dT
        self.lnK, self.aK, self.Tref = lnK, a, T.copy()
        self.depL, self.depV, self.cdL, self.cdV = depL, depV, cdL, cdV

    def K(self, T):
        return np.exp(self.lnK + self.aK * (1.0 / T[:, None] - 1.0 / self.Tref[:, None]))

    # ------------------------------------------------------------- residuals
    def residuals(self, X):
        s = self.s
        N, C = s.N, self.C
        l, v, T = self.unpack(X)
        L = l.sum(1)
        V = v.sum(1)
        Lsafe = np.maximum(L, 1e-12 * self.Ftot)
        Vsafe = np.maximum(V, 1e-12 * self.Ftot)
        K = self.K(T)
        hig = self.hig(T)
        hL = (l * hig).sum(1) + L * (self.depL + self.cdL * (T - self.Tref))      # J/h per kmol basis
        hV = (v * hig).sum(1) + V * (self.depV + self.cdV * (T - self.Tref))
        RR = s.specs.get("RR", 1.0)
        # liquid arriving at stage j from above
        lin = np.zeros((N, C))
        hlin = np.zeros(N)
        lin[1:] = l[:-1]
        hlin[1:] = hL[:-1]
        if s.cond == "Total":
            frac = RR / (1.0 + RR)
            lin[1] *= frac
            hlin[1] *= frac
        vin = np.zeros((N, C))
        hvin = np.zeros(N)
        vin[:-1] = v[1:]
        hvin[:-1] = hV[1:]
        Fs = self.Ftot
        rM = (l + v - lin - vin - self.f) / Fs
        rE = v / Vsafe[:, None] - K * l / Lsafe[:, None]
        rH = (hL + hV - hlin - hvin - self.hf) / self.hscale
        # --- condenser
        if s.cond == "Total":
            rE[0] = v[0] / Fs                                      # no vapour product
            rH_c = float((K[0] * l[0]).sum() / Lsafe[0] - 1.0)       # bubble point
            rH[0] = rH_c
        elif s.cond == "Partial":
            rH[0] = (L[0] - RR * V[0]) / Fs
        # --- reboiler / bottom specification
        if s.reb:
            j = N - 1
            spec, val = s.specs.get("reb_spec"), float(s.specs.get("reb_value", 0.0))
            if spec == "Reboiler duty":
                rH[j] = (hL[j] + hV[j] - hlin[j] - hvin[j] - self.hf[j] - val * 3600.0) / self.hscale
            elif spec == "Bottoms rate":
                rH[j] = (L[j] - val) / Fs
            elif spec == "Boil-up ratio":
                rH[j] = (V[j] - val * L[j]) / Fs
            elif spec == "Reboiler temperature":
                rH[j] = (T[j] - (val + K0)) / 100.0
            elif spec == "Distillate rate":
                # D = F - B by the overall balance; written on the bottoms flow to keep the Jacobian local
                rH[j] = (L[j] - (self.Ftot - val)) / Fs
            else:
                raise ColumnError(f"Unknown reboiler specification {spec!r}")
        return np.hstack([rM, rE, rH[:, None]]).ravel()

    def duties(self, X):
        """Condenser and reboiler duties [kW] from the energy balances (positive = heat in)."""
        s = self.s
        l, v, T = self.unpack(X)
        L, V = l.sum(1), v.sum(1)
        hig = self.hig(T)
        hL = (l * hig).sum(1) + L * (self.depL + self.cdL * (T - self.Tref))
        hV = (v * hig).sum(1) + V * (self.depV + self.cdV * (T - self.Tref))
        RR = s.specs.get("RR", 1.0)
        N = s.N
        Qc = Qr = 0.0
        if s.cond:
            # stage 0: in = vapour from 1 + feed; out = liquid (reflux + distillate) + vapour
            Qc = (hL[0] + hV[0] - hV[1] - self.hf[0]) / 3600.0
        if s.reb:
            j = N - 1
            hlin = hL[j - 1] * (RR / (1 + RR) if (s.cond == "Total" and j == 1) else 1.0)
            Qr = (hL[j] + hV[j] - hlin - self.hf[j]) / 3600.0
        return Qc, Qr

    # ------------------------------------------------------------ Jacobian
    def jacobian(self, X, r0):
        N, nv = self.s.N, self.nv
        n = X.size
        J = np.zeros((n, n))
        l, v, T = self.unpack(X)
        for color in range(3):
            stages = np.arange(color, N, 3)
            for k in range(nv):
                Xp = X.copy()
                cols = stages * nv + k
                h = np.where(k < 2 * self.C, 1e-7 * self.Ftot + 1e-6 * np.abs(X[cols]), 1e-4)
                Xp[cols] += h
                dr = (self.residuals(Xp) - r0)
                for jst, col, hh in zip(stages, cols, h):
                    lo, hi = max(jst - 1, 0) * nv, min(jst + 2, N) * nv
                    J[lo:hi, col] = dr[lo:hi] / hh
        return J

    # ---------------------------------------------------------------- init
    def initial(self):
        s, fp, idx = self.s, self.fp, self.idx
        N, C = s.N, self.C
        if self.warm_key and self.warm_key in _WARM:
            l, v, T = _WARM[self.warm_key]
            if l.shape == (N, C):
                return l.copy(), v.copy(), T.copy()
        zf = self.zf
        Hf = self.hf.sum() / self.Ftot
        Pm = 0.5 * (s.P_top + s.P_bot)
        try:
            fr = fp.ph_flash(np.zeros(fp.n) + np.bincount(idx, zf, fp.n), Pm, Hf, 300.0)
            T0, vf = fr.T, fr.vf
        except Exception:
            T0, vf = 300.0, 0.5
        RR = s.specs.get("RR", 1.0)
        spec, val = s.specs.get("reb_spec"), float(s.specs.get("reb_value", 0.0))
        F = self.Ftot
        # top product estimate
        if spec == "Bottoms rate":
            Dtop = F - val
        elif spec == "Distillate rate":
            Dtop = val
        else:
            Dtop = F * min(max(vf, 0.05), 0.95)
        Dtop = min(max(Dtop, 0.02 * F), 0.98 * F)
        B = F - Dtop
        if s.cond:
            Vint = (RR + 1.0) * Dtop
        else:
            Vint = Dtop
        if s.reb and spec == "Boil-up ratio":
            Vint = max(Vint, val * B)
        V = np.full(N, Vint)
        if s.cond == "Total":
            V[0] = 0.0
        elif s.cond == "Partial":
            V[0] = Dtop
        Fcum = np.cumsum(self.f.sum(1))
        L = np.zeros(N)
        for j in range(N):
            Vbelow = V[j + 1] if j + 1 < N else 0.0
            L[j] = Vbelow + Fcum[j] - Dtop
        if s.cond == "Total":
            L[0] = V[1] * 1.0 if N > 1 else Dtop       # all condensed: reflux + distillate
        L = np.maximum(L, 0.02 * F)
        L[-1] = max(B, 0.02 * F)
        # temperature profile
        if s.reb:
            Ttop, Tbot = T0 - 25.0, T0 + 45.0
            if spec == "Reboiler temperature":
                Tbot = val + K0
        else:
            Ttop, Tbot = T0, T0 + 5.0
        if s.cond:
            Ttop = min(Ttop, T0 - 40.0)
        T = np.linspace(Ttop, Tbot, N)
        l = np.zeros((N, C))
        v = np.zeros((N, C))
        for j in range(N):
            Kw = fp.Pc[idx] / self.P[j] * np.exp(5.373 * (1 + fp.w[idx]) * (1 - fp.Tc[idx] / T[j]))
            beta = V[j] / max(V[j] + L[j], 1e-12)
            x = zf / (1 + beta * (Kw - 1))
            x = x / x.sum()
            y = Kw * x
            y = y / y.sum()
            l[j] = L[j] * x
            v[j] = V[j] * y
        return l, v, T

    # --------------------------------------------------------------- solve
    def solve(self, max_outer=40, tol=1e-6):
        l, v, T = self.initial()
        self.lnK = np.zeros((self.s.N, self.C))
        self.update_models(l, v, T)
        X = self.pack(l, v, T)
        hist = []
        for outer in range(1, max_outer + 1):
            X, rn = self._newton(X)
            l, v, T = self.unpack(X)
            old = self.lnK.copy()
            olddep = np.hstack([self.depL, self.depV])
            self.update_models(l, v, T)
            dK = float(np.max(np.abs(self.lnK - old)))
            ddep = float(np.max(np.abs(np.hstack([self.depL, self.depV]) - olddep)))
            hist.append((outer, rn, dK))
            if dK < tol and ddep < 1.0 and rn < 1e-7:
                X, rn = self._newton(X)
                self.outer_iterations = outer
                self.hist = hist
                if self.warm_key:
                    _WARM[self.warm_key] = self.unpack(X)
                return X
        raise ColumnError(f"Column did not converge in {max_outer} outer iterations "
                          f"(last K change {hist[-1][2]:.2e}, residual {hist[-1][1]:.2e})")

    def _newton(self, X, max_it=60):
        N, C = self.s.N, self.C
        r = self.residuals(X)
        rn = float(np.linalg.norm(r))
        for it in range(max_it):
            if rn < 1e-10:
                break
            J = self.jacobian(X, r)
            try:
                dX = np.linalg.solve(J, -r)
            except np.linalg.LinAlgError:
                dX = np.linalg.lstsq(J, -r, rcond=None)[0]
            l, v, T = self.unpack(X)
            dl, dv, dT = self.unpack(dX)
            # step length: keep flows positive, limit temperature steps
            alpha = 1.0
            for cur, d in ((l, dl), (v, dv)):
                neg = d < 0
                if np.any(neg):
                    ratio = np.min(np.where(neg, -0.9 * np.maximum(cur, 0) / np.where(neg, d, -1), np.inf))
                    alpha = min(alpha, max(ratio, 0.05))
            tmax = float(np.max(np.abs(dT))) if dT.size else 0.0
            if tmax * alpha > 25.0:
                alpha = 25.0 / tmax
            best = None
            for _ in range(12):
                Xn = X + alpha * dX
                ln, vn, Tn = self.unpack(Xn)
                np.maximum(ln, 1e-14 * self.Ftot, out=ln)
                np.maximum(vn, 0.0, out=vn)
                if self.s.cond == "Total":
                    vn[0] = 0.0
                rn_new = float(np.linalg.norm(self.residuals(Xn)))
                if best is None or rn_new < best[1]:
                    best = (Xn, rn_new)
                if rn_new < (1 - 1e-4 * alpha) * rn:
                    break
                alpha *= 0.5
            X, rn_try = best
            r = self.residuals(X)
            rn_prev, rn = rn, float(np.linalg.norm(r))
            if rn_prev - rn < 1e-12 and rn > 1e-7:
                break
        return X, rn

    # ------------------------------------------------------------ products
    def products(self, X):
        """Overhead and bottoms component flows (kmol/h, full component vector) and stage profile."""
        s, fp = self.s, self.fp
        l, v, T = self.unpack(X)
        N = s.N
        RR = s.specs.get("RR", 1.0)
        top = np.zeros(fp.n)
        bot = np.zeros(fp.n)
        if s.cond == "Total":
            top[self.idx] = l[0] / (1.0 + RR)
        else:
            top[self.idx] = v[0]
        bot[self.idx] = l[N - 1]
        return top, bot


def run_column(fp, spec: ColumnSpec, warm_key=None):
    col = Column(fp, spec, warm_key)
    try:
        X = col.solve()
    except ColumnError:
        if warm_key in _WARM:          # a bad warm start can mislead: retry cold once
            _WARM.pop(warm_key, None)
            col = Column(fp, spec, None)
            X = col.solve()
            if warm_key:
                _WARM[warm_key] = col.unpack(X)
        else:
            raise
    return col, X
