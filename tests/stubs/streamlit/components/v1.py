"""Stub of streamlit.components.v1: records component calls, returns the keyed session value."""
import os

import streamlit as st


def declare_component(name, path=None, url=None):
    if path is not None:
        assert os.path.isfile(os.path.join(path, "index.html")), "component frontend missing index.html"

    def component(key=None, default=None, **kwargs):
        import json
        json.dumps(kwargs)   # args must be JSON-serialisable, as in Streamlit
        st.HOOK["component_calls"].append({"name": name, "key": key, "args": kwargs})
        st._register("component", name, key)
        return st.session_state.get(key, default)
    return component
