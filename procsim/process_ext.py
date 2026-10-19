"""Process units added in v7.7 (screening level).

* ``chiller``   - process cooler served by a mechanical refrigeration loop (propane, ethylene, ammonia ...): the duty is the
                  enthalpy change to the outlet temperature, the compressor power follows from a Carnot-fraction COP between the
                  evaporating and condensing temperatures, and the heat rejected at the condenser is duty + power.
* ``membrane``  - gas-separation membrane (CO2 / H2S / H2O removal): cross-flow model in slices with component permeances,
                  feed and permeate pressures; specify the area or let the unit find the area for a CO2 specification.
* ``mol_sieve`` - molecular-sieve dehydration to a ppmv specification: removes the water, sizes the beds from the dynamic
                  capacity and cycle time and estimates the regeneration heat.

All three are screening models: constant permeance (membrane), constant Carnot fraction (chiller), equilibrium-capacity
dynamic loading (sieve)."""
from __future__ import annotations

import math

import numpy as np

from .streams import make_stream, zero_stream, EnergyStream
from .unitops import UnitError, K0, _f, _s, _ph, _one, _energy, _check_P, _heat, _live

GPU = 3.3464e-10            # 1 GPU = 1e-6 cm3(STP)/(cm2 s cmHg) in mol / (m2 s Pa)
MW_W = 18.015

SCHEMA = {
    "chiller": {
        "label": "Chiller (refrigeration loop)", "prefix": "CH", "category": "Heat transfer",
        "ports": {"in": {"in": {"multi": False}}, "out": {"out": {"multi": False}}},
        "params": [
            _f("T_out", "Outlet temperature", "°C", -20.0), _f("dP", "Pressure drop", "bar", 0.3, minv=0.0),
            _s("model", "Refrigeration model", ["Fraction of Carnot COP", "Cycle (PR equation of state)"], "Fraction of Carnot COP",
               help="The cycle model computes the refrigerant flow, compressor power and COP from the refrigerant's own properties"),
            _s("refrigerant", "Refrigerant", ["Propane (R-290)", "Propylene (R-1270)", "Ethylene (R-1150)", "Ammonia (R-717)",
                                              "i-Butane (R-600a)", "Ethane (R-170)", "Other"], "Propane (R-290)",
               help="Used by the cycle model; with the Carnot-fraction model it is only a label"),
            _f("eta_is", "Compressor isentropic efficiency", "%", 75.0, {"model": "Cycle (PR equation of state)"}, minv=40.0, maxv=95.0),
            _f("superheat", "Evaporator superheat", "K", 5.0, {"model": "Cycle (PR equation of state)"}, minv=0.0, maxv=40.0),
            _f("subcool", "Condenser sub-cooling", "K", 0.0, {"model": "Cycle (PR equation of state)"}, minv=0.0, maxv=40.0),
            _s("stages", "Compression", ["Single stage", "Two stages with economiser"], "Single stage",
               show_if={"model": "Cycle (PR equation of state)"}),
            _f("approach", "Evaporator approach to the process outlet", "K", 5.0, minv=1.0, maxv=30.0),
            _f("T_cond", "Condensing temperature", "°C", 40.0, minv=-20.0, maxv=90.0,
               help="About 10-15 K above the cooling-water / air temperature"),
            _f("carnot", "Cycle efficiency (fraction of Carnot COP)", "%", 55.0, {"model": "Fraction of Carnot COP"}, minv=20.0, maxv=90.0,
               help="Propane screw / centrifugal loops are about 50-65 %"),
        ],
    },
    "membrane": {
        "label": "Membrane separator (CO₂ / H₂S removal)", "prefix": "MB", "category": "Separation",
        "ports": {"in": {"feed": {"multi": True}},
                  "out": {"retentate": {"multi": False}, "permeate": {"multi": False}}},
        "params": [
            _s("spec", "Specification", ["Membrane area", "CO2 in the retentate"], "Membrane area"),
            _f("area", "Membrane area", "m²", 5000.0, {"spec": "Membrane area"}, minv=1.0),
            _f("co2_spec", "CO₂ in the retentate", "mol%", 2.5, {"spec": "CO2 in the retentate"}, minv=0.0001, maxv=50.0),
            _f("perm_CO2", "CO₂ permeance", "GPU", 100.0, minv=0.1, help="Polymeric (cellulose acetate / polyimide) membranes: 50-200 GPU"),
            _f("sel_CH4", "CO₂ / CH₄ selectivity", "-", 20.0, minv=1.0, help="Cellulose acetate ~ 15-25, polyimide ~ 30-50"),
            _f("sel_H2S", "H₂S permeance relative to CO₂", "-", 1.5, minv=0.0),
            _f("sel_H2O", "H₂O permeance relative to CO₂", "-", 6.0, minv=0.0),
            _f("heavy", "C₂+ and N₂ permeance relative to CH₄", "-", 0.8, minv=0.0, help="Heavier hydrocarbons can permeate faster than methane"),
            _f("P_perm", "Permeate pressure", "bar(a)", 2.0, minv=0.1),
            _f("dP", "Feed-side pressure drop", "bar", 1.0, minv=0.0),
            _f("slices", "Cross-flow slices", "-", 30.0, minv=5.0, maxv=200.0),
            _s("config", "Configuration", ["Single stage", "Two stages: permeate recompressed, 2nd retentate recycled"], "Single stage",
               help="Stage 1 retentate is the product. Its permeate is recompressed to the feed pressure and fed to stage 2; the stage 2 "
                    "retentate (rich in methane) returns to the inlet, the stage 2 permeate leaves as the CO₂-rich stream. "
                    "Cuts the hydrocarbon loss at the price of the recompressor"),
            _f("area2_ratio", "Stage 2 area / stage 1 area", "-", 0.4, {"config": "Two stages: permeate recompressed, 2nd retentate recycled"}, minv=0.02, maxv=3.0),
            _f("comp_eff", "Recompressor isentropic efficiency", "%", 75.0, {"config": "Two stages: permeate recompressed, 2nd retentate recycled"}, minv=40.0, maxv=95.0),
        ],
    },
    "mol_sieve": {
        "label": "Molecular-sieve dehydration", "prefix": "MS", "category": "Separation",
        "ports": {"in": {"feed": {"multi": True}}, "out": {"dry": {"multi": False}, "water": {"multi": False}}},
        "params": [
            _f("ppmv", "Water in the dried gas", "ppmv", 1.0, minv=0.01, maxv=500.0, help="Cryogenic (LNG / NGL) service: 0.1-1 ppmv"),
            _f("cycle_h", "Adsorption cycle time", "h", 8.0, minv=2.0, maxv=48.0),
            _f("capacity", "Dynamic loading", "kg water / 100 kg sieve", 8.0, minv=2.0, maxv=18.0,
               help="Design loading after ageing and the mass-transfer zone: about 8-10 % for 4A / 3A beads"),
            _f("bulk", "Sieve bulk density", "kg/m³", 700.0, minv=500.0, maxv=900.0),
            _f("regen_frac", "Regeneration gas", "% of feed", 10.0, minv=2.0, maxv=30.0),
            _f("regen_T", "Regeneration temperature", "°C", 260.0, minv=150.0, maxv=320.0),
            _f("dP", "Pressure drop", "bar", 0.7, minv=0.0),
        ],
    },
}


# ------------------------------------------------------------------------------------------------ chiller
def calc_chiller(unit, ins, fp):
    p = unit["params"]
    u2 = dict(unit)
    u2["params"] = {"spec": "Outlet temperature", "T_out": p["T_out"], "dP": p.get("dP", 0.0)}
    outs, res, en = _heat(u2, ins, fp, True)
    Q = res.get("Duty removed [kW]", 0.0) or 0.0
    if not Q:
        return outs, res, en
    Te = float(p["T_out"]) - float(p.get("approach", 5.0)) + K0
    Tc = float(p["T_cond"]) + K0
    if Tc <= Te + 1.0:
        raise UnitError(f"{unit['name']}: condensing temperature {p['T_cond']} °C must be well above the evaporating "
                        f"temperature {Te - K0:.1f} °C")
    cop_c = Te / (Tc - Te)
    cyc = None
    if p.get("model") == "Cycle (PR equation of state)":
        from . import refrigeration as RF
        try:
            cyc = RF.cycle(p.get("refrigerant"), Te - K0, float(p["T_cond"]), float(p["eta_is"]) / 100.0, float(p["superheat"]),
                           float(p["subcool"]), 2 if str(p.get("stages", "")).startswith("Two") else 1, Q)
        except RF.RefrigError as e:
            raise UnitError(f"{unit['name']}: {e}")
        cop = cyc["COP"]
        W = cyc["W_kW"]
    else:
        cop = cop_c * float(p["carnot"]) / 100.0
        W = Q / cop
    if cyc:
        res.update({"Refrigerant": cyc["refrigerant"], "Evaporating P [bar(a)]": cyc["Pe"], "Condensing P [bar(a)]": cyc["Pc"],
                    "Pressure ratio [-]": cyc["pressure_ratio"], "Compressor discharge T [°C]": cyc["T2"],
                    "Refrigerant circulation [kg/h]": cyc["m_ref_kg_h"], "Fraction of Carnot COP [%]": 100.0 * cyc["carnot_fraction"]})
        if cyc.get("Pm"):
            res["Intermediate P [bar(a)]"] = cyc["Pm"]
        if cyc.get("warning"):
            res["Warning"] = cyc["warning"]
    res.update({"Evaporating T [°C]": Te - K0, "Condensing T [°C]": float(p["T_cond"]), "Carnot COP [-]": cop_c, "COP [-]": cop,
                "Refrigeration power [kW]": W, "Condenser heat rejected [kW]": Q + W})
    return outs, res, [en[0], EnergyStream(f"W-{unit['name']} refrigeration", W, f"{unit['name']} refrigeration compressor", "work")]


# ------------------------------------------------------------------------------------------------ membrane
def _perm_table(fp, p):
    """Permeance of every component in mol / (m2 s Pa)."""
    q_co2 = float(p["perm_CO2"]) * GPU
    q_ch4 = q_co2 / max(float(p["sel_CH4"]), 1.0)
    out = np.zeros(fp.n)
    for i, k in enumerate(fp.keys):
        if k == "CO2":
            out[i] = q_co2
        elif k == "H2S":
            out[i] = q_co2 * float(p["sel_H2S"])
        elif k == "H2O":
            out[i] = q_co2 * float(p["sel_H2O"])
        elif k == "C1":
            out[i] = q_ch4
        elif k in ("He", "H2"):
            out[i] = q_co2 * 2.0
        else:
            out[i] = q_ch4 * float(p["heavy"])
    return out


def _membrane_run(n_feed, area, q, Pf, Pp, nsl):
    """Cross-flow integration. Returns (retentate kmol/h vector, permeate kmol/h vector)."""
    n = n_feed.copy()
    perm = np.zeros_like(n)
    dA = area / nsl
    for _ in range(int(nsl)):
        tot = n.sum()
        if tot <= 1e-12:
            break
        x = n / tot
        y = x.copy()
        for _it in range(25):
            J = q * np.maximum(Pf * x - Pp * y, 0.0)            # mol / (m2 s)
            sJ = J.sum()
            if sJ <= 0:
                break
            y_new = J / sJ
            if np.max(np.abs(y_new - y)) < 1e-9:
                y = y_new
                break
            y = 0.5 * y + 0.5 * y_new
        flux = q * np.maximum(Pf * x - Pp * y, 0.0) * dA * 3.6    # kmol/h through this slice
        flux = np.minimum(flux, n * 0.999)
        n = n - flux
        perm = perm + flux
    return n, perm


def _two_stage(n_gas, a1, a2, q, Pf, Pp, nsl, tol=1e-9, itmax=200):
    """Stage 1 -> permeate recompressed -> stage 2; the stage 2 retentate recycles to the stage 1 inlet (fresh feed n_gas, kmol/h).
    Returns (product retentate, final permeate, recompressed flow, recycle flow) in kmol/h."""
    r = np.zeros_like(n_gas)
    for _ in range(itmax):
        feed1 = n_gas + r
        ret1, perm1 = _membrane_run(feed1, a1, q, Pf, Pp, nsl)
        ret2, perm2 = _membrane_run(perm1, a2, q, Pf, Pp, nsl)
        new = ret2
        err = float(np.max(np.abs(new - r))) / max(float(n_gas.sum()), 1e-12)
        r = 0.5 * r + 0.5 * new if _ < 3 else new
        if err < tol:
            break
    return ret1, perm2, perm1, r


def calc_membrane(unit, ins, fp):
    p = unit["params"]
    live = _live(ins.get("feed") or [])
    if not live:
        z = (ins.get("feed") or [None])[0].z if ins.get("feed") else None
        return ({"retentate": [zero_stream("", fp, z)], "permeate": [zero_stream("", fp, z)]}, {"Status": "No flow"}, [])
    if "CO2" not in fp.keys:
        raise UnitError(f"{unit['name']}: the fluid package has no CO2")
    F = sum(s.F for s in live)
    z = sum(s.F * s.z for s in live) / F
    H = sum(s.F * s.H for s in live) / F
    Pf = min(s.P for s in live) - float(p.get("dP", 0.0))
    Pp = float(p["P_perm"])
    _check_P(Pf, unit["name"])
    if Pp >= Pf:
        raise UnitError(f"{unit['name']}: permeate pressure {Pp} bar must be below the feed-side pressure {Pf:.1f} bar")
    fr = _ph(fp, z, Pf, H, live[0].T)
    T = fr.T
    v = fr.phase("V")
    if v is None:
        raise UnitError(f"{unit['name']}: no gas at the membrane inlet")
    Fv = F * v.beta
    n_gas = Fv * v.x
    n_liq = np.maximum(F * z - n_gas, 0.0)
    q = _perm_table(fp, p)
    nsl = int(p.get("slices", 30))
    ic = fp.keys.index("CO2")
    area = float(p["area"])
    two = str(p.get("config", "")).startswith("Two")
    r2 = float(p.get("area2_ratio", 0.4))

    def run_cfg(a):
        if two:
            ret1, perm2, perm1, rec = _two_stage(n_gas, a, a * r2, q, Pf * 1e5, Pp * 1e5, nsl)
            return ret1, perm2, perm1, rec
        r_, pm_ = _membrane_run(n_gas, a, q, Pf * 1e5, Pp * 1e5, nsl)
        return r_, pm_, None, None

    if p.get("spec") == "CO2 in the retentate":
        target = float(p["co2_spec"]) / 100.0

        def co2_ret(a):
            r, *_ = run_cfg(a)
            return r[ic] / max(r.sum(), 1e-12)
        if co2_ret(1e-3) <= target:
            area = 0.0
        else:
            lo, hi = 1.0, 1e7
            if co2_ret(hi) > target:
                raise UnitError(f"{unit['name']}: the CO2 specification cannot be reached by area at this pressure ratio "
                                f"(selectivity / pressure ratio limit) - raise the feed pressure or lower the permeate pressure")
            for _ in range(60 if not two else 40):
                mid = math.sqrt(lo * hi)
                if co2_ret(mid) > target:
                    lo = mid
                else:
                    hi = mid
            area = hi
    ret_g, perm, perm1, rec = run_cfg(area)
    ret = ret_g + n_liq
    Fr, Fp = float(ret.sum()), float(perm.sum())
    outs = {}
    Hin = F * H
    if Fp > 1e-12:
        zp = perm / Fp
        frp = fp.pt_flash(zp, T, Pp)
        sp = make_stream("", fp, Fp, zp, frp)
    else:
        sp = zero_stream("", fp, z, T, Pp)
    zr = ret / max(Fr, 1e-12)
    frr = fp.pt_flash(zr, T, Pf)
    sr = make_stream("", fp, Fr, zr, frr)
    outs = {"retentate": [sr], "permeate": [sp]}
    Q = (Fr * frr.H + (Fp * sp.H if Fp > 1e-12 else 0.0) - Hin) / 3600.0       # kW to hold the isothermal outlets
    ich4 = fp.keys.index("C1") if "C1" in fp.keys else None
    co2_in, co2_out = n_gas[ic] / max(n_gas.sum(), 1e-12), ret_g[ic] / max(ret_g.sum(), 1e-12)
    res = {"Membrane area [m²]": area, "Feed-side P [bar(a)]": Pf, "Permeate P [bar(a)]": Pp, "T [°C]": T - K0,
           "Stage cut (permeate / feed gas) [%]": 100.0 * Fp / max(float(n_gas.sum()), 1e-12),
           "CO₂ in the feed [mol%]": 100.0 * co2_in, "CO₂ in the retentate [mol%]": 100.0 * co2_out,
           "CO₂ in the permeate [mol%]": 100.0 * perm[ic] / max(Fp, 1e-12) if Fp > 0 else 0.0,
           "CO₂ removal [%]": 100.0 * (1.0 - ret_g[ic] / max(n_gas[ic], 1e-12)),
           "Permeate flow [kmol/h]": Fp, "Retentate flow [kmol/h]": Fr,
           "Isothermal heat duty [kW]": Q}
    if ich4 is not None:
        res["Methane loss to the permeate [%]"] = 100.0 * perm[ich4] / max(n_gas[ich4], 1e-12)
    extra_en = []
    if two:
        n2 = float(perm1.sum())                                            # kmol/h recompressed
        gam, eta = 1.3, float(p.get("comp_eff", 75.0)) / 100.0
        W = n2 * 1000.0 * 8.314 * T / 3600.0 * gam / (gam - 1.0) * ((Pf / Pp) ** ((gam - 1.0) / gam) - 1.0) / eta / 1000.0
        res.update({"Stage 1 area [m²]": area, "Stage 2 area [m²]": area * r2, "Permeate recompressed [kmol/h]": n2,
                    "Recycle to the inlet [kmol/h]": float(rec.sum()), "Recompressor power [kW]": W,
                    "Total membrane area [m²]": area * (1.0 + r2)})
        if W > 0:
            extra_en.append(EnergyStream(f"W-{unit['name']} recompressor", W, f"{unit['name']} recompressor", "work"))
    if Pf / Pp < float(p["sel_CH4"]):
        res["Note"] = (f"pressure ratio {Pf / Pp:.1f} is below the selectivity {p['sel_CH4']:.0f}: the pressure ratio limits "
                       "the separation, not the membrane")
    return outs, res, ([_energy(unit, Q)] if abs(Q) > 1e-6 else []) + extra_en


# ------------------------------------------------------------------------------------------------ molecular sieve
def calc_mol_sieve(unit, ins, fp):
    p = unit["params"]
    live = _live(ins.get("feed") or [])
    if not live:
        z = (ins.get("feed") or [None])[0].z if ins.get("feed") else None
        return {"dry": [zero_stream("", fp, z)], "water": [zero_stream("", fp, z)]}, {"Status": "No flow"}, []
    if fp.iw < 0:
        raise UnitError(f"{unit['name']}: the fluid package has no water")
    F = sum(s.F for s in live)
    z = sum(s.F * s.z for s in live) / F
    H = sum(s.F * s.H for s in live) / F
    P = min(s.P for s in live) - float(p.get("dP", 0.0))
    _check_P(P, unit["name"])
    fr = _ph(fp, z, P, H, live[0].T)
    T = fr.T
    v = fr.phase("V")
    if v is None:
        raise UnitError(f"{unit['name']}: no gas to dry")
    iw = fp.iw
    Fv = F * v.beta
    n_gas = Fv * v.x
    free = np.maximum(F * z - n_gas, 0.0)
    y_in = float(v.x[iw])
    y_spec = float(p["ppmv"]) * 1e-6
    y_out = min(y_in, y_spec)
    n_rem = Fv * (y_in - y_out) / max(1.0 - y_out, 1e-12)
    dry_n = n_gas.copy()
    dry_n[iw] -= n_rem
    dry_n = np.maximum(dry_n, 0.0)
    wat_n = free.copy()
    wat_n[iw] += n_rem
    Fd, Fw = float(dry_n.sum()), float(wat_n.sum())
    zd = dry_n / Fd
    frd = fp.pt_flash(zd, T, P)
    outs = {"dry": [make_stream("", fp, Fd, zd, frd)]}
    Hw_tot = 0.0
    if Fw > 1e-12:
        zw = wat_n / Fw
        frw = fp.pt_flash(zw, T, P)                                # the water leaves at the bed temperature
        outs["water"] = [make_stream("", fp, Fw, zw, frw)]
        Hw_tot = Fw * frw.H
    else:
        outs["water"] = [zero_stream("", fp, z, T, P)]
    Q_ads = (Fd * frd.H + Hw_tot - F * H) / 3600.0                # kW: adsorption heat leaves with the regeneration gas
    W_h = n_rem * MW_W                                             # kg/h of water adsorbed
    cyc = float(p["cycle_h"])
    m_sieve = W_h * cyc / (float(p["capacity"]) / 100.0)           # kg of sieve per bed on adsorption
    V_bed = m_sieve / float(p["bulk"])
    m_regen = float(p["regen_frac"]) / 100.0 * Fd * float(fp.MW @ zd)   # kg/h of regeneration gas
    cp_sieve, cp_steel = 0.95, 0.5                                 # kJ/kg/K, rough
    dT = float(p["regen_T"]) - (T - K0)
    # heat to desorb (1.4 x latent heat) and to heat sieve and vessel over the regeneration (about 45 % of the cycle)
    Q_des = W_h * cyc * 2260.0 * 1.4
    Q_heat = m_sieve * cp_sieve * dT + 0.6 * m_sieve * cp_steel * dT
    Q_kW = (Q_des + Q_heat) / (0.45 * cyc * 3600.0)
    res = {"Vessel P [bar(a)]": P, "Vessel T [°C]": T - K0, "Water in [ppmv]": y_in * 1e6, "Water out [ppmv]": y_out * 1e6,
           "Water removed [kg/h]": W_h, "Sieve mass per bed [kg]": m_sieve, "Bed volume [m³]": V_bed,
           "Beds (adsorbing + regenerating)": 2, "Regeneration gas [kg/h]": m_regen,
           "Regeneration heater duty [kW]": Q_kW, "Heat of adsorption removed [kW]": -Q_ads}
    if y_in <= y_spec:
        res["Note"] = "feed already meets the specification"
    en = [EnergyStream(f"Q-{unit['name']} regeneration", Q_kW, f"{unit['name']} regeneration heater", "heat")]
    if abs(Q_ads) > 1e-6:
        en.append(_energy(unit, Q_ads))
    return outs, res, en


def register():
    from .unitops import CATALOGUE, CALC
    CATALOGUE.update(SCHEMA)
    CALC.update({"chiller": calc_chiller, "membrane": calc_membrane, "mol_sieve": calc_mol_sieve})


register()
