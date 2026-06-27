"""Heart-rate training zones, sourced from Garmin.

There is no lactate test and no running pace component, so this renders a single
set of five heart-rate zones (her Garmin-configured zones, or a max-HR estimate),
read from the ``training_zones`` table. No Lab/Garmin/Strava comparison and no
pace zones.
"""

from __future__ import annotations

from html import escape

import pandas as pd
import plotly.graph_objects as go

# Common bpm axis for the band chart.
_AXIS_MIN = 90
_AXIS_MAX = 200

# Intensity colour ramp, easiest (Z1) to hardest (Z5).
_ZONE_COLORS = ["#16a34a", "#84cc16", "#eab308", "#f97316", "#dc2626"]


def _clean_int(value) -> int | None:
    """Coerce a possibly-NaN/None numeric to ``int`` or ``None``."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return int(value)


def _bound_text(lo: int | None, hi: int | None) -> str:
    """Format a zone's bpm bounds for display."""
    if lo is None and hi is not None:
        return f"<{hi}"
    if hi is None and lo is not None:
        return f"{lo}+"
    return f"{lo}-{hi}"


def build_zone_figure(zones: pd.DataFrame) -> go.Figure:
    """Build a horizontal band chart of the five heart-rate zones.

    Parameters
    ----------
    zones : pandas.DataFrame
        Rows from ``training_zones`` (zone_index, zone_name, hr_low, hr_high).

    Returns
    -------
    plotly.graph_objects.Figure
    """
    fig = go.Figure()
    if zones.empty:
        fig.update_layout(height=200, template="plotly_white")
        return fig

    ordered = zones.sort_values("zone_index")
    for zi, z in enumerate(ordered.itertuples()):
        lo = _clean_int(z.hr_low)
        hi = _clean_int(z.hr_high)
        lo_draw = lo if lo is not None else _AXIS_MIN
        hi_draw = hi if hi is not None else _AXIS_MAX
        width = hi_draw - lo_draw
        label = f"Z{z.zone_index} {z.zone_name}"
        fig.add_trace(
            go.Bar(
                y=["Heart-rate zones"],
                x=[width],
                base=lo_draw,
                orientation="h",
                marker=dict(color=_ZONE_COLORS[zi % 5], line=dict(color="white", width=1.5)),
                text=f"{label}<br>{_bound_text(lo, hi)}" if width >= 10 else "",
                textposition="inside",
                insidetextanchor="middle",
                textfont=dict(size=10, color="white"),
                showlegend=False,
                hovertemplate=f"{label}: {_bound_text(lo, hi)} bpm<extra></extra>",
            )
        )

    fig.update_layout(
        barmode="overlay",
        height=200,
        template="plotly_white",
        margin=dict(l=30, r=30, t=20, b=40),
        xaxis=dict(title="Heart rate (bpm)", range=[_AXIS_MIN, _AXIS_MAX]),
        yaxis=dict(showticklabels=False),
    )
    return fig


def zone_table_html(zones: pd.DataFrame) -> str:
    """Render the heart-rate zones as a simple HTML table."""
    if zones.empty:
        return "<p>No zones yet — run <code>python -m plan.zones</code>.</p>"
    header = "<tr><th>Zone</th><th>Heart rate</th></tr>"
    rows = ""
    for z in zones.sort_values("zone_index").itertuples():
        lo, hi = _clean_int(z.hr_low), _clean_int(z.hr_high)
        rows += (
            f"<tr><td>Z{z.zone_index} {escape(str(z.zone_name))}</td>"
            f"<td>{_bound_text(lo, hi)} bpm</td></tr>"
        )
    return f"<table class='zones'>{header}{rows}</table>"
