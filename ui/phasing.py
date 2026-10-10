"""Project phasing in the UI: the stage selector above the diagram, the phase manager, the appearance section of the
property view, and the before / after comparison."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from procsim import phasing as PH
from procsim.flowsheet import solve, build_fluid
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
