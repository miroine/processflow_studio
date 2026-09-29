from .graph_objects import Figure


def make_subplots(rows=1, cols=1, shared_xaxes=False, shared_yaxes=False, subplot_titles=None,
                  vertical_spacing=None, horizontal_spacing=None, row_heights=None, column_widths=None, specs=None):
    if subplot_titles is not None and len(subplot_titles) > rows * cols:
        raise ValueError("too many subplot titles")
    f = Figure()
    f.rows = (rows, cols)
    return f
