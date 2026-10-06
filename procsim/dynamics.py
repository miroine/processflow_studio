"""Dynamic (time-domain) simulation of a flowsheet - lumped holdups, pressure-driven flows, controllers and events.

How it works
------------
The dynamic model is *built from the converged steady-state solution* (as in HYSYS / Aspen "dynamic mode") so that at
t = 0 it reproduces the steady state exactly and stays there until something disturbs it:

* **Holdups** (separators, scrubbers, mixers, pipe cells) carry a component inventory N and an internal energy U in a
  fixed volume; their temperature, pressure, phase split and liquid level follow from a rigorous UV flash
  (:func:`procsim.dyn_thermo.uv_flash`).
* **Flow elements** (control valves, compressors, pumps, relief devices, line resistances) set the flows from the
  pressures at their ends: valves by an IEC-type equation, compressors from their performance curve with speed, pumps from
  a head curve.  Every coefficient is calibrated at the steady state, so the flows are those of the steady solution at t = 0.
* **Quasi-steady units** between holdups (heaters, coolers, expanders ...) pass the flow through and apply their steady
  enthalpy change; they have no inventory.
* **Controllers** (PID) drive valve openings or compressor speed; **events** change feed rates, set-points, trip equipment
  or open blowdown valves at given times.
* The pressures are advanced **semi-implicitly** (the flows are linearised in the node pressures and the linear system
  solved for the pressure change), which keeps small gas volumes from limiting the time step; compositions and energies
  are explicit.  Moles are conserved exactly between holdups.

Limits (lumped model): no pressure waves or momentum (flows follow pressure differences instantly), well-mixed holdups, no
phase slip in lines (homogeneous flow), simple nozzle rules for vessel outlets.  A vessel that fills completely with liquid
is not supported.
"""
from __future__ import annotations

import copy
import math
import time as _time

import numpy as np

from . import dyn_thermo as DT
from .flowsheet import port_edges, feed_stream
from .thermo import V_STD_GAS

K0 = 273.15
NODE_TYPES = ("separator", "separator3", "scrubber", "mixer")
SINK_TYPES = ("product", "flare")
FLOW_TYPES = ("valve", "compressor", "pump")
PIPE_TYPES = ("pipe", "flowline")
QUASI_TYPES = ("heater", "cooler", "aircooler", "recycle", "expander", "valve", "pump", "compressor")
SPLITTERS = ("splitter",)
DP_PIPE = 0.02                   # bar: smallest friction drop a pipe interface is built for
DP_MIN = 0.15                    # bar: smallest pressure drop a calibrated resistance / valve is built for


class DynError(ValueError):
    pass


# ================================================================================================ holdup
class Node:
    """A lumped holdup volume."""

    def __init__(self, name, kind, V, orient="Horizontal", has_level=True):
        self.name, self.kind, self.V, self.orient, self.has_level = name, kind, float(V), orient, has_level
        self.N = None            # kmol per component
        self.U = 0.0             # kJ
        self.T = self.P = None
        self.fr = None
        self.ph = {}
        self.J = None            # warm-start Jacobian of the UV flash
        self.kappa = 1.0         # dP/dN_total estimate [bar/kmol]
        self.Q_kW = 0.0          # heat added to the fluid (heater duty, fire, ...)
        self.wall = None         # dict(C kJ/K, UA_in kW/K, UA_out kW/K, T_amb K)
        self.Tw = None
        self.fixed_T = None      # isothermal holdup (pipe cell)
        self.level = 0.0         # % of diameter / height
        self.level_w = 0.0
        self.V_liq = 0.0
        self.P_ss = None
        self.T_ss = None
        self.uid = None
        self.mw = None

    # ---- state
    def get_state(self):
        return (self.N.copy(), self.U, self.T, self.P, self.J if self.J is None else self.J.copy(), self.kappa, self.Tw)

    def set_state(self, s, fp):
        self.N, self.U, self.T, self.P, J, self.kappa, self.Tw = s[0].copy(), s[1], s[2], s[3], s[4], s[5], s[6]
        self.J = None if J is None else J.copy()
        self.refresh(fp, resolve=False)

    @property
    def Ntot(self):
        return float(self.N.sum())

    def refresh(self, fp, resolve=True):
        """Solve T, P and phases from the inventory (and energy)."""
        try:
            if self.fixed_T is not None:
                if resolve:
                    self.fr, self.P = _tv_pressure(fp, self.N, self.V, self.fixed_T, self.P, self.fr)
                    self.T = self.fixed_T
                else:
                    self.fr, _, _ = DT.vol_energy(fp, self.N / self.Ntot, self.Ntot, self.T, self.P,
                                                  None if self.fr is None else self.fr.Kset)
            elif resolve:
                self.fr, self.T, self.P, self.J = DT.uv_flash(fp, self.N, self.V, self.U, self.T, self.P,
                                                              None if self.fr is None else self.fr.Kset, self.J)
            else:
                self.fr, _, _ = DT.vol_energy(fp, self.N / self.Ntot, self.Ntot, self.T, self.P,
                                              None if self.fr is None else self.fr.Kset)
        except DT.UVError as e:
            raise DynError(f"{self.name}: {e}")
        self.ph = DT.phase_data(self.fr, self.Ntot)
        liq = [k for k in self.ph if k != "V"]
        self.V_liq = sum(self.ph[k]["vol"] for k in liq)
        if self.has_level:
            self.level = DT.level_pct(self.orient, self.V_liq, self.V)
            vw = self.ph["W"]["vol"] if "W" in self.ph else 0.0
            self.level_w = DT.level_pct(self.orient, vw, self.V)

    # ---- outlet properties of a phase port
    _KINDS = {"vapour": ("V",), "liquid": ("L", "W"), "oil": ("L",), "water": ("W",), "out": ("V", "L", "W"),
              "mix": ("V", "L", "W")}

    def port(self, port, MWv):
        kinds = [k for k in self._KINDS.get(port, ("V", "L", "W")) if k in self.ph]
        n = sum(self.ph[k]["n"] for k in kinds)
        if n <= 1e-12:
            return None
        z = sum(self.ph[k]["n"] * self.ph[k]["x"] for k in kinds) / n
        H = sum(self.ph[k]["n"] * self.ph[k]["H"] for k in kinds) / n
        MW = float(z @ MWv)
        vol = sum(self.ph[k]["vol"] for k in kinds)
        rho = n * MW / vol if vol > 0 else 0.0
        v = self.ph.get("V")
        gas = port == "vapour" or (port in ("out", "mix") and self.fr.vf >= 0.5)
        ref = v if (v is not None and gas) else self.ph[kinds[0]]
        return {"z": z, "H": H, "MW": MW, "rho": rho, "k": ref["k"], "Z": ref["Z"], "n": n, "gas": gas,
                "T": self.T, "P": self.P}


def _tv_pressure(fp, N, V, T, P0, fr0):
    """Pressure of a closed volume at a fixed temperature (isothermal holdup); secant on ln P."""
    from . import dyn_thermo as D
    Ntot = float(N.sum())
    z = N / Ntot
    K = None if fr0 is None else fr0.Kset

    def f(lnP):
        fr, Vc, _ = D.vol_energy(fp, z, Ntot, T, math.exp(lnP), K)
        return (Vc - V) / V, fr

    x1 = math.log(P0)
    f1, fr1 = f(x1)
    x2 = x1 + (0.02 if f1 > 0 else -0.02)
    f2, fr2 = f(x2)
    for _ in range(40):
        if abs(f2) < 1e-10:
            return fr2, math.exp(x2)
        if f2 == f1:
            break
        x3 = x2 - f2 * (x2 - x1) / (f2 - f1)
        x3 = min(max(x3, x2 - 0.5), x2 + 0.5)
        x1, f1 = x2, f2
        x2 = x3
        f2, fr2 = f(x2)
    if abs(f2) < 1e-7:
        return fr2, math.exp(x2)
    raise D.UVError("isothermal pressure solve did not converge (liquid-full line?)")


# ================================================================================================ boundaries
class Sink:
    def __init__(self, name, P):
        self.name, self.P, self.P0 = name, float(P), float(P)
        self.cum_kmol = 0.0
        self.cum_kg = 0.0
        self.flow = 0.0                 # kmol/h now
        self.kg_h = 0.0
        self.stdgas = 0.0               # MSm3/d (vapour-like inflow)
        self.uid = None

    def get_state(self):
        return (self.cum_kmol, self.cum_kg, self.P)

    def set_state(self, s, fp=None):
        self.cum_kmol, self.cum_kg, self.P = s


class Feed:
    def __init__(self, name, z, F, T, P, basis, per_unit):
        self.name, self.z = name, np.asarray(z, float)
        self.basis, self.per_unit = basis, per_unit          # kmol/h per unit of the feed's flow basis
        self.sF = [(0.0, float(F))]
        self.sT = [(0.0, float(T))]
        self.sP = [(0.0, float(P))]
        self._H = {}
        self.uid = None
        self.cum_kmol = 0.0

    def _val(self, sched, t):
        ts = [a for a, _ in sched]
        vs = [b for _, b in sched]
        return float(np.interp(t, ts, vs))

    def F(self, t):
        return self._val(self.sF, t)

    def T(self, t):
        return self._val(self.sT, t)

    def P(self, t):
        return self._val(self.sP, t)

    def H(self, t, fp):
        T, P = round(self.T(t), 3), round(self.P(t), 4)
        key = (T, P)
        if key not in self._H:
            self._H[key] = fp.pt_flash(self.z, T, P).H
        return self._H[key]

    def get_state(self):
        return (self.cum_kmol, list(self.sF), list(self.sT), list(self.sP))

    def set_state(self, s, fp=None):
        self.cum_kmol, self.sF, self.sT, self.sP = s[0], list(s[1]), list(s[2]), list(s[3])

    def ramp(self, sched, t, value, ramp):
        """Hold the present value until t, then move linearly to `value` over `ramp` seconds."""
        cur = self._val(sched, t)
        sched[:] = [(a, b) for a, b in sched if a < t - 1e-12]
        sched.append((t, cur))
        sched.append((t + max(ramp, 1e-6), float(value)))


# ================================================================================================ ops
class Op:
    """Quasi-steady unit between a flow element and the next holdup: returns the molar enthalpy after it."""

    def __init__(self, kind, **kw):
        self.kind = kind
        self.__dict__.update(kw)
        self._cache = {}

    def apply(self, z, H, fp):
        if self.kind == "frozen":
            return H + self.dh
        if self.kind == "none":
            return H
        if self.kind == "T_out":
            key = (tuple(np.round(z, 3)), round(self.T, 2), round(self.P, 3))
            if key not in self._cache:
                self._cache[key] = fp.pt_flash(z, self.T, self.P).H
            return self._cache[key]
        raise DynError(f"unknown op {self.kind}")


class Dest:
    def __init__(self, obj, frac, dpd, ops, last_sid=None):
        self.obj, self.frac, self.dpd, self.ops, self.last_sid = obj, frac, dpd, ops, last_sid


# ================================================================================================ flow elements
class Branch:
    """A path from a source port (holdup outlet or feed) to one or more destinations, with one flow-setting element."""
    kind = "res"
    pressure_driven = True

    def __init__(self, name, src, port, dests, dpu=0.0, pre_ops=None):
        self.name, self.src, self.port, self.dests, self.dpu = name, src, port, dests, dpu
        self.pre_ops = pre_ops or []
        self.w = 0.0                  # kmol/h at the last evaluation
        self.uid = None
        self.mass = 0.0
        self.sp = None
        self.cap = True

    def get_state(self):
        return ()

    def set_state(self, s, fp=None):
        pass

    def Pu(self):
        return self.src.P - self.dpu

    def Pd(self, dest=None):
        d = dest or self.dests[0]
        return d.obj.P + d.dpd

    def flow(self, Pu, Pd, sp):
        raise NotImplementedError

    def dH(self, sp, w, Pu, Pd):
        return 0.0

    def advance(self, dt, fp):
        pass

    def compressible(self, sp):
        return bool(sp["gas"])


class ValveBranch(Branch):
    kind = "valve"

    def __init__(self, *a, K=1.0, char="Equal percentage", xT=0.7, x0=0.5, tau=5.0, **kw):
        super().__init__(*a, **kw)
        self.K, self.char, self.xT, self.tau = K, char, xT, tau
        self.x = self.x_cmd = x0
        self.ctrl = None

    def get_state(self):
        return (self.x, self.x_cmd)

    def set_state(self, s, fp=None):
        self.x, self.x_cmd = s

    def flow(self, Pu, Pd, sp):
        drv, self.choked = DT.valve_driving(Pu, Pd, sp["rho"], sp["k"], self.compressible(sp), self.xT)
        m = self.K * DT.valve_char(self.x, self.char) * drv
        return m / sp["MW"]

    def advance(self, dt, fp):
        if self.tau > 0:
            self.x += (self.x_cmd - self.x) * (1.0 - math.exp(-dt / self.tau))
        else:
            self.x = self.x_cmd


class ResBranch(Branch):
    kind = "res"

    def __init__(self, *a, K=1.0, **kw):
        super().__init__(*a, **kw)
        self.K = K

    def flow(self, Pu, Pd, sp):
        drv, _ = DT.valve_driving(Pu, Pd, sp["rho"], sp["k"], self.compressible(sp), 10.0)
        return self.K * drv / sp["MW"]


class PipeBranch(ResBranch):
    """Interface between two pipe cells: turbulent resistance plus the hydrostatic head of the mixture.  Every interface
    has a forward and a reverse branch (only the one with a positive driving pressure carries flow)."""
    kind = "pipe"

    def __init__(self, *a, dz=0.0, **kw):
        super().__init__(*a, **kw)
        self.dz = dz

    def static(self, sp):
        return sp["rho"] * DT.G * self.dz / 1e5

    def flow(self, Pu, Pd, sp):
        drv, _ = DT.valve_driving(Pu, Pd + self.static(sp), sp["rho"], sp["k"], self.compressible(sp), 10.0)
        return self.K * drv / sp["MW"]


class FeedBranch(Branch):
    kind = "feed"
    pressure_driven = False

    def __init__(self, name, feed, dests):
        super().__init__(name, feed, "out", dests)
        self.feed = feed
        self.t = 0.0
        self.cap = False

    def flow(self, Pu, Pd, sp):
        return self.feed.F(self.t)


class CompBranch(Branch):
    kind = "compressor"

    def __init__(self, *a, curve=None, N0=10000.0, N=None, c_head=1.0, eta_ss=0.75, tau_speed=20.0,
                 tau_coast=60.0, **kw):
        super().__init__(*a, **kw)
        self.curve, self.N0 = curve, float(N0)
        self.N = float(N0 if N is None else N)
        self.N_cmd = self.N
        self.running = True
        self.c_head, self.eta_ss = c_head, eta_ss
        self.tau_speed, self.tau_coast = tau_speed, tau_coast
        self.ctrl = None            # speed controller (if any)
        self.asv = None             # anti-surge valve (a ValveBranch from the discharge holdup to the suction holdup)
        self.asc_ctrl = None
        self.last = {}

    def get_state(self):
        return (self.N, self.N_cmd, self.running)

    def set_state(self, s, fp=None):
        self.N, self.N_cmd, self.running = s

    def flow(self, Pu, Pd, sp):
        c = self.curve
        N = self.N
        out = {"Q": 0.0, "Qs": c.surge_flow(max(N, 1e-9)), "surge": False, "head": 0.0, "eta": self.eta_ss, "w_tot": 0.0}
        self.last = out
        if N < 0.02 * self.N0 or (not self.running and N < 0.3 * self.N0) or Pd <= 0 or Pu <= 0:
            return 0.0                       # stopped (or coasting below 30 %: the check valve is closed)
        r = Pd / Pu
        head_req = self.c_head * DT.poly_head(max(r, 1.0 + 1e-9), sp["Z"], sp["T"], sp["MW"], sp["k"], self.eta_ss)
        Qs = c.surge_flow(N)
        Qmax = 1.3 * c.stonewall_flow(N)
        hs = c.at(Qs, N)[0]
        if head_req > hs:
            Q = Qs * max(0.0, 1.0 - (head_req / hs - 1.0) / 0.15)
            out["surge"] = True
        elif c.at(Qmax, N)[0] >= head_req:
            Q = Qmax
        else:
            from scipy.optimize import brentq
            Q = brentq(lambda q: c.at(q, N)[0] - head_req, Qs, Qmax, xtol=1e-6 * Qs, rtol=1e-12)
        eta = max(c.at(max(Q, Qs), N)[1], 5.0) / 100.0
        w_tot = Q * sp["rho"] / sp["MW"]
        out.update(Q=Q, head=head_req, eta=eta, w_tot=w_tot)
        return w_tot

    def dH(self, sp, w, Pu, Pd):
        o = self.last
        return o["head"] * sp["MW"] / max(o.get("eta", self.eta_ss), 0.05) if o.get("w_tot", 0) > 0 else 0.0

    def margin(self):
        o = self.last
        return (o["Q"] / o["Qs"] - 1.0) * 100.0 if o.get("Qs") and o.get("w_tot", 0) > 0 else 100.0

    def power_kW(self, sp):
        o = self.last
        return o["w_tot"] * self.dH(sp, o["w_tot"], 0, 0) / 3600.0 if o.get("w_tot", 0) > 0 else 0.0

    def advance(self, dt, fp):
        if self.running:
            self.N += (self.N_cmd - self.N) * (1.0 - math.exp(-dt / max(self.tau_speed, 1e-6)))
        else:
            self.N *= math.exp(-dt / max(self.tau_coast, 1e-6))


class PumpBranch(Branch):
    kind = "pump"

    def __init__(self, *a, Q_ss=1.0, dP_ss=1.0, eta=0.7, n=1.0, shutoff=1.25, **kw):
        super().__init__(*a, **kw)
        self.Q_ss, self.dP_ss, self.eta, self.n, self.shutoff = Q_ss, dP_ss, eta, n, shutoff
        self.running = True
        self.last = {}

    def get_state(self):
        return (self.n, self.running)

    def set_state(self, s, fp=None):
        self.n, self.running = s

    def flow(self, Pu, Pd, sp):
        if not self.running or self.n <= 0.02:
            self.last = {"Q": 0.0}
            return 0.0
        dP0 = self.shutoff * self.dP_ss * self.n ** 2
        dP = Pd - Pu
        if dP >= dP0:
            self.last = {"Q": 0.0}
            return 0.0
        Q = self.Q_ss * self.n * math.sqrt((dP0 - dP) / (dP0 - self.dP_ss * self.n ** 2 + 1e-12)) \
            if dP0 > self.dP_ss * self.n ** 2 else 0.0
        Q = min(Q, 2.0 * self.Q_ss)
        self.last = {"Q": Q}
        return Q * sp["rho"] / sp["MW"]

    def dH(self, sp, w, Pu, Pd):
        return max(Pd - Pu, 0.0) * 1e5 * (sp["MW"] / sp["rho"] / 1000.0) / self.eta


class ReliefBranch(Branch):
    """Orifice to a pressure boundary: a pressure-relief valve (pops open at the set pressure, closes at the reseat
    pressure) or a blowdown valve (opened by an event)."""
    kind = "relief"

    def __init__(self, name, src, dests, kind_="BDV", D_mm=50.0, Cd=0.85, P_set=None, reseat=0.93, tau=1.0,
                 is_open=False):
        super().__init__(name, src, "vapour", dests)
        self.rtype, self.Cd, self.P_set, self.reseat, self.tau = kind_, Cd, P_set, reseat, tau
        self.A = math.pi * (D_mm / 1000.0) ** 2 / 4.0
        self.D_mm = D_mm
        self.x = 1.0 if is_open else 0.0
        self.x_cmd = self.x
        self.cap = True

    def get_state(self):
        return (self.x, self.x_cmd)

    def set_state(self, s, fp=None):
        self.x, self.x_cmd = s

    def flow(self, Pu, Pd, sp):
        if self.x <= 1e-6:
            return 0.0
        kg_s = DT.orifice_mass_flow(Pu, Pd, sp["T"], sp["MW"], sp["k"], sp["Z"], sp["rho"], self.A * self.x, self.Cd,
                                    gas=sp["gas"])
        return kg_s * 3600.0 / sp["MW"]

    def advance(self, dt, fp):
        if self.rtype == "PSV" and self.P_set:
            P = self.src.P
            if P >= self.P_set:
                self.x_cmd = 1.0
            elif P <= self.P_set * self.reseat:
                self.x_cmd = 0.0
        self.x += (self.x_cmd - self.x) * (1.0 - math.exp(-dt / max(self.tau, 1e-6)))

    def compressible(self, sp):
        return bool(sp["gas"])


# ================================================================================================ controllers
class Controller:
    """PID controller (positional form, anti-windup).  Error in % of the PV span; OP in %."""

    def __init__(self, name, pv_fn, target, sp, Kc=2.0, Ti=300.0, Td=0.0, action="direct", span=(0.0, 100.0),
                 op0=50.0, pv_label="PV", unit=""):
        self.name, self.pv_fn, self.target = name, pv_fn, target
        self.sp, self.Kc, self.Ti, self.Td = float(sp), float(Kc), float(Ti), float(Td)
        self.action = action
        self.span = (float(span[0]), float(span[1]))
        self.bias = float(op0)
        self.I = 0.0                 # integral of the error [% s]
        self.op = float(op0)
        self.mode = "auto"
        self.op_manual = float(op0)
        self.pv = None
        self.e_prev = 0.0
        self.pv_label = pv_label
        self.unit = unit
        self.op_min, self.op_max = 0.0, 100.0

    def get_state(self):
        return (self.sp, self.I, self.op, self.mode, self.op_manual, self.e_prev, self.bias)

    def set_state(self, s, fp=None):
        self.sp, self.I, self.op, self.mode, self.op_manual, self.e_prev, self.bias = s

    def update(self, dt):
        self.pv = self.pv_fn()
        if self.mode == "manual":
            self.op = min(max(self.op_manual, self.op_min), self.op_max)
            # bumpless transfer back to auto: keep the bias so that the output stays where it is
            self.I = 0.0
            self.bias = self.op
            self.e_prev = 0.0
            return self.op
        sgn = 1.0 if self.action == "direct" else -1.0
        e = sgn * (self.pv - self.sp) / max(self.span[1] - self.span[0], 1e-12) * 100.0
        self.I += e * dt
        d = (e - self.e_prev) / dt if (self.Td > 0 and dt > 0) else 0.0
        self.e_prev = e
        raw = self.bias + self.Kc * (e + (self.I / self.Ti if self.Ti > 0 else 0.0) + self.Td * d)
        self.op = min(max(raw, self.op_min), self.op_max)
        if raw != self.op and self.Ti > 0 and self.Kc != 0:      # anti-windup: back-calculate the integral
            self.I = (self.op - self.bias) / self.Kc * 1.0 * self.Ti - e * self.Ti
        return self.op


# ================================================================================================ the dynamic system
class Dyn:
    """Assembled dynamic model: holdups, boundaries, flow elements, controllers, events."""

    def __init__(self, fp, model):
        self.fp, self.model = fp, model
        self.MWv = fp.MW
        self.nodes, self.branches, self.sinks, self.feeds, self.controllers = [], [], [], [], []
        self.events = []
        self.t = 0.0
        self.dt = 1.0
        self.notes = []
        self.cfg = {}
        self._ev = 0
        self.rec = None
        self.inv0 = 0.0
        self.cum_in0 = 0.0
        self.log = []

    # ------------------------------------------------------------------ bookkeeping
    def named(self, name):
        for coll in (self.nodes, self.branches, self.sinks, self.feeds, self.controllers):
            for o in coll:
                if o.name == name:
                    return o
        raise DynError(f"nothing called '{name}' in the dynamic model")

    def inventory(self):
        return sum(n.Ntot for n in self.nodes)

    def balance(self):
        """Closure of the total mole balance [kmol]: initial inventory + fed - discharged - inventory now."""
        fed = sum(f.cum_kmol for f in self.feeds)
        out = sum(s.cum_kmol for s in self.sinks)
        return self.inv0 + fed - out - self.inventory()

    def snapshot(self):
        return ([n.get_state() for n in self.nodes], [b.get_state() for b in self.branches],
                [s.get_state() for s in self.sinks], [f.get_state() for f in self.feeds],
                [c.get_state() for c in self.controllers], self.t)

    def restore(self, snap):
        for o, s in zip(self.nodes, snap[0]):
            o.set_state(s, self.fp)
        for o, s in zip(self.branches, snap[1]):
            o.set_state(s, self.fp)
        for o, s in zip(self.sinks, snap[2]):
            o.set_state(s, self.fp)
        for o, s in zip(self.feeds, snap[3]):
            o.set_state(s, self.fp)
        for o, s in zip(self.controllers, snap[4]):
            o.set_state(s, self.fp)
        self.t = snap[5]

    # ------------------------------------------------------------------ evaluating the flows
    def _src_props(self, b, t):
        if isinstance(b, FeedBranch):
            f = b.feed
            return {"z": f.z, "H": f.H(t, self.fp), "MW": float(f.z @ self.MWv), "rho": 1.0, "k": 1.3, "Z": 1.0,
                    "n": math.inf, "gas": True, "T": f.T(t), "P": f.P(t)}
        sp = b.src.port(b.port, self.MWv)
        if sp is None and isinstance(b, ReliefBranch):
            for alt in ("liquid", "out"):
                sp = b.src.port(alt, self.MWv)
                if sp is not None:
                    break
        return sp

    def _evaluate(self, t, dt_h):
        """Flows, their pressure derivatives and the net inflow of every holdup at the present state."""
        nn = len(self.nodes)
        idx = {id(n): i for i, n in enumerate(self.nodes)}
        f = np.zeros(nn)
        Jm = np.zeros((nn, nn))
        for b in self.branches:
            if isinstance(b, FeedBranch):
                b.t = t
            sp = self._src_props(b, t)
            b.sp = sp
            b.a = b.c = 0.0
            if sp is None:
                b.w = 0.0
                continue
            if b.pressure_driven:
                Pu, Pd = b.Pu(), b.Pd()
                w = b.flow(Pu, Pd, sp)
                if w > 0 or b.kind in ("compressor",):
                    h = 1e-3 * max(1.0, Pu)
                    b.a = (b.flow(Pu + h, Pd, sp) - w) / h
                    b.c = (b.flow(Pu, Pd + h, sp) - w) / h
                    w = b.flow(Pu, Pd, sp)               # leaves the diagnostics at the base point
            else:
                w = b.flow(0, 0, sp)
            if b.cap and math.isfinite(sp["n"]) and dt_h > 0:
                w = min(w, 0.9 * sp["n"] / dt_h)
            b.w = max(w, 0.0)
            src_i = idx.get(id(b.src))
            d0 = b.dests[0].obj
            d0_i = idx.get(id(d0))
            if src_i is not None:
                f[src_i] -= b.w
                Jm[src_i, src_i] -= b.a
                if d0_i is not None:
                    Jm[src_i, d0_i] -= b.c
            for d in b.dests:
                di = idx.get(id(d.obj))
                if di is not None:
                    f[di] += b.w * d.frac
                    if src_i is not None:
                        Jm[di, src_i] += d.frac * b.a
                    if d0_i is not None:
                        Jm[di, d0_i] += d.frac * b.c
        return f, Jm

    # ------------------------------------------------------------------ one step
    def _step(self, dt):
        """Advance by dt [s].  Raises DynError when the new state cannot be solved."""
        fp = self.fp
        t = self.t
        dt_h = dt / 3600.0
        for c in self.controllers:
            op = c.update(dt)
            c.apply(op)
        f, Jm = self._evaluate(t, dt_h)
        nn = len(self.nodes)
        # semi-implicit pressure correction: (I - dt K J) dP = dt K f
        dP = np.zeros(nn)
        if nn:
            K = np.array([n.kappa for n in self.nodes])
            A = np.eye(nn) - dt_h * (K[:, None] * Jm)
            try:
                dP = np.linalg.solve(A, dt_h * K * f)
            except np.linalg.LinAlgError:
                dP = dt_h * K * f
            P0 = np.array([n.P for n in self.nodes])
            dP = np.maximum(dP, -0.6 * P0)
        idx = {id(n): i for i, n in enumerate(self.nodes)}
        dN = {id(n): np.zeros_like(n.N) for n in self.nodes}
        dU = {id(n): 0.0 for n in self.nodes}
        for s in self.sinks:
            s.flow = s.kg_h = s.stdgas = 0.0
        for b in self.branches:
            sp = b.sp
            if sp is None:
                continue
            w = b.w
            if b.pressure_driven and (b.a or b.c):
                w += b.a * (dP[idx[id(b.src)]] if id(b.src) in idx else 0.0)
                d0 = b.dests[0].obj
                w += b.c * (dP[idx[id(d0)]] if id(d0) in idx else 0.0)
                w = max(w, 0.0)
                if b.cap and math.isfinite(sp["n"]):
                    w = min(w, 0.9 * sp["n"] / dt_h)
            b.w = w
            b.mass = w * sp["MW"]
            if w <= 0:
                continue
            z, Hs = sp["z"], sp["H"]
            if id(b.src) in dN:
                dN[id(b.src)] -= w * dt_h * z
                dU[id(b.src)] -= w * dt_h * Hs
            elif isinstance(b, FeedBranch):
                b.feed.cum_kmol += w * dt_h
            Hin = Hs
            for op in b.pre_ops:
                Hin = op.apply(z, Hin, fp)
            Pu, Pd = (b.Pu(), b.Pd()) if b.pressure_driven else (0.0, 0.0)
            Hel = Hin + b.dH(sp, w, Pu, Pd)
            for d in b.dests:
                wd = w * d.frac
                Hd = Hel
                for op in d.ops:
                    Hd = op.apply(z, Hd, fp)
                if id(d.obj) in dN:
                    dN[id(d.obj)] += wd * dt_h * z
                    dU[id(d.obj)] += wd * dt_h * Hd
                else:                                   # sink
                    d.obj.cum_kmol += wd * dt_h
                    d.obj.cum_kg += wd * dt_h * sp["MW"]
                    d.obj.flow += wd
                    d.obj.kg_h += wd * sp["MW"]
                    if sp["gas"]:
                        d.obj.stdgas += wd * V_STD_GAS * 24.0 / 1e6
        # advance the actuators / machines and the holdups
        for b in self.branches:
            b.advance(dt, fp)
        old = [(n.P, n.T, n.level, n.Ntot) for n in self.nodes]
        for n in self.nodes:
            n.N = n.N + dN[id(n)]
            if np.any(n.N < -1e-9 * max(n.Ntot, 1e-9) - 1e-12):
                raise DynError(f"{n.name}: a component inventory became negative (step too large)")
            n.N = np.maximum(n.N, 0.0)
            qk = n.Q_kW
            if n.wall is not None:
                w_ = n.wall
                qw = w_["UA_in"] * (n.Tw - n.T)
                qk += qw
                n.Tw += dt * (-qw + w_["UA_out"] * (w_["T_amb"] - n.Tw)) / w_["C"]
            n.U = n.U + dU[id(n)] + qk * dt
        for n in self.nodes:
            n.refresh(fp)
        self.t = t + dt
        worst = 0.0
        for n, (P0, T0, L0, N0) in zip(self.nodes, old):
            dN_ = n.Ntot - N0
            if abs(dN_) > 1e-5 * N0:
                k_new = (n.P - P0) / dN_
                if k_new > 0:
                    n.kappa = 0.6 * n.kappa + 0.4 * k_new
            worst = max(worst, abs(n.P - P0) / P0 / 0.05, abs(n.T - T0) / 10.0,
                        abs(n.level - L0) / 3.0 if n.has_level else 0.0)
        return worst

    # ------------------------------------------------------------------ events
    def apply_event(self, ev):
        k, tgt, val = ev["kind"], ev.get("target"), ev.get("value")
        ramp = float(ev.get("ramp", 0.0) or 0.0)
        t = self.t
        if k in ("feed_flow", "feed_T", "feed_P"):
            fd = self.named(tgt)
            if k == "feed_flow":
                fd.ramp(fd.sF, t, float(val) * fd.per_unit, ramp)
            elif k == "feed_T":
                fd.ramp(fd.sT, t, float(val) + K0, ramp)
            else:
                fd.ramp(fd.sP, t, float(val), ramp)
        elif k == "ctrl_sp":
            self.named(tgt).sp = float(val)
        elif k == "ctrl_mode":
            c = self.named(tgt)
            c.mode = "manual" if str(val).lower().startswith("m") else "auto"
            if ev.get("op") is not None:
                c.op_manual = float(ev["op"])
            elif c.mode == "manual":
                c.op_manual = c.op
        elif k == "valve_op":
            b = self.named(tgt)
            c = getattr(b, "ctrl", None)
            if c is not None:
                c.mode, c.op_manual = "manual", float(val)
            else:
                b.x_cmd = float(val) / 100.0
        elif k == "comp_trip":
            b = self.named(tgt)
            b.running = False
            if b.asc_ctrl is not None and b.asc_cfg.get("open_on_trip", True):
                b.asc_ctrl.mode, b.asc_ctrl.op_manual = "manual", 100.0
        elif k == "comp_start":
            b = self.named(tgt)
            b.running = True
            if val is not None:
                b.N_cmd = float(val) / 100.0 * b.N0
        elif k == "comp_speed":
            b = self.named(tgt)
            c = b.ctrl
            if c is not None:
                c.mode, c.op_manual = "manual", float(val) / 1.1
            b.N_cmd = float(val) / 100.0 * b.N0
        elif k == "pump_trip":
            self.named(tgt).running = False
        elif k == "pump_start":
            self.named(tgt).running = True
        elif k == "relief_open":
            self.named(tgt).x_cmd = 1.0
        elif k == "relief_close":
            self.named(tgt).x_cmd = 0.0
        elif k == "node_heat":
            self.named(tgt).Q_kW = float(val)
        elif k == "sink_P":
            self.named(tgt).P = float(val)
        else:
            raise DynError(f"unknown event kind '{k}'")
        self.log.append((t, k, tgt, val))

    # ------------------------------------------------------------------ recording
    def values(self):
        v = {}
        for n in self.nodes:
            v[f"{n.name} | Pressure [bar(a)]"] = n.P
            v[f"{n.name} | Temperature [°C]"] = n.T - K0
            v[f"{n.name} | Inventory [kmol]"] = n.Ntot
            v[f"{n.name} | Vapour fraction"] = n.fr.vf
            if n.has_level and n.kind == "vessel":
                v[f"{n.name} | Level [%]"] = n.level
                v[f"{n.name} | Liquid volume [m³]"] = n.V_liq
                if "W" in n.ph:
                    v[f"{n.name} | Water level [%]"] = n.level_w
            if n.wall is not None:
                v[f"{n.name} | Wall temperature [°C]"] = n.Tw - K0
        for b in self.branches:
            if isinstance(b, FeedBranch):
                v[f"{b.feed.name} | Flow [kmol/h]"] = b.w
                continue
            sp = b.sp
            v[f"{b.name} | Flow [kmol/h]"] = b.w
            v[f"{b.name} | Mass flow [kg/h]"] = b.mass
            if sp is not None and sp["gas"]:
                v[f"{b.name} | Std gas flow [MSm³/d]"] = b.w * V_STD_GAS * 24.0 / 1e6
            if b.kind == "valve":
                v[f"{b.name} | Opening [%]"] = 100.0 * b.x
            elif b.kind == "compressor":
                o = b.last
                v[f"{b.name} | Speed [rpm]"] = b.N
                v[f"{b.name} | Speed [% of design]"] = 100.0 * b.N / b.N0
                if sp is not None:
                    v[f"{b.name} | Pressure ratio"] = (b.Pd() / b.Pu()) if b.Pu() > 0 else 0.0
                    v[f"{b.name} | Surge margin [%]"] = b.margin()
                    v[f"{b.name} | Power [kW]"] = b.power_kW(sp)
                    v[f"{b.name} | Machine flow [kmol/h]"] = o.get("w_tot", 0.0)
                    v[f"{b.name} | In surge (1 = yes)"] = 1.0 if o.get("surge") else 0.0
                if b.asv is not None:
                    v[f"{b.name} | Anti-surge valve [%]"] = 100.0 * b.asv.x
                    v[f"{b.name} | Recycle flow [kmol/h]"] = b.asv.w
            elif b.kind == "pump":
                v[f"{b.name} | Speed [%]"] = 100.0 * b.n if b.running else 0.0
            elif b.kind == "relief":
                v[f"{b.name} | Open [%]"] = 100.0 * b.x
                v[f"{b.name} | Flow [kg/s]"] = b.mass / 3600.0
        for s in self.sinks:
            v[f"{s.name} | Inflow [kmol/h]"] = s.flow
            v[f"{s.name} | Cumulative [kmol]"] = s.cum_kmol
            if s.stdgas > 0:
                v[f"{s.name} | Gas inflow [MSm³/d]"] = s.stdgas
        for c in self.controllers:
            v[f"{c.name} | PV"] = c.pv if c.pv is not None else c.pv_fn()
            v[f"{c.name} | SP"] = c.sp
            v[f"{c.name} | OP [%]"] = c.op
        for nm, cells in getattr(self, "pipes", {}).items():
            v[f"{nm} | Inlet pressure [bar(a)]"] = cells[0].P
            v[f"{nm} | Outlet pressure [bar(a)]"] = cells[-1].P
            v[f"{nm} | Line pack [kmol]"] = sum(c.Ntot for c in cells)
            v[f"{nm} | Liquid inventory [m³]"] = sum(c.V_liq for c in cells)
        v["Total inventory [kmol]"] = self.inventory()
        v["Mole balance error [kmol]"] = self.balance()
        return v

    # ------------------------------------------------------------------ run
    def run(self, t_end=None, dt_max=None, dt_out=None, progress=None, max_wall=None, dt_min=0.005):
        cfg = self.cfg
        t_end = float(t_end if t_end is not None else cfg.get("t_end", 600.0))
        dt_max = float(dt_max if dt_max is not None else cfg.get("dt_max", 1.0))
        dt_out = float(dt_out if dt_out is not None else cfg.get("dt_out", 2.0))
        t0w = _time.time()
        evs = sorted(self.events, key=lambda e: float(e["t"]))
        self._ev = 0
        self.rec = {"t": [], "v": {}}
        self.inv0 = self.inventory()
        self._evaluate(self.t, 0.0)
        for c in self.controllers:
            c.pv = c.pv_fn()
        self._record()
        next_out = self.t + dt_out
        self.dt = min(dt_max, max(dt_min, 1.0))
        nsteps = nrej = 0
        status, message = "ok", ""
        while self.t < t_end - 1e-9:
            while self._ev < len(evs) and float(evs[self._ev]["t"]) <= self.t + 1e-9:
                self.apply_event(evs[self._ev])
                self._ev += 1
            limit = min(t_end, next_out)
            if self._ev < len(evs):
                limit = min(limit, float(evs[self._ev]["t"]))
            dt = min(self.dt, limit - self.t)
            if dt < 1e-9:
                dt = max(limit - self.t, 1e-9)
            snap = self.snapshot()
            try:
                worst = self._step(dt)
                ok = worst <= 1.0 or dt <= dt_min * 1.0001
            except (DynError, DT.UVError, FloatingPointError, np.linalg.LinAlgError) as e:
                if dt <= dt_min * 1.0001:
                    status, message = "failed", f"t = {self.t:.2f} s: {e}"
                    self.restore(snap)
                    break
                ok, worst = False, 99.0
                last_err = str(e)
            if not ok:
                self.restore(snap)
                self.dt = max(dt * 0.5, dt_min)
                nrej += 1
                continue
            nsteps += 1
            self.dt = min(dt_max, dt * 1.5) if worst < 0.4 else (dt if worst < 0.8 else max(dt * 0.7, dt_min))
            if self.t >= next_out - 1e-9:
                self._record()
                next_out += dt_out
                if progress:
                    progress(min(self.t / t_end, 1.0))
            if max_wall and (_time.time() - t0w) > max_wall:
                status, message = "stopped", f"stopped at t = {self.t:.1f} s: the wall-clock limit ({max_wall:.0f} s) was reached"
                break
        if not self.rec["t"] or self.rec["t"][-1] < self.t - 1e-9:
            self._record()
        return {"t": self.rec["t"], "series": self.rec["v"], "status": status, "message": message,
                "steps": nsteps, "rejected": nrej, "seconds": round(_time.time() - t0w, 2),
                "events": [(a, b, c, d) for a, b, c, d in self.log],
                "balance_error_kmol": self.balance(), "inventory0": self.inv0, "notes": list(self.notes)}

    def _record(self):
        v = self.values()
        r = self.rec
        n = len(r["t"])
        r["t"].append(self.t)
        for k, val in v.items():
            col = r["v"].setdefault(k, [None] * n)
            col.append(val)
        for k, col in r["v"].items():
            if len(col) < n + 1:
                col.append(None)


def _apply_target(c, obj, kind):
    """Controller output -> equipment."""
    if kind == "valve":
        def f(op):
            obj.x_cmd = op / 100.0
    elif kind == "speed":
        def f(op):
            if obj.running:
                obj.N_cmd = op / 100.0 * 1.1 * obj.N0
    elif kind == "relief":
        def f(op):
            obj.x_cmd = op / 100.0
    else:
        raise DynError(f"unknown controller target '{kind}'")
    c.apply = f


# ================================================================================================ building from the steady state
def _hmean(*a):
    return len(a) / sum(1.0 / x for x in a)


def default_volume(kind, fr, F):
    """Holdup volume [m3] from the steady flows: separators ~ 3 min liquid at 50 % level and 1 min of gas, mixers 20 s."""
    Qg = F * 1000.0 * sum(p.beta * p.Vs for p in fr.phases if p.kind == "V")
    Ql = F * 1000.0 * sum(p.beta * p.Vs for p in fr.phases if p.kind != "V")
    if kind == "mixer":
        return max(1.0, 0.0056 * (Qg + Ql))
    return max(2.0, 0.1 * Ql, 0.02 * Qg)


def default_settings(model, sol):
    """Dynamic settings with every default made explicit (volumes, levels, wall data); the user overrides are
    ``model['dynamics']``."""
    fp = sol.fp
    cfg = {"t_end": 600.0, "dt_max": 1.0, "dt_out": 2.0, "nodes": {}, "valves": {}, "compressors": {}, "pumps": {},
           "controllers": {}, "extra_controllers": [], "relief": [], "events": [], "wall": True}
    for uid, u in model["units"].items():
        if u["type"] not in NODE_TYPES or sol.status.get(uid) not in ("ok", "warning"):
            continue
        try:
            fr, F, T, P = _node_steady(model, sol, uid)
        except DynError:
            continue
        V = default_volume("mixer" if u["type"] == "mixer" else "vessel", fr, F)
        d = {"volume": round(V, 2)}
        if u["type"] != "mixer":
            d.update(orient="Vertical" if u["type"] == "scrubber" else "Horizontal",
                     level0=50.0 if any(p.kind != "V" for p in fr.phases) else 0.0)
        cfg["nodes"][u["name"]] = d
    return cfg


def merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            for kk, vv in v.items():
                if isinstance(vv, dict) and isinstance(out[k].get(kk), dict):
                    out[k][kk].update(vv)
                else:
                    out[k][kk] = copy.deepcopy(vv)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _node_steady(model, sol, uid):
    """(flash, F kmol/h, T K, P bar) of a holdup unit at the steady state."""
    u = model["units"][uid]
    fp = sol.fp
    if u["type"] == "mixer":
        outs = [s for lst in port_edges(model, uid, "out").values() for s in lst]
        if not outs or outs[0] not in sol.streams or sol.streams[outs[0]].empty:
            raise DynError(f"{u['name']}: no flow in the steady-state solution")
        s = sol.streams[outs[0]]
        return s.flash, s.F, s.T, s.P
    res = sol.results.get(uid) or {}
    if "Vessel P [bar(a)]" not in res:
        raise DynError(f"{u['name']}: no flow in the steady-state solution")
    ins = [sol.streams[s] for lst in port_edges(model, uid, "in").values() for s in lst
           if s in sol.streams and not sol.streams[s].empty]
    F = sum(s.F for s in ins)
    z = sum(s.F * s.z for s in ins) / F
    T, P = res["Vessel T [°C]"] + K0, res["Vessel P [bar(a)]"]
    return fp.pt_flash(z, T, P), F, T, P


def _init_vessel(node, fr, level0, fp):
    """Inventory and energy of a vessel holding the steady phases at the given level."""
    ph = DT.phase_data(fr, 1.0)
    V = node.V
    liq = [k for k in ph if k != "V"]
    fl = DT.volume_fraction_at_level(node.orient, level0) if liq else 0.0
    VL = fl * V
    VG = V - VL
    if "V" not in ph and VG > 1e-9 * V:
        raise DynError(f"{node.name}: liquid-full at the steady state (no vapour phase) - not supported")
    N = np.zeros(fp.n)
    U = 0.0
    if "V" in ph:
        n = VG / (1000.0 * ph["V"]["Vs"])
        N += n * ph["V"]["x"]
        U += n * ph["V"]["H"]
    vl = sum(ph[k]["vol"] for k in liq)
    for k in liq:
        n = VL * (ph[k]["vol"] / vl) / (1000.0 * ph[k]["Vs"])
        N += n * ph[k]["x"]
        U += n * ph[k]["H"]
    node.N = N
    node.U = U - 100.0 * fr.P * V


def unique_name(d, nm):
    used = {o.name for coll in (d.nodes, d.branches, d.sinks, d.feeds, d.controllers) for o in coll}
    base, k = nm, 2
    while nm in used:
        nm = f"{base} ({k})"
        k += 1
    return nm


def build(model, sol, settings=None):
    """Assemble the dynamic model from a solved flowsheet.  ``settings`` overrides ``model['dynamics']``."""
    fp = sol.fp
    if not sol.converged or any(v in ("error", "missing", "unsolved") for v in sol.status.values()):
        raise DynError("the steady-state flowsheet must be solved without errors before a dynamic run")
    cfg = merge(default_settings(model, sol), settings if settings is not None else model.get("dynamics"))
    d = Dyn(fp, model)
    d.cfg = cfg
    units, streams = model["units"], model["streams"]
    S = sol.streams
    node_of, sink_of, feed_of, src_of, pipes = {}, {}, {}, {}, {}

    # ---------------- holdups
    for uid, u in units.items():
        if u["type"] not in NODE_TYPES:
            continue
        nm = u["name"]
        fr, F, T, P = _node_steady(model, sol, uid)
        nc = cfg["nodes"].get(nm, {})
        V = float(nc.get("volume") or default_volume("mixer" if u["type"] == "mixer" else "vessel", fr, F))
        if V <= 0:
            raise DynError(f"{nm}: the volume must be positive")
        is_mixer = u["type"] == "mixer"
        node = Node(nm, "mixer" if is_mixer else "vessel", V, nc.get("orient", "Horizontal"), has_level=not is_mixer)
        node.uid, node.P_ss, node.T_ss = uid, P, T
        node.T, node.P, node.fr = T, P, fr
        if is_mixer:
            Vm = sum(p.beta * p.Vs for p in fr.phases)
            node.N = fr.z * V / (1000.0 * Vm)
            node.U = node.Ntot * fr.H - 100.0 * P * V
        else:
            lev0 = float(nc.get("level0", 50.0))
            if not any(p.kind != "V" for p in fr.phases):
                lev0 = 0.0
            _init_vessel(node, fr, lev0, fp)
            node.Q_kW = float(u["params"].get("duty", 0.0) or 0.0)
        # wall (thermal mass of the shell); no loss to ambient by default so that the steady state holds
        if cfg.get("wall", True) and not is_mixer and nc.get("wall", True):
            D = (V / (0.75 * math.pi)) ** (1.0 / 3.0)
            area = math.pi * D * D * 3.0 + 2.0 * math.pi * D * D / 4.0
            t_w = float(nc.get("wall_mm", 25.0)) / 1000.0
            mass = area * t_w * 7850.0
            node.wall = {"C": float(nc.get("wall_C", mass * 0.5)),
                         "UA_in": float(nc.get("UA_in", 50.0 * area / 1000.0)),
                         "UA_out": float(nc.get("UA_out", 0.0)), "T_amb": float(nc.get("T_amb", 15.0)) + K0}
            node.Tw = T
        node.refresh(fp)
        if abs(node.P - P) > 1e-3 * P or abs(node.T - T) > 0.05:
            raise DynError(f"{nm}: the dynamic holdup does not reproduce the steady state "
                           f"(P {node.P:.3f} vs {P:.3f} bar, T {node.T - K0:.2f} vs {T - K0:.2f} °C)")
        ph = node.ph
        nv = ph["V"]["n"] if "V" in ph else 0.0
        node.kappa = P / max(nv, 0.05 * node.Ntot)
        d.nodes.append(node)
        node_of[uid] = node
        src_of[uid] = node

    # ---------------- pipes -> chains of isothermal cells
    for uid, u in units.items():
        if u["type"] not in PIPE_TYPES:
            continue
        prof = sol.profiles.get(uid)
        ins = [s for lst in port_edges(model, uid, "in").values() for s in lst]
        if not prof or not ins or ins[0] not in S or S[ins[0]].empty:
            raise DynError(f"{u['name']}: no steady-state profile (is the line flowing?)")
        p = u["params"]
        pc = cfg.get("pipes", {}).get(u["name"], {})
        n = int(pc.get("cells", 10))
        n = min(max(n, 2), 60)
        Ltot, D = float(p["length"]), float(p["ID"]) / 1000.0
        Vc = math.pi / 4.0 * D * D * Ltot / n
        xs = np.array(prof["L"], float)
        xs = (xs - xs[0]) / max(xs[-1] - xs[0], 1e-9) * Ltot
        zz = S[ins[0]].z
        cells = []
        for i in range(n):
            xc = (i + 0.5) / n * Ltot
            Pc = float(np.interp(xc, xs, prof["P"]))
            Tc = float(np.interp(xc, xs, prof["T"])) + K0
            zc = float(np.interp(xc, xs, prof["z"])) if prof.get("z") else 0.0
            cell = Node(f"{u['name']} [{i + 1}]", "cell", Vc, has_level=False)
            cell.uid, cell.P_ss, cell.T_ss, cell.fixed_T, cell.elev = uid, Pc, Tc, Tc, zc
            fr = fp.pt_flash(zz, Tc, Pc)
            cell.T, cell.P, cell.fr = Tc, Pc, fr
            cell.N = zz * Vc / (1000.0 * sum(ph.beta * ph.Vs for ph in fr.phases))
            cell.refresh(fp)
            cell.kappa = Pc / max(cell.ph["V"]["n"] if "V" in cell.ph else 0.0, 0.05 * cell.Ntot)
            d.nodes.append(cell)
            cells.append(cell)
        pipes[uid] = cells
        node_of[uid] = cells[0]
        src_of[uid] = cells[-1]
        d.pipes = getattr(d, "pipes", {})
        d.pipes[u["name"]] = cells
        for i in range(n - 1):
            a, b2 = cells[i], cells[i + 1]
            dz = b2.elev - a.elev
            for rev, (x, y_, dzz) in enumerate(((a, b2, dz), (b2, a, -dz))):
                br = PipeBranch(unique_name(d, f"{u['name']} seg {i + 1}{' rev' if rev else ''}"), x, "out",
                                [Dest(y_, 1.0, 0.0, [])], 0.0, [], dz=dzz)
                br.F_ss = S[ins[0]].F if not rev else 0.0
                br.reverse = bool(rev)
                br.dp_ref = DP_PIPE
                if rev:
                    d.branches[-1].partner = br
                d.branches.append(br)

    # ---------------- boundaries
    for uid, u in units.items():
        if u["type"] in SINK_TYPES:
            ins = [s for lst in port_edges(model, uid, "in").values() for s in lst]
            if not ins or ins[0] not in S or S[ins[0]].P is None:
                continue
            sk = Sink(u["name"], S[ins[0]].P)
            sk.uid = uid
            d.sinks.append(sk)
            sink_of[uid] = sk
        elif u["type"] == "feed":
            outs = [s for lst in port_edges(model, uid, "out").values() for s in lst]
            if not outs or outs[0] not in S or S[outs[0]].empty:
                continue
            s = S[outs[0]]
            p = u["params"]
            q = float(p.get("flow", 0.0) or 0.0)
            if q > 0:
                per = s.F / q
            else:
                tmp = copy.deepcopy(u)
                tmp["params"]["flow"] = 1.0
                per = feed_stream(tmp, fp, "x").F
            fd = Feed(u["name"], s.z, s.F, s.T, s.P, p.get("flow_basis", "kmol/h"), per)
            fd._H[(round(s.T, 3), round(s.P, 4))] = s.H
            fd.uid = uid
            d.feeds.append(fd)
            feed_of[uid] = fd

    # ---------------- walking the lines
    def sid_out(uid, port=None):
        o = port_edges(model, uid, "out")
        lst = o.get(port) if port else [s for v in o.values() for s in v]
        return lst or []

    def dest_obj(du):
        if du in node_of:
            return node_of[du]
        if du in sink_of:
            return sink_of[du]
        return None

    def op_for(uid):
        """Quasi-steady unit -> Op acting on the molar enthalpy."""
        u = units[uid]
        t = u["type"]
        if t == "recycle":
            return Op("none")
        ins = [s for lst in port_edges(model, uid, "in").values() for s in lst]
        outs = [s for lst in port_edges(model, uid, "out").values() for s in lst]
        if len(ins) != 1 or len(outs) != 1 or ins[0] not in S or outs[0] not in S:
            raise DynError(f"{u['name']} ({t}) cannot be part of the dynamic model: a quasi-steady unit needs one "
                           "inlet and one outlet")
        si, so = S[ins[0]], S[outs[0]]
        if si.empty or so.empty:
            return Op("none")
        p = u["params"]
        if t in ("heater", "cooler") and p.get("spec", "Outlet temperature") == "Outlet temperature":
            return Op("T_out", T=float(p["T_out"]) + K0, P=so.P)
        if t == "valve":
            return Op("none")
        return Op("frozen", dh=so.H - si.H)

    def walk_post(sid, ops, frac):
        """Destinations reached from a stream after the flow element: [(Dest, last stream id)]."""
        s = streams[sid]
        du = s["dst"][0]
        u = units[du]
        t = u["type"]
        if dest_obj(du) is not None:
            return [(dest_obj(du), frac, list(ops), sid)]
        if t in SPLITTERS:
            outs = sid_out(du)
            Fin = sum(S[o].F for o in outs if o in S) or 1.0
            res = []
            for o in outs:
                if o in S and not S[o].empty:
                    res += walk_post(o, ops, frac * S[o].F / Fin)
            return res
        if t in QUASI_TYPES:
            outs = sid_out(du)
            if len(outs) != 1:
                raise DynError(f"{u['name']}: needs exactly one outlet connection")
            return walk_post(outs[0], ops + [op_for(du)], frac)
        raise DynError(f"{u['name']} ({t}) is not supported in the dynamic model; supported: holdups "
                       "(separators, scrubbers, mixers), valves, compressors, pumps, heaters / coolers, splitters")

    def walk_pre(sid, pre):
        """Paths from a holdup outlet: [(pre_ops, element uid or None, element in sid, dests)]."""
        s = streams[sid]
        du = s["dst"][0]
        u = units[du]
        t = u["type"]
        if dest_obj(du) is not None:
            return [(pre, None, sid, [(dest_obj(du), 1.0, [], sid)])]
        if t in FLOW_TYPES:
            outs = sid_out(du)
            if len(outs) != 1:
                raise DynError(f"{u['name']}: needs exactly one outlet connection")
            return [(pre, du, sid, walk_post(outs[0], [], 1.0))]
        if t in SPLITTERS:
            res = []
            for o in sid_out(du):
                if o in S and not S[o].empty:
                    res += walk_pre(o, pre)
            return res
        if t in QUASI_TYPES:
            outs = sid_out(du)
            if len(outs) != 1:
                raise DynError(f"{u['name']}: needs exactly one outlet connection")
            return walk_pre(outs[0], pre + [op_for(du)])
        raise DynError(f"{u['name']} ({t}) is not supported in the dynamic model; supported: holdups "
                       "(separators, scrubbers, mixers), valves, compressors, pumps, heaters / coolers, splitters")

    names_used = set()

    def unique(nm):
        base, k = nm, 2
        while nm in names_used:
            nm = f"{base} ({k})"
            k += 1
        names_used.add(nm)
        return nm

    def Pss(obj):
        return obj.P_ss if isinstance(obj, Node) else obj.P0

    # feeds -> flow-specified branches
    for uid, fd in feed_of.items():
        dsts = []
        for o in sid_out(uid):
            if o in S and not S[o].empty:
                dsts += walk_post(o, [], 1.0)
        if not dsts:
            raise DynError(f"{fd.name}: not connected to a holdup or product")
        dests = []
        for obj, fr_, ops, last in dsts:
            dests.append(Dest(obj, fr_, 0.0, ops, last))
        b = FeedBranch(unique(f"{fd.name} feed"), fd, dests)
        b.uid = uid
        d.branches.append(b)

    # holdup outlets -> flow elements
    for uid, node in src_of.items():
        for port, sids in port_edges(model, uid, "out").items():
            for sid in sids:
                if sid not in S or S[sid].empty:
                    continue
                for pre, elem, in_sid, dsts in walk_pre(sid, []):
                    src_P = node.P_ss
                    dests = []
                    if elem is None:
                        obj0 = dsts[0][0]
                        dp_ss = src_P - Pss(obj0)
                        dp_ref = max(dp_ss, DP_MIN)
                        dpu = 0.0
                        dpd0 = (src_P - dp_ref) - Pss(obj0)
                        for k, (obj, fr_, ops, last) in enumerate(dsts):
                            dests.append(Dest(obj, fr_, dpd0 if k == 0 else (src_P - dp_ref) - Pss(obj), ops, last))
                        b = ResBranch(unique(f"{node.name} {port} line"), node, port, dests, dpu, pre)
                        b.K = 1.0
                        b.F_ss = S[sid].F
                        b.dp_ref = dp_ref
                    else:
                        eu = units[elem]
                        et = eu["type"]
                        out_sid = sid_out(elem)[0]
                        Pu_ss, Pd_ss = S[in_sid].P, S[out_sid].P
                        dpu = src_P - Pu_ss
                        for k, (obj, fr_, ops, last) in enumerate(dsts):
                            if k == 0 and et == "valve":
                                dp_ref = max(Pu_ss - Pd_ss, DP_MIN)
                                dpd = (Pu_ss - dp_ref) - Pss(obj)
                            else:
                                dpd = Pd_ss - Pss(obj)
                            dests.append(Dest(obj, fr_, dpd, ops, last))
                        nm = unique(eu["name"])
                        if et == "valve":
                            res = sol.results.get(elem) or {}
                            x0 = res.get("Valve opening [%]")
                            vs = cfg["valves"].get(eu["name"], {})
                            x0 = float(vs.get("x0", x0 if x0 is not None else 50.0))
                            x0 = min(max(x0, 2.0), 98.0) / 100.0
                            b = ValveBranch(nm, node, port, dests, dpu, pre, K=1.0, char=eu["params"].get("char", "Equal percentage"),
                                            xT=float(eu["params"].get("xT", 0.7)), x0=x0,
                                            tau=float(vs.get("stroke_s", 5.0)))
                            b.F_ss = S[in_sid].F
                            b.dp_ref = max(Pu_ss - Pd_ss, DP_MIN)
                        elif et == "compressor":
                            b = _make_compressor(nm, node, port, dests, dpu, pre, eu, sol, elem, in_sid, out_sid, cfg)
                        else:
                            b = _make_pump(nm, node, port, dests, dpu, pre, eu, sol, elem, in_sid, out_sid)
                        b.uid = elem
                    d.branches.append(b)
    # pressures of the sinks that receive a line are the steady inlet pressures (already set)

    # ---------------- calibrate every flow element at the steady state (so that t = 0 is the steady state)
    d._evaluate(0.0, 0.0)
    for b in d.branches:
        if isinstance(b, FeedBranch):
            continue
        sp = d._src_props(b, 0.0)
        if sp is None:
            b.dead = True
            continue
        Pu, Pd = b.Pu(), b.Pd()
        if b.kind == "pipe":
            if b.reverse:
                continue
            static0 = b.static(sp)
            Pn = b.dests[0].obj.P
            dp_ref = max(Pu - Pn - static0, DP_PIPE)
            b.dests[0].dpd = Pu - dp_ref - static0 - Pn
            drv, _ = DT.valve_driving(Pu, Pu - dp_ref, sp["rho"], sp["k"], b.compressible(sp), 10.0)
            b.K = b.F_ss * sp["MW"] / max(drv, 1e-12)
            b.partner.K = b.K
            b.partner.dests[0].dpd = -b.dests[0].dpd
        elif b.kind in ("valve", "res"):
            drv, _ = DT.valve_driving(Pu, Pd, sp["rho"], sp["k"], b.compressible(sp), b.xT if b.kind == "valve" else 10.0)
            f0 = DT.valve_char(b.x, b.char) if b.kind == "valve" else 1.0
            if drv <= 0 or f0 <= 0:
                raise DynError(f"{b.name}: no pressure drop at the steady state ({Pu:.2f} -> {Pd:.2f} bar)")
            b.K = b.F_ss * sp["MW"] / (f0 * drv)
        elif b.kind == "compressor":
            _calibrate_compressor(b, sp, Pu, Pd)
        elif b.kind == "pump":
            b.Q_ss = b.F_ss * sp["MW"] / sp["rho"]
            b.dP_ss = max(Pd - Pu, 1e-3)

    for b in list(d.branches):
        if b.kind == "compressor" and b.antisurge:
            dn = b.dests[0].obj
            if not isinstance(dn, Node):
                raise DynError(f"{b.name}: anti-surge needs a holdup (vessel / mixer / line) downstream of the compressor")
            port = "vapour" if dn.kind == "vessel" else "out"
            cs = b.asc_cfg
            asv = ValveBranch(unique_name(d, f"ASV-{b.name}"), dn, port, [Dest(b.src, 1.0, 0.0, [])], 0.0, [], K=1.0,
                              char="Equal percentage", xT=0.7, x0=0.0, tau=float(cs.get("stroke_s", 3.0)))
            sp = dn.port(port, d.MWv)
            drv, _ = DT.valve_driving(dn.P, b.src.P, sp["rho"], sp["k"], True, 0.7)
            asv.K = b.F_ss * sp["MW"] / max(drv, 1e-9) * float(cs.get("size", 1.0))
            asv.F_ss = b.F_ss
            asv.is_asv, asv.owner = True, b
            asv.dp_ref = max(dn.P - b.src.P, DP_MIN)
            b.asv = asv
            d.branches.append(asv)
    d.inv0 = d.inventory()
    d.model_summary = None
    _make_controllers(d, cfg, model, sol)
    _make_relief(d, cfg)
    d.events = [dict(e) for e in cfg.get("events", [])]
    return d


def _make_compressor(nm, node, port, dests, dpu, pre, eu, sol, uid, in_sid, out_sid, cfg):
    from .unitops import CompressorCurve, typical_curve
    S = sol.streams
    p = eu["params"]
    res = sol.results.get(uid) or {}
    si, so = S[in_sid], S[out_sid]
    eta = res.get("Polytropic efficiency [%]")
    eta = (eta if eta else float(p.get("eff", 75.0))) / 100.0
    N0 = float(p.get("N_design", 10000.0))
    head_ss = (so.H - si.H) * eta / si.MW
    Qa = res.get("Actual inlet vol flow [m³/h]") or 1.0
    curve = None
    if p.get("curve"):
        try:
            curve = CompressorCurve(p["curve"], N0)
        except Exception:
            curve = None
    if curve is None:
        curve = CompressorCurve(typical_curve(Qa, head_ss, eta * 100.0), N0)
        N_ss = N0
    else:
        N_ss = float(res.get("Speed [rpm]") or res.get("Speed to meet duty (fan laws) [rpm]") or p.get("speed", N0))
    cs = cfg["compressors"].get(eu["name"], {})
    b = CompBranch(nm, node, port, dests, dpu, pre, curve=curve, N0=N0, N=N_ss, eta_ss=eta,
                   tau_speed=float(cs.get("tau_speed", 20.0)), tau_coast=float(cs.get("tau_coast", 60.0)))
    b.F_ss = si.F
    b.sm_min = float(p.get("sm_min", 10.0))
    b.antisurge = bool(cs.get("antisurge", p.get("antisurge", "Off") == "On"))
    b.asc_cfg = cs
    b.dp_ss = so.P - si.P
    return b


def _calibrate_compressor(b, sp, Pu, Pd):
    r0 = Pd / Pu
    Q_ss = b.F_ss * sp["MW"] / sp["rho"]
    h_curve = b.curve.at(Q_ss, b.N)[0]
    b.c_head = h_curve / DT.poly_head(r0, sp["Z"], sp["T"], sp["MW"], sp["k"], b.eta_ss)
    b.last = {}


def _make_pump(nm, node, port, dests, dpu, pre, eu, sol, uid, in_sid, out_sid):
    S = sol.streams
    p = eu["params"]
    b = PumpBranch(nm, node, port, dests, dpu, pre, eta=float(p.get("eff", 70.0)) / 100.0)
    b.F_ss = S[in_sid].F
    return b


def _make_controllers(d, cfg, model, sol):
    """Default controllers (level on liquid outlets, pressure on gas outlets, anti-surge on compressors) with the
    user's changes applied, plus any user-defined controllers."""
    specs = []
    for b in d.branches:
        if isinstance(b, FeedBranch) or getattr(b, "is_asv", False):
            continue
        if b.kind == "valve" and isinstance(b.src, Node) and b.src.kind == "vessel":
            n = b.src
            if b.port == "vapour":
                P = n.P_ss
                specs.append({"name": f"PC-{b.name}", "pv": ("pressure", n.name), "target": ("valve", b.name), "sp": P,
                              "span": (0.0, 2.0 * P), "Kc": 3.0, "Ti": 120.0, "action": "direct", "unit": "bar(a)"})
            elif b.port in ("liquid", "oil"):
                specs.append({"name": f"LC-{b.name}", "pv": ("level", n.name), "target": ("valve", b.name),
                              "sp": n.level, "span": (0.0, 100.0), "Kc": 2.0, "Ti": 300.0, "action": "direct", "unit": "%"})
            elif b.port == "water":
                specs.append({"name": f"WC-{b.name}", "pv": ("level_w", n.name), "target": ("valve", b.name),
                              "sp": n.level_w, "span": (0.0, 100.0), "Kc": 2.0, "Ti": 300.0, "action": "direct", "unit": "%"})
        if b.kind == "compressor" and b.asv is not None:
            specs.append({"name": f"ASC-{b.name}", "pv": ("margin", b.name), "target": ("valve", b.asv.name),
                          "sp": float(b.asc_cfg.get("margin_sp", max(b.sm_min, 10.0) + 5.0)), "span": (0.0, 100.0),
                          "Kc": 0.8, "Ti": 15.0, "action": "reverse", "unit": "%"})
    specs += [dict(s) for s in cfg.get("extra_controllers", [])]
    for s in specs:
        ov = cfg.get("controllers", {}).get(s["name"], {})
        if ov.get("enabled", True) is False:
            continue
        s = {**s, **{k: v for k, v in ov.items() if k not in ("enabled",)}}
        d.controllers.append(_controller(d, s))


def _controller(d, s):
    kind, obj = s["pv"]
    o = d.named(obj)
    if kind == "pressure":
        fn = lambda o=o: o.P
    elif kind == "level":
        fn = lambda o=o: o.level
    elif kind == "level_w":
        fn = lambda o=o: o.level_w
    elif kind == "temperature":
        fn = lambda o=o: o.T - K0
    elif kind == "margin":
        fn = lambda o=o: o.margin()
    elif kind == "flow":
        fn = lambda o=o: o.w
    else:
        raise DynError(f"unknown controller variable '{kind}'")
    tk, tobj = s["target"]
    t = d.named(tobj)
    op0 = {"valve": lambda: 100.0 * t.x, "speed": lambda: 100.0 * t.N / (1.1 * t.N0),
           "relief": lambda: 100.0 * t.x}[tk]()
    c = Controller(s["name"], fn, tk and t, s["sp"], s.get("Kc", 2.0), s.get("Ti", 300.0), s.get("Td", 0.0),
                   s.get("action", "direct"), s.get("span", (0.0, 100.0)), op0, kind, s.get("unit", ""))
    c.mode = s.get("mode", "auto")
    c.op_manual = float(s.get("op_manual", op0))
    _apply_target(c, t, tk)
    if tk in ("valve", "speed"):
        t.ctrl = c
    if tk == "valve" and getattr(t, "is_asv", False):
        t.owner.asc_ctrl = c
    c.target_kind = tk
    c.pv_kind, c.pv_obj = kind, obj
    c.pv = fn()
    return c


def _make_relief(d, cfg):
    for r in cfg.get("relief", []):
        node = d.named(r["node"])
        P_back = float(r.get("P_back", 1.5))
        sk = Sink(f"Flare: {r['name']}", P_back)
        d.sinks.append(sk)
        b = ReliefBranch(r["name"], node, [Dest(sk, 1.0, 0.0, [])], kind_=r.get("type", "BDV"),
                         D_mm=float(r.get("D_mm", 50.0)), Cd=float(r.get("Cd", 0.85)),
                         P_set=(float(r["P_set"]) if r.get("P_set") else None), reseat=float(r.get("reseat", 0.93)),
                         tau=float(r.get("stroke_s", 1.0)), is_open=bool(r.get("open0", False)))
        d.branches.append(b)


# ================================================================================================ helpers for the UI and tests
EVENT_KINDS = {
    # kind: (label, group of targets, meaning of Value)
    "feed_flow": ("Feed rate", "feed", "new rate in the feed's own basis (e.g. kmol/h or MSm³/d)"),
    "feed_T": ("Feed temperature", "feed", "new temperature [°C]"),
    "feed_P": ("Feed pressure", "feed", "new pressure [bar(a)]"),
    "ctrl_sp": ("Controller set-point", "controller", "new set-point (in the controller's unit)"),
    "ctrl_mode": ("Controller mode", "controller", "Auto or Manual (the output is then held)"),
    "valve_op": ("Valve opening (manual)", "valve", "opening [%]; the controller of the valve goes to manual"),
    "comp_trip": ("Compressor trip", "compressor", "(no value)"),
    "comp_start": ("Compressor start", "compressor", "target speed [% of design]; blank = design speed"),
    "comp_speed": ("Compressor speed", "compressor", "speed [% of design]"),
    "pump_trip": ("Pump trip", "pump", "(no value)"),
    "pump_start": ("Pump start", "pump", "(no value)"),
    "relief_open": ("Open a relief / blowdown valve", "relief", "(no value)"),
    "relief_close": ("Close a relief / blowdown valve", "relief", "(no value)"),
    "node_heat": ("Heat input to a vessel (fire case)", "node", "heat input [kW]"),
    "sink_P": ("Back-pressure of a product / flare", "sink", "new pressure [bar(a)]"),
}


def targets(d):
    """Names that events can address, by group, for a built :class:`Dyn`."""
    return {
        "feed": [f.name for f in d.feeds],
        "controller": [c.name for c in d.controllers],
        "valve": [b.name for b in d.branches if b.kind == "valve"],
        "compressor": [b.name for b in d.branches if b.kind == "compressor"],
        "pump": [b.name for b in d.branches if b.kind == "pump"],
        "relief": [b.name for b in d.branches if b.kind == "relief"],
        "node": [n.name for n in d.nodes if n.kind != "cell"],
        "sink": [s.name for s in d.sinks],
    }


def check_events(d, events):
    """Problems in an event table (empty list when it is fine)."""
    tg = targets(d)
    out = []
    for i, e in enumerate(events, start=1):
        k = e.get("kind")
        if k not in EVENT_KINDS:
            out.append(f"event {i}: unknown kind '{k}'")
            continue
        grp = EVENT_KINDS[k][1]
        if e.get("target") not in tg[grp]:
            out.append(f"event {i} ({EVENT_KINDS[k][0]}): '{e.get('target')}' is not a {grp} of this flowsheet")
        if e.get("t") is None or float(e["t"]) < 0:
            out.append(f"event {i}: the time must be zero or later")
        if EVENT_KINDS[k][2].startswith("(no value)") or k == "comp_start":
            continue
        if k == "ctrl_mode":
            continue
        if e.get("value") is None:
            out.append(f"event {i} ({EVENT_KINDS[k][0]}): a value is needed")
    return out


def readiness(model, sol):
    """None when a dynamic model can be built from the solved flowsheet, else the reason."""
    try:
        build(model, sol)
        return None
    except DynError as e:
        return str(e)


def summary(res):
    """Key numbers of a finished run for the UI / tests."""
    S = res["series"]
    out = {"status": res["status"], "t_end": res["t"][-1] if res["t"] else 0.0, "steps": res["steps"],
           "rejected": res["rejected"], "seconds": res["seconds"], "balance_error_kmol": res["balance_error_kmol"],
           "inventory0": res["inventory0"]}
    out["balance_rel"] = abs(res["balance_error_kmol"]) / max(res["inventory0"], 1e-12)
    ext = {}
    for k, col in S.items():
        v = [x for x in col if x is not None]
        if v and (" | Pressure" in k or " | Temperature" in k or " | Level" in k or "Surge margin" in k):
            ext[k] = (min(v), max(v))
    out["extremes"] = ext
    return out
