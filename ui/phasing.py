"""Project phasing in the UI: the stage selector above the diagram, the phase manager, the appearance section of the
property view, and the before / after comparison."""
from __future__ import annotations

import copy

import pandas as pd
import streamlit as st

from procsim import phasing as PH
from procsim.flowsheet import solve, build_fluid, replace_connection
from procsim.unitops import CATALOGUE

from . import state as S

HAS_ORIENT = {"separator": ("Vertical", "Horizontal"), "scrubber": ("Vertical", "Horizontal"),
              "separator3": ("Horizontal", "Vertical")}        # first = the default drawing


def _set_stage(value):
    ss = st.session_state
    ss.model["stage"] = value
    S.bump()


def phase_bar():
    """Stage selector (design view / today / after each phase) and the phase manager."""
    ss = st.session_state
    m = ss.model
    ph = PH.phase_list(m)
    opts = PH.stage_options(m)
    values = [v for v, _ in opts]
    cur = m.get("stage", -1)
    if cur not in values:
        cur = -1
        m["stage"] = -1
    cnt = PH.counts(m)
    labels = {v: lab + (f"  ({cnt[v][0]} units)" if v >= 0 and ph else "") for v, lab in opts}
    c1, c2 = st.columns([5, 1])
    with c1:
        if ph:
            choice = st.radio("View / study stage", values, index=values.index(cur), horizontal=True,
                              format_func=lambda v: labels[v], key=f"stage_sel_{ss.widget_ver}",
                              help="Design view draws every phase in its colour and solves the final stage. A stage view shows "
                                   "and solves the flowsheet as it is in that stage - equipment of later phases is not in place.")
            if choice != cur:
                _set_stage(choice)
                st.rerun()
        else:
            st.caption("No project phases yet. Add a phase to colour future installations and to compare the design "
                       "before and after a modification.")
    with c2:
        add = st.button("＋ Phase", key="ph_add", width="stretch", help="Add a project phase (a future modification)")
        if add:
            PH.add_phase(m)
            S.bump()
            st.rerun()
    if ph:
        with st.expander("Project phases — names, colours, items", expanded=False):
            for i, p in enumerate(ph):
                a, b, c, d = st.columns([3, 1, 3, 1])
                nm = a.text_input("Name", value=p["name"], key=f"ph_nm_{p['id']}_{ss.widget_ver}", label_visibility="collapsed")
                col = b.color_picker("Colour", value=p.get("color") or PH.DEFAULT_COLORS[i % len(PH.DEFAULT_COLORS)],
                                     key=f"ph_col_{p['id']}_{ss.widget_ver}", label_visibility="collapsed")
                items = [u["name"] for u in m["units"].values() if u.get("phase") == p["id"]]
                c.caption(f"+ / − {len(items)} unit(s), "
                          f"{sum(1 for t in m['streams'].values() if t.get('phase') == p['id'])} stream(s)")
                if nm != p["name"] or col != p.get("color"):
                    p["name"], p["color"] = nm.strip() or p["name"], col
                    S.bump()
                    st.rerun()
                if d.button("🗑", key=f"ph_del_{p['id']}", help="Delete the phase; its items become ordinary (existing)"):
                    PH.remove_phase(m, p["id"])
                    S.bump()
                    st.rerun()
            st.caption("Tag equipment and streams on the diagram: select them, then pick *new in Phase n* or *removed in Phase n* "
                       "in the toolbar list (or in *Appearance & project phase* below). A new bypass is drawn as new, the old "
                       "route as removed - both can use the same port because they are never in place together.")


def appearance_panel(obj_id):
    """Phase, colour, size and orientation of the selected unit / stream."""
    ss = st.session_state
    m = ss.model
    el = m["units"].get(obj_id) or m["streams"].get(obj_id)
    if el is None:
        return
    is_unit = obj_id in m["units"]
    ph = PH.phase_list(m)
    with st.expander("🎨 Appearance & project phase", expanded=False):
        changed = False
        opts = ["Existing (in place today)"]
        keymap = {opts[0]: ("", "add")}
        for p in ph:
            a, b = f"New — added in {p['name']}", f"Removed in {p['name']}"
            opts += [a, b]
            keymap[a], keymap[b] = (p["id"], "add"), (p["id"], "remove")
        cur = opts[0]
        for k, (pid, ch) in keymap.items():
            if pid and el.get("phase") == pid and el.get("change", "add") == ch:
                cur = k
        c1, c2 = st.columns(2)
        pick = c1.selectbox("Project phase", opts, index=opts.index(cur), key=f"ap_ph_{obj_id}_{ss.widget_ver}_{len(opts)}_{opts.index(cur)}",
                            help="New items are drawn in the phase colour and are missing in the stages before that phase; "
                                 "removed items are in place until that phase.")
        if pick != cur:
            pid, ch = keymap[pick]
            PH.set_phase(m, [obj_id], pid, ch)
            changed = True
        cur_ph = el.get("phase")
        if cur_ph and el.get("change", "add") == "add":
            ia = PH.phase_index(m, cur_ph)
            later = [x for x in ph[ia:]]
            untils = ["— (stays)"] + [f"Retired in {x['name']}" for x in later]
            cu = next((f"Retired in {x['name']}" for x in later if x["id"] == el.get("until")), untils[0])
            pu = c1.selectbox("In place until", untils, index=untils.index(cu), key=f"ap_un_{obj_id}_{ss.widget_ver}_{len(untils)}_{untils.index(cu)}",
                              help="Retire this item again in a later phase (a temporary route or a unit replaced by a bigger one).")
            if pu != cu:
                if pu == untils[0]:
                    el.pop("until", None)
                else:
                    el["until"] = later[untils.index(pu) - 1]["id"]
                changed = True
        auto = c2.checkbox("Automatic colour", value=not el.get("color"), key=f"ap_auto_{obj_id}_{ss.widget_ver}_{bool(el.get('color'))}")
        if not auto:
            base = el.get("color") or PH.effective_color(el, m) or "#e8590c"
            col = c2.color_picker("Colour", value=base, key=f"ap_col_{obj_id}_{ss.widget_ver}_{base}")
            if col != el.get("color"):
                el["color"] = col
                changed = True
        elif el.get("color"):
            el.pop("color")
            changed = True
        if is_unit:
            d1, d2 = st.columns(2)
            k = float(el.get("scale", 1.0) or 1.0)
            z = d1.slider("Element zoom [%]", 40, 400, int(round(k * 100)), 5, key=f"ap_zoom_{obj_id}_{ss.widget_ver}_{int(round(k * 100))}",
                          help="Enlarges or shrinks this symbol (and its label) on the diagram. Alt + mouse wheel on the "
                               "diagram does the same.")
            if abs(z / 100.0 - k) > 1e-6:
                if abs(z - 100) < 1e-9:
                    el.pop("scale", None)
                else:
                    el["scale"] = z / 100.0
                changed = True
            t = el["type"]
            if t in HAS_ORIENT:
                names = HAS_ORIENT[t]
                cur_o = names[0] if not el.get("orient") else {"v": "Vertical", "h": "Horizontal"}[el["orient"]]
                o = d2.radio("Vessel orientation", list(HAS_ORIENT[t]), index=list(HAS_ORIENT[t]).index(cur_o), horizontal=True,
                             key=f"ap_or_{obj_id}_{ss.widget_ver}_{cur_o}")
                if o != cur_o:
                    code = "v" if o == "Vertical" else "h"
                    default_code = "v" if names[0] == "Vertical" else "h"
                    if code == default_code:
                        el.pop("orient", None)
                    else:
                        el["orient"] = code
                    changed = True
        if changed:
            S.bump()
            st.rerun()
    if ph:
        connection_phase_panel(obj_id)
        if is_unit:
            stage_spec_panel(obj_id)


def connection_phase_panel(obj_id):
    """Explicitly schedule a replacement without deleting the old route."""
    ss = st.session_state
    m = ss.model
    ph = PH.phase_list(m)
    streams = {sid: s for sid, s in m["streams"].items()
               if sid == obj_id or any(s[end][0] == obj_id for end in ("src", "dst"))}
    if not ph or not streams:
        return
    with st.expander("Replace connection in a later phase", expanded=False):
        st.caption("Keep the old route before the chosen phase and use a new route from that phase on. "
                   "Choose the connection to replace, then change its source or destination. "
                   "This changes routing, not equipment parameter values.")
        prefix = f"ss_conn_{obj_id}_{ss.widget_ver}"
        sid = st.selectbox("Connection to replace", list(streams), key=f"{prefix}_old",
                           format_func=lambda k: f"{streams[k]['name']}: "
                           f"{m['units'][streams[k]['src'][0]]['name']} / {streams[k]['src'][1]} -> "
                           f"{m['units'][streams[k]['dst'][0]]['name']} / {streams[k]['dst'][1]}")
        pid = st.selectbox("Replace from phase", [p["id"] for p in ph], key=f"{prefix}_phase",
                           format_func=lambda k: next(p["name"] for p in ph if p["id"] == k))
        old = streams[sid]
        ends = {}
        for end, direction, label in (("src", "out", "New source"), ("dst", "in", "New destination")):
            choices = [uid for uid, u in m["units"].items() if CATALOGUE[u["type"]]["ports"][direction]]
            uid = st.selectbox(label, choices, index=choices.index(old[end][0]),
                               format_func=lambda k: m["units"][k]["name"], key=f"{prefix}_{sid}_{end}")
            ports = list(CATALOGUE[m["units"][uid]["type"]]["ports"][direction])
            port = st.selectbox(f"{label} port", ports,
                                index=ports.index(old[end][1]) if old[end][1] in ports else 0,
                                key=f"{prefix}_{sid}_{end}_{uid}_port")
            ends[end] = (uid, port)
        if st.button("Schedule replacement", key=f"{prefix}_set"):
            updated = copy.deepcopy(m)
            try:
                new_sid = replace_connection(updated, sid, pid, *ends["src"], *ends["dst"])
            except ValueError as exc:
                st.error(str(exc))
            else:
                S.push_edit(f"Replace connection {old['name']} from {PH.stage_name(m, PH.phase_index(m, pid))}")
                ss.model = updated
                ss.selected = [new_sid]
                S.bump()
                st.rerun()


def stage_spec_panel(uid):
    """Different value of a parameter from a later phase on (feed pressure after the booster, a new efficiency ...)."""
    ss = st.session_state
    m = ss.model
    u = m["units"][uid]
    ph = PH.phase_list(m)
    sp = u.get("stage_params", {})
    specs = [x for x in CATALOGUE[u["type"]]["params"] if x["kind"] in ("float", "select")]
    if not specs and not sp:
        st.caption("This equipment has no phase-specific numeric or dropdown settings. To change its routing, "
                   "use 'Replace connection in a later phase' above or select a connection on the diagram.")
        return
    with st.expander("🔀 Different value in a later phase", expanded=bool(sp)):
        st.caption("The Design tab holds today's value. A value set here replaces it in the stage of the chosen phase and every later "
                   "stage (a later phase wins). Use it for what changes with the project: feed pressure after a booster, "
                   "efficiency of a revamped machine, a new rate.")
        if sp:
            rows = []
            for pid, vals in sp.items():
                pn = next((p["name"] for p in ph if p["id"] == pid), pid)
                for k, v in vals.items():
                    rows.append({"From": pn, "Parameter": k, "Today": u["params"].get(k), "New value": v})
            st.dataframe(pd.DataFrame(rows).set_index("From").map(lambda v: S.fmt(v) if isinstance(v, float) else v), width="stretch")
        c1, c2, c3 = st.columns([2, 2, 2])
        pn = c1.selectbox("From", [p["name"] for p in ph], key=f"ss_ph_{uid}_{ss.widget_ver}")
        pid = next(p["id"] for p in ph if p["name"] == pn)
        labels = {x["key"]: x["label"] for x in specs}
        key = c2.selectbox("Parameter", list(labels), format_func=lambda k: labels[k], key=f"ss_k_{uid}_{ss.widget_ver}")
        spec = next((x for x in specs if x["key"] == key), None)
        if spec is not None:
            cur = sp.get(pid, {}).get(key, u["params"].get(key, spec["default"]))
            if spec["kind"] == "select":
                val = c3.selectbox("New value", spec["options"], index=spec["options"].index(cur) if cur in spec["options"] else 0,
                                   key=f"ss_v_{uid}_{ss.widget_ver}_{pid}_{key}")
            else:
                val = c3.number_input("New value", value=float(cur), format="%.6g", key=f"ss_v_{uid}_{ss.widget_ver}_{pid}_{key}")
            a, b = st.columns(2)
            if a.button("Set from this phase", key=f"ss_set_{uid}_{ss.widget_ver}"):
                S.push_edit(f"{u['name']}: {key} from {pn}")
                u.setdefault("stage_params", {}).setdefault(pid, {})[key] = val
                S.bump()
                st.rerun()
            if sp.get(pid, {}).get(key) is not None and b.button("Remove this override", key=f"ss_del_{uid}_{ss.widget_ver}"):
                S.push_edit(f"{u['name']}: remove {key} override")
                sp[pid].pop(key, None)
                if not sp[pid]:
                    sp.pop(pid)
                if not sp:
                    u.pop("stage_params", None)
                S.bump()
                st.rerun()
    if not u.get("stage_params"):
        u.pop("stage_params", None)


# --------------------------------------------------------------- compare stages

def _stage_summary(model, stage, fp):
    m = dict(model, stage=stage)
    sol = solve(m, fp)
    rows = {}
    prod = {}
    for sid, s in sol.streams.items():
        t = model["streams"].get(sid)
        if not t:
            continue
        if model["units"][t["dst"][0]]["type"] == "product" and not s.empty:
            prod[t["name"]] = (s.F * s.MW, s.T - 273.15, s.P)
    work_in = sum(e.duty_kW for e in sol.energy if e.kind == "work" and e.duty_kW > 0)
    work_out = -sum(e.duty_kW for e in sol.energy if e.kind == "work" and e.duty_kW < 0)
    heat_in = sum(e.duty_kW for e in sol.energy if e.kind == "heat" and e.duty_kW > 0)
    heat_out = -sum(e.duty_kW for e in sol.energy if e.kind == "heat" and e.duty_kW < 0)
    bad = [model["units"][u]["name"] for u, v in sol.status.items() if v in ("error", "missing", "unsolved") and u in model["units"]]
    n_units = sum(1 for u in PH.stage_model(model, stage)["units"].values() if u["type"] not in PH.ANNOTATION_TYPES)
    return {"products": prod, "work_in": work_in, "work_out": work_out, "heat_in": heat_in, "heat_out": heat_out,
            "units": n_units, "problems": bad}


def compare_panel():
    """Before / after comparison of two stages: products, power and heat."""
    ss = st.session_state
    m = ss.model
    ph = PH.phase_list(m)
    if not ph:
        st.info("Add a project phase on the Flowsheet tab, tag the future equipment and streams, then compare the stages here.")
        return
    stages = list(range(PH.n_phases(m) + 1))
    c1, c2 = st.columns(2)
    a = c1.selectbox("Before", stages, index=0, format_func=lambda s: PH.stage_name(m, s), key="cmp_a")
    b = c2.selectbox("After", stages, index=len(stages) - 1, format_func=lambda s: PH.stage_name(m, s), key="cmp_b")
    if a == b:
        st.caption("Pick two different stages.")
        return
    if not st.button("Solve and compare", key="cmp_go", type="primary"):
        st.caption("Solves the flowsheet in both stages with the same fluid package; nothing in the model changes.")
        return
    try:
        fp = build_fluid(m)
        with st.spinner("Solving both stages…"):
            ra, rb = _stage_summary(m, a, fp), _stage_summary(m, b, fp)
    except Exception as e:                       # noqa: BLE001
        st.error(str(e))
        return
    for lab, r in (("before", ra), ("after", rb)):
        if r["problems"]:
            st.warning(f"The {lab} stage has unsolved items: {', '.join(r['problems'][:6])}")
    names = sorted(set(ra["products"]) | set(rb["products"]))
    rows = []
    for n in names:
        x, y = ra["products"].get(n), rb["products"].get(n)
        rows.append({"Product": n,
                     "Mass flow before [kg/h]": x[0] if x else None, "Mass flow after [kg/h]": y[0] if y else None,
                     "Δ mass flow [kg/h]": (y[0] if y else 0.0) - (x[0] if x else 0.0),
                     "T before [°C]": x[1] if x else None, "T after [°C]": y[1] if y else None,
                     "P before [bar(a)]": x[2] if x else None, "P after [bar(a)]": y[2] if y else None})
    if rows:
        st.dataframe(pd.DataFrame(rows).set_index("Product").map(lambda v: S.fmt(v) if v is not None else "—"), width="stretch")
    tot = pd.DataFrame({
        "Before": [ra["units"], ra["work_in"], ra["work_out"], ra["heat_in"], ra["heat_out"]],
        "After": [rb["units"], rb["work_in"], rb["work_out"], rb["heat_in"], rb["heat_out"]]},
        index=["Units in place", "Shaft work consumed [kW]", "Shaft work produced [kW]", "Heat added [kW]", "Heat removed [kW]"])
    tot["Change"] = tot["After"] - tot["Before"]
    st.dataframe(tot.map(S.fmt), width="stretch")
    st.caption("Same operating specifications in both stages; equipment that is not yet in place is simply absent, so check that the "
               "remaining units still have a sensible duty (a pump or compressor with a fixed outlet pressure will hold it).")


# --------------------------------------------------------------- phase economics (v7.8)

def _phase_report_button(m, res):
    """One click: printable report with the phase table, the stages and the final-stage flowsheet."""
    from procsim import phasecost as PCo  # noqa: F401
    from .report import build_report
    ss = st.session_state
    try:
        fin = dict(m, stage=-1, phasecost_result={k: v for k, v in res.items() if k != "elements"})
        sol = solve(fin, build_fluid(m))
        html = build_report(fin, sol, ss.get("svg"), "Project phases: " + str(m.get("name", "flowsheet")))
    except Exception as e:                           # noqa: BLE001
        st.caption(f"Phase report unavailable: {e}")
        return
    st.download_button("Download phase report (HTML → print to PDF)", data=html, file_name="processflow_phase_report.html",
                       mime="text/html", key="pc_report")


def phase_economics_panel():
    """CAPEX, change in cost and production, payback and NPV of each project phase."""
    import plotly.graph_objects as go
    from procsim import phasecost as PCo
    ss = st.session_state
    m = ss.model
    if not PH.phase_list(m):
        st.info("Add a project phase on the Flowsheet tab and tag the equipment it brings; the economics of each phase are computed here.")
        return
    p = PCo.cost_params(m)
    st.caption("Equipment cost = reference cost × (size / reference size)^exponent from the solved sizes (generic illustrative values - "
               "override single items below), × installation factor, + contingency. Costs and prices are in millions of the model currency.")
    with st.expander("Cost and price assumptions", expanded=False):
        c = st.columns(3)
        new = {
            "price_boe": c[0].number_input("Price of the production gain [cur/boe]", value=float(p["price_boe"]), min_value=0.0, key="pc_price"),
            "discount": c[1].number_input("Discount rate [%/y]", value=float(p["discount"]), min_value=0.0, max_value=50.0, key="pc_disc"),
            "life": c[2].number_input("Economic life after start-up [years]", value=float(p["life"]), min_value=1.0, max_value=60.0, key="pc_life"),
            "install": c[0].number_input("Installation factor", value=float(p["install"]), min_value=1.0, max_value=6.0, key="pc_inst"),
            "contingency": c[1].number_input("Contingency [%]", value=float(p["contingency"]), min_value=0.0, max_value=100.0, key="pc_cont"),
            "maint": c[2].number_input("Maintenance [% of CAPEX per year]", value=float(p["maint"]), min_value=0.0, max_value=20.0, key="pc_maint"),
        }
        ov_names = sorted(u["name"] for u in m["units"].values() if u.get("phase") and u["type"] not in PCo.NO_COST and u["type"] not in PH.ANNOTATION_TYPES)
        ov = dict(p["overrides"])
        if ov_names:
            st.markdown("**Installed cost override per added unit** (0 = use the cost model)")
            cols = st.columns(3)
            for i, nm in enumerate(ov_names):
                v = cols[i % 3].number_input(nm, value=float(ov.get(nm, 0.0)), min_value=0.0, key=f"pc_ov_{i}_{nm}")
                if v > 0:
                    ov[nm] = v
                else:
                    ov.pop(nm, None)
        new["overrides"] = ov
        new["table"] = dict(p["table"])
        st.markdown("**Own cost table** (CSV with columns `type, S_ref, C_ref, exponent`; C_ref in millions of the model currency)")
        up = st.file_uploader("Import cost table", type=["csv", "txt"], key=f"pc_tab_{ss.get('pc_tab_ver', 0)}")
        if up is not None:
            tab, probs = PCo.parse_cost_csv(up.getvalue().decode("utf-8", "replace"))
            for x in probs[:8]:
                st.warning(x)
            if tab:
                new["table"].update(tab)
                ss["pc_tab_ver"] = ss.get("pc_tab_ver", 0) + 1
                st.success(f"{len(tab)} cost curve(s) imported - kept in the saved flowsheet only.")
        if new["table"]:
            st.caption("Own curves for: " + ", ".join(sorted(new["table"])))
            if st.button("Clear own cost table", key="pc_tab_clear"):
                new["table"] = {}
        if new != {k: p[k] for k in new}:
            m["phasecost"] = new
            p = PCo.cost_params(m)
    if not st.button("Compute the economics of the phases", key="pc_go", type="primary"):
        res = ss.get("_phasecost")
        if not res:
            st.caption("Solves the flowsheet in every stage (one solve per stage).")
            return
    else:
        try:
            with st.spinner("Solving every stage…"):
                ss["_phasecost"] = PCo.rollup(m, build_fluid(m), p)
                m["phasecost_result"] = {k_: v_ for k_, v_ in ss["_phasecost"].items() if k_ != "elements"}
        except Exception as e:                       # noqa: BLE001
            st.error(str(e))
            return
        res = ss["_phasecost"]
    for stg in res["stages"]:
        if stg["problems"]:
            st.warning(f"{stg['name']}: unsolved items {', '.join(stg['problems'][:6])} - the numbers for that stage are incomplete.")
    cur = res["currency"]
    df = pd.DataFrame([{"Phase": x["phase"], "Items": x["items"], "CAPEX [M]": x["capex_Mcur"], "Δ production [boe/d]": x["d_boe_d"],
                        "Δ revenue [M/y]": x["d_revenue_Mcur_y"], "Δ energy + CO₂ [M/y]": -x["d_energy_Mcur_y"], "Maintenance [M/y]": -x["maint_Mcur_y"],
                        "Net benefit [M/y]": x["net_Mcur_y"], "Payback [y]": x["payback_y"], "NPV [M]": x["npv_Mcur"]} for x in res["phases"]])
    st.dataframe(df.set_index("Phase").map(lambda v: S.fmt(v) if isinstance(v, float) else ("never" if v is None else v)), width="stretch")
    t = res["total"]
    k = st.columns(3)
    k[0].metric(f"Total CAPEX [M {cur}]", f"{t['capex_Mcur']:,.1f}")
    k[1].metric(f"Net benefit [M {cur}/y]", f"{t['net_Mcur_y']:,.1f}")
    k[2].metric(f"NPV [M {cur}]", f"{t['npv_Mcur']:,.1f}")
    fig = go.Figure(go.Bar(x=[x["phase"] for x in res["phases"]], y=[x["npv_Mcur"] for x in res["phases"]],
                           marker_color=["#1b9e77" if x["npv_Mcur"] >= 0 else "#c0392b" for x in res["phases"]]))
    fig.update_layout(title="NPV of each phase", yaxis_title=f"M {cur}", height=300, margin=dict(l=10, r=10, t=40, b=10))
    st.plotly_chart(fig, width="stretch", key="pc_npv")
    with st.expander("Cost of each added element"):
        el = pd.DataFrame([e for e in res["elements"] if e["Phase"]])
        if len(el):
            st.dataframe(el.set_index("Unit").map(lambda v: S.fmt(v) if isinstance(v, float) else v), width="stretch")
        else:
            st.caption("No element is tagged with a phase.")
    _phase_report_button(m, res)
    st.caption("A phase that only adds compression often shows no production gain in a steady-state flowsheet whose product rates are "
               "fixed by the feed: tie the benefit to a higher feed rate or a lower well-head pressure in the 'after' stage "
               "(e.g. with a stage-specific feed unit) to see it.")
