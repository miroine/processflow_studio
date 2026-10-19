"""Prognosis tab: drainage-strategy comparison, uncertainty (P90 / P50 / P10) and the recommended development -
built on the field-life engine (procsim.prognosis)."""
from __future__ import annotations

import hashlib
import json

import pandas as pd
import streamlit as st

from procsim import fieldlife as FL
from procsim import prognosis as PG

from .state import sol_is_current, load_model
from .fieldlife import settings_key as fl_key
from . import charts


def _pset(model):
    return hashlib.sha1(json.dumps(model.get("prognosis") or {}, sort_keys=True, default=str).encode()).hexdigest()[:12]


def _set_range(key, i, wkey):
    ss = st.session_state
    r = ss.model.setdefault("prognosis", {}).setdefault("ranges", {})
    cur = list(r.get(key) or [None, None, None])
    cur[i] = float(ss[wkey])
    r[key] = cur


def _set(key, wkey):
    ss = st.session_state
    ss.model.setdefault("prognosis", {})[key] = ss[wkey]


def _fresh(model, ss, name):
    r = (model.get("prognosis_result") or {}).get(name)
    return r if r and r.get("key") == [ss.sol_hash, fl_key(model)] else None


def _store(model, ss, name, value):
    pr = dict(model.get("prognosis_result") or {})
    pr[name] = dict(value, key=[ss.sol_hash, fl_key(model)], pset=_pset(model))
    model["prognosis_result"] = pr


def _counts(txt):
    try:
        c = sorted({max(1, int(round(float(x)))) for x in txt.replace(";", ",").split(",") if x.strip()})
    except ValueError:
        return None
    return c or None


def basis(S):
    p = S["p"]
    gas = S["kind"] == FL.GAS
    k = st.columns(4)
    k[0].metric("Reservoir", "Gas / condensate" if gas else "Oil")
    k[1].metric("Wells in the flowsheet", f"{S['N']}")
    k[2].metric("GIIP" if gas else "STOIIP",
                f"{S['in_place'] / (1e9 if gas else 1e6):,.1f} {'GSm³' if gas else 'MSm³'}")
    k[3].metric("Subsea boosting", "in the flowsheet" if S["has_boost"] else "none")
    if S["in_place_auto"]:
        st.warning("The in-place volume is the automatic estimate (about 7 plateau years for gas, 5 for oil). Enter "
                   "your own on the Field life tab before reading anything into the recovery factor or the ranges.")


def strategy_panel(model, sol, S):
    ss = st.session_state
    st.markdown("##### 1. Drainage strategy")
    st.caption("Each option is swept over the well counts and kept at its highest-NPV count. Oil: pressure support by "
               "water injection and facility size. Gas: facility size (plateau rate, facility cost scaled with the "
               "0.6 power). With subsea boosting in the flowsheet: when the boosting starts.")
    opts = PG.strategy_options(S)
    c = st.columns([2, 3, 1])
    counts_txt = c[0].text_input("Well counts to compare", value=", ".join(str(n) for n in FL.default_counts(S)),
                                 key="pg_counts")
    pick = c[1].multiselect("Options", [o[0] for o in opts], default=[o[0] for o in opts], key="pg_opts")
    c[2].markdown("<div style='padding-top:28px'></div>", unsafe_allow_html=True)
    if c[2].button("Run strategies", key="pg_run_strat", width="stretch", type="primary"):
        counts = _counts(counts_txt)
        if counts is None or not pick:
            st.error("Enter the well counts as numbers separated by commas and pick at least one option")
        else:
            bar = st.progress(0.0, text="Running the cases (the first run of each well count builds its tables) …")
            try:
                rows, res, best = PG.compare_strategies(
                    model, sol, counts, options=[o for o in opts if o[0] in pick],
                    progress=lambda k, n: bar.progress(k / n, text=f"{k} of {n} well counts done"))
            except (PG.PrognosisError, FL.FieldLifeError) as e:
                bar.empty()
                st.error(str(e))
                return
            bar.empty()
            _store(model, ss, "strategy", PG.compact_strategy(rows, res, best))
            st.rerun()
    st_r = _fresh(model, ss, "strategy")
    if st_r is None:
        if (model.get("prognosis_result") or {}).get("strategy"):
            st.info("The flowsheet or the field-life settings changed since the last run - run again.")
        return None
    st.plotly_chart(charts.strategy_figure(st_r, S["kind"]), width="stretch", key="pg_strat_fig")
    st.dataframe(pd.DataFrame(st_r["rows"]).round(2), hide_index=True, width="stretch")
    return st_r


def uncertainty_panel(model, sol, S):
    ss = st.session_state
    v = ss.widget_ver
    st.markdown("##### 2. Uncertainty")
    st.caption("Latin-hypercube samples with triangular low / base / high distributions. Every sample is run for every "
               "well count and reuses that count's deliverability tables, so a sample costs seconds. Output ranges are "
               "P90 (low case, 10th percentile), P50 and P10 (high case, 90th percentile).")
    saved = (model.get("prognosis") or {}).get("ranges") or {}
    dr = PG.default_ranges(S)
    active = []
    with st.expander("Uncertain inputs", expanded=True):
        for key, (lab, unit) in PG.UNC_LABELS.items():
            lo, base, hi = [saved.get(key, [None] * 3)[i] if saved.get(key) and saved[key][i] is not None else dr[key][i]
                            for i in range(3)]
            c = st.columns([0.8, 1, 1, 1])
            on = c[0].toggle(lab, value=True, key=f"pg_on_{key}_{v}", help=f"Unit: {unit}")
            for i, (col, name, val) in enumerate(zip(c[1:], ("Low", "Base", "High"), (lo, base, hi))):
                wk = f"pg_{key}_{i}_{v}"
                col.number_input(f"{name} [{unit}]", value=float(val), key=wk, format="%.4g", disabled=not on,
                                 on_change=_set_range, args=(key, i, wk))
            if on:
                active.append(key)
    c = st.columns([1, 1, 1, 1])
    n = int(c[0].number_input("Samples per well count", min_value=5, max_value=100,
                              value=int((model.get("prognosis") or {}).get("n", 30)), step=5, key=f"pg_n_{v}",
                              on_change=_set, args=("n", f"pg_n_{v}")))
    counts_txt = c[1].text_input("Well counts", value=", ".join(str(x) for x in FL.default_counts(S)[:5]),
                                 key="pg_unc_counts")
    c[3].markdown("<div style='padding-top:28px'></div>", unsafe_allow_html=True)
    if c[3].button("Run uncertainty", key="pg_run_unc", width="stretch", type="primary"):
        counts = _counts(counts_txt)
        if counts is None or not active:
            st.error("Enter the well counts as numbers separated by commas and keep at least one input uncertain")
        else:
            rng = {k: tuple(float(x) for x in
                            (saved.get(k) if saved.get(k) and None not in saved[k] else dr[k])) for k in PG.UNC_LABELS}
            bar = st.progress(0.0, text="Sampling (the first run of each well count builds its tables) …")
            try:
                unc = PG.uncertainty(model, sol, counts, n=n, ranges=rng, active=active,
                                     progress=lambda k, m: bar.progress(k / m, text=f"{k} of {m} well counts done"))
            except (PG.PrognosisError, FL.FieldLifeError) as e:
                bar.empty()
                st.error(str(e))
                return
            bar.empty()
            _store(model, ss, "unc", unc)
            st.rerun()
    ur = _fresh(model, ss, "unc")
    if ur is None:
        if (model.get("prognosis_result") or {}).get("unc"):
            st.info("The flowsheet or the field-life settings changed since the last run - run again.")
        return None
    if ur.get("pset") != _pset(model):
        st.info("The uncertainty inputs changed since this run - run again to update.")
    st.plotly_chart(charts.uncertainty_figure(dict(ur, kind=S["kind"])), width="stretch", key="pg_unc_fig")
    st.dataframe(pd.DataFrame(ur["rows"]).round(1), hide_index=True, width="stretch")
    st.download_button("Download all samples (CSV)", data=pd.DataFrame(ur["per_run"]).to_csv(index=False),
                       file_name="prognosis_samples.csv", mime="text/csv", key="pg_dl")
    return ur


def summary_panel(model, sol, S, strat, unc):
    st.markdown("##### 3. Prognosis summary")
    best = None
    if strat:
        best = strat["best"]
    for line in PG.recommendation(S, strat, unc):
        st.markdown(line)
    if unc:
        rf = next((r for r in unc["rows"] if r["Wells"] == unc["best"]), None)
        if rf:
            k = st.columns(4)
            k[0].metric("Recommended wells", f"{unc['best']}")
            k[1].metric("Recovery factor (P50)", f"{rf['RF P50 [%]']:.0f} %")
            k[2].metric("RF range (P90-P10)", f"{rf['RF P90 [%]']:.0f}-{rf['RF P10 [%]']:.0f} %")
            k[3].metric("Chance of NPV < 0", f"{rf['P(NPV<0) [%]']:.0f} %")
    N = unc["best"] if unc else (best[1] if best else None)
    if N and st.button(f"Apply {N} wells to the flowsheet", key="pg_apply",
                       help="Sets the identical-well counts and feed rates so the total is the plateau rate"):
        m2 = FL.apply_wells(model, sol, N)
        m2.setdefault("fieldlife", {})["n_wells"] = N
        m2.pop("fieldlife_result", None)
        m2.pop("prognosis_result", None)
        load_model(m2, fit=False)
        st.rerun()


def prognosis_tab():
    ss = st.session_state
    model = ss.model
    sol = ss.sol if sol_is_current() else None
    st.markdown("#### Prognosis — drainage strategy, recovery factor and number of wells under uncertainty")
    st.caption("Builds on the Field life model (EOS tank, flowsheet deliverability, plateau / water / booster limits, "
               "economics): compares drainage options, samples the uncertain inputs and recommends the strategy, the "
               "number of wells and the recovery factor with its range. Screening accuracy; prices and costs are "
               "illustrative placeholders; set the reservoir and economics on the Field life tab.")
    if sol is None:
        st.info("Solve the flowsheet to plan the prognosis.")
        return
    try:
        S = FL.setup(model, sol)
    except FL.FieldLifeError as e:
        st.info(f"{e}. Load a *Subsea (SURF)* example to try the prognosis.")
        return
    basis(S)
    strat = strategy_panel(model, sol, S)
    st.markdown("---")
    unc = uncertainty_panel(model, sol, S)
    st.markdown("---")
    summary_panel(model, sol, S, strat, unc)
