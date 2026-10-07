"""Data tab (v7.4): import parameters / compositions / profile / events from CSV, Excel and YAML with a checked change
plan, batch edits, export of results to Excel / CSV / YAML, and a restricted Python editor for adjusting the tables before
export (procsim.datatools)."""
from __future__ import annotations

import hmac
import os
import json

import pandas as pd
import streamlit as st

from procsim import datatools as D
from procsim.unitops import CATALOGUE

from . import state as S
from .state import sol_is_current, valid_choice

BIG = 200_000                      # cells; above this the Excel / ZIP file is prepared on request instead of on every rerun
SAMPLE = ("# 'tables' is a dict of DataFrames - the same tables as on the Export tab.\n"
          "# Change them in place, replace them, add new ones or drop some; print() shows below.\n"
          "if 'Unit results' in tables:\n"
          "    df = tables['Unit results']\n"
          "    df = df[df['Quantity'].str.contains('Power')]\n"
          "    print(len(df), 'power rows')\n"
          "    tables['Power only'] = df\n"
          "else:\n"
          "    print('No unit results yet - solve a flowsheet first. Tables:', list(tables))\n")


def data_tab():
    ss = st.session_state
    st.markdown("#### Data - import, batch edit and export")
    st.caption("Bring parameters, feed compositions, profile tables and dynamic events in from CSV, Excel or YAML (every change is "
               "listed with its old and new value and checked before anything is written; **Undo edit** works as usual), edit many "
               "units at once, and export the tables to Excel, CSV or YAML - after adjusting them in a Python editor if you wish. "
               "Values are in the units shown next to each parameter (bar(a), °C, kW …), whatever display units you use.")
    msg = ss.pop("_data_msg", None)
    if msg:
        st.success(msg)
    t_imp, t_batch, t_exp, t_py = st.tabs(["⬆️ Import", "✏️ Batch edit", "⬇️ Export", "🐍 Python editor"])
    with t_imp:
        _import_tab()
    with t_batch:
        _batch_tab()
    with t_exp:
        _export_tab()
    with t_py:
        _python_tab()


# ================================================================================================== shared pieces
def _frozen(name, make, extra=()):
    """Editors are fed a frozen frame (changes only with the flowsheet or the given tag) and edits are read from the editor's
    output, because feeding them back would apply them twice."""
    ss = st.session_state
    tag = (ss.get("sol_hash"), ss.get("dat_ver", 0)) + tuple(extra)
    k = f"_datbase_{name}"
    if ss.get(k, (None,))[0] != tag:
        ss[k] = (tag, make())
    return ss[k][1], f"dat_{name}_{abs(hash(tag)) % 10**8}"


def _safe(df):
    """A copy of a frame that Streamlit's Arrow serialisation accepts: object columns that mix numbers and text become text."""
    out = df.copy()
    for c in out.columns:
        if out[c].dtype == object:
            kinds = {("n" if isinstance(v, (int, float)) and not isinstance(v, bool) else "s") for v in out[c] if v is not None and not (isinstance(v, float) and v != v)}
            if len(kinds) > 1 or any(isinstance(v, (list, dict, bool)) for v in out[c]):
                out[c] = out[c].map(lambda v: "" if v is None or (isinstance(v, float) and v != v) else str(v))
    return out


def _plan_panel(pl, key, label):
    """Show a change plan and the apply button.  Returns True when something was applied (the page reruns)."""
    ss = st.session_state
    cnt = D.plan_summary(pl)
    k = st.columns(5)
    k[0].metric("To change", cnt["ok"])
    k[1].metric("With a warning", cnt["warning"])
    k[2].metric("Unchanged", cnt["unchanged"])
    k[3].metric("Skipped (blank)", cnt["skipped"])
    k[4].metric("Errors", cnt["error"])
    df = D.plan_frame(pl)
    show_all = st.toggle("Show unchanged and skipped rows", key=f"{key}_all")
    shown = df if show_all else df[df["Status"].isin(["ok", "warning", "error"])]
    if len(shown):
        st.dataframe(_safe(shown), hide_index=True, width="stretch", height=min(60 + 35 * len(shown), 520))
    elif not cnt["error"]:
        st.caption("Nothing to change - every value in the file equals the flowsheet value.")
    n = cnt["ok"] + cnt["warning"]
    allow = True
    if cnt["error"]:
        st.error(f"{cnt['error']} row(s) cannot be applied (see the Message column).")
        allow = st.toggle("Apply the valid rows and leave the rows with errors out", key=f"{key}_force")
    if cnt["warning"]:
        st.caption("Rows with a warning are applied; the message says what to check.")
    if st.button(f"Apply {n} change{'s' if n != 1 else ''}", key=f"{key}_apply", type="primary", disabled=(n == 0 or not allow)):
        S.push_edit(label)
        done = D.apply_plan(ss.model, pl)
        ss.widget_ver += 1
        ss["dat_ver"] = ss.get("dat_ver", 0) + 1
        ss["_data_msg"] = f"{done} value{'s' if done != 1 else ''} changed ({label}). Use *Undo edit* on the Flowsheet tab to go back."
        st.rerun()
        return True
    return False


def _tables():
    """The export tables of the flowsheet as they are now."""
    ss = st.session_state
    sol = ss.sol if sol_is_current() else None
    prof = (ss.get("prof_result") or {}).get("res")
    dyn = (ss.get("dyn_result") or {}).get("res")
    return D.result_tables(ss.model, sol, profile=prof, dynamic=dyn)


# ================================================================================================== import
def _templates(model):
    from procsim import timeseries as TS
    t = D.OrderedDict()
    t["Parameters"] = D.parameter_table(model)
    t["Feed compositions"] = D.composition_table(model)
    rows = (model.get("profile") or {}).get("rows")
    if rows:
        t["Profile"] = pd.DataFrame(rows)
    else:
        fc = TS.feed_columns(model)
        if fc:
            t["Profile"] = pd.DataFrame([dict({"Time": 0.0}, **TS.base_row(model, fc))])
    ev = D.events_table(model)
    if not len(ev):
        ev = pd.DataFrame([{"Time [s]": 60.0, "Event": "Feed rate", "Target": "", "Value": None, "Ramp [s]": 0.0}])
    t["Events"] = ev
    return t


def _import_tab():
    ss = st.session_state
    model = ss.model
    if not model["units"]:
        st.info("The flowsheet is empty - load an example or add units first. (A complete flowsheet in YAML can still be opened below.)")
    with st.expander("Which layouts are accepted, and templates", expanded=False):
        st.markdown(
            "**CSV or Excel** (an Excel workbook is read sheet by sheet; each sheet is recognised from its columns):\n"
            "- *Parameters, one row per parameter*: columns **Unit, Parameter, Value** (the **Parameters** sheet of an export).\n"
            "- *Parameters, one row per unit*: first column **Unit** (or Name), then one column per parameter - key such as `P_out`, or "
            "its label such as `Outlet pressure`; a trailing ` [unit]` is ignored; a blank cell leaves the value alone.\n"
            "- *Feed compositions*: **Feed, Component, Fraction** (long), or **Feed** and one column per component (wide).\n"
            "- *Profile table*: **Time** and `Unit | parameter` columns, as on the Profile tab.\n"
            "- *Dynamic events*: **Time [s], Event, Target, Value, Ramp [s]**.\n\n"
            "**YAML**: `units:` → `Name:` → `parameter: value` (a feed may have `composition: {C1: 0.8, …}`), optionally `profile:` "
            "(list of rows) and `events:` (list of `{t, kind, target, value, ramp}`); or a complete flowsheet file.\n\n"
            "Decimal commas, semicolon or tab delimiters and ` [unit]` suffixes in headers are accepted. Select parameters accept the option text "
            "in any capital letters.")
        tm = _templates(model)
        c = st.columns(3)
        c[0].download_button("Template workbook (Excel)", D.to_excel_bytes(tm, "ProcessFlow Studio import template"),
                             file_name="import_template.xlsx", key="dat_tpl_xl", width="stretch",
                             mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        c[1].download_button("Parameters (CSV)", D.to_csv_text(tm["Parameters"]), file_name="parameters.csv", mime="text/csv",
                             key="dat_tpl_csv", width="stretch")
        try:
            c[2].download_button("Parameters (YAML)", D.parameters_to_yaml(model), file_name="parameters.yaml", mime="text/yaml",
                                 key="dat_tpl_yaml", width="stretch")
        except ImportError:
            c[2].caption("YAML needs the PyYAML package.")
    up = st.file_uploader("File to import (CSV, Excel, YAML or a complete flowsheet)", type=["csv", "tsv", "txt", "xlsx", "xlsm", "yaml", "yml", "json"],
                          key="dat_up")
    if up is None:
        st.caption("Choose a file to see the changes it would make. Nothing is written until you press Apply.")
        return
    data = up.getvalue()
    # a sheet's recognised layout can be overridden
    kinds = ss.get("dat_kinds", {}) if ss.get("dat_kinds_for") == (up.name, up.size) else {}
    try:
        rd = D.read_file(up.name, data, model, kinds)
    except D.DataError as e:
        st.error(f"{up.name}: {e}")
        return
    if rd["sheets"]:
        st.markdown("##### Sheets")
        new_kinds = {}
        for i, (nm, kind, why) in enumerate(rd["sheets"]):
            c = st.columns([2, 3, 4])
            c[0].markdown(f"**{nm}**")
            opts = ["Auto"] + list(D.KINDS) + ["Ignore"]
            cur = kinds.get(nm, "Auto")
            sel = c[1].selectbox("Treat as", opts, index=opts.index(cur) if cur in opts else 0, key=f"dat_kind_{i}", label_visibility="collapsed")
            if sel != "Auto":
                new_kinds[nm] = sel
            c[2].caption((kind or "not recognised - ignored") + f" - {why}" if kind else f"not recognised - {why}")
        if new_kinds != kinds:
            ss["dat_kinds"], ss["dat_kinds_for"] = new_kinds, (up.name, up.size)
            st.rerun()
    for n_ in rd["notes"]:
        if n_.startswith("sheet"):
            st.caption(n_)
    ex = rd["extras"]
    # ---- a whole flowsheet
    if "flowsheet" in ex:
        st.markdown("##### Complete flowsheet")
        try:
            fs = D.check_flowsheet(ex["flowsheet"])
            st.write(f"{len(fs['units'])} units, {len(fs['streams'])} streams.")
            if st.toggle("Replace the current flowsheet with this one", key="dat_fs_ok"):
                if st.button("Open flowsheet", key="dat_fs_open", type="primary"):
                    S.load_model(fs)
                    ss["_data_msg"] = f"Opened {up.name} ({len(fs['units'])} units)."
                    st.rerun()
        except D.DataError as e:
            st.error(str(e))
    # ---- parameters / compositions
    if rd["requests"]:
        st.markdown("##### Parameters and compositions")
        try:
            pl = D.plan(model, rd["requests"])
        except D.DataError as e:
            st.error(str(e))
            pl = []
        if pl:
            _plan_panel(pl, "dat_imp", f"Import {up.name}")
    # ---- profile
    if "profile_rows" in ex:
        st.markdown("##### Profile table")
        try:
            ps = D.profile_settings(model, ex["profile_rows"])
            st.write(f"{len(ps['rows'])} time steps, columns: {', '.join(c for c in ps['rows'][0] if c != 'Time')}")
            st.caption("The times are used in the Profile tab's time unit.")
            if st.button("Use as the Profile table", key="dat_prof_apply"):
                p = model.setdefault("profile", {})
                p.update(rows=ps["rows"], feeds=ps["feeds"], vars=ps["vars"], extra=ps["extra"])
                for k_ in ("prof_feeds", "prof_vars", "prof_extra"):
                    ss.pop(k_, None)
                ss["prof_ver"] = ss.get("prof_ver", 0) + 1
                ss["_data_msg"] = f"Profile table loaded ({len(ps['rows'])} steps) - see the Profile tab."
                st.rerun()
        except D.DataError as e:
            st.error(str(e))
    # ---- events
    if "events" in ex:
        st.markdown("##### Dynamic events")
        evs = ex["events"]
        st.dataframe(_safe(pd.DataFrame(evs)), hide_index=True, width="stretch")
        mode = st.radio("What to do with the existing events", ["Replace them", "Add to them"], horizontal=True, key="dat_ev_mode")
        if st.button("Load the events", key="dat_ev_apply"):
            D.apply_events(model, evs, replace=(mode == "Replace them"))
            ss["dyn_ver"] = ss.get("dyn_ver", 0) + 1
            ss["_data_msg"] = f"{len(evs)} event(s) loaded - see the Dynamic tab (targets are checked there)."
            st.rerun()


# ================================================================================================== batch edit
def _batch_tab():
    ss = st.session_state
    model = ss.model
    if not model["units"]:
        st.info("The flowsheet is empty.")
        return
    mode = st.radio("Edit", ["One operation on many units", "A table of one unit type", "Feed compositions"], horizontal=True, key="dat_bmode")
    if mode == "One operation on many units":
        _batch_op(model)
    elif mode == "A table of one unit type":
        _batch_table(model)
    else:
        _batch_comp(model)


def _batch_op(model):
    types = D.types_in_model(model)
    lab = {t: CATALOGUE[t]["label"] for t in types}
    valid_choice("dat_btypes", types, multi=True)
    c = st.columns([3, 3])
    sel_t = c[0].multiselect("Unit types (none = all)", types, format_func=lab.get, key="dat_btypes")
    pat = c[1].text_input("Name contains / matches", value="", key="dat_bpat",
                          help="Part of a name, or wildcards (* and ?). Several patterns separated by ; or ,   e.g.  K-*; Cooler")
    uids = D.select_units(model, sel_t, pat)
    st.caption(f"{len(uids)} unit(s) selected: " + (", ".join(model["units"][u]["name"] for u in uids[:12]) + (" …" if len(uids) > 12 else "") if uids else "none"))
    use_t = sel_t or sorted({model["units"][u]["type"] for u in uids})
    plist = D.params_of_types(use_t)
    if not plist:
        return
    keys = [""] + list(plist)
    valid_choice("dat_bparam", keys)

    def fmt(k):
        if not k:
            return "Choose a parameter…"
        unit = next((p.get("unit") for t in use_t for p in CATALOGUE[t]["params"] if p["key"] == k), "")
        return f"{plist[k]}  ({k}{', ' + unit if unit else ''})"[:110]
    c = st.columns([4, 2, 3])
    key = c[0].selectbox("Parameter", keys, format_func=fmt, key="dat_bparam")
    op = c[1].selectbox("Operation", D.OPS, key="dat_bop")
    if not key:
        st.caption("Choose a parameter and an operation to preview the changes.")
        return
    spec = next((p for t in use_t for p in CATALOGUE[t]["params"] if p["key"] == key), {})
    val = None
    if op in ("Set to", "Multiply by", "Add"):
        if op == "Set to" and spec.get("kind") == "select":
            opts = [""] + list(spec["options"])
            valid_choice("dat_bval_s", opts)
            val = c[2].selectbox("Value", opts, format_func=lambda o: o or "Choose…", key="dat_bval_s")
        else:
            val = c[2].text_input("Value" if op == "Set to" else "Factor" if op == "Multiply by" else "Amount", value="", key="dat_bval")
    if not uids:
        return
    if op != "Reset to default" and (val is None or str(val).strip() == ""):
        st.caption("Enter a value to preview the change.")
        return
    try:
        pl = D.plan_batch(model, uids, key, op, val)
    except D.DataError as e:
        st.error(str(e))
        return
    _plan_panel(pl, "dat_bop_plan", f"Batch: {op.lower()} {key} on {len(uids)} unit(s)")


def _batch_table(model):
    types = D.types_in_model(model)
    lab = {t: CATALOGUE[t]["label"] for t in types}
    valid_choice("dat_ttype", types)
    t = st.selectbox("Unit type", types, format_func=lambda x: f"{lab[x]} ({sum(u['type'] == x for u in model['units'].values())})", key="dat_ttype")
    base, key = _frozen("tbl", lambda: D.wide_parameters(model, t), extra=(t,))
    cfg = {}
    for p in CATALOGUE[t]["params"]:
        if p["key"] not in base.columns:
            continue
        head = f"{p['key']} [{p['unit']}]" if p.get("unit") else p["key"]
        if p.get("kind") == "select":
            cfg[p["key"]] = st.column_config.SelectboxColumn(head, options=list(p["options"]), help=p["label"])
        elif p.get("kind") in ("float", "int"):
            cfg[p["key"]] = st.column_config.NumberColumn(head, help=p["label"], format="%.6g")
    ed = st.data_editor(base, hide_index=True, key=key, width="stretch", disabled=["Unit"], column_config=cfg)
    st.caption("Edit the cells; only changed cells are planned. Parameters that the unit's specification does not use are flagged.")
    reqs = D.requests_from_edited(model, base, ed)
    if not reqs:
        return
    _plan_panel(D.plan(model, reqs), "dat_tbl_plan", f"Batch table: {CATALOGUE[t]['label']}")


def _batch_comp(model):
    if not any(u["type"] == "feed" for u in model["units"].values()):
        st.info("The flowsheet has no feed.")
        return
    base, key = _frozen("comp", lambda: D.composition_wide(model))
    comps = [c for c in base.columns if c != "Feed"]
    ed = st.data_editor(base, hide_index=True, key=key, width="stretch", disabled=["Feed"],
                        column_config={c: st.column_config.NumberColumn(c, min_value=0.0, format="%.6g") for c in comps})
    sums = ed[comps].apply(pd.to_numeric, errors="coerce").fillna(0.0).sum(axis=1)
    st.caption("Sum per feed: " + ", ".join(f"{f} = {s:.4g}" for f, s in zip(ed["Feed"], sums)) + ". The solver normalises, so the sum does not have to be 1.")
    b2 = base.rename(columns={c: D.COMP_PREFIX + c for c in comps})
    e2 = ed.rename(columns={c: D.COMP_PREFIX + c for c in comps})
    reqs = D.requests_from_edited(model, b2, e2, "Feed")
    if reqs:
        _plan_panel(D.plan(model, reqs), "dat_comp_plan", "Batch edit: feed compositions")


# ================================================================================================== export
def _export_tab():
    ss = st.session_state
    model = ss.model
    base = _tables()
    edited = ss.get("data_edited")
    if edited and edited.get("sig") != S.model_hash(model):
        st.caption("The tables edited in the Python editor belong to an earlier state of the flowsheet and are not used - run the script again.")
        edited = None
    use_edit = False
    if edited:
        use_edit = st.toggle("Use the tables edited in the Python editor", value=True, key="dat_use_edit",
                             help="Switch off to export the tables as the flowsheet gives them.")
    tables = edited["tables"] if (edited and use_edit) else base
    if not sol_is_current():
        st.info("Solve the flowsheet to include the stream, unit and overall results. The parameter tables below are always available.")
    names = list(tables)
    if not names:
        st.info("No tables to export.")
        return
    valid_choice("dat_exp_sel", names, multi=True)
    pick = st.multiselect("Tables to include", names, default=names, key="dat_exp_sel")
    sel = D.OrderedDict((n, tables[n]) for n in pick)
    if not sel:
        return
    cells = sum(df.shape[0] * df.shape[1] for df in sel.values())
    st.caption(f"{len(sel)} table(s), {cells:,} cells. Values are in the units named in the headers.")
    pv = st.selectbox("Preview", list(sel), key="dat_exp_prev")
    st.dataframe(_safe(sel[pv].head(300)), hide_index=True, width="stretch", height=300)
    st.markdown("##### Files")
    c = st.columns(3)
    xl_mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    sig = (tuple(sel), cells, ss.get("sol_hash"), bool(edited and use_edit), (edited or {}).get("n", 0))
    with c[0]:
        _file_button("Excel workbook (.xlsx)", "xl", sig, lambda: D.to_excel_bytes(sel, "ProcessFlow Studio export"), "processflow_export.xlsx", xl_mime, cells)
    with c[1]:
        _file_button("All tables as CSV (.zip)", "zip", sig, lambda: D.to_csv_zip(sel), "processflow_export_csv.zip", "application/zip", cells)
    with c[2]:
        one = st.selectbox("One table as CSV", list(sel), key="dat_exp_one", label_visibility="collapsed")
        st.download_button(f"CSV: {one}"[:60], D.to_csv_text(sel[one]), file_name=f"{D.sheet_names([one])[0]}.csv", mime="text/csv",
                           key="dat_exp_csv", width="stretch")
    c = st.columns(3)
    try:
        c[0].download_button("Flowsheet (YAML)", D.model_to_yaml(model), file_name="flowsheet.yaml", mime="text/yaml", key="dat_exp_fy", width="stretch")
        c[1].download_button("Parameters only (YAML)", D.parameters_to_yaml(model), file_name="parameters.yaml", mime="text/yaml", key="dat_exp_py", width="stretch")
    except ImportError:
        c[0].caption("YAML needs the PyYAML package.")
    c[2].download_button("Flowsheet (JSON)", json.dumps(model, indent=1, default=float), file_name="flowsheet.json", mime="application/json",
                         key="dat_exp_fj", width="stretch")
    st.caption("The *Parameters* and *Feed compositions* sheets can be edited in Excel and imported again on the Import tab.")


def _file_button(label, key, sig, make, fname, mime, cells):
    ss = st.session_state
    ck = f"_datfile_{key}"
    if cells <= BIG:
        st.download_button(label, make(), file_name=fname, mime=mime, key=f"dat_dl_{key}", width="stretch")
        return
    if st.button(f"Prepare {label}", key=f"dat_prep_{key}", width="stretch"):
        ss[ck] = (sig, make())
    if ss.get(ck, (None,))[0] == sig:
        st.download_button(f"Download {fname}", ss[ck][1], file_name=fname, mime=mime, key=f"dat_dl_{key}", width="stretch")
    else:
        st.caption("Large export: press Prepare first.")


# ================================================================================================== Python editor
def _editor_status():
    """(enabled, password or None).  The editor runs code, so on a deployed app it must be switched on by whoever hosts it:
    environment variable PFS_PYTHON_EDITOR=on or the Streamlit secret python_editor = "on"; with
    PFS_PYTHON_EDITOR_PASSWORD / python_editor_password set, the password is asked for."""
    on = os.environ.get("PFS_PYTHON_EDITOR", "").strip().lower() in ("on", "1", "true", "yes")
    pw = os.environ.get("PFS_PYTHON_EDITOR_PASSWORD") or None
    try:
        sec = getattr(st, "secrets", None)
        if sec is not None:
            if str(sec.get("python_editor", "")).strip().lower() in ("on", "1", "true", "yes"):
                on = True
            pw = pw or sec.get("python_editor_password") or None
    except Exception:
        pass
    return on, pw


def _insert_snippet():
    ss = st.session_state
    ss["dat_code"] = D.SNIPPETS[ss["dat_snip"]]


def _python_tab():
    ss = st.session_state
    st.markdown("Adjust the export tables with a short Python script before you export them - round numbers, add a column, keep some "
                "rows, add a summary sheet, or change the *Parameters* table and apply it back to the flowsheet.")
    on, pw = _editor_status()
    if not on:
        st.info("The Python editor is switched off. It executes code, so on a shared or public deployment only the owner should enable it.\n\n"
                "- **Locally:** start the app with the environment variable `PFS_PYTHON_EDITOR=on`.\n"
                "- **Streamlit Community Cloud:** App settings → Secrets → `python_editor = \"on\"` and, strongly recommended, "
                "`python_editor_password = \"…\"` (anyone who can open the app link could otherwise run code on the server).")
        return
    if pw:
        got = st.text_input("Password", type="password", key="dat_pw")
        if not (got and hmac.compare_digest(str(got), str(pw))):
            st.caption("Enter the editor password.")
            return
    base = _tables()
    with st.expander("Tables available to the script", expanded=False):
        st.dataframe(pd.DataFrame([{"Table": k, "Rows": v.shape[0], "Columns": v.shape[1], "Column names": ", ".join(map(str, v.columns))[:140]}
                                   for k, v in base.items()]), hide_index=True, width="stretch")
        st.caption("Available names: `tables` (dict of DataFrames), `pd`, `np`, `math`; `import` works for math, statistics, numpy, pandas, re, datetime, "
                   "json, itertools, collections, functools, decimal, fractions, textwrap, random. No file or network access.")
    c = st.columns([4, 1])
    c[0].selectbox("Examples", list(D.SNIPPETS), key="dat_snip", label_visibility="collapsed")
    c[1].button("Insert", key="dat_snip_go", on_click=_insert_snippet, width="stretch")
    if "dat_code" not in ss:
        ss["dat_code"] = SAMPLE
    code = st.text_area("Script", height=280, key="dat_code", label_visibility="collapsed")
    r = st.columns([1, 1, 4])
    go = r[0].button("Run script", key="dat_run", type="primary", width="stretch")
    if r[1].button("Discard edits", key="dat_discard", width="stretch"):
        ss.pop("data_edited", None)
        ss.pop("_data_run", None)
        st.rerun()
    r[2].caption("The script runs on a copy; the flowsheet is never changed by it. It is stopped after 30 s.")
    if go:
        with st.spinner("Running…"):
            try:
                res = D.run_script(code, base, timeout=30)
            except D.DataError as e:
                res = {"ok": False, "error": str(e), "stdout": "", "tables": None, "seconds": 0.0}
        res["sig"] = S.model_hash(ss.model)
        ss["_data_run"] = res
        if res["ok"]:
            ss["data_edited"] = {"tables": res["tables"], "n": (ss.get("data_edited") or {}).get("n", 0) + 1, "sig": S.model_hash(ss.model)}
    res = ss.get("_data_run")
    if res and res.get("sig") != S.model_hash(ss.model):
        ss.pop("_data_run", None)            # the flowsheet changed since the script ran: its tables no longer match
        res = None
        st.caption("The flowsheet changed since the last run - run the script again.")
    if not res:
        if ss.get("data_edited"):
            st.caption("Edited tables from an earlier run are active on the Export tab.")
        return
    if not res["ok"]:
        st.error(res["error"])
    else:
        st.success(f"Done in {res['seconds']:.1f} s - the edited tables are now what the Export tab offers.")
    if res["stdout"]:
        st.markdown("**Output**\n\n```\n" + res["stdout"][:4000] + "\n```")
    if res["ok"]:
        df = pd.DataFrame([{"Table": k, "What happened": s, "Detail": d} for k, s, d in D.diff_tables(base, res["tables"])])
        st.dataframe(df, hide_index=True, width="stretch")
        names = list(res["tables"])
        if names:
            valid_choice("dat_py_prev", names)
            pv = st.selectbox("Preview", names, key="dat_py_prev")
            st.dataframe(_safe(res["tables"][pv].head(300)), hide_index=True, width="stretch", height=280)
        # an edited parameter / composition table can be applied back to the flowsheet
        reqs = []
        try:
            if "Parameters" in res["tables"]:
                reqs += D.requests_from_frame(res["tables"]["Parameters"], ss.model, D.KINDS[0])[0]
            if "Feed compositions" in res["tables"]:
                reqs += D.requests_from_frame(res["tables"]["Feed compositions"], ss.model, D.KINDS[2])[0]
        except D.DataError as e:
            st.caption(f"The edited Parameters / Feed compositions tables cannot be applied: {e}")
            reqs = []
        if reqs:
            pl = [r_ for r_ in D.plan(ss.model, reqs)]
            if D.plan_summary(pl)["ok"] + D.plan_summary(pl)["error"] + D.plan_summary(pl)["warning"]:
                st.markdown("##### Apply the edited parameters to the flowsheet")
                _plan_panel(pl, "dat_py_plan", "Python editor: edited parameters")
