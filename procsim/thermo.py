"""Peng-Robinson fluid package: EOS, fugacities, enthalpy/entropy, flashes.

Conventions (engine-internal): T in K, P in bar(a), molar quantities per mol,
enthalpy in J/mol (ideal-gas reference: 0 at 298.15 K for every component, no
heats of formation - there are no reactions), entropy in J/mol/K (ideal-gas
reference 298.15 K, 1.01325 bar).

Flash algorithm
---------------
Successive substitution on K-values with Michelsen's multiphase Rachford-Rice
objective (convex, handles 2 or 3 phases and vanishing phases), Wilson start,
free-water start for the aqueous phase, and a Michelsen tangent-plane
stability test whenever the result is single phase.  PH / PS / P-VF flashes
are secant/Newton on T with a bracketed Brent fallback.  Pure components get a
dedicated saturation treatment so that PH flashes through the two-phase dome
return the right vapour fraction.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .components import Component, LIBRARY, default_kij

R = 8.314462618           # J/mol/K
R_BAR = 8.314462618e-5    # m3 bar / mol / K
T_REF = 298.15
P_REF = 1.01325
SQ2 = math.sqrt(2.0)
T_STD = 288.15            # 15 degC
V_STD_GAS = R * T_STD / 101325.0 * 1000.0   # Sm3/kmol (23.645)


class FlashError(RuntimeError):
    pass


@dataclass
class Phase:
    kind: str            # 'V' vapour, 'L' hydrocarbon liquid, 'W' aqueous liquid
    beta: float          # molar phase fraction
    x: np.ndarray        # mole fractions (full component length)
    Z: float
    V: float             # EOS molar volume m3/mol (unshifted)
    Vs: float            # Peneloux-shifted molar volume m3/mol
    MW: float
    H: float = 0.0       # J/mol
    S: float = 0.0       # J/mol/K
    Cp: float = 0.0      # J/mol/K
    Cv: float = 0.0      # J/mol/K

    @property
    def rho(self):
        return self.MW / 1000.0 / self.Vs   # kg/m3


@dataclass
class FlashResult:
    T: float
    P: float
    z: np.ndarray
    phases: list = field(default_factory=list)
    K: np.ndarray | None = None      # last converged K (V relative to L) for warm starts
    Kset: np.ndarray | None = None   # all non-reference K rows (full component space)
    iterations: int = 0

    @property
    def vf(self):
        return sum(p.beta for p in self.phases if p.kind == "V")

    def phase(self, kind):
        for p in self.phases:
            if p.kind == kind:
                return p
        return None

    @property
    def H(self):
        return sum(p.beta * p.H for p in self.phases)

    @property
    def S(self):
        return sum(p.beta * p.S for p in self.phases)

    @property
    def Cp(self):
        return sum(p.beta * p.Cp for p in self.phases)

    @property
    def phase_label(self):
        kinds = [p.kind for p in self.phases]
        if kinds == ["V"]:
            return "Vapour"
        if len(kinds) == 1:
            return "Liquid" if kinds[0] == "L" else "Aqueous"
        return " + ".join({"V": "Vapour", "L": "Liquid", "W": "Aqueous"}[k] for k in kinds)


class FluidPackage:
    """Peng-Robinson (1978 alpha) with editable kij and Peneloux volume shift."""

    def __init__(self, comps: list[Component], kij: np.ndarray | None = None, name="Peng-Robinson"):
        self.name = name
        self.comps = list(comps)
        self.keys = [c.key for c in self.comps]
        n = len(self.comps)
        self.n = n
        self.Tc = np.array([c.Tc for c in comps], float)
        self.Pc = np.array([c.Pc for c in comps], float)
        self.w = np.array([c.omega for c in comps], float)
        self.MW = np.array([c.MW for c in comps], float)
        self.cpc = np.array([c.cp for c in comps], float)          # n x 4
        self.rho_std = np.array([c.rho_std for c in comps], float)
        zra = np.array([c.zra for c in comps], float)
        m = np.where(self.w <= 0.491,
                     0.37464 + 1.54226 * self.w - 0.26992 * self.w ** 2,
                     0.379642 + 1.48503 * self.w - 0.164423 * self.w ** 2 + 0.016666 * self.w ** 3)
        self.m = m
        self.ac = 0.45724 * R_BAR ** 2 * self.Tc ** 2 / self.Pc
        self.b = 0.07780 * R_BAR * self.Tc / self.Pc
        self.c_shift = 0.50033 * (0.25969 - zra) * R_BAR * self.Tc / self.Pc   # m3/mol (Peneloux form for PR)
        if kij is None:
            kij = np.array([[default_kij(a, b_) for b_ in comps] for a in comps], float)
        self.kij = np.array(kij, float)
        self.iw = self.keys.index("H2O") if "H2O" in self.keys else -1
        # polar components that belong to the aqueous phase (for phase labelling)
        self.polar = np.array([c.key in ("H2O", "MeOH", "MEG") for c in self.comps])
        self._kcache = {}
        self._tcache = {}
        # Peneloux with a Rackett Z_RA works well for hydrocarbons but badly for
        # water; calibrate water's shift to its 15 degC density instead.
        for i, c in enumerate(self.comps):
            if c.key in ("H2O", "MeOH", "MEG"):
                idx = np.array([i])
                _, Z, _ = self.lnphi(np.array([1.0]), T_STD, P_REF, idx, "L")
                v = Z * R_BAR * T_STD / P_REF
                self.c_shift[i] = v - c.MW / 1000.0 / c.rho_std

    # ------------------------------------------------------------------ io
    def to_dict(self):
        return {"name": self.name, "components": [c.to_dict() for c in self.comps],
                "kij": self.kij.tolist()}

    @staticmethod
    def from_dict(d):
        comps = [Component.from_dict(c) for c in d["components"]]
        return FluidPackage(comps, np.array(d["kij"], float), d.get("name", "Peng-Robinson"))

    @staticmethod
    def from_keys(keys, hypos=None):
        comps = []
        hypos = hypos or {}
        for k in keys:
            comps.append(LIBRARY[k] if k in LIBRARY else hypos[k])
        return FluidPackage(comps)

    def index(self, key):
        return self.keys.index(key)

    # --------------------------------------------------------------- pure
    def _a(self, T, idx):
        sq = np.sqrt(T / self.Tc[idx])
        al = (1.0 + self.m[idx] * (1.0 - sq)) ** 2
        a = self.ac[idx] * al
        dadT = -self.ac[idx] * self.m[idx] * (1.0 + self.m[idx] * (1.0 - sq)) / np.sqrt(T * self.Tc[idx])
        return a, dadT

    # --------------------------------------------------------- mixture
    def _mix(self, x, T, idx):
        """Return mixture a, b, dadT, and the vector sum_j x_j a_ij (for lnphi)."""
        key = idx.tobytes()
        # a_ij(T) and da_ij/dT depend on T and the component set only: flashes evaluate many compositions at the
        # same temperature (successive substitution, stability tests), so keep the matrices for recent T
        tk = (T, key)
        hit = self._tcache.get(tk)
        if hit is None:
            a, dadT = self._a(T, idx)
            k = self._kcache.get(key)
            if k is None:
                k = self.kij[np.ix_(idx, idx)]
                self._kcache[key] = k
            sqa = np.sqrt(a)
            aij = (1.0 - k) * np.outer(sqa, sqa)
            # d(aij)/dT = (1-k) * 0.5 * sqrt(ai aj) (ai'/ai + aj'/aj)
            r = dadT / a
            daij = 0.5 * aij * (r[:, None] + r[None, :])
            if len(self._tcache) > 512:
                self._tcache.clear()
            self._tcache[tk] = hit = (aij, daij)
        aij, daij = hit
        xa = aij @ x
        am = float(x @ xa)
        dam = float(x @ daij @ x)
        bm = float(x @ self.b[idx])
        return am, bm, dam, xa, aij, daij

    @staticmethod
    def _zroots(A, B):
        """Real roots > B of Z^3 - (1-B)Z^2 + (A-3B^2-2B)Z - (AB-B^2-B^3) = 0 (Cardano)."""
        a2 = -(1.0 - B)
        a1 = A - 3.0 * B * B - 2.0 * B
        a0 = -(A * B - B * B - B ** 3)
        q = (3.0 * a1 - a2 * a2) / 9.0
        r = (9.0 * a2 * a1 - 27.0 * a0 - 2.0 * a2 ** 3) / 54.0
        d = q ** 3 + r * r
        if d > 0:
            sd = math.sqrt(d)
            s1 = math.copysign(abs(r + sd) ** (1 / 3), r + sd)
            s2 = math.copysign(abs(r - sd) ** (1 / 3), r - sd)
            roots = [s1 + s2 - a2 / 3.0]
        else:
            th = math.acos(max(-1.0, min(1.0, r / math.sqrt(-q ** 3)))) if q < 0 else 0.0
            m = 2.0 * math.sqrt(-q) if q < 0 else 0.0
            roots = sorted(m * math.cos((th + 2 * math.pi * k) / 3.0) - a2 / 3.0 for k in range(3))
        # polish with Newton
        out = []
        for z in roots:
            for _ in range(3):
                f = ((z + a2) * z + a1) * z + a0
                df = (3 * z + 2 * a2) * z + a1
                if df == 0:
                    break
                z -= f / df
            if z > B:
                out.append(z)
        if not out:
            out = [max(roots[-1], B * 1.0000001)]
        return sorted(out)

    def _lnphi_z(self, x, T, P, idx, am, bm, xa, Z):
        A = am * P / (R_BAR * T) ** 2
        B = bm * P / (R_BAR * T)
        bi = self.b[idx] / bm
        L = math.log((Z + (1 + SQ2) * B) / (Z + (1 - SQ2) * B))
        return bi * (Z - 1.0) - math.log(Z - B) - A / (2 * SQ2 * B) * (2.0 * xa / am - bi) * L

    def lnphi(self, x, T, P, idx, want=None):
        """ln fugacity coefficients choosing the minimum-Gibbs root.

        want: None (min G), 'V' (largest root) or 'L' (smallest root).
        Returns (lnphi, Z, mix-tuple)."""
        am, bm, dam, xa, aij, daij = self._mix(x, T, idx)
        A = am * P / (R_BAR * T) ** 2
        B = bm * P / (R_BAR * T)
        roots = self._zroots(A, B)
        if len(roots) == 1 or want == "V":
            Z = roots[-1]
        elif want == "L":
            Z = roots[0]
        else:
            best, Z = None, roots[0]
            for zr in (roots[0], roots[-1]):
                lp = self._lnphi_z(x, T, P, idx, am, bm, xa, zr)
                g = float(x @ lp)
                if best is None or g < best:
                    best, Z = g, zr
        lp = self._lnphi_z(x, T, P, idx, am, bm, xa, Z)
        return lp, Z, (am, bm, dam)

    # ------------------------------------------------------- caloric
    def h_ig(self, T, idx):
        c = self.cpc[idx]
        t0 = T_REF
        return (c[:, 0] * (T - t0) + c[:, 1] / 2 * (T ** 2 - t0 ** 2)
                + c[:, 2] / 3 * (T ** 3 - t0 ** 3) + c[:, 3] / 4 * (T ** 4 - t0 ** 4))

    def s_ig_T(self, T, idx):
        c = self.cpc[idx]
        t0 = T_REF
        return (c[:, 0] * math.log(T / t0) + c[:, 1] * (T - t0)
                + c[:, 2] / 2 * (T ** 2 - t0 ** 2) + c[:, 3] / 3 * (T ** 3 - t0 ** 3))

    def cp_ig(self, T, idx):
        c = self.cpc[idx]
        return c[:, 0] + c[:, 1] * T + c[:, 2] * T ** 2 + c[:, 3] * T ** 3

    def phase_props(self, x, T, P, idx, Z, mix):
        """H, S, Cp, Cv, V for one phase (x over idx)."""
        am, bm, dam = mix
        B = bm * P / (R_BAR * T)
        V = Z * R_BAR * T / P
        L = math.log((Z + (1 + SQ2) * B) / (Z + (1 - SQ2) * B))
        hdep = R * T * (Z - 1.0) + 1e5 * (T * dam - am) / (2 * SQ2 * bm) * L
        sdep = R * math.log(Z - B) + 1e5 * dam / (2 * SQ2 * bm) * L
        H = float(x @ self.h_ig(T, idx)) + hdep
        xs = x[x > 0]
        S = float(x @ self.s_ig_T(T, idx)) - R * math.log(P / P_REF) - R * float(xs @ np.log(xs)) + sdep
        # heat capacities from analytic PR derivatives (second T derivative numerically)
        eps = 1e-3 * T
        am2, _, dam2, _, _, _ = self._mix(x, T + eps, idx)
        d2a = (dam2 - dam) / eps
        cv_dep = 1e5 * T * d2a / (2 * SQ2 * bm) * L
        cp_ig = float(x @ self.cp_ig(T, idx))
        cv = cp_ig - R + cv_dep
        denom = V * V + 2 * bm * V - bm * bm
        dPdT = R_BAR / (V - bm) - dam / denom
        dPdV = -R_BAR * T / (V - bm) ** 2 + am * (2 * V + 2 * bm) / denom ** 2
        cp = cv - 1e5 * T * dPdT ** 2 / dPdV if dPdV < 0 else cv + R
        Vs = V - float(x @ self.c_shift[idx])
        if Vs <= 0:
            Vs = V
        return H, S, cp, cv, V, Vs

    # ------------------------------------------------------------ flash
    def _present(self, z):
        idx = np.nonzero(z > 1e-15)[0]
        zz = z[idx]
        return idx, zz / zz.sum()

    def _wilson(self, T, P, idx):
        return self.Pc[idx] / P * np.exp(5.373 * (1.0 + self.w[idx]) * (1.0 - self.Tc[idx] / T))

    def _label_single(self, x, Z, T, P, idx, mix):
        am, bm, dam = mix
        V = Z * R_BAR * T / P
        iw = self._local_water(idx)
        if V / bm > 1.75:
            return "V"
        if iw >= 0 and float(x @ self.polar[idx]) > 0.5:
            return "W"
        return "L"

    def _local_water(self, idx):
        if self.iw < 0:
            return -1
        hits = np.nonzero(idx == self.iw)[0]
        return int(hits[0]) if hits.size else -1

    def _make_phase(self, kind, beta, xloc, T, P, idx, Z, mix):
        H, S, cp, cv, V, Vs = self.phase_props(xloc, T, P, idx, Z, mix)
        x = np.zeros(self.n)
        x[idx] = xloc
        return Phase(kind, beta, x, float(Z), V, Vs, float(xloc @ self.MW[idx]), H, S, cp, cv)

    def pt_flash(self, z, T, P, K0=None) -> FlashResult:
        z = np.asarray(z, float)
        if T <= 0 or P <= 0:
            raise FlashError(f"Invalid state T={T} K, P={P} bar")
        idx, zl = self._present(z)
        res = FlashResult(T, P, z / z.sum())
        if idx.size == 1:
            lp, Z, mix = self.lnphi(zl, T, P, idx)
            kind = self._label_single(zl, Z, T, P, idx, mix)
            res.phases = [self._make_phase(kind, 1.0, zl, T, P, idx, Z, mix)]
            return res

        iw = self._local_water(idx)
        three = iw >= 0 and 1e-8 < zl[iw] < 1.0 - 1e-8
        nrow = 2 if three else 1

        def free_water():
            KW = np.full(idx.size, 1e-6)
            KW[iw] = 1e3
            return KW

        ksets = {}

        def run(rows):
            self._last_lnK = None
            self._last_conv = False
            ph = self._ss(zl, T, P, idx, np.array(rows))
            # keep K as a warm-start seed only when every row describes a present phase: with a vanished
            # reference phase the relative K are ill-defined and would seed a wrong (unconverged) split
            if ph is not None and len(ph) == nrow + 1 and self._last_conv and self._last_lnK is not None \
                    and self._last_lnK.shape[0] == nrow:
                full = np.ones((nrow, self.n))
                full[:, idx] = np.exp(self._last_lnK)
                ksets[id(ph)] = full
            return ph

        maxph = 3 if three else 2

        def complete(ph):
            """A split with fewer than the possible phases is accepted only if its dominant
            non-aqueous phase is stable (no missing phase)."""
            if ph is None or len(ph) < 2:
                return False
            if len(ph) >= maxph:
                return True
            cand = max((q for q in ph if q.kind != "W"), key=lambda q: q.beta, default=ph[0])
            x = np.maximum(cand.x[idx], 1e-300)
            f = self._stability(x / x.sum(), T, P, idx)
            present = {q.kind for q in ph}
            f = [t for t in f if not (t[2] == "W" and "W" in present)]
            return not f

        def gibbs(ph):
            g = 0.0
            for q in ph:
                x = np.maximum(q.x[idx], 1e-300)
                x = x / x.sum()
                lp, _, _ = self.lnphi(x, T, P, idx)
                g += q.beta * float(x @ (np.log(x) + lp))
            return g

        phases = None
        if K0 is not None and np.ndim(K0) == 2 and K0.shape == (nrow, self.n):
            phases = run([np.maximum(K0[r][idx], 1e-30) for r in range(nrow)])      # warm start
            if phases is not None and (not self._last_conv or (len(phases) > 1 and not complete(phases))):
                phases = None
        elif K0 is not None and np.ndim(K0) == 1 and len(K0) == self.n:
            phases = run([np.maximum(K0[idx], 1e-30)] + ([free_water()] if three else []))
            if phases is not None and (not self._last_conv or (len(phases) > 1 and not complete(phases))):
                phases = None
        if phases is None or len(phases) == 1:
            # Michelsen stability test first: a stable feed is single phase (cheap exit);
            # otherwise split from Wilson + free-water K, then from the stability trials,
            # keeping the first complete split (or the lowest Gibbs energy one).
            found = self._stability(zl, T, P, idx)
            if found:
                cands = []
                ph1 = run([self._wilson(T, P, idx)] + ([free_water()] if three else []))
                if ph1 is not None and len(ph1) > 1:
                    cands.append(ph1)
                if not complete(ph1):
                    hc = [f for f in found if f[2] != "W"]
                    wat = [f for f in found if f[2] == "W"]
                    rows = [np.maximum(min(hc, key=lambda f: f[0])[1] / zl, 1e-30)] if hc \
                        else [self._wilson(T, P, idx)]
                    if three:
                        rows.append(np.maximum(wat[0][1] / zl, 1e-30) if wat else free_water())
                    ph2 = run(rows)
                    if ph2 is not None and len(ph2) > 1:
                        cands.append(ph2)
                    if three and not complete(ph2):
                        # last resort: all three phases seeded explicitly
                        ph3 = run([self._wilson(T, P, idx), free_water()])
                        if ph3 is not None and len(ph3) > 1:
                            cands.append(ph3)
                good = [c for c in cands if len(c) >= maxph]
                if good:
                    phases = good[0]
                elif cands:
                    phases = min(cands, key=gibbs)
        if phases is None or len(phases) == 1:
            lp, Z, mix = self.lnphi(zl, T, P, idx)
            kind = self._label_single(zl, Z, T, P, idx, mix)
            res.phases = [self._make_phase(kind, 1.0, zl, T, P, idx, Z, mix)]
            return res
        res.phases = phases
        res.Kset = ksets.get(id(phases))       # warm-start seed describing the chosen split
        pv = res.phase("V")
        pl = res.phase("L") or res.phase("W")
        if pv is not None and pl is not None:
            with np.errstate(divide="ignore", invalid="ignore"):
                K = np.where(pl.x > 0, pv.x / np.maximum(pl.x, 1e-300), 1.0)
            res.K = K
        return res

    @staticmethod
    def _rr2(z, K):
        """Classic two-phase Rachford-Rice (vapour fraction clamped to [0, 1])."""
        Km1 = K - 1.0
        if float(z @ K) <= 1.0:          # below bubble point
            beta = 0.0
        elif float(z @ (1.0 / K)) <= 1.0:  # above dew point
            beta = 1.0
        else:
            lo = 1.0 / (1.0 - K.max()) + 1e-14
            hi = 1.0 / (1.0 - K.min()) - 1e-14
            lo, hi = max(lo, 0.0), min(hi, 1.0)
            beta = 0.5 * (lo + hi)
            for _ in range(100):
                d = 1.0 + beta * Km1
                f = float(z @ (Km1 / d))
                if f > 0:
                    lo = beta
                else:
                    hi = beta
                df = -float(z @ (Km1 * Km1 / (d * d)))
                bn = beta - f / df if df != 0 else 0.5 * (lo + hi)
                if not (lo < bn < hi):
                    bn = 0.5 * (lo + hi)
                if abs(bn - beta) < 1e-14:
                    beta = bn
                    break
                beta = bn
        b = np.array([1.0 - beta, beta])
        return b, 1.0 + beta * Km1

    @staticmethod
    def _rr(z, K, beta0=None):
        """Michelsen multiphase Rachford-Rice. K: (np, n) with reference row ones."""
        npha = K.shape[0]
        if npha == 2:
            return FluidPackage._rr2(z, K[1])
        beta = np.full(npha, 1.0 / npha) if beta0 is None else np.maximum(beta0, 1e-10)
        for _ in range(100):
            E = K.T @ beta
            g = 1.0 - K @ (z / E)
            H = (K * (z / E ** 2)) @ K.T + np.eye(npha) * 1e-14
            free = (beta > 0) | (g < 0)
            if np.all(np.abs(g[free]) < 1e-13):
                break
            d = np.zeros(npha)
            f = np.nonzero(free)[0]
            try:
                d[f] = np.linalg.solve(H[np.ix_(f, f)], -g[f])
            except np.linalg.LinAlgError:
                d[f] = -g[f]
            alpha = 1.0
            hit = -1
            for k in range(npha):
                if d[k] < 0 and beta[k] + alpha * d[k] < 0:
                    alpha = beta[k] / -d[k]
                    hit = k
            Q0 = beta.sum() - float(z @ np.log(E))
            for _ls in range(30):
                nb = beta + alpha * d
                if hit >= 0 and alpha == beta[hit] / -d[hit]:
                    nb[hit] = 0.0
                nb = np.maximum(nb, 0.0)
                En = K.T @ nb
                if np.all(En > 0):
                    Qn = nb.sum() - float(z @ np.log(En))
                    if Qn <= Q0 + 1e-15:
                        break
                alpha *= 0.5
                hit = -1
            beta = nb
            if np.max(np.abs(alpha * d)) < 1e-15:
                break
        E = K.T @ beta
        return beta, E

    def _ss(self, zl, T, P, idx, Ks):
        """Successive substitution. Ks: (np-1, n) K of non-reference phases."""
        npha = Ks.shape[0] + 1
        lnK = np.log(np.maximum(Ks, 1e-300))
        beta = None
        it = 0
        for it in range(1, 400):
            K = np.vstack([np.ones(idx.size), np.exp(lnK)])
            beta, E = self._rr(zl, K, beta)
            xref = zl / E
            X = K * xref
            X = X / X.sum(axis=1, keepdims=True)
            lps = []
            for k in range(npha):
                lp, Z, mix = self.lnphi(X[k], T, P, idx)
                lps.append((lp, Z, mix))
            new = np.array([lps[0][0] - lps[k][0] for k in range(1, npha)])
            err = float(np.max(np.abs(new - lnK)))
            lnK = new
            if err < 1e-10:
                break
            # trivial-solution detection: all non-ref phases collapsing onto the reference
            if it > 8 and np.all(np.max(np.abs(lnK), axis=1) < 1e-4):
                return None
            # the reference phase has vanished from a three-phase split: its K are then ill-defined and
            # SS crawls, so re-reference to the two phases that remain and finish as a two-phase flash
            if npha == 3 and it > 6 and beta[0] <= 0.0 and beta[1] > 0.0 and beta[2] > 0.0:
                return self._ss(zl, T, P, idx, np.exp(lnK[1] - lnK[0])[None, :])
        self._last_lnK = lnK
        self._last_conv = err < 1e-7
        K = np.vstack([np.ones(idx.size), np.exp(lnK)])
        beta, E = self._rr(zl, K, beta)
        xref = zl / E
        X = K * xref
        X = X / X.sum(axis=1, keepdims=True)
        keep = []
        for k in range(npha):
            if beta[k] < 1e-12:
                continue
            dup = False
            for j in keep:
                if np.max(np.abs(X[k] - X[j])) < 1e-6:
                    beta[j] += beta[k]
                    dup = True
            if not dup:
                keep.append(k)
        if len(keep) <= 1:
            return None
        out = []
        for k in keep:
            lp, Z, mix = self.lnphi(X[k], T, P, idx)
            out.append((beta[k], X[k], Z, mix))
        tot = sum(o[0] for o in out)
        # label phases: lowest density -> vapour if vapour-like, water-rich -> aqueous
        iw = self._local_water(idx)
        vols = [o[2] * R_BAR * T / P / o[3][1] for o in out]   # V/b
        order = np.argsort([-v for v in vols])
        labels = [None] * len(out)
        first = order[0]
        labels[first] = "V" if vols[first] > 1.75 or len(out) == 3 else "L"
        for k in order[1:]:
            if iw >= 0 and float(out[k][1] @ self.polar[idx]) > 0.5:
                labels[k] = "W"
            else:
                labels[k] = "L"
        if labels[first] == "L" and all(l == "L" for l in labels):
            labels[first] = "V" if vols[first] > 1.2 else "L"
        # two liquids both labelled L (e.g. LLE without water): lighter stays L, heavier 'W'
        seen = set()
        for k in order:
            if labels[k] in seen:
                labels[k] = "W" if labels[k] == "L" else "L"
            seen.add(labels[k])
        phases = [self._make_phase(labels[k], out[k][0] / tot, out[k][1], T, P, idx, out[k][2], out[k][3])
                  for k in range(len(out))]
        rank = {"V": 0, "L": 1, "W": 2}
        phases.sort(key=lambda p: rank[p.kind])
        return phases

    def _stability(self, zl, T, P, idx):
        """Michelsen tangent-plane stability test.

        Returns a list of (tm, trial_composition, kind) for every trial that proves the
        feed unstable (kind 'V' vapour-like, 'L' liquid-like, 'W' water-like); empty if stable.
        A trial is stopped as soon as its modified TPD goes negative (instability proven)
        or it collapses onto the feed (trivial)."""
        lpz, Zz, _ = self.lnphi(zl, T, P, idx)
        dz = np.log(zl) + lpz
        Kw = self._wilson(T, P, idx)
        trials = [("V", zl * Kw), ("L", zl / Kw)]
        iw = self._local_water(idx)
        if iw >= 0:
            t = np.full(idx.size, 1e-6)
            t[iw] = 1.0
            trials.append(("W", t))
        found = []
        for kind, W in trials:
            W = W / W.sum()
            lnW = np.log(W)
            tm = 0.0
            for _ in range(150):
                y = np.exp(lnW)
                y = y / y.sum()
                lpy, _, _ = self.lnphi(y, T, P, idx)
                Wv = np.exp(lnW)
                tm = 1.0 + float(Wv @ (lnW + lpy - dz - 1.0))
                if tm < -1e-7:
                    break
                new = dz - lpy
                if np.max(np.abs(new - lnW)) < 1e-10:
                    lnW = new
                    break
                lnW = new
                if np.max(np.abs(y - zl)) < 1e-6:
                    break
            Wv = np.exp(lnW)
            y = Wv / Wv.sum()
            if np.max(np.abs(y - zl)) < 1e-5:
                continue
            tm = min(tm, 1.0 - Wv.sum())
            if tm < -1e-7:
                found.append((tm, y, kind))
        return found

    # --------------------------------------------------- pure saturation
    def psat_pure(self, i, T):
        """Saturation pressure of pure component i at T (bar); None above Tc."""
        if T >= self.Tc[i]:
            return None
        idx = np.array([i])
        x = np.array([1.0])
        Tr = T / self.Tc[i]
        P = self.Pc[i] * 10 ** (7.0 / 3.0 * (1 + self.w[i]) * (1 - 1 / Tr))
        for _ in range(100):
            lpl, Zl, _ = self.lnphi(x, T, P, idx, "L")
            lpv, Zv, _ = self.lnphi(x, T, P, idx, "V")
            if abs(Zl - Zv) < 1e-7:
                P *= 0.9 if Zl > 0.3 else 1.1
                continue
            f = lpl[0] - lpv[0]
            Pn = P * math.exp(f)
            if abs(Pn - P) < 1e-10 * P:
                return Pn
            P = Pn
        return P

    def tsat_pure(self, i, P):
        if P >= self.Pc[i]:
            return None
        lo, hi = 0.3 * self.Tc[i], self.Tc[i] * 0.998
        from scipy.optimize import brentq

        def f(T):
            ps = self.psat_pure(i, T)
            return math.log(ps / P)
        try:
            if f(lo) > 0 or f(hi) < 0:
                return None
            return brentq(f, lo, hi, xtol=1e-9)
        except Exception:
            return None

    def _pure_two_phase(self, z, T, P, vf):
        idx, zl = self._present(z)
        i = idx[0]
        x = np.array([1.0])
        lpl, Zl, mixl = self.lnphi(x, T, P, idx, "L")
        lpv, Zv, mixv = self.lnphi(x, T, P, idx, "V")
        res = FlashResult(T, P, z / z.sum())
        ph = []
        if vf > 1e-12:
            ph.append(self._make_phase("V", vf, x, T, P, idx, Zv, mixv))
        if vf < 1 - 1e-12:
            kind = "W" if i == self.iw else "L"
            ph.append(self._make_phase(kind, 1 - vf, x, T, P, idx, Zl, mixl))
        res.phases = ph
        return res

    # ----------------------------------------------- state-function flashes
    def _t_flash(self, z, P, target, prop, T0, lo=60.0, hi=1500.0, K0=None):
        """Find T such that prop(PT-flash) = target. prop in {'H','S'}."""
        z = np.asarray(z, float)
        idx, zl = self._present(z)
        if idx.size == 1:
            i = idx[0]
            Ts = self.tsat_pure(i, P)
            if Ts is not None:
                l = self._pure_two_phase(z, Ts, P, 0.0)
                v = self._pure_two_phase(z, Ts, P, 1.0)
                gl, gv = getattr(l, prop), getattr(v, prop)
                if gl <= target <= gv:
                    vf = (target - gl) / (gv - gl)
                    return self._pure_two_phase(z, Ts, P, vf)
                if target < gl:
                    hi = Ts - 1e-6
                else:
                    lo = Ts + 1e-6
                T0 = min(max(T0, lo + 1), hi - 1)
        from scipy.optimize import brentq
        cache = {}
        Kw = [K0]

        def f(T):
            r = self.pt_flash(z, T, P, Kw[0])
            if r.Kset is not None:
                Kw[0] = r.Kset
            cache[T] = r
            return getattr(r, prop) - target

        # secant from T0
        T1 = min(max(T0, lo + 1), hi - 1)
        f1 = f(T1)
        if abs(f1) < 1e-6:
            return cache[T1]
        dT = 5.0 if f1 < 0 else -5.0
        T2 = min(max(T1 + dT, lo), hi)
        f2 = f(T2)
        for _ in range(25):
            if abs(f2) < 1e-6 * max(1.0, abs(target)) or abs(T2 - T1) < 1e-8:
                return cache[T2]
            if f2 == f1:
                break
            T3 = T2 - f2 * (T2 - T1) / (f2 - f1)
            if not (lo <= T3 <= hi) or abs(T3 - T2) > 150:
                break
            T1, f1, T2, f2 = T2, f2, T3, f(T3)
        # bracket and Brent
        a, b_ = T0 - 20, T0 + 20
        a, b_ = max(a, lo), min(b_, hi)
        fa, fb = f(a), f(b_)
        step = 40.0
        while fa * fb > 0:
            if fa > 0:
                a = max(lo, a - step)
                fa = f(a)
                if a <= lo and fa > 0:
                    raise FlashError(f"{prop}-flash: no solution above {lo} K at P={P:.3f} bar")
            else:
                b_ = min(hi, b_ + step)
                fb = f(b_)
                if b_ >= hi and fb < 0:
                    raise FlashError(f"{prop}-flash: no solution below {hi} K at P={P:.3f} bar")
            step *= 1.6
        T = brentq(f, a, b_, xtol=1e-7, maxiter=200)
        return cache.get(T) or self.pt_flash(z, T, P)

    def ph_flash(self, z, P, H, T0=300.0, K0=None):
        return self._t_flash(z, P, H, "H", T0, K0=K0)

    def ps_flash(self, z, P, S, T0=300.0, K0=None):
        return self._t_flash(z, P, S, "S", T0, K0=K0)

    def pvf_flash(self, z, P, vf, T0=300.0):
        """T at given P and vapour fraction (0 = bubble point, 1 = dew point)."""
        z = np.asarray(z, float)
        idx, zl = self._present(z)
        if idx.size == 1:
            Ts = self.tsat_pure(idx[0], P)
            if Ts is None:
                raise FlashError("Pure component above critical pressure: no vapour fraction spec")
            return self._pure_two_phase(z, Ts, P, vf)
        from scipy.optimize import brentq
        target = min(max(vf, 1e-6), 1 - 1e-6)

        def g(T):
            return self.pt_flash(z, T, P).vf - target

        # scan for bracket
        Ts = np.linspace(80, 900, 83)
        prev_T, prev = None, None
        for T in Ts:
            val = g(T)
            if prev is not None and prev < 0 <= val:
                T = brentq(g, prev_T, T, xtol=1e-6)
                return self.pt_flash(z, T, P)
            prev_T, prev = T, val
        raise FlashError(f"No T with vapour fraction {vf} at P={P:.3f} bar")

    # ------------------------------------------------------- envelope
    def phase_envelope(self, z, P_list, T_min=150.0, T_max=700.0, nT=90):
        """Vapour-fraction map on a P-T grid (for contouring the envelope)."""
        z = np.asarray(z, float)
        Ts = np.linspace(T_min, T_max, nT)
        grid = np.full((len(P_list), nT), np.nan)
        for i, P in enumerate(P_list):
            K = None
            for j, T in enumerate(Ts):
                try:
                    r = self.pt_flash(z, T, P, K)
                    K = r.K if r.K is not None else K
                    grid[i, j] = r.vf
                except Exception:
                    pass
        return Ts, np.array(P_list, float), grid
