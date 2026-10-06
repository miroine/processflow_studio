"""Subsea production equipment (SURF) - Phase 1.

Unit operations for a subsea tie-back, all built on the tested pipe, valve and flash models:

* ``well``         - reservoir inflow (back-pressure, PI or Vogel IPR) + tubing lift (Beggs & Brill VLP)
* ``xmas_tree``    - tree valves + production choke, with a critical-flow and hydrate check
* ``template``     - production template / manifold: N well slots into one header
* ``jumper``       - rigid/flexible jumpers, spools, PLET and PLEM: short pipe + fitting losses
* ``flowline``     - flowline with a design preset (wet insulation, pipe-in-pipe, bundle, ...) and DEH
* ``riser``        - vertical, catenary and lazy-wave geometries + a riser-base slugging screen
* ``subsea_valve`` - SSIV / HIPPS / isolation valve, open or closed, with a HIPPS trip

Equipment data (U-values, roughness, slot counts, fitting losses, riser geometry, ...) come from a
catalogue.  The default ``data/surf_catalogue.csv`` holds *generic, illustrative* textbook values only;
a user can replace it with their own CSV (same columns).  An uploaded catalogue is stored in the model
(``model["surf_catalogue"]``) so it travels with the saved flowsheet.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os

from .thermo import T_STD, V_STD_GAS, FlashError
from .streams import EnergyStream, make_stream, zero_stream
from .unitops import (CATALOGUE, CALC, UnitError, K0, G, _f, _s, _one, _live, _mix, _ph, _check_P,
                      calc_pipe, pipe_gradient, _phase_split, _hyd_margin, _energy, _separate, _vapour_volume)

CATEGORY = "Subsea (SURF)"
DEFAULT_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "surf_catalogue.csv")
CATEGORIES = ("flowline", "riser", "tree", "template", "jumper", "valve", "booster", "separator", "process")
NUMERIC = ("U_W_m2K", "roughness_mm", "dP_bar", "slots", "header_ID_mm", "header_length_m", "K_bend", "K_extra",
           "length_m", "bends", "length_factor", "hog_frac", "sag_frac", "deh_W_m",
           "eff_pct", "min_gvf", "max_gvf", "max_dP_bar", "rated_kW", "motor_eff", "cost_MUSD", "sb_K", "res_min")
COLUMNS = ("category", "item", "description") + NUMERIC + ("note",)
P_STD = 1.01325


# --------------------------------------------------------------------------
# Catalogue
# --------------------------------------------------------------------------

def _num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return None if (isinstance(v, float) and math.isnan(v)) else float(v)
    t = str(v).strip()
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        raise ValueError(f"'{t}' is not a number")


def parse_csv(text, fill_missing=False):
    """Parse and validate catalogue CSV text into a list of row dicts.

    Columns that are absent are read as empty.  With ``fill_missing`` a category with no rows (e.g. a
    catalogue written for an older version, without boosters) is completed from the built-in catalogue;
    otherwise it is an error."""
    rd = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if rd.fieldnames is None:
        raise ValueError("The catalogue file is empty")
    cols = [c.strip() for c in rd.fieldnames]
    for req in ("category", "item"):
        if req not in cols:
            raise ValueError(f"The catalogue needs a '{req}' column (columns: {', '.join(COLUMNS)})")
    rows, seen = [], set()
    for k, raw in enumerate(rd, start=2):
        r = {(c or "").strip(): v for c, v in raw.items()}
        cat = (r.get("category") or "").strip().lower()
        item = (r.get("item") or "").strip()
        if not cat and not item:
            continue
        if cat not in CATEGORIES:
            raise ValueError(f"Line {k}: unknown category '{cat}' (use one of {', '.join(CATEGORIES)})")
        if not item:
            raise ValueError(f"Line {k}: the item name is empty")
        if (cat, item) in seen:
            raise ValueError(f"Line {k}: '{item}' appears twice in category {cat}")
        seen.add((cat, item))
        row = {"category": cat, "item": item, "description": (r.get("description") or "").strip(),
               "note": (r.get("note") or "").strip()}
        for c in NUMERIC:
            try:
                row[c] = _num(r.get(c))
            except ValueError as e:
                raise ValueError(f"Line {k}, column {c}: {e}")
        rows.append(row)
    missing = [c for c in CATEGORIES if not any(r["category"] == c for r in rows)]
    if missing and fill_missing and _DEFAULT_READY:
        rows += [dict(r) for r in _DEFAULT if r["category"] in missing]
    elif missing:
        raise ValueError("The catalogue has no rows for: " + ", ".join(missing))
    for r in rows:
        if r["category"] == "template" and not (r["slots"] and r["slots"] >= 1):
            raise ValueError(f"Template '{r['item']}' needs a slot count ≥ 1")
    return rows


def to_csv(rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(COLUMNS), lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({c: ("" if r.get(c) is None else (f"{r[c]:g}" if isinstance(r.get(c), float) else r.get(c)))
                    for c in COLUMNS})
    return buf.getvalue()


def default_rows():
    with open(DEFAULT_CSV, encoding="utf-8") as fh:
        return parse_csv(fh.read())


_DEFAULT_READY = False
try:
    _DEFAULT = default_rows()
    _DEFAULT_READY = True
except FileNotFoundError as _e:          # surfaced by app.py's guard as "files out of sync"
    raise ImportError(f"the SURF catalogue file is missing: {DEFAULT_CSV}") from _e
_ACTIVE = {"rows": _DEFAULT, "key": None, "index": {}}
# (unit type, parameter key) -> catalogue category, for select parameters fed by the catalogue
_CAT_PARAMS = {("xmas_tree", "tree"): "tree", ("template", "template"): "template", ("jumper", "kind"): "jumper",
               ("flowline", "design"): "flowline", ("riser", "rtype"): "riser", ("subsea_valve", "kind"): "valve",
               ("subsea_booster", "btype"): "booster", ("subsea_pump", "btype"): ("booster", "pump"),
               ("subsea_compressor", "btype"): ("booster", "compressor"),
               ("subsea_separator", "sep_type"): "separator"}
_ORIG_DEFAULT = {}


def _key(rows):
    return hashlib.sha1(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()


def rows():
    return _ACTIVE["rows"]


def filled_categories(text):
    """Categories an uploaded CSV lacks (they are taken from the built-in catalogue)."""
    present = {(r.get("category") or "").strip().lower() for r in csv.DictReader(io.StringIO(text.lstrip("\ufeff")))}
    return [c for c in CATEGORIES if c not in present]


def is_default():
    return _ACTIVE["rows"] is _DEFAULT or _key(_ACTIVE["rows"]) == _key(_DEFAULT)


def items(category):
    return [r["item"] for r in _ACTIVE["rows"] if r["category"] == category]


def booster_items(kind):
    """Catalogue boosters that are compressors (gas-volume-fraction window starting at ≥ 90 %) or pumps."""
    rows_ = [r for r in _ACTIVE["rows"] if r["category"] == "booster"]
    comp = [r["item"] for r in rows_ if (r.get("min_gvf") or 0.0) >= 0.9]
    return comp if kind == "compressor" else [r["item"] for r in rows_ if r["item"] not in comp]


def activate(custom_rows=None):
    """Make a catalogue active (None = the built-in default) and refresh the select options."""
    rws = custom_rows or _DEFAULT
    k = _key(rws)
    if k == _ACTIVE["key"]:
        return
    _ACTIVE.update(rows=rws, key=k,
                   index={(r["category"], r["item"]): r for r in rws})
    for (utype, pkey), cat in _CAT_PARAMS.items():
        if utype not in CATALOGUE:
            continue
        for spec in CATALOGUE[utype]["params"]:
            if spec["key"] == pkey:
                opts = items(cat) if isinstance(cat, str) else booster_items(cat[1])
                spec["options"][:] = opts
                orig = _ORIG_DEFAULT.setdefault((utype, pkey), spec["default"])
                spec["default"] = orig if orig in opts else (opts[0] if opts else "")


def item(category, name, unit_name=""):
    r = _ACTIVE["index"].get((category, name))
    if r is None:
        raise UnitError(f"{unit_name}: '{name}' is not in the active SURF catalogue ({category}); "
                        f"choose one of: {', '.join(items(category)) or 'none'}")
    return r


def _cat(row, col, default=None):
    v = row.get(col)
    return default if v is None else v


def _override(p, key, row, col, default=None):
    """User value if positive, else the catalogue value, else default."""
    v = float(p.get(key, 0.0) or 0.0)
    return v if v > 0 else _cat(row, col, default)


# --------------------------------------------------------------------------
# Catalogue entries (unit schema)
# --------------------------------------------------------------------------

IPR_GAS, IPR_PI, IPR_VOGEL = "Gas back-pressure (C, n)", "Productivity index (liquid)", "Vogel (oil)"


ONLINE, BYPASSED = "Online", "Bypassed"


def _booster_schema(label, prefix, options, default):
    return {
        "label": label, "prefix": prefix, "category": CATEGORY,
        "ports": {"in": {"in": {"multi": False}}, "out": {"out": {"multi": False}}},
        "params": [
                _s("btype", "Booster type", options, default,
                   help="The catalogue sets efficiency, gas-volume-fraction window, maximum boost and rated power"),
                _s("online", "Status", [ONLINE, BYPASSED], ONLINE,
                   help="Bypassed: the flow passes the machine unchanged (installed but not yet running, or spared) - "
                        "the field-life tab uses this to time the start of boosting"),
                _s("spec", "Specification", ["Pressure boost", "Outlet pressure", "Performance curve"],
                   "Pressure boost",
                   help="Performance curve: the curve at the actual flow per machine and the speed sets the boost"),
                _f("dP", "Pressure boost (total)", "bar", 30.0, {"spec": "Pressure boost"}, minv=0.1),
                _f("P_out", "Outlet pressure", "bar(a)", 150.0, {"spec": "Outlet pressure"}),
                _f("eff", "Efficiency (0 = catalogue or curve)", "%", 0.0, {"spec": ["Pressure boost", "Outlet pressure"]},
                   minv=0.0, maxv=100.0),
                _f("n_par", "Machines in parallel", "-", 1, minv=1, maxv=8),
                _f("n_ser", "Machines in series", "-", 1, minv=1, maxv=4),
                _f("speed", "Speed", "rpm", 3600.0, {"spec": "Performance curve"}, minv=1.0,
                   help="Fan laws scale the design-speed curve: flow ~ N, boost ~ N²"),
                _f("N_design", "Curve (design) speed", "rpm", 3600.0, {"spec": "Performance curve"}, minv=1.0),
            ],
    }


def _schema():
    return {
        "well": {
            "label": "Well (IPR + tubing)", "prefix": "W", "category": CATEGORY,
            "ports": {"in": {"in": {"multi": False}, "lift": {"multi": False, "optional": True}},
                      "out": {"out": {"multi": False}}},
            "params": [
                _s("ipr", "Inflow model (IPR)", [IPR_GAS, IPR_PI, IPR_VOGEL], IPR_GAS,
                   help="Connect a feed at reservoir pressure and temperature (its composition, P and T are the "
                        "reservoir's)"),
                _f("C", "Back-pressure coefficient C", "Sm³/d/bar²ⁿ", 500.0, {"ipr": IPR_GAS}, minv=1e-9,
                   help="q_gas = C (Pr² − Pwf²)ⁿ with q in Sm³/d and pressures in bar"),
                _f("n", "Back-pressure exponent n", "-", 0.8, {"ipr": IPR_GAS}, minv=0.5, maxv=1.0),
                _f("PI", "Productivity index (liquid at standard conditions)", "Sm³/d/bar", 50.0, {"ipr": IPR_PI},
                   minv=1e-9),
                _f("qmax", "Vogel maximum oil rate (AOF)", "Sm³/d", 3000.0, {"ipr": IPR_VOGEL}, minv=1e-9),
                _s("rate_spec", "Rate", [RATE_FEED, RATE_WHP], RATE_FEED,
                   help="From the feed: the connected feed's flow is the rate. Wellhead pressure: the well solves "
                        "its own rate for the wellhead pressure and writes it back to the feed stream"),
                _f("WHP", "Wellhead pressure", "bar(a)", 150.0, {"rate_spec": RATE_WHP}, minv=1.0),
                _f("n_par", "Identical wells", "-", 1, minv=1, maxv=40,
                   help="Wells with the same inflow and tubing producing in parallel (the rate is split equally): "
                        "one unit can stand for a cluster of wells; the field-life tab varies it to find the well count"),
                _f("MD", "Tubing length (measured depth)", "m", 3500.0, minv=1.0),
                _f("TVD", "True vertical depth, reservoir to wellhead", "m", 3000.0, minv=0.0),
                _f("ID", "Tubing inner diameter", "mm", 125.0, minv=10.0),
                _f("rough", "Tubing roughness", "mm", 0.045, minv=0.0),
                _f("U", "Overall U, tubing to formation", "W/m²·K", 15.0, minv=0.0),
                _f("T_wh_amb", "Ambient at the wellhead (seabed)", "°C", 4.0,
                   help="Ambient runs linearly from reservoir T at the bottom to this value (geothermal gradient)"),
                _f("n_seg", "Calculation increments", "-", 12, minv=2, maxv=100),
                _f("gl_depth", "Gas-lift valve depth (MD from the wellhead)", "m", 2000.0, minv=1.0,
                   help="Used when a lift-gas stream is connected to the 'lift' port: the gas (split equally over "
                        "identical wells) is injected into the tubing at this depth"),
            ],
        },
        "xmas_tree": {
            "label": "Subsea Xmas tree + choke", "prefix": "XT", "category": CATEGORY,
            "ports": {"in": {"in": {"multi": False}}, "out": {"out": {"multi": False}}},
            "params": [
                _s("tree", "Tree type", items("tree"), "Horizontal Xmas tree (HXT)"),
                _f("dP_tree", "Tree valve ΔP (0 = catalogue)", "bar", 0.0, minv=0.0),
                _s("spec", "Choke specification", ["Outlet pressure", "Choke pressure drop", "Choke fully open",
                                                    CHOKE_CV], "Outlet pressure",
                   help="Choke opening: the pressure drop follows from the flow through the choke's Cv at that opening "
                        "(equal-percentage trim)"),
                _f("P_out", "Choke outlet pressure", "bar(a)", 150.0, {"spec": "Outlet pressure"}),
                _f("dP", "Choke pressure drop", "bar", 20.0, {"spec": "Choke pressure drop"}, minv=0.0),
                _f("Cv_max", "Choke Cv fully open", "US gpm/psi½", 250.0, {"spec": CHOKE_CV}, minv=0.01),
                _f("opening", "Choke opening", "%", 60.0, {"spec": CHOKE_CV}, minv=1.0, maxv=100.0),
                _f("rangeability", "Trim rangeability (equal %)", "-", 50.0, {"spec": CHOKE_CV}, minv=2.0),
            ],
        },
        "template": {
            "label": "Template / manifold", "prefix": "TMP", "category": CATEGORY,
            "ports": {"in": {"in": {"multi": True}}, "out": {"out": {"multi": False}}},
            "params": [
                _s("template", "Template", items("template"), "4-slot template"),
                _f("hdr_ID", "Header inner diameter (0 = catalogue)", "mm", 0.0, minv=0.0),
                _f("hdr_L", "Header length (0 = catalogue)", "m", 0.0, minv=0.0),
                _f("K", "Header fittings K (0 = catalogue)", "-", 0.0, minv=0.0),
            ],
        },
        "jumper": {
            "label": "Jumper / spool / PLET / PLEM", "prefix": "J", "category": CATEGORY,
            "ports": {"in": {"in": {"multi": False}}, "out": {"out": {"multi": False}}},
            "params": [
                _s("kind", "Component", items("jumper"), "Rigid M-shape jumper"),
                _f("length", "Length (0 = catalogue)", "m", 0.0, minv=0.0),
                _f("ID", "Inner diameter", "mm", 254.0, minv=1.0),
                _f("dz", "Elevation change (outlet − inlet)", "m", 0.0),
                _f("bends", "Number of bends (−1 = catalogue)", "-", -1.0, minv=-1.0, maxv=20.0),
                _f("rough", "Roughness (0 = catalogue)", "mm", 0.0, minv=0.0),
            ],
        },
        "flowline": {
            "label": "Subsea flowline", "prefix": "FL", "category": CATEGORY,
            "ports": {"in": {"in": {"multi": False}}, "out": {"out": {"multi": False}}},
            "params": [
                _s("design", "Flowline design", items("flowline"), "Pipe-in-pipe"),
                _s("method", "Correlation", ["Beggs & Brill", "Homogeneous (no-slip)"], "Beggs & Brill"),
                _f("length", "Length", "m", 25000.0, minv=1.0),
                _f("ID", "Inner diameter", "mm", 305.0, minv=1.0),
                _f("dz", "Elevation change (outlet − inlet)", "m", 0.0),
                _f("U", "Overall U (0 = catalogue)", "W/m²·K", 0.0, minv=0.0),
                _f("rough", "Roughness (0 = catalogue)", "mm", 0.0, minv=0.0),
                _f("T_amb", "Seabed temperature", "°C", 4.0),
                _s("heating", "Heating system", HEATING, HEAT_NONE,
                   help="DEH: AC current through the pipe wall; ETH-PiP: heating cables in the pipe-in-pipe annulus; "
                        "hot water: circulated from a topside heater through a bundle"),
                _s("heat_ctrl", "Heating control", [CTRL_FIXED, CTRL_HOLD], CTRL_FIXED,
                   {"heating": HEATING[1:]},
                   help="Fixed: a constant W/m along the line. Hold: just enough heat to keep the fluid at or above "
                        "the set temperature, up to the installed W/m"),
                _f("deh_W_m", "Heat input (0 = catalogue)", "W/m", 0.0, {"heat_ctrl": CTRL_FIXED}, minv=0.0),
                _f("T_hold", "Minimum fluid temperature", "°C", 25.0, {"heat_ctrl": CTRL_HOLD},
                   help="Typically hydrate temperature + margin, or above the wax appearance temperature"),
                _f("q_max_W_m", "Installed heating capacity", "W/m", 150.0, {"heating": HEATING[1:]}, minv=0.0),
                _f("heat_eff", "Heating system efficiency (0 = typical)", "%", 0.0, {"heating": HEATING[1:]},
                   minv=0.0, maxv=100.0,
                   help="Heat into the fluid / power drawn. Typical: DEH 60 %, ETH-PiP 90 %, hot water 70 %"),
                _f("n_seg", "Calculation increments", "-", 12, minv=1, maxv=200),
            ],
        },
        "riser": {
            "label": "Riser", "prefix": "RSR", "category": CATEGORY,
            "ports": {"in": {"in": {"multi": False}}, "out": {"out": {"multi": False}}},
            "params": [
                _s("rtype", "Riser type", items("riser"), "Lazy-wave (flexible)"),
                _f("depth", "Water depth", "m", 350.0, minv=1.0),
                _f("ID", "Inner diameter", "mm", 254.0, minv=1.0),
                _f("lf", "Riser length / water depth (0 = catalogue)", "-", 0.0, minv=0.0),
                _f("U", "Overall U (0 = catalogue)", "W/m²·K", 0.0, minv=0.0),
                _f("rough", "Roughness (0 = catalogue)", "mm", 0.0, minv=0.0),
                _f("T_amb", "Average sea temperature", "°C", 6.0),
                _f("n_seg", "Calculation increments (total)", "-", 12, minv=3, maxv=200),
                _f("fl_len", "Upstream flowline length (slugging screen)", "m", 25000.0, minv=0.0),
                _f("fl_incl", "Flowline inclination at the riser base", "°", -0.3, minv=-10.0, maxv=10.0,
                   help="Negative = downhill towards the riser base, which favours severe slugging"),
            ],
        },
        "subsea_booster": _booster_schema("Subsea booster (pump / compressor)", "P", items("booster"),
                                          "Helico-axial multiphase pump"),
        "subsea_pump": dict(_booster_schema("Subsea pump", "P", booster_items("pump"), "Helico-axial multiphase pump")),
        "subsea_compressor": dict(_booster_schema("Subsea compressor", "K", booster_items("compressor"),
                                                  "Wet-gas compressor")),
        "subsea_separator": {
            "label": "Subsea separator", "prefix": "V", "category": CATEGORY,
            "ports": {"in": {"feed": {"multi": True}},
                      "out": {"vapour": {"multi": False}, "oil": {"multi": False}, "water": {"multi": False}}},
            "params": [
                _s("sep_type", "Separator type", items("separator"), "Gas-liquid separator (vertical)",
                   help="Gas-liquid: ahead of a subsea compressor and pump. Liquid-liquid: removes water for "
                        "reinjection. Outlets: gas (top), oil / liquid (right), water (bottom, optional)"),
                _f("dP", "Pressure drop", "bar", 0.3, minv=0.0),
                _f("res_min", "Liquid residence time (0 = catalogue)", "min", 0.0, minv=0.0),
                _f("P_design", "Design pressure (0 = 1.1 × operating)", "bar(a)", 0.0, minv=0.0),
            ],
        },
        "subsea_cooler": {
            "label": "Subsea cooler", "prefix": "E", "category": CATEGORY,
            "ports": {"in": {"in": {"multi": False}}, "out": {"out": {"multi": False}}},
            "params": [
                _s("spec", "Specification", ["Outlet temperature", "Approach to sea temperature", "Cooler area"],
                   "Approach to sea temperature",
                   help="A passive cooler rejects heat to the sea by natural convection; the outlet can never be "
                        "colder than the sea"),
                _f("T_out", "Outlet temperature", "°C", 25.0, {"spec": "Outlet temperature"}),
                _f("approach", "Approach to sea temperature", "°C", 10.0, {"spec": "Approach to sea temperature"},
                   minv=0.1),
                _f("area", "Cooler area", "m²", 300.0, {"spec": "Cooler area"}, minv=0.1),
                _f("U", "Overall U (0 = catalogue)", "W/m²·K", 0.0, minv=0.0),
                _f("T_sea", "Sea temperature", "°C", 4.0),
                _f("dP", "Pressure drop", "bar", 0.5, minv=0.0),
            ],
        },
        "intensifier": {
            "label": "Pressure intensifier", "prefix": "PI", "category": CATEGORY,
            "ports": {"in": {"in": {"multi": False}}, "out": {"out": {"multi": False}}},
            "params": [
                _s("spec", "Specification", ["Area ratio", "Outlet pressure"], "Area ratio",
                   help="Hydraulically driven reciprocating booster: outlet pressure = hydraulic supply × area "
                        "ratio × mechanical efficiency"),
                _f("ratio", "Area ratio", "-", 2.0, {"spec": "Area ratio"}, minv=1.0, maxv=50.0),
                _f("P_out", "Outlet pressure", "bar(a)", 450.0, {"spec": "Outlet pressure"}),
                _f("P_hyd", "Hydraulic supply pressure", "bar(a)", 345.0, minv=1.0),
                _f("eff", "Mechanical efficiency (0 = catalogue)", "%", 0.0, minv=0.0, maxv=100.0),
            ],
        },
        "cimv": {
            "label": "Chemical injection valve (CIMV)", "prefix": "CI", "category": CATEGORY,
            "ports": {"in": {"in": {"multi": False}, "chem": {"multi": False}}, "out": {"out": {"multi": False}}},
            "params": [
                _f("dP_min", "Minimum ΔP across the valve (0 = catalogue)", "bar", 0.0, minv=0.0,
                   help="The chemical must arrive at least this much above the production pressure"),
            ],
        },
        "injection_well": {
            "label": "Water injection well", "prefix": "IW", "category": CATEGORY,
            "ports": {"in": {"in": {"multi": False}}, "out": {"out": {"multi": False, "optional": True}}},
            "params": [
                _f("II", "Injectivity index (per well)", "Sm³/d/bar", 50.0, minv=1e-6,
                   help="q_water = II (P_bottomhole − P_reservoir) at standard conditions"),
                _f("P_res", "Reservoir pressure at the injector", "bar(a)", 250.0, minv=1.0),
                _f("T_res", "Reservoir temperature", "°C", 90.0),
                _f("n_par", "Identical wells", "-", 1, minv=1, maxv=40),
                _f("MD", "Tubing length (measured depth)", "m", 3000.0, minv=1.0),
                _f("TVD", "True vertical depth, wellhead to reservoir", "m", 2700.0, minv=0.0),
                _f("ID", "Tubing inner diameter", "mm", 125.0, minv=10.0),
                _f("rough", "Tubing roughness", "mm", 0.045, minv=0.0),
                _f("U", "Overall U, tubing to formation", "W/m²·K", 15.0, minv=0.0),
                _f("n_seg", "Calculation increments", "-", 10, minv=2, maxv=100),
            ],
        },
        "subsea_valve": {
            "label": "SSIV / HIPPS valve", "prefix": "XV", "category": CATEGORY,
            "ports": {"in": {"in": {"multi": False}}, "out": {"out": {"multi": False}}},
            "params": [
                _s("kind", "Valve", items("valve"), "SSIV"),
                _s("state", "Position", ["Open", "Closed"], "Open"),
                _f("dP", "Open pressure drop (0 = catalogue)", "bar", 0.0, minv=0.0),
                _s("hipps", "HIPPS trip", ["Off", "On"], "Off"),
                _f("P_trip", "Trip pressure", "bar(a)", 200.0, {"hipps": "On"}, minv=1.0),
            ],
        },
    }


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def standard_rates(st, fp):
    """(gas MSm³/d, oil Sm³/d, water Sm³/d) from a flash of the stream at standard conditions."""
    fr = fp.pt_flash(st.z, T_STD, P_STD)
    gas = oil = wat = 0.0
    for ph in fr.phases:
        if ph.kind == "V":
            gas += st.F * ph.beta * V_STD_GAS * 24.0 / 1e6
        else:
            v = st.F * ph.beta * 1000.0 * ph.Vs * 24.0
            if ph.kind == "W":
                wat += v
            else:
                oil += v
    return gas, oil, wat


def _merge_profiles(dst, src):
    """Append a pipe profile to another, dropping the duplicated junction point."""
    if not dst:
        dst.update({k: list(v) for k, v in src.items()})
        return dst
    for k, v in src.items():
        dst.setdefault(k, [])
        dst[k].extend(v if k == "q_heat" else v[1:])      # q_heat is per increment, not per node
    return dst


def _pipe_unit(unit, params):
    return {"name": unit["name"], "type": "pipe", "energy_name": unit.get("energy_name"),
            "params": dict({"method": "Beggs & Brill", "sigma": 0.02, "heat": "Adiabatic"}, **params)}


def _elev_note(res, dz):
    res["Elevation change [m]"] = dz
    return res


# --------------------------------------------------------------------------
# Well: IPR + tubing VLP
# --------------------------------------------------------------------------

def bottomhole_pressure(ipr, Pr, q_gas_Sm3d, q_oil, q_liq, p):
    """(Pwf, AOF, AOF unit label) for the IPR; rates in Sm³/d."""
    if ipr == IPR_GAS:
        C, n = float(p["C"]), float(p["n"])
        aof = C * Pr ** (2.0 * n)
        if q_gas_Sm3d <= 0:
            raise UnitError("the gas back-pressure IPR needs gas at standard conditions")
        if q_gas_Sm3d >= aof:
            raise UnitError(f"gas rate {q_gas_Sm3d / 1e6:.3f} MSm³/d exceeds the absolute open flow "
                            f"{aof / 1e6:.3f} MSm³/d")
        return math.sqrt(Pr * Pr - (q_gas_Sm3d / C) ** (1.0 / n)), aof / 1e6, "AOF gas [MSm³/d]"
    if ipr == IPR_PI:
        PI = float(p["PI"])
        aof = PI * Pr
        if q_liq >= aof:
            raise UnitError(f"liquid rate {q_liq:.0f} Sm³/d exceeds the absolute open flow {aof:.0f} Sm³/d")
        return Pr - q_liq / PI, aof, "AOF liquid [Sm³/d]"
    qmax = float(p["qmax"])
    q = q_oil if q_oil > 0 else q_liq
    r = q / qmax
    if r >= 1.0:
        raise UnitError(f"oil rate {q:.0f} Sm³/d exceeds the Vogel maximum rate {qmax:.0f} Sm³/d")
    x = (-0.2 + math.sqrt(0.04 + 3.2 * (1.0 - r))) / 1.6
    return x * Pr, qmax, "AOF oil [Sm³/d]"


RATE_FEED, RATE_WHP = "From the feed", "Wellhead pressure"
CHOKE_CV = "Choke opening (Cv)"
HEAT_NONE = "None"
HEATING = [HEAT_NONE, "Direct electrical heating (DEH)", "Heat-traced pipe-in-pipe (ETH-PiP)",
           "Hot-water circulation (bundle)"]
HEAT_EFF = {HEATING[1]: 60.0, HEATING[2]: 90.0, HEATING[3]: 70.0}
CTRL_FIXED, CTRL_HOLD = "Fixed heat input", "Hold minimum temperature"


def n_wells(unit):
    """Identical wells in parallel represented by a well unit."""
    return max(1, int(round(float(unit["params"].get("n_par", 1) or 1))))


def _well_state(unit, s, fp, n_seg=None, lift=None):
    """Inflow + tubing lift for the inlet (reservoir) stream s at its flow (shared equally by n_par identical
    wells), with optional gas lift injected at the valve depth. Returns a dict of intermediate results; rates are
    per well, the outlet stream carries the total."""
    p = unit["params"]
    nw = n_wells(unit)
    Ftot = s.F
    if lift is not None and lift.empty:
        lift = None
    if nw > 1:
        s = make_stream("", fp, s.F / nw, s.z, s.flash)
    Pr, Tr = s.P, s.T
    gas, oil, wat = standard_rates(s, fp)
    Pwf, aof, aof_lbl = bottomhole_pressure(p.get("ipr", IPR_GAS), Pr, gas * 1e6, oil, oil + wat, p)
    if Pwf <= 1.0:
        raise UnitError(f"bottomhole flowing pressure {Pwf:.2f} bar is not physical")
    MD, TVD = float(p["MD"]), float(p["TVD"])
    if TVD > MD:
        raise UnitError(f"TVD {TVD:.0f} m exceeds the measured depth {MD:.0f} m")
    sf = make_stream("", fp, s.F, s.z, fp.pt_flash(s.z, Tr, Pwf, s.flash.Kset))
    U = float(p.get("U", 0.0))
    nseg = n_seg or p.get("n_seg", 12)
    heat = "Overall U to ambient" if U > 0 else "Adiabatic"
    T_wh = float(p.get("T_wh_amb", 4.0))
    gl = {}
    if lift is None:
        tub = _pipe_unit(unit, {"length": MD, "ID": p["ID"], "rough": p["rough"], "dz": TVD, "n_seg": nseg,
                                "heat": heat, "U": U, "T_amb": Tr - K0, "T_amb_out": T_wh})
        outs, rp, en = calc_pipe(tub, {"in": [sf]}, fp)
        out = outs["out"][0]
        profile = tub["_profile"]
    else:
        D = min(max(float(p.get("gl_depth", 2000.0)), 1.0), MD - 1.0)
        f_lo = (MD - D) / MD                                   # share of the tubing below the valve
        T_valve = Tr - K0 + (T_wh - (Tr - K0)) * f_lo
        n1 = max(2, int(round(nseg * f_lo)))
        lo = _pipe_unit(unit, {"length": MD - D, "ID": p["ID"], "rough": p["rough"], "dz": TVD * f_lo, "n_seg": n1,
                               "heat": heat, "U": U, "T_amb": Tr - K0, "T_amb_out": T_valve})
        o1, r1, e1 = calc_pipe(lo, {"in": [sf]}, fp)
        st1 = o1["out"][0]
        Fl = lift.F / nw
        rho_g = Fl > 0 and lift.F * lift.MW / 3600.0 / max(sum(lift.F * ph.beta * ph.Vs for ph in lift.flash.phases) * 1000.0 / 3600.0, 1e-12)
        P_inj = lift.P + float(rho_g) * G * TVD * (1.0 - f_lo) / 1e5      # gas column in the annulus
        if P_inj < st1.P:
            raise UnitError(f"lift gas reaches the valve at {P_inj:.1f} bar, below the tubing pressure {st1.P:.1f} bar "
                            "there: raise the lift-gas pressure or set the valve shallower")
        F2 = st1.F + Fl
        z2 = (st1.F * st1.z + Fl * lift.z) / F2
        h_pe = lift.MW / 1000.0 * G * TVD * (1.0 - f_lo)                   # J/mol gained down the annulus
        H2 = (st1.F * st1.H + Fl * (lift.H + h_pe)) / F2
        fr = _ph(fp, z2, st1.P, H2, st1.T)
        mix = make_stream("", fp, F2, z2, fr)
        up = _pipe_unit(unit, {"length": D, "ID": p["ID"], "rough": p["rough"], "dz": TVD * (1.0 - f_lo),
                               "n_seg": max(2, nseg - n1), "heat": heat, "U": U, "T_amb": T_valve, "T_amb_out": T_wh,
                               "z0": TVD * f_lo, "L0": MD - D})
        o2, r2, e2 = calc_pipe(up, {"in": [mix]}, fp)
        out = o2["out"][0]
        profile = _merge_profiles({}, lo["_profile"])
        _merge_profiles(profile, up["_profile"])
        rp = dict(r2)
        rp.update({"Pressure drop [bar]": sf.P - out.P, "Heat loss [kW]": r1["Heat loss [kW]"] + r2["Heat loss [kW]"],
                   "Erosional velocity ratio (API RP 14E, C=100)": max(r1["Erosional velocity ratio (API RP 14E, C=100)"],
                                                                     r2["Erosional velocity ratio (API RP 14E, C=100)"]),
                   "Flow regime (dominant)": r2["Flow regime (dominant)"]})
        hm = [x for x in profile.get("Hm", []) if x is not None]
        if hm:
            rp["Min. hydrate margin along line [°C]"] = min(hm)
        en = [EnergyStream(e.name, sum(x.duty_kW for x in e1 + e2 if x.name == e.name), e.unit, e.kind) for e in e2]
        gl = {"Gas-lift valve depth (MD) [m]": D, "Tubing P at the valve [bar(a)]": st1.P,
              "Lift gas at the valve [bar(a)]": P_inj, "Gas-lift rate [MSm³/d]": lift.F * V_STD_GAS * 24.0 / 1e6,
              "Gas-lift rate per well [MSm³/d]": Fl * V_STD_GAS * 24.0 / 1e6}
    if nw > 1:
        out = make_stream("", fp, out.F * nw, out.z, out.flash)
        sf = make_stream("", fp, Ftot, sf.z, sf.flash)
        en = [EnergyStream(e.name, e.duty_kW * nw, e.unit, e.kind) for e in en]
    return {"Pwf": Pwf, "aof": aof, "aof_lbl": aof_lbl, "gas": gas, "oil": oil, "wat": wat, "sf": sf,
            "out": out, "rp": rp, "en": en, "profile": profile, "TVD": TVD, "nw": nw, "gl": gl}


def _scaled_stream(s, fp, F):
    return make_stream("", fp, F, s.z, s.flash)


def whp_at_rate(unit, s, fp, F, n_seg=None, lift=None):
    """Wellhead pressure at molar rate F (None if the well cannot deliver F)."""
    try:
        return _well_state(unit, _scaled_stream(s, fp, F), fp, n_seg, lift)["out"].P
    except (UnitError, FlashError, ValueError, ZeroDivisionError):
        return None


def solve_rate_for_whp(unit, s, fp, target, tol_bar=0.02, lift=None):
    """Molar rate at which the wellhead pressure equals target.

    Bracket from the feed rate (×/÷ 1.5 steps), Illinois false position on coarse tubing increments to 0.3 bar,
    then Newton steps on the full increments with the bracket's slope.  Where the WHP curve turns over at low rate
    (liquid loading) the root on the stable, higher-rate branch is returned."""
    n_full = int(round(unit["params"].get("n_seg", 12)))
    n_c = max(3, min(6, n_full))
    FAIL = -1e6

    def g(F, n=n_c):
        w = whp_at_rate(unit, s, fp, F, n, lift)
        return (w - target) if w is not None else FAIL       # cannot deliver F: "too much flow"

    F0 = max(s.F, 1e-6)
    g0 = g(F0)
    lo = hi = None
    if g0 > 0:
        lo, glo, F = F0, g0, F0
        for _ in range(25):
            F *= 1.5
            gf = g(F)
            if gf <= 0:
                hi, ghi = F, gf
                break
            lo, glo = F, gf
    else:
        hi, ghi, F = F0, g0, F0
        for _ in range(8):                                   # down to ~4 % of the feed rate
            F /= 1.5
            gf = g(F)
            if gf > 0:
                lo, glo = F, gf
                break
            if gf > FAIL / 2:
                hi, ghi = F, gf
            elif hi == F0:
                hi, ghi = F, gf
    if lo is None or hi is None:
        raise UnitError(f"no rate gives a wellhead pressure of {target:.1f} bar "
                        f"({'the target is above the highest wellhead pressure the well reaches' if lo is None else 'the well cannot flow enough to lower it that far'})")
    raw = {lo: glo, hi: ghi}
    side, F, gf = 0, lo, glo
    for _ in range(40):
        F = 0.5 * (lo + hi) if ghi < FAIL / 2 else hi - ghi * (hi - lo) / (ghi - glo)
        gf = g(F)
        raw[F] = gf
        if abs(gf) < 0.3 or abs(hi - lo) < 1e-7 * hi:
            break
        if gf > 0:
            lo, glo = F, gf
            if side == 1:
                ghi *= 0.5
            side = 1
        else:
            hi, ghi = F, gf
            if side == -1:
                glo *= 0.5
            side = -1
    pts = sorted((x, y) for x, y in raw.items() if y > FAIL / 2)
    near = sorted(pts, key=lambda t: abs(t[0] - F))[:2]
    slope = (near[1][1] - near[0][1]) / (near[1][0] - near[0][0]) if len(near) == 2 and near[1][0] != near[0][0] else None
    if n_full != n_c and slope:                               # Newton polish on the full tubing increments
        g1 = g(F, n_full)
        for _ in range(5):
            if abs(g1) < tol_bar or g1 < FAIL / 2:
                break
            F2 = F - g1 / slope
            g2 = g(F2, n_full)
            if g2 < FAIL / 2:
                break
            if abs(F2 - F) > 1e-12:
                slope = (g2 - g1) / (F2 - F) if g2 != g1 else slope
            F, g1 = F2, g2
    return F


def calc_well(unit, ins, fp):
    p = unit["params"]
    s = _one(ins, "in")
    if s.empty:
        return {"out": [zero_stream("", fp, s.z, s.T, s.P)]}, {"Status": "No flow"}, []
    spec = p.get("rate_spec", RATE_FEED)
    lift = (ins.get("lift") or [None])[0]
    try:
        if spec == RATE_WHP:
            F = solve_rate_for_whp(unit, s, fp, float(p["WHP"]), lift=lift)
            s = _scaled_stream(s, fp, F)
            unit["_inlet_F"] = F                  # the flowsheet writes the rate back to the feed stream
        w = _well_state(unit, s, fp, lift=lift)
    except UnitError as e:
        raise UnitError(f"{unit['name']}: {e}")
    unit["_profile"] = w["profile"]
    out, rp = w["out"], w["rp"]
    Pr, Tr = s.P, s.T
    Q_res = w["sf"].heat_flow_kW - s.heat_flow_kW          # isothermal inflow: heat from the reservoir rock
    nw = w["nw"]
    gas, oil, wat, Pwf = w["gas"] * nw, w["oil"] * nw, w["wat"] * nw, w["Pwf"]
    res = {"Rate specification": "solved for the wellhead pressure" if spec == RATE_WHP else "feed flow",
           "Reservoir P [bar(a)]": Pr, "Reservoir T [°C]": Tr - K0, "Bottomhole flowing P [bar(a)]": Pwf,
           "Drawdown [bar]": Pr - Pwf, w["aof_lbl"]: w["aof"],
           "Gas rate [MSm³/d]": gas, "Oil/condensate rate [Sm³/d]": oil, "Water rate [Sm³/d]": wat,
           "Molar rate [kmol/h]": s.F}
    res.update(w["gl"])
    if nw > 1:
        res.update({"Identical wells": nw, "Gas rate per well [MSm³/d]": w["gas"],
                    "Oil/condensate rate per well [Sm³/d]": w["oil"], "Water rate per well [Sm³/d]": w["wat"]})
    if oil > 1e-9:
        res["GOR [Sm³/Sm³]"] = gas * 1e6 / oil
    if oil + wat > 1e-9:
        res["Water cut [%]"] = 100.0 * wat / (oil + wat)
    res.update({"Wellhead P [bar(a)]": out.P, "Wellhead T [°C]": out.T - K0, "Tubing ΔP [bar]": Pwf - out.P,
                "Flow regime (dominant)": rp["Flow regime (dominant)"],
                "Wellhead mixture velocity [m/s]": rp["Outlet mixture velocity [m/s]"],
                "Erosional velocity ratio (API RP 14E, C=100)": rp["Erosional velocity ratio (API RP 14E, C=100)"],
                "Heat loss to formation [kW]": rp["Heat loss [kW]"] * nw})
    if "Min. hydrate margin along line [°C]" in rp:
        res["Min. hydrate margin along line [°C]"] = rp["Min. hydrate margin along line [°C]"]
    if "Warning" in rp:
        res["Warning"] = rp["Warning"]
    _elev_note(res, w["TVD"])
    en = [EnergyStream(f"Q-{unit['name']} reservoir", Q_res, unit["name"], "heat")] + w["en"]
    return {"out": [out]}, res, en


def deliverability_curve(unit, s, fp):
    """Wellhead pressure vs rate from 10 % to 200 % of the current rate, stopping where the well can no longer
    deliver (for the property view; coarse tubing increments)."""
    try:
        w0 = _well_state(unit, s, fp, 6)
    except (UnitError, FlashError):
        return []
    lbl = w0["aof_lbl"]
    nw = w0["nw"]
    q0 = w0["gas"] if lbl.startswith("AOF gas") else (w0["oil"] if lbl.startswith("AOF oil") else w0["oil"] + w0["wat"])
    if q0 <= 0 or s.F <= 0:
        return []
    pts = []
    for fac in (0.1, 0.25, 0.4, 0.55, 0.7, 0.85, 1.0, 1.15, 1.3, 1.5, 1.75, 2.0):
        F = s.F * fac
        if fac * q0 >= w0["aof"]:                  # beyond the open-flow potential
            break
        whp = whp_at_rate(unit, s, fp, F, 6)
        if whp is None:
            pts.append({"Molar rate [kmol/h]": F, "Gas rate [MSm³/d]": nw * F * w0["gas"] / s.F,
                        "Liquid rate [Sm³/d]": nw * F * (w0["oil"] + w0["wat"]) / s.F, "Wellhead P [bar(a)]": None})
            break                                  # beyond the deliverable rate
        pts.append({"Molar rate [kmol/h]": F, "Gas rate [MSm³/d]": nw * F * w0["gas"] / s.F,
                    "Liquid rate [Sm³/d]": nw * F * (w0["oil"] + w0["wat"]) / s.F, "Wellhead P [bar(a)]": whp})
    return pts


# --------------------------------------------------------------------------
# Xmas tree + production choke
# --------------------------------------------------------------------------

def critical_ratio(k):
    """Critical (sonic) pressure ratio P2/P1 for an ideal gas with heat-capacity ratio k."""
    k = max(k, 1.01)
    return (2.0 / (k + 1.0)) ** (k / (k - 1.0))


def choke_cv(p):
    """Cv at the opening for an equal-percentage trim: Cv = Cv_max · R^(opening − 1)."""
    R = max(float(p.get("rangeability", 50.0)), 1.0001)
    return float(p["Cv_max"]) * R ** (float(p.get("opening", 100.0)) / 100.0 - 1.0)


def choke_dp(s, P1, cv, xT=0.70):
    """(choke ΔP [bar], choked) for stream s entering at P1: homogeneous mixture, ISA-style sizing
    Q = Kv · Y · sqrt(ΔP / SG) with Kv = Cv / 1.156, expansion factor Y = 1 − x / (3 F_k x_T) weighted by the gas
    volume fraction, and choked flow at x = F_k x_T (screening for multiphase chokes)."""
    q = sum(s.F * ph.beta * ph.Vs for ph in s.flash.phases) * 1000.0           # m³/h at inlet
    rho = s.F * s.MW / q if q > 0 else 1000.0
    sg = rho / 1000.0
    kv = cv / 1.156
    v = s.flash.phase("V")
    gvf = (s.F * v.beta * v.Vs * 1000.0 / q) if (v is not None and q > 0) else 0.0
    k = (v.Cp / v.Cv) if (v is not None and v.Cv > 0) else 1.3
    Fk = k / 1.4
    x_ch = Fk * xT
    dp = (q / kv) ** 2 * sg
    choked = False
    for _ in range(60):
        x = min(dp / P1, x_ch)
        Y = 1.0 - gvf * x / (3.0 * Fk * xT)
        new = (q / (kv * max(Y, 0.3))) ** 2 * sg
        if gvf > 0 and new / P1 >= x_ch:
            new, choked = x_ch * P1 * (1.0 + 1e-9), True
            # beyond choking the flow cannot pass: report the critical drop (the upstream would rise)
        if abs(new - dp) < 1e-8 * max(1.0, dp):
            dp = new
            break
        dp = 0.5 * (dp + new)
    if dp >= P1 * 0.98:
        raise UnitError(f"the choke Cv {cv:.1f} is too small for the flow (ΔP {dp:.0f} bar ≥ inlet {P1:.0f} bar): "
                        "open the choke or raise Cv")
    return dp, choked


def calc_xmas_tree(unit, ins, fp):
    p = unit["params"]
    s = _one(ins, "in")
    if s.empty:
        return {"out": [zero_stream("", fp, s.z, s.T, s.P)]}, {"Status": "No flow"}, []
    row = item("tree", p.get("tree"), unit["name"])
    dpt = _override(p, "dP_tree", row, "dP_bar", 0.0)
    Pa = s.P - dpt
    _check_P(Pa, unit["name"])
    spec = p.get("spec", "Outlet pressure")
    if spec == "Outlet pressure":
        P2 = float(p["P_out"])
        if P2 > Pa + 1e-9:
            raise UnitError(f"{unit['name']}: choke outlet {P2:.2f} bar is above the pressure after the tree "
                            f"valves {Pa:.2f} bar (wellhead {s.P:.2f} bar − tree ΔP {dpt:.2f} bar)")
    elif spec == "Choke pressure drop":
        P2 = Pa - float(p["dP"])
    elif spec == CHOKE_CV:
        cv = choke_cv(p)
        dp_c, choked = choke_dp(s, Pa, cv)
        P2 = Pa - dp_c
        cv_info = {"Choke Cv at this opening [US gpm/psi½]": cv, "Choke flow regime": "choked (critical)" if choked
                   else "sub-critical"}
    else:
        P2 = Pa
    _check_P(P2, unit["name"])
    fr = _ph(fp, s.z, P2, s.H, s.T, s.flash.Kset)
    out = make_stream("", fp, s.F, s.z, fr)
    res = {"Wellhead P [bar(a)]": s.P, "Wellhead T [°C]": s.T - K0, "Tree valve ΔP [bar]": dpt,
           "Choke ΔP [bar]": Pa - P2, "Outlet P [bar(a)]": P2, "Outlet T [°C]": fr.T - K0,
           "ΔT across tree and choke [°C]": fr.T - s.T, "Choke pressure ratio P2/P1 [-]": P2 / Pa}
    if spec == CHOKE_CV:
        res.update(cv_info)
    v = s.flash.phase("V")
    if v is not None and v.Cv > 0 and Pa - P2 > 1e-9:
        rc = critical_ratio(v.Cp / v.Cv)
        res["Critical pressure ratio [-]"] = rc
        res["Choke flow"] = ("Critical (sonic): rate independent of downstream P" if P2 / Pa <= rc
                             else "Sub-critical")
        if s.flash.vf < 0.9:
            res["Choke flow"] += " (gas-based estimate, multiphase)"
    elif Pa - P2 > 1e-9:
        res["Choke flow"] = "Liquid: no critical flow (check flashing/cavitation)"
    else:
        res["Choke flow"] = "Fully open"
    hm = _hyd_margin(out, fp)
    if hm is not None:
        res["Downstream hydrate margin [°C]"] = hm
        if hm < 0:
            res["Warning"] = "Choke outlet is inside the hydrate region: inhibit, insulate or heat"
    return {"out": [out]}, res, []


# --------------------------------------------------------------------------
# Template / manifold
# --------------------------------------------------------------------------

def calc_template(unit, ins, fp):
    p = unit["params"]
    row = item("template", p.get("template"), unit["name"])
    slots = int(round(_cat(row, "slots", 4)))
    streams = ins.get("in") or []
    if len(streams) > slots:
        raise UnitError(f"{unit['name']}: {len(streams)} inlets connected but the {row['item']} has {slots} slots")
    live = _live(streams)
    if not live:
        z = streams[0].z if streams else None
        return {"out": [zero_stream("", fp, z)]}, {"Status": "No flow", "Slots used": f"{len(streams)}/{slots}"}, []
    F, z, Hflow, Pmin, Tg = _mix(live, fp)
    fr = _ph(fp, z, Pmin, Hflow / F, Tg)
    mixed = make_stream("", fp, F, z, fr)
    D = _override(p, "hdr_ID", row, "header_ID_mm", 254.0) / 1000.0
    L = _override(p, "hdr_L", row, "header_length_m", 20.0)
    K = _override(p, "K", row, "K_extra", 0.0)
    eps = _cat(row, "roughness_mm", 0.045) / 1000.0
    _, d = pipe_gradient(fp, mixed, D, eps, 0.0, "Beggs & Brill", 0.02)
    dp = (d["dpf"] * L + K * d["rho_ns"] * d["vm"] ** 2 / 2.0) / 1e5
    P2 = Pmin - dp
    _check_P(P2, unit["name"])
    fr2 = _ph(fp, z, P2, Hflow / F, fr.T, fr.Kset)
    Ps = [s.P for s in live]
    res = {"Slots used": f"{len(streams)}/{slots}", "Spare slots": slots - len(streams),
           "Highest inlet P [bar(a)]": max(Ps), "Lowest inlet P [bar(a)]": min(Ps),
           "Inlet pressure spread [bar]": max(Ps) - min(Ps), "Header ΔP [bar]": dp,
           "Header velocity [m/s]": d["vm"], "Header flow regime": d["regime"],
           "Outlet P [bar(a)]": P2, "Outlet T [°C]": fr2.T - K0,
           "Erosional velocity ratio (API RP 14E, C=100)": d["vm"] / (122.0 / math.sqrt(max(d["rho_ns"], 1e-6)))}
    if max(Ps) - min(Ps) > 1e-6:
        res["Note"] = "Higher-pressure slots are throttled to the lowest inlet; balance them with the tree chokes"
    return {"out": [make_stream("", fp, F, z, fr2)]}, res, []


# --------------------------------------------------------------------------
# Jumpers, spools, PLET/PLEM
# --------------------------------------------------------------------------

def calc_jumper(unit, ins, fp):
    p = unit["params"]
    s = _one(ins, "in")
    if s.empty:
        return {"out": [zero_stream("", fp, s.z, s.T, s.P)]}, {"Status": "No flow"}, []
    row = item("jumper", p.get("kind"), unit["name"])
    L = _override(p, "length", row, "length_m", 10.0)
    bends = float(p.get("bends", -1.0))
    bends = _cat(row, "bends", 0.0) if bends < 0 else bends
    K = bends * _cat(row, "K_bend", 0.0) + _cat(row, "K_extra", 0.0)
    rough = _override(p, "rough", row, "roughness_mm", 0.045)
    dz = float(p.get("dz", 0.0))
    pu = _pipe_unit(unit, {"length": L, "ID": p["ID"], "rough": rough, "dz": dz, "n_seg": 3})
    outs, rp, en = calc_pipe(pu, {"in": [s]}, fp)
    o = outs["out"][0]
    D = float(p["ID"]) / 1000.0
    _, d = pipe_gradient(fp, o, D, rough / 1000.0, 0.0, "Beggs & Brill", 0.02)
    dpk = K * d["rho_ns"] * d["vm"] ** 2 / 2.0 / 1e5
    P2 = o.P - dpk
    _check_P(P2, unit["name"])
    fr = _ph(fp, o.z, P2, o.H, o.T, o.flash.Kset)
    out = make_stream("", fp, o.F, o.z, fr)
    prof = pu["_profile"]
    for k_, v_ in (("L", prof["L"][-1]), ("P", P2), ("T", fr.T - K0), ("z", prof["z"][-1]),
                   ("Hm", _hyd_margin(out, fp)), ("HL", prof["HL"][-1]), ("regime", prof["regime"][-1]),
                   ("vm", prof["vm"][-1])):
        prof[k_].append(v_)
    unit["_profile"] = prof
    res = {"Component": row["item"], "Pressure drop [bar]": s.P - P2, "Friction + elevation ΔP [bar]": s.P - o.P,
           "Fittings ΔP [bar]": dpk, "Bends": int(round(bends)), "Total fittings K [-]": K, "Length [m]": L,
           "Outlet P [bar(a)]": P2, "Outlet T [°C]": fr.T - K0, "Flow regime (dominant)": rp["Flow regime (dominant)"],
           "Mixture velocity [m/s]": d["vm"],
           "Erosional velocity ratio (API RP 14E, C=100)": rp["Erosional velocity ratio (API RP 14E, C=100)"]}
    if "Warning" in rp:
        res["Warning"] = rp["Warning"]
    return {"out": [out]}, _elev_note(res, dz), en


# --------------------------------------------------------------------------
# Flowline with design preset and DEH
# --------------------------------------------------------------------------

def heating_system(p):
    """(system, control) of a flowline, mapping the v5 DEH on/off switch onto the heating options."""
    system = p.get("heating", HEAT_NONE)
    if system == HEAT_NONE and p.get("deh") == "On":
        system = HEATING[1]
    return system, p.get("heat_ctrl", CTRL_FIXED)


def calc_flowline(unit, ins, fp):
    p = unit["params"]
    s = _one(ins, "in")
    if s.empty:
        return {"out": [zero_stream("", fp, s.z, s.T, s.P)]}, {"Status": "No flow"}, []
    row = item("flowline", p.get("design"), unit["name"])
    U = _override(p, "U", row, "U_W_m2K", 0.0)
    rough = _override(p, "rough", row, "roughness_mm", 0.045)
    system, ctrl = heating_system(p)
    q_in, hold = 0.0, {}
    if system != HEAT_NONE:
        if ctrl == CTRL_HOLD:
            hold = {"T_hold": float(p.get("T_hold", 25.0)), "q_max_W_m": float(p.get("q_max_W_m", 150.0) or 0.0)}
        else:
            q_in = _override(p, "deh_W_m", row, "deh_W_m", 0.0)
            if q_in <= 0 and p.get("heating", HEAT_NONE) != HEAT_NONE:   # legacy deh="On" files must set W/m
                q_in = float(p.get("q_max_W_m", 0.0) or 0.0)
            if q_in <= 0:
                raise UnitError(f"{unit['name']}: heating is on but no heat input is set (catalogue or override)")
    secs = route_sections(p)
    base = {"ID": p["ID"], "rough": rough, "method": p.get("method", "Beggs & Brill"),
            "heat": "Overall U to ambient" if U > 0 else "Adiabatic", "U": U, "T_amb": p.get("T_amb", 4.0),
            "q_in_W_m": q_in}
    if not secs:
        L = float(p["length"])
        dz_tot = float(p.get("dz", 0.0))
        pu = _pipe_unit(unit, dict(base, length=L, dz=dz_tot, n_seg=p.get("n_seg", 12), **hold))
        outs, rp, en = calc_pipe(pu, {"in": [s]}, fp)
        unit["_profile"] = pu["_profile"]
        route_res = {}
    else:
        outs, rp, en, L, dz_tot, route_res = _march_route(unit, s, fp, secs, base, hold, int(p.get("n_seg", 12)))
    res = {"Design": row["item"], "U used [W/m²·K]": U, "Roughness used [mm]": rough}
    res.update(route_res)
    if system != HEAT_NONE:
        delivered = q_in * L / 1000.0 if ctrl == CTRL_FIXED else rp.get("Controlled heating [kW]", 0.0)
        eff = float(p.get("heat_eff", 0.0) or 0.0) or HEAT_EFF[system]
        res.update({"Heating system": system, "Heating control": ctrl,
                    "Heat into the fluid [kW]": delivered, "Heating system efficiency [%]": eff})
        if system == HEATING[3]:
            res["Topside heater duty for heating [kW]"] = delivered / (eff / 100.0)
        else:
            res["Electrical heating power [kW]"] = delivered / (eff / 100.0)
        if system == HEATING[1] and ctrl == CTRL_FIXED:
            res["DEH power [kW]"] = delivered                     # kept for v5.x flowsheets and tests
    res.update(rp)
    if system != HEAT_NONE:
        res["Net heat loss [kW]"] = res.pop("Heat loss [kW]")
    return outs, _elev_note(res, dz_tot), en


def flowline_length(p):
    """Flowline length [m]: the route length when a route is given, else the length parameter."""
    secs = route_sections(p)
    return sum(ln for ln, _ in secs) if secs else float(p.get("length", 0.0))


def route_sections(p):
    """[(length m, dz m)] from a flowline route of [distance km, water depth m] points (dz up = positive);
    empty when no route (fewer than 2 points)."""
    pts = []
    for r in p.get("route") or []:
        try:
            x, d = float(r[0]), float(r[1])
        except (TypeError, ValueError, IndexError):
            continue
        pts.append((x * 1000.0, d))
    pts.sort()
    if len(pts) < 2:
        return []
    out = []
    for (x1, d1), (x2, d2) in zip(pts[:-1], pts[1:]):
        dx = x2 - x1
        if dx <= 0:
            continue
        dz = -(d2 - d1)
        out.append((math.hypot(dx, dz), dz))
    return out


def route_low_points(p):
    """Water depths of the local low points of a route (where liquid collects), deepest first."""
    pts = sorted((float(r[0]), float(r[1])) for r in (p.get("route") or []) if len(r) >= 2)
    lows = [d for (i, (_, d)) in enumerate(pts) if 0 < i < len(pts) - 1 and d > pts[i - 1][1] and d >= pts[i + 1][1]]
    return sorted(lows, reverse=True)


def _march_route(unit, s, fp, secs, base, hold, n_tot):
    """March a flowline section by section along its route; returns calc_pipe-like aggregates."""
    Ltot = sum(ln for ln, _ in secs)
    prof, st = {}, s
    z0 = L0 = 0.0
    Q = Qh = heated = peak = evr = inv = 0.0
    warns = []
    rp = {}
    for ln, dz in secs:
        ns = max(2, int(round(n_tot * ln / Ltot)))
        pu = _pipe_unit(unit, dict(base, length=ln, dz=dz, n_seg=ns, z0=z0, L0=L0, **hold))
        outs, rp, _ = calc_pipe(pu, {"in": [st]}, fp)
        _merge_profiles(prof, pu["_profile"])
        st = outs["out"][0]
        z0 += dz
        L0 += ln
        Q += rp["Heat loss [kW]"]
        Qh += rp.get("Controlled heating [kW]", 0.0) or 0.0
        heated += rp.get("Heated length [m]", 0.0) or 0.0
        peak = max(peak, rp.get("Peak heating [W/m]", 0.0) or 0.0)
        inv += rp.get("Liquid inventory [m³]", 0.0) or 0.0
        evr = max(evr, rp["Erosional velocity ratio (API RP 14E, C=100)"])
        if "Warning" in rp and "erosional" not in rp["Warning"]:
            warns.append(rp["Warning"])
    unit["_profile"] = prof
    regs = prof["regime"]
    agg = dict(rp)
    agg.update({"Pressure drop [bar]": s.P - st.P, "Outlet P [bar(a)]": st.P, "Outlet T [°C]": st.T - K0,
                "Inlet T [°C]": s.T - K0, "Flow regime (dominant)": max(set(regs), key=regs.count),
                "Inlet mixture velocity [m/s]": prof["vm"][0], "Outlet mixture velocity [m/s]": prof["vm"][-1],
                "Average liquid holdup [-]": sum(prof["HL"]) / len(prof["HL"]), "Liquid inventory [m³]": inv,
                "Erosional velocity ratio (API RP 14E, C=100)": evr, "Heat loss [kW]": Q})
    agg.pop("Inclination [°]", None)
    agg.pop("Warning", None)
    if Qh > 0 or "Controlled heating [kW]" in rp:
        agg.update({"Controlled heating [kW]": Qh, "Heated length [m]": heated, "Peak heating [W/m]": peak})
    hm = [x for x in prof.get("Hm", []) if x is not None]
    if hm:
        agg["Min. hydrate margin along line [°C]"] = min(hm)
    if evr > 1.0:
        warns.append(f"Mixture velocity exceeds the API RP 14E erosional velocity (ratio {evr:.2f})")
    if warns:
        agg["Warning"] = "; ".join(dict.fromkeys(warns))
    p = unit["params"]
    lows = route_low_points(p)
    depths = [float(r[1]) for r in p.get("route") or []]
    route_res = {"Route points": len(depths), "Route length [m]": Ltot,
                 "Horizontal length [m]": (max(float(r[0]) for r in p["route"]) - min(float(r[0]) for r in p["route"])) * 1000.0,
                 "Deepest point [m]": max(depths), "Low points along the route": len(lows)}
    en = [EnergyStream(unit.get("energy_name") or f"Q-{unit['name']}", -Q, unit["name"], "heat")] \
        if (base["U"] > 0 or Qh > 0) else []
    return {"out": [st]}, agg, en, Ltot, z0, route_res


# --------------------------------------------------------------------------
# Riser
# --------------------------------------------------------------------------

def riser_sections(row, depth, lf):
    """List of (length, dz) sections from the riser base (touch-down) to the hang-off."""
    L = depth * lf
    hog, sag = row.get("hog_frac"), row.get("sag_frac")
    if hog and sag is not None and hog > sag:
        h1, h2 = hog * depth, sag * depth
        s3 = (depth - h2) * 1.05
        rest = L - s3
        secs = [(0.7 * rest, h1), (0.3 * rest, -(h1 - h2)), (s3, depth - h2)]
        if any(abs(dz) > 1.0001 * ln for ln, dz in secs) or rest <= 0:
            raise UnitError(f"riser length {L:.0f} m is too short for the lazy-wave shape at {depth:.0f} m water "
                            "depth - increase the length factor")
        return secs
    if lf < 1.1:
        return [(max(L, depth), depth)]
    up = 0.7 * depth * 1.05                      # steep upper catenary
    return [(L - up, 0.3 * depth), (up, 0.7 * depth)]


def boe_number(fp, base, top, D, eps, fl_len, fl_incl_deg):
    """Riser-base regime, holdup and the Bøe (1981) severe-slugging number (< 1: severe slugging possible).

    Bøe: severe slugging can occur when the flowline flow is stratified towards the riser base and the
    hydrostatic head of liquid filling the riser base grows faster than the gas pressure in the flowline:
    ρ_l g u_sl > P0 u_sg0 / (α L), i.e. u_sg0 < ρ_l g α L u_sl / P0, with P0 the riser-top pressure, u_sg0
    the gas superficial velocity at P0, L the flowline length and α the flowline gas fraction.
    A screening indicator only."""
    theta = math.radians(fl_incl_deg)
    _, d = pipe_gradient(fp, base, D, eps, theta, "Beggs & Brill", 0.02)
    _, dt = pipe_gradient(fp, top, D, eps, 0.0, "Beggs & Brill", 0.02)
    _, _, _, rho_l, _, _ = _phase_split(fp, base)
    alpha = max(1.0 - d["HL"], 1e-6)
    boe = None
    if fl_len > 0 and d["vsl"] > 0 and dt["vsg"] > 0:
        crit = rho_l * G * alpha * fl_len * d["vsl"] / (top.P * 1e5)
        boe = dt["vsg"] / crit
    return d["regime"], d["HL"], boe


def calc_riser(unit, ins, fp):
    p = unit["params"]
    s = _one(ins, "in")
    if s.empty:
        return {"out": [zero_stream("", fp, s.z, s.T, s.P)]}, {"Status": "No flow"}, []
    row = item("riser", p.get("rtype"), unit["name"])
    depth = float(p["depth"])
    lf = _override(p, "lf", row, "length_factor", 1.0)
    lf = max(lf, 1.0)
    U = _override(p, "U", row, "U_W_m2K", 0.0)
    rough = _override(p, "rough", row, "roughness_mm", 0.045)
    try:
        secs = riser_sections(row, depth, lf)
    except UnitError as e:
        raise UnitError(f"{unit['name']}: {e}")
    Ltot = sum(ln for ln, _ in secs)
    n_tot = max(3, int(round(p.get("n_seg", 12))))
    prof, st = {}, s
    z0 = L0 = Q = evr = 0.0
    warns = []
    for ln, dz in secs:
        ns = max(2, int(round(n_tot * ln / Ltot)))
        pu = _pipe_unit(unit, {"length": ln, "ID": p["ID"], "rough": rough, "dz": dz, "n_seg": ns,
                               "heat": "Overall U to ambient" if U > 0 else "Adiabatic", "U": U,
                               "T_amb": p.get("T_amb", 6.0), "z0": z0, "L0": L0})
        outs, rp, _ = calc_pipe(pu, {"in": [st]}, fp)
        _merge_profiles(prof, pu["_profile"])
        st = outs["out"][0]
        z0 += dz
        L0 += ln
        Q += rp["Heat loss [kW]"]
        evr = max(evr, rp["Erosional velocity ratio (API RP 14E, C=100)"])
        if "Warning" in rp and "erosional" not in rp["Warning"]:
            warns.append(rp["Warning"])
    if evr > 1.0:
        warns.append(f"Mixture velocity exceeds the API RP 14E erosional velocity (ratio {evr:.2f})")
    unit["_profile"] = prof
    D = float(p["ID"]) / 1000.0
    regime, HL, boe = boe_number(fp, s, st, D, rough / 1000.0, float(p.get("fl_len", 0.0)),
                                 float(p.get("fl_incl", 0.0)))
    regs = prof["regime"]
    res = {"Riser type": row["item"], "Water depth [m]": depth, "Riser length [m]": Ltot,
           "Sections": len(secs), "Pressure drop [bar]": s.P - st.P, "Outlet P [bar(a)]": st.P,
           "Outlet T [°C]": st.T - K0, "Inlet T [°C]": s.T - K0,
           "Flow regime (dominant)": max(set(regs), key=regs.count),
           "Average liquid holdup [-]": sum(prof["HL"]) / len(prof["HL"]),
           "Top mixture velocity [m/s]": prof["vm"][-1], "Erosional velocity ratio (API RP 14E, C=100)": evr,
           "Heat loss [kW]": Q, "Riser-base flow regime": regime, "Riser-base liquid holdup [-]": HL}
    hm = [x for x in prof.get("Hm", []) if x is not None]
    if hm:
        res["Min. hydrate margin along line [°C]"] = min(hm)
    if boe is not None:
        res["Bøe number (< 1: severe slugging possible) [-]"] = boe
    stratified = regime in ("Segregated", "Transition")
    risky = stratified and float(p.get("fl_incl", 0.0)) <= 0.0 and boe is not None and boe < 1.0
    res["Riser-base slugging risk"] = ("High: severe slugging possible (stratified, Bøe < 1)" if risky else "Low")
    if len(secs) == 3:
        res["Sag-bend note"] = "Liquid can collect in the lazy-wave sag bend at low rates: check turndown"
    if risky:
        warns.append("Severe riser-base slugging is possible (Bøe screening): consider gas lift, "
                     "a smaller riser or topside choking")
    if warns:
        res["Warning"] = "; ".join(dict.fromkeys(warns))
    en = [EnergyStream(f"Q-{unit['name']}" if not unit.get("energy_name") else unit["energy_name"],
                       -Q, unit["name"], "heat")] if U > 0 else []
    return {"out": [st]}, _elev_note(res, depth), en


# --------------------------------------------------------------------------
# SSIV / HIPPS
# --------------------------------------------------------------------------

def calc_subsea_valve(unit, ins, fp):
    p = unit["params"]
    s = _one(ins, "in")
    row = item("valve", p.get("kind"), unit["name"])
    if s.empty:
        return {"out": [zero_stream("", fp, s.z, s.T, s.P)]}, {"Status": "No flow"}, []
    if p.get("state", "Open") == "Closed":
        return ({"out": [zero_stream("", fp, s.z, s.T, s.P)]},
                {"Position": "Closed", "Status": "No flow downstream", "Inlet P [bar(a)]": s.P}, [])
    if p.get("hipps") == "On" and s.P > float(p["P_trip"]):
        return ({"out": [zero_stream("", fp, s.z, s.T, s.P)]},
                {"Position": "Tripped closed", "Inlet P [bar(a)]": s.P, "Trip pressure [bar(a)]": float(p["P_trip"]),
                 "Warning": f"HIPPS tripped: inlet {s.P:.1f} bar is above the trip pressure "
                            f"{float(p['P_trip']):.1f} bar - valve closed"}, [])
    dp = _override(p, "dP", row, "dP_bar", 0.0)
    P2 = s.P - dp
    _check_P(P2, unit["name"])
    fr = _ph(fp, s.z, P2, s.H, s.T, s.flash.Kset)
    res = {"Position": "Open", "Valve": row["item"], "ΔP [bar]": dp, "Outlet P [bar(a)]": P2,
           "Outlet T [°C]": fr.T - K0}
    if p.get("hipps") == "On":
        res["Trip pressure [bar(a)]"] = float(p["P_trip"])
        res["Margin to trip [bar]"] = float(p["P_trip"]) - s.P
    return {"out": [make_stream("", fp, s.F, s.z, fr)]}, res, []



# --------------------------------------------------------------------------
# Subsea booster: multiphase pump, hybrid pump, wet-gas compressor, liquid pump
# --------------------------------------------------------------------------

def gas_volume_fraction(st):
    """Actual gas volume fraction of a stream (0-1)."""
    if st.empty or st.flash is None:
        return 0.0
    vols = {k: 0.0 for k in ("V", "L")}
    for ph in st.flash.phases:
        vols["V" if ph.kind == "V" else "L"] += ph.beta * ph.Vs
    tot = vols["V"] + vols["L"]
    return vols["V"] / tot if tot > 0 else 0.0


def typical_booster_curve(Q_design, dP_design, eff_design):
    """Generic design-speed curve through a duty point: boost ΔP [bar] and efficiency [%] vs actual inlet flow
    [m³/h] per machine. ΔP = ΔP_d (1.3 − 0.3 q²) (30 % rise to shut-off), η = η_d (1 − 0.8 (q − 1)²); the first
    point is the minimum-flow limit, the last the run-out limit. Illustrative shape, not vendor data."""
    qs = [0.45, 0.6, 0.75, 0.9, 1.0, 1.1, 1.2, 1.35]
    return {"flow": [Q_design * q for q in qs], "head": [dP_design * (1.3 - 0.3 * q * q) for q in qs],
            "eff": [max(eff_design * (1.0 - 0.8 * (q - 1.0) ** 2), 0.3 * eff_design) for q in qs]}


def _speed_for_boost(curve, Q, dp):
    """Speed at which the curve delivers boost dp at actual flow Q (fan laws), by bisection; None if out of reach."""
    from .unitops import CompressorCurve  # noqa: F401  (type only)

    def f(r):
        return curve.at(Q, r * curve.N0)[0] - dp
    lo, hi = 0.2, 2.0
    if f(lo) > 0 or f(hi) < 0:
        return None
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if f(mid) > 0:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi) * curve.N0


def calc_subsea_booster(unit, ins, fp):
    """Isentropic compression of the whole mixture (PS flash) divided by the machine efficiency - the usual
    screening model for multiphase pumps and wet-gas compressors - with the machine's operating envelope.

    Optional performance curve (boost ΔP and efficiency vs actual flow per machine at the design speed, fan laws
    for other speeds) and machines in parallel (flow split equally) and in series (boost shared equally)."""
    from .unitops import CompressorCurve
    p = unit["params"]
    s = _one(ins, "in")
    row = item("booster", p.get("btype"), unit["name"])
    if s.empty:
        return {"out": [zero_stream("", fp, s.z, s.T, s.P)]}, {"Status": "No flow"}, []
    if p.get("online", ONLINE) == BYPASSED:
        return ({"out": [s.copy("")]}, {"Status": "Bypassed (not running)", "Booster type": row["item"],
                                        "Inlet GVF [%]": 100.0 * gas_volume_fraction(s), "Pressure boost [bar]": 0.0,
                                        "Outlet P [bar(a)]": s.P, "Shaft power [kW]": 0.0, "Electrical power [kW]": 0.0},
                [_energy(unit, 0.0, "work")])
    n_par = max(1, int(round(float(p.get("n_par", 1) or 1))))
    n_ser = max(1, int(round(float(p.get("n_ser", 1) or 1))))
    q_act = sum(s.F * ph.beta * 1000.0 * ph.Vs for ph in s.flash.phases)       # m3/h
    Qm = q_act / n_par
    spec = p.get("spec", "Pressure boost")
    curve = None
    if p.get("curve"):
        try:
            curve = CompressorCurve(p["curve"], float(p.get("N_design", 3600.0) or 3600.0))
        except UnitError as e:
            if spec == "Performance curve":
                raise UnitError(f"{unit['name']}: {e}")
    info, warns = {}, []
    eta = _override(p, "eff", row, "eff_pct", 60.0) / 100.0
    if spec == "Performance curve":
        if curve is None:
            raise UnitError(f"{unit['name']}: no performance curve (Performance curve tab)")
        N = float(p.get("speed", curve.N0) or curve.N0)
        dpm, eff_c, Qe = curve.at(Qm, N)
        if dpm <= 0:
            raise UnitError(f"{unit['name']}: the flow per machine is beyond the curve's run-out at {N:.0f} rpm")
        P2 = s.P + n_ser * dpm
        eta = eff_c / 100.0
        info.update({"Speed [rpm]": N, "Curve efficiency [%]": eff_c})
    else:
        P2 = s.P + float(p["dP"]) if spec == "Pressure boost" else float(p["P_out"])
        if curve is not None:
            Nreq = _speed_for_boost(curve, Qm, (P2 - s.P) / n_ser)
            if Nreq is None:
                warns.append("the curve cannot deliver this boost at 20–200 % speed")
            else:
                dpm, eff_c, Qe = curve.at(Qm, Nreq)
                eta = eff_c / 100.0 if p.get("eff", 0.0) in (0, 0.0, None) else eta
                info.update({"Speed to meet duty [rpm]": Nreq, "Curve efficiency [%]": eff_c})
    if P2 <= s.P:
        raise UnitError(f"{unit['name']}: outlet pressure must exceed the inlet ({s.P:.2f} bar)")
    if curve is not None and "Curve efficiency [%]" in info:
        N_op = info.get("Speed [rpm]", info.get("Speed to meet duty [rpm]"))
        r = N_op / curve.N0
        q_min, q_max = curve.q[0] * r, curve.q[-1] * r
        info["Minimum-flow margin [%]"] = 100.0 * (Qm / q_min - 1.0)
        if Qm < q_min:
            warns.append(f"flow per machine {Qm:.0f} m³/h is below the minimum-flow limit {q_min:.0f} m³/h "
                         "(recirculation needed)")
        if Qm > q_max:
            warns.append(f"flow per machine {Qm:.0f} m³/h is beyond the run-out limit {q_max:.0f} m³/h")
        unit["_map"] = {"kind": "booster", "curve": {"flow": curve.q.tolist(), "head": curve.h.tolist(),
                                                     "eff": curve.e.tolist()},
                        "N0": curve.N0, "N": N_op, "Q": Qm, "dP": (P2 - s.P) / n_ser}
    gvf = gas_volume_fraction(s)
    fs = fp.ps_flash(s.z, P2, s.S, s.T, s.flash.Kset)
    H2 = s.H + (fs.H - s.H) / eta
    fr = _ph(fp, s.z, P2, H2, fs.T, fs.Kset)
    W = s.F * (fr.H - s.H) / 3600.0                              # kW shaft, all machines
    n_mach = n_par * n_ser
    motor = _cat(row, "motor_eff", 0.93) or 0.93
    rated = _cat(row, "rated_kW")
    res = {"Booster type": row["item"], "Machines": f"{n_par} parallel × {n_ser} series", "Inlet GVF [%]": 100.0 * gvf,
           "Pressure boost [bar]": P2 - s.P, "Boost per machine [bar]": (P2 - s.P) / n_ser,
           "Outlet P [bar(a)]": P2, "Outlet T [°C]": fr.T - K0, "Temperature rise [°C]": fr.T - s.T,
           "Efficiency used [%]": 100.0 * eta, "Actual inlet flow [m³/h]": q_act, "Flow per machine [m³/h]": Qm,
           "Hydraulic power [kW]": q_act / 3600.0 * (P2 - s.P) * 1e5 / 1000.0,
           "Shaft power [kW]": W, "Shaft power per machine [kW]": W / n_mach, "Electrical power [kW]": W / motor}
    res.update(info)
    gmin, gmax = _cat(row, "min_gvf", 0.0), _cat(row, "max_gvf", 1.0)
    if not (gmin - 1e-9 <= gvf <= gmax + 1e-9):
        warns.append(f"inlet GVF {100 * gvf:.1f} % is outside the {row['item']} window "
                     f"({100 * gmin:.0f}–{100 * gmax:.0f} %)")
    dpmax = _cat(row, "max_dP_bar")
    if dpmax and (P2 - s.P) / n_ser > dpmax:
        warns.append(f"boost per machine {(P2 - s.P) / n_ser:.1f} bar exceeds the {dpmax:.0f} bar a single "
                     f"{row['item']} delivers (add a machine in series)")
    if rated:
        res["Rated power [kW]"] = rated
        res["Power utilisation [%]"] = 100.0 * W / n_mach / rated
        if W / n_mach > rated:
            warns.append(f"shaft power per machine {W / n_mach:.0f} kW exceeds the rated {rated:.0f} kW "
                         "(add a machine in parallel)")
    if warns:
        res["Warning"] = "; ".join(warns)
    return {"out": [make_stream("", fp, s.F, s.z, fr)]}, res, [_energy(unit, W, "work")]


# --------------------------------------------------------------------------
# Subsea separator, cooler, pressure intensifier, chemical injection valve
# --------------------------------------------------------------------------

S_ALLOW_MPA = 138.0          # allowable stress, carbon steel (generic)
CORR_MM = 3.0


def _act_vol(st):
    """Actual volumetric flow [m³/s] and density [kg/m³] of a stream (0, 0 when empty)."""
    if st.empty:
        return 0.0, 0.0
    q = sum(st.F * ph.beta * ph.Vs for ph in st.flash.phases) * 1000.0 / 3600.0
    return q, (st.F * st.MW / 3600.0 / q if q > 0 else 0.0)


def calc_subsea_separator(unit, ins, fp):
    """Three-outlet subsea separator (gas, oil/liquid, water) on the separator model, with indicative sizing:
    Souders-Brown gas area, liquid residence volume, ASME wall thickness for the design pressure, shell weight."""
    p = unit["params"]
    row = item("separator", p.get("sep_type"), unit["name"])
    two = "gas-liquid" in row["item"].lower()       # gas/liquid only: hydrocarbon liquid and water leave together
    outs, res, en = _separate(unit, ins, fp, not two)
    if two:
        liq = outs.pop("liquid")[0]
        outs["oil"] = [liq]
        outs["water"] = [zero_stream("", fp, liq.z, liq.T, liq.P)]
        res.pop("Note", None)
        if "Liquid flow [kg/h]" in res:
            res["Oil flow [kg/h]"] = res.pop("Liquid flow [kg/h]")
    if res.get("Status") == "No flow":
        return outs, res, en
    gas, oil, wat = outs["vapour"][0], outs["oil"][0], outs["water"][0]
    qg, rg = _act_vol(gas)
    qo, ro = _act_vol(oil)
    qw, rw = _act_vol(wat)
    ql = qo + qw
    rl = (qo * ro + qw * rw) / ql if ql > 0 else 1000.0
    K = _cat(row, "sb_K", 0.10)
    tres = float(p.get("res_min", 0.0) or 0.0) or _cat(row, "res_min", 3.0)
    V_liq = ql * tres * 60.0
    v_max = K * math.sqrt(max(rl - rg, 1.0) / rg) if rg > 0 else math.inf
    D_gas = math.sqrt(4.0 * qg / (math.pi * v_max)) if qg > 0 and math.isfinite(v_max) else 0.0
    P_op = res["Vessel P [bar(a)]"]
    vertical = "vertical" in row["item"].lower()
    if vertical:
        D = max(D_gas, 0.6)
        h_liq = V_liq / (math.pi * D * D / 4.0)
        L = h_liq + max(1.5, D)                       # liquid section + disengagement / demister height
    else:
        LD = 20.0 if "pipe" in row["item"].lower() else 4.0
        D = max((8.0 * V_liq / (math.pi * LD)) ** (1.0 / 3.0), D_gas * math.sqrt(2.0), 0.6)   # half full of liquid
        L = LD * D
    Pd = float(p.get("P_design", 0.0) or 0.0) or 1.1 * P_op
    Pmpa = Pd / 10.0
    t = Pmpa * D / (2.0 * S_ALLOW_MPA - 1.2 * Pmpa) * 1000.0 + CORR_MM          # mm, ASME VIII-1 cylinder
    weight = 7850.0 * math.pi * (D + t / 1000.0) * t / 1000.0 * (L + D) * 1.3 / 1000.0  # t, +30 % internals/nozzles
    res.update({"Separator type": row["item"], "Souders-Brown K [m/s]": K, "Gas flow [m³/h]": qg * 3600.0,
                "Liquid flow [m³/h]": ql * 3600.0, "Liquid residence time [min]": tres,
                "Liquid hold-up volume [m³]": V_liq, "Vessel ID [mm]": D * 1000.0, "Length (T/T) [m]": L,
                "Design pressure [bar(a)]": Pd, "Wall thickness incl. corrosion [mm]": t,
                "Shell weight (indicative) [t]": weight, "Water to reinjection [m³/d]": qw * 86400.0})
    if t > 150.0:
        res["Warning"] = (f"wall thickness {t:.0f} mm: beyond typical forging limits — split into parallel "
                          "vessels or use a pipe separator")
    return outs, res, en


def calc_subsea_cooler(unit, ins, fp):
    """Passive seawater cooler: outlet T from a spec, an approach to the sea temperature, or the area
    (T_out = T_sea + (T_in − T_sea)·exp(−U·A/ṁc_p)); duty rejected to the sea; hydrate check at the outlet."""
    p = unit["params"]
    s = _one(ins, "in")
    row = item("process", "Passive subsea cooler", unit["name"])
    if s.empty:
        return {"out": [zero_stream("", fp, s.z, s.T, s.P)]}, {"Status": "No flow"}, []
    P2 = s.P - float(p.get("dP", 0.0))
    _check_P(P2, unit["name"])
    U = _override(p, "U", row, "U_W_m2K", 250.0)
    Ts = float(p.get("T_sea", 4.0))
    Tin = s.T - K0
    mcp = s.F * 1000.0 / 3600.0 * max(s.flash.Cp, 1.0)           # W/K
    spec = p.get("spec", "Approach to sea temperature")
    if spec == "Outlet temperature":
        T2 = float(p["T_out"])
    elif spec == "Approach to sea temperature":
        T2 = Ts + float(p["approach"])
    else:
        T2 = Ts + (Tin - Ts) * math.exp(-U * float(p["area"]) / mcp)
    if T2 <= Ts:
        raise UnitError(f"{unit['name']}: outlet {T2:.1f} °C cannot be at or below the sea temperature {Ts:.1f} °C")
    if T2 > Tin + 1e-9:
        raise UnitError(f"{unit['name']}: outlet {T2:.1f} °C is above the inlet {Tin:.1f} °C (a cooler cannot heat)")
    fr = fp.pt_flash(s.z, T2 + K0, P2, s.flash.Kset)
    out = make_stream("", fp, s.F, s.z, fr)
    duty = s.F * (fr.H - s.H) / 3600.0
    area = (-math.log((T2 - Ts) / (Tin - Ts)) * mcp / U) if Tin - Ts > 1e-9 and T2 < Tin else 0.0
    res = {"Outlet T [°C]": T2, "Outlet P [bar(a)]": P2, "Duty rejected to sea [kW]": -duty,
           "U used [W/m²·K]": U, "Required area [m²]": area, "Inlet T [°C]": Tin}
    hm = _hyd_margin(out, fp)
    if hm is not None:
        res["Outlet hydrate margin [°C]"] = hm
        if hm < 0:
            res["Warning"] = "cooler outlet is inside the hydrate region: inhibit upstream or cool less"
    return {"out": [out]}, res, [_energy(unit, duty)]


def calc_intensifier(unit, ins, fp):
    """Hydraulically driven pressure intensifier for chemicals or control fluid: outlet P = hydraulic supply ×
    area ratio × mechanical efficiency; liquid compression work V·ΔP / η into the fluid; hydraulic fluid drawn
    = liquid flow × area ratio / 95 % volumetric efficiency."""
    p = unit["params"]
    s = _one(ins, "in")
    row = item("process", "Pressure intensifier", unit["name"])
    if s.empty:
        return {"out": [zero_stream("", fp, s.z, s.T, s.P)]}, {"Status": "No flow"}, []
    eta = _override(p, "eff", row, "eff_pct", 85.0) / 100.0
    Ph = float(p["P_hyd"])
    if p.get("spec", "Area ratio") == "Area ratio":
        ratio = float(p["ratio"])
        P2 = Ph * ratio * eta
    else:
        P2 = float(p["P_out"])
        ratio = P2 / (Ph * eta)
    if P2 <= s.P:
        raise UnitError(f"{unit['name']}: intensified pressure {P2:.0f} bar is not above the inlet {s.P:.0f} bar")
    Vs = _vapour_volume(s.flash)                         # m³/mol
    H2 = s.H + Vs * (P2 - s.P) * 1e5 / eta
    fr = _ph(fp, s.z, P2, H2, s.T, s.flash.Kset)
    W = s.F * (fr.H - s.H) / 3600.0
    q_liq = s.F * 1000.0 * Vs                           # m³/h
    q_hyd = q_liq * ratio / 0.95
    res = {"Outlet P [bar(a)]": P2, "Area ratio [-]": ratio, "Hydraulic supply P [bar(a)]": Ph,
           "Mechanical efficiency [%]": 100.0 * eta, "Liquid flow [L/h]": q_liq * 1000.0,
           "Hydraulic fluid consumption [L/min]": q_hyd * 1000.0 / 60.0,
           "Hydraulic power drawn [kW]": q_hyd / 3600.0 * Ph * 1e5 / 1000.0, "Power into the fluid [kW]": W,
           "Outlet T [°C]": fr.T - K0}
    if s.flash.vf > 1e-6:
        res["Warning"] = f"vapour in the intensifier feed (vapour fraction {s.flash.vf:.4f}): it pumps liquids only"
    return {"out": [make_stream("", fp, s.F, s.z, fr)]}, res, [_energy(unit, W, "work")]


def calc_cimv(unit, ins, fp):
    """Chemical injection metering valve: the chemical is throttled into the production stream at the production
    pressure (adiabatic mix); dosage and, for MEG/methanol, the inhibited hydrate margin downstream."""
    from .streams import stream_properties, hydrate_state
    p = unit["params"]
    prod = _one(ins, "in")
    chem = _one(ins, "chem")
    row = item("process", "Chemical injection metering valve", unit["name"])
    dpmin = _override(p, "dP_min", row, "dP_bar", 5.0)
    if prod.empty:
        return {"out": [zero_stream("", fp, prod.z, prod.T, prod.P)]}, {"Status": "No production flow"}, []
    if not chem.empty and chem.P < prod.P + dpmin:
        raise UnitError(f"{unit['name']}: chemical arrives at {chem.P:.1f} bar, below the production pressure "
                        f"{prod.P:.1f} bar + {dpmin:.1f} bar across the valve — raise the delivery pressure "
                        "(pump or intensifier)")
    F, z, Hf, _, Tg = _mix([prod, chem], fp)
    fr = _ph(fp, z, prod.P, Hf / F, Tg)
    out = make_stream("", fp, F, z, fr)
    pp = stream_properties(prod, fp)
    res = {"Injection pressure [bar(a)]": prod.P}
    if not chem.empty:
        pc = stream_properties(chem, fp)
        res.update({"Chemical rate [kg/h]": pc["Mass flow [kg/h]"], "Chemical rate [L/h]": pc["Std liq vol flow [m³/h]"] * 1000.0,
                    "ΔP across the valve [bar]": chem.P - prod.P})
        if pp["Std liq vol flow [m³/h]"] > 0:
            res["Dosage [ppm of liquid, vol]"] = 1e6 * pc["Std liq vol flow [m³/h]"] / pp["Std liq vol flow [m³/h]"]
    t_hyd, t_inh, margin, wt = hydrate_state(out, fp)
    if margin is not None:
        res.update({"Inhibitor in water [wt%]": wt, "Inhibited hydrate T [°C]": t_inh, "Hydrate margin downstream [°C]": margin})
        if margin < 0:
            res["Warning"] = "still inside the hydrate region after injection: raise the dosage"
    res["Outlet T [°C]"] = fr.T - K0
    return {"out": [out]}, res, []


# --------------------------------------------------------------------------
# Water injection well
# --------------------------------------------------------------------------

def calc_injection_well(unit, ins, fp):
    """Water flows down the tubing from the wellhead (hydrostatic gain, friction, heat to the formation) and is
    injected where the bottomhole pressure exceeds the reservoir pressure: q = II (P_bh − P_res)."""
    p = unit["params"]
    s = _one(ins, "in")
    if s.empty:
        return {"out": [zero_stream("", fp, s.z, s.T, s.P)]}, {"Status": "No flow"}, []
    nw = n_wells(unit)
    MD, TVD = float(p["MD"]), float(p["TVD"])
    if TVD > MD:
        raise UnitError(f"{unit['name']}: TVD {TVD:.0f} m exceeds the measured depth {MD:.0f} m")
    sw = make_stream("", fp, s.F / nw, s.z, s.flash)
    U = float(p.get("U", 0.0))
    tub = _pipe_unit(unit, {"length": MD, "ID": p["ID"], "rough": p["rough"], "dz": -TVD,
                            "n_seg": p.get("n_seg", 10), "heat": "Overall U to ambient" if U > 0 else "Adiabatic",
                            "U": U, "T_amb": s.T - K0, "T_amb_out": float(p.get("T_res", 90.0))})
    try:
        outs, rp, en = calc_pipe(tub, {"in": [sw]}, fp)
    except UnitError as e:
        raise UnitError(f"{unit['name']}: {e}")
    unit["_profile"] = tub["_profile"]
    bh = outs["out"][0]
    _, oil, wat = standard_rates(sw, fp)
    q = wat + oil                                         # liquid injected per well, Sm³/d
    P_res, II = float(p["P_res"]), float(p["II"])
    P_req = P_res + q / II
    margin = bh.P - P_req
    res = {"Identical wells": nw, "Injection rate per well [Sm³/d]": q, "Injection rate [Sm³/d]": q * nw,
           "Wellhead P [bar(a)]": s.P, "Bottomhole P [bar(a)]": bh.P, "Required bottomhole P [bar(a)]": P_req,
           "Injection margin [bar]": margin,
           "Rate this wellhead pressure can inject per well [Sm³/d]": max(II * (bh.P - P_res), 0.0),
           "Hydrostatic + friction gain [bar]": bh.P - s.P, "Bottomhole T [°C]": bh.T - K0,
           "Heat to formation [kW]": rp["Heat loss [kW]"] * nw}
    if margin < 0:
        res["Warning"] = (f"the wellhead pressure is {-margin:.1f} bar too low to inject {q:.0f} Sm³/d per well "
                          f"(raise the pump discharge to {s.P - margin:.1f} bar or add wells)")
    out = make_stream("", fp, s.F, bh.z, bh.flash)
    en = [EnergyStream(e.name, e.duty_kW * nw, e.unit, e.kind) for e in en]
    return {"out": [out]}, _elev_note(res, -TVD), en


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------

BOOSTER_TYPES = ("subsea_booster", "subsea_pump", "subsea_compressor")
SURF_TYPES = ("well", "xmas_tree", "template", "jumper", "flowline", "riser", "subsea_valve") + BOOSTER_TYPES + (
    "subsea_separator", "subsea_cooler", "intensifier", "cimv", "injection_well")
PROFILE_TYPES = ("well", "jumper", "flowline", "riser", "injection_well")


def register():
    _ACTIVE.update(index={(r["category"], r["item"]): r for r in _DEFAULT}, key=_key(_DEFAULT))
    CATALOGUE.update(_schema())
    CALC.update({"well": calc_well, "xmas_tree": calc_xmas_tree, "template": calc_template,
                 "jumper": calc_jumper, "flowline": calc_flowline, "riser": calc_riser,
                 "subsea_valve": calc_subsea_valve, "subsea_booster": calc_subsea_booster,
                 "subsea_pump": calc_subsea_booster, "subsea_compressor": calc_subsea_booster,
                 "subsea_separator": calc_subsea_separator, "subsea_cooler": calc_subsea_cooler,
                 "intensifier": calc_intensifier, "cimv": calc_cimv, "injection_well": calc_injection_well})


register()
