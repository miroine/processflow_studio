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
