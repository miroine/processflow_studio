"""Printable simulation report (self-contained HTML; print to PDF from the browser)."""
from __future__ import annotations

import datetime as _dt
import html

import numpy as np

from procsim.streams import stream_properties, hydrate_risk
from procsim.unitops import CATALOGUE

from .state import fmt, qfmt
from . import units as U

CSS = """
@page { size: A4 landscape; margin: 14mm; }
body { font-family: "Segoe UI", Arial, sans-serif; color: #1d2733; font-size: 10.5pt; margin: 0 24px; }
h1 { font-size: 20pt; margin: 18px 0 2px; color: #243746; }
h2 { font-size: 13pt; margin: 22px 0 8px; color: #243746; border-bottom: 3px solid #007079; padding-bottom: 3px; }
h3 { font-size: 11pt; margin: 14px 0 4px; }
.meta { color: #5d6b7a; margin-bottom: 12px; }
.kpis { display: flex; gap: 12px; flex-wrap: wrap; margin: 10px 0; }
.kpi { border: 1px solid #d5dbe3; border-radius: 6px; padding: 8px 14px; min-width: 150px; }
.kpi b { display: block; font-size: 14pt; color: #243746; }
.kpi { border-left: 4px solid #007079 !important; }
.kpi span { color: #5d6b7a; font-size: 9pt; }
table { border-collapse: collapse; width: 100%; margin: 4px 0 12px; font-size: 9pt; page-break-inside: avoid; }
th, td { border: 1px solid #d5dbe3; padding: 3px 6px; text-align: right; }
th { background: #deedee; color: #004f55; }
td:first-child, th:first-child { text-align: left; }
.units { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 10px; }
.unit { border: 1px solid #d5dbe3; border-radius: 6px; padding: 6px 10px; page-break-inside: avoid; }
.warn { color: #b3261e; }
.pfd { border: 1px solid #d5dbe3; border-radius: 6px; padding: 6px; page-break-inside: avoid; }
.pfd svg { width: 100%; height: auto; max-height: 150mm; }
.note { color: #5d6b7a; font-style: italic; }
.break { page-break-before: always; }
.banner { background: linear-gradient(100deg,#004F55,#007079); color: #fff; padding: 14px 20px; border-radius: 8px;
  margin-top: 14px; border-bottom: 4px solid #FF1243; }
.banner h1 { color: #fff; margin: 0; }
.banner .meta { color: #e6f2f2; margin: 4px 0 0; }
.credit { margin-top: 26px; border-top: 3px solid #007079; padding-top: 8px; font-size: 9pt; color: #3d3d3d; }
@media print { .noprint { display: none; } }
"""

KEY_PROPS = ["Phase", "Vapour fraction", "Temperature [°C]", "Pressure [bar(a)]", "Molar flow [kmol/h]",
             "Mass flow [kg/h]", "Std gas flow [MSm³/d]", "Std liq vol flow [m³/h]", "Actual vol flow [m³/h]",
             "Mass density [kg/m³]", "Molecular weight", "Heat flow [kW]", "Viscosity vapour [cP]",
             "Viscosity liquid [cP]", "Hydrate margin [°C]"]


def _e(x):
    return html.escape(str(x))


def _table(header, rows):
    h = "".join(f"<th>{_e(c)}</th>" for c in header)
    b = "".join("<tr>" + "".join(f"<td>{_e(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><tr>{h}</tr>{b}</table>"


def build_report(model, sol, svg=None, title="Process simulation report", project=""):
    fp = sol.fp
    now = _dt.datetime.now().strftime("%d %b %Y %H:%M")
    work = sum(e.duty_kW for e in sol.energy if e.kind == "work")
    heat_in = sum(e.duty_kW for e in sol.energy if e.kind == "heat" and e.duty_kW > 0)
    heat_out = -sum(e.duty_kW for e in sol.energy if e.kind == "heat" and e.duty_kW < 0)
    bad = [(model["units"][k]["name"], v, sol.errors.get(k, "")) for k, v in sol.status.items()
           if k in model["units"] and v in ("error", "missing", "unsolved")]
    warns = [(model["units"][k]["name"], (sol.results.get(k) or {}).get("Warning"))
             for k in sol.results if k in model["units"] and (sol.results.get(k) or {}).get("Warning")]
    for sid, s in model["streams"].items():
        st_ = sol.streams.get(sid)
        if st_ is not None and hydrate_risk(st_, fp):
            p = stream_properties(st_, fp)
            warns.append((s["name"], f"below hydrate T ({p['Hydrate T (inhibited) [°C]']:.1f} °C incl. inhibitor)"))
    parts = [f"<!doctype html><html><head><meta charset='utf-8'><title>{_e(title)}</title><style>{CSS}</style>"
             "</head><body>"]
    parts.append(f"<div class='banner'><h1>{_e(title)}</h1><div class='meta'>{_e(project)}{' · ' if project else ''}"
                 f"{now} · ProcessFlow Studio · {_e(fp.name)} ({len(fp.keys)} components) · units: {_e(U.system())}</div></div>")
    status = "Converged" if sol.converged and not bad else f"{len(bad)} object(s) not solved"
    n_units = sum(1 for u in model["units"].values() if u["type"] not in ("feed", "product"))
    parts.append("<div class='kpis'>" + "".join(
        f"<div class='kpi'><b>{_e(v)}</b><span>{_e(k)}</span></div>" for k, v in [
            ("Solver status", status), ("Unit operations", n_units), ("Material streams", len(model["streams"])),
            ("Net shaft work in", qfmt("Power [kW]", work, 0)), ("Heat added", qfmt("Duty [kW]", heat_in, 2 if U.field() else 0)),
            ("Heat removed", qfmt("Duty [kW]", heat_out, 2 if U.field() else 0))]) + "</div>")
    if bad or warns:
        parts.append("<h3>Items needing attention</h3><ul>" +
                     "".join(f"<li class='warn'><b>{_e(n)}</b>: {_e(m or v)}</li>" for n, v, m in bad) +
                     "".join(f"<li class='warn'><b>{_e(n)}</b>: {_e(m)}</li>" for n, m in warns) + "</ul>")
    parts.append("<h2>Process flow diagram</h2>")
    if svg:
        parts.append(f"<div class='pfd'>{svg}</div>")
    else:
        parts.append("<p class='note'>Use <b>Export SVG</b> on the diagram toolbar before downloading the report "
                     "to include the PFD here.</p>")
    # streams, 7 per table so it prints
    parts.append("<h2 class='break'>Material streams</h2>")
    sids = [sid for sid in model["streams"] if sid in sol.streams]
    for i in range(0, len(sids), 7):
        chunk = sids[i:i + 7]
        props = [stream_properties(sol.streams[s], fp) for s in chunk]
        rows = [[U.key(k)] + [fmt(U.kv(k, p.get(k))[1]) for p in props] for k in KEY_PROPS]
        parts.append(_table(["Property"] + [model["streams"][s]["name"] for s in chunk], rows))
    parts.append("<h3>Overall compositions (mole fraction)</h3>")
    for i in range(0, len(sids), 9):
        chunk = sids[i:i + 9]
        rows = [[k] + [fmt(sol.streams[s].z[j]) for s in chunk] for j, k in enumerate(fp.keys)]
        parts.append(_table(["Component"] + [model["streams"][s]["name"] for s in chunk], rows))
    parts.append("<h2 class='break'>Unit operations</h2><div class='units'>")
    for uid, u in model["units"].items():
        if u["type"] in ("feed", "product"):
            continue
        res = sol.results.get(uid) or {}
        rows = [[k2, fmt(v2)] for k2, v2 in (U.kv(k, v) for k, v in res.items())]
        err = sol.errors.get(uid)
        parts.append(f"<div class='unit'><h3>{_e(u['name'])} <span class='note'>— "
                     f"{_e(CATALOGUE[u['type']]['label'])}</span></h3>"
                     + (f"<p class='warn'>{_e(err)}</p>" if err else "") + _table(["Result", "Value"], rows)
                     + "</div>")
    parts.append("</div>")
    if sol.energy:
        parts.append("<h2>Energy streams</h2>" + _table(
            ["Energy stream", "Unit", "Kind", "Duty [kW]"],
            [[e.name, e.unit, e.kind, fmt(e.duty_kW, 1)] for e in sol.energy]) if not U.field() else _table(
            ["Energy stream", "Unit", "Kind", "Duty"],
            [[e.name, e.unit, e.kind, qfmt("Power [kW]" if e.kind == "work" else "Duty [kW]", e.duty_kW, 2)]
             for e in sol.energy]))
    try:
        from procsim.economics import compute
        ec = compute(model, sol)
        cur = ec["params"]["currency"]
        tt = ec["totals"]
        erows = []
        for k, v in tt.items():
            k2, v2 = U.kv(k, v)
            erows.append([k2.replace("cur", cur), fmt(v2)])
        parts.append("<h2>Economics &amp; CO₂ (screening)</h2><p class='note'>Energy source: "
                     f"{_e(ec['params']['driver'])}; heating: {_e(ec['params']['heating'])}; "
                     f"{ec['params']['hours']:.0f} h/y. Prices and factors are user inputs.</p>" +
                     _table(["Quantity", "Value"], erows))
    except Exception:            # economics are optional in the report
        pass
    fl = [[c.key, c.name, fmt(c.MW), fmt(c.Tc - 273.15), fmt(c.Pc), fmt(c.omega)] for c in fp.comps]
    parts.append("<h2>Fluid package</h2><p>Peng-Robinson (1978), Peneloux volume shift, LBC viscosity; "
                 "standard conditions 15 °C / 1.01325 bar.</p>" +
                 _table(["Key", "Component", "MW", "Tc [°C]", "Pc [bar]", "ω"], fl))
    parts.append("<p class='note'>Screening-level results. Hydrate temperatures use the Motiee (1991) gas-gravity "
                 "correlation; pipe pressure drops use Beggs &amp; Brill (1973) with Payne corrections.</p>")
    from .theme import AUTHOR, DISCLAIMER
    parts.append(f"<div class='credit'>Made by <b>{_e(AUTHOR)}</b> · {_e(DISCLAIMER)}</div>")
    parts.append("</body></html>")
    return "".join(parts)
