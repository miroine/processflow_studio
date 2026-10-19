"""Topside example flowsheets (v7.1): HP compressor bypass, debottlenecking, two parallel trains with recycle loops,
produced-water handling and reinjection, gas blending to a sales-gas specification, and a flare system.
Generic illustrative data only (the repository is public)."""
from __future__ import annotations

from .flowsheet import new_model, add_unit, connect
from .unitops import typical_curve


def _fluid(name):
    """A composition of procsim.examples (imported late: that module imports this one at its end)."""
    from . import examples
    return dict(getattr(examples, name))


# ---------------------------------------------------------------------------------------------------------------
# 1. HP compressor with a bypass line
# ---------------------------------------------------------------------------------------------------------------


def hp_compressor_bypass():
    """Early-life topside: the HP separator gas (85 bar) is split between the HP compressor (to 130 bar export) and a
    bypass valve that lets the rest down to the 70 bar MP header. The split fraction is the operating choice: 1.0 =
    everything compressed, 0 = full bypass (compressor shut down)."""
    m = new_model()
    f = add_unit(m, "feed", 0, 200, "HP well gas", {"T_C": 40.0, "P_bar": 85.0, "flow_basis": "MSm³/d", "flow": 3.0,
                                                    "composition": _fluid("WET_GAS")})
    v1 = add_unit(m, "separator3", 160, 190, "V-100 HP separator", {"ID": 1900.0})
    sc = add_unit(m, "scrubber", 330, 180, "V-101 Suction scrubber", {"internal": "Vane pack", "ID": 1300.0})
    tee = add_unit(m, "splitter", 500, 200, "TEE-100 Bypass split", {"fractions": "0.7"})
    k = add_unit(m, "compressor", 660, -20, "K-100 HP compressor",
                 {"P_out": 130.0, "eff": 77.0, "speed": 9500.0, "N_design": 9500.0,
                  "curve": typical_curve(900.0, 46.0, 78.0), "antisurge": "On", "sm_min": 10.0,
                  "driver": "Electric motor", "driver_rating": 1500.0})
    e1 = add_unit(m, "cooler", 830, -20, "E-100 Discharge cooler", {"T_out": 40.0, "dP": 0.7})
    ex = add_unit(m, "product", 1000, -20, "Export gas (130 bar)")
    vb = add_unit(m, "valve", 660, 340, "VLV-100 Compressor bypass", {"P_out": 70.0, "Cv_rated": 110.0})
    by = add_unit(m, "product", 850, 340, "Bypass gas to the MP header (70 bar)")
    cond = add_unit(m, "product", 330, 330, "Condensate")
    wat = add_unit(m, "product", 160, 330, "Produced water")
    liq = add_unit(m, "product", 430, 330, "Scrubber liquids")
    connect(m, f, "out", v1, "feed")
    connect(m, v1, "vapour", sc, "feed", "HP gas")
    connect(m, sc, "vapour", tee, "in", "Gas to split")
    connect(m, tee, "out", k, "in", "To compressor")
    connect(m, tee, "out", vb, "in", "Bypass")
    connect(m, k, "out", e1, "in", "Discharge")
    connect(m, e1, "out", ex, "in")
    connect(m, vb, "out", by, "in")
    connect(m, v1, "oil", cond, "in")
    connect(m, v1, "water", wat, "in")
    connect(m, sc, "liquid", liq, "in")
    return m


# ---------------------------------------------------------------------------------------------------------------
# 2. Debottlenecking study
# ---------------------------------------------------------------------------------------------------------------


def debottlenecking():
    """A two-stage gas compression plant with its capacities set (vessel diameters, compressor curves, driver
    ratings, valve size, export line): at the base rate everything is below its limit; the Design tab's
    *Debottlenecking* panel ranks the utilisation and sweeps the throughput to find which limit is reached first."""
    m = new_model()
    f = add_unit(m, "feed", 0, 200, "Wet gas", {"T_C": 35.0, "P_bar": 20.0, "flow_basis": "MSm³/d", "flow": 3.0,
                                                 "composition": _fluid("WET_GAS")})
    v1 = add_unit(m, "separator3", 160, 190, "V-100 Inlet separator", {"ID": 2300.0})
    k1 = add_unit(m, "compressor", 340, 90, "K-100 LP compressor",
                  {"P_out": 55.0, "eff": 77.0, "speed": 10000.0, "N_design": 10000.0,
                   "curve": typical_curve(5100.0, 132.0, 78.0), "antisurge": "On", "sm_min": 10.0,
                   "driver": "Electric motor", "driver_rating": 6000.0})
    e1 = add_unit(m, "cooler", 520, 90, "E-100 Interstage cooler", {"T_out": 35.0, "dP": 0.5})
    v2 = add_unit(m, "scrubber", 680, 80, "V-101 Interstage scrubber", {"internal": "Mesh pad", "ID": 1950.0})
    k2 = add_unit(m, "compressor", 860, 0, "K-101 HP compressor",
                  {"P_out": 140.0, "eff": 77.0, "speed": 10000.0, "N_design": 10000.0,
                   "curve": typical_curve(1900.0, 113.0, 78.0), "antisurge": "On", "sm_min": 10.0,
                   "driver": "Electric motor", "driver_rating": 5000.0})
    e2 = add_unit(m, "cooler", 1040, 0, "E-101 Aftercooler", {"T_out": 40.0, "dP": 0.7})
    vx = add_unit(m, "valve", 1200, 0, "VLV-100 Export control valve", {"P_out": 130.0, "Cv_rated": 300.0})
    pe = add_unit(m, "pipe", 1370, 0, "PIPE-100 Export line", {"length": 6000.0, "ID": 250.0, "rough": 0.045})
    ex = add_unit(m, "product", 1540, 0, "Export gas")
    cond = add_unit(m, "product", 340, 330, "Condensate")
    wat = add_unit(m, "product", 160, 330, "Produced water")
    liq = add_unit(m, "product", 700, 250, "Scrubber liquids")
    connect(m, f, "out", v1, "feed")
    connect(m, v1, "vapour", k1, "in", "LP gas")
    connect(m, k1, "out", e1, "in", "1")
    connect(m, e1, "out", v2, "feed", "2")
    connect(m, v2, "vapour", k2, "in", "MP gas")
    connect(m, k2, "out", e2, "in", "3")
    connect(m, e2, "out", vx, "in", "4")
    connect(m, vx, "out", pe, "in", "5")
    connect(m, pe, "out", ex, "in")
    connect(m, v1, "oil", cond, "in")
    connect(m, v1, "water", wat, "in")
    connect(m, v2, "liquid", liq, "in")
    return m


# ---------------------------------------------------------------------------------------------------------------
# 3. Two parallel trains, each with its own recycle loop
# ---------------------------------------------------------------------------------------------------------------


def two_trains():
    """Two parallel gas-compression trains (A and B) fed from one inlet through a tee (55 / 45), each with its own
    scrubber-liquid recycle loop (RCY-A, RCY-B) back to its inlet separator; the compressed gas is recombined in the
    export header. Change the split to load the trains differently or to take one out of service (1 / 0)."""
    m = new_model()
    f = add_unit(m, "feed", 0, 250, "Wet gas", {"T_C": 30.0, "P_bar": 10.0, "flow_basis": "MSm³/d", "flow": 4.0,
                                                 "composition": _fluid("WET_GAS")})
    tee = add_unit(m, "splitter", 150, 250, "TEE-100 Train split", {"fractions": "0.55"})
    ids = {}
    for tag, y, Qd, rating in (("A", 40, 9500.0, 6000.0), ("B", 480, 7800.0, 5200.0)):
        mx = add_unit(m, "mixer", 300, y + 20, f"MIX-{tag}")
        v1 = add_unit(m, "separator3", 450, y + 10, f"V-{tag}00 Inlet separator {tag}")
        k = add_unit(m, "compressor", 640, y - 100, f"K-{tag}00 Compressor {tag}",
                     {"P_out": 40.0, "eff": 76.0, "speed": 10000.0, "N_design": 10000.0,
                      "curve": typical_curve(Qd, 188.0, 77.0), "antisurge": "On", "sm_min": 10.0,
                      "driver": "Electric motor", "driver_rating": rating})
        e = add_unit(m, "cooler", 810, y - 100, f"E-{tag}00 Cooler {tag}", {"T_out": 35.0, "dP": 0.5})
        sc = add_unit(m, "scrubber", 960, y - 110, f"V-{tag}01 Scrubber {tag}", {"internal": "Mesh pad", "ID": 1800.0})
        vl = add_unit(m, "valve", 960, y + 140, f"VLV-{tag}00 Liquid let-down", {"P_out": 10.0})
        rc = add_unit(m, "recycle", 700, y + 150, f"RCY-{tag}")
        m["units"][rc]["flip"] = True
        oil = add_unit(m, "product", 560, y + 120, f"Condensate {tag}")
        wat = add_unit(m, "product", 450, y + 215, f"Water {tag}")
        connect(m, mx, "out", v1, "feed", f"{tag}1")
        connect(m, v1, "vapour", k, "in", f"{tag}2")
        connect(m, k, "out", e, "in", f"{tag}3")
        connect(m, e, "out", sc, "feed", f"{tag}4")
        connect(m, sc, "liquid", vl, "in", f"{tag}5")
        connect(m, vl, "out", rc, "in", f"{tag}6")
        connect(m, rc, "out", mx, "in", f"{tag}7")
        connect(m, v1, "oil", oil, "in")
        connect(m, v1, "water", wat, "in")
        ids[tag] = (mx, sc)
    hdr = add_unit(m, "mixer", 1130, 250, "MIX-100 Export header")
    ex = add_unit(m, "product", 1290, 250, "Export gas")
    connect(m, tee, "out", ids["A"][0], "in", "Train A feed")
    connect(m, tee, "out", ids["B"][0], "in", "Train B feed")
    connect(m, f, "out", tee, "in")
    connect(m, ids["A"][1], "vapour", hdr, "in", "A gas")
    connect(m, ids["B"][1], "vapour", hdr, "in", "B gas")
    connect(m, hdr, "out", ex, "in")
    return m


# ---------------------------------------------------------------------------------------------------------------
# 4. Produced-water handling and reinjection
# ---------------------------------------------------------------------------------------------------------------


def _wet_oil():
    """A high-water-cut well fluid (about 60 mol% water)."""
    w = dict(_fluid("WELL_FLUID"), H2O=0.60)
    return {k: v / sum(w.values()) for k, v in w.items()}


def water_reinjection():
    """Topside produced-water system: water from the HP separator is let down to a degasser (flash gas to the
    LP header), split between reinjection and overboard discharge, topped up with treated seawater, boosted by an
    injection pump and injected through two wells (injectivity, wellhead pressure and pump power are reported)."""
    m = new_model()
    f = add_unit(m, "feed", 0, 150, "Well fluid", {"T_C": 75.0, "P_bar": 60.0, "flow_basis": "kmol/h",
                                                    "flow": 7000.0, "composition": _wet_oil()})
    hp = add_unit(m, "separator3", 150, 140, "V-100 HP separator")
    v1 = add_unit(m, "valve", 310, 270, "VLV-100", {"P_out": 8.0})
    lp = add_unit(m, "separator3", 450, 260, "V-200 LP separator")
    gas = add_unit(m, "product", 330, 20, "HP gas")
    lpg = add_unit(m, "product", 620, 200, "LP gas")
    oil = add_unit(m, "product", 620, 330, "Stabilised oil")
    vw = add_unit(m, "valve", 450, 470, "VLV-W Water let-down", {"P_out": 3.0})
    dg = add_unit(m, "separator", 750, 460, "V-300 Water degasser")
    dgg = add_unit(m, "product", 900, 380, "Degasser flash gas")
    tee = add_unit(m, "splitter", 900, 480, "TEE-W Reinjection / overboard", {"fractions": "0.85"})
    ob = add_unit(m, "product", 1060, 590, "Overboard water")
    sw = add_unit(m, "feed", 900, 640, "Treated seawater", {"T_C": 8.0, "P_bar": 3.0, "flow_basis": "kg/h",
                                                           "flow": 40000.0, "composition": {"H2O": 1.0}})
    mx = add_unit(m, "mixer", 1050, 480, "MIX-I Injection water")
    pu = add_unit(m, "pump", 1200, 480, "P-100 Water injection pump", {"P_out": 110.0, "eff": 75.0})
    iw = add_unit(m, "injection_well", 1370, 470, "WI-1 Water injectors",
                  {"II": 30.0, "P_res": 270.0, "T_res": 85.0, "n_par": 2, "MD": 2900.0, "TVD": 2600.0})
    res = add_unit(m, "product", 1540, 480, "Water into the reservoir")
    connect(m, f, "out", hp, "feed")
    connect(m, hp, "vapour", gas, "in")
    connect(m, hp, "oil", v1, "in", "HP oil")
    connect(m, v1, "out", lp, "feed", "1")
    connect(m, lp, "vapour", lpg, "in")
    connect(m, lp, "oil", oil, "in")
    connect(m, hp, "water", vw, "in", "Produced water")
    connect(m, lp, "water", add_unit(m, "product", 620, 400, "LP water"), "in")
    connect(m, vw, "out", dg, "feed", "3")
    connect(m, dg, "vapour", dgg, "in")
    connect(m, dg, "liquid", tee, "in", "Degassed water")
    connect(m, tee, "out", mx, "in", "To reinjection")
    connect(m, tee, "out", ob, "in", "Overboard")
    connect(m, sw, "out", mx, "in", "Seawater make-up")
    connect(m, mx, "out", pu, "in", "Injection water")
    connect(m, pu, "out", iw, "in", "Pump discharge")
    connect(m, iw, "out", res, "in", "Injected water")
    return m


# ---------------------------------------------------------------------------------------------------------------
# 5. Gas mixing (blending to a sales-gas specification)
# ---------------------------------------------------------------------------------------------------------------
LEAN_GAS = {"N2": 0.025, "CO2": 0.015, "C1": 0.940, "C2": 0.015, "C3": 0.005}
RICH_GAS = {"N2": 0.004, "CO2": 0.020, "C1": 0.700, "C2": 0.130, "C3": 0.090, "iC4": 0.020, "nC4": 0.025,
            "iC5": 0.006, "nC5": 0.005}
CO2_GAS = {"N2": 0.010, "CO2": 0.090, "C1": 0.860, "C2": 0.025, "C3": 0.010, "nC4": 0.005}
BLEND_KEYS = ["N2", "CO2", "C1", "C2", "C3", "iC4", "nC4", "iC5", "nC5"]


def gas_blending():
    """Three gases at different pressures (lean, rich, CO₂-rich) are let down to a common header pressure and blended;
    an Adjust varies the rich-gas flow until the Wobbe index of the sales gas is 50.5 MJ/Sm³ (the export window is
    about 47-52). The sales-gas properties (GCV, Wobbe index, relative density, CO₂) are in the stream table; the
    CO₂-rich gas flow sets the CO₂ content (limit 2.5 mol%)."""
    m = new_model(BLEND_KEYS)
    a = add_unit(m, "feed", 0, 40, "Lean gas (field A)", {"T_C": 30.0, "P_bar": 70.0, "flow_basis": "MSm³/d",
                                                          "flow": 2.5, "composition": dict(LEAN_GAS)})
    b = add_unit(m, "feed", 0, 200, "Rich gas (field B)", {"T_C": 35.0, "P_bar": 55.0, "flow_basis": "MSm³/d",
                                                           "flow": 0.4, "composition": dict(RICH_GAS)})
    c = add_unit(m, "feed", 0, 360, "CO2-rich gas (field C)", {"T_C": 30.0, "P_bar": 48.0, "flow_basis": "MSm³/d",
                                                                "flow": 0.5, "composition": dict(CO2_GAS)})
    va = add_unit(m, "valve", 200, 40, "VLV-A Header pressure control", {"P_out": 45.0})
    vb = add_unit(m, "valve", 200, 200, "VLV-B Header pressure control", {"P_out": 45.0})
    vc = add_unit(m, "valve", 200, 360, "VLV-C Header pressure control", {"P_out": 45.0})
    mx = add_unit(m, "mixer", 400, 200, "MIX-100 Blending header")
    sales = add_unit(m, "product", 560, 200, "Sales gas")
    adj = add_unit(m, "adjust", 400, 330, "ADJ-1 Wobbe index")
    connect(m, a, "out", va, "in", "A")
    connect(m, b, "out", vb, "in", "B")
    connect(m, c, "out", vc, "in", "C")
    connect(m, va, "out", mx, "in", "A at header")
    connect(m, vb, "out", mx, "in", "B at header")
    connect(m, vc, "out", mx, "in", "C at header")
    connect(m, mx, "out", sales, "in", "Blend")
    m["units"][adj]["params"].update({"var_unit": b, "var_param": "flow", "tgt_kind": "stream", "tgt_obj": "Sales gas",
                                      "tgt_prop": "Wobbe index [MJ/Sm³]", "tgt_value": 50.5,
                                      "var_min": 0.01, "var_max": 3.0, "tol": 0.005})
    return m


# ---------------------------------------------------------------------------------------------------------------
# 6. Flare system
# ---------------------------------------------------------------------------------------------------------------


def flare_system():
    """A two-header flare system: HP header (PSVs on the HP separator and on the compressor discharge, plus a purge
    stream) and LP header (PSV on the LP separator), each through a header line and a knock-out drum to its flare
    (radiation and flame length at the design distance). Check the PSV back pressure, the header velocity and the
    radiation at the fence line. PSV-200 is a balanced-bellows valve (Kb = 0.9) because its back pressure is 25 % of the set pressure."""
    G = {"N2": 0.010, "CO2": 0.020, "C1": 0.820, "C2": 0.070, "C3": 0.040, "nC4": 0.020, "nC5": 0.012, "nC6": 0.008}
    m = new_model(list(G))
    s1 = add_unit(m, "feed", 0, 40, "HP separator relief gas", {"T_C": 55.0, "P_bar": 60.0, "flow_basis": "kmol/h",
                                                                 "flow": 2400.0, "composition": dict(G)})
    s2 = add_unit(m, "feed", 0, 200, "Compressor discharge relief gas", {"T_C": 120.0, "P_bar": 135.0,
                                                                          "flow_basis": "kmol/h", "flow": 1500.0,
                                                                          "composition": dict(G)})
    pg = add_unit(m, "feed", 0, 120, "Purge / pilot gas", {"T_C": 30.0, "P_bar": 5.0, "flow_basis": "kmol/h",
                                                          "flow": 15.0, "composition": dict(G)})
    s3 = add_unit(m, "feed", 0, 500, "LP separator relief gas", {"T_C": 60.0, "P_bar": 9.0, "flow_basis": "kmol/h",
                                                                  "flow": 1800.0, "composition": dict(G)})
    r1 = add_unit(m, "relief_valve", 190, 40, "PSV-100 HP separator", {"P_set": 66.0, "P_back": 4.0})
    r2 = add_unit(m, "relief_valve", 190, 200, "PSV-101 Compressor discharge", {"P_set": 143.0, "P_back": 4.0})
    hp = add_unit(m, "mixer", 370, 120, "MIX-HP HP flare header", {"pressure": "Specified", "P_out": 3.5})
    ph = add_unit(m, "pipe", 520, 110, "PIPE-HP HP header", {"length": 400.0, "ID": 600.0, "rough": 0.045})
    kh = add_unit(m, "separator", 690, 100, "V-HP HP flare KO drum")
    fh = add_unit(m, "flare", 860, 100, "FL-HP HP flare", {"H_stack": 60.0, "dist": 70.0})
    r3 = add_unit(m, "relief_valve", 190, 500, "PSV-200 LP separator", {"P_set": 10.0, "P_back": 2.5, "Kb": 0.9})
    pl = add_unit(m, "pipe", 520, 490, "PIPE-LP LP header", {"length": 300.0, "ID": 500.0, "rough": 0.045})
    kl = add_unit(m, "separator", 690, 480, "V-LP LP flare KO drum")
    fl = add_unit(m, "flare", 860, 480, "FL-LP LP flare", {"H_stack": 45.0, "dist": 60.0})
    kh_l = add_unit(m, "product", 690, 250, "HP KO drum liquids")
    kl_l = add_unit(m, "product", 690, 620, "LP KO drum liquids")
    connect(m, s1, "out", r1, "in")
    connect(m, s2, "out", r2, "in")
    connect(m, r1, "out", hp, "in", "PSV-100 outlet")
    connect(m, r2, "out", hp, "in", "PSV-101 outlet")
    connect(m, pg, "out", hp, "in", "Purge gas")
    connect(m, hp, "out", ph, "in", "HP header")
    connect(m, ph, "out", kh, "feed", "To HP KO drum")
    connect(m, kh, "vapour", fh, "in", "To HP flare")
    connect(m, kh, "liquid", kh_l, "in")
    connect(m, s3, "out", r3, "in")
    connect(m, r3, "out", pl, "in", "PSV-200 outlet")
    connect(m, pl, "out", kl, "feed", "To LP KO drum")
    connect(m, kl, "vapour", fl, "in", "To LP flare")
    connect(m, kl, "liquid", kl_l, "in")
    return m


TOPSIDE_EXAMPLES = {
    "HP compressor bypass (topside): split between compression and a let-down bypass": hp_compressor_bypass,
    "Debottlenecking (topside): utilisation of every unit and the first limit as the rate rises": debottlenecking,
    "Two parallel trains (topside): split, two compressor trains and two recycle loops": two_trains,
    "Water handling and reinjection (topside): degasser, seawater make-up, injection pump and wells": water_reinjection,
    "Gas mixing (topside): blending three gases to a Wobbe-index specification": gas_blending,
    "Flare system (topside): HP and LP headers, PSVs, knock-out drums and flares": flare_system,
}
