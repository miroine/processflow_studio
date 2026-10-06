"""Streamlit wrapper for the field-layout drawing (static frontend, no build step; same protocol as pfd_canvas)."""
from __future__ import annotations

import os

import streamlit.components.v1 as components

_FRONTEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")
_component = components.declare_component("fieldmap_canvas", path=_FRONTEND)


def fieldmap_canvas(drawing, nonce, height=640, title="Field layout", key="fieldmap"):
    """Render the drawing from ``procsim.fieldmap.field_drawing``. Returns the last event dict or None.

    Events (each carries session, rev, nonce): move {id, x, y} [km], rotate {id, rot} [deg], bend {pair, bend},
    reset, export_svg {svg}.
    """
    return _component(drawing=drawing, nonce=nonce, height=height, title=title, key=key, default=None)
