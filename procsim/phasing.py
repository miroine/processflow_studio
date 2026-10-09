"""Project phasing: which equipment and streams are in place today and which are added or removed in later phases.

The model keeps ONE flowsheet.  ``model["phases"]`` is an ordered list of ``{"id", "name", "color"}`` (Phase 1, Phase 2 ...);
a unit or stream may carry

    phase   id of a project phase ("" = in place today and kept)
    change  "add" (in place from that phase on) or "remove" (in place until that phase)
    color   optional colour override for the drawing

Stages: 0 = today, i = after phase i (i = 1 .. N).  ``model["stage"]`` selects the stage that is solved and shown;
-1 (the default) is the design view: everything is drawn in its phase colour and the *final* stage (after the last phase)
is solved.  ``stage_model`` returns the flowsheet as it is in a stage: elements that are not in place are dropped, and so
are streams that lost an end.  Because several versions of an element can coexist in the drawing (the old bypass and the
new route into the same port), connection checks look at the stages in which two items are present together
(``presence``)."""
from __future__ import annotations

import copy

DEFAULT_COLORS = ["#e8590c", "#7048e8", "#0b7285", "#c2255c", "#5c940d", "#f08c00"]
ANNOTATION_TYPES = ("platform",)          # drawn only: no ports, never solved


def phase_list(model):
    return [p for p in (model.get("phases") or []) if isinstance(p, dict) and p.get("id")]


def n_phases(model):
    return len(phase_list(model))


def phase_index(model, pid):
    """1-based position of a phase, 0 if the id is empty / unknown."""
    for i, p in enumerate(phase_list(model), 1):
        if p["id"] == pid:
            return i
    return 0


def phase_color(model, pid):
    for i, p in enumerate(phase_list(model)):
        if p["id"] == pid:
            return p.get("color") or DEFAULT_COLORS[i % len(DEFAULT_COLORS)]
    return None


def add_phase(model, name=None, color=None):
    ph = model.setdefault("phases", [])
    n = len(ph) + 1
    used = {p["id"] for p in ph}
    k = n
    while f"p{k}" in used:
        k += 1
    p = {"id": f"p{k}", "name": name or f"Phase {n}", "color": color or DEFAULT_COLORS[(n - 1) % len(DEFAULT_COLORS)]}
    ph.append(p)
    return p


def remove_phase(model, pid):
    """Delete a phase; items tagged with it become ordinary (in place today)."""
    model["phases"] = [p for p in phase_list(model) if p["id"] != pid]
    for coll in ("units", "streams"):
        for el in model.get(coll, {}).values():
            if el.get("phase") == pid:
                el.pop("phase", None)
                el.pop("change", None)
    if model.get("stage", -1) > n_phases(model):
        model["stage"] = -1


def present(el, model, stage):
    """Is the unit / stream in place at ``stage`` (0 .. N)?"""
    i = phase_index(model, el.get("phase") or "")
    if i == 0:
        return True
    if el.get("change", "add") == "remove":
        return stage < i
    return stage >= i


def presence(el, model):
    return frozenset(s for s in range(n_phases(model) + 1) if present(el, model, s))


def resolve_stage(model, stage=None):
    s = model.get("stage", -1) if stage is None else stage
    n = n_phases(model)
    if s is None or s < 0 or s > n:
        return n
    return int(s)


def stage_name(model, stage):
    ph = phase_list(model)
    if stage < 0:
        return "Design view (all phases)"
    if stage == 0:
        return "Today (before any phase)"
    return f"After {ph[stage - 1]['name']}" if stage <= len(ph) else f"Stage {stage}"


def stage_options(model):
    """[(value, label)] for the view selector."""
    return [(-1, stage_name(model, -1))] + [(s, stage_name(model, s)) for s in range(n_phases(model) + 1)]


def effective_color(el, model):
    return el.get("color") or phase_color(model, el.get("phase") or "")


def stage_model(model, stage=None):
    """The flowsheet as it stands in ``stage`` (None = the model's own stage).  Returns ``model`` itself when no phasing
    is used; otherwise a filtered copy."""
    n = n_phases(model)
    if n == 0 and not any(el.get("phase") for coll in ("units", "streams") for el in model.get(coll, {}).values()):
        return model
    s = resolve_stage(model, stage)
    out = copy.copy(model)
    units = {k: u for k, u in model["units"].items() if present(u, model, s)}
    streams = {k: t for k, t in model["streams"].items()
               if present(t, model, s) and t["src"][0] in units and t["dst"][0] in units}
    out["units"], out["streams"] = units, streams
    # an adjust whose variable or target is gone cannot run
    for uid, u in list(units.items()):
        if u["type"] == "adjust":
            p = u["params"]
            tgt_ok = (p.get("tgt_obj") in {x["name"] for x in units.values()} | {t["name"] for t in streams.values()}
                      or not p.get("tgt_obj"))
            if p.get("var_unit") not in units or not tgt_ok:
                units[uid] = dict(u, params=dict(p, active=False))
    return out


def set_phase(model, ids, pid, change="add"):
    """Tag units / streams ``ids`` (pid '' clears the tag)."""
    for i in ids:
        el = model["units"].get(i) or model["streams"].get(i)
        if el is None:
            continue
        if pid and phase_index(model, pid):
            el["phase"], el["change"] = pid, ("remove" if change == "remove" else "add")
        else:
            el.pop("phase", None)
            el.pop("change", None)


def counts(model):
    """{stage: (units, streams)} in place, to label the stage selector."""
    out = {}
    for s in range(n_phases(model) + 1):
        out[s] = (sum(1 for u in model["units"].values() if present(u, model, s) and u["type"] not in ANNOTATION_TYPES),
                  sum(1 for t in model["streams"].values() if present(t, model, s)))
    return out
