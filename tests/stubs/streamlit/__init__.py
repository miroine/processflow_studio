"""Minimal Streamlit stand-in for headless UI smoke tests (Streamlit is not installable here).

It executes the app script top to bottom like Streamlit does, records what was
rendered, enforces unique widget keys, validates common arguments, and lets a test
script press buttons / change widget values between runs (callbacks fire before
the next run, as in Streamlit).
"""
from __future__ import annotations

import re
import types

import pandas as pd


class RerunException(Exception):
    pass


class SessionState(dict):
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError as e:
            raise AttributeError(k) from e

    def __setattr__(self, k, v):
        self[k] = v

    def __delattr__(self, k):
        del self[k]


session_state = SessionState()

# test hooks ------------------------------------------------------------------
HOOK = {"press": set(), "values": {}, "log": [], "keys": set(), "callbacks": {}, "component_calls": [],
        "charts": [], "frames": 0, "errors": [], "texts": []}


def reset_run():
    HOOK["keys"] = set()
    HOOK["log"] = []
    HOOK["component_calls"] = []
    HOOK["charts"] = []
    HOOK["errors"] = []
    HOOK["texts"] = []
    HOOK["callbacks"] = {}


def _register(kind, label, key, extra=""):
    ident = key if key is not None else f"{kind}:{label}:{extra}"
    if ident in HOOK["keys"]:
        raise RuntimeError(f"DuplicateWidgetID: {ident}")
    HOOK["keys"].add(ident)
    HOOK["log"].append((kind, label, key))
    return ident


def _cb(ident, on_change, args, kwargs=None):
    if on_change:
        HOOK["callbacks"][ident] = (on_change, args or (), kwargs or {})


def _check_width(kw):
    w = kw.get("width")
    if w is not None and not (w in ("stretch", "content") or isinstance(w, int)):
        raise ValueError(f"bad width {w!r}")
    if "use_container_width" in kw:
        raise ValueError("use_container_width is deprecated in current Streamlit")


# containers -------------------------------------------------------------------
class _Ctx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __getattr__(self, name):
        return globals()[name]


sidebar = _Ctx()


def columns(spec, **kw):
    n = spec if isinstance(spec, int) else len(spec)
    return [_Ctx() for _ in range(n)]


def tabs(labels):
    assert all(isinstance(x, str) for x in labels)
    return [_Ctx() for _ in labels]


def expander(label, expanded=False, **kw):
    return _Ctx()


def container(**kw):
    return _Ctx()


def spinner(text=""):
    return _Ctx()


# output -------------------------------------------------------------------------
def set_page_config(**kw):
    pass


def _text(*a, **kw):
    HOOK["texts"].append(" ".join(str(x) for x in a))


markdown = caption = info = warning = success = write = subheader = title = header = _text


def error(msg, *a, **kw):
    HOOK["errors"].append(str(msg))
    _text(msg)


def divider():
    pass


def metric(label, value, delta=None, **kw):
    _text(label, value)


def dataframe(data, **kw):
    _check_width(kw)
    if not isinstance(data, (pd.DataFrame, pd.Series)):
        raise TypeError(f"dataframe got {type(data)}")
    data.to_string()   # exercises formatting
    cc = kw.get("column_config") or {}
    for k in cc:
        if isinstance(data, pd.DataFrame) and k not in data.columns:
            raise KeyError(f"column_config for missing column {k}")


def plotly_chart(fig, **kw):
    _check_width(kw)
    import plotly.graph_objects as go
    if not isinstance(fig, go.Figure):
        raise TypeError("plotly_chart expects a Figure")
    key = kw.get("key")
    _register("plotly_chart", "", key, str(id(fig)))
    HOOK["charts"].append(fig)


def download_button(label, data=None, file_name=None, mime=None, key=None, **kw):
    _check_width(kw)
    if not isinstance(data, (str, bytes)):
        raise TypeError(f"download_button data must be str/bytes, got {type(data)}")
    _register("download_button", label, key)
    return False


# widgets -------------------------------------------------------------------------
def _value(ident, default):
    if ident in HOOK["values"]:
        return HOOK["values"].pop(ident)
    return default


def button(label, key=None, on_click=None, args=None, **kw):
    _check_width(kw)
    ident = _register("button", label, key)
    if ident in HOOK["press"] or label in HOOK["press"]:
        HOOK["press"].discard(ident)
        HOOK["press"].discard(label)
        return True
    return False


def toggle(label, value=False, key=None, on_change=None, args=None, **kw):
    ident = _register("toggle", label, key)
    _cb(ident, on_change, args)
    if key is not None and key in session_state:
        return session_state[key]
    v = _value(ident, value)
    if key is not None:
        session_state[key] = v
    return v


def selectbox(label, options, index=0, key=None, format_func=str, on_change=None, args=None, **kw):
    options = list(options)
    ident = _register("selectbox", label, key)
    _cb(ident, on_change, args)
    for o in options:
        format_func(o)
    if options and not (0 <= index < len(options)):
        raise IndexError(f"selectbox {label}: index {index} out of range")
    if key is not None and key in session_state and session_state[key] in options:
        return session_state[key]
    v = _value(ident, options[index] if options else None)
    if key is not None:
        session_state[key] = v
    return v


def radio(label, options, index=0, key=None, on_change=None, args=None, horizontal=False, help=None, **kw):
    options = list(options)
    ident = _register("radio", label, key)
    _cb(ident, on_change, args)
    if key is not None and key in session_state and session_state[key] in options:
        return session_state[key]
    v = _value(ident, options[index])
    if key is not None:
        session_state[key] = v
    return v


def multiselect(label, options, default=None, key=None, format_func=str, **kw):
    options = list(options)
    ident = _register("multiselect", label, key)
    for o in options:
        format_func(o)
    for d in default or []:
        if d not in options:
            raise ValueError(f"multiselect default {d!r} not in options")
    return _value(ident, list(default or []))


_FMT = re.compile(r"^%[0-9.]*[defgiu]$")


def number_input(label, value=None, min_value=None, max_value=None, step=None, format=None, key=None,
                 on_change=None, args=None, help=None, **kw):
    ident = _register("number_input", label, key)
    _cb(ident, on_change, args)
    if format is not None and not _FMT.match(format):
        raise ValueError(f"number_input format {format!r}")
    if value is not None:
        if min_value is not None and value < min_value:
            raise ValueError(f"{label}: value {value} < min {min_value}")
        if max_value is not None and value > max_value:
            raise ValueError(f"{label}: value {value} > max {max_value}")
        numeric = [x for x in (value, min_value, max_value, step) if x is not None]
        if len({type(x) for x in numeric}) > 1:
            raise TypeError(f"{label}: mixed numeric types {[type(x).__name__ for x in numeric]}")
    if key is not None and key in session_state:
        return session_state[key]
    v = _value(ident, value)
    if key is not None:
        session_state[key] = v
    return v


def text_input(label, value="", key=None, on_change=None, args=None, **kw):
    ident = _register("text_input", label, key)
    _cb(ident, on_change, args)
    if key is not None and key in session_state:
        return session_state[key]
    v = _value(ident, value)
    if key is not None:
        session_state[key] = v
    return v


def slider(label, min_value=None, max_value=None, value=None, step=None, key=None, **kw):
    ident = _register("slider", label, key)
    return _value(ident, value)


def file_uploader(label, type=None, key=None, **kw):
    _register("file_uploader", label, key)
    return None


def data_editor(data, key=None, **kw):
    _check_width(kw)
    ident = _register("data_editor", "", key)
    if not isinstance(data, pd.DataFrame):
        raise TypeError("data_editor expects a DataFrame")
    for k in kw.get("disabled", []) or []:
        if k not in data.columns:
            raise KeyError(f"disabled column {k} missing")
    return _value(ident, data.copy())


class column_config:
    @staticmethod
    def NumberColumn(*a, **kw):
        return {"type": "number", **kw}


def cache_data(func=None, **kw):
    if func is None:
        return lambda f: f
    return func


def rerun():
    raise RerunException()


class _Progress:
    def progress(self, value, text=None):
        if not (0.0 <= float(value) <= 1.0):
            raise ValueError("progress value must be in [0, 1]")


def progress(value, text=None):
    if not (0.0 <= float(value) <= 1.0):
        raise ValueError("progress value must be in [0, 1]")
    if text:
        HOOK["texts"].append(str(text))
    return _Progress()
