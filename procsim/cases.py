"""Named cases (variants) in one flowsheet file (v7.9).

``model["cases"]`` holds snapshots {name: model without "cases"} of the variants that are *not* open; the open one is the model
itself and is called ``model["case_active"]``.  Switching saves the open variant under its name and opens the other, so a
"Base", a "High GOR" and a "Phase 2 revamp" travel together in one .json file."""
from __future__ import annotations

import copy

SKIP = ("cases", "case_active")
DEFAULT_NAME = "Base"


def active(model):
    return model.get("case_active") or DEFAULT_NAME


def names(model):
    return [active(model)] + [n for n in (model.get("cases") or {}) if n != active(model)]


def _snapshot(model):
    return copy.deepcopy({k: v for k, v in model.items() if k not in SKIP})


def add_case(model, name, copy_current=True):
    """Store a copy of the open variant under ``name`` and keep working in the open one.  Returns the name used."""
    name = (name or "").strip()
    if not name:
        raise ValueError("give the case a name")
    if name in names(model):
        raise ValueError(f"a case called '{name}' exists")
    model.setdefault("case_active", active(model))
    model.setdefault("cases", {})[name] = _snapshot(model)
    return name


def switch(model, name):
    """A new model object with case ``name`` open (the old open variant is kept under its name)."""
    if name == active(model):
        return model
    cases = dict(model.get("cases") or {})
    if name not in cases:
        raise KeyError(name)
    cases[active(model)] = _snapshot(model)
    new = copy.deepcopy(cases.pop(name))
    new["cases"] = cases
    new["case_active"] = name
    return new


def delete(model, name):
    if name == active(model):
        raise ValueError("the open case cannot be deleted: switch to another one first")
    (model.get("cases") or {}).pop(name, None)
    if not model.get("cases"):
        model.pop("cases", None)


def rename(model, old, new):
    new = (new or "").strip()
    if not new or new in names(model):
        raise ValueError("choose a new, unused name")
    if old == active(model):
        model["case_active"] = new
    else:
        model["cases"][new] = model["cases"].pop(old)
