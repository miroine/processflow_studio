"""3D plant view: a button on the Flowsheet tab swaps the 2D diagram for an interactive 3D scene of the same design (v7.10)."""
from __future__ import annotations

import json
import os

import streamlit as st

from procsim import scene3d as SC
from . import state as S

_PORTS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pfd_3d", "frontend", "ports2d.json")
_ports = None


def ports2d():
    global _ports
    if _ports is None:
        with open(_PORTS_FILE, encoding="utf-8") as f:
            _ports = json.load(f)
    return _ports


def toggle_button():
    """The swap button (2D diagram <-> 3D plant view)."""
    ss = st.session_state
    if ss.get("view3d"):
        if st.button("🧩 Back to the 2D diagram", key="btn_view2d", type="primary"):
            ss["view3d"] = False
            S.bump(fit=True)                 # the canvas is mounted again: let it adopt the model and fit the drawing
            st.rerun()
    elif st.button("🧊 View in 3D", key="btn_view3d",
                   help="Swap the diagram for a 3D model of the same design: click equipment to inspect it, watch the flow in the pipes."):
        ss["view3d"] = True
        st.rerun()


def _apply_edit(res):
    """Take over the layout the user edited in the 3D view (component value) and rerun once so the scene carries it."""
    ss = st.session_state
    if not isinstance(res, dict) or not res.get("nonce") or res.get("nonce") == ss.get("_l3d_nonce"):
        return
    ss["_l3d_nonce"] = res["nonce"]
    old = ss.model.get("layout3d") or {}
    if res.get("reset"):
        ss.model["layout3d"] = {"units": {}, "routes": {}, "origin": None, "ver": int(old.get("ver", 0)) + 1}
    else:
        lay = SC.sanitize_layout(res.get("layout"))
        lay["origin"] = old.get("origin") or SC.scene_origin(ss.model)
        lay["ver"] = int(old.get("ver", 0))
        ss.model["layout3d"] = lay
    st.rerun()


def view3d_panel(height=720):
    from pfd_3d import plant_3d
    ss = st.session_state
    if not ss.model["units"]:
        st.info("Build or load a flowsheet first - the 3D view draws the same design.")
        return
    sol = ss.sol if S.sol_is_current() else None
    scene = SC.build_scene(ss.model, sol, fmt=S.fmt, qfmt=S.qfmt)
    if sol is None:
        st.info("The flowsheet is not solved (or was edited since): equipment is shown without results and the pipes without flow. Press **▶ Solve**.")
    res = plant_3d(scene, ports2d(), height=max(int(height), 640), key="plant3d")
    _apply_edit(res)
    lay = ss.model.get("layout3d")
    c1, c2, _ = st.columns([1, 1, 4])
    if c1.button("↺ Reset 3D layout", key="btn_reset3d", disabled=not (lay and (lay.get("units") or lay.get("routes"))),
                 help="Forget the hand-placed equipment and pipe routes: the layout follows the 2D drawing again."):
        ss.model["layout3d"] = {"units": {}, "routes": {}, "origin": None, "ver": int((lay or {}).get("ver", 0)) + 1}
        st.rerun()
    st.caption("The 3D model is a schematic of the same design: equipment shapes are generic, the first plot layout follows the 2D drawing "
               "(neighbours are pushed apart for clearance; use ✎ Edit layout to move things) and pipe sizes are indicative (typical design velocities), not a line-sizing "
               "result. Particle speed follows the solved velocity; colour follows the phase. Switch stage above to see future phases. "
               "Made by Merouane Hamdani - for educational purposes only.")
