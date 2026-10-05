"""Ready-made example flowsheets (built through the public model API)."""
from __future__ import annotations

from .flowsheet import new_model, add_unit, connect
from .unitops import typical_curve

WET_GAS = {"N2": 0.012, "CO2": 0.021, "C1": 0.780, "C2": 0.071, "C3": 0.043, "iC4": 0.008, "nC4": 0.013,
           "iC5": 0.005, "nC5": 0.005, "nC6": 0.006, "nC7": 0.007, "nC8": 0.005, "H2O": 0.024}
WELL_FLUID = {"N2": 0.005, "CO2": 0.018, "C1": 0.420, "C2": 0.065, "C3": 0.055, "iC4": 0.012, "nC4": 0.028,
              "iC5": 0.013, "nC5": 0.016, "nC6": 0.030, "nC7": 0.060, "nC8": 0.078, "H2O": 0.200}
DRY_GAS = {"N2": 0.010, "CO2": 0.020, "C1": 0.840, "C2": 0.065, "C3": 0.035, "iC4": 0.007, "nC4": 0.010,
           "iC5": 0.004, "nC5": 0.003, "nC6": 0.003, "nC7": 0.002, "nC8": 0.001, "H2O": 0.0}


def compression_train():
    m = new_model()
    f = add_unit(m, "feed", 0, 260, "Wet gas", {"T_C": 30.0, "P_bar": 10.0, "flow_basis": "MSm³/d",
                                                 "flow": 2.0, "composition": dict(WET_GAS)})
    mx = add_unit(m, "mixer", 150, 260, "MIX-100")
    v1 = add_unit(m, "separator3", 320, 250, "V-100 Inlet sep")
    k1 = add_unit(m, "compressor", 520, 120, "K-100", {"P_out": 30.0, "eff": 76.0, "speed": 10000.0,
                                                       "N_design": 10500.0,
                                                       "curve": typical_curve(8900.0, 158.0, 77.0),
                                                       "antisurge": "On", "sm_min": 10.0})
    e1 = add_unit(m, "cooler", 680, 120, "E-100", {"T_out": 35.0, "dP": 0.5})
    v2 = add_unit(m, "scrubber", 840, 110, "V-101 Scrubber", {"internal": "Mesh pad", "ID": 1800.0})
    k2 = add_unit(m, "compressor", 1000, 10, "K-101", {"P_out": 95.0, "eff": 76.0, "speed": 11000.0,
                                                        "N_design": 11000.0,
                                                        "curve": typical_curve(2900.0, 160.0, 77.0),
                                                        "antisurge": "On", "sm_min": 10.0})
    e2 = add_unit(m, "cooler", 1160, 10, "E-101", {"T_out": 40.0, "dP": 0.7})
    exp = add_unit(m, "product", 1320, 10, "Export gas")
    vl = add_unit(m, "valve", 900, 300, "VLV-100", {"P_out": 10.0})
    rc = add_unit(m, "recycle", 600, 420, "RCY-1")
    oil = add_unit(m, "product", 560, 330, "Condensate")
    wat = add_unit(m, "product", 430, 370, "Produced water")
    m["units"][rc]["flip"] = True            # recycle flows right-to-left back to the mixer
    connect(m, f, "out", mx, "in")
    connect(m, mx, "out", v1, "feed", "2")
    connect(m, v1, "vapour", k1, "in", "3")
    connect(m, k1, "out", e1, "in", "4")
    connect(m, e1, "out", v2, "feed", "5")
    connect(m, v2, "vapour", k2, "in", "6")
    connect(m, k2, "out", e2, "in", "7")
    connect(m, e2, "out", exp, "in")
    connect(m, v2, "liquid", vl, "in", "8")
    connect(m, vl, "out", rc, "in", "9")
    connect(m, rc, "out", mx, "in", "10")
    connect(m, v1, "oil", oil, "in")
    connect(m, v1, "water", wat, "in")
    return m


def oil_stabilisation():
    m = new_model()
    f = add_unit(m, "feed", 30, 260, "Well fluid", {"T_C": 75.0, "P_bar": 70.0, "flow_basis": "kmol/h",
                                                    "flow": 3000.0, "composition": dict(WELL_FLUID)})
    hp = add_unit(m, "separator3", 150, 250, "V-100 HP sep", {})
    v1 = add_unit(m, "valve", 300, 380, "VLV-100", {"P_out": 20.0})
    mp = add_unit(m, "separator", 430, 370, "V-200 MP sep")
    v2 = add_unit(m, "valve", 560, 480, "VLV-200", {"P_out": 3.0})
    h1 = add_unit(m, "heater", 680, 480, "E-200 Oil heater", {"T_out": 60.0, "dP": 0.3})
    lp = add_unit(m, "separator3", 810, 470, "V-300 LP sep")
    k3 = add_unit(m, "compressor", 930, 330, "K-300 LP comp", {"P_out": 20.0, "eff": 75.0})
    c3 = add_unit(m, "cooler", 1170, 330, "E-300", {"T_out": 40.0, "dP": 0.3})
    s3 = add_unit(m, "scrubber", 1290, 300, "V-310 Scrubber", {"internal": "Mesh pad"})
    mx2 = add_unit(m, "mixer", 1050, 330, "MIX-200")
    k2 = add_unit(m, "compressor", 1380, 170, "K-200 MP comp", {"P_out": 70.0, "eff": 75.0})
    c2 = add_unit(m, "cooler", 1480, 170, "E-201", {"T_out": 40.0, "dP": 0.3})
    s2 = add_unit(m, "scrubber", 1580, 140, "V-210 Scrubber", {"internal": "Vane pack"})
    mx1 = add_unit(m, "mixer", 1700, 60, "MIX-100")
    rl = add_unit(m, "product", 1420, 440, "Scrubber liquids 1")
    rl2 = add_unit(m, "product", 1720, 260, "Scrubber liquids 2")
    gas = add_unit(m, "product", 1820, 60, "Export gas")
    oil = add_unit(m, "product", 960, 580, "Stabilised oil")
    w1 = add_unit(m, "product", 300, 520, "HP water")
    w3 = add_unit(m, "product", 960, 660, "LP water")
    connect(m, f, "out", hp, "feed")
    connect(m, hp, "oil", v1, "in", "HP oil")
    connect(m, v1, "out", mp, "feed", "11")
    connect(m, mp, "liquid", v2, "in", "MP oil")
    connect(m, v2, "out", h1, "in", "12")
    connect(m, h1, "out", lp, "feed", "13")
    connect(m, lp, "vapour", k3, "in", "LP gas")
    connect(m, k3, "out", mx2, "in", "14")
    connect(m, mp, "vapour", mx2, "in", "MP gas")
    connect(m, mx2, "out", c3, "in", "15")
    connect(m, c3, "out", s3, "feed", "16")
    connect(m, s3, "vapour", k2, "in", "16a")
    connect(m, s3, "liquid", rl, "in")
    connect(m, k2, "out", c2, "in", "17")
    connect(m, c2, "out", s2, "feed", "18")
    connect(m, s2, "vapour", mx1, "in", "18a")
    connect(m, s2, "liquid", rl2, "in")
    connect(m, hp, "vapour", mx1, "in", "HP gas")
    connect(m, mx1, "out", gas, "in")
    connect(m, lp, "oil", oil, "in")
    connect(m, hp, "water", w1, "in")
    connect(m, lp, "water", w3, "in")
    return m


def jt_dewpoint():
    m = new_model(["N2", "CO2", "C1", "C2", "C3", "iC4", "nC4", "iC5", "nC5", "nC6", "nC7", "nC8", "H2O"])
    gas = dict(DRY_GAS)
    gas["H2O"] = 0.0
    f = add_unit(m, "feed", 30, 150, "Rich gas", {"T_C": 35.0, "P_bar": 110.0, "flow_basis": "MSm³/d",
                                                  "flow": 5.0, "composition": gas})
    hx = add_unit(m, "hx", 200, 140, "E-100 Gas/gas", {"spec": "Tube outlet temperature", "T_spec": 0.0,
                                                        "dP_tube": 0.5, "dP_shell": 0.5})
    jt = add_unit(m, "valve", 380, 260, "VLV-100 JT", {"P_out": 70.0})
    lts = add_unit(m, "separator", 520, 250, "V-100 LTS")
    ngl = add_unit(m, "product", 660, 380, "NGL")
    sales = add_unit(m, "product", 30, 40, "Sales gas")
    adj = add_unit(m, "adjust", 380, 380, "ADJ-1")
    rc = add_unit(m, "recycle", 380, 40, "RCY-1")
    connect(m, f, "out", hx, "tube_in")
    connect(m, hx, "tube_out", jt, "in", "2")
    connect(m, jt, "out", lts, "feed", "3")
    connect(m, lts, "vapour", rc, "in", "Cold gas")
    connect(m, rc, "out", hx, "shell_in", "4")
    connect(m, hx, "shell_out", sales, "in")
    connect(m, lts, "liquid", ngl, "in")
    m["units"][adj]["params"].update({"var_unit": jt, "var_param": "P_out", "tgt_kind": "stream",
                                      "tgt_obj": "3", "tgt_prop": "Temperature [°C]", "tgt_value": -20.0,
                                      "var_min": 30.0, "var_max": 105.0, "tol": 0.01})
    return m


def subsea_tieback(meg_kg_h=2500.0):
    m = new_model(["N2", "CO2", "C1", "C2", "C3", "iC4", "nC4", "iC5", "nC5", "nC6", "nC7", "nC8", "H2O", "MEG"])
    wf = dict(WET_GAS)
    wf["H2O"] = 0.03
    f = add_unit(m, "feed", -160, 150, "Wellstream", {"T_C": 75.0, "P_bar": 160.0, "flow_basis": "MSm³/d",
                                                      "flow": 4.0, "composition": wf})
    meg = add_unit(m, "feed", -160, 290, "Lean MEG", {"T_C": 20.0, "P_bar": 170.0, "flow_basis": "kg/h",
                                                        "flow": meg_kg_h, "comp_basis": "Mass fractions",
                                                        "composition": {"MEG": 0.90, "H2O": 0.10}})
    mx = add_unit(m, "mixer", 20, 160, "MIX-100 MEG injection")
    ch = add_unit(m, "valve", 210, 152, "VLV-100 Choke", {"P_out": 130.0})
    fl = add_unit(m, "pipe", 440, 160, "PIPE-100 Flowline", {"length": 25000.0, "ID": 305.0, "dz": -120.0,
                                                               "n_seg": 12, "heat": "Overall U to ambient",
                                                               "U": 8.0, "T_amb": 4.0})
    rs = add_unit(m, "pipe", 680, 60, "PIPE-101 Riser", {"length": 400.0, "ID": 254.0, "dz": 380.0,
                                                          "n_seg": 6, "heat": "Overall U to ambient",
                                                          "U": 10.0, "T_amb": 6.0})
    ar = add_unit(m, "separator3", 900, 60, "V-100 Arrival sep")
    gas = add_unit(m, "product", 1080, -30, "Gas to process")
    cond = add_unit(m, "product", 1100, 150, "Condensate")
    wat = add_unit(m, "product", 960, 220, "Produced water")
    connect(m, f, "out", mx, "in")
    connect(m, meg, "out", mx, "in")
    connect(m, mx, "out", ch, "in", "Inhibited wellstream")
    connect(m, ch, "out", fl, "in", "Flowline inlet")
    connect(m, fl, "out", rs, "in", "Riser base")
    connect(m, rs, "out", ar, "feed", "Topside arrival")
    connect(m, ar, "vapour", gas, "in")
    connect(m, ar, "oil", cond, "in")
    connect(m, ar, "water", wat, "in")
    return m


def condensate_stabiliser():
    """Inlet 3-phase separation, let-down, preheat and a reboiled stabiliser column tuned to a TVP spec."""
    m = new_model()
    wf = dict(WELL_FLUID)
    f = add_unit(m, "feed", 0, 200, "Well fluid", {"T_C": 60.0, "P_bar": 45.0, "flow_basis": "kmol/h",
                                                    "flow": 2000.0, "composition": wf})
    v1 = add_unit(m, "separator3", 170, 190, "V-100 Inlet sep")
    gas = add_unit(m, "product", 360, 60, "HP gas")
    wat = add_unit(m, "product", 290, 330, "Produced water")
    lv = add_unit(m, "valve", 380, 272, "VLV-100", {"P_out": 9.0})
    h1 = add_unit(m, "heater", 540, 280, "E-100 Preheater", {"T_out": 70.0, "dP": 0.3})
    col = add_unit(m, "column", 740, 200, "T-100 Stabiliser",
                   {"n_trays": 8, "P_top": 8.5, "P_bot": 8.8, "condenser": "None", "reboiler": "Yes",
                    "reb_spec": "Reboiler temperature", "reb_value": 150.0})
    og = add_unit(m, "product", 900, 60, "Stabiliser off-gas")
    st_ = add_unit(m, "cooler", 920, 390, "E-101 Product cooler", {"T_out": 30.0, "dP": 0.3})
    oil = add_unit(m, "product", 1090, 390, "Stable condensate")
    adj = add_unit(m, "adjust", 600, 440, "ADJ-1 TVP")
    connect(m, f, "out", v1, "feed")
    connect(m, v1, "vapour", gas, "in")
    connect(m, v1, "water", wat, "in")
    connect(m, v1, "oil", lv, "in", "HP oil")
    connect(m, lv, "out", h1, "in", "1")
    connect(m, h1, "out", col, "feed_top", "Stabiliser feed")
    connect(m, col, "overhead", og, "in")
    connect(m, col, "bottoms", st_, "in", "Stabiliser bottoms")
    connect(m, st_, "out", oil, "in")
    m["units"][adj]["params"].update({"var_unit": col, "var_param": "reb_value", "tgt_kind": "unit",
                                      "tgt_obj": "T-100 Stabiliser", "tgt_prop": "Bottoms TVP @ 37.8 °C [bar(a)]",
                                      "tgt_value": 0.80, "var_min": 100.0, "var_max": 220.0, "tol": 0.002})
    return m



def subsea_field(n_wells=4, boosted=False):
    """SURF tie-back: wells (IPR + tubing) -> Xmas trees with chokes -> 4-slot template -> spool ->
    pipe-in-pipe flowline -> SSIV -> lazy-wave riser -> arrival separator.  Generic illustrative data.

    ``boosted``: a late-life, long step-out variant - depleted reservoir, chokes nearly open, a subsea
    wet-gas compressor after the template and a 45 km flowline."""
    m = new_model()
    wf = dict(WET_GAS)
    wells = [(320.0, 1.20, 520.0, 3450.0), (312.0, 1.00, 480.0, 3300.0), (328.0, 1.35, 560.0, 3600.0),
             (316.0, 1.10, 500.0, 3400.0)][:n_wells]
    dp_res, qf, P_choke, L_fl, dx = (0.0, 1.0, 150.0, 25000.0, 0) if not boosted else (110.0, 0.75, 50.0, 45000.0, 150)
    tmp = add_unit(m, "template", 330, 200, "TMP-100 Template", {"template": "4-slot template"})
    for k, (Pr, q, C, md) in enumerate(wells, start=1):
        y = 20 + 120 * (k - 1)
        f = add_unit(m, "feed", -260, y, f"Reservoir W-{k}", {"T_C": 110.0, "P_bar": Pr - dp_res, "flow_basis": "MSm³/d",
                                                              "flow": q * qf, "composition": dict(wf)})
        w = add_unit(m, "well", -110, y, f"W-{k}", {"C": C, "n": 0.8, "MD": md, "TVD": 3000.0, "ID": 125.0})
        xt = add_unit(m, "xmas_tree", 60, y, f"XT-{k}", {"tree": "Horizontal Xmas tree (HXT)", "P_out": P_choke})
        connect(m, f, "out", w, "in")
        connect(m, w, "out", xt, "in", f"W-{k} wellhead")
        connect(m, xt, "out", tmp, "in", f"XT-{k} outlet")
    up = tmp
    if boosted:
        bst = add_unit(m, "subsea_booster", 520, 200, "P-100 Subsea compressor",
                       {"btype": "Wet-gas compressor", "dP": 55.0})
        connect(m, tmp, "out", bst, "in", "Template outlet")
        up = bst
    sp = add_unit(m, "jumper", 540 + dx, 200, "J-100 Spool", {"kind": "Rigid spool (Z-shape)", "ID": 305.0})
    fl = add_unit(m, "flowline", 750 + dx, 200, "FL-100 Flowline", {"design": "Pipe-in-pipe", "length": L_fl,
                                                                       "ID": 305.0, "dz": -100.0, "n_seg": 10})
    sv = add_unit(m, "subsea_valve", 930 + dx, 200, "XV-100 SSIV", {"kind": "SSIV"})
    rs = add_unit(m, "riser", 1090 + dx, 130, "RSR-100 Riser", {"rtype": "Lazy-wave (flexible)", "depth": 350.0,
                                                               "ID": 305.0 if boosted else 254.0, "fl_len": L_fl,
                                                               "n_seg": 9})
    ar = add_unit(m, "separator3", 1270 + dx, 60, "V-100 Arrival sep")
    gas = add_unit(m, "product", 1450 + dx, -30, "Gas to process")
    cond = add_unit(m, "product", 1470 + dx, 160, "Condensate")
    wat = add_unit(m, "product", 1340 + dx, 240, "Produced water")
    connect(m, up, "out", sp, "in", "Booster outlet" if boosted else "Template outlet")
    connect(m, sp, "out", fl, "in", "Flowline inlet")
    connect(m, fl, "out", sv, "in", "Riser base")
    connect(m, sv, "out", rs, "in", "Riser inlet")
    connect(m, rs, "out", ar, "feed", "Topside arrival")
    connect(m, ar, "vapour", gas, "in")
    connect(m, ar, "oil", cond, "in")
    connect(m, ar, "water", wat, "in")
    return m


def subsea_field_boosted():
    return subsea_field(boosted=True)



OIL_WC = {"N2": 0.003, "CO2": 0.010, "C1": 0.200, "C2": 0.035, "C3": 0.030, "iC4": 0.007, "nC4": 0.015,
          "iC5": 0.007, "nC5": 0.009, "nC6": 0.016, "nC7": 0.033, "nC8": 0.045, "H2O": 0.590}


def heated_oil_tieback():
    """Oil tie-back kept warm by direct electrical heating: 2 oil wells → trees → template → chemical injection
    (methanol for start-up, delivered at low pressure and raised by a subsea pressure intensifier) → 18 km
    DEH-ready wet-insulated flowline holding 25 °C → SCR → arrival separator."""
    m = new_model(["N2", "CO2", "C1", "C2", "C3", "iC4", "nC4", "iC5", "nC5", "nC6", "nC7", "nC8", "H2O", "MeOH"])
    tmp = add_unit(m, "template", 330, 160, "TMP-100 Template", {"template": "4-slot template"})
    for k, (Pr, q, PI) in enumerate([(240.0, 700.0, 45.0), (235.0, 600.0, 40.0)], start=1):
        y = 60 + 180 * (k - 1)
        f = add_unit(m, "feed", -260, y, f"Reservoir P-{k}", {"T_C": 85.0, "P_bar": Pr, "flow_basis": "kmol/h",
                                                              "flow": q, "composition": dict(WELL_FLUID)})
        w = add_unit(m, "well", -110, y, f"P-{k}", {"ipr": "Productivity index (liquid)", "PI": PI, "MD": 2700.0,
                                                    "TVD": 2500.0, "ID": 150.0})
        xt = add_unit(m, "xmas_tree", 60, y, f"XT-{k}", {"tree": "Vertical Xmas tree (VXT)", "P_out": 75.0})
        connect(m, f, "out", w, "in")
        connect(m, w, "out", xt, "in", f"P-{k} wellhead")
        connect(m, xt, "out", tmp, "in", f"XT-{k} outlet")
    meoh = add_unit(m, "feed", 360, -40, "Methanol (umbilical)", {"T_C": 6.0, "P_bar": 60.0, "flow_basis": "kg/h",
                                                                   "flow": 300.0, "comp_basis": "Mass fractions",
                                                                   "composition": {"MeOH": 1.0}})
    pi = add_unit(m, "intensifier", 470, -40, "PI-100 Intensifier", {"spec": "Area ratio", "ratio": 2.0, "P_hyd": 207.0})
    ci = add_unit(m, "cimv", 540, 168, "CI-100 Methanol injection")
    fl = add_unit(m, "flowline", 740, 160, "FL-100 Heated flowline",
                  {"design": "Electrically heated (DEH)", "length": 18000.0, "ID": 254.0, "dz": 60.0, "n_seg": 10,
                   "heating": "Direct electrical heating (DEH)", "heat_ctrl": "Hold minimum temperature",
                   "T_hold": 25.0, "q_max_W_m": 120.0})
    rs = add_unit(m, "riser", 930, 80, "RSR-100 Riser", {"rtype": "Steel catenary riser (SCR)", "depth": 300.0,
                                                         "ID": 254.0, "fl_len": 18000.0, "fl_incl": 0.2, "n_seg": 8})
    ar = add_unit(m, "separator3", 1110, 20, "V-100 Arrival sep")
    gas = add_unit(m, "product", 1290, -60, "Gas")
    oil = add_unit(m, "product", 1300, 110, "Oil")
    wat = add_unit(m, "product", 1170, 170, "Produced water")
    connect(m, meoh, "out", pi, "in")
    connect(m, pi, "out", ci, "chem", "Methanol at injection pressure")
    connect(m, tmp, "out", ci, "in", "Template outlet")
    connect(m, ci, "out", fl, "in", "Flowline inlet")
    connect(m, fl, "out", rs, "in", "Riser base")
    connect(m, rs, "out", ar, "feed", "Topside arrival")
    connect(m, ar, "vapour", gas, "in")
    connect(m, ar, "oil", oil, "in")
    connect(m, ar, "water", wat, "in")
    return m


def subsea_compression():
    """Subsea gas compression station (in the spirit of the Åsgard / Gullfaks systems): depleted gas wells →
    template → subsea cooler → gas-liquid separator → dry-gas compressor; liquids pumped by a subsea pump and
    recombined → 40 km pipe-in-pipe → lazy-wave riser → arrival."""
    m = new_model()
    tmp = add_unit(m, "template", 300, 200, "TMP-100 Template", {"template": "4-slot template"})
    for k, (Pr, q, C) in enumerate([(205.0, 1.0, 520.0), (200.0, 0.9, 480.0), (210.0, 1.1, 560.0),
                                     (198.0, 0.9, 500.0)], start=1):
        y = 20 + 120 * (k - 1)
        f = add_unit(m, "feed", -260, y, f"Reservoir W-{k}", {"T_C": 105.0, "P_bar": Pr, "flow_basis": "MSm³/d",
                                                              "flow": q, "composition": dict(WET_GAS)})
        w = add_unit(m, "well", -110, y, f"W-{k}", {"C": C, "n": 0.8, "MD": 3300.0, "TVD": 3000.0, "ID": 125.0})
        xt = add_unit(m, "xmas_tree", 60, y, f"XT-{k}", {"tree": "Horizontal Xmas tree (HXT)", "P_out": 62.0})
        connect(m, f, "out", w, "in")
        connect(m, w, "out", xt, "in", f"W-{k} wellhead")
        connect(m, xt, "out", tmp, "in", f"XT-{k} outlet")
    cl = add_unit(m, "subsea_cooler", 450, 200, "E-100 Subsea cooler", {"spec": "Approach to sea temperature",
                                                                         "approach": 22.0, "T_sea": 4.0})
    sep = add_unit(m, "subsea_separator", 600, 190, "V-100 Subsea separator",
                   {"sep_type": "Gas-liquid separator (vertical)"})
    k1 = add_unit(m, "subsea_compressor", 760, 90, "K-100 Subsea compressor",
                  {"btype": "Dry-gas centrifugal compressor", "dP": 55.0, "n_par": 2})
    pp = add_unit(m, "subsea_pump", 760, 320, "P-100 Subsea pump", {"btype": "Single-phase liquid pump", "dP": 56.0})
    mx = add_unit(m, "mixer", 900, 200, "MIX-100 Recombination")
    fl = add_unit(m, "flowline", 1060, 200, "FL-100 Flowline", {"design": "Pipe-in-pipe", "length": 40000.0,
                                                                   "ID": 356.0, "dz": -100.0, "n_seg": 12})
    rs = add_unit(m, "riser", 1220, 120, "RSR-100 Riser", {"rtype": "Lazy-wave (flexible)", "depth": 350.0,
                                                           "ID": 305.0, "fl_len": 40000.0, "n_seg": 9})
    ar = add_unit(m, "separator3", 1400, 60, "V-200 Arrival sep")
    gas = add_unit(m, "product", 1580, -30, "Gas to process")
    cond = add_unit(m, "product", 1600, 160, "Condensate")
    wat = add_unit(m, "product", 1470, 230, "Produced water")
    connect(m, tmp, "out", cl, "in", "Template outlet")
    connect(m, cl, "out", sep, "feed", "Cooled wellstream")
    connect(m, sep, "vapour", k1, "in", "Gas to compressor")
    connect(m, sep, "oil", pp, "in", "Liquid to pump")
    connect(m, k1, "out", mx, "in", "Compressed gas")
    connect(m, pp, "out", mx, "in", "Pumped liquid")
    connect(m, mx, "out", fl, "in", "Flowline inlet")
    connect(m, fl, "out", rs, "in", "Riser base")
    connect(m, rs, "out", ar, "feed", "Topside arrival")
    connect(m, ar, "vapour", gas, "in")
    connect(m, ar, "oil", cond, "in")
    connect(m, ar, "water", wat, "in")
    return m


def subsea_separation():
    """Subsea separation and water reinjection (in the spirit of Tordis / Marlim): high water-cut oil wells →
    template → liquid-liquid separator; water pumped back into the reservoir by a water injection pump; oil and
    gas boosted by a multiphase pump → 15 km wet-insulated flowline → SCR → arrival."""
    m = new_model()
    tmp = add_unit(m, "template", 300, 160, "TMP-100 Template", {"template": "4-slot template"})
    for k, (Pr, q, PI) in enumerate([(210.0, 1500.0, 120.0), (205.0, 1400.0, 110.0), (215.0, 1600.0, 130.0)], start=1):
        y = 20 + 140 * (k - 1)
        f = add_unit(m, "feed", -260, y, f"Reservoir P-{k}", {"T_C": 80.0, "P_bar": Pr, "flow_basis": "kmol/h",
                                                              "flow": q, "composition": dict(OIL_WC)})
        w = add_unit(m, "well", -110, y, f"P-{k}", {"ipr": "Productivity index (liquid)", "PI": PI, "MD": 2500.0,
                                                    "TVD": 2300.0, "ID": 150.0})
        xt = add_unit(m, "xmas_tree", 60, y, f"XT-{k}", {"tree": "Vertical Xmas tree (VXT)", "P_out": 30.0})
        connect(m, f, "out", w, "in")
        connect(m, w, "out", xt, "in", f"P-{k} wellhead")
        connect(m, xt, "out", tmp, "in", f"XT-{k} outlet")
    sep = add_unit(m, "subsea_separator", 470, 150, "V-100 Subsea separator",
                   {"sep_type": "Liquid-liquid separator (horizontal)"})
    mx = add_unit(m, "mixer", 640, 80, "MIX-100 Oil + gas")
    mpp = add_unit(m, "subsea_pump", 780, 80, "P-100 Multiphase pump", {"btype": "Helico-axial multiphase pump",
                                                                        "dP": 45.0})
    wip = add_unit(m, "subsea_pump", 640, 330, "P-200 Water injection pump", {"btype": "Water injection pump",
                                                                              "spec": "Outlet pressure", "P_out": 230.0})
    inj = add_unit(m, "product", 820, 330, "Water to injection well")
    fl = add_unit(m, "flowline", 950, 80, "FL-100 Flowline", {"design": "Wet insulation (multilayer PP)",
                                                                 "length": 15000.0, "ID": 305.0, "dz": -50.0,
                                                                 "n_seg": 10})
    rs = add_unit(m, "riser", 1110, 20, "RSR-100 Riser", {"rtype": "Steel catenary riser (SCR)", "depth": 250.0,
                                                          "ID": 254.0, "fl_len": 15000.0, "n_seg": 8})
    ar = add_unit(m, "separator3", 1290, -30, "V-200 Arrival sep")
    gas = add_unit(m, "product", 1470, -110, "Gas")
    oil = add_unit(m, "product", 1480, 60, "Oil")
    wat = add_unit(m, "product", 1350, 120, "Produced water (topside)")
    connect(m, tmp, "out", sep, "feed", "Template outlet")
    connect(m, sep, "vapour", mx, "in", "Separated gas")
    connect(m, sep, "oil", mx, "in", "Separated oil")
    connect(m, sep, "water", wip, "in", "Separated water")
    connect(m, wip, "out", inj, "in")
    connect(m, mx, "out", mpp, "in", "Oil + gas")
    connect(m, mpp, "out", fl, "in", "Flowline inlet")
    connect(m, fl, "out", rs, "in", "Riser base")
    connect(m, rs, "out", ar, "feed", "Topside arrival")
    connect(m, ar, "vapour", gas, "in")
    connect(m, ar, "oil", oil, "in")
    connect(m, ar, "water", wat, "in")
    return m



EXAMPLES = {
    "Two-stage gas compression with liquid recycle": compression_train,
    "Oil stabilisation: 3-stage separation + recompression": oil_stabilisation,
    "JT dew-point control: gas/gas exchanger + LTS + Adjust": jt_dewpoint,
    "Subsea tie-back: MEG injection + flowline + riser (Beggs & Brill, hydrate check)": subsea_tieback,
    "Condensate stabiliser column with TVP spec (Adjust)": condensate_stabiliser,
    "Subsea field (SURF): 4 wells, template, pipe-in-pipe flowline, lazy-wave riser": subsea_field,
    "Subsea boosting (SURF): late life, wet-gas compressor, 45 km step-out": subsea_field_boosted,
    "Heated flowline (SURF): DEH oil tie-back with methanol injection and an intensifier": heated_oil_tieback,
    "Subsea compression station (SURF): cooler, separator, compressor and pump": subsea_compression,
    "Subsea separation (SURF): water removal and reinjection, multiphase pump": subsea_separation,
}
