"""Subsea (SURF) tab: system summary, well table, pressure budget from reservoir to arrival, and the
equipment catalogue (view, download, replace with your own CSV)."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from procsim import surf
from procsim import subsea_design as sd
from procsim import subsea_ops as so
from procsim.flowsheet import port_edges

from .state import sol_is_current, bump, SHORT, fmt, qfmt
from . import charts
from . import units as U

CAT_COLS = ["category", "item", "description", "U_W_m2K", "roughness_mm", "dP_bar", "slots", "header_ID_mm",
            "header_length_m", "K_bend", "K_extra", "length_m", "bends", "length_factor", "hog_frac", "sag_frac",
            "deh_W_m", "note"]


# ------------------------------------------------------------------ catalogue

def apply_catalogue(model, text):
    """Validate CSV text and make it the model's SURF catalogue. Returns an error message or None."""
    try:
        rows = surf.parse_csv(text, fill_missing=True)
    except ValueError as e:
        return str(e)
    model["surf_catalogue"] = rows
    surf.activate(rows)
    return None


def reset_catalogue(model):
    model.pop("surf_catalogue", None)
    surf.activate(None)


def catalogue_frame():
    df = pd.DataFrame(surf.rows())
    return df[[c for c in CAT_COLS if c in df.columns]]


def catalogue_panel(model):
    ss = st.session_state
    custom = bool(model.get("surf_catalogue"))
    st.markdown("##### Equipment catalogue")
    st.caption(("**Custom catalogue** (stored in this flowsheet). " if custom else
                "**Built-in catalogue**: generic, illustrative textbook values only, not vendor or company data. ")
               + "Replace it with your own CSV (same columns) to use project data; it is saved with the flowsheet "
                 "file, never with the app.")
    if custom and ss.get("_surf_cat_filled"):
        st.info("Your file had no rows for: " + ", ".join(ss["_surf_cat_filled"]) +
                " — those were taken from the built-in catalogue.")
    st.dataframe(catalogue_frame(), hide_index=True, width="stretch")
    c1, c2, c3 = st.columns([2, 2, 1])
    with c1:
        st.download_button("Download catalogue (CSV)", data=surf.to_csv(surf.rows()), file_name="surf_catalogue.csv",
                           mime="text/csv", width="stretch", key="surf_cat_dl")
    with c2:
        up = st.file_uploader("Replace with a CSV", type=["csv"], key="surf_cat_up")
        if up is not None and ss.get("_surf_cat_file") != (up.name, up.size):
            text = up.getvalue().decode("utf-8-sig")
            err = apply_catalogue(model, text)
            if err:
                st.error(f"Catalogue not loaded: {err}")
            else:
                ss["_surf_cat_file"] = (up.name, up.size)
                ss["_surf_cat_filled"] = surf.filled_categories(text)
                bump(False)
                st.rerun()
    with c3:
        if custom and st.button("Reset", key="surf_cat_reset", width="stretch",
                                help="Go back to the built-in illustrative catalogue"):
            reset_catalogue(model)
            ss["_surf_cat_file"] = None
            bump(False)
            st.rerun()


# ------------------------------------------------------------------ summaries

def surf_units(model):
    return [u for u, d in model["units"].items() if d["type"] in surf.SURF_TYPES]


def well_table(model, sol):
    rows = []
    for uid, u in model["units"].items():
        if u["type"] != "well":
            continue
        r = sol.results.get(uid) or {}
        aof = next((v for k, v in r.items() if k.startswith("AOF")), None)
        rows.append({"Well": u["name"], "IPR": u["params"].get("ipr", ""),
                     "Reservoir P [bar(a)]": r.get("Reservoir P [bar(a)]"),
                     "Bottomhole P [bar(a)]": r.get("Bottomhole flowing P [bar(a)]"),
                     "Drawdown [bar]": r.get("Drawdown [bar]"),
                     "Wellhead P [bar(a)]": r.get("Wellhead P [bar(a)]"), "Wellhead T [°C]": r.get("Wellhead T [°C]"),
                     "Gas [MSm³/d]": r.get("Gas rate [MSm³/d]"), "Oil/cond. [Sm³/d]": r.get("Oil/condensate rate [Sm³/d]"),
                     "Water [Sm³/d]": r.get("Water rate [Sm³/d]"),
                     "Rate / AOF [%]": (100.0 * r["Gas rate [MSm³/d]"] / aof
                                        if aof and "AOF gas [MSm³/d]" in r else None),
                     "Status": sol.status.get(uid, "")})
    return pd.DataFrame(rows)


def _in_out(model, sol, uid):
    ins = [sol.streams[s] for lst in port_edges(model, uid, "in").values() for s in lst if s in sol.streams]
    outs = [sol.streams[s] for lst in port_edges(model, uid, "out").values() for s in lst if s in sol.streams]
    return ins, outs


def equipment_table(model, sol):
    rows = []
    for uid in surf_units(model):
        u = model["units"][uid]
        if u["type"] == "well":
            continue
        r = sol.results.get(uid) or {}
        ins, outs = _in_out(model, sol, uid)
        live_in = [s for s in ins if not s.empty]
        pin = min((s.P for s in live_in), default=None)
        o = outs[0] if outs and not outs[0].empty else None
        remark = (r.get("Warning") or r.get("Choke flow") or r.get("Riser-base slugging risk") or r.get("Slots used")
                  or r.get("Position") or "")
        rows.append({"Unit": u["name"], "Type": SHORT.get(u["type"], u["type"]),
                     "Inlet P [bar(a)]": pin, "Outlet P [bar(a)]": o.P if o else None,
                     "ΔP [bar]": (pin - o.P) if (o and pin is not None) else None,
                     "Outlet T [°C]": (o.T - 273.15) if o else None,
                     "Min. hydrate margin [°C]": r.get("Min. hydrate margin along line [°C]",
                                                      r.get("Downstream hydrate margin [°C]")),
                     "Remarks": str(remark), "Status": sol.status.get(uid, "")})
    return pd.DataFrame(rows)


def budget_steps(model, sol, well_uid, max_units=40):
    """[(label, P_in, P_out)] from a well's reservoir to the end of its flow path (bar(a))."""
    steps = []
    uid = well_uid
    r = sol.results.get(uid) or {}
    name = model["units"][uid]["name"]
    if "Bottomhole flowing P [bar(a)]" not in r:
        return steps
    steps.append((f"{name} inflow", r["Reservoir P [bar(a)]"], r["Bottomhole flowing P [bar(a)]"]))
    steps.append((f"{name} tubing", r["Bottomhole flowing P [bar(a)]"], r["Wellhead P [bar(a)]"]))
    for _ in range(max_units):
        outs = port_edges(model, uid, "out")
        sids = [s for lst in outs.values() for s in lst]
        if len(sids) != 1 or sids[0] not in sol.streams:
            break
        sid = sids[0]
        nxt = model["streams"][sid]["dst"][0]
        u = model["units"][nxt]
        if u["type"] in ("product", "recycle") or nxt not in sol.results:
            break
        nouts = [s for lst in port_edges(model, nxt, "out").values() for s in lst]
        if len(nouts) != 1 or nouts[0] not in sol.streams or sol.streams[nouts[0]].empty:
            break
        steps.append((u["name"], sol.streams[sid].P, sol.streams[nouts[0]].P))
        uid = nxt
    return steps


# ------------------------------------------------------------------ equipment list & CAPEX

def _set_in(section, key, wkey):
    ss = st.session_state
    ss.model.setdefault(section, {})[key] = ss[wkey]


def _num_setting(col, section, params, key, label, fmt_="%.4g", **kw):
    v = st.session_state.widget_ver
    wk = f"{section}_{key}_{v}"
    col.number_input(label, value=float(params[key]), key=wk, format=fmt_, on_change=_set_in,
                     args=(section, key, wk), **kw)


def capex_panel(model, sol):
    ss = st.session_state
    cp = sd.capex_params(model)
    st.caption("Class 5 screening estimate. Equipment costs come from the catalogue's **cost_MUSD** column "
               "(flowlines and risers: per km at a 10-inch bore, scaled by bore); everything else below is an "
               "allowance you set here. All values are **illustrative placeholders**, saved with the flowsheet.")
    with st.expander("Cost basis", expanded=False):
        keys = list(sd.CAPEX_DEFAULTS)
        for row in range(0, len(keys), 3):
            cols = st.columns(3)
            for c, k in zip(cols, keys[row:row + 3]):
                lab, unit = sd.CAPEX_LABELS[k]
                _num_setting(c, "capex", cp, k, f"{lab} [{unit}]", min_value=0.0)
        if st.button("Reset cost basis", key=f"capex_reset_{ss.widget_ver}"):
            model.pop("capex", None)
            ss.widget_ver += 1
            st.rerun()
    items, tot = sd.equipment_list(model, sol)
    if not items:
        st.caption("No subsea equipment to cost.")
        return
    k = st.columns(4)
    k[0].metric("Total CAPEX", f"{tot['Total CAPEX [MUSD]']:,.0f} MUSD")
    k[1].metric("Subsea equipment & lines", f"{tot['Subsea equipment & lines [MUSD]']:,.0f} MUSD")
    k[2].metric("Installation", f"{tot['Installation [MUSD]']:,.0f} MUSD")
    k[3].metric("Wells (D&C)", f"{tot['Wells [MUSD]']:,.0f} MUSD")
    st.plotly_chart(charts.capex_figure(tot["by_group"], tot), width="stretch", key="surf_capex")
    df = pd.DataFrame(items)
    st.markdown("##### Equipment list")
    st.dataframe(df, hide_index=True, width="stretch",
                 column_config={c: st.column_config.NumberColumn(format="%.2f") for c in
                                ("Qty", "Unit cost [MUSD]", "Equipment [MUSD]", "Installation [MUSD]", "Total [MUSD]")})
    if tot["missing_costs"]:
        st.warning("No catalogue cost for: " + ", ".join(tot["missing_costs"]) + " (counted as zero).")
    summary = pd.DataFrame([{"Line": k_.replace(" [MUSD]", ""), "MUSD": v_} for k_, v_ in tot.items()
                            if k_.endswith("[MUSD]")])
    st.dataframe(summary, hide_index=True, width="stretch",
                 column_config={"MUSD": st.column_config.NumberColumn(format="%.1f")})
    st.download_button("Download equipment list (CSV)", data=df.to_csv(index=False), file_name="subsea_equipment_list.csv",
                       mime="text/csv", key="surf_capex_dl")


# ------------------------------------------------------------------ umbilical & power

def umbilical_panel(model, sol):
    ss = st.session_state
    up = sd.umbilical_params(model)
    st.caption("Chemical-injection tubes are sized for friction ΔP and velocity over the umbilical length "
               "(smooth tube, Churchill friction); the topside pump pressure is delivery pressure + friction − "
               "hydrostatic head. Booster power: 3-phase AC cable, smallest voltage and conductor meeting ampacity, "
               "voltage drop and a charging-current limit. Generic data, screening only.")
    with st.expander("Design basis", expanded=False):
        c = st.columns(4)
        _num_setting(c[0], "umbilical", up, "length_km", "Umbilical length [km] (0 = flowline + riser)", min_value=0.0)
        _num_setting(c[1], "umbilical", up, "depth_m", "Water depth [m] (0 = from the riser)", min_value=0.0)
        _num_setting(c[2], "umbilical", up, "dP_allow", "Allowable tube friction ΔP [bar]", min_value=1.0)
        _num_setting(c[3], "umbilical", up, "v_max", "Maximum velocity [m/s]", min_value=0.1)
        c = st.columns(4)
        _num_setting(c[0], "umbilical", up, "tube_rating", "Tube pressure rating [bar]", min_value=10.0)
        _num_setting(c[1], "umbilical", up, "pump_eff", "Injection pump efficiency [%]", min_value=5.0, max_value=100.0)
        _num_setting(c[2], "umbilical", up, "cos_phi", "Power factor cos φ [-]", min_value=0.5, max_value=1.0)
        _num_setting(c[3], "umbilical", up, "dV_max", "Maximum voltage drop [%]", min_value=1.0, max_value=30.0)
    st.markdown("##### Injection services")
    # the editor keeps its edits as deltas on the data it was first given, so hand it the same base table on
    # every rerun (a new base comes with a new widget version, e.g. after loading a flowsheet)
    bkey = f"_umb_base_{ss.widget_ver}"
    if bkey not in ss:
        ss[bkey] = sd.services(model)
    cur = pd.DataFrame(ss[bkey])
    ed = st.data_editor(cur, key=f"umb_services_{ss.widget_ver}", num_rows="dynamic", hide_index=True, width="stretch",
                        column_config={"Fluid": st.column_config.SelectboxColumn(options=list(sd.FLUIDS)),
                                       "Flow [L/h]": st.column_config.NumberColumn(min_value=0.0, format="%.1f"),
                                       "Delivery P [bar(a)]": st.column_config.NumberColumn(
                                           min_value=0.0, format="%.1f", help="0 = highest wellhead pressure + 10 bar")})
    recs = [{k: (None if (isinstance(v, float) and v != v) else v) for k, v in r.items()}
            for r in ed.to_dict("records") if any(x not in (None, "") for x in r.values())]
    if recs != sd.services(model):
        model.setdefault("umbilical", {})["services"] = recs
    res = sd.umbilical_design(model, sol)
    k = st.columns(3)
    k[0].metric("Umbilical length", f"{res['length_km']:,.1f} km")
    k[1].metric("Water depth", f"{res['depth_m']:,.0f} m")
    k[2].metric("Subsea booster power", f"{res['booster_kW']:,.0f} kW")
    rows = pd.DataFrame(res["services"])
    if not rows.empty:
        st.dataframe(rows, hide_index=True, width="stretch",
                     column_config={c_: st.column_config.NumberColumn(format="%.2f") for c_ in
                                    ("Velocity [m/s]", "Friction ΔP [bar]", "Hydrostatic head [bar]",
                                     "Topside pump P [bar(a)]", "Pump power [kW]", "Delivery P [bar(a)]")
                                    if c_ in rows.columns})
    st.caption("Also in the umbilical: " + res["hydraulics"] + ".")
    cab = res["cable"]
    if cab:
        st.markdown("##### Booster power cable")
        c = st.columns(5)
        c[0].metric("Voltage", f"{cab['Voltage [kV]']:g} kV")
        c[1].metric("Conductor", f"3 × {cab['Conductor [mm²]']} mm²")
        c[2].metric("Current", f"{cab['Current [A]']:,.0f} A of {cab['Ampacity [A]']} A")
        c[3].metric("Voltage drop", f"{cab['Voltage drop [%]']:.1f} %")
        c[4].metric("Charging current", f"{cab['Charging current [A]']:,.0f} A")
        st.caption(f"Cable losses {cab['Cable losses [kW]']:,.0f} kW at full load.")
    elif res["booster_kW"] <= 0:
        st.caption("No subsea boosters in the flowsheet: no power cable needed.")
    for n in res["notes"]:
        st.warning(n)


# ------------------------------------------------------------------ tie-back screening

def _floats(text, lo=None):
    out = []
    for t in str(text).replace(";", ",").split(","):
        t = t.strip()
        if t:
            v = float(t)
            if lo is not None and v <= lo:
                raise ValueError(f"{v:g} must be above {lo:g}")
            out.append(v)
    if not out:
        raise ValueError("enter at least one value")
    return sorted(set(out))


def downstream_riser(model, uid, max_units=10):
    """First riser downstream of a unit along single-outlet connections (or None)."""
    for _ in range(max_units):
        sids = [s for lst in port_edges(model, uid, "out").values() for s in lst]
        if len(sids) != 1:
            return None
        uid = model["streams"][sids[0]]["dst"][0]
        if model["units"][uid]["type"] == "riser":
            return uid
    return None


def run_screening(model, sol, fl_uid, distances, rates, boost, progress=None):
    rs = downstream_riser(model, fl_uid)
    inlet = sol.streams[port_edges(model, fl_uid, "in")["in"][0]]
    return sd.tieback_screen(sol.fp, inlet, model["units"][fl_uid]["params"],
                             model["units"][rs]["params"] if rs else None, distances, rates, boost, progress)


def screening_panel(model, sol):
    ss = st.session_state
    fls = {model["units"][u]["name"]: u for u in model["units"] if model["units"][u]["type"] == "flowline"}
    if not fls:
        st.info("Add a subsea flowline to screen tie-back distances.")
        return
    st.caption("Takes the solved flowline inlet (pressure, temperature, composition) and recomputes flowline + riser "
               "for each distance and rate. Rates scale the inlet flow at constant composition; the inlet pressure is "
               "held (well deliverability is not re-evaluated); an optional boost raises the inlet pressure at "
               "constant temperature (ideal booster). Screening only.")
    c = st.columns([2, 2, 2, 1.4, 1.4])
    fl_name = c[0].selectbox("Flowline", list(fls), key="tb_fl")
    dist_txt = c[1].text_input("Distances [km]", value="5, 15, 30, 50, 75", key="tb_dist")
    rate_txt = c[2].text_input("Rate factors [× current]", value="0.5, 1, 1.5", key="tb_rates")
    boost = c[3].number_input("Boost ΔP [bar]", value=0.0, min_value=0.0, key="tb_boost", format="%.4g")
    P_min = c[4].number_input(f"Min. arrival P [{U.uP()}]", value=float(U.P(40.0)), min_value=0.0, key="tb_pmin",
                              format="%.4g")
    P_min_si = U.to_si("bar(a)", P_min)
    try:
        distances, rates = _floats(dist_txt, 0.0), _floats(rate_txt, 0.0)
    except ValueError as e:
        st.error(f"Check the inputs: {e}")
        return
    fl = fls[fl_name]
    rs = downstream_riser(model, fl)
    st.caption(f"Riser included: **{model['units'][rs]['name']}**" if rs else "No riser found downstream: flowline only.")
    sig = (ss.sol_hash, fl, tuple(distances), tuple(rates), float(boost))
    if st.button("Run screening", key="tb_run", type="primary"):
        bar = st.progress(0.0, text=f"Screening {len(distances) * len(rates)} cases…")
        rows = run_screening(model, sol, fl, distances, rates, float(boost),
                             lambda f: bar.progress(min(max(f, 0.0), 1.0)))
        ss["tieback"] = {"sig": sig, "rows": rows}
    tb = ss.get("tieback")
    if not tb:
        st.caption(f"{len(distances) * len(rates)} cases; press **Run screening** (about 1–2 s per case).")
        return
    if tb["sig"] != sig:
        st.info("The results below are for different inputs or an earlier solution — press **Run screening** again.")
    rows = tb["rows"]
    st.plotly_chart(charts.tieback_pressure_figure(rows, P_min_si), width="stretch", key="tb_p")
    st.plotly_chart(charts.tieback_margin_figure(rows), width="stretch", key="tb_hm")
    uL = "km" if not U.field() else "mi"
    f_ = 1.0 if not U.field() else 1 / 1.609344
    summ = []
    for rf in sorted({r["Rate factor"] for r in rows}):
        d, basis = sd.max_distance(rows, rf, P_min_si)
        summ.append({"Rate factor": rf, f"Max tie-back [{uL}]": None if d is None else d * f_, "Basis": basis})
    st.markdown(f"##### Maximum tie-back distance for an arrival pressure ≥ {P_min:g} {U.uP()}")
    st.dataframe(pd.DataFrame(summ), hide_index=True, width="stretch",
                 column_config={f"Max tie-back [{uL}]": st.column_config.NumberColumn(format="%.1f")})
    with st.expander("All cases", expanded=False):
        st.dataframe(U.df_display(pd.DataFrame(rows)), hide_index=True, width="stretch")


# ------------------------------------------------------------------ tab

# ------------------------------------------------------------------ Phase 3: slugging, turndown, layout

def _kv_table(d, skip=()):
    rows = [{"Quantity": k2, "Value": fmt(v2)} for k2, v2 in (U.kv(k, v) for k, v in d.items() if k not in skip)]
    return pd.DataFrame(rows)


def slugging_panel(model, sol):
    ss = st.session_state
    st.caption("Transient-lite screening: steady-state slug correlations and volume balances, to size the arrival "
               "surge volume and flag risers that may block. Hydrodynamic slugs at the riser base: Gregory & Scott "
               "(1969) frequency, Norris (1982) mean length with a log-normal 1-in-1000 maximum, Gregory et al. (1978) "
               "slug-body holdup. Severe slug: the riser fills with liquid while flowline gas and liquid keep "
               "arriving (volume balance). Confirm with a dynamic multiphase model.")
    margin = st.number_input("Design margin on the governing slug [%]", value=20.0, min_value=0.0, max_value=200.0,
                             key="slug_margin", format="%.4g")
    res = so.slug_assessment(model, sol, 1.0 + margin / 100.0)
    if not res:
        st.info("No solved subsea flowline in this flowsheet.")
        return
    td = ss.get("turndown")
    ramp = td["window"].get("ramp-up surge [m³]") if (td and td.get("sig", (None,))[0] == ss.sol_hash) else None
    summ = []
    for r in res:
        sev = r.get("severe") or {}
        summ.append({"Flowline": r["Flowline"], "Riser": r["Riser"], "Flow regime at riser base": r["hydro"]["Flow regime"],
                     "1-in-1000 hydrodynamic slug [m³]": r["hydro"]["1-in-1000 slug volume [m³]"] if r["hydro"]["Slugging"] else 0.0,
                     "Severe-slug risk": (sev.get("Risk") or "—").split(":")[0],
                     "Severe slug volume [m³]": sev.get("Severe slug volume [m³]"),
                     "Governing": r["Governing slug"], "Design surge volume [m³]": r["Design surge volume [m³]"]})
    df = pd.DataFrame(summ)
    st.dataframe(U.df_display(df), hide_index=True, width="stretch")
    big = max(r["Design surge volume [m³]"] for r in res)
    c = st.columns(3)
    c[0].metric("Arrival surge volume (slugs)", qfmt("Volume [m³]", big, 1))
    c[1].metric("Ramp-up sweep-out (turndown tab)", "run the turndown" if ramp is None else qfmt("Volume [m³]", ramp, 1))
    c[2].metric("Slug-catcher / separator surge to design for",
                qfmt("Volume [m³]", max(big, (ramp or 0.0) * (1.0 + margin / 100.0)), 1),
                help="The larger of the governing slug and the liquid swept out when ramping from minimum to maximum "
                     "rate, with the design margin")
    for r in res:
        with st.expander(f"Details: {r['Flowline']} → {r['Riser']}", expanded=False):
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**Hydrodynamic slugging (riser base)**")
                st.dataframe(_kv_table(r["hydro"]), hide_index=True, width="stretch")
                if not r["hydro"]["Slugging"]:
                    st.caption(f"{r['hydro']['Flow regime']} flow: no hydrodynamic slugs expected; the statistics show "
                               "what slugs would look like if the regime changed.")
            with c2:
                if r.get("severe"):
                    st.markdown("**Severe (riser-base) slug if the riser blocks**")
                    st.dataframe(_kv_table(r["severe"]), hide_index=True, width="stretch")
                    if not str(r["severe"].get("Risk", "")).startswith("High"):
                        st.caption("Bøe screening says the riser does not block at this rate; the volume is what a "
                                   "blocked riser would hold.")


def turndown_panel(model, sol):
    ss = st.session_state
    fls = {model["units"][u]["name"]: u for u in model["units"] if model["units"][u]["type"] == "flowline"}
    if not fls:
        st.info("Add a subsea flowline to build a turndown envelope.")
        return
    st.caption("Flowline + riser recomputed from the solved flowline inlet at each rate (composition, pressure and "
               "temperature held). Limits: arrival pressure ≥ minimum, hydrate margin ≥ 0, erosional ratio ≤ 1, no "
               "severe riser-base slugging. The window is the continuous feasible range around the current rate; the "
               "ramp-up sweep-out is the liquid inventory at minimum rate minus that at maximum rate.")
    c = st.columns([2, 3, 1.6])
    fl_name = c[0].selectbox("Flowline", list(fls), key="td_fl")
    rate_txt = c[1].text_input("Rate factors [× current]", value="0.1, 0.2, 0.35, 0.5, 0.7, 0.85, 1, 1.2, 1.4", key="td_rates")
    P_min = c[2].number_input(f"Min. arrival P [{U.uP()}]", value=float(U.P(40.0)), min_value=0.0, key="td_pmin",
                              format="%.4g")
    P_min_si = U.to_si("bar(a)", P_min)
    try:
        rates = _floats(rate_txt, 0.0)
    except ValueError as e:
        st.error(f"Check the rate factors: {e}")
        return
    fl = fls[fl_name]
    rs = downstream_riser(model, fl)
    sig = (ss.sol_hash, fl, tuple(rates), float(P_min_si))
    if st.button("Run turndown", key="td_run", type="primary"):
        bar = st.progress(0.0, text=f"Turndown: {len(rates)} rates…")
        inlet = sol.streams[port_edges(model, fl, "in")["in"][0]]
        rows, win = so.turndown_envelope(sol.fp, inlet, model["units"][fl]["params"],
                                         model["units"][rs]["params"] if rs else None, rates, P_min_si,
                                         lambda f: bar.progress(min(max(f, 0.0), 1.0)))
        ss["turndown"] = {"sig": sig, "rows": rows, "window": win}
    td = ss.get("turndown")
    if not td:
        st.caption(f"{len(rates)} rates; press **Run turndown** (about 1–2 s per rate).")
        return
    if td["sig"] != sig:
        st.info("The results below are for different inputs or an earlier solution — press **Run turndown** again.")
    win = td["window"]
    k = st.columns(4)
    if win["feasible"]:
        k[0].metric("Minimum rate", qfmt("Gas [MSm³/d]", win["min gas [MSm³/d]"], 2), f"{win['min']:g} × current",
                    delta_color="off")
        k[1].metric("Maximum rate", qfmt("Gas [MSm³/d]", win["max gas [MSm³/d]"], 2), f"{win['max']:g} × current",
                    delta_color="off")
        k[2].metric("Turndown ratio", f"{win['max'] / win['min']:.1f} : 1")
        k[3].metric("Ramp-up sweep-out", qfmt("Volume [m³]", win["ramp-up surge [m³]"], 1))
        st.caption(f"Low end limited by: **{win['low limit']}** · high end limited by: **{win['high limit']}**")
    else:
        st.warning("No rate in the sweep meets every limit.")
    st.plotly_chart(charts.turndown_figure(td["rows"], win, P_min_si), width="stretch", key="td_fig")
    with st.expander("All rates", expanded=False):
        st.dataframe(U.df_display(pd.DataFrame(td["rows"])), hide_index=True, width="stretch")


def _set_bearing(uid, wkey):
    ss = st.session_state
    ss.model.setdefault("layout", {}).setdefault("bearing", {})[uid] = float(ss[wkey])


def layout_panel(model, sol):
    ss = st.session_state
    risers = [u for u in model["units"] if model["units"][u]["type"] == "riser"]
    if not risers:
        st.info("The layout starts from a riser at the host: add a riser to see the field layout.")
        return
    st.caption("Built from the flowsheet: the host at the origin, each riser's touch-down at its horizontal footprint, "
               "flowlines and jumpers to scale along a bearing, well slots on a ring around their template (not to "
               "scale), satellites on their own bearing. Set the bearings to match your field; they are saved with "
               "the flowsheet and never trigger a re-solve.")
    bear = (model.get("layout") or {}).get("bearing") or {}
    cols = st.columns(min(len(risers), 4))
    for k, r in enumerate(sorted(risers, key=lambda u: model["units"][u]["name"])):
        wk = f"lay_b_{r}_{ss.widget_ver}"
        cols[k % len(cols)].number_input(f"Bearing of {model['units'][r]['name']} line [° from north]",
                                         value=float(bear.get(r, so.default_bearing(k))), min_value=0.0, max_value=360.0,
                                         key=wk, on_change=_set_bearing, args=(r, wk), format="%.4g")
    lay = so.field_layout(model, sol)
    st.plotly_chart(charts.layout_figure(lay), width="stretch", key="lay_fig")
    rows = [{"Object": n["name"], "Type": SHORT.get(n["type"], n["type"]).replace("host", "Host"),
             "East [km]": n["x"], "North [km]": n["y"], "Distance from host [km]": (n["x"] ** 2 + n["y"] ** 2) ** 0.5}
            for n in lay["nodes"]]
    with st.expander("Coordinates", expanded=False):
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                     column_config={c_: st.column_config.NumberColumn(format="%.2f")
                                    for c_ in ("East [km]", "North [km]", "Distance from host [km]")})


def _set_cool(key, wkey):
    ss = st.session_state
    ss.model.setdefault("cooldown", {})[key] = ss[wkey]


def cooldown_panel(model, sol):
    ss = st.session_state
    st.caption("After a shutdown the fluid stops and cools towards the sea temperature. Each point of the operating "
               "profile cools exponentially with a time constant set by the heat stored in the contents (in-situ "
               "holdup), the steel wall and part of the insulation, over U·πD. The no-touch time is how long until "
               "the coldest-responding point reaches the hydrate temperature at the shut-in pressure — the time "
               "available before depressurisation or inhibitor injection. The flowing U is used, which is "
               "conservative (the internal film is weaker at rest). Screening only.")
    cp = so.cool_params(model)
    v = ss.widget_ver
    with st.expander("Cool-down basis", expanded=False):
        c = st.columns(5)
        for col, key, lab, kw in ((c[0], "wall_mm", "Steel wall [mm]", {"min_value": 1.0}),
                                  (c[1], "insul_mm", "Insulation [mm] (−1 = by design)", {"min_value": -1.0}),
                                  (c[2], "insul_MJ_m3K", "Insulation ρ·cp [MJ/m³K]", {"min_value": 0.0}),
                                  (c[3], "insul_frac", "Share of insulation heat counted [-]",
                                   {"min_value": 0.0, "max_value": 1.0})):
            wk = f"cool_{key}_{v}"
            col.number_input(lab, value=float(cp[key]), key=wk, format="%.4g", on_change=_set_cool, args=(key, wk), **kw)
        opts = ["Settle-out", "Local operating"]
        wk = f"cool_shutin_{v}"
        c[4].selectbox("Shut-in pressure", opts, index=opts.index(cp["shutin"]) if cp["shutin"] in opts else 0, key=wk,
                       on_change=_set_cool, args=("shutin", wk),
                       help="Settle-out: the line's mean operating pressure everywhere; local: the operating pressure")
    lines = so.cooldown(model, sol)
    if not lines:
        st.info("No solved flowline, riser or heat-losing pipe to cool down.")
        return
    shortest = min(lines, key=lambda r: r["No-touch time [h]"])
    k = st.columns(3)
    nt = shortest["No-touch time [h]"]
    k[0].metric("Shortest no-touch time", "no hydrate risk" if nt == float("inf") else f"{nt:.1f} h")
    k[1].metric("Governing line", shortest["Line"])
    k[2].metric("At", qfmt("Distance [m]", shortest["Critical point [m]"], 0) + " from the line inlet")
    st.plotly_chart(charts.cooldown_figure(lines), width="stretch", key="cool_fig")
    df = pd.DataFrame([{"Line": r["Line"], "Type": r["Type"], "U [W/m²·K]": r["U [W/m²·K]"],
                        "Insulation counted [mm]": r["Insulation counted [mm]"], "Sea temperature [°C]": r["Ambient [°C]"],
                        "Critical point [m]": r["Critical point [m]"],
                        "Hydrate T at shut-in [°C]": r["Hydrate T at critical point [°C]"],
                        "No-touch time [h]": None if r["No-touch time [h]"] == float("inf") else r["No-touch time [h]"]}
                       for r in lines])
    st.dataframe(U.df_display(df), hide_index=True, width="stretch")
    st.caption("No-touch time — blank: the line never cools into the hydrate region (hydrate T below the sea "
               "temperature, or no free water); 0: already inside it while flowing.")


def surf_tab():
    ss = st.session_state
    model = ss.model
    sol = ss.sol if sol_is_current() else None
    st.markdown("#### Subsea production system (SURF)")
    units = surf_units(model)
    t_sys, t_lay, t_capex, t_umb, t_tb, t_td, t_slug, t_cool, t_cat = st.tabs(
        ["System", "Field layout", "Equipment & CAPEX", "Umbilical & power", "Tie-back screening", "Turndown",
         "Slugging", "Cool-down", "Catalogue"])
    with t_sys:
        if not units:
            st.info("No subsea equipment in this flowsheet. Drag wells, Xmas trees, templates, jumpers, flowlines, "
                    "risers, boosters and SSIV/HIPPS valves from the **Subsea (SURF)** palette group, or load a "
                    "*Subsea (SURF)* example from the sidebar.")
        elif sol is None:
            st.info("Solve the flowsheet to see the subsea summary.")
        else:
            system_panel(model, sol, units)
    with t_capex:
        if units:
            capex_panel(model, sol)
        else:
            st.caption("Add subsea equipment to build the equipment list.")
    with t_umb:
        if units:
            umbilical_panel(model, sol)
        else:
            st.caption("Add subsea equipment to size the umbilical.")
    with t_tb:
        if sol is None:
            st.info("Solve the flowsheet first.")
        else:
            screening_panel(model, sol)
    with t_lay:
        if units:
            layout_panel(model, sol)
        else:
            st.caption("Add subsea equipment to see the field layout.")
    with t_td:
        if sol is None:
            st.info("Solve the flowsheet first.")
        else:
            turndown_panel(model, sol)
    with t_slug:
        if sol is None:
            st.info("Solve the flowsheet first.")
        else:
            slugging_panel(model, sol)
    with t_cool:
        if sol is None:
            st.info("Solve the flowsheet first.")
        else:
            cooldown_panel(model, sol)
    with t_cat:
        catalogue_panel(model)
        with st.expander("Catalogue columns", expanded=False):
            st.markdown(CATALOGUE_HELP)


CATALOGUE_HELP = (
    "- **category**: flowline, riser, tree, template, jumper, valve or booster; **item**: the name shown in the "
    "property view\n"
    "- **U_W_m2K** overall heat-transfer coefficient (flowlines, risers); **roughness_mm**\n"
    "- **dP_bar** pressure drop of tree valves / subsea valves when open\n"
    "- **slots**, **header_ID_mm**, **header_length_m**, **K_extra** for templates\n"
    "- **length_m**, **bends**, **K_bend**, **K_extra** for jumpers, spools, PLET and PLEM\n"
    "- **length_factor** (riser length / water depth), **hog_frac** and **sag_frac** (lazy-wave bend "
    "heights as a fraction of water depth)\n"
    "- **deh_W_m** direct electrical heating input per metre\n"
    "- boosters: **eff_pct** efficiency, **min_gvf** / **max_gvf** gas-volume-fraction window (0–1), "
    "**max_dP_bar** boost per machine, **rated_kW** shaft power, **motor_eff**\n"
    "- **cost_MUSD**: cost per item; for flowlines and risers per km at a 10-inch bore\n"
    "Empty cells are allowed where a column does not apply. A category missing from an uploaded file is taken "
    "from the built-in catalogue. Units that reference an item missing from a new catalogue report an error until "
    "another item is selected.")


def system_panel(model, sol, units):
    wt = well_table(model, sol)
    if not wt.empty:
        st.markdown("##### Wells: inflow and lift")
        st.dataframe(U.df_display(wt), hide_index=True, width="stretch")
    et = equipment_table(model, sol)
    if not et.empty:
        st.markdown("##### Subsea equipment")
        st.dataframe(U.df_display(et), hide_index=True, width="stretch")
    wells = [u for u in units if model["units"][u]["type"] == "well" and u in sol.results]
    if wells:
        names = {model["units"][u]["name"]: u for u in wells}
        pick = st.selectbox("Pressure budget for well", list(names), key="surf_budget_well")
        steps = budget_steps(model, sol, names[pick])
        if steps:
            st.plotly_chart(charts.pressure_budget_figure(steps, f"Pressure budget: {pick} to arrival"),
                            width="stretch", key="surf_budget")
    for uid in units:
        u = model["units"][uid]
        if u["type"] == "riser" and uid in sol.profiles:
            st.plotly_chart(charts.surf_profile_figure(sol.profiles[uid], u["name"]), width="stretch",
                            key=f"surf_rsr_{uid}")
