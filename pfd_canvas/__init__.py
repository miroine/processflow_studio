"""Streamlit wrapper for the PFD canvas (static frontend, no build step)."""
from __future__ import annotations

import os

import streamlit.components.v1 as components

_FRONTEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")
_component = components.declare_component("pfd_canvas", path=_FRONTEND)


def pfd_canvas(model, catalogue, results, nonce, selected=None, height=620, status="", fit=False, key="pfd"):
    """Render the canvas. Returns the last event dict sent by the canvas (or None).

    model     : {"units": {id: {type, name, x, y, flip}}, "streams": {id: {name, src, dst}}}
    catalogue : {type: {label, prefix, category, ports: {"in": {...}, "out": {...}}}}
    results   : {"streams": {sid: {solved, label, tip}}, "units": {uid: {status, label, tip, energy}},
                 "links": [[adjust_uid, target_id], ...]}
    nonce     : bump whenever Python changes the structure so the canvas adopts `model`
    """
    return _component(model=model, catalogue=catalogue, results=results, nonce=nonce,
                      selected=selected or [], height=height, status=status, fit=fit,
                      key=key, default=None)
