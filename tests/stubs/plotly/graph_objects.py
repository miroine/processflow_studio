"""Plotly stand-in that validates the trace/layout property names the app uses.

The allowlists mirror real plotly.graph_objects property names, so a typo in the
app (e.g. `hovertemplates`) fails here the way it would fail in Plotly.
"""

_ALLOWED = {
    "Scatter": {"x", "y", "mode", "name", "line", "marker", "text", "textposition", "yaxis", "xaxis", "showlegend",
                "hoverinfo", "hovertemplate", "fill", "fillcolor", "legendgroup", "customdata", "textfont",
                "connectgaps", "opacity", "legendrank"},
    "Bar": {"x", "y", "marker", "text", "textposition", "name", "showlegend", "orientation", "hovertemplate",
            "customdata", "legendgroup", "texttemplate", "base", "width", "textfont", "cliponaxis"},
    "Contour": {"x", "y", "z", "contours", "line", "showscale", "name", "hoverinfo", "showlegend", "colorscale"},
    "Heatmap": {"x", "y", "z", "colorscale", "showscale", "hoverinfo", "name", "zmin", "zmax"},
    "Sankey": {"node", "link", "arrangement", "valueformat", "valuesuffix", "orientation"},
}
_NESTED = {
    "line": {"color", "width", "dash", "shape"},
    "marker": {"size", "color", "line", "symbol", "cornerradius", "pattern"},
    "contours": {"start", "end", "size", "coloring", "showlabels", "labelfont"},
    "node": {"label", "color", "pad", "thickness", "line", "customdata", "hovertemplate", "x", "y"},
    "link": {"source", "target", "value", "color", "label", "customdata", "hovertemplate"},
}
_LAYOUT = {"title", "xaxis", "yaxis", "xaxis2", "yaxis2", "xaxis3", "yaxis3", "yaxis4", "xaxis4", "template",
           "margin", "height", "font", "legend", "showlegend", "colorway", "paper_bgcolor", "plot_bgcolor",
           "hovermode", "hoverlabel", "annotations", "shapes", "barmode", "bargap", "bargroupgap", "uniformtext"}
_AXIS = {"title", "gridcolor", "zeroline", "linecolor", "ticks", "showline", "type", "autorange", "range",
         "ticksuffix", "tickformat", "showgrid", "mirror", "tickfont", "matches", "dtick", "categoryorder",
         "automargin", "title_text", "showspikes", "spikemode", "tickcolor", "zerolinecolor"}
_TEXTPOS = {"top center", "outside", "inside", "auto", "bottom center", "middle right", "middle left", "top right",
            "top left", "bottom right", "none"}
_MODES = {"lines", "markers", "lines+markers", "markers+text", "lines+markers+text", "text", "lines+text"}


def _check(kind, kw):
    for k, v in kw.items():
        base = k.split("_")[0]
        if k not in _ALLOWED[kind] and base not in _ALLOWED[kind]:
            raise ValueError(f"{kind}: invalid property {k!r}")
        if isinstance(v, dict) and k in _NESTED:
            for kk in v:
                if kk not in _NESTED[k]:
                    raise ValueError(f"{kind}.{k}: invalid property {kk!r}")
    if "mode" in kw and kw["mode"] not in _MODES:
        raise ValueError(f"bad mode {kw['mode']}")
    if "textposition" in kw and isinstance(kw["textposition"], str) and kw["textposition"] not in _TEXTPOS:
        raise ValueError(f"bad textposition {kw['textposition']}")
    if kind == "Sankey":
        link = kw.get("link", {})
        n = len(kw.get("node", {}).get("label", []))
        for key in ("source", "target"):
            if any(not (0 <= i < n) for i in link.get(key, [])):
                raise ValueError("Sankey link index out of range")
        if len({len(link.get(k, [])) for k in ("source", "target", "value")}) > 1:
            raise ValueError("Sankey link arrays differ in length")
    for axis in ("x", "y", "z"):
        if axis in kw and kw[axis] is not None and not hasattr(kw[axis], "shape"):
            list(kw[axis])


class _Trace:
    def __init__(self, **kw):
        _check(type(self).__name__, kw)
        self.kw = kw


class Scatter(_Trace):
    pass


class Bar(_Trace):
    pass


class Contour(_Trace):
    pass


class Heatmap(_Trace):
    pass


class Sankey(_Trace):
    pass


def _check_layout(kw):
    for k, v in kw.items():
        if k not in _LAYOUT and k.split("_")[0] not in _LAYOUT:
            raise ValueError(f"layout: invalid property {k!r}")
        if k.startswith(("xaxis", "yaxis")) and isinstance(v, dict):
            for kk in v:
                if kk not in _AXIS and kk not in ("side", "overlaying"):
                    raise ValueError(f"layout.{k}: invalid property {kk!r}")


class Figure:
    def __init__(self, data=None, layout=None, **kw):
        self.data = []
        self.layout = {}
        self.rows = None
        if data is not None:
            for t in (data if isinstance(data, (list, tuple)) else [data]):
                self.add_trace(t)
        if layout:
            self.update_layout(**layout)

    def add_trace(self, t, row=None, col=None, secondary_y=None):
        if not isinstance(t, _Trace):
            raise TypeError("add_trace expects a trace")
        if secondary_y:
            raise ValueError("secondary_y is not allowed in this app (single-axis rule)")
        if self.rows is not None:
            if row is None or col is None or not (1 <= row <= self.rows[0] and 1 <= col <= self.rows[1]):
                raise ValueError(f"subplot trace needs a valid row/col, got {row}, {col}")
        elif row is not None:
            raise ValueError("row/col given on a figure without subplots")
        self.data.append(t)

    def update_layout(self, **kw):
        _check_layout(kw)
        if "yaxis2" in kw and isinstance(kw["yaxis2"], dict) and "overlaying" in kw["yaxis2"]:
            raise ValueError("dual y-axis (overlaying) is not allowed in this app")
        self.layout.update(kw)

    def update_xaxes(self, row=None, col=None, **kw):
        for k in kw:
            if k not in _AXIS:
                raise ValueError(f"xaxes: invalid property {k!r}")

    def update_yaxes(self, row=None, col=None, **kw):
        for k in kw:
            if k not in _AXIS:
                raise ValueError(f"yaxes: invalid property {k!r}")

    def add_annotation(self, **kw):
        self.layout.setdefault("annotations_list", []).append(kw)

    def add_hline(self, y=None, **kw):
        self.layout.setdefault("hlines", []).append(y)

    def add_vline(self, x=None, **kw):
        self.layout.setdefault("vlines", []).append(x)
