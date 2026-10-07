"""Plotly figures — one visual system for every chart in the app.

Palette (validated with the dataviz colour checks on the white chart surface: lightness band,
chroma floor, adjacent-pair CVD separation >= 10, normal-vision >= 23, contrast >= 3:1):
the categorical order below is fixed and never cycled.  Semantic roles reuse the slots
consistently: hot side = orange, cold side = blue, pressure = teal.  Status colours are
reserved for limits (surge line, hydrate risk) and always carry a text label.
Single y-axis everywhere: two quantities of different scale go into stacked subplots.
"""
from __future__ import annotations

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from procsim.streams import stream_properties, PHASE_NAMES
from . import units as U


def _arr(si_unit, a, label="", **kw):
    return [U.value(si_unit, v, label, **kw) for v in a]

# ---- palette ---------------------------------------------------------------------------------
TEAL, ORANGE, BLUE, OCHRE, MAGENTA, GREEN = "#00909A", "#E8541E", "#3D5FC4", "#B08400", "#B1407E", "#3F8F3A"
SERIES = [TEAL, ORANGE, BLUE, OCHRE, MAGENTA, GREEN]
GREY = "#6F6F6F"
INK, INK2, GRID, AXIS = "#243746", "#565656", "#E6E6E6", "#BEBEBE"
CRITICAL, WARNING = "#EB0000", "#FF9200"         # status only (EDS danger / warning)
MOSS = "#007079"                                   # brand primary (UI chrome, not a data series)
RED = ORANGE                                       # backwards-compatible aliases: hot
FONT = "Equinor, Inter, 'Segoe UI', Arial, sans-serif"

_AXIS = dict(gridcolor=GRID, zeroline=False, linecolor=AXIS, showline=True, ticks="outside", tickcolor=AXIS,
             automargin=True)
LAYOUT = dict(template="plotly_white", colorway=SERIES, paper_bgcolor="#FFFFFF", plot_bgcolor="#FFFFFF",
              margin=dict(l=64, r=24, t=56, b=56), height=420,
              font=dict(family=FONT, size=12.5, color=INK2),
              hoverlabel=dict(font=dict(family=FONT, size=12), bgcolor="#FFFFFF", bordercolor=AXIS),
              legend=dict(orientation="h", y=-0.18, x=0, font=dict(size=12)),
              xaxis=_AXIS, yaxis=_AXIS)


def _title(text, sub=None):
    t = f"<b>{text}</b>" + (f"<br><span style='font-size:12px;color:{INK2}'>{sub}</span>" if sub else "")
    return dict(text=t, x=0, xanchor="left", font=dict(size=15, color=INK))


def _style(fig, title, sub=None, height=None, **kw):
    lay = dict(LAYOUT)
    lay.update(kw)
    if height:
        lay["height"] = height
    fig.update_layout(title=_title(title, sub), **lay)
    return fig


def _style_subplots(fig, title, sub=None, height=520):
    lay = {k: v for k, v in LAYOUT.items() if k not in ("xaxis", "yaxis")}
    lay["height"] = height
    if sub:                                   # room for the subtitle above the subplot titles
        lay["margin"] = dict(LAYOUT["margin"], t=92)
    fig.update_layout(title=_title(title, sub), **lay)
    fig.update_xaxes(**_AXIS)
    fig.update_yaxes(**_AXIS)
    return fig


def _alpha(hex_color, a):
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{a})"


# ---- phase envelope / flow assurance -------------------------------------------------------------

def phase_envelope(fp, z, T_min_C=-120.0, T_max_C=300.0, P_max=200.0, nT=56, nP=28):
    """Vapour-fraction map on a (P, T) grid. Returns T_C, P, grid."""
    Ps = np.linspace(1.0, P_max, nP)
    Ts, _, grid = fp.phase_envelope(np.asarray(z, float), Ps, T_min_C + 273.15, T_max_C + 273.15, nT)
    return Ts - 273.15, Ps, grid


def _envelope_traces(fig, tr, quality=True, label_quality=True):
    """Draw a traced envelope: shaded two-phase region, dew (orange) and bubble (blue) lines as smooth curves, the critical
    point, cricondenbar / cricondentherm and (optional) lines of constant vapour fraction."""
    uT, uP = U.uT(), U.uP()
    hov = f"%{{x:.1f}} {uT} · %{{y:.2f}} {uP}<extra></extra>"
    path = tr.get("path") or []
    if len(path) > 3:
        xs = [U.T(t - 273.15) for t, _ in path]
        ys = [U.P(p) for _, p in path]
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="none", fill="toself", fillcolor=_alpha(TEAL, .10), hoverinfo="skip",
                                 name="Two-phase region", showlegend=True))
    if quality:
        first = True
        for b, line in sorted((tr.get("isopleths") or {}).items()):
            fig.add_trace(go.Scatter(x=[U.T(t - 273.15) for t, _ in line], y=[U.P(p) for _, p in line], mode="lines",
                                     line=dict(color=GREY, width=1, dash="dot"), name="Vapour fraction",
                                     legendgroup="vf", showlegend=first, hovertemplate=f"vapour fraction {b:.2f}<br>" + hov))
            first = False
            if label_quality and len(line) > 8:
                pc = (tr.get("critical") or tr.get("cricondenbar") or (0.0, max(p_ for _, p_ in line)))[1]
                k = min(range(len(line)), key=lambda i_: abs(line[i_][1] - {0.1: 0.18, 0.25: 0.34, 0.5: 0.42, 0.75: 0.5, 0.9: 0.58}.get(round(b, 2), 0.4) * pc))
                fig.add_trace(go.Scatter(x=[U.T(line[k][0] - 273.15)], y=[U.P(line[k][1])], mode="text", text=[f"{b * 100:.0f} %"],
                                         textfont=dict(size=10, color=GREY), showlegend=False, hoverinfo="skip"))
    for key, col, lab in (("bubble", BLUE, "Bubble-point line"), ("dew", ORANGE, "Dew-point line")):
        pts = tr.get(key) or []
        if len(pts) > 1:
            fig.add_trace(go.Scatter(x=[U.T(t - 273.15) for t, _ in pts], y=[U.P(p) for _, p in pts], mode="lines", name=lab,
                                     line=dict(color=col, width=2.6), hovertemplate=lab + "<br>" + hov))
    cr = tr.get("critical")
    if cr:
        fig.add_trace(go.Scatter(x=[U.T(cr[0] - 273.15)], y=[U.P(cr[1])], mode="markers", name="Critical point",
                                 marker=dict(size=11, symbol="diamond", color="#FFFFFF", line=dict(color=INK, width=2)),
                                 hovertemplate="Critical point<br>" + hov))


def envelope_figure(tr, points=(), title="Phase envelope", ranges=None, quality=True):
    """Envelope from a traced dict (``procsim.envelope.trace_envelope``).  ``ranges`` = (Tmin, Tmax, Pmax) in °C / bar(a)."""
    fig = go.Figure()
    _envelope_traces(fig, tr, quality)
    for key, sym, lab in (("cricondenbar", "triangle-up", "Cricondenbar"), ("cricondentherm", "triangle-right", "Cricondentherm")):
        v = tr.get(key)
        if v:
            fig.add_trace(go.Scatter(x=[U.T(v[0] - 273.15)], y=[U.P(v[1])], mode="markers+text", text=[lab],
                                     textposition="top left" if key == "cricondentherm" else "top right", name=lab,
                                     marker=dict(size=13, symbol=sym, color=INK, line=dict(color="#FFFFFF", width=1.5)),
                                     hovertemplate=f"{lab}: %{{x:.1f}} {U.uT()} · %{{y:.1f}} {U.uP()}<extra></extra>"))
    if points:
        fig.add_trace(go.Scatter(x=[U.T(p[1]) for p in points], y=[U.P(p[2]) for p in points], mode="markers+text",
                                 text=[p[0] for p in points], textposition="top center",
                                 marker=dict(size=10, color=INK, line=dict(color="#FFFFFF", width=2)),
                                 name="Stream", hovertemplate=f"%{{text}}<br>%{{x:.1f}} {U.uT()} · %{{y:.1f}} {U.uP()}"
                                                              "<extra></extra>"))
    xa, ya = dict(_AXIS, title=f"Temperature [{U.uT()}]"), dict(_AXIS, title=f"Pressure [{U.uP()}]")
    if ranges:                                   # the window requested, trimmed to the envelope (and the stream point)
        path = tr.get("path") or []
        Ts = [t - 273.15 for t, _ in path] + [p_[1] for p_ in points]
        Ps = [p for _, p in path] + [p_[2] for p_ in points]
        t_lo, t_hi, p_hi = ranges[0], ranges[1], ranges[2]
        if Ts:
            t_lo, t_hi = max(t_lo, min(Ts) - 12.0), min(t_hi, max(Ts) + 25.0)
            p_hi = min(p_hi, 1.12 * max(Ps))
        if t_hi > t_lo and p_hi > 0:
            xa["range"] = [U.T(t_lo), U.T(t_hi)]
            ya["range"] = [0, U.P(p_hi)]
    meth = "traced by continuation" if tr.get("method") == "continuation" else "bisection on the phase count"
    return _style(fig, title, f"Peng-Robinson, hydrocarbon phases ({meth}); shaded = two-phase region", xaxis=xa, yaxis=ya)


def flow_assurance_figure(env, trajectory, hydrate_curves, title="Flow assurance: P–T trajectory"):
    """Envelope (optional) + hydrate curves + the P-T path of a sequence of streams.

    env: (T_C, P, grid) or None; trajectory: list of (name, T_C, P, at_risk);
    hydrate_curves: list of (label, P array, T array, dashed)."""
    fig = go.Figure()
    if env is not None:
        _envelope_traces(fig, env, quality=False)
    uT, uP = U.uT(), U.uP()
    for k, (label, Ph, Th, dashed) in enumerate(hydrate_curves):
        fig.add_trace(go.Scatter(x=_arr("°C", Th), y=_arr("bar(a)", Ph), mode="lines", name=label,
                                 line=dict(color=MAGENTA, width=2.5, dash="dash" if dashed else "solid"),
                                 hovertemplate=label + f"<br>%{{x:.1f}} {uT} at %{{y:.0f}} {uP}<extra></extra>"))
    trajectory = [(t[0], U.T(t[1]), U.P(t[2]), t[3]) for t in trajectory]
    if trajectory:
        fig.add_trace(go.Scatter(x=[t[1] for t in trajectory], y=[t[2] for t in trajectory], mode="lines+markers",
                                 name="Process path", line=dict(color=INK, width=2),
                                 marker=dict(size=9, color=[CRITICAL if t[3] else INK for t in trajectory],
                                             line=dict(color="#FFFFFF", width=2)),
                                 text=[t[0] for t in trajectory],
                                 hovertemplate=f"<b>%{{text}}</b><br>%{{x:.1f}} {uT} · %{{y:.1f}} {uP}<extra></extra>"))
        risky = [t for t in trajectory if t[3]]
        if risky:
            fig.add_trace(go.Scatter(x=[t[1] for t in risky], y=[t[2] for t in risky], mode="text",
                                     text=[f"⚠ {t[0]}" for t in risky], textposition="middle right",
                                     textfont=dict(color=CRITICAL, size=11), name="Below hydrate curve",
                                     hoverinfo="skip"))
        # label first and last stream only (selective direct labels)
        ends = [trajectory[0], trajectory[-1]] if len(trajectory) > 1 else trajectory
        fig.add_trace(go.Scatter(x=[t[1] for t in ends], y=[t[2] for t in ends], mode="text",
                                 text=[t[0] for t in ends], textposition="top center", showlegend=False,
                                 textfont=dict(color=INK, size=11), hoverinfo="skip"))
    return _style(fig, title, "Red markers sit on the hydrate side of the inhibited curve",
                  xaxis=dict(_AXIS, title=f"Temperature [{uT}]"), yaxis=dict(_AXIS, title=f"Pressure [{uP}]"),
                  height=500)


# ---- heat transfer ---------------------------------------------------------------------------------

def hx_figure(curve, name):
    fig = go.Figure()
    q = _arr("kW", curve["q"], power=False)
    uq, uT = U.unit("kW", power=False), U.uT()
    fig.add_trace(go.Scatter(x=q, y=_arr("°C", curve["Th"]), mode="lines+markers", name="Hot side",
                             line=dict(color=ORANGE, width=2.5), marker=dict(size=8),
                             hovertemplate=f"Hot %{{y:.1f}} {uT} at %{{x:,.2f}} {uq}<extra></extra>"))
    fig.add_trace(go.Scatter(x=q, y=_arr("°C", curve["Tc"]), mode="lines+markers", name="Cold side",
                             line=dict(color=BLUE, width=2.5), marker=dict(size=8),
                             hovertemplate=f"Cold %{{y:.1f}} {uT} at %{{x:,.2f}} {uq}<extra></extra>"))
    th, tc = np.array(curve["Th"]), np.array(curve["Tc"])
    k = int(np.argmin(th - tc))
    fig.add_annotation(x=q[k], y=U.T((th[k] + tc[k]) / 2),
                       text=f"min approach {U.value('°C', curve['min_approach'], delta=True):.1f} {uT}",
                       showarrow=False, xanchor="left", font=dict(color=INK, size=11))
    return _style(fig, f"{name} — heat curve", "Counter-current, zoned from the hot end",
                  xaxis=dict(_AXIS, title=f"Heat transferred [{uq}]"), yaxis=dict(_AXIS, title=f"Temperature [{uT}]"))


# ---- convergence -------------------------------------------------------------------------------------

def recycle_figure(log):
    fig = go.Figure()
    for n in sorted({r["recycle"] for r in log}):
        rows = [r for r in log if r["recycle"] == n]
        fig.add_trace(go.Scatter(x=list(range(1, len(rows) + 1)), y=[max(r["flow error"], 1e-12) for r in rows],
                                 mode="lines+markers", name=n, marker=dict(size=8)))
    return _style(fig, "Recycle convergence", "Relative change of the tear-stream component flows",
                  xaxis=dict(_AXIS, title="Iteration"), yaxis=dict(_AXIS, title="Relative flow error", type="log"))


def adjust_figure(log):
    fig = go.Figure()
    for n in sorted({r["adjust"] for r in log}):
        rows = [r for r in log if r["adjust"] == n]
        fig.add_trace(go.Scatter(x=[r["iteration"] for r in rows], y=[r["error"] for r in rows],
                                 mode="lines+markers", name=n, marker=dict(size=8)))
    fig.add_hline(y=0, line=dict(color=AXIS, width=1))
    return _style(fig, "Adjust convergence", "Target error per iteration",
                  xaxis=dict(_AXIS, title="Iteration"), yaxis=dict(_AXIS, title="Target error"))


# ---- energy / equipment ----------------------------------------------------------------------------

def energy_figure(sol):
    en = sorted([e for e in sol.energy if abs(e.duty_kW) > 1e-9], key=lambda e: abs(e.duty_kW))
    fig = go.Figure()
    uq = U.unit("kW", power=False)          # one axis: work shown in heat-duty units in field mode
    groups = [("Shaft work", lambda e: e.kind == "work", TEAL), ("Heat added", lambda e: e.kind == "heat" and
              e.duty_kW > 0, ORANGE), ("Heat removed", lambda e: e.kind == "heat" and e.duty_kW < 0, BLUE)]
    for label, pred, color in groups:
        sel = [e for e in en if pred(e)]
        if not sel:
            continue
        vals = [U.value("kW", abs(e.duty_kW), power=False) for e in sel]
        fig.add_trace(go.Bar(y=[e.name for e in sel], x=vals, orientation="h", name=label,
                             marker=dict(color=color, cornerradius=4),
                             text=[f"{v:,.0f}" if not U.field() else f"{v:,.2f}" for v in vals], textposition="outside",
                             hovertemplate="%{y}: %{x:,.2f} " + uq + "<extra>" + label + "</extra>"))
    return _style(fig, "Energy streams", "Absolute duty; colour shows the direction",
                  height=max(320, 70 + 30 * len(en)), barmode="relative", bargap=0.35,
                  xaxis=dict(_AXIS, title=uq), yaxis=dict(_AXIS, categoryorder="total ascending", title=""))


def tp_figure(model, sol):
    groups = {"Vapour": ([], ORANGE), "Two-phase": ([], OCHRE), "Liquid": ([], BLUE)}
    for sid, s in model["streams"].items():
        st_ = sol.streams.get(sid)
        if st_ is None or st_.empty:
            continue
        p = stream_properties(st_, sol.fp)
        vf = p["Vapour fraction"]
        g = "Vapour" if vf >= 0.9999 else ("Liquid" if vf <= 1e-6 else "Two-phase")
        groups[g][0].append((s["name"], U.T(p["Temperature [°C]"]), U.P(p["Pressure [bar(a)]"]), vf))
    fig = go.Figure()
    for g, (pts, color) in groups.items():
        if pts:
            fig.add_trace(go.Scatter(x=[q[1] for q in pts], y=[q[2] for q in pts], mode="markers", name=g,
                                     marker=dict(size=11, color=color, line=dict(color="#FFFFFF", width=2)),
                                     text=[q[0] for q in pts], customdata=[q[3] for q in pts],
                                     hovertemplate=f"<b>%{{text}}</b><br>%{{x:.1f}} {U.uT()} · %{{y:.2f}} {U.uP()}<br>"
                                                   "VF %{customdata:.3f}<extra></extra>"))
    return _style(fig, "Stream conditions", "Every solved stream, coloured by phase",
                  xaxis=dict(_AXIS, title=f"Temperature [{U.uT()}]"), yaxis=dict(_AXIS, title=f"Pressure [{U.uP()}]"))


def compressor_figure(model, sol):
    rows = [(u["name"], sol.results[uid]) for uid, u in model["units"].items()
            if u["type"] == "compressor" and uid in sol.results and "Power [kW]" in sol.results[uid]]
    up = U.unit("kW", power=True)
    fig = make_subplots(rows=1, cols=2, subplot_titles=(f"Shaft power [{up}]", f"Discharge temperature [{U.uT()}]"),
                        horizontal_spacing=0.12)
    if rows:
        names = [r[0] for r in rows]
        pw = [U.value("kW", r[1]["Power [kW]"], power=True) for r in rows]
        td = [U.T(r[1]["Outlet T [°C]"]) for r in rows]
        fig.add_trace(go.Bar(x=names, y=pw, marker=dict(color=TEAL, cornerradius=4),
                             name="Power", showlegend=False, text=[f"{v:,.0f}" for v in pw],
                             textposition="outside", hovertemplate="%{x}: %{y:,.0f} " + up + "<extra></extra>"),
                      row=1, col=1)
        fig.add_trace(go.Bar(x=names, y=td, marker=dict(color=ORANGE, cornerradius=4),
                             name="Discharge T", showlegend=False, text=[f"{v:.0f}" for v in td], textposition="outside",
                             hovertemplate="%{x}: %{y:.1f} " + U.uT() + "<extra></extra>"), row=1, col=2)
    return _style_subplots(fig, "Compressors", "Power and discharge temperature per machine", height=400)


def compressor_map(mp, name):
    """Head vs actual inlet flow: fan-law speed lines, surge and stonewall lines, operating point."""
    fq = U.value("m³/h", 1.0, "Actual gas flow")          # linear conversions (no offset)
    fh = U.value("kJ/kg", 1.0)
    uq, uh = U.unit("m³/h", "Actual gas flow"), U.unit("kJ/kg")
    mp = dict(mp)
    q0 = np.array(mp["curve"]["flow"]) * fq
    h0 = np.array(mp["curve"]["head"]) * fh
    e0 = np.array(mp["curve"]["eff"])
    for k_ in ("Q", "Q_process"):
        if mp.get(k_):
            mp[k_] = mp[k_] * fq
    if mp.get("head"):
        mp["head"] = mp["head"] * fh
    N0 = mp["N0"]
    fig = go.Figure()
    fr = [0.7, 0.8, 0.9, 1.0, 1.05]
    greys = ["#B9D9DC", "#8FC4C8", "#5FAAB0", TEAL, "#006A71"]      # sequential teal: slower = lighter
    from scipy.interpolate import PchipInterpolator
    qd = np.linspace(q0[0], q0[-1], 40)
    hd = PchipInterpolator(q0, h0)(qd)
    ed = PchipInterpolator(q0, e0)(qd)
    for r, c in zip(fr, greys):
        fig.add_trace(go.Scatter(x=qd * r, y=hd * r * r, mode="lines", name=f"{r * 100:.0f} % speed",
                                 line=dict(color=c, width=2), customdata=ed,
                                 hovertemplate=f"{r * N0:,.0f} rpm<br>%{{x:,.0f}} {uq} · %{{y:.1f}} {uh}<br>"
                                               "η_p %{customdata:.1f} %<extra></extra>"))
    rs = np.linspace(0.65, 1.1, 20)
    fig.add_trace(go.Scatter(x=q0[0] * rs, y=h0[0] * rs * rs, mode="lines", name="Surge line",
                             line=dict(color=CRITICAL, width=2, dash="dash"),
                             hovertemplate="Surge line<br>%{x:,.0f} " + uq + "<extra></extra>"))
    if mp.get("control_line"):
        cl = mp["control_line"]
        fig.add_trace(go.Scatter(x=q0[0] * cl * rs, y=h0[0] * rs * rs, mode="lines", name="Anti-surge control line",
                                 line=dict(color=WARNING, width=2, dash="dash"),
                                 hovertemplate="Control line<br>%{x:,.0f} " + uq + "<extra></extra>"))
    if mp.get("Q_process") and mp.get("Q") and mp["Q"] > mp["Q_process"] * 1.0001:
        fig.add_trace(go.Scatter(x=[mp["Q_process"], mp["Q"]], y=[mp["head"], mp["head"]], mode="lines+markers",
                                 name="Anti-surge recycle", line=dict(color=WARNING, width=2),
                                 marker=dict(size=8, color=WARNING),
                                 hovertemplate="%{x:,.0f} " + uq + "<extra>process → machine flow</extra>"))
    fig.add_trace(go.Scatter(x=q0[-1] * rs, y=h0[-1] * rs * rs, mode="lines", name="Stonewall",
                             line=dict(color=GREY, width=2, dash="dot"), hoverinfo="skip"))
    if mp.get("Q") and mp.get("head"):
        lab = f"Operating point · {mp['N']:,.0f} rpm" if mp.get("N") else "Operating point"
        fig.add_trace(go.Scatter(x=[mp["Q"]], y=[mp["head"]], mode="markers+text", name=lab,
                                 text=["  " + lab], textposition="middle right", textfont=dict(color=INK, size=11),
                                 marker=dict(size=14, color=INK, symbol="diamond", line=dict(color="#FFFFFF", width=2)),
                                 hovertemplate="%{x:,.0f} " + uq + " · %{y:.1f} " + uh + "<extra>Operating point</extra>"))
    return _style(fig, f"{name} — performance map", "Fan laws: Q ∝ N, head ∝ N²", height=460,
                  xaxis=dict(_AXIS, title=f"Actual inlet flow [{uq}]"), yaxis=dict(_AXIS, title=f"Polytropic head [{uh}]"))


def booster_map(mp, name):
    """Subsea booster map: boost ΔP vs actual flow per machine, fan-law speed lines, minimum-flow and run-out
    limits, operating point."""
    fq = U.value("m³/h", 1.0, "Actual liquid flow")
    uq, uP = U.unit("m³/h", "Actual liquid flow"), U.unit("bar")
    fp_ = U.value("bar", 1.0)
    q0 = np.array(mp["curve"]["flow"]) * fq
    h0 = np.array(mp["curve"]["head"]) * fp_
    e0 = np.array(mp["curve"]["eff"])
    N0 = mp["N0"]
    fig = go.Figure()
    from scipy.interpolate import PchipInterpolator
    qd = np.linspace(q0[0], q0[-1], 40)
    hd = PchipInterpolator(q0, h0)(qd)
    ed = PchipInterpolator(q0, e0)(qd)
    fr = [0.7, 0.8, 0.9, 1.0, 1.1]
    shades = ["#B9D9DC", "#8FC4C8", "#5FAAB0", TEAL, "#006A71"]
    for r, c in zip(fr, shades):
        fig.add_trace(go.Scatter(x=list(qd * r), y=list(hd * r * r), mode="lines", name=f"{r * 100:.0f} % speed",
                                 line=dict(color=c, width=2), customdata=list(ed),
                                 hovertemplate=f"{r * N0:,.0f} rpm<br>%{{x:,.0f}} {uq} · %{{y:.1f}} {uP}<br>"
                                               "η %{customdata:.1f} %<extra></extra>"))
    rs = np.linspace(0.65, 1.15, 20)
    fig.add_trace(go.Scatter(x=list(q0[0] * rs), y=list(h0[0] * rs * rs), mode="lines", name="Minimum flow",
                             line=dict(color=CRITICAL, width=2, dash="dash"), hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=list(q0[-1] * rs), y=list(h0[-1] * rs * rs), mode="lines", name="Run-out",
                             line=dict(color=GREY, width=2, dash="dot"), hoverinfo="skip"))
    if mp.get("Q") and mp.get("dP"):
        lab = f"Operating point · {mp['N']:,.0f} rpm"
        fig.add_trace(go.Scatter(x=[mp["Q"] * fq], y=[mp["dP"] * fp_], mode="markers+text", name=lab,
                                 text=["  " + lab], textposition="middle right", textfont=dict(color=INK, size=11),
                                 marker=dict(size=14, color=INK, symbol="diamond", line=dict(color="#FFFFFF", width=2)),
                                 hovertemplate="%{x:,.0f} " + uq + " · %{y:.1f} " + uP + "<extra>Operating point</extra>"))
    return _style(fig, f"{name} — booster map (per machine)", "Fan laws: flow ∝ N, boost ∝ N²", height=460,
                  xaxis=dict(_AXIS, title=f"Actual inlet flow per machine [{uq}]"),
                  yaxis=dict(_AXIS, title=f"Boost per machine [{uP}]"))


# ---- pipe ------------------------------------------------------------------------------------------------

def pipe_figure(prof, name):
    uL, uT, uP = U.unit("m"), U.uT(), U.uP()
    L = _arr("m", prof["L"])
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08,
                        subplot_titles=(f"Pressure [{uP}]", f"Temperature [{uT}]"))
    fig.add_trace(go.Scatter(x=L, y=_arr("bar(a)", prof["P"]), mode="lines+markers", name="Pressure",
                             line=dict(color=TEAL, width=2.5), marker=dict(size=7), showlegend=False,
                             hovertemplate=f"%{{x:,.0f}} {uL}: %{{y:.2f}} {uP}<extra></extra>"), row=1, col=1)
    fig.add_trace(go.Scatter(x=L, y=_arr("°C", prof["T"]), mode="lines+markers", name="Temperature",
                             line=dict(color=ORANGE, width=2.5), marker=dict(size=7), showlegend=False,
                             hovertemplate=f"%{{x:,.0f}} {uL}: %{{y:.1f}} {uT}<extra></extra>"), row=2, col=1)
    fig.update_xaxes(title=f"Distance [{uL}]", row=2, col=1)
    return _style_subplots(fig, f"{name} — pressure and temperature profile", height=520)


def holdup_figure(prof, name):
    n = len(prof["L"])
    uL, uv = U.unit("m"), U.unit("m/s")
    L = _arr("m", prof["L"])
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08,
                        subplot_titles=("Liquid holdup [-]", f"Mixture velocity [{uv}]"))
    fig.add_trace(go.Scatter(x=L, y=prof["HL"][:n], mode="lines+markers", name="Holdup",
                             line=dict(color=BLUE, width=2.5), marker=dict(size=7), text=prof["regime"][:n],
                             showlegend=False,
                             hovertemplate=f"%{{x:,.0f}} {uL}: HL %{{y:.3f}}<br>%{{text}}<extra></extra>"),
                  row=1, col=1)
    fig.add_trace(go.Scatter(x=L, y=_arr("m/s", prof["vm"][:n]), mode="lines+markers", name="Velocity",
                             line=dict(color=TEAL, width=2.5), marker=dict(size=7), showlegend=False,
                             hovertemplate=f"%{{x:,.0f}} {uL}: %{{y:.2f}} {uv}<extra></extra>"), row=2, col=1)
    fig.update_xaxes(title=f"Distance [{uL}]", row=2, col=1)
    return _style_subplots(fig, f"{name} — liquid holdup and velocity", "Beggs & Brill flow regime in the hover",
                           height=520)


# ---- subsea (SURF) -------------------------------------------------------------------------------------

def surf_profile_figure(prof, name):
    """Elevation along the line and the hydrate margin (T minus inhibited hydrate T) - SURF units."""
    n = len(prof["L"])
    uL, uz, dT = U.unit("m"), U.unit("m"), U.unit("°C", "margin", delta=True)
    L = _arr("m", prof["L"])
    hm = prof.get("Hm") or [None] * n
    has_hm = any(v is not None for v in hm)
    rows = 2 if has_hm else 1
    titles = (f"Elevation relative to the inlet [{uz}]",) + ((f"Hydrate margin [{dT}]",) if has_hm else ())
    fig = make_subplots(rows=rows, cols=1, shared_xaxes=True, vertical_spacing=0.1, subplot_titles=titles)
    fig.add_trace(go.Scatter(x=L, y=_arr("m", prof.get("z") or [0.0] * n), mode="lines", name="Elevation",
                             line=dict(color=INK, width=3), fill="tozeroy", fillcolor=_alpha(TEAL, 0.12),
                             showlegend=False, hovertemplate=f"%{{x:,.0f}} {uL}: %{{y:,.1f}} {uz}<extra></extra>"),
                  row=1, col=1)
    if has_hm:
        hmd = [None if v is None else U.value("°C", v, "margin", delta=True) for v in hm]
        fig.add_trace(go.Scatter(x=L, y=hmd, mode="lines+markers", name="Hydrate margin",
                                 line=dict(color=MAGENTA, width=2.5), marker=dict(size=6), showlegend=False,
                                 hovertemplate=f"%{{x:,.0f}} {uL}: %{{y:.1f}} {dT}<extra></extra>"), row=2, col=1)
        fig.add_trace(go.Scatter(x=[L[0], L[-1]], y=[0.0, 0.0], mode="lines", name="Hydrate line",
                                 line=dict(color=CRITICAL, width=1.5, dash="dash"), showlegend=False,
                                 hoverinfo="skip"), row=2, col=1)
    fig.update_xaxes(title=f"Distance [{uL}]", row=rows, col=1)
    return _style_subplots(fig, f"{name} — geometry" + (" and hydrate margin" if has_hm else ""),
                           "Below the dashed line the fluid is inside the (inhibited) hydrate region" if has_hm else None,
                           height=480 if has_hm else 320)


def pressure_budget_figure(steps, title="Pressure budget"):
    """Waterfall of pressure along a flow path. steps: [(label, P_in, P_out)] in bar(a), first = source."""
    uP = U.uP()
    labels, base, height, color, text = [], [], [], [], []
    if not steps:
        return _style(go.Figure(), title)
    P0 = steps[0][1]
    labels.append("Start")
    base.append(0.0)
    height.append(U.value("bar(a)", P0))
    color.append(BLUE)
    text.append(f"{U.value('bar(a)', P0):,.1f}")
    for lab, pin, pout in steps:
        dp = pin - pout
        labels.append(lab)
        lo = U.value("bar(a)", min(pin, pout))
        base.append(lo)
        height.append(abs(U.value("bar(a)", pin) - U.value("bar(a)", pout)))
        color.append(ORANGE if dp >= 0 else GREEN)
        text.append(f"{'−' if dp >= 0 else '+'}{abs(U.value('bar', dp)):,.1f}")
    Pend = steps[-1][2]
    labels.append("End")
    base.append(0.0)
    height.append(U.value("bar(a)", Pend))
    color.append(GREY)
    text.append(f"{U.value('bar(a)', Pend):,.1f}")
    fig = go.Figure(go.Bar(x=labels, y=height, base=base, marker=dict(color=color), text=text,
                           textposition="outside", cliponaxis=False, showlegend=False,
                           hovertemplate="%{x}: %{text}<extra></extra>"))
    return _style(fig, title, f"Pressure losses (orange) and gains (green) in {U.unit('bar')}; start and end "
                              f"pressures in {uP}",
                  xaxis=dict(_AXIS, title=""), yaxis=dict(_AXIS, title=f"Pressure [{uP}]"), height=440)


def capex_figure(by_group, totals):
    """CAPEX by group (horizontal bars, one series) with the indirects - sorted largest first."""
    rows = [(g, v) for g, v in by_group.items() if v > 1e-9]
    rows += [("Engineering & management", totals["Engineering & management [MUSD]"]),
             ("Contingency", totals["Contingency [MUSD]"])]
    rows = sorted(rows, key=lambda r: r[1])
    fig = go.Figure(go.Bar(y=[r[0] for r in rows], x=[r[1] for r in rows], orientation="h",
                           marker=dict(color=TEAL, cornerradius=4), text=[f"{r[1]:,.0f}" for r in rows],
                           textposition="outside", cliponaxis=False, showlegend=False,
                           hovertemplate="%{y}: %{x:,.1f} MUSD<extra></extra>"))
    return _style(fig, f"CAPEX estimate: {totals['Total CAPEX [MUSD]']:,.0f} MUSD",
                  "Class 5 screening with illustrative catalogue costs; installation included in each group",
                  xaxis=dict(_AXIS, title="MUSD"), yaxis=dict(_AXIS, title=""), height=max(300, 90 + 48 * len(rows)))


def _rate_label(rf):
    return f"{rf:g} × base rate"


def tieback_pressure_figure(rows, P_min):
    """Arrival pressure vs tie-back distance, one line per rate factor; infeasible points are left out,
    points inside the hydrate region are ringed in the critical colour."""
    uP, uL = U.uP(), ("km" if not U.field() else "mi")
    conv = (lambda km: km) if not U.field() else (lambda km: km / 1.609344)
    fig = go.Figure()
    rfs = sorted({r["Rate factor"] for r in rows})
    for k, rf in enumerate(rfs[:len(SERIES)]):
        pts = sorted((r for r in rows if r["Rate factor"] == rf), key=lambda r: r["Distance [km]"])
        x = [conv(r["Distance [km]"]) for r in pts if r["Arrival P [bar(a)]"] is not None]
        y = [U.P(r["Arrival P [bar(a)]"]) for r in pts if r["Arrival P [bar(a)]"] is not None]
        fig.add_trace(go.Scatter(x=x, y=y, mode="lines+markers", name=_rate_label(rf),
                                 line=dict(color=SERIES[k], width=2.5), marker=dict(size=8),
                                 hovertemplate=f"{_rate_label(rf)}<br>%{{x:,.0f}} {uL}: %{{y:,.1f}} {uP}<extra></extra>"))
    risky = [r for r in rows if r["Arrival P [bar(a)]"] is not None and (r["Min. hydrate margin [°C]"] or 1) < 0]
    if risky:
        fig.add_trace(go.Scatter(x=[conv(r["Distance [km]"]) for r in risky], y=[U.P(r["Arrival P [bar(a)]"]) for r in risky],
                                 mode="markers", name="Hydrate margin < 0 (⚠)",
                                 marker=dict(size=15, color="rgba(0,0,0,0)", line=dict(color=CRITICAL, width=2.5)),
                                 hoverinfo="skip"))
    xs = [conv(r["Distance [km]"]) for r in rows]
    if xs:
        fig.add_trace(go.Scatter(x=[min(xs), max(xs)], y=[U.P(P_min)] * 2, mode="lines", name="Minimum arrival P",
                                 line=dict(color=GREY, width=1.5, dash="dash"), hoverinfo="skip"))
    return _style(fig, "Tie-back screening: arrival pressure", "Missing points: the line cannot deliver that rate "
                  "over that distance", xaxis=dict(_AXIS, title=f"Tie-back distance [{uL}]"),
                  yaxis=dict(_AXIS, title=f"Arrival pressure [{uP}]"), height=460)


def tieback_margin_figure(rows):
    """Minimum hydrate margin along flowline + riser vs distance, one line per rate factor."""
    dT = U.unit("°C", "margin", delta=True)
    uL = "km" if not U.field() else "mi"
    conv = (lambda km: km) if not U.field() else (lambda km: km / 1.609344)
    fig = go.Figure()
    rfs = sorted({r["Rate factor"] for r in rows})
    for k, rf in enumerate(rfs[:len(SERIES)]):
        pts = sorted((r for r in rows if r["Rate factor"] == rf and r["Min. hydrate margin [°C]"] is not None),
                     key=lambda r: r["Distance [km]"])
        fig.add_trace(go.Scatter(x=[conv(r["Distance [km]"]) for r in pts],
                                 y=[U.value("°C", r["Min. hydrate margin [°C]"], "margin", delta=True) for r in pts],
                                 mode="lines+markers", name=_rate_label(rf), line=dict(color=SERIES[k], width=2.5),
                                 marker=dict(size=8),
                                 hovertemplate=f"{_rate_label(rf)}<br>%{{x:,.0f}} {uL}: %{{y:.1f}} {dT}<extra></extra>"))
    xs = [conv(r["Distance [km]"]) for r in rows]
    if xs:
        fig.add_trace(go.Scatter(x=[min(xs), max(xs)], y=[0.0, 0.0], mode="lines", name="Hydrate line",
                                 line=dict(color=CRITICAL, width=1.5, dash="dash"), hoverinfo="skip"))
    return _style(fig, "Tie-back screening: hydrate margin", "Minimum along flowline and riser; below the dashed "
                  "line the line needs inhibition, insulation or heating",
                  xaxis=dict(_AXIS, title=f"Tie-back distance [{uL}]"), yaxis=dict(_AXIS, title=f"Hydrate margin [{dT}]"),
                  height=400)


def _rate_axis(rows):
    """x values (gas rate in display units) and axis title for turndown charts."""
    uq = U.unit("MSm³/d")
    return [U.value("MSm³/d", r["Gas rate [MSm³/d]"]) for r in rows], f"Gas rate [{uq}]"


def turndown_figure(rows, window, P_min):
    """Turndown envelope: arrival P, hydrate margin, liquid inventory and erosional ratio vs rate. Points outside
    the operating window are ringed in the critical colour; dashed lines are the limits."""
    rows = sorted([r for r in rows if r.get("Arrival P [bar(a)]") is not None], key=lambda r: r["Rate factor"])
    uP, dT, uV = U.uP(), U.unit("°C", "margin", delta=True), U.unit("m³")
    fig = make_subplots(rows=2, cols=2, shared_xaxes=True, vertical_spacing=0.14, horizontal_spacing=0.1,
                        subplot_titles=(f"Arrival pressure [{uP}]", f"Minimum hydrate margin [{dT}]",
                                        f"Liquid inventory, flowline + riser [{uV}]", "Erosional velocity ratio [-]"))
    x, xt = _rate_axis(rows)
    series = [
        (1, 1, [U.P(r["Arrival P [bar(a)]"]) for r in rows], U.P(P_min)),
        (1, 2, [None if r["Min. hydrate margin [°C]"] is None else U.value("°C", r["Min. hydrate margin [°C]"], "margin",
                                                                          delta=True) for r in rows], 0.0),
        (2, 1, [U.value("m³", r["Liquid inventory [m³]"]) for r in rows], None),
        (2, 2, [r["Erosional ratio [-]"] for r in rows], 1.0),
    ]
    bad = [not r["Feasible"] for r in rows]
    for k, (ro, co, y, lim) in enumerate(series):
        fig.add_trace(go.Scatter(x=x, y=y, mode="lines+markers", name="Operating point", showlegend=k == 0,
                                 legendgroup="op", line=dict(color=TEAL, width=2.5), marker=dict(size=8),
                                 text=[r["Limits"] or "within limits" for r in rows],
                                 hovertemplate="%{x:.2f}: %{y:.2f}<br>%{text}<extra></extra>"), row=ro, col=co)
        if any(bad):
            fig.add_trace(go.Scatter(x=[a for a, b_ in zip(x, bad) if b_], y=[a for a, b_ in zip(y, bad) if b_],
                                     mode="markers", name="Outside the window (⚠)", showlegend=k == 0, legendgroup="bad",
                                     marker=dict(size=15, color="rgba(0,0,0,0)", line=dict(color=CRITICAL, width=2.5)),
                                     hoverinfo="skip"), row=ro, col=co)
        if lim is not None and x:
            fig.add_trace(go.Scatter(x=[min(x), max(x)], y=[lim, lim], mode="lines", name="Limit", showlegend=k == 0,
                                     legendgroup="lim", line=dict(color=GREY, width=1.5, dash="dash"), hoverinfo="skip"),
                          row=ro, col=co)
    fig.update_xaxes(title=xt, row=2, col=1)
    fig.update_xaxes(title=xt, row=2, col=2)
    sub = (f"Operating window {window['min']:g}–{window['max']:g} × current rate" if window.get("feasible")
           else "No rate in the sweep meets every limit")
    return _style_subplots(fig, "Turndown envelope", sub, height=620)


_NODE_STYLE = {"host": ("Host facility", "square", BLUE, 18), "template": ("Template / manifold", "square", TEAL, 15),
               "well": ("Well (slot, not to scale)", "circle", ORANGE, 10),
               "subsea_booster": ("Subsea booster", "diamond", MAGENTA, 14),
               "subsea_pump": ("Subsea pump", "diamond", MAGENTA, 14),
               "subsea_compressor": ("Subsea compressor", "diamond-wide", MAGENTA, 15),
               "subsea_separator": ("Subsea separator", "hexagon", GREEN, 15),
               "subsea_valve": ("SSIV / HIPPS", "triangle-up", OCHRE, 12), "riser": ("Riser touch-down", "circle", BLUE, 9)}


def layout_figure(lay):
    """Plan view of the subsea field: host at the origin, lines to scale, well slots on a ring (not to scale)."""
    fig = go.Figure()
    styles = {"flowline": ("Flowline", INK, 3.5, "solid"), "jumper": ("Jumper / spool", GREY, 1.5, "solid"),
              "umbilical": ("Umbilical", OCHRE, 1.5, "dash"), "riser": ("Riser (horizontal footprint)", BLUE, 2.5, "solid")}
    km = "km" if not U.field() else "mi"
    f_ = 1.0 if not U.field() else 1 / 1.609344
    for kind, (name, color, w, dash) in styles.items():
        es = [e for e in lay["edges"] if e["kind"] == kind and (e["x0"], e["y0"]) != (e["x1"], e["y1"])]
        if not es:
            continue
        xs, ys, tx = [], [], []
        for e in es:
            xs += [e["x0"] * f_, e["x1"] * f_, None]
            ys += [e["y0"] * f_, e["y1"] * f_, None]
            tx += [e["label"], e["label"], ""]
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", name=name, line=dict(color=color, width=w, dash=dash),
                                 text=tx, hovertemplate="%{text}<extra></extra>", connectgaps=False))
    for t, (name, sym, color, size) in _NODE_STYLE.items():
        ns = [n for n in lay["nodes"] if n["type"] == t]
        if not ns:
            continue
        labelled = t in ("host", "template", "subsea_booster", "subsea_pump", "subsea_compressor", "subsea_separator")
        fig.add_trace(go.Scatter(x=[n["x"] * f_ for n in ns], y=[n["y"] * f_ for n in ns],
                                 mode="markers+text" if labelled else "markers", name=name,
                                 marker=dict(size=size, color=color, symbol=sym, line=dict(color="#FFFFFF", width=2)),
                                 text=[n["label"] for n in ns], textposition="top center",
                                 textfont=dict(color=INK, size=11),
                                 hovertemplate="%{text}<br>%{x:.2f}, %{y:.2f} " + km + "<extra></extra>"))
    return _style(fig, "Field layout (plan view)", "Host at the origin; line lengths to scale, bearings as set; "
                  "well slots drawn around their template, not to scale",
                  xaxis=dict(_AXIS, title=f"East [{km}]", zeroline=False),
                  yaxis=dict(_AXIS, title=f"North [{km}]", scaleanchor="x", scaleratio=1), height=560)


def deliverability_figure(pts, name, op=None, target=None, liquid=False):
    """Well deliverability: wellhead pressure vs rate (coarse tubing increments), operating point, WHP target."""
    key, uq_si = ("Liquid rate [Sm³/d]", "Sm³/d") if liquid else ("Gas rate [MSm³/d]", "MSm³/d")
    uq, uP = U.unit(uq_si), U.uP()
    ok = [p for p in pts if p["Wellhead P [bar(a)]"] is not None]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=[U.value(uq_si, p[key]) for p in ok], y=[U.P(p["Wellhead P [bar(a)]"]) for p in ok],
                             mode="lines+markers", name="Deliverability", line=dict(color=TEAL, width=2.5),
                             marker=dict(size=8), hovertemplate=f"%{{x:.3f}} {uq}: %{{y:.1f}} {uP}<extra></extra>"))
    if op is not None:
        fig.add_trace(go.Scatter(x=[U.value(uq_si, op[0])], y=[U.P(op[1])], mode="markers+text", name="Operating point",
                                 marker=dict(size=13, color=ORANGE, line=dict(color="#FFFFFF", width=2)),
                                 text=["operating point"], textposition="top right", textfont=dict(color=INK, size=11),
                                 hovertemplate=f"%{{x:.3f}} {uq}: %{{y:.1f}} {uP}<extra>operating point</extra>"))
    if target is not None and ok:
        xs = [U.value(uq_si, p[key]) for p in ok]
        fig.add_trace(go.Scatter(x=[min(xs), max(xs)], y=[U.P(target)] * 2, mode="lines", name="Wellhead P target",
                                 line=dict(color=GREY, width=1.5, dash="dash"), hoverinfo="skip"))
    last = pts[-1] if pts else None
    sub = ("The well cannot deliver more than about "
           f"{U.value(uq_si, last[key]):.3g} {uq} (tubing and inflow limit)" if last and last["Wellhead P [bar(a)]"] is None
           else "Wellhead pressure the well delivers at each rate (coarse tubing increments)")
    return _style(fig, f"{name} — deliverability", sub, xaxis=dict(_AXIS, title=f"{'Liquid' if liquid else 'Gas'} rate [{uq}]"),
                  yaxis=dict(_AXIS, title=f"Wellhead pressure [{uP}]"), height=420)


def cooldown_figure(lines):
    """Temperature after shut-in at each line's critical point, with its hydrate temperature (dashed, same colour)
    and the no-touch time marked."""
    uT = U.uT()
    fig = go.Figure()
    for k, r in enumerate(lines[:len(SERIES)]):
        c = SERIES[k]
        cv = r["curve"]
        fig.add_trace(go.Scatter(x=cv["t_h"], y=[U.T(t) for t in cv["T"]], mode="lines", name=f"{r['Line']} (critical point)",
                                 line=dict(color=c, width=2.5), legendgroup=r["Line"],
                                 hovertemplate=f"{r['Line']}<br>%{{x:.1f}} h: %{{y:.1f}} {uT}<extra></extra>"))
        if cv["T_hyd"] is not None:
            fig.add_trace(go.Scatter(x=[cv["t_h"][0], cv["t_h"][-1]], y=[U.T(cv["T_hyd"])] * 2, mode="lines",
                                     name=f"{r['Line']} hydrate T", legendgroup=r["Line"],
                                     line=dict(color=c, width=1.5, dash="dash"), hoverinfo="skip"))
        nt = r["No-touch time [h]"]
        if cv["T_hyd"] is not None and nt == nt and nt < cv["t_h"][-1]:
            fig.add_trace(go.Scatter(x=[nt], y=[U.T(cv["T_hyd"])], mode="markers+text", showlegend=False,
                                     legendgroup=r["Line"], marker=dict(size=11, color=c, line=dict(color="#FFFFFF", width=2)),
                                     text=[f"{nt:.1f} h"], textposition="top right", textfont=dict(color=INK, size=11),
                                     hovertemplate=f"{r['Line']}: no-touch time %{{x:.1f}} h<extra></extra>"))
    return _style(fig, "Cool-down after shut-in", "At each line's critical point; the marker is the no-touch time "
                  "(the fluid reaches the dashed hydrate temperature)", xaxis=dict(_AXIS, title="Time after shut-in [h]"),
                  yaxis=dict(_AXIS, title=f"Fluid temperature [{uT}]"), height=440)


def scenario_figure(vals, label, current_name):
    """One result across scenarios (bars; the unsaved current case in grey)."""
    names = [n for n, _ in vals]
    ys = [v for _, v in vals]
    fig = go.Figure(go.Bar(x=names, y=ys, marker=dict(color=[GREY if n == current_name else TEAL for n in names],
                                                      cornerradius=4),
                           text=[fmt_num(v) for v in ys], textposition="outside", cliponaxis=False, showlegend=False,
                           hovertemplate="%{x}: %{y:,.4g}<extra></extra>"))
    return _style(fig, label, "Saved scenarios in teal, the current (unsaved) case in grey",
                  xaxis=dict(_AXIS, title=""), yaxis=dict(_AXIS, title=label), height=400)


def fmt_num(v):
    a = abs(v)
    return f"{v:,.0f}" if a >= 100 else (f"{v:,.1f}" if a >= 10 else f"{v:,.2f}")


def heated_line_figure(prof, prof_off, name, T_set=None, T_hyd=None):
    """Heated flowline: fluid temperature along the line with and without heating, the set temperature and the
    hydrate temperature (top); heat delivered per metre along the line (bottom)."""
    uT, uL = U.uT(), U.unit("m")
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.12, row_heights=[0.62, 0.38],
                        subplot_titles=(f"Fluid temperature [{uT}]", "Heat into the fluid [W/m]"))
    L = _arr("m", prof["L"])
    fig.add_trace(go.Scatter(x=L, y=[U.T(t) for t in prof["T"]], mode="lines+markers", name="Heated",
                             line=dict(color=TEAL, width=2.5), marker=dict(size=7),
                             hovertemplate=f"%{{x:,.0f}} {uL}: %{{y:.1f}} {uT}<extra>heated</extra>"), row=1, col=1)
    if prof_off:
        fig.add_trace(go.Scatter(x=_arr("m", prof_off["L"]), y=[U.T(t) for t in prof_off["T"]], mode="lines+markers",
                                 name="Without heating", line=dict(color=ORANGE, width=2.5), marker=dict(size=7),
                                 hovertemplate=f"%{{x:,.0f}} {uL}: %{{y:.1f}} {uT}<extra>without heating</extra>"),
                      row=1, col=1)
    for val, lab, col, dash in ((T_set, "Set temperature", GREY, "dash"), (T_hyd, "Hydrate temperature", CRITICAL, "dot")):
        if val is not None:
            fig.add_trace(go.Scatter(x=[L[0], L[-1]], y=[U.T(val)] * 2, mode="lines", name=lab,
                                     line=dict(color=col, width=1.5, dash=dash), hoverinfo="skip"), row=1, col=1)
    q = prof.get("q_heat") or []
    if q:
        mids = [0.5 * (a + b) for a, b in zip(L[:-1], L[1:])]
        fig.add_trace(go.Bar(x=mids, y=q, name="Heat input", marker=dict(color=OCHRE, cornerradius=4), showlegend=False,
                             hovertemplate=f"%{{x:,.0f}} {uL}: %{{y:.0f}} W/m<extra></extra>"), row=2, col=1)
    fig.update_xaxes(title=f"Distance [{uL}]", row=2, col=1)
    return _style_subplots(fig, f"{name} — heated flowline", "Fixed input heats the whole line; hold control only "
                           "where the fluid would fall below the set temperature", height=560)


# ---- column ----------------------------------------------------------------------------------------------

def column_figure(prof, name):
    labels = prof["labels"]
    uT, uF = U.uT(), U.unit("kmol/h")
    fig = make_subplots(rows=1, cols=2, shared_yaxes=True, horizontal_spacing=0.06,
                        subplot_titles=(f"Temperature [{uT}]", f"Internal flows [{uF}]"))
    fig.add_trace(go.Scatter(x=_arr("°C", prof["T"]), y=labels, mode="lines+markers", name="Temperature",
                             line=dict(color=ORANGE, width=2.5), marker=dict(size=8), showlegend=False,
                             hovertemplate=f"Stage %{{y}}: %{{x:.1f}} {uT}<extra></extra>"), row=1, col=1)
    fig.add_trace(go.Scatter(x=_arr("kmol/h", prof["L"]), y=labels, mode="lines+markers", name="Liquid",
                             line=dict(color=BLUE, width=2.5), marker=dict(size=8),
                             hovertemplate=f"Stage %{{y}}: L %{{x:,.1f}} {uF}<extra></extra>"), row=1, col=2)
    fig.add_trace(go.Scatter(x=_arr("kmol/h", prof["V"]), y=labels, mode="lines+markers", name="Vapour",
                             line=dict(color=TEAL, width=2.5), marker=dict(size=8),
                             hovertemplate=f"Stage %{{y}}: V %{{x:,.1f}} {uF}<extra></extra>"), row=1, col=2)
    fig.update_yaxes(autorange="reversed", title="Stage (top → bottom)", row=1, col=1)
    fig.update_yaxes(autorange="reversed", row=1, col=2)
    return _style_subplots(fig, f"{name} — stage profiles", height=max(420, 26 * len(labels) + 160))


def column_comp_figure(prof, name, n_show=6):
    x = np.array(prof["x"])
    keys = prof["keys"]
    spread = x.max(0) - x.min(0)
    top = [i for i in np.argsort(-spread) if spread[i] > 1e-6][:n_show]
    fig = go.Figure()
    for k, i in enumerate(sorted(top)):
        fig.add_trace(go.Scatter(x=x[:, i], y=prof["labels"], mode="lines+markers", name=keys[i],
                                 line=dict(color=SERIES[k % len(SERIES)], width=2), marker=dict(size=7),
                                 hovertemplate=keys[i] + " stage %{y}: x = %{x:.4f}<extra></extra>"))
    return _style(fig, f"{name} — liquid composition", f"The {len(top)} components that change most along the column",
                  height=max(420, 26 * len(prof["labels"]) + 160), xaxis=dict(_AXIS, title="Liquid mole fraction"),
                  yaxis=dict(_AXIS, autorange="reversed", title="Stage (top → bottom)"))


# ---- flowsheet-level analysis ------------------------------------------------------------------------------

def sankey_figure(model, sol):
    """Mass-flow Sankey: nodes = feeds, unit operations, products; links = material streams."""
    ids = list(model["units"].keys())
    index = {u: k for k, u in enumerate(ids)}
    labels, colors = [], []
    for u in ids:
        t = model["units"][u]["type"]
        labels.append(model["units"][u]["name"])
        colors.append(TEAL if t == "feed" else (BLUE if t == "product" else "#8A9BA8"))
    src, tgt, val, lcol, lab = [], [], [], [], []
    for sid, s in model["streams"].items():
        st_ = sol.streams.get(sid)
        if st_ is None or st_.empty:
            continue
        m = st_.F * st_.MW
        if m <= 0:
            continue
        vf = st_.flash.vf
        c = ORANGE if vf >= 0.9999 else (BLUE if vf <= 1e-6 else OCHRE)
        src.append(index[s["src"][0]])
        tgt.append(index[s["dst"][0]])
        val.append(U.value("t/h", m / 1000.0))
        lcol.append(_alpha(c, 0.35))
        lab.append(s["name"])
    ut = U.unit("t/h")
    fig = go.Figure(go.Sankey(arrangement="snap", valueformat=",.1f", valuesuffix=" " + ut,
                              node=dict(label=labels, color=colors, pad=18, thickness=16,
                                        line=dict(color="#FFFFFF", width=1)),
                              link=dict(source=src, target=tgt, value=val, color=lcol, label=lab,
                                        hovertemplate="%{label}: %{value:,.1f} " + ut + "<extra></extra>")))
    lay = {k: v for k, v in LAYOUT.items() if k not in ("xaxis", "yaxis", "legend")}
    lay["height"] = 480
    fig.update_layout(title=_title("Mass flow through the flowsheet",
                                   "Links: orange vapour · ochre two-phase · blue liquid; width = " + ut), **lay)
    return fig


COMP_GROUPS = [("N₂ + CO₂ + H₂S", ("N2", "CO2", "H2S", "O2", "H2")), ("C1", ("C1",)), ("C2", ("C2",)),
               ("C3–C4", ("C3", "iC4", "nC4")), ("C5–C6", ("iC5", "nC5", "nC6")),
               ("C7+", ("nC7", "nC8", "nC9", "nC10")), ("Water + inhibitors", ("H2O", "MEG", "MeOH"))]


def _group_of(c):
    """Group label of a fluid component for the stacked composition bars (library keys first, then by family / size)."""
    for label, members in COMP_GROUPS:
        if c.key in members:
            return label
    if c.family == "hypo" or (c.family == "hydrocarbon" and c.MW >= 98.0):
        return "C7+"
    if c.family in ("hydrocarbon",) and c.MW < 98.0:
        return "C5–C6" if c.MW >= 70.0 else "C3–C4"
    return "Other"


def composition_figure(model, sol, sids, basis="mole"):
    """Stacked horizontal bars: grouped composition of selected streams (fixed group order and colours)."""
    fp = sol.fp
    colors = SERIES + [GREY]
    names = [model["streams"][s]["name"] for s in sids]
    fracs = []
    for s in sids:
        st_ = sol.streams[s]
        z = st_.z * (fp.MW if basis == "mass" else 1.0)
        z = z / z.sum()
        fracs.append(z)
    labels = [g for g, _ in COMP_GROUPS] + ["Other"]
    of = [_group_of(c) for c in fp.comps]
    fig = go.Figure()
    for g, label in enumerate(labels):
        vals = [100 * sum(float(z[i]) for i in range(fp.n) if of[i] == label) for z in fracs]
        if max(vals) <= 1e-9:
            continue
        fig.add_trace(go.Bar(y=names, x=vals, orientation="h", name=label,
                             marker=dict(color=(_alpha(INK, .55) if label == "Other" else colors[g % len(colors)]), line=dict(color="#FFFFFF", width=2)),
                             hovertemplate="%{y} · " + label + ": %{x:.2f} %<extra></extra>"))
    return _style(fig, "Stream compositions", f"Grouped, {basis} %", barmode="stack", bargap=0.35,
                  height=max(320, 110 + 42 * len(sids)),
                  xaxis=dict(_AXIS, title=f"{basis.capitalize()} %", range=[0, 100]),
                  yaxis=dict(_AXIS, autorange="reversed", title=""))


def case_figure(df, xcol, ycol):
    xs = [float(v) if v is not None else None for v in df[xcol].to_numpy().ravel()]
    ys = [float(v) if v is not None else None for v in df[ycol].to_numpy().ravel()]
    fig = go.Figure(go.Scatter(x=xs, y=ys, mode="lines+markers", name=ycol,
                               line=dict(color=TEAL, width=2.5), marker=dict(size=9),
                               hovertemplate="%{x:.4g} → %{y:.4g}<extra></extra>"))
    return _style(fig, ycol, f"vs {xcol}", height=360, xaxis=dict(_AXIS, title=xcol),
                  yaxis=dict(_AXIS, title=ycol.split(" · ")[-1]))


def economics_figure(rows, currency):
    """Annual energy + CO₂ cost and CO₂ emissions per energy stream, coloured by utility category."""
    cats = [("Power", TEAL), ("Heating", ORANGE), ("Cooling", BLUE), ("Power (credit)", GREEN)]
    rows = sorted(rows, key=lambda r: r["Energy cost [cur/y]"] + r["CO₂ cost [cur/y]"])
    ut = U.unit("t/y")
    fig = make_subplots(rows=1, cols=2, shared_yaxes=True, horizontal_spacing=0.04,
                        subplot_titles=(f"Energy + CO₂ cost [M{currency}/y]", f"CO₂ emissions [{ut}]"))
    for cat, color in cats:
        sel = [r for r in rows if r["Category"] == cat]
        if not sel:
            continue
        names = [r["Energy stream"] for r in sel]
        cost = [(r["Energy cost [cur/y]"] + r["CO₂ cost [cur/y]"]) / 1e6 for r in sel]
        co2 = [U.value("t/y", r["CO₂ [t/y]"]) for r in sel]
        fig.add_trace(go.Bar(y=names, x=cost, orientation="h", name=cat, legendgroup=cat,
                             marker=dict(color=color, cornerradius=4),
                             hovertemplate="%{y}: %{x:,.3f} M" + currency + "/y<extra>" + cat + "</extra>"),
                      row=1, col=1)
        fig.add_trace(go.Bar(y=names, x=co2, orientation="h", name=cat, legendgroup=cat, showlegend=False,
                             marker=dict(color=color, cornerradius=4),
                             hovertemplate="%{y}: %{x:,.0f} " + ut + "<extra>" + cat + "</extra>"),
                      row=1, col=2)
    return _style_subplots(fig, "Energy cost and CO₂ by energy stream", "Colour = utility category",
                           height=max(360, 90 + 30 * len(rows)))


# ---- field life ----------------------------------------------------------------------------------------

def fieldlife_figure(fl):
    """Production profile: primary and secondary rates (top), water cut or WGR (middle), reservoir and delivery
    pressure with the minimum (bottom); events as dotted verticals."""
    rows = [r for r in fl["annual"] if r["Year"] >= 1]
    gas = fl["kind"] == "Gas"
    yrs = [r["Year"] for r in rows]
    uG, uO, uP = U.unit("MSm³/d"), U.unit("Sm³/d", "Std liq"), U.uP()
    prim_lab = f"Gas [{uG}]" if gas else f"Oil [{uO}]"
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.08, row_heights=[0.42, 0.24, 0.34],
                        subplot_titles=(f"{'Gas' if gas else 'Oil'} rate, annual average [{uG if gas else uO}]",
                                        "Water-gas ratio [Sm³/MSm³]" if gas else "Water cut [%]",
                                        f"Pressure [{uP}]"))
    prim = [U.value("MSm³/d", r["Gas [MSm³/d]"]) if gas else U.value("Sm³/d", r["Oil/condensate [Sm³/d]"], "Std liq")
            for r in rows]
    fig.add_trace(go.Bar(x=yrs, y=prim, name=prim_lab, marker=dict(color=TEAL, cornerradius=3),
                         hovertemplate="Year %{x}: %{y:,.3g}<extra></extra>"), row=1, col=1)
    up = float(fl.get("uptime", 100.0)) / 100.0
    q_pl = (U.value("MSm³/d", fl["q_plat"]) if gas else U.value("Sm³/d", fl["q_plat"], "Std liq")) * up
    fig.add_trace(go.Scatter(x=[0.5, max(yrs) + 0.5], y=[q_pl, q_pl], mode="lines",
                             name=f"Plateau rate × {100 * up:.0f} % production efficiency",
                             line=dict(color=GREY, width=1.5, dash="dash"), hoverinfo="skip"), row=1, col=1)
    fig.add_trace(go.Scatter(x=yrs, y=[r["Water cut / WGR"] for r in rows], mode="lines+markers",
                             name="Water cut" if not gas else "WGR", line=dict(color=BLUE, width=2.5),
                             marker=dict(size=6), hovertemplate="Year %{x}: %{y:,.3g}<extra></extra>"), row=2, col=1)
    fig.add_trace(go.Scatter(x=yrs, y=[U.P(r["Reservoir P [bar(a)]"]) for r in rows], mode="lines+markers",
                             name="Reservoir pressure", line=dict(color=ORANGE, width=2.5), marker=dict(size=6),
                             hovertemplate="Year %{x}: %{y:,.1f}<extra>reservoir</extra>"), row=3, col=1)
    dp = [(r["Year"], r["Delivery P [bar(a)]"]) for r in rows if r.get("Delivery P [bar(a)]") is not None]
    if dp:
        fig.add_trace(go.Scatter(x=[a for a, _ in dp], y=[U.P(b) for _, b in dp], mode="lines+markers",
                                 name="Delivery pressure", line=dict(color=TEAL, width=2.5), marker=dict(size=6),
                                 hovertemplate="Year %{x}: %{y:,.1f}<extra>delivery</extra>"), row=3, col=1)
    fig.add_trace(go.Scatter(x=[0.5, max(yrs) + 0.5], y=[U.P(fl["P_min"])] * 2, mode="lines",
                             name="Minimum delivery pressure", line=dict(color=GREY, width=1.5, dash="dot"),
                             hoverinfo="skip"), row=3, col=1)
    for t, text in fl.get("events", []):
        fig.add_vline(x=t + 0.5, line=dict(color=INK2, width=1, dash="dot"), row="all", col=1)
    fig.update_xaxes(title="Production year", dtick=1 if len(yrs) <= 20 else 2, row=3, col=1)
    sub = (f"Plateau {fl['summary']['Plateau length [years]']:.1f} years · recovery factor "
           f"{fl['summary']['Recovery factor [%]']:.1f} % · dotted lines mark the events listed below")
    return _style_subplots(fig, "Production profile", sub, height=720)


def cashflow_figure(fl):
    """Annual cash flow (bars) and cumulative cash (line), MUSD."""
    rows = fl["annual"]
    yrs = [r["Year"] for r in rows]
    cf = [r["Cash flow [MUSD]"] for r in rows]
    fig = go.Figure()
    fig.add_trace(go.Bar(x=yrs, y=cf, name="Annual cash flow", marker=dict(color=[TEAL if v >= 0 else ORANGE for v in cf],
                                                                            cornerradius=3),
                         hovertemplate="Year %{x}: %{y:,.0f} MUSD<extra></extra>"))
    fig.add_trace(go.Scatter(x=yrs, y=[r["Cumulative cash [MUSD]"] for r in rows], mode="lines+markers",
                             name="Cumulative (undiscounted)", line=dict(color=INK, width=2), marker=dict(size=5),
                             hovertemplate="Year %{x}: %{y:,.0f} MUSD<extra>cumulative</extra>"))
    sm = fl["summary"]
    sub = f"NPV {sm['NPV [MUSD]']:,.0f} MUSD" + (f" · IRR {sm['IRR [%]']:.0f} %" if sm.get("IRR [%]") is not None else "") \
        + (f" · payback in year {sm['Payback year']}" if sm.get("Payback year") else "")
    return _style(fig, "Cash flow", sub, height=380, xaxis=dict(_AXIS, title="Year (0 = first production)", dtick=2),
                  yaxis=dict(_AXIS, title="MUSD"))


def wells_sweep_figure(rows, best=None):
    """NPV and recovery factor against the number of wells, one line per boosting option."""
    fig = make_subplots(rows=1, cols=2, horizontal_spacing=0.12,
                        subplot_titles=("NPV [MUSD]", "Recovery factor [%]"))
    modes = list(dict.fromkeys(r["Boosting"] for r in rows))
    for k, mode in enumerate(modes):
        rr = sorted([r for r in rows if r["Boosting"] == mode], key=lambda r: r["Wells"])
        col = SERIES[k % len(SERIES)]
        for c_, key in ((1, "NPV [MUSD]"), (2, "Recovery factor [%]")):
            fig.add_trace(go.Scatter(x=[r["Wells"] for r in rr], y=[r[key] for r in rr], mode="lines+markers",
                                     name=mode, legendgroup=mode, showlegend=c_ == 1, line=dict(color=col, width=2.5),
                                     marker=dict(size=8), hovertemplate="%{x} wells: %{y:,.1f}<extra>" + mode + "</extra>"),
                          row=1, col=c_)
    if best:
        b = next((r for r in rows if r["Wells"] == best[0] and r["Boosting"] == best[1]), None)
        if b:
            fig.add_trace(go.Scatter(x=[b["Wells"]], y=[b["NPV [MUSD]"]], mode="markers", name="Highest NPV",
                                     marker=dict(size=18, color="rgba(0,0,0,0)", line=dict(color=INK, width=2.5)),
                                     hoverinfo="skip"), row=1, col=1)
    fig.update_xaxes(title="Producing wells", dtick=1)
    return _style_subplots(fig, "Well count", "Each point is a full field-life run", height=420)


# ---- wax and sand ------------------------------------------------------------------------------------------

def wax_figure(rows, name, WAT):
    """Bulk and wall temperature along a line with the WAT (top) and the wax deposit growth (bottom)."""
    uT, uL = U.uT(), U.unit("m")
    L = _arr("m", [r["Distance [m]"] for r in rows])
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.12, row_heights=[0.6, 0.4],
                        subplot_titles=(f"Temperature [{uT}]", "Wax deposit growth [mm/y]"))
    fig.add_trace(go.Scatter(x=L, y=[U.T(r["Bulk T [°C]"]) for r in rows], mode="lines", name="Fluid (bulk)",
                             line=dict(color=TEAL, width=2.5)), row=1, col=1)
    fig.add_trace(go.Scatter(x=L, y=[U.T(r["Wall T [°C]"]) for r in rows], mode="lines", name="Pipe wall (inner)",
                             line=dict(color=BLUE, width=2, dash="dash")), row=1, col=1)
    if WAT is not None:
        fig.add_trace(go.Scatter(x=[L[0], L[-1]], y=[U.T(WAT)] * 2, mode="lines", name="Wax appearance T",
                                 line=dict(color=CRITICAL, width=1.5, dash="dot"), hoverinfo="skip"), row=1, col=1)
    fig.add_trace(go.Bar(x=L, y=[r["Deposit growth [mm/y]"] for r in rows], name="Deposit growth", showlegend=False,
                         marker=dict(color=OCHRE, cornerradius=3),
                         hovertemplate=f"%{{x:,.0f}} {uL}: %{{y:.2f}} mm/y<extra></extra>"), row=2, col=1)
    fig.update_xaxes(title=f"Distance [{uL}]", row=2, col=1)
    return _style_subplots(fig, f"{name} — wax deposition", "Deposit grows where the wall is colder than the WAT",
                           height=520)


def erosion_figure(rows, sand_kg_d):
    """Bend erosion rate per line at its highest mixture velocity."""
    names = [r["Line"] for r in rows]
    ys = [r["Erosion rate [mm/y]"] for r in rows]
    fig = go.Figure(go.Bar(x=names, y=ys, marker=dict(color=TEAL, cornerradius=4), showlegend=False,
                           text=[f"{v:.3g}" for v in ys], textposition="outside", cliponaxis=False,
                           hovertemplate="%{x}: %{y:.3g} mm/y<extra></extra>"))
    return _style(fig, "Sand erosion of bends", f"DNV-RP-O501 at {sand_kg_d:g} kg/d of sand, highest velocity per line",
                  xaxis=dict(_AXIS, title=""), yaxis=dict(_AXIS, title="Erosion rate [mm/y]"), height=380)


def emulsion_figure(row):
    """Apparent viscosity of the oil-water mixture against water cut, with the inversion and the operating point."""
    c = row["curve"]
    xs = [100.0 * w for w in c["wc"]]
    fig = go.Figure(go.Scatter(x=xs, y=c["mu"], mode="lines", name="Apparent viscosity", line=dict(color=TEAL, width=2.5),
                               hovertemplate="%{x:.0f} % water: %{y:.3g} cP<extra></extra>"))
    fig.add_vline(x=100.0 * c["inv"], line=dict(color=GREY, width=1.2, dash="dash"),
                  annotation_text="inversion", annotation_position="top")
    mu_now = row["Apparent viscosity [cP]"]
    fig.add_trace(go.Scatter(x=[100.0 * c["now"]], y=[mu_now], mode="markers", name="Operating point",
                             marker=dict(size=12, color=ORANGE), hovertemplate="%{x:.1f} %: %{y:.3g} cP<extra></extra>"))
    return _style(fig, f"{row['Line']} — oil-water viscosity", "Brinkman dispersion, continuous phase swaps at the inversion",
                  xaxis=dict(_AXIS, title="Water cut [vol %]"), yaxis=dict(_AXIS, title="Apparent viscosity [cP]"),
                  height=360)


def sizing_figure(rows, rec, P_min):
    """Pipe-size sweep: steel mass (bars) and arrival pressure (line) against the inside diameter."""
    ids = [r["ID [mm]"] for r in rows]
    fig = go.Figure()
    fig.add_trace(go.Bar(x=ids, y=[r["Steel [t/km]"] for r in rows], name="Steel [t/km]",
                         marker=dict(color=["#BFD9DB" if r["Feasible"] else "#E9C9BE" for r in rows], cornerradius=4),
                         hovertemplate="%{x:.0f} mm: %{y:.1f} t/km<extra></extra>"))
    arr = [(r["ID [mm]"], r["Arrival P [bar(a)]"]) for r in rows if r["Arrival P [bar(a)]"] is not None]
    if arr:
        fig.add_trace(go.Scatter(x=[a for a, _ in arr], y=[U.value("bar(a)", b, "Arrival P [bar(a)]") for _, b in arr],
                                 name="Arrival pressure", mode="lines+markers", yaxis="y2",
                                 line=dict(color=TEAL, width=2.5), hovertemplate="%{x:.0f} mm: %{y:.1f}<extra></extra>"))
        fig.add_hline(y=U.value("bar(a)", P_min, "Arrival P [bar(a)]"), line=dict(color=ORANGE, width=1.5, dash="dash"),
                      yref="y2", annotation_text="minimum arrival P", annotation_position="top left")
    if rec:
        fig.add_trace(go.Scatter(x=[rec["ID [mm]"]], y=[rec["Steel [t/km]"]], mode="markers", name="Recommended",
                                 marker=dict(size=15, color="rgba(0,0,0,0)", line=dict(color=ORANGE, width=3)),
                                 hoverinfo="skip"))
    return _style(fig, "Flowline inside diameter", "Pale bars meet every limit; the ring marks the smallest that does",
                  xaxis=dict(_AXIS, title="Inside diameter [mm]", type="category"),
                  yaxis=dict(_AXIS, title="Steel [t/km]"),
                  yaxis2=dict(overlaying="y", side="right", showgrid=False, title=f"Arrival pressure [{U.uP()}]"),
                  height=400)


def gaslift_figure(curves, alloc_rows):
    """Oil rate against lift-gas rate per well, with the present and optimal operating points."""
    fig = go.Figure()
    for i, c in enumerate(curves):
        col = SERIES[i % len(SERIES)]
        pts = sorted((p[0], p[1]) for p in c["points"])
        fig.add_trace(go.Scatter(x=[p[0] for p in pts], y=[p[1] for p in pts], mode="markers", name=c["Well"],
                                 marker=dict(size=7, color=col), legendgroup=c["Well"],
                                 hovertemplate=c["Well"] + ": %{x:.2f} MSm³/d → %{y:.0f} Sm³/d<extra></extra>"))
        fig.add_trace(go.Scatter(x=[p[0] for p in c["curve"]], y=[p[1] for p in c["curve"]], mode="lines",
                                 line=dict(color=col, width=2), showlegend=False, legendgroup=c["Well"], hoverinfo="skip"))
    if alloc_rows:
        fig.add_trace(go.Scatter(x=[r["Optimal lift gas [MSm³/d]"] for r in alloc_rows],
                                 y=[r["Oil at the optimum [Sm³/d]"] for r in alloc_rows], mode="markers", name="Optimal split",
                                 marker=dict(size=13, color="rgba(0,0,0,0)", line=dict(color=ORANGE, width=2.5)),
                                 hovertemplate="%{x:.2f} MSm³/d → %{y:.0f} Sm³/d<extra></extra>"))
    return _style(fig, "Gas-lift performance", "Oil rate at the present wellhead pressure; lines are the concave envelopes",
                  xaxis=dict(_AXIS, title="Lift gas [MSm³/d]"), yaxis=dict(_AXIS, title="Oil rate [Sm³/d]"), height=400)


def esp_figure(opts):
    """Pump options: efficiency against Q/Q_bep, one marker per series and frequency."""
    fig = go.Figure()
    for i, o in enumerate(opts):
        fig.add_trace(go.Bar(x=[f"{o['Series']} · {o['Frequency [Hz]']:g} Hz"], y=[o["Pump efficiency [%]"]],
                             marker=dict(color=SERIES[i % len(SERIES)], cornerradius=4), showlegend=False,
                             text=[f"{o['Stages']} st · {o['Motor [kW]']} kW"], textposition="outside", cliponaxis=False,
                             hovertemplate="%{x}: %{y:.1f} %<extra></extra>"))
    return _style(fig, "ESP options", "Pump efficiency at the intake flow; stages and motor size on the bars",
                  xaxis=dict(_AXIS, title=""), yaxis=dict(_AXIS, title="Pump efficiency [%]", rangemode="tozero"), height=360)


def network_figure(nodes, pipes):
    """Pressure at each node, ordered by pressure."""
    ns = sorted(nodes, key=lambda n: -n["Pressure [bar(a)]"])
    fig = go.Figure(go.Bar(x=[n["Node"] for n in ns], y=[U.value("bar(a)", n["Pressure [bar(a)]"], "Pressure [bar(a)]") for n in ns],
                           marker=dict(color=TEAL, cornerradius=4), showlegend=False,
                           hovertemplate="%{x}: %{y:.1f}<extra></extra>"))
    return _style(fig, "Node pressures", None, xaxis=dict(_AXIS, title=""),
                  yaxis=dict(_AXIS, title=f"Pressure [{U.uP()}]", rangemode="tozero"), height=340)


def route_figure(route, name):
    """Flowline route: water depth along the route (depth increasing downwards), low points marked."""
    pts = sorted((float(a), float(b)) for a, b in route)
    uL = U.unit("m")
    x = [a for a, _ in pts]
    y = [U.value("m", b) for _, b in pts]
    lows = [i for i in range(1, len(pts) - 1) if pts[i][1] > pts[i - 1][1] and pts[i][1] >= pts[i + 1][1]]
    fig = go.Figure(go.Scatter(x=x, y=y, mode="lines+markers", name="Seabed route", line=dict(color=TEAL, width=2.5),
                               marker=dict(size=7), hovertemplate=f"%{{x:.2f}} km: %{{y:,.0f}} {uL}<extra></extra>"))
    if lows:
        fig.add_trace(go.Scatter(x=[x[i] for i in lows], y=[y[i] for i in lows], mode="markers", name="Low point",
                                 marker=dict(size=14, color="rgba(0,0,0,0)", line=dict(color=ORANGE, width=2.5)),
                                 hoverinfo="skip"))
    return _style(fig, f"{name} — route", "Low points collect liquid (slugging, hydrates, corrosion)",
                  xaxis=dict(_AXIS, title="Distance along the route [km]"),
                  yaxis=dict(_AXIS, title=f"Water depth [{uL}]", autorange="reversed"), height=360)


# ---- prognosis (v7) -----------------------------------------------------------------------------------------

def strategy_figure(strat, kind):
    """Left: NPV and recovery factor of each drainage option at its best well count (bars). Right: the production
    profile of each option."""
    rows, prof = strat["rows"], strat.get("profiles") or {}
    gas = kind == "Gas"
    key = "Gas [MSm³/d]" if gas else "Oil/condensate [Sm³/d]"
    uR = U.unit("MSm³/d") if gas else U.unit("Sm³/d", "Std liq")
    fig = make_subplots(rows=1, cols=2, horizontal_spacing=0.12, column_widths=[0.45, 0.55],
                        subplot_titles=("NPV at the best well count [MUSD]", f"Production rate [{uR}]"))
    labels = [f"{r['Strategy']} ({r['Best wells']} wells)" for r in rows]
    best = strat.get("best") or [None, None]
    cols = [TEAL if r["Strategy"] == best[0] else GREY for r in rows]
    fig.add_trace(go.Bar(y=labels, x=[r["NPV [MUSD]"] for r in rows], orientation="h", name="NPV", showlegend=False,
                         marker=dict(color=cols, cornerradius=3),
                         customdata=[[r["Recovery factor [%]"]] for r in rows],
                         hovertemplate="%{y}<br>NPV %{x:,.0f} MUSD<br>RF %{customdata[0]:.1f} %<extra></extra>"),
                  row=1, col=1)
    for k, r in enumerate(rows):
        a = prof.get(r["Strategy"]) or []
        if not a:
            continue
        fig.add_trace(go.Scatter(x=[x["Year"] for x in a],
                                 y=[U.value("MSm³/d", x[key]) if gas else U.value("Sm³/d", x[key], "Std liq") for x in a],
                                 mode="lines", name=r["Strategy"], line=dict(color=SERIES[k % len(SERIES)], width=2.5),
                                 hovertemplate="Year %{x}: %{y:,.3g}<extra>" + r["Strategy"] + "</extra>"), row=1, col=2)
    fig.update_yaxes(autorange="reversed", row=1, col=1)
    fig.update_xaxes(title="Year", row=1, col=2)
    return _style_subplots(fig, "Drainage strategies", "Each option at its highest-NPV well count", height=420)


def uncertainty_figure(unc):
    """Left: NPV range per well count (P90-P10 whiskers, P50 and expected value). Right: P90 / P50 / P10 production."""
    rows = unc["rows"]
    gas = unc.get("kind") == "Gas"
    uR = U.unit("MSm³/d") if gas else U.unit("Sm³/d", "Std liq")
    fig = make_subplots(rows=1, cols=2, horizontal_spacing=0.12, column_widths=[0.5, 0.5],
                        subplot_titles=("NPV by well count: P90 - P50 - P10 [MUSD]",
                                        f"Production rate for {unc['best']} wells [{uR}]"))
    x = [r["Wells"] for r in rows]
    p50 = [r["NPV P50 [MUSD]"] for r in rows]
    fig.add_trace(go.Scatter(x=x, y=p50, mode="markers", name="P50",
                             error_y=dict(type="data", symmetric=False,
                                          array=[r["NPV P10 [MUSD]"] - r["NPV P50 [MUSD]"] for r in rows],
                                          arrayminus=[r["NPV P50 [MUSD]"] - r["NPV P90 [MUSD]"] for r in rows],
                                          color=TEAL, thickness=2.5, width=8),
                             marker=dict(size=9, color=TEAL),
                             hovertemplate="%{x} wells: P50 %{y:,.0f}<extra></extra>"), row=1, col=1)
    fig.add_trace(go.Scatter(x=x, y=[r["Expected NPV [MUSD]"] for r in rows], mode="lines+markers", name="Expected",
                             line=dict(color=ORANGE, width=2, dash="dash"), marker=dict(size=7, symbol="diamond"),
                             hovertemplate="%{x} wells: expected %{y:,.0f}<extra></extra>"), row=1, col=1)
    fig.add_hline(y=0, line=dict(color=GREY, width=1, dash="dot"), row=1, col=1)
    b = unc.get("bands") or {}
    if b:
        yrs = list(range(1, len(b["P50"]) + 1))
        cv = (lambda v: U.value("MSm³/d", v)) if gas else (lambda v: U.value("Sm³/d", v, "Std liq"))
        fig.add_trace(go.Scatter(x=yrs, y=[cv(v) for v in b["P10"]], mode="lines", line=dict(width=0), showlegend=False,
                                 hoverinfo="skip"), row=1, col=2)
        fig.add_trace(go.Scatter(x=yrs, y=[cv(v) for v in b["P90"]], mode="lines", line=dict(width=0),
                                 fill="tonexty", fillcolor="rgba(0,144,154,0.2)", name="P90 - P10", hoverinfo="skip"),
                      row=1, col=2)
        fig.add_trace(go.Scatter(x=yrs, y=[cv(v) for v in b["P50"]], mode="lines", name="P50",
                                 line=dict(color=TEAL, width=2.5),
                                 hovertemplate="Year %{x}: %{y:,.3g}<extra>P50</extra>"), row=1, col=2)
    fig.update_xaxes(title="Producing wells", dtick=1, row=1, col=1)
    fig.update_xaxes(title="Year", row=1, col=2)
    return _style_subplots(fig, "Uncertainty", f"{unc['n']} samples per well count", height=420)


# ---- debottlenecking (v7.1) ---------------------------------------------------------------------------------

def utilisation_figure(rows):
    """Horizontal bars of the capacity utilisation of every checked item, with the 100 % limit."""
    rr = sorted(rows, key=lambda r: r["Utilisation [%]"])
    lab = [f"{r['Unit']}: {r['Check']}" for r in rr]
    val = [r["Utilisation [%]"] for r in rr]
    col = [CRITICAL if v > 100 else (ORANGE if v >= 90 else TEAL) for v in val]
    fig = go.Figure(go.Bar(y=lab, x=val, orientation="h", marker=dict(color=col, cornerradius=3), showlegend=False,
                           hovertemplate="%{y}: %{x:.0f} %<extra></extra>"))
    fig.add_vline(x=100, line=dict(color=INK, width=1.5, dash="dash"))
    _style(fig, "Capacity utilisation", "100 % is the limit of each item", height=max(280, 34 * len(rr) + 130))
    fig.update_xaxes(title="Utilisation [% of the limit]", rangemode="tozero")
    return fig


def capacity_sweep_figure(sw):
    """Utilisation of each check against the throughput factor; the 100 % line is the limit."""
    fig = go.Figure()
    xs = sw["factors"]
    for k, r in enumerate(sw["series"]):
        ys = r["util"]
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines+markers", name=f"{r['Unit']}: {r['Check']}",
                                 line=dict(color=SERIES[k % len(SERIES)], width=2.2 if k < 3 else 1.4),
                                 marker=dict(size=6), connectgaps=False,
                                 hovertemplate="%{x:.2f} × : %{y:.0f} %<extra>" + r["Unit"] + "</extra>"))
    fig.add_hline(y=100, line=dict(color=INK, width=1.5, dash="dash"))
    fig.add_vline(x=1.0, line=dict(color=GREY, width=1, dash="dot"))
    _style(fig, "Throughput sweep", "The first line to cross 100 % is the bottleneck", height=440)
    fig.update_xaxes(title="Throughput [× present rate]")
    fig.update_yaxes(title="Utilisation [%]", rangemode="tozero")
    return fig


def profile_figure(times, series, time_unit, failed=()):
    """Stacked panels (one per quantity, own y-axis) against time for a profile run.  series: {label: [values]};
    failed: times of steps that did not solve (marked on the axis)."""
    names = list(series)[:6]
    n = max(1, len(names))
    fig = make_subplots(rows=n, cols=1, shared_xaxes=True, vertical_spacing=min(0.08, 0.5 / n),
                        subplot_titles=[s.split(" | ")[-1] if s.startswith("Overall") else s for s in names])
    for i, lab in enumerate(names, start=1):
        ys = [None if v is None else float(v) for v in series[lab]]
        fig.add_trace(go.Scatter(x=list(times), y=ys, mode="lines+markers", name=lab, showlegend=False,
                                 line=dict(color=SERIES[(i - 1) % len(SERIES)], width=2.5, shape="hv"),
                                 marker=dict(size=7),
                                 hovertemplate="t = %{x:.4g} " + time_unit + " → %{y:.5g}<extra></extra>"), row=i, col=1)
    if failed:
        fig.add_trace(go.Scatter(x=list(failed), y=[0] * len(failed), mode="markers", name="step not solved",
                                 marker=dict(symbol="x", size=11, color=CRITICAL), yaxis="y",
                                 hovertemplate="step at %{x:.4g} did not solve<extra></extra>"), row=1, col=1)
    _style_subplots(fig, "Profile results", f"one steady-state solve per time step; time in {time_unit}",
                    height=max(360, 190 * n + 90))
    fig.update_xaxes(title_text=f"Time [{time_unit}]", row=n, col=1)
    return fig


def dynamic_figure(times, series, time_unit, events=()):
    """Stacked panels (one per quantity, own y-axis) against time for a dynamic run; events are marked as dotted
    vertical lines.  series: {label: [values]}; times already in ``time_unit``; events: [(time in the same unit, text)]."""
    names = list(series)[:6]
    n = max(1, len(names))
    fig = make_subplots(rows=n, cols=1, shared_xaxes=True, vertical_spacing=min(0.07, 0.5 / n), subplot_titles=names)
    for i, lab in enumerate(names, start=1):
        ys = [None if v is None else float(v) for v in series[lab]]
        fig.add_trace(go.Scatter(x=list(times), y=ys, mode="lines", name=lab, showlegend=False,
                                 line=dict(color=SERIES[(i - 1) % len(SERIES)], width=2.4),
                                 hovertemplate="t = %{x:.5g} " + time_unit + " → %{y:.5g}<extra></extra>"), row=i, col=1)
    for t, txt in list(events)[:12]:
        fig.add_vline(x=t, line=dict(color=GREY, width=1, dash="dot"))
    _style_subplots(fig, "Dynamic results", f"time in {time_unit}" + (" - dotted lines mark the events" if events else ""),
                    height=max(360, 190 * n + 90))
    fig.update_xaxes(title_text=f"Time [{time_unit}]", row=n, col=1)
    return fig
