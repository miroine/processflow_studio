"""Calibrate & optimise tab (v7.8): fit unit parameters to plant data, then optimise the calibrated flowsheet.

Plant data are entered as rows - ``set`` rows give the operating point of a case (feed rate, a set-point ...), ``measure`` rows give
what was measured (unit result, stream property, composition, economics total) with a σ - or imported from CSV.  Cases flagged
*validation* are held out of the fit and show how the calibrated model predicts unseen conditions (procsim.tuning)."""
from __future__ import annotations

import io

import pandas as pd
import streamlit as st

from procsim import tuning as TU
from procsim.unitops import CATALOGUE

from . import charts
from . import state as S
from .state import sol_is_current, fmt

COLS = ["Case", "Use", "Row", "Kind", "Object", "Quantity", "Value", "σ"]
USES = ["fit", "validation"]
ROWS = ["set", "measure"]
KINDS = ["unit", "stream", "economics"]


def _store():
    ss = st.session_state
    cal = ss.model.setdefault("calibration", {})
    cal.setdefault("rows", [])
    cal.setdefault("params", [])
    cal.setdefault("opt", {"vars": [], "constraints": [], "objective": {"kind": "economics", "obj": "", "q": "Power demand [kW]", "sense": "min"}})
    return cal


def rows_to_cases(rows):
    """Long table rows -> cases for procsim.tuning.calibrate."""
    cases, order = {}, []
    for r in rows:
        name = str(r.get("Case") or "").strip()
        if not name:
            continue
        if name not in cases:
            cases[name] = {"name": name, "validate": False, "set": [], "measure": []}
            order.append(name)
        if r.get("Use") == "validation":
            cases[name]["validate"] = True
        if r.get("Row") == "set":
            cases[name]["set"].append({"unit": str(r["Object"]), "key": str(r["Quantity"]), "value": float(r["Value"])})
        elif r.get("Row") == "measure":
            sg = r.get("σ")
            cases[name]["measure"].append({"kind": str(r["Kind"]), "obj": str(r.get("Object") or ""), "q": str(r["Quantity"]),
                                           "value": float(r["Value"]), "sigma": None if sg in (None, "") or pd.isna(sg) else float(sg)})
    return [cases[n] for n in order]


def _frame(rows):
    df = pd.DataFrame(rows, columns=COLS) if rows else pd.DataFrame({c: pd.Series(dtype=object) for c in COLS})
    for c in ("Value", "σ"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def _clean(df):
    out = []
    for r in df.to_dict("records"):
        if not str(r.get("Case") or "").strip() or r.get("Row") not in ROWS or pd.isna(r.get("Value")):
            continue
        out.append({"Case": str(r["Case"]).strip(), "Use": r.get("Use") if r.get("Use") in USES else "fit", "Row": r["Row"],
                    "Kind": r.get("Kind") if r.get("Kind") in KINDS else "unit", "Object": "" if pd.isna(r.get("Object")) else str(r["Object"]),
                    "Quantity": "" if pd.isna(r.get("Quantity")) else str(r["Quantity"]), "Value": float(r["Value"]),
                    "σ": None if pd.isna(r.get("σ")) else float(r["σ"])})
    return out


def _pick(prefix, model, sol, kinds=KINDS):
    """kind / object / quantity pickers for something that can be read from a solution."""
    ss = st.session_state
    c1, c2, c3 = st.columns([1, 2, 3])
    kind = c1.selectbox("Kind", kinds, key=f"{prefix}_kind_{ss.widget_ver}", format_func=lambda k: {"unit": "Unit result", "stream": "Stream", "economics": "Economics total"}[k])
    if kind == "unit":
        objs = sorted(u["name"] for u in model["units"].values() if u["type"] not in ("platform",))
    elif kind == "stream":
        objs = sorted({s["name"] for s in model["streams"].values()})
    else:
        objs = [""]
    obj = c2.selectbox("Object", objs, key=f"{prefix}_obj_{ss.widget_ver}") if objs else ""
    qs = TU.quantities(model, sol, kind, obj) if sol is not None else []
    q = c3.selectbox("Quantity", qs, key=f"{prefix}_q_{ss.widget_ver}") if qs else c3.text_input("Quantity", key=f"{prefix}_qt_{ss.widget_ver}")
    return kind, obj, q


def _data_tab(cal, model, sol):
    ss = st.session_state
    st.caption("Each **case** is one operating condition of the plant. Add *set* rows for what you impose in that case (feed rate, inlet "
               "temperature, a set-point) and *measure* rows for what was measured (with its uncertainty σ). Mark some cases "
               "*validation*: they are held out of the fit and tell you whether the calibrated model predicts conditions it has not seen. "
               "Values are in the engine units named in the quantity (°C, bar(a), kW …).")
    with st.expander("➕ Add a row", expanded=not cal["rows"]):
        c1, c2, c3 = st.columns([2, 1, 1])
        case = c1.text_input("Case", value="Case 1", key=f"pc_case_{ss.widget_ver}")
        use = c2.selectbox("Use", USES, key=f"pc_use_{ss.widget_ver}")
        row = c3.radio("Row type", ROWS, horizontal=True, key=f"pc_row_{ss.widget_ver}",
                       format_func=lambda r: "Operating point (set)" if r == "set" else "Measurement")
        if row == "set":
            d1, d2, d3 = st.columns([2, 2, 1])
            uname = d1.selectbox("Unit", sorted(u["name"] for u in model["units"].values()), key=f"pc_su_{ss.widget_ver}")
            ut = next((u["type"] for u in model["units"].values() if u["name"] == uname), None)
            keys = [k for k, *_ in TU.float_params(ut)] if ut else []
            key = d2.selectbox("Parameter", keys, key=f"pc_sk_{ss.widget_ver}")
            cur = TU.get_param(model, uname, key) if key else 0.0
            val = d3.number_input("Value", value=float(cur), key=f"pc_sv_{ss.widget_ver}_{uname}_{key}")
            ready, new = bool(key), {"Kind": "unit", "Object": uname, "Quantity": key, "Value": val, "σ": None}
        else:
            kind, obj, q = _pick("pc_m", model, sol)
            e1, e2 = st.columns(2)
            cur = TU.measure(model, sol, {"kind": kind, "obj": obj, "q": q}) if (sol is not None and q) else None
            val = e1.number_input("Measured value", value=float(cur) if cur is not None else 0.0, format="%.6g", key=f"pc_mv_{ss.widget_ver}_{kind}_{obj}_{q}")
            sg = e2.number_input("σ (measurement uncertainty, 0 = 1 % of the value)", value=0.0, min_value=0.0, key=f"pc_ms_{ss.widget_ver}_{kind}_{obj}_{q}")
            ready, new = bool(q), {"Kind": kind, "Object": obj, "Quantity": q, "Value": val, "σ": sg or None}
        if st.button("Add row", key="pc_add", disabled=not ready or not case.strip()):
            cal["rows"].append(dict({"Case": case.strip(), "Use": use, "Row": row}, **new))
            st.rerun()
    cfg = {"Use": st.column_config.SelectboxColumn(options=USES), "Row": st.column_config.SelectboxColumn(options=ROWS),
           "Kind": st.column_config.SelectboxColumn(options=KINDS)}
    ed = st.data_editor(_frame(cal["rows"]), num_rows="dynamic", hide_index=True, width="stretch", column_config=cfg, key=f"pc_tbl_{ss.get('pc_ver', 0)}")
    new_rows = _clean(ed)
    if new_rows != cal["rows"]:
        cal["rows"] = new_rows
    cases = rows_to_cases(cal["rows"])
    n_fit = sum(len(c["measure"]) for c in cases if not c["validate"])
    n_val = sum(len(c["measure"]) for c in cases if c["validate"])
    st.caption(f"{len(cases)} case(s) · {n_fit} measurement(s) in the fit · {n_val} held out for validation")
    a, b, c = st.columns(3)
    a.download_button("Download the data (CSV)", data=_frame(cal["rows"]).to_csv(index=False), file_name="plant_data.csv", mime="text/csv", key="pc_dl")
    tmpl = _frame([{"Case": "Case 1", "Use": "fit", "Row": "set", "Kind": "unit", "Object": next(iter(model["units"].values()))["name"] if model["units"] else "", "Quantity": "", "Value": 0.0, "σ": None},
                   {"Case": "Case 1", "Use": "fit", "Row": "measure", "Kind": "stream", "Object": "", "Quantity": "Temperature [°C]", "Value": 0.0, "σ": 0.5}])
    b.download_button("CSV template", data=tmpl.to_csv(index=False), file_name="plant_data_template.csv", mime="text/csv", key="pc_tpl")
    up = c.file_uploader("Import CSV (adds to the table)", type=["csv"], key=f"pc_up_{ss.get('pc_ver', 0)}")
    if up is not None:
        try:
            df = pd.read_csv(io.BytesIO(up.getvalue()))
            miss = [x for x in COLS if x not in df.columns]
            if miss:
                st.error("Missing columns: " + ", ".join(miss))
            else:
                cal["rows"] += _clean(df[COLS])
                ss["pc_ver"] = ss.get("pc_ver", 0) + 1
                st.rerun()
        except Exception as e:      # noqa: BLE001
            st.error(f"Could not read the file: {e}")


def _fit_tab(cal, model, sol):
    ss = st.session_state
    st.caption("Choose the parameters the data should tune: efficiencies, heat-transfer coefficients, pressure drops, roughness, permeance … "
               "Every numeric unit parameter can be calibrated. Keep the number of parameters small (fewer than the number of independent "
               "measurements); the result tells you which ones the data could not determine.")
    with st.expander("➕ Add a parameter", expanded=not cal["params"]):
        d1, d2, d3, d4 = st.columns([2, 2, 1, 1])
        uname = d1.selectbox("Unit", sorted(u["name"] for u in model["units"].values()), key=f"pcp_u_{ss.widget_ver}")
        ut = next((u["type"] for u in model["units"].values() if u["name"] == uname), None)
        fps = TU.float_params(ut) if ut else []
        key = d2.selectbox("Parameter", [k for k, *_ in fps], key=f"pcp_k_{ss.widget_ver}",
                           format_func=lambda k: next((f"{lab} [{un}]" if un else lab for kk, lab, un, *_ in fps if kk == k), k))
        cur = TU.get_param(model, uname, key) if key else 0.0
        mn, mx = next(((a, b) for kk, _l, _u, a, b in fps if kk == key), (None, None))
        lo = d3.number_input("Lower bound", value=float(mn if mn is not None else (0.5 * cur if cur > 0 else cur - 1.0)), key=f"pcp_lo_{ss.widget_ver}_{uname}_{key}")
        hi = d4.number_input("Upper bound", value=float(mx if mx is not None else (2.0 * cur if cur > 0 else cur + 1.0)), key=f"pcp_hi_{ss.widget_ver}_{uname}_{key}")
        if st.button("Add parameter", key="pcp_add", disabled=not key or hi <= lo or any(p["unit"] == uname and p["key"] == key for p in cal["params"])):
            cal["params"].append({"unit": uname, "key": key, "lo": lo, "hi": hi})
            st.rerun()
    if cal["params"]:
        df = pd.DataFrame([{"Unit": p["unit"], "Parameter": p["key"], "Current": TU.get_param(model, p["unit"], p["key"]) if any(u["name"] == p["unit"] for u in model["units"].values()) else None,
                            "Lower": p["lo"], "Upper": p["hi"]} for p in cal["params"]])
        ed = st.data_editor(df, hide_index=True, width="stretch", num_rows="dynamic", disabled=["Unit", "Parameter", "Current"], key=f"pcp_tbl_{len(cal['params'])}_{ss.widget_ver}")
        newp = [{"unit": r["Unit"], "key": r["Parameter"], "lo": float(r["Lower"]), "hi": float(r["Upper"])} for r in ed.to_dict("records")
                if r.get("Unit") and r.get("Parameter") and pd.notna(r.get("Lower")) and pd.notna(r.get("Upper"))]
        if newp != cal["params"]:
            cal["params"] = newp
    cases = rows_to_cases(cal["rows"])
    fit_cases = [c for c in cases if not c["validate"]]
    n_meas = sum(len(c["measure"]) for c in fit_cases)
    c1, c2 = st.columns(2)
    nfev = c1.slider("Maximum iterations", 5, 60, 25, key="pc_nfev")
    per = (sol.seconds if sol is not None else 1.0) or 1.0
    est = per * max(len(cases), 1) * (len(cal["params"]) + 1) * nfev * 0.6
    c2.caption(f"Each iteration solves the flowsheet {max(len(fit_cases), 1)} × ({len(cal['params'])} + 1) times - up to about "
               f"{est / 60:.1f} min at {per:.1f} s per solve. {n_meas} measurements for {len(cal['params'])} parameters.")
    if st.button("▶ Calibrate", type="primary", key="pc_run", disabled=not cal["params"] or not cases):
        spec = {"params": cal["params"], "cases": cases, "max_nfev": nfev}
        bar = st.progress(0.0, text="Calibrating …")
        try:
            with st.spinner("Solving the flowsheet for every case …"):
                res = TU.calibrate(ss.model, spec, progress=lambda n: None)
            ss["_calib_result"] = res
            bar.progress(1.0, text="Done")
            st.success("Calibration finished - see **3 · Results**.")
        except Exception as e:     # noqa: BLE001
            ss.pop("_calib_result", None)
            st.error(str(e))


def _results_tab(cal, model):
    ss = st.session_state
    res = ss.get("_calib_result")
    if not res:
        st.info("Run a calibration to see the result.")
        return
    m = st.columns(4)
    m[0].metric("RMS residual, fit (σ) before → after", f"{res['rms_z_fit_before']:.2f} → {res['rms_z_fit_after']:.2f}")
    if res["rms_z_val_before"] is not None:
        m[1].metric("RMS residual, validation (σ)", f"{res['rms_z_val_before']:.2f} → {res['rms_z_val_after']:.2f}")
    m[2].metric("Iterations / solves", f"{res['nfev']} / {res['solves']}")
    m[3].metric("Condition number", f"{res['cond']:.1e}" if res["cond"] != float("inf") else "∞")
    for note in res["notes"]:
        st.warning(note)
    if res["rms_z_fit_after"] > 3:
        st.warning("The calibrated model still misses the data by more than 3 σ: a parameter that is not in the fit, a wrong σ, "
                   "or a model assumption (e.g. fluid composition) may be the cause.")
    pt = pd.DataFrame([{"Unit": p["unit"], "Parameter": p["key"], "Before": p["before"], "After": p["after"],
                        "± 95 %": p["ci95"], "Lower": p["lo"], "Upper": p["hi"]} for p in res["params"]])
    st.dataframe(pt.set_index("Unit").map(lambda v: fmt(v) if isinstance(v, float) else v), width="stretch")
    st.plotly_chart(charts.calibration_figure(res), width="stretch", key="pc_z")
    qs = []
    for r in res["rows"]:
        if r["Quantity"] not in qs:
            qs.append(r["Quantity"])
    cols = st.columns(2)
    for i, q in enumerate(qs[:6]):
        if sum(1 for r in res["rows"] if r["Quantity"] == q) >= 2:
            cols[i % 2].plotly_chart(charts.parity_figure(res, q), width="stretch", key=f"pc_par_{i}")
    with st.expander("All residuals"):
        df = pd.DataFrame(res["rows"])
        st.dataframe(df.map(lambda v: fmt(v) if isinstance(v, float) else v), width="stretch")
        st.download_button("Download residuals (CSV)", data=df.to_csv(index=False), file_name="calibration_residuals.csv", mime="text/csv", key="pc_res_dl")
    a, b = st.columns(2)
    if a.button("✅ Apply the calibrated values to the flowsheet", type="primary", key="pc_apply"):
        S.push_edit("apply calibration")
        n = TU.apply_calibration(ss.model, res)
        S.bump()
        ss["_cal_msg"] = f"{n} parameter(s) written to the flowsheet. They are stored with the project (export the flowsheet to keep them)."
        st.rerun()
    if b.button("Discard the result", key="pc_discard"):
        ss.pop("_calib_result", None)
        st.rerun()


def _opt_tab(cal, model, sol):
    ss = st.session_state
    opt = cal["opt"]
    st.caption("Minimise or maximise one result by moving a few unit parameters, within limits. Typical uses: the intermediate pressure of a "
               "compression train (minimum power), a cooler outlet temperature, a split fraction, a chiller temperature, the membrane "
               "area for a CO₂ limit. Run it after calibrating, so it works on the real picture.")
    with st.expander("➕ Add a variable", expanded=not opt["vars"]):
        d1, d2, d3, d4 = st.columns([2, 2, 1, 1])
        uname = d1.selectbox("Unit", sorted(u["name"] for u in model["units"].values()), key=f"opv_u_{ss.widget_ver}")
        ut = next((u["type"] for u in model["units"].values() if u["name"] == uname), None)
        fps = TU.float_params(ut) if ut else []
        key = d2.selectbox("Parameter", [k for k, *_ in fps], key=f"opv_k_{ss.widget_ver}")
        cur = TU.get_param(model, uname, key) if key else 0.0
        lo = d3.number_input("Lower", value=float(0.5 * cur if cur > 0 else cur - 1.0), key=f"opv_lo_{ss.widget_ver}_{uname}_{key}")
        hi = d4.number_input("Upper", value=float(2.0 * cur if cur > 0 else cur + 1.0), key=f"opv_hi_{ss.widget_ver}_{uname}_{key}")
        if st.button("Add variable", key="opv_add", disabled=not key or hi <= lo):
            opt["vars"].append({"unit": uname, "key": key, "lo": lo, "hi": hi})
            st.rerun()
    if opt["vars"]:
        df = pd.DataFrame([{"Unit": v["unit"], "Parameter": v["key"], "Lower": v["lo"], "Upper": v["hi"]} for v in opt["vars"]])
        ed = st.data_editor(df, hide_index=True, width="stretch", num_rows="dynamic", disabled=["Unit", "Parameter"], key=f"opv_tbl_{len(opt['vars'])}_{ss.widget_ver}")
        nv = [{"unit": r["Unit"], "key": r["Parameter"], "lo": float(r["Lower"]), "hi": float(r["Upper"])} for r in ed.to_dict("records") if r.get("Unit") and pd.notna(r.get("Lower")) and pd.notna(r.get("Upper"))]
        if nv != opt["vars"]:
            opt["vars"] = nv
    st.markdown("**Objective**")
    k, o, q = _pick("opo", model, sol)
    sense = st.radio("Direction", ["min", "max"], horizontal=True, key=f"opo_sense_{ss.widget_ver}", format_func=lambda s: "Minimise" if s == "min" else "Maximise")
    objective = {"kind": k, "obj": o, "q": q, "sense": sense}
    with st.expander("➕ Add a limit (constraint)"):
        k2, o2, q2 = _pick("opc", model, sol)
        e1, e2 = st.columns(2)
        op = e1.selectbox("Must be", ["<=", ">="], key=f"opc_op_{ss.widget_ver}")
        val = e2.number_input("Limit", value=0.0, format="%.6g", key=f"opc_val_{ss.widget_ver}")
        if st.button("Add limit", key="opc_add", disabled=not q2):
            opt["constraints"].append({"kind": k2, "obj": o2, "q": q2, "op": op, "value": val})
            st.rerun()
    for i, cns in enumerate(opt["constraints"]):
        a, b = st.columns([6, 1])
        a.markdown(f"- {cns['obj'] or 'economics'} · **{cns['q']}** {cns['op']} {cns['value']:g}")
        if b.button("✖", key=f"opc_del_{i}"):
            opt["constraints"].pop(i)
            st.rerun()
    if st.button("▶ Optimise", type="primary", key="opt_run", disabled=not opt["vars"] or not q):
        try:
            with st.spinner("Optimising - every point is a flowsheet solve …"):
                res = TU.optimise(ss.model, {"vars": opt["vars"], "objective": objective, "constraints": opt["constraints"]})
            ss["_opt_result"] = res
        except Exception as e:      # noqa: BLE001
            ss.pop("_opt_result", None)
            st.error(str(e))
    res = ss.get("_opt_result")
    if res:
        for n_ in res["notes"]:
            st.warning(n_)
        c = st.columns(3)
        c[0].metric("Objective, start", fmt(res["objective_start"]))
        c[1].metric("Objective, best", fmt(res["objective_best"]))
        gain = (res["objective_best"] - res["objective_start"]) / abs(res["objective_start"]) * 100 if res["objective_start"] else 0.0
        c[2].metric("Change", f"{gain:+.1f} %")
        st.dataframe(pd.DataFrame([{"Unit": v["unit"], "Parameter": v["key"], "Start": v["start"], "Best": v["best"]} for v in res["vars"]]).set_index("Unit").map(lambda v: fmt(v) if isinstance(v, float) else v), width="stretch")
        st.plotly_chart(charts.optimiser_figure(res), width="stretch", key="opt_hist")
        if st.button("✅ Apply the optimum to the flowsheet", key="opt_apply"):
            TU.apply_optimum(ss.model, res)
            S.bump()
            ss["_cal_msg"] = f"{len(res['vars'])} variable(s) set to the optimum."
            st.rerun()


def _tornado_tab(model, sol):
    """Which parameters move one result the most (one at a time, +-x %)."""
    import plotly.graph_objects as go
    ss = st.session_state
    st.caption("Moves each chosen parameter by ± the given percentage, one at a time, and shows how much one result changes - the "
               "bars say where to spend effort on data or design. Every parameter costs two flowsheet solves.")
    names = sorted(u["name"] for u in model["units"].values() if TU.float_params(u["type"]))
    cands = []
    for n_ in names:
        ut = next(u["type"] for u in model["units"].values() if u["name"] == n_)
        cands += [(n_, k, f"{n_} · {lab}") for k, lab, *_ in TU.float_params(ut)]
    label = {(a, b): c for a, b, c in cands}
    chosen = st.multiselect("Parameters", list(label), format_func=lambda t: label[t], max_selections=20, key=f"tor_p_{ss.widget_ver}")
    k, o, q = _pick("tor", model, sol)
    d = st.number_input("Change [%]", value=10.0, min_value=0.1, max_value=50.0, key="tor_d")
    if st.button("Run sensitivity", key="tor_go", type="primary", disabled=not (chosen and k and o and q)):
        try:
            with st.spinner("Solving…"):
                ss["_tornado"] = TU.tornado(model, chosen, {"kind": k, "obj": o, "q": q}, d)
        except Exception as e:                       # noqa: BLE001
            st.error(str(e))
    res = ss.get("_tornado")
    if not res:
        return
    rows = res["rows"][::-1]
    base = res["base"]
    fig = go.Figure()
    fig.add_bar(y=[f"{r['unit']} · {r['key']}" for r in rows], x=[(r["low"] - base) if r["low"] is not None else 0 for r in rows],
                orientation="h", name=f"−{res['delta_pct']:g} %", marker_color="#2c7fb8")
    fig.add_bar(y=[f"{r['unit']} · {r['key']}" for r in rows], x=[(r["high"] - base) if r["high"] is not None else 0 for r in rows],
                orientation="h", name=f"+{res['delta_pct']:g} %", marker_color="#d95f0e")
    fig.update_layout(barmode="relative", title=f"Change in {res['ref']['q']} (base {base:,.4g})", height=max(260, 40 * len(rows) + 120),
                      margin=dict(l=10, r=10, t=50, b=10))
    st.plotly_chart(fig, width="stretch", key="tor_fig")
    if res["failed"]:
        st.warning("Not solved at one of the two points: " + ", ".join(res["failed"]) + " (bar shown as zero on that side).")


def calibrate_tab():
    ss = st.session_state
    st.markdown("#### Calibrate to plant data, then optimise")
    msg = ss.pop("_cal_msg", None)
    if msg:
        st.success(msg)
    if not ss.model["units"]:
        st.info("Build or load a flowsheet first.")
        return
    cal = _store()
    sol = ss.sol if sol_is_current() else None
    if sol is None:
        st.info("Solve the flowsheet to pick measured quantities from lists (you can also type them).")
    t1, t2, t3, t4, t5 = st.tabs(["1 · Plant data", "2 · Parameters & fit", "3 · Results", "Optimiser", "Sensitivity"])
    with t1:
        _data_tab(cal, ss.model, sol)
    with t2:
        _fit_tab(cal, ss.model, sol)
    with t3:
        _results_tab(cal, ss.model)
    with t4:
        _opt_tab(cal, ss.model, sol)
    with t5:
        _tornado_tab(ss.model, sol)
