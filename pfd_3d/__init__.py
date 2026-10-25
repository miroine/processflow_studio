"""Streamlit wrapper for the 3D plant view (static frontend: Three.js r160, MIT, no build step)."""
from __future__ import annotations

import os

import streamlit.components.v1 as components

_FRONTEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")
_component = components.declare_component("pfd_3d", path=_FRONTEND)


def plant_3d(scene, ports, height=720, key="plant3d"):
    """Render the 3D view of ``scene`` (see procsim.scene3d.build_scene).  ``ports`` is the 2D port table
    (pfd_canvas GEOM, as fractions of the symbol) used to place the nozzles."""
    return _component(scene=scene, ports=ports, height=height, key=key, default=None)
