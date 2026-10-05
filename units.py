"""Display units: SI (metric, the engine's units) or Field (oilfield/US customary).

The engine always works in metric; this module converts at the display boundary only.  Every
displayed quantity carries its unit in brackets in its label ("Outlet T [°C]"), so a label plus
a value is enough to convert.  Temperature *differences* (approach, margin, rise, ΔT, error)
are scaled without the 32 °F offset; power ("Power", "work", "fan") goes to hp and heat duty to
MMBtu/h.

Standard volumes: 1 Sm³ (15 °C, 1.01325 bar) = 35.383 scf (60 °F, 14.696 psia) on an equal-moles
basis; 1 m³ = 6.28981 bbl.
"""
from __future__ import annotations

import re

SI, FIELD = "SI (metric)", "Field"
SYSTEMS = [SI, FIELD]
_state = {"system": SI}

SCF_PER_SM3 = 35.383
BBL_PER_M3 = 6.28981

# SI unit -> (field unit, factor, offset)
_TABLE = {
    "°C": ("°F", 1.8, 32.0),
    "bar(a)": ("psia", 14.5038, 0.0),
    "bar": ("psi", 14.5038, 0.0),
    "kg/h": ("lb/h", 2.20462, 0.0),
    "kmol/h": ("lbmol/h", 2.20462, 0.0),
    "MSm³/d": ("MMscf/d", SCF_PER_SM3, 0.0),
    "Sm³/d": ("bbl/d", BBL_PER_M3, 0.0),
    "Sm³/h": ("Mscf/d", SCF_PER_SM3 * 24 / 1000.0, 0.0),
    "kg/m³": ("lb/ft³", 0.0624280, 0.0),
    "m": ("ft", 3.28084, 0.0),
    "mm": ("in", 0.0393701, 0.0),
    "m/s": ("ft/s", 3.28084, 0.0),
    "kJ/kg": ("Btu/lb", 0.429923, 0.0),
    "kJ/kmol": ("Btu/lbmol", 0.429923, 0.0),
    "kJ/kmol·K": ("Btu/lbmol·°F", 0.238846, 0.0),
    "kW/°C": ("Btu/h·°F", 1895.63, 0.0),
    "W/m²·K": ("Btu/h·ft²·°F", 0.176110, 0.0),
    "m³": ("bbl", BBL_PER_M3, 0.0),
    "m³/d": ("bbl/d", BBL_PER_M3, 0.0),
    "Pa": ("inH₂O", 0.00401463, 0.0),
    "kg/s": ("lb/s", 2.20462, 0.0),
    "t/h": ("klb/h", 2.20462, 0.0),
    "t/y": ("ton/y", 1.10231, 0.0),          # short tons
    "MSm³/y": ("Bscf/y", SCF_PER_SM3 / 1000.0, 0.0),
}
_POWER = ("hp", 1.34102, 0.0)
_DUTY = ("MMBtu/h", 0.00341214, 0.0)
_DELTA_WORDS = ("Δ", "approach", "margin", "LMTD", "rise", "error", "depression", "change", "difference")
_POWER_WORDS = ("power", "work", "fan", "shaft")
_BRACKET = re.compile(r"\[([^\[\]]+)\]")


def set_system(name):
    _state["system"] = name if name in SYSTEMS else SI


def system():
    return _state["system"]


def field():
    return _state["system"] == FIELD


def _is_delta(label):
    lab = label.lower()
    return any(w.lower() in lab for w in _DELTA_WORDS)


def _is_power(label):
    lab = label.lower()
    return any(w in lab for w in _POWER_WORDS)


def _rule(si_unit, label="", delta=None, power=None):
    """(display unit, factor, offset) for an SI unit in the current system."""
    if not field() or not si_unit:
        return si_unit, 1.0, 0.0
    if si_unit == "kW":
        pw = _is_power(label) if power is None else power
        return _POWER if pw else _DUTY
    if si_unit == "m³/h":
        # standard/actual liquid rates in bbl/d, actual gas in ft³/h
        if "liq" in label.lower() or "iquid" in label:
            return "bbl/d", BBL_PER_M3 * 24.0, 0.0
        return "ft³/h", 35.3147, 0.0
    r = _TABLE.get(si_unit)
    if r is None:
        return si_unit, 1.0, 0.0
    u, f, o = r
    if o and (_is_delta(label) if delta is None else delta):
        o = 0.0
    return u, f, o


def value(si_unit, v, label="", delta=None, power=None):
    """Convert an SI value for display."""
    if v is None or isinstance(v, str):
        return v
    u, f, o = _rule(si_unit, label, delta, power)
    try:
        return float(v) * f + o
    except (TypeError, ValueError):
        return v


def to_si(si_unit, v, label="", delta=None, power=None):
    """Convert a displayed value back to SI (inverse of value())."""
    if v is None or isinstance(v, str):
        return v
    u, f, o = _rule(si_unit, label, delta, power)
    return (float(v) - o) / f


def unit(si_unit, label="", delta=None, power=None):
    return _rule(si_unit, label, delta, power)[0]


def key(label):
    """Relabel 'Name [SI unit]' in the current system."""
    m = _BRACKET.search(label)
    if not m or not field():
        return label
    u = unit(m.group(1), label)
    return label[:m.start()] + f"[{u}]" + label[m.end():]


def kv(label, v):
    """(display label, display value) for a labelled SI quantity."""
    m = _BRACKET.search(label)
    if not m or isinstance(v, str) or isinstance(v, bool):
        return label, v
    return key(label), value(m.group(1), v, label)


def convert_dict(d):
    out = {}
    for k, v in d.items():
        k2, v2 = kv(k, v)
        out[k2] = v2
    return out


def T(v):
    return value("°C", v)


def P(v):
    return value("bar(a)", v)


def uT():
    return unit("°C")


def uP():
    return unit("bar(a)")


def df_display(df):
    """Convert every column labelled 'Name [SI unit]' of a DataFrame for display."""
    out = df.copy()
    ren = {}
    for c in df.columns:
        m = _BRACKET.search(str(c))
        if not m:
            continue
        si = m.group(1)
        out[c] = [value(si, v, str(c)) if not isinstance(v, str) else v for v in df[c]]
        ren[c] = key(str(c))
    return unique_columns(out.rename(columns=ren))


def unique_columns(df):
    """Make DataFrame column names unique ('Name', 'Name (2)', ...): Streamlit/Arrow and Plotly (narwhals)
    both reject duplicate column names."""
    seen = {}
    cols = []
    for c in df.columns:
        n = seen.get(c, 0)
        seen[c] = n + 1
        cols.append(c if n == 0 else f"{c} ({n + 1})")
    if cols != list(df.columns):
        df = df.copy()
        df.columns = cols
    return df


def column_to_si(si_label, values):
    """Convert a displayed column (whose SI label is si_label) back to SI values."""
    m = _BRACKET.search(si_label)
    if not m:
        return list(values)
    return [to_si(m.group(1), v, si_label) for v in values]
