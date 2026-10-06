"""Profile (time-series) simulation (procsim.timeseries): one steady-state solve per time step.

The user gives a table - one row per time step - with the feed rate, pressure and temperature (or any other
unit parameter) for that step.  Each row is applied to a copy of the flowsheet, the flowsheet is solved, and
the chosen results are collected.  A blank cell keeps the value of the flowsheet.

This is a *quasi-steady* profile: every step is a complete steady-state solution (recycles and Adjust loops
included) and nothing carries over from one step to the next.  There is no hold-up, no inventory and no
dynamics between steps - it answers "what does the plant do at the rates, pressures and temperatures of
year 3?", not "how does it get there".  Cumulative quantities integrate the steady results over the step
durations (rectangle rule, each step held until the next row's time).
"""
from __future__ import annotations

import copy
import math
import time as _time

from .flowsheet import solve
from .streams import stream_properties

MAX_STEPS = 400
TIME_UNITS = {"h": 1.0 / 24.0, "d": 1.0, "week": 7.0, "month": 365.25 / 12.0, "y": 365.25}   # in days
FEED_VARS = {"flow": "flow", "P": "P_bar", "T": "T_C"}
SEP = " | "
_DROP = ("scenarios", "fieldlife_result", "prognosis_result", "profile", "profile_result", "fieldlife",
         "prognosis", "design", "waxsand", "power", "fa2")
_STREAM_PROPS = ("Temperature [°C]", "Pressure [bar(a)]", "Vapour fraction", "Molar flow [kmol/h]",
                 "Mass flow [kg/h]", "Std gas flow [MSm³/d]", "Std liq vol flow [m³/h]", "Actual vol flow [m³/h]",
                 "Heat flow [kW]", "GCV (dry) [MJ/Sm³]", "Wobbe index [MJ/Sm³]", "Hydrate margin [°C]")
CUMULATIVE = {            # KPI label -> (cumulative label, factor from "per day" or "kW" to the cumulative unit)
    "Overall | Gas products [MSm³/d]": ("Cumulative gas [MSm³]", 1.0),
    "Overall | Liquid products [Sm³/d]": ("Cumulative liquid [Sm³]", 1.0),
    "Overall | Power demand [kW]": ("Cumulative power [MWh]", 24.0 / 1000.0),
    "Overall | CO₂ emissions [t/y]": ("Cumulative CO₂ [t]", 1.0 / 365.25),
}


class ProfileError(ValueError):
    pass


# ---------------------------------------------------------------------------------------------- table

def feeds(model):
    """[(uid, name, flow basis)] of the feed units, by name."""
    out = [(u, d["name"], d["params"].get("flow_basis", "kmol/h")) for u, d in model["units"].items()
           if d["type"] == "feed"]
    return sorted(out, key=lambda t: t[1])


def column(name, var):
    return f"{name}{SEP}{var}"


def feed_columns(model, variables=("flow", "P", "T"), names=None):
    return [column(n, FEED_VARS[v]) for _, n, _ in feeds(model) if names is None or n in names for v in variables]


def column_label(model, col):
    """Header with the unit, e.g. 'Well feed | flow [MSm³/d]'."""
    name, _, var = col.partition(SEP)
    u = next((d for d in model["units"].values() if d["name"] == name), None)
    if u is None:
        return col
    if var == "flow":
        return f"{col} [{u['params'].get('flow_basis', 'kmol/h')}]"
    if var == "P_bar":
        return f"{col} [bar(a)]"
    if var == "T_C":
        return f"{col} [°C]"
    return col


def _blank(v):
    return v is None or v == "" or (isinstance(v, float) and math.isnan(v))


def base_row(model, cols):
    out = {}
    for c in cols:
        name, _, var = c.partition(SEP)
        u = next((d for d in model["units"].values() if d["name"] == name), None)
        out[c] = None if u is None else u["params"].get(var)
    return out


def generate(model, n=8, dt=1.0, t0=0.0, flow_end=1.0, P_end=1.0, T_end_delta=0.0, shape="linear",
             variables=("flow", "P", "T"), names=None):
    """A starting table: n steps spaced dt apart, each chosen variable moving from the flowsheet value to
    value*factor (flow, pressure) or value+delta (temperature) at the last step.  shape: linear | exponential
    (exponential needs a positive end factor and only applies to flow and pressure)."""
    n = int(n)
    if n < 1 or n > MAX_STEPS:
        raise ProfileError(f"number of steps must be between 1 and {MAX_STEPS}")
    cols = feed_columns(model, variables, names)
    base = base_row(model, cols)
    rows = []
    for i in range(n):
        f = i / (n - 1) if n > 1 else 0.0
        row = {"Time": float(t0) + i * float(dt)}
        for c in cols:
            b = base[c]
            if b is None:
                row[c] = None
                continue
            var = c.rpartition(SEP)[2]
            if var == "T_C":
                row[c] = float(b) + float(T_end_delta) * f
            else:
                k = float(flow_end if var == "flow" else P_end)
                if shape == "exponential":
                    if k <= 0:
                        raise ProfileError("exponential shape needs a positive end factor")
                    row[c] = float(b) * k ** f
                else:
                    row[c] = float(b) * (1.0 + (k - 1.0) * f)
        rows.append(row)
    return rows


def parse_csv(text):
    """Rows from CSV text: first column Time, others 'Unit | parameter' (a trailing ' [unit]' is ignored)."""
    import csv
    import io
    lines = [ln for ln in io.StringIO(text.replace("﻿", "")).read().splitlines() if ln.strip()]
    if len(lines) < 2:
        raise ProfileError("the file needs a header row and at least one data row")
    delim = ";" if lines[0].count(";") > lines[0].count(",") else ("\t" if lines[0].count("\t") > lines[0].count(",") else ",")
    rd = list(csv.reader(lines, delimiter=delim))
    head = [_strip_unit(h) for h in rd[0]]
    if not head or head[0].lower() not in ("time", "t", "step"):
        raise ProfileError("the first column must be 'Time'")
    head[0] = "Time"
    rows = []
    for ln, rec in enumerate(rd[1:], start=2):
        row = {}
        for h, v in zip(head, rec):
            v = v.strip().replace(",", ".") if delim != "," else v.strip()
            if v == "":
                row[h] = None
                continue
            try:
                row[h] = float(v)
            except ValueError:
                raise ProfileError(f"line {ln}: '{v}' in column '{h}' is not a number")
        rows.append(row)
    return rows


def _strip_unit(h):
    h = h.strip()
    if h.endswith("]") and " [" in h:
        h = h[: h.rindex(" [")]
    return h


def to_csv(model, rows):
    cols = [c for c in (rows[0] if rows else {"Time": 0}) if c != "Time"]
    out = [",".join(["Time"] + [column_label(model, c).replace(",", ";") for c in cols])]
    for r in rows:
        out.append(",".join("" if _blank(r.get(c)) else repr(float(r[c])) for c in ["Time"] + cols))
    return "\n".join(out) + "\n"


def clean_rows(rows):
    """Plain list of dicts, numbers as float, blanks as None; raises on a missing or non-increasing time."""
    out = []
    for i, r in enumerate(rows, start=1):
        r = dict(r)
        t = r.get("Time")
        if _blank(t):
            raise ProfileError(f"row {i}: Time is missing")
        try:
            row = {"Time": float(t)}
            for k, v in r.items():
                if k != "Time":
                    row[k] = None if _blank(v) else float(v)
        except (TypeError, ValueError):
            raise ProfileError(f"row {i}: a value is not a number")
        out.append(row)
    if not out:
        raise ProfileError("the profile has no rows")
    if len(out) > MAX_STEPS:
        raise ProfileError(f"at most {MAX_STEPS} time steps")
    for a, b in zip(out, out[1:]):
        if b["Time"] <= a["Time"]:
            raise ProfileError("Time must increase from row to row")
    return out


def check_columns(model, rows):
    """Problems with the column names (unknown unit, parameter that is not a number)."""
    names = {d["name"]: d for d in model["units"].values()}
    count = {}
    for d in model["units"].values():
        count[d["name"]] = count.get(d["name"], 0) + 1
    bad = []
    for c in rows[0]:
        if c == "Time":
            continue
        n, sep, p = c.partition(SEP)
        if not sep or n not in names:
            bad.append(f"'{c}': no unit called '{n}'")
        elif count[n] > 1:
            bad.append(f"'{c}': {count[n]} units are called '{n}' - rename them to tell them apart")
        elif p not in names[n]["params"]:
            bad.append(f"'{c}': '{n}' has no parameter '{p}'")
        elif isinstance(names[n]["params"][p], (str, bool, dict, list)):
            bad.append(f"'{c}': parameter '{p}' is not a number")
    return bad


def warnings(model, rows):
    out = []
    names = {d["name"]: d for d in model["units"].values()}
    for c in rows[0]:
        n, _, p = c.partition(SEP)
        u = names.get(n)
        if u and u["type"] == "feed" and p == "T_C" and u["params"].get("spec", "T & P") != "T & P":
            out.append(f"{n} is specified by vapour fraction, so its temperature column is ignored.")
    return out


def apply_row(model, row):
    """Copy of the model with the row's overrides applied (blank cells keep the flowsheet value)."""
    m = {k: v for k, v in model.items() if k not in _DROP}
    m = copy.deepcopy(m)
    by_name = {d["name"]: d for d in m["units"].values()}
    for c, v in row.items():
        if c == "Time" or _blank(v):
            continue
        n, _, p = c.partition(SEP)
        by_name[n]["params"][p] = float(v)
    return m


# ---------------------------------------------------------------------------------------------- run

def collect(model, sol):
    """Flat {label: number} of everything worth plotting from one solution."""
    from . import scenarios as SC
    vals = {}
    for sid, s in model["streams"].items():
        stm = sol.streams.get(sid)
        if stm is None or stm.empty:
            continue
        try:
            p = stream_properties(stm, sol.fp)
        except Exception:
            continue
        for k in _STREAM_PROPS:
            v = p.get(k)
            if isinstance(v, (int, float)) and math.isfinite(v):
                vals[f"Stream {s['name']}{SEP}{k}"] = float(v)
    for uid, res in sol.results.items():
        name = model["units"][uid]["name"] if uid in model["units"] else uid
        for k, v in (res or {}).items():
            if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
                vals[f"Unit {name}{SEP}{k}"] = float(v)
    try:
        for k, v in SC.scenario_kpis(model, sol).items():
            if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
                vals[f"Overall{SEP}{k}"] = float(v)
    except Exception:
        pass
    return vals


def run(model, rows, progress=None, time_unit="d"):
    """Solve every row.  Returns {"time_unit", "columns", "steps": [...], "cumulative": {...}}.

    Each step: {"t", "status" (ok | warning | failed), "message", "seconds", "values": {label: number}}.
    A step that fails to solve does not stop the run; its values are empty and it is skipped in the cumulative."""
    if time_unit not in TIME_UNITS:
        raise ProfileError(f"time unit must be one of {', '.join(TIME_UNITS)}")
    rows = clean_rows(rows)
    bad = check_columns(model, rows)
    if bad:
        raise ProfileError("; ".join(bad))
    steps = []
    for i, row in enumerate(rows):
        t0 = _time.time()
        try:
            m = apply_row(model, row)
            sol = solve(m)
            bads = [(m["units"][u]["name"], sol.errors.get(u, v)) for u, v in sol.status.items()
                    if v in ("error", "missing", "unsolved") and u in m["units"]]
            if bads:
                st = {"status": "failed", "message": "; ".join(f"{n}: {e}" for n, e in bads[:3]), "values": {}}
            else:
                warn = [m["units"][u]["name"] for u, v in sol.status.items() if v == "warning" and u in m["units"]]
                st = {"status": "ok" if sol.converged and not warn else "warning",
                      "message": ("not converged" if not sol.converged else
                                  ("warnings: " + ", ".join(warn[:4]) if warn else "")),
                      "values": collect(m, sol)}
        except Exception as e:             # a bad row must not end the run
            st = {"status": "failed", "message": f"{type(e).__name__}: {e}", "values": {}}
        st.update(t=row["Time"], seconds=round(_time.time() - t0, 3))
        steps.append(st)
        if progress:
            progress(i + 1, len(rows))
    return {"time_unit": time_unit, "rows": rows, "steps": steps, "cumulative": cumulative(steps, time_unit)}


def durations_days(times, time_unit="d"):
    """Duration of each step in days: until the next row; the last step repeats the previous duration
    (a single-row profile counts as one unit of time)."""
    f = TIME_UNITS[time_unit]
    if len(times) == 1:
        return [f]
    d = [(b - a) * f for a, b in zip(times, times[1:])]
    return d + [d[-1]]


def cumulative(steps, time_unit="d"):
    days = durations_days([s["t"] for s in steps], time_unit)
    out = {}
    for lab, (clab, k) in CUMULATIVE.items():
        if not any(lab in s["values"] for s in steps):
            continue
        tot, series = 0.0, []
        for s, dd in zip(steps, days):
            v = s["values"].get(lab)
            if v is not None:
                tot += v * dd * k
            series.append(tot)
        out[clab] = series
    return out


def labels(result):
    seen = []
    for s in result["steps"]:
        for k in s["values"]:
            if k not in seen:
                seen.append(k)
    return seen


def series(result, label):
    return [s["values"].get(label) for s in result["steps"]]


def summary(result):
    n = len(result["steps"])
    ok = sum(s["status"] == "ok" for s in result["steps"])
    warn = sum(s["status"] == "warning" for s in result["steps"])
    fail = n - ok - warn
    return {"steps": n, "ok": ok, "warning": warn, "failed": fail,
            "seconds": round(sum(s["seconds"] for s in result["steps"]), 2)}


def frame_rows(result, labs):
    """Table rows: Time, status, the chosen labels, then the cumulative columns."""
    rows = []
    for i, s in enumerate(result["steps"]):
        r = {"Time": s["t"], "Status": s["status"] + (f" - {s['message']}" if s["message"] else "")}
        for lab in labs:
            r[lab] = s["values"].get(lab)
        for clab, ser in result["cumulative"].items():
            r[clab] = ser[i]
        rows.append(r)
    return rows
