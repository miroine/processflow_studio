"""Subsea field-development screening on a solved flowsheet (SURF Phase 2).

* ``equipment_list``   - every subsea item with quantity and cost from the catalogue, rolled up into a CAPEX
                          estimate (equipment, installation, wells, umbilical/power, engineering, contingency)
* ``umbilical_design`` - chemical-injection tube sizing (friction vs. allowable ΔP, velocity, topside pump
                          pressure) and the power cable for subsea boosters (voltage, conductor, voltage drop,
                          charging current)
* ``tieback_screen``   - arrival pressure / temperature / hydrate margin of the flowline + riser over a grid of
                          tie-back distances and rates, and the maximum distance for a minimum arrival pressure

Costs, prices and cable data are generic, illustrative placeholders, stored with the flowsheet
(``model["capex"]``, ``model["umbilical"]``) and editable in the app.  Screening accuracy only (class 5).
"""
from __future__ import annotations

import copy
import math

from . import surf
from .streams import make_stream
from .unitops import UnitError, K0, G, churchill_f
from .thermo import FlashError

# ------------------------------------------------------------------------------------------------ CAPEX

CAPEX_DEFAULTS = {
    "well_MUSD": 70.0,           # drilling & completion per well
    "install_pct": 60.0,         # installation as % of subsea equipment cost
    "umb_MUSD_km": 1.2,          # static umbilical per km
    "cable_MUSD_km": 1.0,        # power cable per km (only with boosters)
    "topside_power_MUSD_MW": 4.0,  # topside VSDs / transformers per MW of subsea power
    "heating_MUSD_km": 1.5,      # flowline heating system per heated km
    "transformer_MUSD_MVA": 2.5,  # subsea step-down transformer per MVA
    "eng_pct": 12.0,             # engineering, procurement management
    "cont_pct": 25.0,            # contingency
    "ref_ID_mm": 254.0,          # the catalogue's per-km line costs are for this bore
    "size_exp": 0.7,             # line cost ~ (ID / ref ID)^exp
}
CAPEX_LABELS = {
    "well_MUSD": ("Drilling & completion per well", "MUSD"), "install_pct": ("Installation", "% of equipment"),
    "umb_MUSD_km": ("Umbilical", "MUSD/km"), "cable_MUSD_km": ("Power cable", "MUSD/km"),
    "topside_power_MUSD_MW": ("Topside power (VSD, transformer)", "MUSD/MW"),
    "heating_MUSD_km": ("Flowline heating system", "MUSD/km"),
    "transformer_MUSD_MVA": ("Subsea step-down transformer", "MUSD/MVA"),
    "eng_pct": ("Engineering & management", "%"), "cont_pct": ("Contingency", "%"),
    "ref_ID_mm": ("Reference bore of line costs", "mm"), "size_exp": ("Line cost size exponent", "-"),
}
GROUPS = ("Wells", "Subsea equipment", "Subsea processing", "Subsea boosting", "Pipelines & risers", "Flowline heating",
          "Umbilical & power")


def capex_params(model):
    p = dict(CAPEX_DEFAULTS)
    p.update({k: float(v) for k, v in (model.get("capex") or {}).items() if k in CAPEX_DEFAULTS})
    return p


def _riser_length(u, res):
    if res and res.get("Riser length [m]"):
        return float(res["Riser length [m]"])
    try:
        row = surf.item("riser", u["params"].get("rtype"))
    except UnitError:
        return float(u["params"].get("depth", 0.0))
    lf = float(u["params"].get("lf", 0.0) or 0.0) or (row.get("length_factor") or 1.0)
    return float(u["params"].get("depth", 0.0)) * max(lf, 1.0)


def line_lengths_km(model, sol=None):
    """(flowline km, riser km) summed over the flowsheet."""
    fl = rs = 0.0
    for uid, u in model["units"].items():
        if u["type"] == "flowline":
            fl += surf.flowline_length(u["params"]) / 1000.0
        elif u["type"] == "riser":
            rs += _riser_length(u, sol.results.get(uid) if sol else None) / 1000.0
    return fl, rs


def booster_power_kW(model, sol):
    """(shaft kW, electrical kW) of all subsea boosters in the solution."""
    sh = el = 0.0
    if sol is None:
        return sh, el
    for uid, u in model["units"].items():
        if u["type"] in surf.BOOSTER_TYPES:
            r = sol.results.get(uid) or {}
            sh += float(r.get("Shaft power [kW]") or 0.0)
            el += float(r.get("Electrical power [kW]") or 0.0)
    return sh, el


def heating_power_kW(model, sol):
    """Electrical power drawn by flowline heating (DEH, heat-traced PiP) in the solution."""
    if sol is None:
        return 0.0
    return sum(float((sol.results.get(uid) or {}).get("Electrical heating power [kW]") or 0.0)
               for uid, u in model["units"].items() if u["type"] == "flowline")


def upstream_wells(model, uid):
    """Wells (identical-well count of a well unit) directly feeding a unit, at least 1."""
    from .flowsheet import port_edges
    n = 0
    for lst in port_edges(model, uid, "in").values():
        for sid in lst:
            src = model["units"].get(model["streams"][sid]["src"][0])
            if src and src["type"] == "well":
                n += surf.n_wells(src)
    return max(n, 1)


def equipment_list(model, sol=None):
    """(items, totals). Items: dicts with Item, Type, Group, Qty, Unit, Unit cost [MUSD], Equipment [MUSD],
    Installation [MUSD], Total [MUSD], Basis. Costs from the active catalogue (``cost_MUSD``)."""
    cp = capex_params(model)
    ref, ex = cp["ref_ID_mm"], cp["size_exp"]
    inst = cp["install_pct"] / 100.0
    items = []

    def add(name, typ, group, qty, unit, unit_cost, basis, install=True):
        eq = qty * (unit_cost or 0.0)
        ins = eq * inst if install else 0.0
        items.append({"Item": name, "Type": typ, "Group": group, "Qty": qty, "Unit": unit,
                      "Unit cost [MUSD]": unit_cost, "Equipment [MUSD]": eq, "Installation [MUSD]": ins,
                      "Total [MUSD]": eq + ins, "Basis": basis})

    cat_of = {"xmas_tree": ("tree", "tree"), "template": ("template", "template"), "jumper": ("jumper", "kind"),
              "subsea_valve": ("valve", "kind"), "subsea_booster": ("booster", "btype"),
              "subsea_pump": ("booster", "btype"), "subsea_compressor": ("booster", "btype"),
              "subsea_separator": ("separator", "sep_type"),
              "flowline": ("flowline", "design"), "riser": ("riser", "rtype")}
    fixed = {"subsea_cooler": "Passive subsea cooler", "intensifier": "Pressure intensifier",
             "cimv": "Chemical injection metering valve"}
    for uid, u in sorted(model["units"].items(), key=lambda kv: (surf.SURF_TYPES.index(kv[1]["type"])
                                                                 if kv[1]["type"] in surf.SURF_TYPES else 99,
                                                                 kv[1]["name"])):
        t = u["type"]
        if t not in surf.SURF_TYPES:
            continue
        if t in ("well", "injection_well"):
            add(u["name"], "Well" if t == "well" else "Water injection well", "Wells", surf.n_wells(u), "well",
                cp["well_MUSD"], "Drilling & completion allowance", False)
            continue
        cat, name_ = ("process", fixed[t]) if t in fixed else (cat_of[t][0], u["params"].get(cat_of[t][1]))
        try:
            row = surf.item(cat, name_)
        except UnitError:
            row = {"item": str(name_), "cost_MUSD": None}
        c = row.get("cost_MUSD")
        basis = "Catalogue" if c is not None else "No cost in the catalogue"
        if t in ("flowline", "riser"):
            ID = float(u["params"].get("ID", ref))
            L = (surf.flowline_length(u["params"]) if t == "flowline"
                 else _riser_length(u, sol.results.get(uid) if sol else None)) / 1000.0
            uc = None if c is None else c * (ID / ref) ** ex
            add(u["name"], row["item"], "Pipelines & risers", L, "km", uc,
                basis + (f", scaled to {ID:.0f} mm bore" if c is not None else ""))
            system = surf.heating_system(u["params"])[0] if t == "flowline" else surf.HEAT_NONE
            if system != surf.HEAT_NONE:
                add(f"{u['name']} heating", system, "Flowline heating", L, "km", cp["heating_MUSD_km"],
                    "Allowance per km (cables, power supply, monitoring)")
        else:
            qty = upstream_wells(model, uid) if t == "xmas_tree" else 1      # one tree per well of a cluster
            add(u["name"], row["item"], "Subsea boosting" if t in surf.BOOSTER_TYPES
                else ("Subsea processing" if t in ("subsea_separator", "subsea_cooler") else "Subsea equipment"), qty,
                "ea", c, basis)
    units = model["units"].values()
    if any(u["type"] in surf.SURF_TYPES for u in units):
        um = umbilical_params(model)
        L = um["length_km"] or sum(line_lengths_km(model, sol))
        add("Main umbilical", "Static umbilical (chemicals, hydraulics, signal)", "Umbilical & power", L, "km",
            cp["umb_MUSD_km"], "Allowance per km")
        _, el = booster_power_kW(model, sol)
        el_heat = heating_power_kW(model, sol)
        if any(u["type"] in surf.BOOSTER_TYPES for u in units) or el_heat > 0:
            add("Power cable", "Subsea power cable (boosters, electrical heating)", "Umbilical & power", L, "km",
                cp["cable_MUSD_km"], "Allowance per km")
        if el > 0:
            try:
                cab = umbilical_design(model, sol).get("cable") or {}
            except Exception:                                      # noqa: BLE001
                cab = {}
            if cab.get("Subsea step-down transformer [MVA]"):
                add("Subsea transformer", "Subsea step-down transformer", "Umbilical & power",
                    cab["Subsea step-down transformer [MVA]"], "MVA", cp["transformer_MUSD_MVA"], "Allowance per MVA")
        if el + el_heat > 0:
            add("Topside power", "VSDs and transformers (boosters, electrical heating)", "Umbilical & power",
                (el + el_heat) / 1000.0, "MW", cp["topside_power_MUSD_MW"],
                "Allowance per MW of subsea electrical power", False)
    tot = {g: sum(i["Total [MUSD]"] for i in items if i["Group"] == g) for g in GROUPS}
    equip = sum(i["Equipment [MUSD]"] for i in items if i["Group"] != "Wells")
    install = sum(i["Installation [MUSD]"] for i in items)
    wells = tot["Wells"]
    base = equip + install
    eng = base * cp["eng_pct"] / 100.0
    cont = (base + eng + wells) * cp["cont_pct"] / 100.0
    totals = {"Subsea equipment & lines [MUSD]": equip, "Installation [MUSD]": install, "Wells [MUSD]": wells,
              "Engineering & management [MUSD]": eng, "Contingency [MUSD]": cont,
              "Total CAPEX [MUSD]": base + eng + wells + cont, "by_group": tot,
              "missing_costs": [i["Item"] for i in items if i["Unit cost [MUSD]"] is None]}
    return items, totals


# ----------------------------------------------------------------------------------- umbilical & power

FLUIDS = {   # density kg/m3, viscosity cP at seabed temperature (generic values)
    "MEG (90 wt%)": (1110.0, 30.0), "Methanol": (800.0, 0.9), "Scale inhibitor": (1100.0, 5.0),
    "Corrosion inhibitor": (950.0, 10.0), "Wax / asphaltene inhibitor": (900.0, 15.0),
}
TUBES_MM = (("1/4 in", 6.35), ("3/8 in", 9.53), ("1/2 in", 12.7), ("3/4 in", 19.05), ("1 in", 25.4),
            ("1 1/2 in", 38.1), ("2 in", 50.8))
CABLES = ((35, 170), (50, 205), (70, 250), (95, 300), (120, 340), (150, 380), (185, 430), (240, 500), (300, 560),
          (400, 640))                     # conductor mm², indicative ampacity A
VOLTAGES_KV = (6.6, 11.0, 22.0, 33.0)
UMB_DEFAULTS = {"length_km": 0.0, "depth_m": 0.0, "dP_allow": 100.0, "v_max": 3.0, "tube_rating": 690.0,
                "pump_eff": 70.0, "cos_phi": 0.9, "dV_max": 8.0, "freq": 50.0, "C_uF_km": 0.3, "X_ohm_km": 0.12,
                "T_cond": 90.0}
DEFAULT_SERVICES = [
    {"Service": "MEG injection", "Fluid": "MEG (90 wt%)", "Flow [L/h]": 2000.0, "Delivery P [bar(a)]": 0.0},
    {"Service": "Methanol (start-up)", "Fluid": "Methanol", "Flow [L/h]": 300.0, "Delivery P [bar(a)]": 0.0},
    {"Service": "Scale inhibitor", "Fluid": "Scale inhibitor", "Flow [L/h]": 20.0, "Delivery P [bar(a)]": 0.0},
    {"Service": "Corrosion inhibitor", "Fluid": "Corrosion inhibitor", "Flow [L/h]": 10.0, "Delivery P [bar(a)]": 0.0},
]


def umbilical_params(model):
    p = dict(UMB_DEFAULTS)
    p.update({k: float(v) for k, v in (model.get("umbilical") or {}).items() if k in UMB_DEFAULTS})
    return p


def services(model):
    s = (model.get("umbilical") or {}).get("services")
    return copy.deepcopy(s) if s else copy.deepcopy(DEFAULT_SERVICES)


def _max_depth(model):
    d = [float(u["params"].get("depth", 0.0)) for u in model["units"].values() if u["type"] == "riser"]
    return max(d) if d else 300.0


def _max_wellhead_P(model, sol):
    if sol is None:
        return None
    ps = [r.get("Wellhead P [bar(a)]") for uid, r in sol.results.items()
          if uid in model["units"] and model["units"][uid]["type"] == "well"]
    ps = [x for x in ps if x is not None]
    return max(ps) if ps else None


def tube_dp(flow_L_h, D_m, L_m, rho, mu_cP):
    """Friction ΔP [bar], velocity [m/s] and Reynolds number in a tube (Churchill, smooth tube)."""
    q = flow_L_h / 1000.0 / 3600.0
    A = math.pi * D_m * D_m / 4.0
    v = q / A
    Re = rho * v * D_m / (mu_cP * 1e-3)
    f = churchill_f(max(Re, 1e-6), 1.5e-6 / D_m)
    return f * L_m / D_m * rho * v * v / 2.0 / 1e5, v, Re


def cable_design(P_kW, L_km, up):
    """Smallest (voltage, conductor) meeting ampacity, voltage drop and a charging-current limit.
    Returns a dict (or with 'ok' False and the best attempt)."""
    if P_kW <= 0:
        return None
    rho20 = 0.01724                                       # Ω·mm²/m copper
    rho = rho20 * (1 + 0.00393 * (up["T_cond"] - 20.0))
    cphi = up["cos_phi"]
    sphi = math.sqrt(max(1 - cphi * cphi, 0.0))
    best = None
    for kv in VOLTAGES_KV:
        V = kv * 1000.0
        I = P_kW * 1000.0 / (math.sqrt(3) * V * cphi)
        Ic = V / math.sqrt(3) * 2 * math.pi * up["freq"] * up["C_uF_km"] * 1e-6 * L_km
        for A, amp in CABLES:
            R = rho * L_km * 1000.0 / A                   # Ω per conductor
            X = up["X_ohm_km"] * L_km
            dV = math.sqrt(3) * I * (R * cphi + X * sphi) / V * 100.0
            cand = {"Voltage [kV]": kv, "Conductor [mm²]": A, "Current [A]": I, "Ampacity [A]": amp,
                    "Voltage drop [%]": dV, "Charging current [A]": Ic, "Cable losses [kW]": 3 * I * I * R / 1000.0,
                    "ok": I <= amp and dV <= up["dV_max"] and Ic <= 0.5 * amp}
            if cand["ok"]:
                return cand
            if best is None or dV < best["Voltage drop [%]"]:
                best = cand
    return best


def umbilical_design(model, sol=None):
    """Tube sizing for every injection service and the booster power cable."""
    up = umbilical_params(model)
    L_km = up["length_km"] or sum(line_lengths_km(model, sol))
    L = L_km * 1000.0
    depth = up["depth_m"] or _max_depth(model)
    whp = _max_wellhead_P(model, sol)
    rows, notes = [], []
    for svc in services(model):
        fluid = svc.get("Fluid") or "MEG (90 wt%)"
        rho, mu = FLUIDS.get(fluid, FLUIDS["MEG (90 wt%)"])
        flow = float(svc.get("Flow [L/h]") or 0.0)
        Pdel = float(svc.get("Delivery P [bar(a)]") or 0.0)
        auto = Pdel <= 0
        if auto:
            Pdel = (whp + 10.0) if whp else 200.0
        row = {"Service": svc.get("Service") or fluid, "Fluid": fluid, "Flow [L/h]": flow,
               "Delivery P [bar(a)]": Pdel, "Delivery P basis": "max wellhead P + 10 bar" if (auto and whp) else
               ("default 200 bar" if auto else "specified")}
        if flow <= 0 or L <= 0:
            rows.append(dict(row, **{"Tube": "—", "Tube ID [mm]": None, "Velocity [m/s]": None,
                                     "Friction ΔP [bar]": None, "Topside pump P [bar(a)]": None,
                                     "Pump power [kW]": None, "Status": "no flow or no length"}))
            continue
        chosen = None
        for name, d in TUBES_MM:
            dp, v, Re = tube_dp(flow, d / 1000.0, L, rho, mu)
            if dp <= up["dP_allow"] and v <= up["v_max"]:
                chosen = (name, d, dp, v, Re)
                break
        status = "ok"
        if chosen is None:
            name, d = TUBES_MM[-1]
            dp, v, Re = tube_dp(flow, d / 1000.0, L, rho, mu)
            chosen = (name, d, dp, v, Re)
            status = "largest tube exceeds the allowable ΔP: split the service or allow more ΔP"
        name, d, dp, v, Re = chosen
        head = rho * G * depth / 1e5
        Ptop = Pdel + dp - head
        if Ptop > up["tube_rating"]:
            status = f"topside pressure above the {up['tube_rating']:.0f} bar tube rating"
        power = flow / 1000.0 / 3600.0 * max(Ptop - 1.0, 0.0) * 1e5 / (up["pump_eff"] / 100.0) / 1000.0
        rows.append(dict(row, **{"Tube": name, "Tube ID [mm]": d, "Velocity [m/s]": v, "Reynolds [-]": Re,
                                 "Friction ΔP [bar]": dp, "Hydrostatic head [bar]": head,
                                 "Topside pump P [bar(a)]": Ptop, "Pump power [kW]": power, "Status": status}))
    _, el_b = booster_power_kW(model, sol)
    el_h = heating_power_kW(model, sol)
    el = el_b + el_h
    cable = cable_design(el, L_km, up) if el > 0 else None
    if cable:
        cable["Load: boosters [kW]"] = el_b
        cable["Load: electrical heating [kW]"] = el_h
        if cable["Voltage [kV]"] > 6.6 and el_b > 0:
            mva = el / 1000.0 / up["cos_phi"]
            cable["Subsea step-down transformer [MVA]"] = mva
            cable["Transformer losses [kW]"] = 0.015 * el
            notes.append(f"Transmission at {cable['Voltage [kV]']:.0f} kV: a {mva:.1f} MVA subsea step-down transformer "
                         "feeds the 6.6 kV booster motors (CAPEX allowance added)")
    if cable and not cable["ok"]:
        notes.append("No AC cable option meets the limits: consider a subsea step-down transformer, a higher "
                     "transmission voltage, low-frequency AC or DC")
    elif cable and cable["Charging current [A]"] > 0.25 * cable["Ampacity [A]"]:
        notes.append("Long AC step-out: charging current is significant; check reactive compensation")
    return {"length_km": L_km, "depth_m": depth, "services": rows, "booster_kW": el_b, "heating_kW": el_h, "cable": cable,
            "notes": notes, "hydraulics": "2 × HP and 2 × LP hydraulic supply, 1 return, fibre-optic/signal "
                                           "pairs (not sized - standard allowances)"}


# ----------------------------------------------------------------------------------- tie-back screening

def _scaled(st, fp, factor, boost_bar=0.0):
    if boost_bar > 0:
        return make_stream("", fp, st.F * factor, st.z, fp.pt_flash(st.z, st.T, st.P + boost_bar, st.flash.Kset))
    return make_stream("", fp, st.F * factor, st.z, st.flash)


def tieback_screen(fp, inlet, fl_params, riser_params=None, distances_km=(5, 15, 30, 50, 75),
                   rate_factors=(0.5, 1.0, 1.5), boost_bar=0.0, progress=None):
    """Arrival conditions of flowline (+ riser) over distance × rate. Returns a list of row dicts."""
    rows = []
    n = len(distances_km) * len(rate_factors)
    k = 0
    for rf in rate_factors:
        st0 = _scaled(inlet, fp, rf, boost_bar)
        for dkm in distances_km:
            k += 1
            row = {"Rate factor": rf, "Distance [km]": dkm, "Arrival P [bar(a)]": None, "Arrival T [°C]": None,
                   "Min. hydrate margin [°C]": None, "Feasible": False, "Limit": ""}
            try:
                fl = {"name": "Flowline", "type": "flowline",
                      "params": dict(fl_params, length=dkm * 1000.0, n_seg=max(4, min(10, int(math.ceil(dkm / 6.0)))))}
                outs, r1, _ = surf.calc_flowline(fl, {"in": [st0]}, fp)
                st = outs["out"][0]
                margins = [r1.get("Min. hydrate margin along line [°C]")]
                if riser_params:
                    rs = {"name": "Riser", "type": "riser", "params": dict(riser_params, n_seg=6)}
                    outs, r2, _ = surf.calc_riser(rs, {"in": [st]}, fp)
                    st = outs["out"][0]
                    margins.append(r2.get("Min. hydrate margin along line [°C]"))
                margins = [m for m in margins if m is not None]
                row.update({"Arrival P [bar(a)]": st.P, "Arrival T [°C]": st.T - K0,
                            "Min. hydrate margin [°C]": min(margins) if margins else None, "Feasible": True})
            except (UnitError, FlashError, ValueError, ZeroDivisionError) as e:
                row["Limit"] = str(e)[:140]
            rows.append(row)
            if progress:
                progress(k / n)
    return rows


def max_distance(rows, rf, P_min):
    """Longest tie-back at rate factor rf with arrival P ≥ P_min.

    Returns (km, basis): basis "interpolated" (linear between grid points), "at least" (the next grid point
    is infeasible, so the last feasible distance is a lower bound), "beyond grid" (every distance works), or
    (None, "not feasible")."""
    pts = sorted((r["Distance [km]"], r["Arrival P [bar(a)]"]) for r in rows if r["Rate factor"] == rf)
    if not pts or pts[0][1] is None or pts[0][1] < P_min:
        return None, "not feasible"
    for (d1, p1), (d2, p2) in zip(pts, pts[1:]):
        if p2 is None:
            return d1, "at least"
        if p2 < P_min:
            return d1 + (p1 - P_min) / (p1 - p2) * (d2 - d1), "interpolated"
    return pts[-1][0], "beyond grid"


# ------------------------------------------------------------------------------------- power supply options

POWER_DEFAULTS = {"shore_km": 150.0, "shore_cable_MUSD_km": 2.5, "converter_MUSD_MW": 1.5, "gt_MUSD_MW": 2.0,
                  "years": 20.0, "disc": 8.0, "fx": 10.5}
POWER_LABELS = {"shore_km": ("Distance to shore", "km"), "shore_cable_MUSD_km": ("Power-from-shore cable", "MUSD/km"),
                "converter_MUSD_MW": ("Shore / offshore converters", "MUSD/MW"),
                "gt_MUSD_MW": ("Gas turbine generator sets", "MUSD/MW"), "years": ("Evaluation period", "years"),
                "disc": ("Discount rate", "%"), "fx": ("Energy-cost currency per USD", "-")}


def power_params(model):
    p = dict(POWER_DEFAULTS)
    p.update({k: float(v) for k, v in (model.get("power") or {}).items() if k in POWER_DEFAULTS})
    return p


def power_supply_options(model, sol):
    """Power from shore vs local gas turbines for the flowsheet's power demand: CAPEX, energy cost, CO₂ and
    its cost, the discounted cost over the period and the CO₂ abatement cost of power from shore."""
    from . import economics
    p = power_params(model)
    ep = economics.params(model)
    P_kW = economics.compute(model, sol)["totals"]["Power demand [kW]"]
    if P_kW <= 0:
        return None
    hours = float(ep["hours"])
    mwh = P_kW * hours / 1000.0
    fx = max(p["fx"], 1e-9)
    ann = sum(1.0 / (1.0 + p["disc"] / 100.0) ** (k + 0.5) for k in range(int(p["years"])))
    MW = P_kW / 1000.0
    fuel_sm3 = P_kW * 3.6 / float(ep["fuel_lhv"]) / (float(ep["gt_eff"]) / 100.0) * hours
    gt = {"Option": "Local gas turbines", "CAPEX [MUSD]": MW * 1.3 * p["gt_MUSD_MW"],     # N+1 sparing ~30 %
          "Energy cost [MUSD/y]": fuel_sm3 * float(ep["fuel_price"]) / fx / 1e6,
          "CO₂ [kt/y]": fuel_sm3 * float(ep["fuel_co2"]) / 1e6}
    pfs = {"Option": "Power from shore", "CAPEX [MUSD]": p["shore_km"] * p["shore_cable_MUSD_km"] + MW * p["converter_MUSD_MW"],
           "Energy cost [MUSD/y]": mwh * float(ep["el_price"]) / fx / 1e6,
           "CO₂ [kt/y]": mwh * float(ep["grid_co2"]) / 1000.0}
    for o in (gt, pfs):
        o["CO₂ cost [MUSD/y]"] = o["CO₂ [kt/y]"] * 1000.0 * float(ep["co2_tax"]) / fx / 1e6
        o["Discounted cost over the period [MUSD]"] = o["CAPEX [MUSD]"] + ann * (o["Energy cost [MUSD/y]"] + o["CO₂ cost [MUSD/y]"])
    d_co2 = (gt["CO₂ [kt/y]"] - pfs["CO₂ [kt/y]"]) * 1000.0 * ann
    d_cost = (pfs["CAPEX [MUSD]"] + ann * pfs["Energy cost [MUSD/y]"]) - (gt["CAPEX [MUSD]"] + ann * gt["Energy cost [MUSD/y]"])
    abate = d_cost * 1e6 / d_co2 if d_co2 > 0 else None
    return {"Power demand [MW]": MW, "rows": [gt, pfs],
            "Abatement cost of power from shore [USD/t CO₂]": abate,
            "Cheaper over the period": min((gt, pfs), key=lambda o: o["Discounted cost over the period [MUSD]"])["Option"]}
