"""v7.6 example: a phased tie-in (today / after Phase 1) with a platform frame, gas turbine and phase splitter."""
from __future__ import annotations

from .flowsheet import new_model, add_unit, connect
from . import phasing as PH


def phased_hp_compression():
    from .examples import WET_GAS
    m = new_model()
    plat = add_unit(m, "platform", 330, 150, "Platform A (existing)", {"w": 760, "h": 360, "kind": "fixed"})
    f = add_unit(m, "feed", 20, 140, "Well stream", {"T_C": 30.0, "P_bar": 10.0, "flow_basis": "MSm³/d", "flow": 2.0,
                                                    "composition": dict(WET_GAS)})
    ps = add_unit(m, "phase_splitter", 130, 140, "PS-100 Inlet phase splitter", {"dP": 0.2})
    tee = add_unit(m, "splitter", 240, 100, "TEE-100 Fuel gas", {"fractions": "0.03"})
    k1 = add_unit(m, "compressor", 350, 100, "K-100 LP compressor", {"P_out": 30.0})
    gt = add_unit(m, "gas_turbine", 520, 235, "GT-100 Power turbine", {"rated_kW": 20000.0})
    k2 = add_unit(m, "compressor", 520, 100, "K-101 HP compressor", {"P_out": 90.0})
    e = add_unit(m, "cooler", 680, 100, "E-100 Export cooler", {"T_out": 35.0})
    out = add_unit(m, "product", 810, 100, "Export gas")
    oil = add_unit(m, "product", 240, 230, "Oil")
    wat = add_unit(m, "product", 240, 300, "Produced water")
    connect(m, f, "out", ps, "feed", "1")
    connect(m, ps, "oil", oil, "in", "Oil")
    connect(m, ps, "water", wat, "in", "Water")
    connect(m, ps, "vapour", tee, "in", "2")
    connect(m, tee, "out", gt, "fuel", "Fuel")
    connect(m, tee, "out", k1, "in", "3")
    p = PH.add_phase(m, "Phase 1: HP compression")
    old = connect(m, k1, "out", e, "in", "4")                       # today: LP compressor straight to the cooler
    PH.set_phase(m, [old], p["id"], "remove")
    m["units"][k2].update(phase=p["id"], change="add")              # Phase 1: HP compressor in the line
    for sid, (a, ap, b, bp, nm) in {"phA": (k1, "out", k2, "in", "4b"), "phB": (k2, "out", e, "in", "5")}.items():
        m["streams"][sid] = {"name": nm, "src": [a, ap], "dst": [b, bp], "phase": p["id"], "change": "add"}
    connect(m, e, "out", out, "in", "6")
    m["stage"] = -1
    return m


PHASED_EXAMPLES = {
    "Phasing (v7.6): future HP compression, gas turbine, phase splitter, platform frame": phased_hp_compression,
}


def gas_conditioning():
    """v7.7: sour wet gas -> molecular sieve -> membrane (CO2) -> chiller + cold separator (NGL / dew point)."""
    from .examples import WET_GAS
    m = new_model()
    comp = dict(WET_GAS)
    comp["CO2"], comp["C1"] = 0.10, comp["C1"] - 0.079
    tot = sum(comp.values())
    comp = {k: v / tot for k, v in comp.items()}
    f = add_unit(m, "feed", 20, 140, "Sour wet gas", {"T_C": 30.0, "P_bar": 70.0, "flow_basis": "MSm³/d", "flow": 3.0, "composition": comp})
    h = add_unit(m, "heater", 130, 140, "E-100 Feed heater", {"spec": "Outlet temperature", "T_out": 40.0})
    sc = add_unit(m, "scrubber", 240, 140, "V-100 Inlet scrubber", {})
    ms = add_unit(m, "mol_sieve", 380, 140, "MS-100 Dryer", {"ppmv": 1.0})
    mb = add_unit(m, "membrane", 520, 140, "MB-100 CO2 membrane", {"spec": "CO2 in the retentate", "co2_spec": 2.5, "P_perm": 3.0})
    ch = add_unit(m, "chiller", 660, 140, "CH-100 Chiller", {"T_out": -15.0})
    ls = add_unit(m, "scrubber", 780, 140, "V-101 Cold separator", {})
    cl = add_unit(m, "cooler", 560, 270, "E-101 Permeate cooler", {"spec": "Outlet temperature", "T_out": 35.0})
    sales = add_unit(m, "product", 900, 100, "Sales gas")
    ngl = add_unit(m, "product", 800, 300, "NGL / condensate")
    perm = add_unit(m, "product", 720, 270, "CO2-rich permeate")
    wat = add_unit(m, "product", 240, 290, "Water")
    wat2 = add_unit(m, "product", 380, 290, "Sieve water")
    connect(m, f, "out", h, "in", "1")
    connect(m, h, "out", sc, "feed", "2")
    connect(m, sc, "vapour", ms, "feed", "3")
    connect(m, sc, "liquid", wat, "in", "Liquids")
    connect(m, ms, "dry", mb, "feed", "4")
    connect(m, ms, "water", wat2, "in", "Sieve water")
    connect(m, mb, "retentate", ch, "in", "5")
    connect(m, mb, "permeate", cl, "in", "Permeate")
    connect(m, cl, "out", perm, "in", "Cooled permeate")
    connect(m, ch, "out", ls, "feed", "6")
    connect(m, ls, "vapour", sales, "in", "Sales")
    connect(m, ls, "liquid", ngl, "in", "NGL")
    return m


PHASED_EXAMPLES["Gas conditioning (v7.7): mol-sieve dryer, CO₂ membrane, chiller and cold separator"] = gas_conditioning


def subsea_equipment():
    """v7.8: two wells -> manifold -> choke module -> multiphase meter -> electric heater -> flowline -> pig receiver; a water
    take-off through a de-oiling hydrocyclone is shown on a separate branch."""
    from .examples import WELL_FLUID
    m = new_model()
    fl = {"T_C": 85.0, "P_bar": 140.0, "flow_basis": "kmol/h", "flow": 900.0, "composition": dict(WELL_FLUID)}
    w1 = add_unit(m, "feed", 20, 80, "Well A", dict(fl))
    w2 = add_unit(m, "feed", 20, 220, "Well B", dict(fl, T_C=78.0, P_bar=132.0, flow=700.0))
    mf = add_unit(m, "subsea_manifold", 170, 150, "MF-100 Manifold", {"dP": 0.4, "ID": 254.0, "slots": 4})
    ck = add_unit(m, "subsea_choke", 310, 150, "CK-100 Choke module", {"spec": "Opening (Cv)", "Cv_max": 500.0, "opening": 35.0})
    ft = add_unit(m, "mpfm", 440, 150, "FT-100 Multiphase meter", {})
    ht = add_unit(m, "subsea_heater", 580, 150, "H-100 Electric heater", {"spec": "Outlet temperature", "T_out": 95.0, "eff": 97.0})
    line = add_unit(m, "flowline", 740, 150, "FL-100 Flowline", {"design": "Pipe-in-pipe", "length": 15000.0, "ID": 254.0, "dz": 0.0})
    pg = add_unit(m, "pig_station", 900, 150, "PG-100 Pig receiver", {"L": 15.0, "ID": 254.0, "holdup": 15.0, "capacity": 150.0})
    out = add_unit(m, "product", 1030, 150, "To host")
    connect(m, w1, "out", mf, "in", "A")
    connect(m, w2, "out", mf, "in", "B")
    connect(m, mf, "out", ck, "in", "1")
    connect(m, ck, "out", ft, "in", "2")
    connect(m, ft, "out", ht, "in", "3")
    connect(m, ht, "out", line, "in", "4")
    connect(m, line, "out", pg, "in", "5")
    connect(m, pg, "out", out, "in", "6")
    return m


PHASED_EXAMPLES["Subsea equipment (v7.8): manifold, choke module, MPFM, electric heater, pig receiver"] = subsea_equipment
