"""Flow-assurance screening (v6.1): shut-in heat transfer, depressurisation below the hydrate pressure, wax
appearance temperature and wax deposition, and sand erosion of bends (DNV-RP-O501).

All methods are screening level and documented where they are used:

* **Shut-in U** - while flowing, the inner film is forced convection (Dittus-Boelter on mixture properties); after a
  shutdown it becomes natural convection (about 10 W/m²K gas-filled, 60 W/m²K liquid-filled).
  1/U_shut = 1/U_flow − 1/h_forced + 1/h_natural (inner-diameter basis).
* **Depressurisation** - isothermal blowdown of the gas inventory through a vent (critical ideal-gas flow,
  P(t) = P0 exp(−t/τ), τ = V / (C_d A c*)); the liquid left in the riser keeps the low point above
  P_vent + ρ_l g H_liquid, which may stay above the hydrate pressure.
* **Wax** - (heated lines: the heat delivered at the wall is subtracted, so a wall warmer than the fluid takes no
  deposit; no shear removal or deposit insulation: an upper-bound diffusion estimate) - WAT from a multi-solid ideal model (Won 1986 melting points and heats of fusion of n-paraffins; a
  fraction of each heavy component counted as n-paraffin); deposition by molecular diffusion
  (dm/dt = D_m · dC/dT · dT/dr at the wall, Burger et al. 1981), Wilke-Chang diffusivity, linear solubility below WAT.
* **Sand erosion** - DNV-RP-O501 (2015) smooth-bend model with G = 1 (no particle-size reduction, conservative).
"""
from __future__ import annotations

import math

import numpy as np

K0 = 273.15
G = 9.81
K_PHASE = {"V": 0.04, "L": 0.12, "W": 0.60}          # W/mK thermal conductivities (screening)
H_NC = {"gas": 10.0, "liquid": 60.0}                  # W/m²K natural convection inside a shut-in line


# ------------------------------------------------------------------------------------------- shut-in U

def forced_film(fp, st, D, vm):
    """Inner forced-convection film coefficient [W/m²K] of a flowing mixture (Dittus-Boelter, Re > 2300 assumed
    turbulent; laminar Nu = 3.66)."""
    from .transport import phase_viscosity_cP
    if st.empty or vm <= 0:
        return 50.0
    vol = sum(ph.beta * ph.Vs for ph in st.flash.phases)
    rho = mu = cp = k = 0.0
    for ph in st.flash.phases:
        f = ph.beta * ph.Vs / vol                         # volume fraction (no-slip)
        rho += f * ph.rho
        mu += f * phase_viscosity_cP(fp, ph, st.T) * 1e-3
        cp += f * ph.Cp / ph.MW * 1000.0                  # J/kgK
        k += f * K_PHASE.get(ph.kind, 0.1)
    Re = rho * vm * D / max(mu, 1e-7)
    Pr = cp * mu / max(k, 1e-6)
    Nu = 0.023 * Re ** 0.8 * Pr ** 0.4 if Re > 2300 else 3.66
    return Nu * k / D


def shutin_U(U_flow, h_forced, HL):
    """U after shut-in [W/m²K]: the forced inner film replaced by natural convection."""
    h_nc = HL * H_NC["liquid"] + (1.0 - HL) * H_NC["gas"]
    r_rest = max(1.0 / U_flow - 1.0 / h_forced, 0.05 / U_flow)
    return 1.0 / (r_rest + 1.0 / h_nc)


# ----------------------------------------------------------------------------------------- depressurisation

def hydrate_pressure(fp, x_gas, T_C, dT_inhib=0.0):
    """Pressure [bar] below which the gas forms no hydrate at T (bisection on the selected hydrate model);
    None if hydrate forms at no pressure in 2-600 bar, inf if it never forms below 600 bar."""
    from .transport import hydrate_T
    def form(P):
        t = hydrate_T(fp, x_gas, P)
        return t is not None and t - dT_inhib >= T_C
    grid = list(np.exp(np.linspace(math.log(4.0), math.log(600.0), 28)))
    if form(grid[0]):
        return grid[0]
    hi = next((P for P in grid if form(P)), None)
    if hi is None:
        return math.inf                                     # no hydrate within the model's pressure range
    lo = grid[grid.index(hi) - 1]
    for _ in range(30):
        mid = math.sqrt(lo * hi)
        if form(mid):
            hi = mid
        else:
            lo = mid
    return lo


def blowdown(P0, P_target, V_gas, T_K, MW, k=1.3, Z=0.9, d_mm=50.0, Cd=0.85, P_vent=2.0):
    """(time [h] from P0 to P_target, time constant [h]) of an isothermal gas blowdown through an orifice; the
    exponential (critical-flow) law is used down to the target and noted when the flow turns sub-critical."""
    if P_target >= P0:
        return 0.0, None
    A = math.pi * (d_mm / 1000.0) ** 2 / 4.0
    c_star = math.sqrt(k * Z * 8.314462618 * T_K / (MW / 1000.0)) * (2.0 / (k + 1.0)) ** ((k + 1.0) / (2.0 * (k - 1.0)))
    tau = V_gas / (Cd * A * c_star)
    P_t = max(P_target, P_vent * 1.05)
    return tau * math.log(P0 / P_t) / 3600.0, tau / 3600.0


# ---------------------------------------------------------------------------------------------------- wax

def won_melting(MW):
    """(melting point [K], heat of fusion [J/mol]) of an n-paraffin of molecular weight MW (Won 1986)."""
    Tf = 374.5 + 0.02617 * MW - 20172.0 / MW
    return Tf, 0.1426 * MW * Tf * 4.184


def wax_formers(fp, paraffin_frac=0.3):
    """[(index, fraction of the component that is n-paraffin)] for components heavier than C7."""
    out = []
    for i, c in enumerate(fp.comps):
        if c.key in ("H2O", "MeOH", "MEG") or c.MW < 98.0:
            continue
        frac = 1.0 if c.key.startswith("nC") else paraffin_frac
        out.append((i, frac))
    return out


def wat_estimate(fp, z, P_bar, paraffin_frac=0.3, T_lo=200.0, T_hi=360.0):
    """Wax appearance temperature [°C] of a fluid at P: highest T where any n-paraffin in the hydrocarbon liquid
    exceeds its ideal solid solubility, x_i ≥ exp(−ΔH_f/R (1/T − 1/T_f)). None if no solid in the range."""
    formers = wax_formers(fp, paraffin_frac)
    if not formers:
        return None

    def excess(T):
        try:
            fr = fp.pt_flash(z, T, P_bar)
        except Exception:                                   # noqa: BLE001
            return -1.0
        liq = fr.phase("L")
        x = liq.x if liq is not None else None
        if x is None:
            return -1.0
        best = -1e9
        for i, f in formers:
            if x[i] * f <= 0:
                continue
            Tf, dH = won_melting(fp.MW[i])
            best = max(best, math.log(x[i] * f) + dH / 8.314462618 * (1.0 / T - 1.0 / Tf))
        return best
    if excess(T_lo) < 0:
        return None
    if excess(T_hi) >= 0:
        return T_hi - K0
    lo, hi = T_lo, T_hi
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        if excess(mid) >= 0:
            lo = mid
        else:
            hi = mid
        if hi - lo < 0.02:
            break
    return 0.5 * (lo + hi) - K0


WAX_DEFAULTS = {"WAT": -999.0, "wax_wt": 5.0, "sol_span": 60.0, "porosity": 0.6, "paraffin_frac": 0.3,
                "pig_mm": 3.0, "sand_kg_d": 10.0, "bend_R": 5.0, "allow_mm": 3.0, "vent_mm": 50.0}


def wax_params(model):
    p = dict(WAX_DEFAULTS)
    p.update({k: v for k, v in (model.get("waxsand") or {}).items() if k in WAX_DEFAULTS})
    return p


def wilke_chang(T_K, mu_cP, MW_solvent, V_solute_cm3=430.0):
    """Molecular diffusivity [m²/s] of a wax molecule in oil (Wilke & Chang 1955, association factor 1)."""
    return 7.4e-8 * math.sqrt(MW_solvent) * T_K / (max(mu_cP, 1e-3) * V_solute_cm3 ** 0.6) * 1e-4


def wax_deposition(fp, prof, st, D, U, T_amb_C, WAT_C, p, vm_default=1.0):
    """Wax deposit growth along a line: rows per profile point (wall T, rate [mm/y]) and the maximum."""
    from .transport import phase_viscosity_cP
    liq = st.flash.phase("L") if not st.empty else None
    if liq is None:
        return [], 0.0
    mu_l = phase_viscosity_cP(fp, liq, st.T)
    rho_l = liq.rho
    Dm = wilke_chang(st.T, mu_l, liq.MW)
    dCdT = rho_l * p["wax_wt"] / 100.0 / max(p["sol_span"], 1.0)          # kg/m³K
    k_oil = K_PHASE["L"]
    rows, worst = [], 0.0
    qh = prof.get("q_heat") or []
    for kx in range(len(prof["L"])):
        Tb = prof["T"][kx]
        vm = prof["vm"][kx] if kx < len(prof.get("vm", [])) else vm_default
        h = forced_film(fp, st, D, vm)
        q_in = (qh[min(kx, len(qh) - 1)] if qh else 0.0) / (math.pi * D)    # heating delivered at the wall, W/m²
        q = U * (Tb - T_amb_C) - q_in                                       # net flux from the fluid to the wall
        Tw = Tb - q / h
        rate = 0.0
        if Tw < WAT_C and q > 0:
            dTdr = q / k_oil
            flux = Dm * dCdT * dTdr                                         # kg/m²s
            rate = flux / (900.0 * (1.0 - p["porosity"])) * 3.156e7 * 1000.0   # mm/y of deposit
        worst = max(worst, rate)
        rows.append({"Distance [m]": prof["L"][kx], "Bulk T [°C]": Tb, "Wall T [°C]": Tw, "Deposit growth [mm/y]": rate})
    return rows, worst


# ------------------------------------------------------------------------------------------ sand erosion

def dnv_bend_erosion(U_p, D, sand_kg_s, R=5.0, rho_t=7800.0, K=2.0e-9, n=2.6, C1=2.5, GF=2.0, Gfac=1.0):
    """Erosion rate [mm/year] of a smooth bend (DNV-RP-O501): E = K U^n F(α) m_p G C1 GF C_unit / (ρ_t A_t)."""
    if U_p <= 0 or sand_kg_s <= 0:
        return 0.0
    alpha = math.atan(1.0 / math.sqrt(2.0 * R))
    sa = math.sin(alpha)
    F = 0.6 * (sa + 7.2 * (sa - sa * sa)) ** 0.6 * (1.0 - math.exp(-20.0 * alpha))
    A_t = math.pi * D * D / 4.0 / sa
    return K * U_p ** n * F * sand_kg_s * Gfac * C1 * GF * 3.15e10 / (rho_t * A_t)


def velocity_for_erosion(rate_mm_y, D, sand_kg_s, R=5.0):
    """Mixture velocity [m/s] at which a bend erodes at rate_mm_y."""
    e1 = dnv_bend_erosion(1.0, D, sand_kg_s, R)
    return (rate_mm_y / e1) ** (1.0 / 2.6) if e1 > 0 else math.inf


# ----------------------------------------------------------------------------------------- per flowsheet

def line_units(model, sol):
    """(uid, unit, inlet stream, outlet stream) of solved lines with a profile."""
    from .flowsheet import port_edges
    out = []
    for uid, u in sorted(model["units"].items(), key=lambda kv: kv[1]["name"]):
        if u["type"] not in ("flowline", "riser", "jumper", "pipe", "well") or uid not in sol.profiles:
            continue
        ins = [s for lst in port_edges(model, uid, "in").values() for s in lst]
        outs = [s for lst in port_edges(model, uid, "out").values() for s in lst]
        if not ins or not outs or ins[0] not in sol.streams or outs[0] not in sol.streams:
            continue
        if sol.streams[ins[0]].empty:
            continue
        out.append((uid, u, sol.streams[ins[0]], sol.streams[outs[0]]))
    return out


def _U_of(u):
    from . import surf
    from .unitops import UnitError
    p = u["params"]
    if u["type"] in ("flowline", "riser"):
        cat, key = ("flowline", "design") if u["type"] == "flowline" else ("riser", "rtype")
        try:
            row = surf.item(cat, p.get(key))
        except UnitError:
            row = {}
        return float(p.get("U", 0.0) or 0.0) or (row.get("U_W_m2K") or 5.0)
    return float(p.get("U", 0.0) or 0.0)


def _D(u):
    return float(u["params"].get("ID", 254.0)) / 1000.0


def erosion_table(model, sol, p=None):
    """Sand erosion of a bend at the highest mixture velocity of every line."""
    p = p or wax_params(model)
    m_s = float(p["sand_kg_d"]) / 86400.0
    rows = []
    for uid, u, sin, sout in line_units(model, sol):
        prof = sol.profiles[uid]
        vms = prof.get("vm") or []
        if not vms:
            continue
        v = max(vms)
        D = _D(u)
        e = dnv_bend_erosion(v, D, m_s, float(p["bend_R"]))
        life = float(p["allow_mm"]) / e if e > 0 else math.inf
        rows.append({"Line": u["name"], "ID [mm]": D * 1000.0, "Max. mixture velocity [m/s]": v,
                     "Erosion rate [mm/y]": e, "Years to use the allowance": life,
                     "Velocity for 0.1 mm/y [m/s]": velocity_for_erosion(0.1, D, m_s, float(p["bend_R"])),
                     "uid": uid})
    return rows


def wax_table(model, sol, p=None):
    """WAT (estimated or entered) and the deposit growth along every line with a hydrocarbon liquid."""
    p = p or wax_params(model)
    out = []
    for uid, u, sin, sout in line_units(model, sol):
        if u["type"] == "well":
            continue
        if sin.flash.phase("L") is None and sout.flash.phase("L") is None:
            continue
        WAT = float(p["WAT"])
        est = None
        if WAT <= -900:
            est = wat_estimate(sol.fp, sin.z, sin.P, float(p["paraffin_frac"]))
            WAT = est if est is not None else -999.0
        prof = sol.profiles[uid]
        U = _U_of(u)
        T_amb = float(u["params"].get("T_amb", u["params"].get("T_wh_amb", 4.0)))
        st = sin if sin.flash.phase("L") is not None else sout
        rows, worst = wax_deposition(sol.fp, prof, st, _D(u), U, T_amb, WAT, p) if U > 0 and WAT > -900 else ([], 0.0)
        out.append({"Line": u["name"], "WAT [°C]": None if WAT <= -900 else WAT,
                    "WAT source": ("entered" if float(p["WAT"]) > -900 else
                                   "estimated (Won)" if est is not None else "estimated: no wax above −73 °C"),
                    "Min. wall T [°C]": min((r["Wall T [°C]"] for r in rows), default=None),
                    "Max. deposit growth [mm/y]": worst,
                    "Pigging interval [days]": (float(p["pig_mm"]) / worst * 365.25) if worst > 0 else math.inf,
                    "rows": rows, "uid": uid})
    return out


def depressurisation(model, sol, p=None):
    """Per flowline / riser: hydrate pressure at seabed temperature, settle-out pressure, blowdown time through the
    vent, and the pressure the liquid column keeps at the low point."""
    from . import subsea_ops as so
    from .transport import hydrate_depression
    p = p or wax_params(model)
    rows = []
    fp = sol.fp
    for uid, u, sin, sout in line_units(model, sol):
        if u["type"] not in ("flowline", "riser", "pipe"):
            continue
        if fp.iw < 0 or sin.z[fp.iw] <= 1e-9:
            continue
        v = sin.flash.phase("V")
        if v is None:
            continue
        prof = sol.profiles[uid]
        T_sea = float(u["params"].get("T_amb", 4.0))
        aq = sin.flash.phase("W")
        dTi = hydrate_depression(fp, aq.x if aq is not None else sin.z)
        P_hyd = hydrate_pressure(fp, v.x, T_sea, dTi)
        P_settle = sum(prof["P"]) / len(prof["P"])
        D = _D(u)
        L = prof["L"][-1] - prof["L"][0]
        HL = sum(prof.get("HL") or [0.0]) / max(len(prof.get("HL") or [1]), 1)
        V_gas = math.pi * D * D / 4.0 * L * (1.0 - HL)
        dz = max(prof["z"]) - min(prof["z"]) if prof.get("z") else 0.0
        liq = sin.flash.phase("L") or sin.flash.phase("W")
        rho_l = liq.rho if liq is not None else 0.0
        P_low = 2.0 + rho_l * G * dz * HL / 1e5
        t_h, tau_h = (blowdown(P_settle, P_hyd, V_gas, T_sea + K0, v.MW, d_mm=float(p["vent_mm"]))
                      if P_hyd not in (None, math.inf) else (0.0, None))
        ok = P_hyd is None or P_hyd == math.inf or P_low < P_hyd
        rows.append({"Line": u["name"], "Seabed T [°C]": T_sea, "Inhibitor depression [°C]": dTi,
                     "Hydrate pressure at seabed T [bar(a)]": None if P_hyd in (None, math.inf) else P_hyd,
                     "Settle-out P [bar(a)]": P_settle, "Gas volume [m³]": V_gas,
                     "Blowdown time to below the hydrate P [h]": t_h if P_hyd not in (None, math.inf) else None,
                     "Low-point P from the liquid column [bar(a)]": P_low,
                     "Can depressurise below the hydrate P": "yes" if ok else "no (liquid head)",
                     "Note": ("no hydrate at any pressure at seabed T" if P_hyd == math.inf else
                              "already below the hydrate pressure" if P_hyd is not None and P_settle <= P_hyd else ""),
                     "_MW": v.MW, "_T": T_sea + K0})
    lines = [r for r in rows if r["Hydrate pressure at seabed T [bar(a)]"] is not None]
    if len(rows) > 1 and lines:                 # the system is blown down together through one vent
        V = sum(r["Gas volume [m³]"] for r in rows)
        P0 = max(r["Settle-out P [bar(a)]"] for r in rows)
        Ph = min(r["Hydrate pressure at seabed T [bar(a)]"] for r in lines)
        t, _ = blowdown(P0, Ph, V, min(r["_T"] for r in rows), rows[0]["_MW"], d_mm=float(p["vent_mm"]))
        rows.append({"Line": "Whole system (one vent)", "Seabed T [°C]": min(r["Seabed T [°C]"] for r in rows),
                     "Inhibitor depression [°C]": min(r["Inhibitor depression [°C]"] for r in rows),
                     "Hydrate pressure at seabed T [bar(a)]": Ph, "Settle-out P [bar(a)]": P0, "Gas volume [m³]": V,
                     "Blowdown time to below the hydrate P [h]": t,
                     "Low-point P from the liquid column [bar(a)]": max(r["Low-point P from the liquid column [bar(a)]"] for r in rows),
                     "Can depressurise below the hydrate P": "yes" if all(r["Can depressurise below the hydrate P"] == "yes" for r in rows) else "no (liquid head)",
                     "Note": "lowest hydrate pressure, highest settle-out pressure, total gas volume"})
    for r in rows:
        r.pop("_MW", None)
        r.pop("_T", None)
    return rows
