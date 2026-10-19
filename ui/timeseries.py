"""Profile tab: run the flowsheet once per time step from a table of rates, pressures and temperatures
(procsim.timeseries) - a quasi-steady profile, not a dynamic simulation."""
from __future__ import annotations

import hashlib
import json

import pandas as pd
import streamlit as st

from procsim import timeseries as TS

from .state import model_hash, valid_choice
from . import charts

VAR_LABEL = {"flow": "Feed rate", "P": "Feed pressure", "T": "Feed temperature"}


def _prof(model):
    return model.setdefault("profile", {})


def _sig(model, rows, unit):
    return (model_hash(model), hashlib.sha1(json.dumps([rows, unit], default=str).encode()).hexdigest()[:12])


def _extra_options(model):
    """'Unit | parameter' for every numeric parameter of the units that are not feeds."""
    out = []
    dup = {n for n in (d["name"] for d in model["units"].values())
           if sum(x["name"] == n for x in model["units"].values()) > 1}      # names must be unique to address a unit
    for d in sorted(model["units"].values(), key=lambda d: d["name"]):
        if d["name"] in dup:
            continue
        if d["type"] in ("feed", "adjust", "recycle", "note"):
            continue
        for k, v in d["params"].items():
            if isinstance(v, (int, float)) and not isinstance(v, bool) and not k.startswith("_"):
                out.append(TS.column(d["name"], k))
    return out


def _set_rows(model, rows):
    p = _prof(model)
    p["rows"] = rows
    st.session_state["prof_ver"] = st.session_state.get("prof_ver", 0) + 1


def profile_tab():
    ss = st.session_state
    model = ss.model
    p = _prof(model)
    fds = TS.feeds(model)
    st.markdown("#### Profile simulation")
    st.caption("Enter a table with one row per time step - feed rate, pressure and temperature (and, if you wish, any "
               "other unit parameter) - and the flowsheet is solved once for every row. A blank cell keeps the "
               "value of the flowsheet. Each step is a full **steady-state** solution (recycles and Adjust loops "
               "included): nothing carries over between steps, so this is a quasi-steady profile - what the plant does at "
               "the conditions of each step - not a dynamic simulation. The single-step solution on the "
               "*Flowsheet* tab is unchanged.")
    if not fds:
        st.info("The flowsheet has no feed unit - add a feed (or load an example) to build a profile.")
        return

    # ---- set-up --------------------------------------------------------------------------------------------------
    c = st.columns([1, 3, 3])
    unit = c[0].selectbox("Time unit", list(TS.TIME_UNITS), index=list(TS.TIME_UNITS).index(p.get("time_unit", "y")),
                          key="prof_unit")
    p["time_unit"] = unit
    names = [n for _, n, _ in fds]
    valid_choice("prof_feeds", names, multi=True)          # a choice remembered from another flowsheet may not exist here
    pick_f = c[1].multiselect("Feeds in the table", names, default=[n for n in (p.get("feeds") or names) if n in names] or names,
                              key="prof_feeds")
    pick_v = c[2].multiselect("Feed variables", list(VAR_LABEL), default=p.get("vars") or ["flow", "P", "T"],
                              format_func=VAR_LABEL.get, key="prof_vars")
    p["feeds"], p["vars"] = pick_f, pick_v
    extra_opts = _extra_options(model)
    cur_extra = [e for e in (p.get("extra") or []) if e in extra_opts]
    valid_choice("prof_extra", extra_opts, multi=True)
    extra = st.multiselect("Other unit parameters to vary (optional)", extra_opts, default=cur_extra, key="prof_extra",
                           help="For example a separator pressure or a compressor speed. Shown as 'Unit | parameter'.")
    p["extra"] = extra
    cols = TS.feed_columns(model, pick_v, pick_f) + extra

    # ---- starting table ------------------------------------------------------------------------------------------
    with st.expander("Generate a starting table", expanded=not p.get("rows")):
        g = st.columns(5)
        n = g[0].number_input("Steps", min_value=1, max_value=TS.MAX_STEPS, value=int(p.get("gen_n", 8)), key="prof_n")
        dt = g[1].number_input(f"Step length [{unit}]", min_value=1e-6, value=float(p.get("gen_dt", 1.0)), key="prof_dt")
        fe = g[2].number_input("Rate at the last step [× present]", min_value=0.0, value=float(p.get("gen_fe", 0.5)), key="prof_fe")
        pe = g[3].number_input("Pressure at the last step [× present]", min_value=0.0, value=float(p.get("gen_pe", 0.8)), key="prof_pe")
        te = g[4].number_input("Temperature change at the last step [K]", min_value=-500.0, max_value=500.0, value=float(p.get("gen_te", 0.0)), key="prof_te")
        s = st.columns([2, 2, 3])
        shape = s[0].selectbox("Shape", ["linear", "exponential"], key="prof_shape")
        if s[1].button("Fill the table", key="prof_fill", width="stretch"):
            p.update(gen_n=int(n), gen_dt=float(dt), gen_fe=float(fe), gen_pe=float(pe), gen_te=float(te))
            try:
                rows = TS.generate(model, n, dt, 0.0, fe, pe, te, shape, pick_v, pick_f)
                base = TS.base_row(model, extra)
                for r in rows:
                    r.update(base)
                _set_rows(model, rows)
                st.rerun()
            except TS.ProfileError as e:
                st.error(str(e))
        s[2].caption("Linear: value × (1 + (factor − 1) × fraction of the way). Exponential: value × factor^fraction. "
                     "Then edit any cell below.")

    # ---- table ---------------------------------------------------------------------------------------------------
    rows = p.get("rows") or []
    if not rows:
        st.caption("No table yet - generate one above, upload a CSV, or add rows below.")
        rows = [dict({"Time": 0.0}, **TS.base_row(model, cols))]
    # keep the chosen columns (data in a column that is no longer chosen is dropped)
    show_cols = ["Time"] + cols
    labels = {c_: ("Time [" + unit + "]" if c_ == "Time" else TS.column_label(model, c_)) for c_ in show_cols}
    # The editor is fed a frozen table that only changes when the columns change or a table is generated/uploaded;
    # edits are read back from its output (feeding them back every run would apply them twice).
    tag = (ss.get("prof_ver", 0), tuple(show_cols), unit, tuple(labels.values()))
    if ss.get("_prof_base_tag") != tag:
        ss["_prof_base_tag"] = tag
        ss["_prof_base"] = pd.DataFrame([{labels[c_]: r.get(c_) for c_ in show_cols} for r in rows])
    ed = st.data_editor(ss["_prof_base"], key=f"prof_ed_{abs(hash(tag)) % 10**8}",
                        num_rows="dynamic", width="stretch",
                        column_config={labels[c_]: st.column_config.NumberColumn(format="%.6g") for c_ in show_cols})
    back = {v: k for k, v in labels.items()}
    new_rows = []
    for rec in ed.to_dict("records"):
        r = {back[k]: (None if TS._blank(v) else float(v)) for k, v in rec.items() if k in back}
        if any(v is not None for v in r.values()):
            new_rows.append(r)
    p["rows"] = new_rows

    u = st.columns(2)
    up = u[0].file_uploader("Upload a profile (CSV: Time, then 'Unit | parameter' columns)", type=["csv"], key="prof_up")
    if up is not None and ss.get("_prof_file") != (up.name, up.size, getattr(up, "file_id", None)):
        ss["_prof_file"] = (up.name, up.size, getattr(up, "file_id", None))
        try:
            rws = TS.parse_csv(up.getvalue().decode("utf-8", "replace"))
            bad = TS.check_columns(model, rws)
            if bad:
                st.error("; ".join(bad))
            else:
                chosen = [c_ for c_ in rws[0] if c_ != "Time"]
                p["feeds"] = sorted({c_.partition(TS.SEP)[0] for c_ in chosen if c_.partition(TS.SEP)[0] in names}) or names
                p["vars"] = [v for v in ("flow", "P", "T") if any(c_.endswith(TS.SEP + TS.FEED_VARS[v]) for c_ in chosen)] or ["flow", "P", "T"]
                p["extra"] = [c_ for c_ in chosen if c_ in extra_opts]
                for k_ in ("prof_feeds", "prof_vars", "prof_extra"):
                    ss.pop(k_, None)
                _set_rows(model, rws)
                st.rerun()
        except TS.ProfileError as e:
            st.error(str(e))
    if new_rows:
        u[1].download_button("Download the table (CSV)", TS.to_csv(model, new_rows), file_name="profile_input.csv",
                             mime="text/csv", key="prof_dl_in")

    # ---- run -----------------------------------------------------------------------------------------------------
    try:
        clean = TS.clean_rows(new_rows)
        problem = "; ".join(TS.check_columns(model, clean))
    except TS.ProfileError as e:
        clean, problem = None, str(e)
    if problem:
        st.error(problem)
    else:
        for w in TS.warnings(model, clean):
            st.info(w)
    est = (ss.sol.seconds if ss.get("sol") is not None else 1.0) * (len(clean) if clean else 0)
    r1, r2 = st.columns([1, 4])
    go = r1.button("Run profile", key="prof_run", type="primary", disabled=bool(problem), width="stretch")
    r2.caption(f"{len(clean) if clean else 0} steps, each a full solve" + (f" - roughly {est:.0f} s" if est >= 5 else ""))
    if go and clean:
        bar = st.progress(0.0, text=f"Solving {len(clean)} time steps…")
        res = TS.run(model, clean, progress=lambda a, b: bar.progress(a / b, text=f"{a} of {b} steps solved"),
                     time_unit=unit)
        bar.empty()
        ss["prof_result"] = {"sig": _sig(model, clean, unit), "res": res}
    out = ss.get("prof_result")
    if not out:
        return
    if clean is not None and out["sig"] != _sig(model, clean, unit):
        st.info("The results below are for different inputs or a different flowsheet - run the profile again.")
    _results(model, out["res"])


def _results(model, res):
    ss = st.session_state
    sm = TS.summary(res)
    k = st.columns(4)
    k[0].metric("Steps", f"{sm['steps']}")
    k[1].metric("Solved", f"{sm['ok']}")
    k[2].metric("With warnings", f"{sm['warning']}")
    k[3].metric("Not solved", f"{sm['failed']}")
    for s in res["steps"]:
        if s["status"] != "ok":
            st.markdown(f"- t = {s['t']:g} {res['time_unit']}: **{s['status']}** - {s['message']}")
    labs = TS.labels(res)
    if not labs:
        st.warning("No step solved - nothing to show. Check that the flowsheet solves on the Flowsheet tab.")
        return
    flt = st.text_input("Filter the list of results", value="", key="prof_flt",
                        help="Type part of a stream, unit or quantity name, e.g. 'Sales gas' or 'Power'.")
    opts = [lab for lab in labs if flt.strip().lower() in lab.lower()] if flt.strip() else labs
    default = [lab for lab in labs if lab.startswith("Overall")][:3]
    cur = [v for v in (ss.get("prof_track") or default) if v in labs]
    ss["prof_track"] = cur
    opts = list(dict.fromkeys(cur + opts))
    valid_choice("prof_track", opts, multi=True)
    track = st.multiselect("Results to show (up to 6 are charted)", opts, key="prof_track")
    if track:
        failed = [s["t"] for s in res["steps"] if s["status"] == "failed"]
        st.plotly_chart(charts.profile_figure([s["t"] for s in res["steps"]], {t: TS.series(res, t) for t in track},
                                              res["time_unit"], failed), width="stretch", key="prof_fig")
        st.dataframe(pd.DataFrame(TS.frame_rows(res, track)), hide_index=True, width="stretch")
    elif res["cumulative"]:
        st.dataframe(pd.DataFrame(TS.frame_rows(res, [])), hide_index=True, width="stretch")
    if res["cumulative"]:
        st.caption("Cumulative columns integrate each step's steady result over its duration (the step is held until "
                   "the next row's time; the last step repeats the previous duration).")
    full = pd.DataFrame(TS.frame_rows(res, labs))
    st.download_button("Download all results (CSV)", full.to_csv(index=False), file_name="profile_results.csv",
                       mime="text/csv", key="prof_dl_out")
