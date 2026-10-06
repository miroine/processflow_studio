"""Field life tab: production profile over time, recovery factor, drainage strategy, well count and boosting
timing, and the field economics - built on the solved flowsheet (procsim.fieldlife)."""
from __future__ import annotations

import hashlib
import json

import pandas as pd
import streamlit as st

from procsim import fieldlife as FL

from .state import sol_is_current, fmt, load_model
from . import charts
from . import units as U


def _set(key, wkey, scale=1.0):
    ss = st.session_state
    v = ss[wkey]
    ss.model.setdefault("fieldlife", {})[key] = (v * scale) if isinstance(v, (int, float)) and not isinstance(v, bool) else v


def settings_key(model):
    return hashlib.sha1(json.dumps(model.get("fieldlife") or {}, sort_keys=True, default=str).encode()).hexdigest()[:12]


def _num(col, key, value, label, unit_lbl="", scale=1.0, **kw):
    ss = st.session_state
    wk = f"fl_{key}_{ss.widget_ver}"
    lab = label + (f" [{unit_lbl}]" if unit_lbl else "")
    col.number_input(lab, value=float(value), key=wk, format="%.4g", on_change=_set, args=(key, wk, scale), **kw)


def basis_panel(model, S):
    ss = st.session_state
    p = S["p"]
    v = ss.widget_ver
    kind = S["kind"]
    gas = kind == FL.GAS
    with st.expander("Reservoir and drive", expanded=False):
        c = st.columns(4)
        wk = f"fl_kind_{v}"
        c[0].selectbox("Reservoir fluid", list(FL.KINDS), index=list(FL.KINDS).index(p["kind"]) if p["kind"] in FL.KINDS else 0,
                       key=wk, on_change=_set, args=("kind", wk),
                       help="Auto: gas if any well uses the gas back-pressure inflow model, otherwise oil")
        _num(c[1], "in_place", S["in_place"] / (1e9 if gas else 1e6), "GIIP" if gas else "STOIIP",
             "GSm³" if gas else "MSm³", min_value=0.0,
             help="Gas initially in place (separator gas at standard conditions) / stock-tank oil initially in place. "
                  "The default is an automatic estimate - enter your own")
        _num(c[2], "aquifer", p["aquifer"], "Pot aquifer (× pore volume)", min_value=0.0,
             help="Aquifer water volume as a multiple of the reservoir pore volume; influx = W·(cw + cf)·(pi − p)")
        if not gas:
            _num(c[3], "VRR", p["VRR"], "Water injection (voidage replacement)", min_value=0.0, max_value=2.0,
                 help="Reservoir volume of water injected per reservoir volume produced (0 = no injection, "
                      "1 = full pressure support)")
        c = st.columns(4)
        _num(c[0], "Swc", p["Swc"], "Connate water saturation", min_value=0.0, max_value=0.8)
        _num(c[1], "cf", p["cf"] * 1e5, "Pore compressibility", "1e-5/bar", 1e-5, min_value=0.0)
        _num(c[2], "cw", p["cw"] * 1e5, "Water compressibility", "1e-5/bar", 1e-5, min_value=0.0)
        _num(c[3], "Slc", p["Slc"], "Critical liquid saturation", min_value=0.0, max_value=0.9,
             help="Condensate (gas fields) or oil saturation below which the liquid does not flow (Corey)")
        c = st.columns(4)
        _num(c[0], "Sgc", p["Sgc"], "Critical gas saturation", min_value=0.0, max_value=0.9)
        _num(c[1], "ng", p["ng"], "Corey exponent, gas", min_value=0.5, max_value=6.0)
        _num(c[2], "nl", p["nl"], "Corey exponent, liquid", min_value=0.5, max_value=6.0)
        st.caption(f"Initial reservoir pressure {U.P(S['Pi']):.0f} {U.uP()} at {U.T(S['T'] - 273.15):.0f} {U.uT()} "
                   f"(rate-weighted over the well feeds). Water production: "
                   + (f"WGR {S['w0']:.1f} Sm³/MSm³ now." if gas else f"water cut {100 * S['w0']:.1f} % now."))
    with st.expander("Water production", expanded=False):
        c = st.columns(3)
        _num(c[0], "RF_bt", p["RF_bt"], "Breakthrough at recovery factor", min_value=0.0, max_value=1.0,
             help="Fraction of the in-place volume produced when water from the aquifer or the injectors arrives "
                  "(1 = never)")
        _num(c[1], "RF_rise", p["RF_rise"], "Rise to the maximum over recovery", min_value=0.01, max_value=1.0)
        _num(c[2], "w_max", p["w_max"], "Maximum WGR" if gas else "Maximum water cut", "Sm³/MSm³" if gas else "%",
             min_value=0.0)
    with st.expander("Facility, wells and boosting", expanded=True):
        c = st.columns(4)
        _num(c[0], "q_plat", U.value("MSm³/d", S["q_plat"]) if gas else U.value("Sm³/d", S["q_plat"], "Std liq"),
             "Plateau (facility) rate", U.unit("MSm³/d") if gas else U.unit("Sm³/d", "Std liq"),
             1.0 / (U.value("MSm³/d", 1.0) if gas else U.value("Sm³/d", 1.0, "Std liq")), min_value=0.0,
             help="Gas or oil handling capacity of the host; the default is the current flowsheet rate")
        _num(c[1], "P_min", U.P(S["P_min"]), "Minimum delivery pressure", U.uP(), 1.0 / U.P(1.0) if U.P(1.0) else 1.0,
             min_value=0.0, help="The rate falls when the delivery stream can no longer arrive above this pressure "
                                 "(topside inlet / compression suction). Default: half of the current arrival pressure")
        names = {sid: s["name"] for sid, s in model["streams"].items()}
        ids = sorted(names, key=lambda k: names[k])
        wk = f"fl_deliv_{v}"
        c[2].selectbox("Delivery stream", ids, index=ids.index(S["deliv"]) if S["deliv"] in ids else 0,
                       format_func=lambda k: names[k], key=wk, on_change=_set, args=("deliv", wk))
        _num(c[3], "uptime", p["uptime"], "Production efficiency", "%", min_value=1.0, max_value=100.0)
        c = st.columns(4)
        _num(c[0], "n_wells", S["N"], "Producing wells", min_value=1.0, max_value=80.0, step=1.0,
             help="Spread over the well units in proportion to their 'Identical wells' setting")
        _num(c[1], "rig", p["rig"], "Drilling rate (0 = all ready)", "wells/y", min_value=0.0)
        _num(c[2], "water_cap", U.value("Sm³/d", p["water_cap"], "Std liq"), "Water capacity (0 = none)",
             U.unit("Sm³/d", "Std liq"), 1.0 / U.value("Sm³/d", 1.0, "Std liq"), min_value=0.0)
        _num(c[3], "years", p["years"], "Maximum field life", "years", min_value=1.0, max_value=60.0)
        if S["has_boost"]:
            c = st.columns(4)
            wk = f"fl_boost_{v}"
            c[0].selectbox("Subsea boosting", list(FL.BOOST_MODES),
                           index=list(FL.BOOST_MODES).index(p["boost_mode"]) if p["boost_mode"] in FL.BOOST_MODES else 0,
                           key=wk, on_change=_set, args=("boost_mode", wk))
            _num(c[1], "boost_year", p["boost_year"], "Boosting starts in year", min_value=1.0, max_value=60.0, step=1.0)
            wk = f"fl_rating_{v}"
            c[2].toggle("Respect booster power rating", value=bool(p["rating"]), key=wk, on_change=_set,
                        args=("rating", wk))
    with st.expander("Economics", expanded=False):
        c = st.columns(3)
        _num(c[0], "gas_price", p["gas_price"], "Gas price", "USD/Sm³", min_value=0.0)
        _num(c[1], "oil_price", p["oil_price"], "Oil / condensate price", "USD/bbl", min_value=0.0)
        _num(c[2], "fx", p["fx"], "Energy-cost currency per USD", min_value=1e-6,
             help="The energy and CO₂ costs come from the Analysis tab's economics settings in their currency")
        c = st.columns(3)
        _num(c[0], "opex_pct", p["opex_pct"], "Fixed OPEX", "% of CAPEX per year", min_value=0.0)
        _num(c[1], "disc", p["disc"], "Discount rate", "%/year", min_value=0.0)
        _num(c[2], "capex_years", p["capex_years"], "Construction years", min_value=0.0, max_value=6.0, step=1.0)
        st.caption("CAPEX comes from the SURF equipment list (cost basis on the SURF tab): wells and trees scale with "
                   "the well count, extra templates are added when the wells exceed the slots, and boosting is paid the "
                   "year before it starts when it is timed. All prices are illustrative placeholders.")
        if st.button("Reset field-life settings", key=f"fl_reset_{v}"):
            model.pop("fieldlife", None)
            ss.widget_ver += 1
            st.rerun()


def _fresh(model, ss):
    fr = model.get("fieldlife_result")
    return fr if fr and fr.get("key") == [ss.sol_hash, settings_key(model)] else None


def results_panel(model, fl):
    sm = fl["summary"]
    gas = fl["kind"] == FL.GAS
    k = st.columns(5)
    k[0].metric("Recovery factor", f"{sm['Recovery factor [%]']:.1f} %")
    k[1].metric("Plateau", f"{sm['Plateau length [years]']:.1f} years")
    k[2].metric("Production years", f"{sm['Production years']}")
    k[3].metric("NPV", f"{sm['NPV [MUSD]']:,.0f} MUSD")
    k[4].metric("IRR", "—" if sm.get("IRR [%]") is None else f"{sm['IRR [%]']:.0f} %")
    for line in fl.get("strategy", []):
        st.markdown(line)
    st.plotly_chart(charts.fieldlife_figure(fl), width="stretch", key="fl_prof")
    if fl.get("events"):
        st.markdown("**Events**")
        for t, e in fl["events"]:
            st.markdown(f"- {e}")
    st.plotly_chart(charts.cashflow_figure(fl), width="stretch", key="fl_cash")
    df = U.df_display(pd.DataFrame(fl["annual"]))
    with st.expander("Year by year", expanded=False):
        st.dataframe(df.round(3), hide_index=True, width="stretch")
    st.download_button("Download profile (CSV)", data=df.to_csv(index=False), file_name="field_life_profile.csv",
                       mime="text/csv", key="fl_dl")
    rows = [{"Quantity": a, "Value": fmt(b) if not isinstance(b, str) else b} for a, b in sm.items()]
    rows += [{"Quantity": a, "Value": fmt(b)} for a, b in (fl.get("capex") or {}).items()]
    with st.expander("Summary table", expanded=False):
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption(f"{fl.get('n_solves', 0)} flowsheet solves. One EOS tank for all wells; the network's lift tables use "
               "the initial reservoir fluid (GOR / CGR changes alter the volumes, not the hydraulics); quarterly steps.")


def sweep_panel(model, sol, S):
    ss = st.session_state
    st.markdown("##### Well count and boosting")
    c = st.columns([2, 2, 1])
    counts_txt = c[0].text_input("Well counts to compare", value=", ".join(str(n) for n in FL.default_counts(S)),
                                 key="fl_counts")
    modes = [S["p"]["boost_mode"] if S["has_boost"] else FL.B_AS_IS]
    if S["has_boost"]:
        modes = c[1].multiselect("Boosting options", list(FL.BOOST_MODES), default=[S["p"]["boost_mode"]],
                                 key="fl_modes")
    c[2].markdown("<div style='padding-top:28px'></div>", unsafe_allow_html=True)
    run = c[2].button("Run comparison", key="fl_sweep", width="stretch")
    if run:
        try:
            counts = sorted({max(1, int(round(float(x)))) for x in counts_txt.replace(";", ",").split(",") if x.strip()})
        except ValueError:
            st.error("Enter the well counts as numbers separated by commas")
            return
        if not counts or not modes:
            st.error("Choose at least one well count and one boosting option")
            return
        bar = st.progress(0.0, text="Running the cases …")
        n_cases = len(counts) * len(modes)
        try:
            rows, results, best = FL.sweep(model, sol, counts, modes,
                                           case_progress=lambda k, n: bar.progress(k / n, text=f"{k} of {n} cases done"))
        except FL.FieldLifeError as e:
            bar.empty()
            st.error(str(e))
            return
        bar.empty()
        r_best = results[best]
        model["fieldlife_result"] = dict(FL.compact(r_best, FL.setup(model, sol), rows, best),
                                         key=[ss.sol_hash, settings_key(model)])
        st.toast(f"{n_cases} cases run")
        st.rerun()
    fl = _fresh(model, ss)
    if fl and fl.get("sweep"):
        rows = fl["sweep"]
        best = tuple(fl["best"]) if fl.get("best") else None
        st.plotly_chart(charts.wells_sweep_figure(rows, best), width="stretch", key="fl_sweep_fig")
        st.dataframe(pd.DataFrame(rows).round(2), hide_index=True, width="stretch")
        if best:
            st.success(f"Highest NPV: **{best[0]} wells** ({best[1].lower()}). The profile above shows this case.")
            if st.button(f"Apply {best[0]} wells to the flowsheet", key="fl_apply",
                         help="Sets the identical-well counts and the feed rates so that the total is the plateau rate"):
                m2 = FL.apply_wells(model, sol, best[0])
                m2.setdefault("fieldlife", {})["n_wells"] = best[0]
                m2.pop("fieldlife_result", None)
                load_model(m2, fit=False)
                st.rerun()


def fieldlife_tab():
    ss = st.session_state
    model = ss.model
    sol = ss.sol if sol_is_current() else None
    st.markdown("#### Field life — production profile, recovery factor, wells and boosting")
    st.caption("Marches the solved flowsheet through the life of the field: an EOS tank model depletes the reservoir "
               "(rock and water compressibility, aquifer, water injection), the flowsheet is solved with the chokes "
               "fully open to find the rate the wells and network can deliver to the minimum delivery pressure, and "
               "the plateau, water handling and booster rating limit it. Gives the drainage strategy, the recovery "
               "factor at the economic limit, the well count with the highest NPV and the boosting start.")
    if sol is None:
        st.info("Solve the flowsheet to plan the field life.")
        return
    try:
        S = FL.setup(model, sol)
    except FL.FieldLifeError as e:
        st.info(f"{e}. Load a *Subsea (SURF)* example to try the field-life planner.")
        return
    basis_panel(model, S)
    c = st.columns([1, 3])
    if c[0].button("Run field life", key="fl_run", type="primary", width="stretch"):
        bar = st.progress(0.0, text="Building the deliverability tables …")
        try:
            res = FL.run(model, sol, S=S,
                         progress=lambda n: bar.progress(min(0.98, n / 40.0), text=f"{n} flowsheet solves …"))
        except FL.FieldLifeError as e:
            bar.empty()
            st.error(str(e))
            return
        bar.empty()
        model["fieldlife_result"] = dict(FL.compact(res, S), key=[ss.sol_hash, settings_key(model)])
        st.rerun()
    fl = _fresh(model, ss)
    if fl is None:
        c[1].caption("A run takes a minute or two (each point of the deliverability tables is a full flowsheet solve; "
                     "only the reservoir pressures the run reaches are solved). Results are stored with the flowsheet.")
        if model.get("fieldlife_result"):
            st.info("The flowsheet or the settings changed since the last run - run again to update the profile.")
    else:
        results_panel(model, fl)
    st.markdown("---")
    sweep_panel(model, sol, S)
