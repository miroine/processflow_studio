"""Subsea operability screening on a solved flowsheet (SURF Phase 3).

* ``slug_assessment``    - hydrodynamic slugging in each flowline (Gregory & Scott frequency, Norris slug length
                            with a Brill-type log-normal 1-in-1000 maximum, Gregory slug-body holdup) and the
                            severe (riser-base) slug that would form if the riser blocks: riser volume + flowline
                            penetration and its cycle period; the arrival surge volume to design for.
* ``turndown_envelope``  - flowline + riser over a range of rates: arrival P/T, hydrate margin, erosional ratio,
                            liquid inventory, riser-base slugging and hydrodynamic slugs; the operating window and
                            the liquid swept out on ramp-up.
* ``field_layout``       - plan-view coordinates of host, risers, flowlines, templates and wells from the flowsheet
                            topology and line lengths (bearings editable, well slots not to scale).

"Transient-lite" means steady-state correlations and volume balances, not a transient multiphase simulation:
screening numbers to size slug catchers and bound the operating envelope, to be confirmed with a dynamic model.
"""
from __future__ import annotations

import math

from . import surf
from .flowsheet import port_edges
from .streams import make_stream
from .thermo import V_STD_GAS, FlashError
from .unitops import UnitError, K0, G, pipe_gradient, _phase_split

LOGN_SIGMA = 0.5                    # log-normal spread of slug length (Brill et al. 1981)
Z_1000 = 3.09                       # 1-in-1000 slug (one-sided normal quantile)


# ---------------------------------------------------------------------------------------- slug correlations

def slug_body_holdup(vm):
    """Liquid holdup in the slug body, Gregory, Nicholson & Aziz (1978): 1 / (1 + (vm/8.66)^1.39)."""
    return 1.0 / (1.0 + (max(vm, 0.0) / 8.66) ** 1.39)


def slug_frequency(vsl, vm, D):
    """Slug frequency [1/s], Gregory & Scott (1969): 0.0226 [(vsl / gD)(19.75/vm + vm)]^1.2 (SI units)."""
    if vm <= 0 or vsl <= 0:
        return 0.0
    return 0.0226 * ((vsl / (G * D)) * (19.75 / vm + vm)) ** 1.2


def mean_slug_length(D):
    """Mean slug length [m], Norris (1982, Prudhoe Bay): ln Ls[ft] = −26.8 + 28.5 (ln D[in])^0.1, at least 12 D."""
    d_in = D / 0.0254
    if d_in <= 1.0:
        return 12.0 * D
    Ls = math.exp(-26.8 + 28.5 * math.log(d_in) ** 0.1) * 0.3048
    return max(Ls, 12.0 * D)


def hydrodynamic_slugs(fp, st, D, eps, theta):
    """Hydrodynamic slug statistics for a line state (meaningful in intermittent flow; the Beggs & Brill
    transition band is treated as slugging too, conservatively)."""
    _, d = pipe_gradient(fp, st, D, eps, theta, "Beggs & Brill", 0.02)
    A = math.pi * D * D / 4.0
    Ls = mean_slug_length(D)
    Lmax = Ls * math.exp(Z_1000 * LOGN_SIGMA)
    Hs = slug_body_holdup(d["vm"])
    f = slug_frequency(d["vsl"], d["vm"], D)
    return {"Flow regime": d["regime"], "Slugging": d["regime"] in ("Intermittent", "Transition"),
            "vsl [m/s]": d["vsl"], "vsg [m/s]": d["vsg"], "Mixture velocity [m/s]": d["vm"],
            "Slug frequency [1/h]": f * 3600.0, "Mean slug length [m]": Ls, "1-in-1000 slug length [m]": Lmax,
            "Slug body holdup [-]": Hs, "Mean slug volume [m³]": Ls * A * Hs, "1-in-1000 slug volume [m³]": Lmax * A * Hs}


def severe_slug(fp, base, top, D_riser, riser_len, depth, D_fl, L_fl, HL_fl):
    """Severe-slug volume and cycle if the riser base blocks (volume balance, Schmidt/Taitel-type build-up).

    During build-up the riser fills with liquid (volume V_r = A_r·L_r) while the head ΔP = ρ_l g H must be
    matched by the flowline gas pressure.  Gas keeps flowing in (Q_g0 at P0) and liquid keeps arriving (Q_l),
    so the liquid pushed back into the flowline, x, follows from the gas balance
        (P0 + ΔP)(V_g − x) = P0 (V_g + Q_g0 (V_r + x)/Q_l),   V_g = α A_f L
    which is linear in x; x ≤ 0 means the gas inflow keeps up and the slug cannot grow beyond the riser."""
    A_r = math.pi * D_riser ** 2 / 4.0
    A_f = math.pi * D_fl ** 2 / 4.0
    _, _, _, rho_l, _, _ = _phase_split(fp, base)
    alpha = max(1.0 - HL_fl, 1e-6)
    P0 = top.P * 1e5
    dP = rho_l * G * depth
    V_r = A_r * riser_len
    V_g = alpha * A_f * L_fl
    _, d = pipe_gradient(fp, base, D_fl, 4.5e-5, 0.0, "Beggs & Brill", 0.02)
    _, dt = pipe_gradient(fp, top, D_fl, 4.5e-5, 0.0, "Beggs & Brill", 0.02)
    q_l = d["vsl"] * A_f                                     # m3/s liquid arriving at the riser base
    q_g0 = dt["vsg"] * A_f                                   # m3/s gas at the riser-top pressure
    x = 0.0
    if q_l > 0:
        x = (V_g - P0 * (V_g + q_g0 * V_r / q_l) / (P0 + dP)) / (1.0 + P0 * q_g0 / (q_l * (P0 + dP)))
        x = min(max(x, 0.0), V_g)
    V = V_r + x
    return {"Riser fill volume [m³]": V_r, "Flowline penetration volume [m³]": x, "Severe slug volume [m³]": V,
            "Liquid inflow [m³/h]": q_l * 3600.0, "Build-up time [min]": V / q_l / 60.0 if q_l > 0 else None,
            "Hydrostatic head of a full riser [bar]": dP / 1e5}


# ------------------------------------------------------------------------------------- flowsheet topology

def _src(model, sid):
    return model["streams"][sid]["src"][0]


def _single_in(model, uid):
    ins = [s for lst in port_edges(model, uid, "in").values() for s in lst]
    return ins[0] if len(ins) == 1 else None


def upstream_flowline(model, riser_uid, max_units=12):
    """The first flowline upstream of a riser along single-inlet units (or None)."""
    uid = riser_uid
    for _ in range(max_units):
        sid = _single_in(model, uid)
        if sid is None:
            return None
        uid = _src(model, sid)
        if model["units"][uid]["type"] == "flowline":
            return uid
    return None


def _theta(u):
    L = surf.flowline_length(u["params"]) or 1.0
    secs = surf.route_sections(u["params"])
    dz = sum(d for _, d in secs) if secs else float(u["params"].get("dz", 0.0))
    return math.asin(max(-1.0, min(1.0, dz / L))) if L > 0 else 0.0


def _rough_m(u):
    try:
        row = surf.item("flowline", u["params"].get("design"))
    except UnitError:
        row = {}
    r = float(u["params"].get("rough", 0.0) or 0.0) or (row.get("roughness_mm") or 0.045)
    return r / 1000.0


def slug_assessment(model, sol, margin=1.2):
    """Per flowline (+ its riser): hydrodynamic slugs at the riser base, the potential severe slug, and the
    arrival surge volume to design for (margin × the governing slug volume)."""
    out = []
    for fl_uid, u in model["units"].items():
        if u["type"] != "flowline" or fl_uid not in sol.results:
            continue
        sid_out = [s for lst in port_edges(model, fl_uid, "out").values() for s in lst]
        if not sid_out or sid_out[0] not in sol.streams or sol.streams[sid_out[0]].empty:
            continue
        base = sol.streams[sid_out[0]]
        D = float(u["params"]["ID"]) / 1000.0
        hyd = hydrodynamic_slugs(sol.fp, base, D, _rough_m(u), _theta(u))
        rs = next((r for r, x in model["units"].items() if x["type"] == "riser" and upstream_flowline(model, r) == fl_uid
                   and r in sol.results), None)
        row = {"Flowline": u["name"], "Riser": model["units"][rs]["name"] if rs else "—", "hydro": hyd}
        governing, why = (hyd["1-in-1000 slug volume [m³]"], "1-in-1000 hydrodynamic slug") if hyd["Slugging"] else (0.0, "")
        if rs:
            rr = sol.results[rs]
            top_sid = [s for lst in port_edges(model, rs, "out").values() for s in lst]
            top = sol.streams[top_sid[0]]
            sev = severe_slug(sol.fp, base, top, float(model["units"][rs]["params"]["ID"]) / 1000.0,
                              float(rr["Riser length [m]"]), float(rr["Water depth [m]"]), D,
                              surf.flowline_length(u["params"]), float(sol.results[fl_uid]["Average liquid holdup [-]"]))
            risk = str(rr.get("Riser-base slugging risk", "Low"))
            sev["Risk"] = risk
            sev["Bøe number [-]"] = rr.get("Bøe number (< 1: severe slugging possible) [-]")
            row["severe"] = sev
            if risk.startswith("High") and sev["Severe slug volume [m³]"] > governing:
                governing, why = sev["Severe slug volume [m³]"], "severe riser-base slug"
        row["Governing slug"] = why or "none (no slugging predicted)"
        row["Governing volume [m³]"] = governing
        row["Design surge volume [m³]"] = governing * margin
        out.append(row)
    return out


# ------------------------------------------------------------------------------------------- turndown

def _scaled(st, fp, factor):
    return make_stream("", fp, st.F * factor, st.z, st.flash)


def turndown_envelope(fp, inlet, fl_params, riser_params=None,
                      rate_factors=(0.1, 0.2, 0.35, 0.5, 0.7, 0.85, 1.0, 1.2, 1.4),
                      P_min=40.0, progress=None):
    """Flowline (+ riser) performance over rate; returns (rows, window)."""
    rows = []
    D = float(fl_params["ID"]) / 1000.0
    theta = _theta({"params": fl_params})
    eps = _rough_m({"params": fl_params})
    for k, rf in enumerate(sorted(rate_factors), start=1):
        st0 = _scaled(inlet, fp, rf)
        row = {"Rate factor": rf, "Gas rate [MSm³/d]": st0.F * V_STD_GAS * 24.0 / 1e6, "Feasible": False,
               "Limits": ""}
        try:
            fl = {"name": "Flowline", "type": "flowline", "params": dict(fl_params)}
            outs, r1, _ = surf.calc_flowline(fl, {"in": [st0]}, fp)
            base = st = outs["out"][0]
            inv = float(r1["Liquid inventory [m³]"])
            evr = float(r1["Erosional velocity ratio (API RP 14E, C=100)"])
            margins = [r1.get("Min. hydrate margin along line [°C]")]
            risk, boe = "Low", None
            if riser_params:
                rs = {"name": "Riser", "type": "riser", "params": dict(riser_params)}
                outs, r2, _ = surf.calc_riser(rs, {"in": [st]}, fp)
                st = outs["out"][0]
                prof = rs["_profile"]
                Dr = float(riser_params["ID"]) / 1000.0
                inv += sum(prof["HL"]) / len(prof["HL"]) * math.pi * Dr * Dr / 4.0 * float(r2["Riser length [m]"])
                evr = max(evr, float(r2["Erosional velocity ratio (API RP 14E, C=100)"]))
                margins.append(r2.get("Min. hydrate margin along line [°C]"))
                risk = str(r2.get("Riser-base slugging risk", "Low"))
                boe = r2.get("Bøe number (< 1: severe slugging possible) [-]")
            hyd = hydrodynamic_slugs(fp, base, D, eps, theta)
            margins = [m for m in margins if m is not None]
            hm = min(margins) if margins else None
            fails = []
            if st.P < P_min:
                fails.append("arrival pressure")
            if hm is not None and hm < 0:
                fails.append("hydrate margin")
            if evr > 1.0:
                fails.append("erosional velocity")
            if risk.startswith("High"):
                fails.append("severe slugging")
            row.update({"Arrival P [bar(a)]": st.P, "Arrival T [°C]": st.T - K0, "Min. hydrate margin [°C]": hm,
                        "Erosional ratio [-]": evr, "Liquid inventory [m³]": inv, "Bøe number [-]": boe,
                        "Riser-base slugging": "High" if risk.startswith("High") else "Low",
                        "Flowline regime": hyd["Flow regime"],
                        "1-in-1000 slug volume [m³]": hyd["1-in-1000 slug volume [m³]"] if hyd["Slugging"] else 0.0,
                        "Feasible": not fails, "Limits": ", ".join(fails)})
        except (UnitError, FlashError, ValueError, ZeroDivisionError) as e:
            row["Limits"] = "cannot deliver the rate: " + str(e)[:100]
        rows.append(row)
        if progress:
            progress(k / len(rate_factors))
    return rows, operating_window(rows)


def operating_window(rows):
    """Contiguous feasible rates around the current rate (factor 1, or the feasible point nearest to it)."""
    rows = sorted(rows, key=lambda r: r["Rate factor"])
    ok = [r["Feasible"] for r in rows]
    if not any(ok):
        return {"feasible": False, "min": None, "max": None, "low limit": "no feasible rate",
                "high limit": "no feasible rate", "ramp-up surge [m³]": None}
    i0 = min((i for i, f in enumerate(ok) if f), key=lambda i: abs(rows[i]["Rate factor"] - 1.0))
    lo = hi = i0
    while lo > 0 and ok[lo - 1]:
        lo -= 1
    while hi < len(rows) - 1 and ok[hi + 1]:
        hi += 1
    low_lim = rows[lo - 1]["Limits"] if lo > 0 else "below the swept range"
    high_lim = rows[hi + 1]["Limits"] if hi < len(rows) - 1 else "above the swept range"
    inv_lo, inv_hi = rows[lo].get("Liquid inventory [m³]"), rows[hi].get("Liquid inventory [m³]")
    return {"feasible": True, "min": rows[lo]["Rate factor"], "max": rows[hi]["Rate factor"],
            "min gas [MSm³/d]": rows[lo]["Gas rate [MSm³/d]"], "max gas [MSm³/d]": rows[hi]["Gas rate [MSm³/d]"],
            "low limit": low_lim, "high limit": high_lim,
            "ramp-up surge [m³]": max(inv_lo - inv_hi, 0.0) if (inv_lo is not None and inv_hi is not None) else None}


# ------------------------------------------------------------------------------------------ field layout

LINE_LEN = {"flowline": lambda u: surf.flowline_length(u["params"]) / 1000.0}


def _jumper_km(u):
    try:
        row = surf.item("jumper", u["params"].get("kind"))
    except UnitError:
        row = {}
    L = float(u["params"].get("length", 0.0) or 0.0) or (row.get("length_m") or 10.0)
    return L / 1000.0


def _len_km(u):
    if u["type"] == "flowline":
        return surf.flowline_length(u["params"]) / 1000.0
    if u["type"] == "jumper":
        return _jumper_km(u)
    return 0.0


def default_bearing(k):
    return (240.0 + 55.0 * k) % 360.0


def field_layout(model, sol=None):
    """Plan-view layout. Returns {"nodes": [...], "edges": [...], "extent_km": float}.

    Node: name, type, x, y [km east/north of the host], label. Edge: x0, y0, x1, y1, kind (flowline, jumper,
    umbilical, riser), label. The host sits at the origin; each riser's line runs out along its bearing
    (``model["layout"]["bearing"][riser uid]`` in degrees from north, editable); units along the line are placed
    at their cumulative distance; wells on a template are drawn on a ring around it (not to scale); satellite
    wells more than 0.5 km away run out along their own bearing."""
    lay = model.get("layout") or {}
    bearings = lay.get("bearing") or {}
    units = model["units"]
    risers = sorted([u for u, x in units.items() if x["type"] == "riser"], key=lambda u: units[u]["name"])
    nodes, edges, placed = [], [], set()
    host = None
    for r in risers:
        outs = [s for lst in port_edges(model, r, "out").values() for s in lst]
        if outs:
            host = units[model["streams"][outs[0]]["dst"][0]]["name"]
            break
    nodes.append({"name": host or "Host facility", "type": "host", "x": 0.0, "y": 0.0, "label": host or "Host facility"})

    def pos(dist, b):
        return dist * math.sin(math.radians(b)), dist * math.cos(math.radians(b))

    def walk(uid, dist, b, prev_xy, depth=0):
        """Place uid and everything upstream of it; dist = distance of uid's outlet from the host."""
        if uid in placed or depth > 40:
            return
        placed.add(uid)
        u = units[uid]
        t = u["type"]
        L = _len_km(u)
        x0, y0 = prev_xy                                   # outlet end
        x1, y1 = pos(dist + L, b)                          # inlet end
        if t in ("flowline", "jumper"):
            edges.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1, "kind": t,
                          "label": f"{u['name']}: {L:.1f} km" + (f", {u['params'].get('design', '')}" if t == "flowline" else "")})
        elif t not in ("feed",):
            nodes.append({"name": u["name"], "type": t, "x": x1, "y": y1, "label": u["name"]})
        ins = [s for lst in port_edges(model, uid, "in").values() for s in lst]
        if t == "feed" or not ins:
            return
        if len(ins) == 1:
            walk(_src(model, ins[0]), dist + L, b, (x1, y1), depth + 1)
            return
        if t != "template":
            # a processing station (mixer after a separator, a chemical injection point, ...): its branches are
            # co-located equipment, not well slots - walk each one at the same spot along the line
            for sid in ins:
                walk(_src(model, sid), dist + L, b, (x1, y1), depth + 1)
            return
        # manifold / template: spread the inlet branches
        branch = []
        for sid in ins:
            s0 = _src(model, sid)
            chain, cur = 0.0, s0
            for _ in range(20):                            # length of the branch up to its well
                chain += _len_km(units[cur])
                nxt = [s for lst in port_edges(model, cur, "in").values() for s in lst]
                if len(nxt) != 1 or units[cur]["type"] in ("well", "feed"):
                    break
                cur = _src(model, nxt[0])
            branch.append((s0, chain))
        ring = max(0.04 * max(dist + L, 1.0), 0.15)
        n = len(branch)
        for k, (s0, chain) in enumerate(branch):
            if chain > 0.5:                                # satellite: its own bearing away from the host line
                bb = b + (k - (n - 1) / 2.0) * 35.0
                walk(s0, dist + L, bb, (x1, y1), depth + 1)
            else:                                          # well slots: ring around the template (not to scale)
                ang = 2 * math.pi * k / max(n, 1)
                cx, cy = x1 + ring * math.cos(ang), y1 + ring * math.sin(ang)
                _place_slot(s0, cx, cy, (x1, y1))

    def _place_slot(uid, cx, cy, hub):
        names = []
        cur = uid
        for _ in range(20):
            if cur in placed:
                break
            placed.add(cur)
            if units[cur]["type"] != "feed":
                names.append(units[cur]["name"])
            nxt = [s for lst in port_edges(model, cur, "in").values() for s in lst]
            if len(nxt) != 1 or units[cur]["type"] == "feed":
                break
            cur = _src(model, nxt[0])
        edges.append({"x0": hub[0], "y0": hub[1], "x1": cx, "y1": cy, "kind": "jumper", "label": ""})
        nodes.append({"name": " / ".join(reversed(names)), "type": "well", "x": cx, "y": cy,
                      "label": " / ".join(reversed(names))})

    for k, r in enumerate(risers):
        b = float(bearings.get(r, default_bearing(k)))
        u = units[r]
        res = (sol.results.get(r) if sol else None) or {}
        Lr = float(res.get("Riser length [m]") or 0.0)
        depth = float(u["params"].get("depth", 0.0))
        d0 = math.sqrt(max(Lr * Lr - depth * depth, 0.0)) / 1000.0      # horizontal footprint (touch-down)
        tx0, ty0 = pos(d0, b)
        nodes.append({"name": u["name"], "type": "riser", "x": tx0, "y": ty0, "label": f"{u['name']} (touch-down)"})
        placed.add(r)
        edges.append({"x0": 0.0, "y0": 0.0, "x1": tx0, "y1": ty0, "kind": "riser", "label": u["name"]})
        sid = _single_in(model, r)
        if sid:
            walk(_src(model, sid), d0, b, (tx0, ty0))
        fl_len = sum(_len_km(units[x]) for x in placed if units[x]["type"] in ("flowline", "jumper"))
        tx, ty = pos(max(fl_len, 0.1), b)
        off = 0.015 * max(fl_len, 1.0)
        ox, oy = off * math.cos(math.radians(b)), -off * math.sin(math.radians(b))
        edges.append({"x0": ox, "y0": oy, "x1": tx + ox, "y1": ty + oy, "kind": "umbilical",
                      "label": f"Umbilical from {host or 'host'}"})
    ext = max([abs(n_["x"]) for n_ in nodes] + [abs(n_["y"]) for n_ in nodes] + [1.0])
    # units sitting at (almost) the same spot along a line (e.g. a booster next to its template, an SSIV at the
    # touch-down) are drawn slightly apart so their markers and labels stay readable
    # templates stay put (their well ring is centred on them); other units move off them
    seen = [(n_["x"], n_["y"]) for n_ in nodes if n_["type"] == "template"]
    for n_ in nodes:
        if n_["type"] in ("well", "host", "template"):
            continue
        k = sum(1 for (x, y) in seen if abs(x - n_["x"]) < 0.01 * ext and abs(y - n_["y"]) < 0.01 * ext)
        seen.append((n_["x"], n_["y"]))
        if k:
            n_["y"] -= 0.045 * ext * k
            n_["label"] += " (offset for clarity)"
    return {"nodes": nodes, "edges": edges, "extent_km": ext}


# --------------------------------------------------------------------------------------- cool-down / no-touch

STEEL_RHO_CP = 7850.0 * 480.0                  # J/m3K
INSUL_MM = {"Bare carbon steel": 0.0, "Wet insulation (multilayer PP)": 60.0, "Pipe-in-pipe": 40.0,
            "Flexible pipe (insulated)": 30.0, "Towed bundle": 50.0, "Trenched and buried": 0.0,
            "Electrically heated (DEH)": 60.0}
U_FLOWING, U_NATURAL = "Flowing U (conservative)", "Natural convection after shut-in"
U_MODES = (U_FLOWING, U_NATURAL)
COOL_DEFAULTS = {"wall_mm": 20.0, "insul_mm": -1.0, "insul_MJ_m3K": 1.7, "insul_frac": 0.5, "shutin": "Settle-out",
                 "U_mode": U_FLOWING}


def cool_params(model):
    p = dict(COOL_DEFAULTS)
    p.update({k: v for k, v in (model.get("cooldown") or {}).items() if k in COOL_DEFAULTS})
    return p


def _vol_heat_capacity(fp, st, D, theta):
    """In-situ volumetric heat capacity of the line contents [J/m3K] from the Beggs & Brill holdup."""
    _, d = pipe_gradient(fp, st, D, 4.5e-5, theta, "Beggs & Brill", 0.02)
    HL = d["HL"]
    cg = cl = 0.0
    vg = vl = 0.0
    for ph in st.flash.phases:
        c = ph.rho * ph.Cp / ph.MW * 1000.0                 # J/m3K of the phase
        v = ph.beta * ph.Vs
        if ph.kind == "V":
            cg, vg = cg + c * v, vg + v
        else:
            cl, vl = cl + c * v, vl + v
    cg = cg / vg if vg > 0 else 0.0
    cl = cl / vl if vl > 0 else 0.0
    if vg <= 0:
        HL = 1.0
    if vl <= 0:
        HL = 0.0
    return HL * cl + (1 - HL) * cg


def _hydrate_T(fp, st, P):
    """Inhibited hydrate temperature [°C] of a stream's gas at pressure P (None when not applicable)."""
    from .transport import hydrate_T, hydrate_depression
    if st.empty or fp.iw < 0 or st.z[fp.iw] <= 1e-9:
        return None
    v = st.flash.phase("V")
    x = v.x if v is not None else st.z
    t = hydrate_T(fp, x, P)
    if t is None:
        return None
    aq = st.flash.phase("W")
    return t - hydrate_depression(fp, aq.x if aq is not None else st.z)


def cooldown_line(fp, name, prof, st_in, st_out, D, U, T_amb, basis, insul_mm):
    """Cool-down of one line after shut-in. Every profile point cools exponentially to ambient,
    T(t) = T_amb + (T0 − T_amb) exp(−t/τ), τ = C'/(U π D), with C' the heat capacity per metre of the contents,
    the steel wall and part of the insulation. No-touch time = time until T reaches the hydrate temperature at
    the shut-in pressure (settle-out = the line's mean operating pressure, or the local operating pressure)."""
    A = math.pi * D * D / 4.0
    theta = 0.0
    c_in = _vol_heat_capacity(fp, st_in, D, theta)
    c_out = _vol_heat_capacity(fp, st_out, D, theta)
    wall = basis["wall_mm"] / 1000.0
    C_wall = STEEL_RHO_CP * math.pi * (D + wall) * wall
    ins = insul_mm / 1000.0
    Do = D + 2 * wall
    C_ins = basis["insul_frac"] * basis["insul_MJ_m3K"] * 1e6 * math.pi * (Do + ins) * ins
    P_settle = sum(prof["P"]) / len(prof["P"])
    pts = []
    n = len(prof["L"])
    for k in range(n):
        f = k / max(n - 1, 1)
        C = A * (c_in * (1 - f) + c_out * f) + C_wall + C_ins          # J/(m K)
        tau = C / (U * math.pi * D) if U > 0 else math.inf               # s
        T0 = prof["T"][k]
        P = P_settle if basis["shutin"] == "Settle-out" else prof["P"][k]
        Th = _hydrate_T(fp, st_in, P)
        if Th is None or Th <= T_amb:
            t_nt = math.inf
        elif T0 <= Th:
            t_nt = 0.0
        else:
            t_nt = tau * math.log((T0 - T_amb) / (Th - T_amb))
        pts.append({"Distance [m]": prof["L"][k], "Operating T [°C]": T0, "Shut-in P [bar(a)]": P,
                    "Hydrate T [°C]": Th, "Time constant [h]": tau / 3600.0, "No-touch time [h]": t_nt / 3600.0})
    crit = min(pts, key=lambda r: r["No-touch time [h]"])
    tmax = min(max(3 * crit["Time constant [h]"], 6.0), 240.0) if math.isfinite(crit["Time constant [h]"]) else 48.0
    times = [tmax * i / 40 for i in range(41)]
    curve = [T_amb + (crit["Operating T [°C]"] - T_amb) * math.exp(-t / crit["Time constant [h]"])
             if math.isfinite(crit["Time constant [h]"]) else crit["Operating T [°C]"] for t in times]
    return {"Line": name, "points": pts, "No-touch time [h]": crit["No-touch time [h]"],
            "Critical point [m]": crit["Distance [m]"], "Hydrate T at critical point [°C]": crit["Hydrate T [°C]"],
            "Ambient [°C]": T_amb, "U [W/m²·K]": U,
            "curve": {"t_h": times, "T": curve, "T_hyd": crit["Hydrate T [°C]"]}}


def cooldown(model, sol):
    """Cool-down of every solved flowline and riser (with its operating profile)."""
    basis = cool_params(model)
    out = []
    for uid, u in model["units"].items():
        t = u["type"]
        if t not in ("flowline", "riser", "pipe") or uid not in sol.profiles:
            continue
        if t == "pipe" and (u["params"].get("heat") != "Overall U to ambient" or float(u["params"].get("U", 0)) <= 0):
            continue                                        # adiabatic pipe: no heat path defined
        ins = [s for lst in port_edges(model, uid, "in").values() for s in lst]
        outs = [s for lst in port_edges(model, uid, "out").values() for s in lst]
        if not ins or not outs or ins[0] not in sol.streams or outs[0] not in sol.streams:
            continue
        st_in, st_out = sol.streams[ins[0]], sol.streams[outs[0]]
        if st_in.empty:
            continue
        p = u["params"]
        if t == "pipe":
            row, key = {}, None
        else:
            cat, key = ("flowline", "design") if t == "flowline" else ("riser", "rtype")
            try:
                row = surf.item(cat, p.get(key))
            except UnitError:
                row = {}
        U = float(p.get("U", 0.0) or 0.0) or (row.get("U_W_m2K") or 5.0)
        ins_mm = (basis["insul_mm"] if basis["insul_mm"] >= 0
                  else (0.0 if t == "pipe" else INSUL_MM.get(p.get(key), 30.0)))
        D = float(p["ID"]) / 1000.0
        prof = sol.profiles[uid]
        U_flow = U
        if basis.get("U_mode") == U_NATURAL:
            from .flowassure import forced_film, shutin_U
            vm = (prof.get("vm") or [1.0])[0]
            HLs = prof.get("HL") or [0.0]
            U = shutin_U(U_flow, forced_film(sol.fp, st_in, D, vm), sum(HLs) / len(HLs))
        res = cooldown_line(sol.fp, u["name"], prof, st_in, st_out, D, U, float(p.get("T_amb", 4.0)), basis, ins_mm)
        res["U flowing [W/m²·K]"] = U_flow
        res["Type"] = {"flowline": "Flowline", "riser": "Riser", "pipe": "Pipe"}[t]
        res["Insulation counted [mm]"] = ins_mm
        out.append(res)
    return out


# ------------------------------------------------------------------------------------------- heated flowlines

HEAT_DEFAULTS = {"margin": 3.0, "WAT": 0.0, "shutdowns": 4.0, "shutdown_h": 24.0, "mode": "Continuous"}
HEAT_MODES = ("Continuous", "Shutdown and restart only")


def heat_params(model):
    p = dict(HEAT_DEFAULTS)
    p.update({k: v for k, v in (model.get("heating") or {}).items() if k in HEAT_DEFAULTS})
    return p


def line_heat_capacity(fp, st_in, st_out, D, basis, insul_mm):
    """Mean heat capacity per metre [J/(m K)] of the line contents (in-situ), steel wall and insulation share."""
    A = math.pi * D * D / 4.0
    c = 0.5 * (_vol_heat_capacity(fp, st_in, D, 0.0) + _vol_heat_capacity(fp, st_out, D, 0.0))
    wall = basis["wall_mm"] / 1000.0
    ins = insul_mm / 1000.0
    Do = D + 2 * wall
    return (A * c + STEEL_RHO_CP * math.pi * (D + wall) * wall
            + basis["insul_frac"] * basis["insul_MJ_m3K"] * 1e6 * math.pi * (Do + ins) * ins)


def heat_up_time(C, U, D, q, T_amb, T_set):
    """Hours to heat the static line from sea temperature to T_set with q W/m installed:
    C dT/dt = q − U π D (T − T_amb)  →  t = −τ ln(1 − U π D (T_set − T_amb) / q), τ = C / (U π D)."""
    loss = U * math.pi * D * (T_set - T_amb)
    if q <= loss or U <= 0:
        return math.inf
    tau = C / (U * math.pi * D)
    return -tau * math.log(1.0 - loss / q) / 3600.0


def heated_lines(model, sol):
    """Flow-assurance and energy view of every heated flowline: power while flowing, power to hold the line
    above the set temperature during a shutdown, heat-up time after a long shutdown, the unheated no-touch time,
    and the annual energy for the chosen operating mode."""
    from . import economics
    basis, hp = cool_params(model), heat_params(model)
    hours = float(economics.params(model)["hours"])
    shut_h = float(hp["shutdowns"]) * float(hp["shutdown_h"])
    cd = {r["Line"]: r for r in cooldown(model, sol)}
    out = []
    for uid, u in model["units"].items():
        if u["type"] != "flowline" or uid not in sol.profiles or uid not in sol.results:
            continue
        p = u["params"]
        system, ctrl = surf.heating_system(p)
        if system == surf.HEAT_NONE:
            continue
        r = sol.results[uid]
        ins = [s for lst in port_edges(model, uid, "in").values() for s in lst]
        outs = [s for lst in port_edges(model, uid, "out").values() for s in lst]
        st_in, st_out = sol.streams[ins[0]], sol.streams[outs[0]]
        prof = sol.profiles[uid]
        D, L = float(p["ID"]) / 1000.0, surf.flowline_length(p)
        U, T_amb = float(r["U used [W/m²·K]"]), float(p.get("T_amb", 4.0))
        eff = float(r["Heating system efficiency [%]"]) / 100.0
        P_settle = sum(prof["P"]) / len(prof["P"])
        Th = _hydrate_T(sol.fp, st_in, P_settle)
        wat = float(hp["WAT"] or 0.0)
        floor = max([x for x in (Th, wat if wat > 0 else None) if x is not None] or [T_amb])
        T_set = float(p.get("T_hold", 25.0)) if ctrl == surf.CTRL_HOLD else floor + float(hp["margin"])
        try:
            row_ = surf.item("flowline", p.get("design"))
        except UnitError:
            row_ = {}
        q_inst = (float(p.get("q_max_W_m", 0.0) or 0.0) if ctrl == surf.CTRL_HOLD else
                  (float(p.get("deh_W_m", 0.0) or 0.0) or (row_.get("deh_W_m") or 0.0) or float(p.get("q_max_W_m", 0.0) or 0.0)))
        insul = basis["insul_mm"] if basis["insul_mm"] >= 0 else INSUL_MM.get(p.get("design"), 30.0)
        C = line_heat_capacity(sol.fp, st_in, st_out, D, basis, insul)
        hold_Wm = max(U * math.pi * D * (T_set - T_amb), 0.0)
        flowing_kW = float(r["Heat into the fluid [kW]"])
        power_lbl = "Topside heater duty" if system == surf.HEATING[3] else "Electrical power"
        t_up = heat_up_time(C, U, D, q_inst, T_amb, T_set)
        if hp["mode"] == HEAT_MODES[0]:
            energy = flowing_kW / eff * hours / 1000.0 + hold_Wm * L / 1000.0 / eff * shut_h / 1000.0
        else:
            restart = q_inst * L / 1000.0 / eff * (t_up if math.isfinite(t_up) else float(hp["shutdown_h"])) / 1000.0
            energy = hold_Wm * L / 1000.0 / eff * shut_h / 1000.0 + float(hp["shutdowns"]) * restart
        unheated = cd.get(u["name"], {}).get("No-touch time [h]")
        minT = min(prof["T"])
        res = {"Line": u["name"], "Heating system": system, "Control": ctrl, "Set temperature [°C]": T_set,
               "Hydrate T at settle-out [°C]": Th, "WAT [°C]": wat if wat > 0 else None,
               "Minimum fluid T flowing [°C]": minT,
               "Margin to hydrate flowing [°C]": (minT - Th) if Th is not None else None,
               "Heat into the fluid flowing [kW]": flowing_kW, f"{power_lbl} flowing [kW]": flowing_kW / eff,
               "Installed heating [W/m]": q_inst, "Hold power, shut-in [W/m]": hold_Wm,
               f"{power_lbl} to hold during shut-in [kW]": hold_Wm * L / 1000.0 / eff,
               "Can hold the line during shut-in": "yes" if q_inst >= hold_Wm else "no — installed heating too low",
               "Heat-up time from sea temperature [h]": t_up if math.isfinite(t_up) else None,
               "Unheated no-touch time [h]": None if unheated is None or not math.isfinite(unheated) else unheated,
               "Line heat capacity [kJ/(m·K)]": C / 1000.0, "Time constant [h]": C / (U * math.pi * D) / 3600.0 if U > 0 else None,
               f"Annual heating energy ({hp['mode'].lower()}) [MWh/y]": energy,
               "power_label": power_lbl, "uid": uid}
        out.append(res)
    return out


def unheated_profile(model, sol, uid):
    """Profile of a heated flowline recomputed with its heating switched off (for comparison)."""
    u = model["units"][uid]
    unit = {"name": u["name"], "type": "flowline", "params": dict(u["params"], heating=surf.HEAT_NONE, deh="Off")}
    ins = [s for lst in port_edges(model, uid, "in").values() for s in lst]
    try:
        surf.calc_flowline(unit, {"in": [sol.streams[ins[0]]]}, sol.fp)
    except (UnitError, FlashError, ValueError):
        return None
    return unit["_profile"]
