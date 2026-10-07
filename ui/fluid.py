"""Fluid-package manager and workbook (stream/unit tables, exports)."""
from __future__ import annotations

import io

import numpy as np
import pandas as pd
import streamlit as st

from procsim.components import LIBRARY, make_hypothetical, Component
from procsim.flowsheet import build_fluid
from procsim.streams import stream_properties, PHASE_NAMES
from procsim.unitops import CATALOGUE

from .state import fmt, sol_is_current, valid_choice
from . import units as U


# ------------------------------------------------------------------ fluid

FAMILY_LABEL = {"hydrocarbon": "Hydrocarbons", "inert": "Inerts / light gases", "acid": "Acid gases",
                "water": "Water", "alcohol": "Alcohols", "glycol": "Glycols", "amine": "Amines", "hypo": "Hypothetical"}


def _library_frame():
    rows = [{"Key": k, "Name": c.name, "Formula": c.formula, "CAS": c.cas, "Family": c.family, "MW": c.MW,
             "Tc [°C]": c.Tc - 273.15, "Pc [bar]": c.Pc, "ω": c.omega} for k, c in LIBRARY.items()]
    return pd.DataFrame(rows)


def fluid_tab():
    ss = st.session_state
    fl = ss.model["fluid"]
    st.markdown("#### Fluid package — Peng-Robinson (1978) with Peneloux volume shift")
    st.caption(f"Vapour–liquid–aqueous equilibrium from the PR EOS (free-water start + stability test), "
               f"ideal-gas Cp polynomials, enthalpy/entropy departures from the EOS. Library: {len(LIBRARY)} components "
               f"plus any number of hypothetical / plus-fraction pseudo-components; any component or pair can be "
               f"calibrated to data in the *EOS calibration* tab.")
    hypos = fl.get("hypos", {})
    all_keys = list(LIBRARY.keys()) + [k for k in hypos if k not in LIBRARY]
    fam_of = {k: (LIBRARY[k].family if k in LIBRARY else "hypo") for k in all_keys}
    names = {k: (LIBRARY[k].name if k in LIBRARY else hypos[k]["name"]) for k in all_keys}
    cal = set((fl.get("overrides") or {})) | {k for k, d in hypos.items() if d.get("m_pr") or d.get("vshift")}
    f1, f2 = st.columns([1, 2])
    fams = f1.multiselect("Filter by family", [f for f in FAMILY_LABEL if f in set(fam_of.values())],
                          format_func=lambda f: FAMILY_LABEL[f], key=f"fl_fam_{ss.widget_ver}")
    q = f2.text_input("Search (name, key, formula or CAS)", key=f"fl_q_{ss.widget_ver}").strip().lower()

    def match(k):
        if fams and fam_of[k] not in fams:
            return False
        if not q:
            return True
        c = LIBRARY.get(k)
        hay = f"{k} {names[k]} {c.formula if c else ''} {c.cas if c else ''}".lower()
        return q in hay
    cur = [k for k in fl["components"] if k in all_keys]
    options = [k for k in all_keys if match(k) or k in cur]
    sel = st.multiselect("Components", options, default=cur, key=f"fl_comps_{ss.widget_ver}_{len(options)}",
                         format_func=lambda k: f"{k} — {names[k]}" + (" ✎" if k in cal else ""),
                         help="✎ marks a component with calibrated EOS parameters. Use the filter / search to find one "
                              f"of the {len(LIBRARY)} library components.")
    if set(sel) != set(cur):
        if not sel:
            st.warning("At least one component is needed.")
        else:
            order = [k for k in fl["components"] if k in sel] + [k for k in sel if k not in fl["components"]]
            fl["components"] = order
            st.rerun()
    from procsim.transport import HYDRATE_MODELS, MOTIEE
    curh = fl.get("hydrate_model", MOTIEE)
    hm = st.selectbox("Hydrate model", list(HYDRATE_MODELS), index=list(HYDRATE_MODELS).index(curh)
                      if curh in HYDRATE_MODELS else 0, key=f"fl_hydmodel_{ss.widget_ver}",
                      help="Motiee: gas-gravity correlation (fast, 3.5–280 bar). van der Waals–Platteeuw: statistical "
                           "model of sI and sII hydrates with Kihara cell potentials and PR fugacities, composition-"
                           "specific (CO₂, H₂S, N₂ and propane effects), 2–600 bar. Inhibitors (MEG, methanol) are "
                           "applied as a temperature depression in both.")
    if hm != curh:
        fl["hydrate_model"] = hm
        st.rerun()
    fp = None
    try:
        fp = build_fluid(ss.model)
    except Exception as e:
        st.error(str(e))
    t1, t2, t3, t4, t5, t6 = st.tabs(["Component properties", "Library browser", "Binary interaction (kij)",
                                      "Hypothetical components", "Plus fraction", "EOS calibration"])
    with t1:
        if fp:
            rows = [{"Key": c.key, "Name": c.name, "MW": c.MW, "Tc [°C]": c.Tc - 273.15, "Pc [bar]": c.Pc,
                     "ω": c.omega, "m (alpha)": float(fp.m[i]), "Peneloux shift [cm³/mol]": float(fp.c_shift[i]) * 1e6,
                     "Std liq density [kg/m³]": c.rho_std, "Type": c.family,
                     "Calibrated": "yes" if c.key in cal else ""} for i, c in enumerate(fp.comps)]
            st.dataframe(pd.DataFrame(rows).set_index("Key").map(lambda v: fmt(v) if not isinstance(v, str) else v),
                         width="stretch")
    with t2:
        lib = _library_frame()
        c1, c2 = st.columns([1, 2])
        ff = c1.selectbox("Family", ["All"] + sorted(lib.Family.unique()), key="lib_fam")
        qq = c2.text_input("Search", key="lib_q").strip().lower()
        v = lib if ff == "All" else lib[lib.Family == ff]
        if qq:
            m = v.apply(lambda r: qq in " ".join(str(x) for x in (r.Key, r.Name, r.Formula, r.CAS)).lower(), axis=1)
            v = v[m]
        st.dataframe(v.set_index("Key").map(lambda x: fmt(x) if not isinstance(x, str) else x), width="stretch",
                     height=420)
        st.caption(f"{len(v)} of {len(lib)} components. Critical properties, acentric factors and ideal-gas heat "
                   "capacities of the extended set come from the ChemSep pure-component database (Artistic License 2.0). "
                   "Select components in the list above; add petroleum fractions in the next tabs.")
    with t3:
        if fp:
            st.caption("Defaults: water–HC 0.50, CO₂–HC 0.10–0.12, N₂–C₁ 0.025, H₂S–HC 0.07–0.08, plus generic rules for "
                       "alcohols, glycols and amines. Edit the upper triangle; the matrix is kept symmetric. "
                       "Fit a pair to bubble-pressure data in *EOS calibration*.")
            df = pd.DataFrame(fp.kij, index=fp.keys, columns=fp.keys)
            ed = st.data_editor(df, key=f"kij_{ss.widget_ver}_{'-'.join(fp.keys)}", width="stretch")
            arr = ed.to_numpy(float)
            if not np.allclose(arr, fp.kij):
                diff = np.argwhere(~np.isclose(arr, fp.kij))
                kij = fl.setdefault("kij", {})
                for i, j in diff:
                    if i == j:
                        continue
                    a, b = fp.keys[i], fp.keys[j]
                    kij[f"{a}|{b}"] = float(arr[i, j])
                    kij.pop(f"{b}|{a}", None)
                ss.widget_ver += 1
                st.rerun()
            if fl.get("kij") and st.button("Reset kij to defaults"):
                fl["kij"] = {}
                ss.widget_ver += 1
                st.rerun()
    with t4:
        st.caption("Petroleum fractions from normal boiling point and specific gravity — Kesler–Lee (1976) Tc, "
                   "Pc, ω; Riazi–Daubert (1980) molecular weight if not given; ideal-gas Cp from the n-paraffin "
                   "series (per unit mass) scaled by MW.")
        c1, c2, c3, c4, c5 = st.columns(5)
        key = c1.text_input("Key", value="C7+*", key="hy_key")
        tb = c2.number_input("NBP [°C]", value=180.0, key="hy_tb")
        sg = c3.number_input("SG (60/60 °F)", value=0.78, min_value=0.5, max_value=1.2, key="hy_sg")
        mw = c4.number_input("MW (0 = estimate)", value=0.0, min_value=0.0, key="hy_mw")
        c5.markdown("<div style='padding-top:28px'></div>", unsafe_allow_html=True)
        if c5.button("Add", width="stretch"):
            k = key.strip()
            if not k or k in LIBRARY or "|" in k:
                st.error("Choose a new, unique key.")
            else:
                comp = make_hypothetical(k, f"Hypo {k} (NBP {tb:g} °C, SG {sg:g})", tb, sg, mw or None)
                fl.setdefault("hypos", {})[k] = comp.to_dict()
                if k not in fl["components"]:
                    fl["components"].append(k)
                ss.widget_ver += 1
                st.rerun()
        if hypos:
            rows = []
            for k, d in hypos.items():
                c = Component.from_dict(d)
                rows.append({"Key": k, "NBP [°C]": d.get("hypo", {}).get("NBP_C"), "SG": d.get("hypo", {}).get("SG"),
                             "MW": c.MW, "Tc [°C]": c.Tc - 273.15, "Pc [bar]": c.Pc, "ω": c.omega})
            st.dataframe(pd.DataFrame(rows).set_index("Key").map(fmt), width="stretch")
            valid_choice("hy_rm", ["—"] + list(hypos.keys()))
            rm = st.selectbox("Remove hypothetical", ["—"] + list(hypos.keys()), key="hy_rm")
            if rm != "—" and st.button("Remove"):
                hypos.pop(rm)
                if rm in fl["components"]:
                    fl["components"].remove(rm)
                st.rerun()
    with t5:
        _plus_fraction_panel(fl)
    with t6:
        _calibration_panel(fl, fp)


def _plus_fraction_panel(fl):
    from procsim import plusfraction as PF
    ss = st.session_state
    st.caption("Split a measured plus fraction (e.g. C7+) into single carbon numbers with a Pedersen exponential "
               "distribution and Søreide densities, then lump into a few equal-mass pseudo-components. Mole fraction, "
               "molar mass and density of the plus fraction are conserved. The pseudo-components are added to the "
               "fluid as hypothetical components; their mole fractions are shown so they can be entered in a feed.")
    c1, c2, c3, c4, c5 = st.columns(5)
    n0 = c1.number_input("First carbon number", min_value=6, max_value=12, value=7, key="pf_n0")
    zp = c2.number_input("Plus-fraction mole fraction", min_value=0.0001, max_value=1.0, value=0.10, format="%.4f", key="pf_z")
    mwp = c3.number_input("Plus-fraction MW [kg/kmol]", min_value=90.0, max_value=900.0, value=215.0, key="pf_mw")
    sgp = c4.number_input("Plus-fraction SG (60/60 °F)", min_value=0.6, max_value=1.1, value=0.84, key="pf_sg")
    ng = c5.number_input("Pseudo-components", min_value=1, max_value=8, value=4, key="pf_ng")
    pref = st.text_input("Key prefix", value="C", key="pf_pref")
    try:
        res = PF.split(zp, mwp, sgp, int(n0), 80, int(ng), pref or "C")
    except Exception as e:
        st.error(str(e))
        return
    g = pd.DataFrame(res["groups"])[["key", "n_lo", "n_hi", "z", "MW", "SG", "Tb_C"]]
    g.columns = ["Key", "From Cn", "To Cn", "Mole fraction (of plus)", "MW", "SG", "NBP [°C]"]
    g["Mole fraction (of plus)"] = g["Mole fraction (of plus)"] / zp
    st.dataframe(g.set_index("Key").map(fmt), width="stretch")
    ck = res["checks"]
    st.caption(f"Check: Σz = {ck['z']:.4f}, MW = {ck['MW']:.1f}, SG = {ck['SG']:.3f} (targets {zp:.4f}, {mwp:g}, {sgp:g}).")
    if st.button("Add pseudo-components to the fluid", key="pf_add"):
        bad = [x["key"] for x in res["groups"] if x["key"] in LIBRARY]
        if bad:
            st.error("Key clash with the library; change the prefix.")
            return
        for x in res["groups"]:
            comp = make_hypothetical(x["key"], f"{x['name']} (NBP {x['Tb_C']:.0f} °C, SG {x['SG']:.3f})", x["Tb_C"],
                                     x["SG"], x["MW"])
            fl.setdefault("hypos", {})[x["key"]] = comp.to_dict()
            if x["key"] not in fl["components"]:
                fl["components"].append(x["key"])
        ss.pf_last = {x["key"]: x["z"] for x in res["groups"]}
        ss.widget_ver += 1
        st.rerun()
    if ss.get("pf_last"):
        st.info("Added. Mole fractions to use in a feed (sum = plus fraction): " +
                ", ".join(f"{k} {v:.4f}" for k, v in ss.pf_last.items()))


def _calibration_panel(fl, fp):
    from procsim import eoscal as EC
    ss = st.session_state
    if fp is None:
        return
    st.caption("Tune the Peng-Robinson parameters of one component to measured vapour pressures and liquid densities, "
               "or fit the kij of a pair to bubble pressures. Weak priors keep the parameters close to their starting "
               "values when data are few. The fit is shown before it is applied.")
    mode = st.radio("What to calibrate", ["Pure component", "Binary kij"], horizontal=True, key="cal_mode")
    if mode == "Pure component":
        key = st.selectbox("Component", fp.keys, key="cal_comp")
        base = EC.base_component(fl, key)
        p0 = EC.effective_params(base)
        params = st.multiselect("Parameters to fit", list(EC.PURE_PARAMS), default=["m_pr", "vshift"],
                                format_func=lambda k: f"{EC.PURE_PARAMS[k]}  (now {p0[k]:.4g})", key="cal_params")
        st.markdown("**Measured data** — `psat`: T and vapour pressure in *value* [bar]; `rho_l`: T, pressure `P_bar` "
                    "and liquid density in *value* [kg/m³]. Paste or type your data; the template starts from the "
                    "current model so you can overwrite it.")
        dkey = f"cal_data_{key}"
        if dkey not in ss:
            ss[dkey] = pd.DataFrame({"kind": ["psat"] * 5 + ["rho_l"] * 3,
                                     "T_C": [float("nan")] * 8, "P_bar": [float("nan")] * 8,
                                     "value": [float("nan")] * 8})
        up = st.file_uploader("…or upload a CSV (columns kind, T_C, P_bar, value)", type="csv", key=f"cal_up_{key}")
        cA, cB = st.columns(2)
        if cA.button("Fill template from current model", key="cal_tpl"):
            single = EC._single(base)
            Tc = base.Tc
            rows = []
            for f in (0.55, 0.65, 0.75, 0.85, 0.92):
                T = f * Tc
                ps = single.psat_pure(0, T)
                if ps:
                    rows.append({"kind": "psat", "T_C": T - 273.15, "P_bar": float("nan"), "value": ps})
            for f in (0.5, 0.6, 0.7):
                T = f * Tc
                ps = single.psat_pure(0, T) or 1.0
                rows.append({"kind": "rho_l", "T_C": T - 273.15, "P_bar": max(ps * 1.5, 1.0),
                             "value": EC.rho_liquid(single, 0, T, max(ps * 1.5, 1.0))})
            ss[dkey] = pd.DataFrame(rows)
            ss.pop(f"cal_ed_{key}", None)
            st.rerun()
        if up is not None:
            try:
                ss[dkey] = pd.read_csv(up)
            except Exception as e:
                st.error(f"Could not read the CSV: {e}")
        ed = st.data_editor(ss[dkey], num_rows="dynamic", width="stretch", key=f"cal_ed_{key}",
                            column_config={"kind": st.column_config.SelectboxColumn(options=["psat", "rho_l"])})
        if cB.button("Run calibration", type="primary", key="cal_run"):
            with st.spinner("Fitting…"):
                ss.cal_result = {"mode": "pure", "key": key,
                                 "res": EC.calibrate_pure(base, ed.to_dict("records"), tuple(params))}
        r = ss.get("cal_result")
        if r and r["mode"] == "pure" and r["key"] == key:
            _show_pure_result(r["res"], key, fl)
        if (fl.get("overrides") or {}).get(key) or (fl.get("hypos", {}).get(key, {}).get("m_pr")):
            if st.button("Reset calibrated parameters of this component", key="cal_reset"):
                if key in (fl.get("overrides") or {}):
                    fl["overrides"].pop(key)
                else:
                    fl["hypos"][key].update({"m_pr": 0.0, "vshift": 0.0, "note": ""})
                ss.pop("cal_result", None)
                ss.widget_ver += 1
                st.rerun()
    else:
        if fp.n < 2:
            st.info("Add a second component first.")
            return
        c1, c2 = st.columns(2)
        a = c1.selectbox("Component A", fp.keys, index=0, key="cal_a")
        b = c2.selectbox("Component B", [k for k in fp.keys if k != a], key="cal_b")
        ca, cb = EC.base_component(fl, a), EC.base_component(fl, b)
        cur = float(fp.kij[fp.index(a), fp.index(b)])
        st.markdown(f"**Bubble-pressure data** — liquid mole fraction of A (`x1`), T and pressure. Current kij = {cur:.4f}.")
        dkey = f"cal_bdata_{a}_{b}"
        if dkey not in ss:
            ss[dkey] = pd.DataFrame({"T_C": [float("nan")] * 4, "x1": [float("nan")] * 4, "P_bar": [float("nan")] * 4})
        ed = st.data_editor(ss[dkey], num_rows="dynamic", width="stretch", key=f"cal_bed_{a}_{b}")
        if st.button("Run calibration", type="primary", key="cal_runb"):
            with st.spinner("Fitting…"):
                ss.cal_result = {"mode": "bin", "key": (a, b),
                                 "res": EC.calibrate_kij(ca, cb, ed.to_dict("records"), kij0=cur)}
        r = ss.get("cal_result")
        if r and r["mode"] == "bin" and r["key"] == (a, b):
            res = r["res"]
            if not res["ok"]:
                st.error(res["message"])
            else:
                st.success(f"kij {res['kij0']:.4f} → {res['kij1']:.4f};  bubble-pressure AAD "
                           f"{res['before']['pb']['AAD%']:.2f} % → {res['after']['pb']['AAD%']:.2f} %  ({res['n']} points)")
                st.dataframe(pd.DataFrame(res["table"]).map(fmt), width="stretch", hide_index=True)
                if st.button(f"Apply kij {a}–{b} = {res['kij1']:.4f}", key="cal_applyb"):
                    EC.apply_kij(fl, a, b, res["kij1"])
                    ss.pop("cal_result", None)
                    ss.widget_ver += 1
                    st.rerun()


def _show_pure_result(res, key, fl):
    import plotly.graph_objects as go
    from procsim import eoscal as EC
    ss = st.session_state
    if not res["ok"]:
        st.error(res["message"] or "The fit failed.")
        return
    if res.get("message"):
        st.caption(res["message"])
    parts = []
    for k, lab in (("psat", "vapour pressure"), ("rho_l", "liquid density")):
        if k in res["before"]:
            parts.append(f"{lab} AAD {res['before'][k]['AAD%']:.2f} % → {res['after'][k]['AAD%']:.2f} %")
    st.success(f"{key}: " + ";  ".join(parts) + f"  ({res['n']} points)")
    pr = pd.DataFrame({"Parameter": [EC.PURE_PARAMS[k] for k in EC.PURE_PARAMS],
                       "Before": [res["params0"][k] for k in EC.PURE_PARAMS],
                       "After": [res["params1"][k] for k in EC.PURE_PARAMS]})
    st.dataframe(pr.set_index("Parameter").map(lambda v: f"{v:.5g}"), width="stretch")
    tb = pd.DataFrame(res["table"])
    fig = go.Figure()
    for kind, sym in (("psat", "circle"), ("rho_l", "square")):
        d = tb[tb.kind == kind]
        if d.empty:
            continue
        fig.add_scatter(x=d["T_C"], y=d["before_dev%"], mode="markers", name=f"{kind} before",
                        marker=dict(symbol=sym + "-open", size=9))
        fig.add_scatter(x=d["T_C"], y=d["after_dev%"], mode="markers", name=f"{kind} after",
                        marker=dict(symbol=sym, size=8))
    fig.add_hline(y=0, line_dash="dot", line_color="gray")
    fig.update_layout(height=320, margin=dict(l=40, r=10, t=30, b=40), xaxis_title="T [°C]",
                      yaxis_title="deviation, calc vs measured [%]", title="Deviation before / after calibration")
    st.plotly_chart(fig, width="stretch")
    st.dataframe(tb.set_index("kind").map(fmt), width="stretch")
    if st.button("Apply to the fluid package", key="cal_apply"):
        EC.apply_pure(fl, key, res["override"])
        ss.pop("cal_result", None)
        ss.widget_ver += 1
        st.rerun()
    st.caption("A correlated fit (m with Tc / Pc / ω) can reproduce the data and still extrapolate badly; keep to "
               "m and the volume shift unless you have data spanning the range of interest.")


# ------------------------------------------------------------------ workbook

def stream_table(model, sol):
    cols = {}
    for sid, s in model["streams"].items():
        st_ = sol.streams.get(sid)
        if st_ is None:
            continue
        props = U.convert_dict(stream_properties(st_, sol.fp))
        cols[s["name"]] = props
    if not cols:
        return pd.DataFrame()
    return pd.DataFrame(cols)


def composition_matrix(model, sol, phase=None):
    cols = {}
    for sid, s in model["streams"].items():
        st_ = sol.streams.get(sid)
        if st_ is None or st_.z is None:
            continue
        if phase is None:
            cols[s["name"]] = st_.z
        else:
            ph = st_.flash.phase(phase) if not st_.empty else None
            cols[s["name"]] = ph.x if ph is not None else np.full(sol.fp.n, np.nan)
    return pd.DataFrame(cols, index=sol.fp.keys)


def unit_table(model, sol):
    rows = []
    for uid, u in model["units"].items():
        if u["type"] in ("feed", "product"):
            continue
        res = sol.results.get(uid) or {}
        key = ", ".join(f"{k2} = {fmt(v2)}" for k2, v2 in (U.kv(k, v) for k, v in list(res.items())[:4]))
        rows.append({"Name": u["name"], "Type": CATALOGUE[u["type"]]["label"], "Status": sol.status.get(uid, "—"),
                     "Key results": key, "Message": sol.errors.get(uid, res.get("Warning", ""))})
    return pd.DataFrame(rows)


def energy_table(sol):
    """Energy streams; in field units work is in hp and heat in MMBtu/h, so both are listed."""
    rows = []
    for e in sol.energy:
        rows.append({"Energy stream": e.name, "Unit": e.unit, "Kind": e.kind, "Duty [kW]": e.duty_kW,
                     f"Duty [{U.unit('kW', power=(e.kind == 'work'))}]":
                         U.value("kW", e.duty_kW, power=(e.kind == "work"))})
    df = pd.DataFrame(rows)
    if not U.field() and not df.empty:
        df = df.loc[:, ~df.columns.duplicated()]
    return df


def workbook_tab():
    ss = st.session_state
    if not sol_is_current():
        st.info("Solve the flowsheet to fill the workbook.")
        return
    sol, model = ss.sol, ss.model
    t1, t2, t3, t4 = st.tabs(["Material streams", "Compositions", "Unit operations", "Energy streams"])
    df = stream_table(model, sol)
    with t1:
        if df.empty:
            st.caption("No streams.")
        else:
            st.dataframe(df.map(fmt), width="stretch", height=min(40 + 35 * len(df), 700))
    with t2:
        ph = st.radio("Phase", ["Overall", "Vapour", "Liquid", "Aqueous"], horizontal=True, key="wb_phase")
        code = {"Overall": None, "Vapour": "V", "Liquid": "L", "Aqueous": "W"}[ph]
        cm = composition_matrix(model, sol, code)
        st.dataframe(cm.map(lambda v: fmt(v)), width="stretch")
        st.caption("Mole fractions.")
    with t3:
        st.dataframe(unit_table(model, sol), hide_index=True, width="stretch")
    with t4:
        et = energy_table(sol)
        if not et.empty:
            st.dataframe(et, hide_index=True, width="stretch",
                         column_config={"Duty [kW]": st.column_config.NumberColumn(format="%.1f")})
            w = et[et.Kind == "work"]["Duty [kW]"].sum()
            q = et[et.Kind == "heat"]["Duty [kW]"]
            c1, c2, c3 = st.columns(3)
            c1.metric("Net shaft work in", f"{U.value('kW', w, power=True):,.1f} {U.unit('kW', power=True)}")
            c2.metric("Heat added", f"{U.value('kW', q[q > 0].sum(), power=False):,.1f} {U.unit('kW', power=False)}")
            c3.metric("Heat removed", f"{U.value('kW', -q[q < 0].sum(), power=False):,.1f} "
                                      f"{U.unit('kW', power=False)}")
            st.caption("Sign convention: positive duty flows into the process, negative out of it.")
        else:
            st.caption("No energy streams.")
    st.markdown("---")
    c1, c2 = st.columns(2)
    with c1:
        st.download_button("Download workbook (Excel)", data=excel_bytes(model, sol),
                           file_name="processflow_workbook.xlsx", width="stretch",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    with c2:
        from .report import build_report
        title = st.text_input("Report title", value="Process simulation report", key="rep_title")
        st.download_button("Download printable report (HTML → print to PDF)",
                           data=build_report(model, sol, ss.get("svg"), title), file_name="processflow_report.html",
                           mime="text/html", width="stretch")
        if not ss.get("svg"):
            st.caption("Tip: press **Export SVG** on the diagram toolbar first to include the PFD in the report.")


def excel_bytes(model, sol):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        stream_table(model, sol).to_excel(xw, sheet_name="Streams")
        composition_matrix(model, sol).to_excel(xw, sheet_name="Composition (mole)")
        for code, nm in (("V", "Vapour"), ("L", "Liquid"), ("W", "Aqueous")):
            composition_matrix(model, sol, code).to_excel(xw, sheet_name=f"{nm} composition")
        unit_table(model, sol).to_excel(xw, sheet_name="Unit operations", index=False)
        energy_table(sol).to_excel(xw, sheet_name="Energy streams", index=False)
        if sol.recycle_log:
            pd.DataFrame(sol.recycle_log).to_excel(xw, sheet_name="Recycle log", index=False)
    return buf.getvalue()
