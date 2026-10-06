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

def fluid_tab():
    ss = st.session_state
    fl = ss.model["fluid"]
    st.markdown("#### Fluid package — Peng-Robinson (1978) with Peneloux volume shift")
    st.caption("Vapour–liquid–aqueous equilibrium from the PR EOS (free-water start + stability test), "
               "ideal-gas Cp polynomials, enthalpy/entropy departures from the EOS.")
    hypos = fl.get("hypos", {})
    all_keys = list(LIBRARY.keys()) + [k for k in hypos if k not in LIBRARY]
    names = {k: (LIBRARY[k].name if k in LIBRARY else hypos[k]["name"]) for k in all_keys}
    sel = st.multiselect("Components", all_keys, default=[k for k in fl["components"] if k in all_keys],
                         format_func=lambda k: f"{k} — {names[k]}", key=f"fl_comps_{ss.widget_ver}")
    if sel != fl["components"]:
        if not sel:
            st.warning("At least one component is needed.")
        else:
            fl["components"] = sel
            st.rerun()
    from procsim.transport import HYDRATE_MODELS, MOTIEE
    cur = fl.get("hydrate_model", MOTIEE)
    hm = st.selectbox("Hydrate model", list(HYDRATE_MODELS), index=list(HYDRATE_MODELS).index(cur) if cur in HYDRATE_MODELS
                      else 0, key=f"fl_hydmodel_{ss.widget_ver}",
                      help="Motiee: gas-gravity correlation (fast, 3.5–280 bar). van der Waals–Platteeuw: statistical "
                           "model of sI and sII hydrates with Kihara cell potentials and PR fugacities, composition-"
                           "specific (CO₂, H₂S, N₂ and propane effects), 2–600 bar. Inhibitors (MEG, methanol) are "
                           "applied as a temperature depression in both.")
    if hm != cur:
        fl["hydrate_model"] = hm
        st.rerun()
    fp = None
    try:
        fp = build_fluid(ss.model)
    except Exception as e:
        st.error(str(e))
    t1, t2, t3 = st.tabs(["Component properties", "Binary interaction (kij)", "Hypothetical components"])
    with t1:
        if fp:
            rows = [{"Key": c.key, "Name": c.name, "MW": c.MW, "Tc [°C]": c.Tc - 273.15, "Pc [bar]": c.Pc,
                     "ω": c.omega, "Std liq density [kg/m³]": c.rho_std, "Type": c.family} for c in fp.comps]
            st.dataframe(pd.DataFrame(rows).set_index("Key").map(lambda v: fmt(v) if not isinstance(v, str) else v),
                         width="stretch")
    with t2:
        if fp:
            st.caption("Defaults: water–HC 0.50, CO₂–HC 0.10–0.12, N₂–C₁ 0.025, H₂S–HC 0.07–0.08. Edit the upper "
                       "triangle; the matrix is kept symmetric.")
            df = pd.DataFrame(fp.kij, index=fp.keys, columns=fp.keys)
            ed = st.data_editor(df, key=f"kij_{ss.widget_ver}_{'-'.join(fp.keys)}", width="stretch")
            arr = ed.to_numpy(float)
            if not np.allclose(arr, fp.kij):
                # take the edited cell, symmetrise
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
    with t3:
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
            if not k or k in LIBRARY:
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
