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


def _envelope_traces(fig, T_C, P, grid, quality=True):
    T_C, P = _arr("°C", T_C), _arr("bar(a)", P)
    g = np.nan_to_num(grid, nan=1.0)
    two = np.where((g > 1e-6) & (g < 1 - 1e-6), 1.0, np.nan)
    fig.add_trace(go.Heatmap(x=T_C, y=P, z=two, colorscale=[[0, _alpha(TEAL, .10)], [1, _alpha(TEAL, .10)]],
                             showscale=False, hoverinfo="skip", name="Two-phase region"))
    if quality:
        fig.add_trace(go.Contour(x=T_C, y=P, z=g, contours=dict(start=0.25, end=0.75, size=0.25, coloring="none",
                                                                showlabels=True, labelfont=dict(size=10, color=GREY)),
                                 line=dict(color=GREY, width=1, dash="dot"), showscale=False,
                                 name="Vapour fraction 0.25 / 0.5 / 0.75", hoverinfo="skip", showlegend=True))
    fig.add_trace(go.Contour(x=T_C, y=P, z=g, contours=dict(start=1e-4, end=1e-4, size=1, coloring="none"),
                             line=dict(color=BLUE, width=2.5), showscale=False, name="Bubble-point line",
                             hoverinfo="skip", showlegend=True))
    fig.add_trace(go.Contour(x=T_C, y=P, z=g, contours=dict(start=0.9999, end=0.9999, size=1, coloring="none"),
                             line=dict(color=ORANGE, width=2.5), showscale=False, name="Dew-point line",
                             hoverinfo="skip", showlegend=True))


def envelope_figure(T_C, P, grid, points=(), title="Phase envelope"):
    fig = go.Figure()
    _envelope_traces(fig, T_C, P, grid)
    if points:
        fig.add_trace(go.Scatter(x=[U.T(p[1]) for p in points], y=[U.P(p[2]) for p in points], mode="markers+text",
                                 text=[p[0] for p in points], textposition="top center",
                                 marker=dict(size=10, color=INK, line=dict(color="#FFFFFF", width=2)),
                                 name="Stream", hovertemplate=f"%{{text}}<br>%{{x:.1f}} {U.uT()} · %{{y:.1f}} {U.uP()}"
                                                              "<extra></extra>"))
    return _style(fig, title, "Peng-Robinson; shaded = two-phase region", xaxis=dict(_AXIS, title=f"Temperature [{U.uT()}]"),
                  yaxis=dict(_AXIS, title=f"Pressure [{U.uP()}]"))


def flow_assurance_figure(env, trajectory, hydrate_curves, title="Flow assurance: P–T trajectory"):
    """Envelope (optional) + hydrate curves + the P-T path of a sequence of streams.

    env: (T_C, P, grid) or None; trajectory: list of (name, T_C, P, at_risk);
    hydrate_curves: list of (label, P array, T array, dashed)."""
    fig = go.Figure()
    if env is not None:
        _envelope_traces(fig, *env, quality=False)
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
    fig = go.Figure()
    for g, (label, members) in enumerate(COMP_GROUPS):
        vals = []
        for z in fracs:
            v = sum(z[fp.index(k)] for k in members if k in fp.keys)
            # hypothetical cuts join C7+
            if label == "C7+":
                v += sum(z[i] for i, c in enumerate(fp.comps) if c.family == "hypo")
            vals.append(100 * v)
        if max(vals) <= 1e-9:
            continue
        fig.add_trace(go.Bar(y=names, x=vals, orientation="h", name=label,
                             marker=dict(color=colors[g], line=dict(color="#FFFFFF", width=2)),
                             hovertemplate="%{y} · " + label + ": %{x:.2f} %<extra></extra>"))
    return _style(fig, "Stream compositions", f"Grouped, {basis} %", barmode="stack", bargap=0.35,
                  height=max(320, 110 + 42 * len(sids)),
                  xaxis=dict(_AXIS, title=f"{basis.capitalize()} %", range=[0, 100]),
                  yaxis=dict(_AXIS, autorange="reversed", title=""))


def case_figure(df, xcol, ycol):
    fig = go.Figure(go.Scatter(x=df[xcol], y=df[ycol], mode="lines+markers", name=ycol,
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
