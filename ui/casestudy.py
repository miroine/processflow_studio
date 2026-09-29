"""Case study (HYSYS 'Case Study' / sensitivity): sweep one specification, record results."""
from __future__ import annotations

import copy

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from procsim.flowsheet import solve, build_fluid
from procsim.streams import stream_properties
from procsim.unitops import CATALOGUE

from .state import sol_is_current, fmt
from . import charts
from . import units as U

STREAM_PROPS = ["Temperature [°C]", "Pressure [bar(a)]", "Vapour fraction", "Molar flow [kmol/h]",
                "Mass flow [kg/h]", "Std gas flow [MSm³/d]", "Std liq vol flow [m³/h]", "Actual vol flow [m³/h]",
                "Mass density [kg/m³]", "Molecular weight", "Hydrate margin [°C]", "Viscosity vapour [cP]"]


def dependent_options(model, sol):
    opts = []
    for sid, s in model["streams"].items():
        if sid in sol.streams and not sol.streams[sid].empty:
            for p in STREAM_PROPS:
                opts.append(("stream", s["name"], p))
    for uid, u in model["units"].items():
        for k, v in (sol.results.get(uid) or {}).items():
            if isinstance(v, (int, float, np.floating)) and not isinstance(v, bool):
                opts.append(("unit", u["name"], k))
    for k in ("Energy + CO₂ cost [cur/y]", "Energy cost [cur/y]", "CO₂ emissions [t/y]", "CO₂ intensity [kg/boe]",
              "Cost per boe [cur/boe]", "Fuel gas [Sm³/h]", "Power demand [kW]"):
        opts.append(("econ", "Economics", k))
    return opts


def _label(o):
    return f"{o[1]} · {o[2]}"


def evaluate(model, sol, o):
    if o[0] == "econ":
        from procsim.economics import compute
        return compute(model, sol)["totals"].get(o[2])
    if o[0] == "stream":
        st_ = sol.stream_by_name(model, o[1])
        return None if st_ is None else stream_properties(st_, sol.fp).get(o[2])
    uid = next((k for k, u in model["units"].items() if u["name"] == o[1]), None)
    v = (sol.results.get(uid) or {}).get(o[2]) if uid else None
    return v if isinstance(v, (int, float, np.floating)) else None


def run_case(model, var_uid, var_key, values, deps, progress=None):
    fp = build_fluid(model)
    rows = []
    for i, x in enumerate(values):
        m = copy.deepcopy(model)
        m["units"][var_uid]["params"][var_key] = float(x)
        row = {"x": float(x), "Converged": False, "Message": ""}
        try:
            s = solve(m, fp)
            bad = [m["units"][k]["name"] for k, v in s.status.items() if v in ("error", "missing", "unsolved")]
            row["Converged"] = s.converged and not bad
            row["Message"] = ("; ".join(f"{m['units'][k]['name']}: {s.errors[k]}" for k in s.errors
                                        if s.status.get(k) in ("error", "unsolved")))[:300]
            for o in deps:
                row[_label(o)] = evaluate(m, s, o)
        except Exception as e:   # noqa: BLE001 - a failed case must not stop the sweep
            row["Message"] = f"{type(e).__name__}: {e}"
        rows.append(row)
        if progress is not None:
            progress.progress((i + 1) / len(values), text=f"Case {i + 1} of {len(values)}")
    return pd.DataFrame(rows)


def case_study_tab():
    ss = st.session_state
    st.markdown("#### Case study — sweep one specification and record the results")
    if not sol_is_current():
        st.info("Solve the flowsheet first: the list of result variables comes from the current solution.")
        return
    model, sol = ss.model, ss.sol
    units = {k: u for k, u in model["units"].items() if u["type"] not in ("product", "adjust", "recycle")}
    names = {u["name"]: k for k, u in units.items()}
    if not names:
        st.caption("No unit operations to vary.")
        return
    # widget keys carry a signature of the flowsheet so stale selections never meet new options
    sig = str(abs(hash(tuple(sorted(names)) + tuple(s_["name"] for s_ in model["streams"].values()))) % 10 ** 8)
    ss["cs_sig"] = sig
    c1, c2 = st.columns(2)
    uname = c1.selectbox("Independent variable — object", list(names.keys()), key=f"cs_obj_{sig}")
    uid = names[uname]
    specs = [s for s in CATALOGUE[units[uid]["type"]]["params"] if s["kind"] == "float"]
    if not specs:
        st.caption("This object has no numeric specification.")
        return
    from .panels import param_unit, DELTA_PARAMS
    params = units[uid]["params"]
    si_unit = {s["key"]: param_unit(s, params) for s in specs}
    labels = {s["key"]: s["label"] + (f" [{si_unit[s['key']]}]" if si_unit[s["key"]] else "") for s in specs}
    key = c2.selectbox("Specification", list(labels.keys()), format_func=lambda k: U.key(labels[k]),
                       key=f"cs_key_{uid}")
    cur = float(params.get(key, 0.0))
    su, dl = si_unit[key], key in DELTA_PARAMS
    d1, d2, d3 = st.columns(3)
    sysk = U.system()[:2]
    lo = U.to_si(su, d1.number_input("From", value=float(U.value(su, cur * 0.8 if cur else 0.0, labels[key], dl)),
                                     key=f"cs_lo_{uid}_{key}_{sysk}", format="%.6g"), labels[key], dl)
    hi = U.to_si(su, d2.number_input("To", value=float(U.value(su, cur * 1.2 if cur else 1.0, labels[key], dl)),
                                     key=f"cs_hi_{uid}_{key}_{sysk}", format="%.6g"), labels[key], dl)
    n = d3.number_input("Points", value=8, min_value=2, max_value=40, step=1, key="cs_n")
    opts = dependent_options(model, sol)
    default = [o for o in opts if o[0] == "unit" and ("Power" in o[2] or "Duty" in o[2])][:3] or opts[:2]
    deps = st.multiselect("Results to record", opts, default=default, format_func=lambda o: U.key(_label(o)),
                          key=f"cs_deps_{sig}_{ss.sol_hash[:8]}")
    est = max(sol.seconds, 0.2) * int(n)
    go_ = st.button(f"Run case study (≈ {est:.0f} s)", type="primary", key="cs_run")
    if go_:
        if not deps:
            st.warning("Pick at least one result to record.")
        else:
            values = np.linspace(lo, hi, int(n))
            prog = st.progress(0.0, text="Starting…")
            df = run_case(model, uid, key, values, deps, prog)
            ss.case = {"df": df, "xlabel": f"{uname} · {labels[key]}", "deps": [_label(o) for o in deps]}
    case = ss.get("case")
    if not case:
        st.caption("The base flowsheet is not changed by a case study; each case is solved on a copy.")
        return
    df = U.df_display(case["df"].rename(columns={"x": case["xlabel"]}))
    ren = {c: U.key(c) for c in [case["xlabel"]] + case["deps"]}
    ok = df["Converged"].sum()
    st.caption(f"{ok} of {len(df)} cases converged.")
    cols = st.columns(2)
    for i, dep in enumerate(case["deps"]):
        dep = ren[dep]
        if dep not in df.columns:
            continue
        fig = charts.case_figure(df, ren[case["xlabel"]], dep)
        with cols[i % 2]:
            st.plotly_chart(fig, width="stretch", key=f"cs_fig_{i}")
    st.dataframe(df.map(lambda v: fmt(v) if isinstance(v, (float, np.floating)) else v), hide_index=True,
                 width="stretch")
    st.download_button("Download case study (CSV)", data=df.to_csv(index=False), file_name="case_study.csv",
                       mime="text/csv")
