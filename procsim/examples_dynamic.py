"""Dynamic-simulation example flowsheets (v7.3): each is a normal steady-state flowsheet plus a ``dynamics`` block
(events, relief devices, tuning) that the Dynamic tab picks up.  Generic illustrative data only (the repository is
public)."""
from __future__ import annotations

from .flowsheet import new_model, add_unit, connect


def _fluid(name):
    from . import examples
    return dict(getattr(examples, name))


def separator_control():
    """HP separator with level and pressure control: a feed-rate step (+30 %, a slug-like disturbance) and then a drop
    in feed temperature; the default level and pressure controllers are active."""
    m = new_model()
    f = add_unit(m, "feed", 0, 100, "Well stream", {"T_C": 60.0, "P_bar": 40.0, "flow_basis": "kmol/h", "flow": 6000.0,
                                                     "composition": _fluid("WELL_FLUID")})
    s = add_unit(m, "separator", 170, 90, "V-100 HP separator", {"P_bar": 30.0})
    vg = add_unit(m, "valve", 340, 20, "PCV-100 gas outlet", {"P_out": 20.0})
    vl = add_unit(m, "valve", 340, 190, "LCV-100 liquid outlet", {"P_out": 10.0})
    g = add_unit(m, "product", 500, 20, "Gas to the next stage")
    o = add_unit(m, "product", 500, 190, "Liquid to the next stage")
    connect(m, f, "out", s, "feed")
    connect(m, s, "vapour", vg, "in")
    connect(m, s, "liquid", vl, "in")
    connect(m, vg, "out", g, "in")
    connect(m, vl, "out", o, "in")
    m["dynamics"] = {"t_end": 1800.0, "dt_out": 10.0,
                     "events": [{"t": 120, "kind": "feed_flow", "target": "Well stream", "value": 7800.0, "ramp": 30},
                                {"t": 900, "kind": "feed_T", "target": "Well stream", "value": 45.0, "ramp": 60}]}
    return m


def gas_blowdown():
    """Depressurising a gas vessel through a blowdown valve to a flare header: the pressure and temperature fall as the
    gas expands (isentropic-like), the valve being sized as an orifice."""
    m = new_model()
    f = add_unit(m, "feed", 0, 100, "Gas supply", {"T_C": 30.0, "P_bar": 60.0, "flow_basis": "kmol/h", "flow": 300.0,
                                                    "composition": {"C1": 0.95, "C2": 0.05}})
    s = add_unit(m, "scrubber", 170, 90, "V-200 Gas vessel", {})
    v = add_unit(m, "valve", 340, 90, "XV-200 outlet", {"P_out": 55.0})
    p = add_unit(m, "product", 500, 90, "Process outlet")
    connect(m, f, "out", s, "feed")
    connect(m, s, "vapour", v, "in")
    connect(m, v, "out", p, "in")
    m["dynamics"] = {"t_end": 900.0, "dt_out": 5.0,
                     "nodes": {"V-200 Gas vessel": {"volume": 40.0, "orient": "Vertical", "level0": 0.0}},
                     "relief": [{"name": "BDV-200", "node": "V-200 Gas vessel", "type": "BDV", "D_mm": 40.0,
                                 "P_back": 1.5}],
                     "events": [{"t": 30, "kind": "feed_flow", "target": "Gas supply", "value": 0.0, "ramp": 1},
                                {"t": 30, "kind": "valve_op", "target": "XV-200 outlet", "value": 0.0},
                                {"t": 60, "kind": "relief_open", "target": "BDV-200"}]}
    return m


def compressor_trip():
    """Gas compression train: suction separator, compressor with an anti-surge valve, cooler, discharge scrubber and an
    export valve.  The compressor trips at 60 s; the anti-surge valve opens and the machine coasts down."""
    m = new_model()
    f = add_unit(m, "feed", 30, 100, "Gas in", {"T_C": 30.0, "P_bar": 10.0, "flow_basis": "kmol/h", "flow": 5000.0,
                                                 "composition": {"C1": 0.9, "C2": 0.06, "C3": 0.04}})
    s = add_unit(m, "separator", 150, 100, "V-100 Suction scrubber", {})
    k = add_unit(m, "compressor", 270, 100, "K-100", {"P_out": 30.0, "eff": 78.0})
    c = add_unit(m, "cooler", 380, 100, "E-100 Aftercooler", {"T_out": 40.0, "dP": 0.3})
    s2 = add_unit(m, "scrubber", 480, 100, "V-110 Discharge scrubber", {})
    va = add_unit(m, "valve", 600, 100, "PCV-110 export valve", {"P_out": 25.0})
    p = add_unit(m, "product", 720, 100, "Export gas")
    connect(m, f, "out", s, "feed"); connect(m, s, "vapour", k, "in"); connect(m, k, "out", c, "in")
    connect(m, c, "out", s2, "feed"); connect(m, s2, "vapour", va, "in"); connect(m, va, "out", p, "in")
    m["dynamics"] = {"t_end": 300.0, "dt_out": 5.0,
                     "compressors": {"K-100": {"antisurge": True, "tau_coast": 40.0}},
                     "events": [{"t": 60, "kind": "comp_trip", "target": "K-100"},
                                {"t": 62, "kind": "feed_flow", "target": "Gas in", "value": 0.0, "ramp": 5}]}
    return m


def pipeline_linepack():
    """A 20 km gas export line with a fixed delivery valve opening: the inlet rate is stepped up by 20 % and the line pack
    and pressures respond over about an hour (no controller acts - the delivery rate follows the pressure)."""
    m = new_model()
    f = add_unit(m, "feed", 30, 100, "Gas in", {"T_C": 40.0, "P_bar": 70.0, "flow_basis": "MSm³/d", "flow": 3.0,
                                                 "composition": {"C1": 0.92, "C2": 0.05, "C3": 0.03}})
    pp = add_unit(m, "pipe", 150, 100, "PL-1 Export line", {"length": 20000.0, "ID": 500.0, "rough": 0.05, "dz": 0.0,
                                                             "n_seg": 10})
    va = add_unit(m, "valve", 300, 100, "PCV-out delivery valve", {"P_out": 50.0})
    p = add_unit(m, "product", 430, 100, "Delivery")
    connect(m, f, "out", pp, "in"); connect(m, pp, "out", va, "in"); connect(m, va, "out", p, "in")
    m["dynamics"] = {"t_end": 3600.0, "dt_max": 10.0, "dt_out": 30.0, "pipes": {"PL-1 Export line": {"cells": 10}},
                     "events": [{"t": 60, "kind": "feed_flow", "target": "Gas in", "value": 3.6, "ramp": 5}]}
    return m


DYNAMIC_EXAMPLES = {
    "Dynamic: HP separator with level and pressure control (feed step and temperature drop)": separator_control,
    "Dynamic: gas vessel blowdown to flare (depressurisation)": gas_blowdown,
    "Dynamic: compressor trip with anti-surge valve and coast-down": compressor_trip,
    "Dynamic: pipeline line-pack after a rate step": pipeline_linepack,
}
