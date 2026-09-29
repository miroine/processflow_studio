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


EXAMPLES = {
    "Two-stage gas compression with liquid recycle": compression_train,
    "Oil stabilisation: 3-stage separation + recompression": oil_stabilisation,
    "JT dew-point control: gas/gas exchanger + LTS + Adjust": jt_dewpoint,
    "Subsea tie-back: MEG injection + flowline + riser (Beggs & Brill, hydrate check)": subsea_tieback,
    "Condensate stabiliser column with TVP spec (Adjust)": condensate_stabiliser,
}
