"""OLGA-style flow-assurance screening (v6.6).

Steady-state, screening level methods that sit next to the Beggs & Brill line model - none of them is a transient
simulator. Every correlation is documented where it is used:

* **Mechanistic flow regime and holdup** - Taitel & Dukler (1976) map with the equilibrium stratified holdup for near-
  horizontal lines (|θ| ≤ 15°); Taitel, Barnea & Dukler (1980) bubble / slug / annular transitions for inclined and
  vertical lines. Holdup: equilibrium stratified, Butterworth fit of Lockhart-Martinelli (annular), Bendiksen (1984)
  drift flux (slug / bubble), no-slip (dispersed bubble). Reported next to Beggs & Brill at the same state.
* **CO₂ corrosion** - de Waard, Lotz & Milliams (1991/1993): kinetic rate in series with mass transfer, scale
  temperature correction, pH correction with the pH of CO₂-saturated condensed water (de Waard 1995 form); inhibitor
  efficiency as a factor. (A NORSOK M-506 model needs its licensed tables; this is the open literature model.)
* **Emulsions** - Brinkman (1952) viscosity of the dispersion with the inversion water cut of Arirachakaran et al. (1989).
* **Liquid loading** - Turner et al. (1969) droplet-reversal velocity and the Coleman et al. (1991) version without the
  20 % factor.
* **Pigging** - run time at the pig speed, liquid swept ahead of the pig, the receiver arrival rate and the extra
  inlet pressure while the slug is pushed.
* **Line pack** - gas inventory (real-gas law) and the volume available by drawing the line down to a minimum pressure.
* **Insulation** - thickness that gives a target no-touch (cool-down) time, with the existing cool-down model.
* **PVT table** - property grid of a stream composition for use in other tools.
"""
from __future__ import annotations

import math

import numpy as np

from .unitops import _phase_split, pipe_gradient, churchill_f, K0, G
from .flowassure import line_units, _D, _U_of

R_GAS = 8.314462618
STD_VOL = 0.0236454            # m³/mol at 15 °C, 1.01325 bar
SIGMA_DEFAULT = 0.02           # N/m


# =================================================================================== mechanistic regime
def _geom(h):
    """Dimensionless areas / perimeters of a stratified layer of relative height h = hL/D (Taitel & Dukler)."""
    c = 2.0 * h - 1.0
    ac = math.acos(max(-1.0, min(1.0, c)))
    s = math.sqrt(max(1.0 - c * c, 0.0))
    AL = 0.25 * (math.pi - ac + c * s)
    AG = 0.25 * (ac - c * s)
    return AL, AG, math.pi - ac, ac, s


def _fric(Re):
    """(coefficient, exponent) of f = C·Re^-n for laminar / turbulent flow."""
    return (16.0, 1.0) if Re < 1500.0 else (0.046, 0.2)


def _stratified_h(X2, Y, nL, nG):
    """Equilibrium liquid height h/D of stratified flow (Taitel-Dukler dimensionless momentum balance)."""
    A0 = math.pi / 4.0

    def res(h):
        AL, AG, SL, SG, Si = _geom(h)
        uL, uG = A0 / AL, A0 / AG
        DL, DG = 4.0 * AL / SL, 4.0 * AG / (SG + Si)
        return (X2 * (uL * DL) ** (-nL) * uL ** 2 * SL / AL
                - (uG * DG) ** (-nG) * uG ** 2 * (SG / AG + Si * (1.0 / AL + 1.0 / AG)) - 4.0 * Y)
    hs = [1e-4 + (1.0 - 2e-4) * i / 300.0 for i in range(301)]
    f = [res(h) for h in hs]
    for i in range(300):
        if f[i] * f[i + 1] <= 0.0:
            a, b, fa = hs[i], hs[i + 1], f[i]
            for _ in range(60):
                m = 0.5 * (a + b)
                fm = res(m)
                if fa * fm <= 0.0:
                    b = m
                else:
                    a, fa = m, fm
            return 0.5 * (a + b)
    return None


def mechanistic_regime(vsg, vsl, D, theta, rho_g, rho_l, mu_g, mu_l, sigma=SIGMA_DEFAULT):
    """Flow regime and liquid holdup. theta [rad] is positive for upward flow. Returns a dict."""
    out = {"vsg": vsg, "vsl": vsl}
    if vsl <= 1e-9:
        return dict(out, regime="Single-phase gas", HL=0.0, method="-")
    if vsg <= 1e-9:
        return dict(out, regime="Single-phase liquid", HL=1.0, method="-")
    Um = vsg + vsl
    lam = vsl / Um
    drho = max(rho_l - rho_g, 1e-6)
    sin_t, cos_t = math.sin(theta), math.cos(theta)
    nuL, nuG = mu_l / rho_l, mu_g / rho_g
    ReL, ReG = vsl * D / nuL, vsg * D / nuG
    CL, nL = _fric(ReL)
    CG, nG = _fric(ReG)
    dpL = 2.0 * CL * ReL ** (-nL) * rho_l * vsl ** 2 / D
    dpG = 2.0 * CG * ReG ** (-nG) * rho_g * vsg ** 2 / D
    X2 = dpL / max(dpG, 1e-12)
    gD = math.sqrt(G * D)
    out["X"] = math.sqrt(X2)
    if abs(theta) <= math.radians(15.0):
        Y = -drho * G * sin_t / max(dpG, 1e-12)            # Taitel-Dukler's Y: positive for downward flow
        h = _stratified_h(X2, Y, nL, nG)
        if h is not None:
            AL_, AG_, SL, SG, Si = _geom(h)
            A0 = math.pi / 4.0
            uG = A0 / AG_
            F2 = (rho_g / drho) * vsg ** 2 / (D * G * max(cos_t, 1e-3))
            dAL = Si                                          # dÃL/dh̃
            strat = F2 * (uG ** 2 * dAL / (AG_ * (1.0 - h) ** 2)) < 1.0
            if strat:
                return dict(out, regime="Stratified", HL=AL_ / A0, method="Taitel-Dukler equilibrium",
                            h_D=h)
            uL = A0 / AL_
            DL = 4.0 * AL_ / SL
            T2 = dpL / (drho * G * max(cos_t, 1e-3))
            if h > 0.5 and T2 >= 8.0 * AG_ / (Si * uL ** 2 * (uL * DL) ** (-nL)):
                return dict(out, regime="Dispersed bubble", HL=lam, method="no slip", h_D=h)
            if h > 0.5:
                C0 = 1.05 + 0.15 * sin_t ** 2
                u0 = gD * (0.35 * sin_t + 0.54 * cos_t)
                HG = vsg / (C0 * Um + u0)
                return dict(out, regime="Slug (intermittent)", HL=min(max(1.0 - HG, lam), 1.0),
                            method="Bendiksen drift flux", h_D=h)
        # no stratified equilibrium, or a thin film: annular
        HL = 1.0 - (1.0 + X2 ** 0.4) ** -0.378
        return dict(out, regime="Annular", HL=min(max(HL, 0.0), 1.0), method="Butterworth (Lockhart-Martinelli)")
    # inclined / vertical (Taitel, Barnea, Dukler 1980)
    v0 = 1.53 * (G * drho * sigma / rho_l ** 2) ** 0.25
    vsg_ann = 3.1 * (sigma * G * drho / rho_g ** 2) ** 0.25
    d_min = 19.0 * math.sqrt(drho * sigma / (rho_l ** 2 * G))
    if vsg >= vsg_ann:
        HL = 1.0 - (1.0 + X2 ** 0.4) ** -0.378
        return dict(out, regime="Annular", HL=min(max(HL, 0.0), 1.0), method="Butterworth (Lockhart-Martinelli)")
    if D >= d_min and vsg <= 0.4286 * vsl + 0.357 * v0:
        HG = vsg / (1.2 * Um + v0)
        return dict(out, regime="Bubble", HL=min(max(1.0 - HG, lam), 1.0), method="drift flux")
    C0 = 1.2 if sin_t > 0.99 else 1.05 + 0.15 * sin_t ** 2
    u0 = gD * (0.35 * sin_t + 0.54 * cos_t)
    HG = vsg / (C0 * Um + u0)
    return dict(out, regime="Slug / churn", HL=min(max(1.0 - HG, lam), 1.0), method="Bendiksen drift flux")


def _state(fp, st, D):
    qg, ql, rg, rl, mg, ml = _phase_split(fp, st)
    A = math.pi * D * D / 4.0
    return {"vsg": qg / A, "vsl": ql / A, "rho_g": rg, "rho_l": rl, "mu_g": mg, "mu_l": ml}


def _mean_theta(prof):
    L = prof["L"][-1] - prof["L"][0]
    dz = prof["z"][-1] - prof["z"][0]
    return math.asin(max(-1.0, min(1.0, dz / L))) if L > 0 else 0.0


def mechanistic_table(model, sol, sigma=SIGMA_DEFAULT):
    """Per line, at the inlet and outlet state: mechanistic regime / holdup beside Beggs & Brill."""
    from .subsea_ops import slug_body_holdup, slug_frequency, mean_slug_length
    rows = []
    fp = sol.fp
    for uid, u, sin, sout in line_units(model, sol):
        prof = sol.profiles[uid]
        D = _D(u)
        th = _mean_theta(prof)
        for where, st in (("inlet", sin), ("outlet", sout)):
            if st.empty:
                continue
            s = _state(fp, st, D)
            m = mechanistic_regime(s["vsg"], s["vsl"], D, th, s["rho_g"], s["rho_l"], s["mu_g"], s["mu_l"], sigma)
            _, bb = pipe_gradient(fp, st, D, 4.5e-5, th, "Beggs & Brill", sigma)
            row = {"Line": u["name"], "At": where, "Inclination [°]": math.degrees(th),
                   "vsg [m/s]": s["vsg"], "vsl [m/s]": s["vsl"],
                   "Regime (mechanistic)": m["regime"], "Holdup (mechanistic) [-]": m["HL"],
                   "Regime (Beggs & Brill)": bb["regime"], "Holdup (Beggs & Brill) [-]": bb["HL"],
                   "Holdup difference [-]": m["HL"] - bb["HL"], "Holdup method": m["method"]}
            if m["regime"].startswith("Slug"):
                Um = s["vsg"] + s["vsl"]
                Ls = mean_slug_length(D)
                f = slug_frequency(s["vsl"], Um, D)
                row["Slug frequency [1/h]"] = f * 3600.0
                row["Mean slug length [m]"] = Ls
                row["Slug body holdup [-]"] = slug_body_holdup(Um)
            row["uid"] = uid
            rows.append(row)
    return rows


# =================================================================================== CO2 corrosion
def co2_corrosion(T_C, pCO2_bar, vm, D, pH=None, inhib_eff=0.0):
    """CO₂ corrosion rate [mm/y] of carbon steel (de Waard-Lotz-Milliams 1993 with scale and pH corrections).
    Returns (rate, details)."""
    pco2 = max(pCO2_bar, 1e-6)
    T = T_C + K0
    lg = math.log10(pco2)
    Vr = 10.0 ** (4.93 - 1119.0 / T + 0.58 * lg)                       # kinetic [mm/y]
    Vm = 2.45 * max(vm, 0.05) ** 0.8 / D ** 0.2 * pco2                 # mass transfer [mm/y]
    V = 1.0 / (1.0 / Vr + 1.0 / Vm)
    Ts = 2400.0 / (6.7 + 0.6 * lg)
    Fs = 1.0
    if T > Ts:
        Fs = min(1.0, 10.0 ** (2400.0 / T - 0.6 * lg - 6.7))
    pH_sat = 3.71 + 0.00417 * T_C - 0.5 * lg
    Fph = 1.0
    if pH is not None:
        Fph = min(1.0, 10.0 ** (0.32 * (pH_sat - pH)))
    V_nopH = V * Fs
    rate = V_nopH * Fph * (1.0 - min(max(inhib_eff, 0.0), 99.0) / 100.0)
    return rate, {"kinetic [mm/y]": Vr, "mass transfer [mm/y]": Vm, "scale factor": Fs, "pH factor": Fph,
                  "pH of saturated water": pH_sat, "uninhibited, no pH credit [mm/y]": V_nopH}


CORR_DEFAULTS = {"life_y": 25.0, "allow_mm": 3.0, "pH": -1.0, "inhib_eff": 0.0, "avail": 90.0}


def corrosion_table(model, sol, p=None):
    """CO₂ corrosion at the inlet and outlet of every line carrying free water (or a hydrocarbon with CO₂)."""
    q = dict(CORR_DEFAULTS)
    q.update(p or {})
    fp = sol.fp
    if "CO2" not in fp.keys:
        return []
    ic = fp.keys.index("CO2")
    ih = fp.keys.index("H2S") if "H2S" in fp.keys else -1
    rows = []
    for uid, u, sin, sout in line_units(model, sol):
        D = _D(u)
        best = None
        for where, st in (("inlet", sin), ("outlet", sout)):
            if st.empty:
                continue
            aq = st.flash.phase("W")
            v = st.flash.phase("V")
            if v is not None:
                yc = float(v.x[ic])
                yh = float(v.x[ih]) if ih >= 0 else 0.0
            else:
                yc, yh = float(st.z[ic]), (float(st.z[ih]) if ih >= 0 else 0.0)
            pco2, ph2s = yc * st.P, yh * st.P
            s = _state(fp, st, D)
            vm = max(s["vsg"] + s["vsl"], 0.05)
            pH = None if q["pH"] < 0 else float(q["pH"])
            T_C = st.T - K0
            eff = float(q["inhib_eff"]) * float(q["avail"]) / 100.0
            rate, det = co2_corrosion(T_C, pco2, vm, D, pH, eff)
            wet = aq is not None
            r = {"At": where, "T [°C]": T_C, "P [bar(a)]": st.P, "pCO₂ [bar]": pco2, "pH₂S [bar]": ph2s,
                 "Mixture velocity [m/s]": vm, "Free water": "yes" if wet else "no",
                 "Corrosion rate [mm/y]": rate if wet else 0.0,
                 "pH of saturated water": det["pH of saturated water"],
                 "_unc": det["uninhibited, no pH credit [mm/y]"], "_det": det}
            if best is None or r["Corrosion rate [mm/y]"] > best["Corrosion rate [mm/y]"] or \
                    (best["Free water"] == "no" and r["Free water"] == "yes"):
                best = r
        if best is None:
            continue
        rate = best["Corrosion rate [mm/y]"]
        need = rate * float(q["life_y"])
        row = {"Line": u["name"], "Worst at": best["At"], "T [°C]": best["T [°C]"], "P [bar(a)]": best["P [bar(a)]"],
               "pCO₂ [bar]": best["pCO₂ [bar]"], "Free water": best["Free water"],
               "Mixture velocity [m/s]": best["Mixture velocity [m/s]"],
               "pH of saturated water": best["pH of saturated water"],
               "Corrosion rate [mm/y]": rate, "Allowance needed over the life [mm]": need,
               "Allowance available [mm]": float(q["allow_mm"]),
               "Result": ("no free water" if best["Free water"] == "no" else
                          "ok" if need <= float(q["allow_mm"]) else "exceeds the allowance")}
        unc = best["_unc"]
        if unc > 0.1 and best["Free water"] == "yes":
            pHn = best["pH of saturated water"] + math.log10(unc / 0.1) / 0.32
            row["pH needed for 0.1 mm/y"] = pHn
            row["pH stabilisation enough"] = "yes" if pHn <= 6.5 else "no (needs inhibitor or CRA)"
        else:
            row["pH needed for 0.1 mm/y"] = None
            row["pH stabilisation enough"] = "not needed" if best["Free water"] == "yes" else "-"
        if best["Free water"] == "yes":
            row["Inhibitor efficiency needed for the allowance [%]"] = max(
                0.0, 100.0 * (1.0 - float(q["allow_mm"]) / max(unc * float(q["life_y"]), 1e-12)))
        sour = best["pH₂S [bar]"] > 0.003
        row["Sour service (pH₂S > 0.003 bar)"] = "yes - check ISO 15156 / NACE MR0175" if sour else "no"
        row["uid"] = uid
        rows.append(row)
    return rows


# =================================================================================== emulsions
def inversion_cut(mu_o_cP):
    """Water cut at which the emulsion inverts, Arirachakaran et al. (1989): 0.5 − 0.1108 log10(μo [cP])."""
    return min(max(0.5 - 0.1108 * math.log10(max(mu_o_cP, 1e-3)), 0.25), 0.75)


def emulsion_viscosity(mu_o, mu_w, wc, phi_inv=None):
    """Apparent viscosity of an oil-water mixture [same unit as mu_o, mu_w] and the continuous phase.
    Brinkman: μ = μc (1 − φ)^-2.5 with φ the dispersed fraction; the continuous phase changes at the inversion water
    cut. A stable emulsion is assumed (no demulsifier, enough shear): condensate-water mixtures often separate."""
    wc = min(max(wc, 0.0), 1.0)
    phi_inv = inversion_cut(mu_o) if phi_inv is None else phi_inv                # viscosities in cP
    if wc <= phi_inv:
        phi = wc
        mu_c, cont = mu_o, "oil"
    else:
        phi = 1.0 - wc
        mu_c, cont = mu_w, "water"
    phi = min(phi, 0.74)
    return mu_c * (1.0 - phi) ** -2.5, cont


def emulsion_table(model, sol):
    """Per line with both oil and water: viscosity of the mixture at the actual water cut, and the curve."""
    from .transport import phase_viscosity_cP, water_viscosity_cP
    rows = []
    fp = sol.fp
    for uid, u, sin, sout in line_units(model, sol):
        st = sin
        lo, lw = st.flash.phase("L"), st.flash.phase("W")
        if lo is None or lw is None:
            continue
        T = st.T
        mu_o = phase_viscosity_cP(fp, lo, T)
        mu_w = water_viscosity_cP(T) if lw is None else phase_viscosity_cP(fp, lw, T)
        qo = st.F * lo.beta * lo.Vs
        qw = st.F * lw.beta * lw.Vs
        wc = qw / (qo + qw)
        inv = inversion_cut(mu_o)
        mu, cont = emulsion_viscosity(mu_o, mu_w, wc, inv)
        cuts = [i / 20.0 for i in range(0, 21)]
        curve = [emulsion_viscosity(mu_o, mu_w, w, inv)[0] for w in cuts]
        peak = max(curve)
        rows.append({"Line": u["name"], "T [°C]": T - K0, "Oil viscosity [cP]": mu_o, "Water viscosity [cP]": mu_w,
                     "Water cut [vol %]": 100.0 * wc, "Inversion water cut [vol %]": 100.0 * inv,
                     "Continuous phase": cont, "Apparent viscosity [cP]": mu,
                     "Viscosity ratio to oil [-]": mu / mu_o, "Peak viscosity on the curve [cP]": peak,
                     "Note": ("close to inversion: viscosity peak, check the pressure drop"
                              if abs(wc - inv) < 0.1 else ""),
                     "curve": {"wc": cuts, "mu": curve, "inv": inv, "now": wc}, "uid": uid})
    return rows


# =================================================================================== liquid loading
def liquid_loading(vg, rho_g, rho_l, sigma=SIGMA_DEFAULT):
    """(Turner, Coleman) critical gas velocities [m/s] for lifting liquid droplets.
    Field form v = 1.593 σ^0.25 (ρL − ρG)^0.25 / ρG^0.5 (Coleman; σ dyn/cm, ρ lbm/ft³, v ft/s) converted to SI;
    Turner adds 20 %."""
    k_si = 1.593 * 1000.0 ** 0.25 * 16.018 ** 0.25 / 3.2808
    vc = k_si * sigma ** 0.25 * max(rho_l - rho_g, 1e-6) ** 0.25 / math.sqrt(max(rho_g, 1e-6))
    return 1.2 * vc, vc


def loading_table(model, sol, sigma=SIGMA_DEFAULT):
    """Wells and upward lines carrying gas and liquid: margin of the gas velocity over the droplet velocity."""
    rows = []
    fp = sol.fp
    for uid, u, sin, sout in line_units(model, sol):
        prof = sol.profiles[uid]
        th = _mean_theta(prof)
        if u["type"] not in ("well", "riser") and th < math.radians(5.0):
            continue
        D = _D(u)
        worst = None
        for where, st in (("inlet", sin), ("outlet", sout)):
            if st.empty or st.flash.phase("V") is None:
                continue
            s = _state(fp, st, D)
            if s["vsl"] <= 1e-9 or s["vsg"] <= 1e-9:
                continue
            vt, vc = liquid_loading(s["vsg"], s["rho_g"], s["rho_l"], sigma)
            v = st.flash.phase("V")
            Qstd = st.F * v.beta * 1000.0 / 3600.0 * STD_VOL * 86400.0 / 1e6          # MSm³/d gas
            r = {"At": where, "Gas velocity [m/s]": s["vsg"], "Turner velocity [m/s]": vt,
                 "Coleman velocity [m/s]": vc, "Margin to Turner [-]": s["vsg"] / vt,
                 "Gas rate [MSm³/d]": Qstd, "Minimum stable rate (Turner) [MSm³/d]": Qstd * vt / s["vsg"],
                 "Minimum stable rate (Coleman) [MSm³/d]": Qstd * vc / s["vsg"]}
            if worst is None or r["Margin to Turner [-]"] < worst["Margin to Turner [-]"]:
                worst = r
        if worst is None:
            continue
        worst = dict({"Line": u["name"]}, **worst)
        worst["Result"] = ("unloading risk" if worst["Margin to Turner [-]"] < 1.0 else
                           "marginal (< 1.2)" if worst["Margin to Turner [-]"] < 1.2 else "ok")
        worst["uid"] = uid
        rows.append(worst)
    return rows


# =================================================================================== pigging
PIG_DEFAULTS = {"v_pig": -1.0, "catcher_m3": 150.0}


def pigging_table(model, sol, p=None):
    q = dict(PIG_DEFAULTS)
    q.update(p or {})
    rows = []
    fp = sol.fp
    for uid, u, sin, sout in line_units(model, sol):
        if u["type"] == "well":
            continue
        prof = sol.profiles[uid]
        D = _D(u)
        A = math.pi * D * D / 4.0
        L = prof["L"][-1] - prof["L"][0]
        if L <= 0 or sin.empty or sout.empty:
            continue
        vms = prof.get("vm") or [1.0]
        v_pig = float(q["v_pig"]) if float(q["v_pig"]) > 0 else sum(vms) / len(vms)
        t_run = L / max(v_pig, 1e-6)
        HL = sum(prof.get("HL") or [0.0]) / max(len(prof.get("HL") or [1]), 1)
        so_ = _state(fp, sout, D)
        Qliq_out = so_["vsl"] * A                                   # m³/s arriving at the receiver
        V_in = HL * A * L
        V_sw = max(V_in - Qliq_out * t_run, 0.0)
        rate_peak = v_pig * A * 3600.0                              # m³/h of liquid at the receiver while the slug arrives
        dur = V_sw / (v_pig * A) / 3600.0 if v_pig > 0 else 0.0
        Ls = V_sw / A
        si_ = _state(fp, sin, D)
        rho_l = so_["rho_l"]
        mu_l = max(so_["mu_l"], 1e-4)
        Re = rho_l * v_pig * D / mu_l
        f = churchill_f(Re, 4.5e-5 / D)
        dz = prof["z"][-1] - prof["z"][0]
        dP = rho_l * (G * max(math.sin(_mean_theta(prof)), 0.0) * Ls + f * v_pig ** 2 * Ls / (2.0 * D)) / 1e5
        note = []
        if v_pig < 0.5:
            note.append("pig speed < 0.5 m/s: stall risk")
        if v_pig > 5.0:
            note.append("pig speed > 5 m/s: wear and surge")
        if V_sw > float(q["catcher_m3"]):
            note.append(f"swept liquid exceeds the {float(q['catcher_m3']):.0f} m³ receiver")
        rows.append({"Line": u["name"], "Length [m]": L, "Pig speed [m/s]": v_pig, "Run time [h]": t_run / 3600.0,
                     "Liquid inventory [m³]": V_in, "Liquid swept ahead of the pig [m³]": V_sw,
                     "Slug length ahead of the pig [m]": Ls, "Peak liquid arrival rate [m³/h]": rate_peak,
                     "Slug arrival time [h]": dur, "Extra inlet pressure to push the slug [bar]": dP,
                     "Note": "; ".join(note), "uid": uid})
    return rows


# =================================================================================== line pack
PACK_DEFAULTS = {"P_min": 40.0}


def line_pack_table(model, sol, p=None):
    q = dict(PACK_DEFAULTS)
    q.update(p or {})
    rows = []
    fp = sol.fp
    for uid, u, sin, sout in line_units(model, sol):
        if u["type"] == "well":
            continue
        prof = sol.profiles[uid]
        D = _D(u)
        A = math.pi * D * D / 4.0
        L = prof["L"][-1] - prof["L"][0]
        vo = sout.flash.phase("V") or sin.flash.phase("V")
        if L <= 0 or vo is None:
            continue
        HL = sum(prof.get("HL") or [0.0]) / max(len(prof.get("HL") or [1]), 1)
        P = sum(prof["P"]) / len(prof["P"])
        T = sum(prof["T"]) / len(prof["T"]) + K0
        Z = vo.Z
        Vg = A * L * (1.0 - HL)
        n = P * 1e5 * Vg / (Z * R_GAS * T)
        n_min = float(q["P_min"]) * 1e5 * Vg / (Z * R_GAS * T)
        pack = max(n - n_min, 0.0) * STD_VOL / 1e6
        v = sout.flash.phase("V")
        q_std = (sout.F * v.beta * 1000.0 / 3600.0 * STD_VOL * 86400.0 / 1e6) if v is not None else 0.0
        rows.append({"Line": u["name"], "Volume [m³]": A * L, "Liquid holdup [-]": HL, "Mean P [bar(a)]": P,
                     "Mean T [°C]": T - K0, "Gas inventory [MSm³]": n * STD_VOL / 1e6,
                     "Gas inventory at the minimum pressure [MSm³]": n_min * STD_VOL / 1e6,
                     "Line pack available by draw-down [MSm³]": pack,
                     "Hours of the current gas rate [h]": pack / q_std * 24.0 if q_std > 0 else None,
                     "Liquid inventory [m³]": HL * A * L, "uid": uid})
    return rows


# =================================================================================== insulation sizing
INSUL_DEFAULTS = {"target_h": 24.0, "k_ins": 0.0, "t_max": 300.0}


def insulation_table(model, sol, p=None):
    """Insulation thickness for a target no-touch time. The line keeps its present wall and steel; the insulation
    resistance ln((Do+2t)/Do)/(2πk) replaces the present layer and U follows from the total resistance. With
    k = 0 the conductivity is back-calculated so that the present thickness gives the present U."""
    from . import subsea_ops as so
    from .unitops import UnitError
    from . import surf
    q = dict(INSUL_DEFAULTS)
    q.update(p or {})
    basis = so.cool_params(model)
    out = []
    for uid, u in model["units"].items():
        t = u["type"]
        if t not in ("flowline", "riser") or uid not in sol.profiles:
            continue
        from .flowsheet import port_edges
        ins = [s for lst in port_edges(model, uid, "in").values() for s in lst]
        outs = [s for lst in port_edges(model, uid, "out").values() for s in lst]
        if not ins or not outs or ins[0] not in sol.streams or outs[0] not in sol.streams:
            continue
        st_in, st_out = sol.streams[ins[0]], sol.streams[outs[0]]
        if st_in.empty:
            continue
        pp = u["params"]
        cat, key = ("flowline", "design") if t == "flowline" else ("riser", "rtype")
        try:
            row = surf.item(cat, pp.get(key))
        except UnitError:
            row = {}
        U0 = float(pp.get("U", 0.0) or 0.0) or (row.get("U_W_m2K") or 5.0)
        t0 = basis["insul_mm"] if basis["insul_mm"] >= 0 else so.INSUL_MM.get(pp.get(key), 30.0)
        D = float(pp["ID"]) / 1000.0
        Do = D + 2.0 * basis["wall_mm"] / 1000.0
        R0 = 1.0 / (U0 * math.pi * D)                                   # m·K/W of the whole wall now
        k = float(q["k_ins"])
        if k <= 0.0:                                                    # back-calculate from the present design
            k = (math.log((Do + 2.0 * t0 / 1000.0) / Do) / (2.0 * math.pi * R0)) if t0 > 0 else 0.17

        def R_ins(tm):
            return math.log((Do + 2.0 * tm / 1000.0) / Do) / (2.0 * math.pi * k)

        def no_touch(tm):
            Rt = max(R0 - R_ins(t0) + R_ins(tm), 1e-6)
            Ut = 1.0 / (Rt * math.pi * D)
            r = so.cooldown_line(sol.fp, u["name"], sol.profiles[uid], st_in, st_out, D, Ut,
                                 float(pp.get("T_amb", 4.0)), basis, tm)
            return r["No-touch time [h]"], Ut
        target = float(q["target_h"])
        t_now, U_now = no_touch(t0)
        row_out = {"Line": u["name"], "Present insulation [mm]": t0, "Present U [W/m²·K]": U0, "Conductivity used [W/m·K]": k,
                   "No-touch time now [h]": t_now, "Target [h]": target, "uid": uid}
        if t_now == math.inf:
            row_out.update({"Insulation needed [mm]": 0.0, "U needed [W/m²·K]": None,
                            "Note": "no hydrate risk at the sea temperature"})
        elif t_now >= target:
            lo, hi = 0.0, t0
            if no_touch(0.0)[0] >= target:
                need = 0.0
            else:
                for _ in range(40):
                    mid = 0.5 * (lo + hi)
                    if no_touch(mid)[0] >= target:
                        hi = mid
                    else:
                        lo = mid
                need = hi
            row_out.update({"Insulation needed [mm]": need, "U needed [W/m²·K]": no_touch(need)[1],
                            "Note": "the present insulation meets the target" if need <= t0 else ""})
        else:
            tmax = float(q["t_max"])
            if no_touch(tmax)[0] < target:
                row_out.update({"Insulation needed [mm]": None, "U needed [W/m²·K]": None,
                                "Note": f"not reached with {tmax:.0f} mm of k = {k} W/m·K: use pipe-in-pipe or heating"})
            else:
                lo, hi = t0, tmax
                for _ in range(40):
                    mid = 0.5 * (lo + hi)
                    if no_touch(mid)[0] >= target:
                        hi = mid
                    else:
                        lo = mid
                row_out.update({"Insulation needed [mm]": hi, "U needed [W/m²·K]": no_touch(hi)[1], "Note": ""})
        out.append(row_out)
    return out


# =================================================================================== PVT table
def pvt_table(fp, z, P_list, T_list):
    """Property grid [bar(a), °C] of a composition: phase fractions, densities, viscosities, Z, Bg, Cp."""
    from .transport import phase_viscosity_cP
    rows = []
    z = np.asarray(z, float)
    for P in P_list:
        for Tc in T_list:
            T = Tc + K0
            fr = fp.pt_flash(z, T, P)
            v, lo, w = fr.phase("V"), fr.phase("L"), fr.phase("W")
            Mtot = sum(ph.beta * ph.MW for ph in fr.phases)

            def mf(ph):
                return ph.beta * ph.MW / Mtot if ph is not None else 0.0
            r = {"P [bar(a)]": P, "T [°C]": Tc, "Gas mass fraction [-]": mf(v), "Oil mass fraction [-]": mf(lo),
                 "Water mass fraction [-]": mf(w),
                 "Gas density [kg/m³]": v.rho if v is not None else None,
                 "Oil density [kg/m³]": lo.rho if lo is not None else None,
                 "Water density [kg/m³]": w.rho if w is not None else None,
                 "Gas viscosity [cP]": phase_viscosity_cP(fp, v, T) if v is not None else None,
                 "Oil viscosity [cP]": phase_viscosity_cP(fp, lo, T) if lo is not None else None,
                 "Water viscosity [cP]": phase_viscosity_cP(fp, w, T) if w is not None else None,
                 "Gas Z [-]": v.Z if v is not None else None,
                 "Gas Bg [m³/Sm³]": (v.Z * T / P * 1.01325 / 288.15) if v is not None else None,
                 "Mixture Cp [J/mol/K]": sum(ph.beta * ph.Cp for ph in fr.phases),
                 "Mixture enthalpy [J/mol]": fr.H}
            rows.append(r)
    return rows


def pvt_csv(rows):
    if not rows:
        return ""
    keys = list(rows[0])
    lines = [",".join(f'"{k}"' for k in keys)]
    for r in rows:
        lines.append(",".join("" if r[k] is None else f"{r[k]:.8g}" for k in keys))
    return "\n".join(lines) + "\n"


def settings(model):
    """All settings of this module: the defaults overridden by ``model["fa2"]`` (post-processing: never re-solves)."""
    d = dict(CORR_DEFAULTS)
    d.update({"sigma": SIGMA_DEFAULT})
    d.update(PIG_DEFAULTS)
    d.update(PACK_DEFAULTS)
    d.update(INSUL_DEFAULTS)
    d.update({k: v for k, v in (model.get("fa2") or {}).items() if k in d})
    return d
