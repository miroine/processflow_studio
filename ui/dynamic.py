"""Dynamic tab: lumped, time-domain simulation of the solved flowsheet (procsim.dynamics) - vessel hold-up with
level and pressure control, blowdown, pipeline line-pack, compressor trip / surge.  Inputs are kept in
``model['dynamics']`` (saved with the flowsheet); results stay in the session."""
from __future__ import annotations

import hashlib
import json

import pandas as pd
import streamlit as st

from procsim import dynamics as DY

from .state import model_hash, sol_is_current, valid_choice
from . import charts

TU = {"s": 1.0, "min": 60.0, "h": 3600.0}
ORIENT = ["Horizontal", "Vertical"]
RELIEF_TYPES = ["BDV", "PSV"]


def _cfg(model):
    return model.setdefault("dynamics", {})


def _sig(model, cfg):
    return (model_hash(model), hashlib.sha1(json.dumps(cfg, sort_keys=True, default=str).encode()).hexdigest()[:12])


def _num(v, default=None):
    try:
        if v is None or (isinstance(v, float) and v != v):
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _frozen(name, make):
    """The editors are fed a table that is frozen until the flowsheet changes (edits are read back from the editor's
    output; feeding them back every run would apply them twice).  Returns (frame, widget key)."""
    ss = st.session_state
    tag = (ss.get("sol_hash"), ss.get("dyn_ver", 0))
    k = f"_dynbase_{name}"
    if ss.get(k, (None,))[0] != tag:
        ss[k] = (tag, make())
    return ss[k][1], f"dyn_{name}_{abs(hash(tag)) % 10**8}"


def _cached_build(model, sol, cfg):
    """DY.build is the slow part of every rerun: keep the last few assemblies (read-only use) keyed on flowsheet + settings."""
    import json
    ss = st.session_state
    try:
        if ss.get("sol_hash") is None:
            return DY.build(model, sol, cfg)
        key = (ss.get("sol_hash"), json.dumps(cfg, sort_keys=True, default=str))
    except Exception:
        return DY.build(model, sol, cfg)
    cache = ss.setdefault("_dyn_builds", {})
    if key not in cache:
        if len(cache) > 6:
            cache.clear()
        cache[key] = DY.build(model, sol, cfg)
    return cache[key]


def dynamic_tab():
    ss = st.session_state
    model = ss.model
    st.markdown("#### Dynamic simulation")
    st.caption("A lumped, time-domain model of the solved flowsheet: well-mixed vessels with level and pressure control, "
               "pipeline line-pack, blowdown through an orifice, and compressors that coast down, trip or surge. "
               "It starts from the steady-state solution (so t = 0 reproduces it exactly) and then follows your "
               "events. Seconds to hours - **no pressure waves or momentum, no slip in pipes, well-mixed vessels**; "
               "see *Help & methods* for the limits.")
    if not sol_is_current():
        st.info("Solve the flowsheet first - the dynamic model is built from the steady-state solution.")
        return
    sol = ss.sol
    cfg = _cfg(model)
    unit = st.selectbox("Time unit for inputs and charts", list(TU), index=list(TU).index(cfg.get("time_unit", "s")),
                        key="dyn_unit", help="Event times and the run length are entered in this unit.")
    cfg["time_unit"] = unit
    f = TU[unit]

    try:
        base = DY.default_settings(model, sol)
        d = _cached_build(model, sol, {k: v for k, v in cfg.items() if k != "time_unit"})
    except DY.DynError as e:
        st.warning(f"This flowsheet cannot be run dynamically yet: {e}")
        return
    for n_ in d.notes:
        st.caption(f"ⓘ {n_}")

    # ---- run settings --------------------------------------------------------------------------------------------
    c = st.columns(3)
    t_end = c[0].number_input(f"Run length [{unit}]", min_value=1e-6, value=max(float(cfg.get("t_end", base["t_end"])) / f, 1e-6),
                              key="dyn_tend") * f
    dt_max = c[1].number_input("Largest time step [s]", min_value=0.01, value=float(cfg.get("dt_max", base["dt_max"])),
                               key="dyn_dtmax", help="The step shrinks by itself when things change fast. Use 1 s or less "
                               "with a compressor and anti-surge loop.")
    dt_out = c[2].number_input("Recording interval [s]", min_value=0.01, value=float(cfg.get("dt_out", base["dt_out"])),
                               key="dyn_dtout")
    cfg.update(t_end=t_end, dt_max=dt_max, dt_out=dt_out)

    # ---- holdups -------------------------------------------------------------------------------------------------
    vessels = [n for n in d.nodes if n.kind != "cell"]
    with st.expander(f"Vessels and lines ({len(vessels)} holdups)", expanded=False):
        rows = []
        for n in vessels:
            nc = cfg.get("nodes", {}).get(n.name, {})
            rows.append({"Holdup": n.name, "Volume [m³]": nc.get("volume", base["nodes"].get(n.name, {}).get("volume", n.V)),
                         "Orientation": nc.get("orient", base["nodes"].get(n.name, {}).get("orient")) or None,
                         "Initial level [%]": nc.get("level0", base["nodes"].get(n.name, {}).get("level0", None))})
        if rows:
            fr_, key_ = _frozen("nodes", lambda rows=rows: pd.DataFrame(rows))
            ed = st.data_editor(fr_, hide_index=True, key=key_, width="stretch", disabled=["Holdup"],
                                column_config={"Orientation": st.column_config.SelectboxColumn(options=ORIENT)})
            nodes = {}
            for r in ed.to_dict("records"):
                dct = {}
                bd = base["nodes"].get(r["Holdup"], {})          # only what differs from the defaults is stored
                v = _num(r["Volume [m³]"])
                if v and v > 0 and not (bd.get("volume") and abs(v - bd["volume"]) <= 1e-9 * max(1.0, bd["volume"])):
                    dct["volume"] = v
                if r["Orientation"] in ORIENT and r["Orientation"] != bd.get("orient"):
                    dct["orient"] = r["Orientation"]
                lv = _num(r["Initial level [%]"])
                if lv is not None:
                    lv = min(max(lv, 0.0), 95.0)
                    if bd.get("level0") is None or abs(lv - bd["level0"]) > 1e-9:
                        dct["level0"] = lv
                if dct:
                    nodes[r["Holdup"]] = dct
            cfg["nodes"] = nodes
            st.caption("Default volumes give about 3 minutes of liquid residence at 50 % level. A holdup with no liquid "
                       "starts empty. Liquid-full vessels are not supported.")
        if d.pipes if hasattr(d, "pipes") else False:
            pc = cfg.setdefault("pipes", {})
            for nm, cells in d.pipes.items():
                pc[nm] = {"cells": int(st.number_input(f"{nm}: number of cells", min_value=2, max_value=60,
                                                       value=int(pc.get(nm, {}).get("cells", len(cells))), key=f"dyn_cells_{nm}"))}

    # ---- controllers ---------------------------------------------------------------------------------------------
    ctrl_all = _cached_build(model, sol, {k: v for k, v in cfg.items() if k not in ("time_unit", "controllers")})   # defaults
    off = [k for k, v in cfg.get("controllers", {}).items() if v.get("enabled") is False]
    with st.expander(f"Controllers ({len(ctrl_all.controllers)})", expanded=False):
        if ctrl_all.controllers:
            rows = []
            for c0 in ctrl_all.controllers:
                o = cfg.get("controllers", {}).get(c0.name, {})
                rows.append({"Controller": c0.name, "Active": c0.name not in off, "Set-point": o.get("sp", c0.sp),
                             "Kc": o.get("Kc", c0.Kc), "Ti [s]": o.get("Ti", c0.Ti)})
            fr_, key_ = _frozen("ctrl", lambda rows=rows: pd.DataFrame(rows))
            ed = st.data_editor(fr_, hide_index=True, key=key_, width="stretch", disabled=["Controller"])
            ov = {}
            for r, c_ in zip(ed.to_dict("records"), ctrl_all.controllers):
                o = {}
                if not r["Active"]:
                    o["enabled"] = False
                for key, col, cur in (("sp", "Set-point", c_.sp), ("Kc", "Kc", c_.Kc), ("Ti", "Ti [s]", c_.Ti)):
                    v = _num(r[col])
                    if v is not None and abs(v - cur) > 1e-12 * max(1.0, abs(cur)):
                        o[key] = v
                if o:
                    ov[c_.name] = o
            cfg["controllers"] = ov
            st.caption("PI controllers, error in % of span, with anti-windup. Defaults: level (liquid / oil outlet valve), "
                       "pressure (gas outlet valve), anti-surge (compressor with *antisurge* on). Un-tick *Active* to hold "
                       "the valve at its steady opening.")
        else:
            st.caption("No controllers: no vessel outlet valves in this flowsheet.")

    # ---- compressors, relief -------------------------------------------------------------------------------------
    comps = [b for b in d.branches if b.kind == "compressor"]
    if comps:
        with st.expander("Compressors", expanded=False):
            cc = cfg.setdefault("compressors", {})
            for b in comps:
                cur = cc.setdefault(b.name, {})
                a = st.columns(4)
                cur["antisurge"] = a[0].toggle(f"{b.name}: anti-surge valve", value=bool(cur.get("antisurge", b.antisurge)), key=f"dyn_as_{b.name}")
                cur["tau_coast"] = a[1].number_input("Coast-down time constant [s]", min_value=1.0, value=float(cur.get("tau_coast", b.tau_coast if hasattr(b, 'tau_coast') else 30.0)), key=f"dyn_tc_{b.name}")
                cur["tau_speed"] = a[2].number_input("Speed-change time constant [s]", min_value=0.5, value=float(cur.get("tau_speed", b.tau_speed if hasattr(b, 'tau_speed') else 10.0)), key=f"dyn_ts_{b.name}")
                cur["stroke_s"] = a[3].number_input("Anti-surge valve stroke [s]", min_value=0.5, value=float(cur.get("stroke_s", 3.0)), key=f"dyn_st_{b.name}")
    with st.expander("Relief and blowdown devices", expanded=bool(cfg.get("relief"))):
      if not vessels:
        st.caption("No vessel to attach a relief device to.")
      else:
        rows = cfg.get("relief") or []
        df = pd.DataFrame(rows, columns=["name", "node", "type", "D_mm", "Cd", "P_set", "P_back"]) if rows else \
            pd.DataFrame({"name": pd.Series(dtype=str), "node": pd.Series(dtype=str), "type": pd.Series(dtype=str),
                          "D_mm": pd.Series(dtype=float), "Cd": pd.Series(dtype=float), "P_set": pd.Series(dtype=float),
                          "P_back": pd.Series(dtype=float)})
        fr_, key_ = _frozen("relief", lambda df=df: df)
        ed = st.data_editor(fr_, hide_index=True, num_rows="dynamic", key=key_, width="stretch",
                            column_config={"name": "Name", "node": st.column_config.SelectboxColumn("Holdup", options=[n.name for n in vessels]),
                                           "type": st.column_config.SelectboxColumn("Type", options=RELIEF_TYPES),
                                           "D_mm": "Orifice [mm]", "Cd": "Cd", "P_set": "Set pressure [bar(a)] (PSV)",
                                           "P_back": "Back-pressure [bar(a)]"})
        rel = []
        for r in ed.to_dict("records"):
            if r.get("name") and r.get("node") in [n.name for n in vessels]:
                rr = {"name": str(r["name"]), "node": r["node"], "type": r.get("type") or "BDV",
                      "D_mm": _num(r.get("D_mm"), 50.0), "Cd": _num(r.get("Cd"), 0.85), "P_back": _num(r.get("P_back"), 1.5)}
                if _num(r.get("P_set")):
                    rr["P_set"] = _num(r["P_set"])
                rel.append(rr)
        cfg["relief"] = rel
        st.caption("A **PSV** opens at its set pressure and reseats at 93 %; a **BDV** stays shut until a *Open a relief / "
                   "blowdown valve* event. Gas flow follows API 520 / ISO 4126 (choked or subsonic). Set the orifice "
                   "so that the flow is realistic for the vessel.")
        if rel:
            d = DY.build(model, sol, {k: v for k, v in cfg.items() if k != "time_unit"})     # the new devices become event targets

    # ---- events --------------------------------------------------------------------------------------------------
    st.markdown("##### Events")
    tg = DY.targets(d)
    all_t = [x for lst in tg.values() for x in lst]
    kinds = list(DY.EVENT_KINDS)
    evs = cfg.get("events") or []
    df = pd.DataFrame([{"Time": e["t"] / f, "Event": DY.EVENT_KINDS.get(e["kind"], (e["kind"],))[0], "Target": e.get("target"),
                        "Value": e.get("value"), "Ramp [s]": e.get("ramp", 0.0)} for e in evs],
                      columns=["Time", "Event", "Target", "Value", "Ramp [s]"]) if evs else \
        pd.DataFrame({"Time": pd.Series(dtype=float), "Event": pd.Series(dtype=str), "Target": pd.Series(dtype=str),
                      "Value": pd.Series(dtype=float), "Ramp [s]": pd.Series(dtype=float)})
    label2kind = {v[0]: k for k, v in DY.EVENT_KINDS.items()}
    fr_, key_ = _frozen("events", lambda df=df: df)
    ed = st.data_editor(fr_, hide_index=True, num_rows="dynamic", key=key_, width="stretch",
                        column_config={"Time": st.column_config.NumberColumn(f"Time [{unit}]", min_value=0.0),
                                       "Event": st.column_config.SelectboxColumn(options=[DY.EVENT_KINDS[k][0] for k in kinds]),
                                       "Target": st.column_config.SelectboxColumn(options=all_t or ["-"])})
    new_ev = []
    for r in ed.to_dict("records"):
        k = label2kind.get(r.get("Event"))
        if k is None or _num(r.get("Time")) is None:
            continue
        e = {"t": _num(r["Time"]) * f, "kind": k, "target": r.get("Target")}
        if k == "ctrl_mode":
            e["value"] = "Manual" if str(r.get("Value") or "").lower().startswith("m") or _num(r.get("Value")) == 1 else "Auto"
        elif _num(r.get("Value")) is not None:
            e["value"] = _num(r["Value"])
        if _num(r.get("Ramp [s]")):
            e["ramp"] = _num(r["Ramp [s]"])
        new_ev.append(e)
    cfg["events"] = new_ev
    with st.expander("What the Value means for each event"):
        for k in kinds:
            st.markdown(f"- **{DY.EVENT_KINDS[k][0]}** ({DY.EVENT_KINDS[k][1]}): {DY.EVENT_KINDS[k][2]}")
    probs = DY.check_events(d, new_ev)
    for p_ in probs:
        st.error(p_)

    # ---- run -----------------------------------------------------------------------------------------------------
    clean = {k: v for k, v in cfg.items() if k != "time_unit"}
    r1, r2 = st.columns([1, 4])
    go = r1.button("Run dynamic simulation", type="primary", key="dyn_run", disabled=bool(probs), width="stretch")
    r2.caption(f"{t_end:g} s of plant time; typically 5 - 20 s of computing per simulated hour.")
    if go:
        bar = st.progress(0.0, text="Simulating…")
        try:
            dd = DY.build(model, sol, clean)
            res = dd.run(progress=lambda a: bar.progress(a, text=f"{a * 100:.0f} % of the run"), max_wall=240)
            ss["dyn_result"] = {"sig": _sig(model, clean), "res": res, "unit": unit}
        except DY.DynError as e:
            ss["dyn_result"] = None
            st.error(str(e))
        bar.empty()
    out = ss.get("dyn_result")
    if not out:
        return
    if out["sig"] != _sig(model, clean):
        st.info("The results below are for different inputs - run the simulation again.")
    _results(out["res"], out["unit"])


def _results(res, unit):
    ss = st.session_state
    f = TU[unit]
    sm = DY.summary(res)
    k = st.columns(4)
    k[0].metric("Status", {"ok": "Completed", "failed": "Stopped (failed)", "stopped": "Stopped (time limit)"}.get(res["status"], res["status"]))
    k[1].metric("Steps", f"{res['steps']}", help=f"{res['rejected']} rejected and retried")
    k[2].metric("Computing time", f"{res['seconds']:.1f} s")
    k[3].metric("Mole balance error", f"{sm['balance_rel']:.1e}", help="|initial + fed − discharged − final| / initial inventory")
    if res["status"] != "ok":
        st.warning(res["message"])
    for n_ in res["notes"]:
        st.caption(f"ⓘ {n_}")
    S = res["series"]
    labs = [k_ for k_ in S if any(v is not None for v in S[k_])]
    flt = st.text_input("Filter the list of results", value="", key="dyn_flt", help="Part of a unit or quantity name, e.g. 'Pressure' or 'K-100'.")
    opts = [l for l in labs if flt.strip().lower() in l.lower()] if flt.strip() else labs
    pref = [l for l in labs if any(s in l for s in (" | Pressure", " | Level", "Surge margin", "OP [%]"))][:4] or labs[:3]
    cur = [v for v in (ss.get("dyn_track") or pref) if v in labs]
    ss["dyn_track"] = cur
    opts = list(dict.fromkeys(cur + opts))
    valid_choice("dyn_track", opts, multi=True)
    track = st.multiselect("Results to show (up to 6 are charted)", opts, key="dyn_track")
    times = [t / f for t in res["t"]]
    if track:
        evs = [(e[0] / f, e[1]) for e in res["events"]]
        st.plotly_chart(charts.dynamic_figure(times, {t: S[t] for t in track}, unit, evs), width="stretch", key="dyn_fig")
    if res["events"]:
        with st.expander("Events that were applied"):
            st.dataframe(pd.DataFrame([{"Time": e[0] / f, "Event": DY.EVENT_KINDS.get(e[1], (e[1],))[0], "Target": e[2], "Value": e[3]}
                                       for e in res["events"]]).rename(columns={"Time": f"Time [{unit}]"}), hide_index=True, width="stretch")
    if sm["extremes"]:
        with st.expander("Minimum and maximum of pressures, temperatures, levels"):
            st.dataframe(pd.DataFrame([{"Quantity": q, "Minimum": a, "Maximum": b} for q, (a, b) in sm["extremes"].items()]),
                         hide_index=True, width="stretch")
    full = pd.DataFrame({f"Time [{unit}]": times, **{l: S[l] for l in labs}})
    st.download_button("Download all results (CSV)", full.to_csv(index=False), file_name="dynamic_results.csv", mime="text/csv", key="dyn_dl")
