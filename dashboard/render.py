"""Render the Hyrox training dashboard to a self-contained HTML file.

Produces a two-panel Plotly figure (training-load model + HRV trend) wrapped in
a lightweight HTML shell with three tabs: Training load, Training plan (Hyrox
session cards with Runna-style banded steps + justifications), and Heart-rate
zones. Plotly.js is loaded from a CDN to keep the committed file small.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from html import escape

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from dashboard import styles, theme
from dashboard import zones as zones_mod
from dashboard.metrics import CTL_WARMUP_DAYS, ReadinessSnapshot
from plan import phase as plan_phase

_COLOR_CTL = theme.COLOR_CTL     # blue   - fitness
_COLOR_ATL = theme.COLOR_ATL     # orange - fatigue
_COLOR_TSB = theme.COLOR_TSB     # green  - form
_COLOR_LOAD = theme.COLOR_LOAD   # grey   - daily load bars
_COLOR_HRV = theme.COLOR_HRV     # violet - nightly HRV
_COLOR_BAND = theme.COLOR_BAND   # HRV baseline band fill

# Periodization strip colours: saturated data marks, identical in both schemes.
_PHASE_COLOR = {
    "base": "#0ea5e9", "build": "#2563eb", "peak": "#f97316",
    "taper": "#16a34a", "off": "#94a3b8",
}
# Status and intensity are tinted surfaces rather than saturated marks, so they
# carry a CSS class instead of an inline colour: an inline style cannot answer
# ``prefers-color-scheme``, and these need a different tint per scheme.
_STATUS_CLASS = {
    "done": "s-done", "partial": "s-partial", "swapped": "s-swapped",
    "missed": "s-missed", "upcoming": "s-upcoming", "rest": "s-rest",
}
# Zone accent dot (easiest -> hardest).
_ZONE_DOT = {
    "Recovery": "#639922", "Endurance": "#97C459", "Aerobic": "#EF9F27",
    "Threshold": "#D85A30", "Maximum": "#E24B4A", "mixed": "#64748b",
}
_INTENSITY_CLASS = {"hard": "i-hard", "moderate": "i-moderate", "easy": "i-easy"}
# Runna-style phase bands (saturated) and per-segment type tags.
_PHASE_BAND = {
    "warmup": ("Warm-up", "#e0683a"),
    "main": ("Main set", "#5a51c9"),
    "cooldown": ("Cool-down", "#14a08a"),
}
_KIND_TAG = {
    "station": ("STATION", "#ba7517"),
    "strength": ("STRENGTH", "#534ab7"),
    "run": ("RUN", "#1d9e75"),
    "rest": ("REST", "#378add"),
    "note": ("", "#94a3b8"),
}

# Curated, vetted bibliography rendered as the static sources panel. Kept here (not
# model-generated) so a citation can never be hallucinated. (claim, citation, tier, url).
_METHODOLOGY_SOURCES = [
    ("Strength training improves endurance/running economy",
     "Llanos-Lagos et al. 2024 — meta-analysis (Sports Medicine)", "strong",
     "https://pubmed.ncbi.nlm.nih.gov/38165636/"),
    ("Concurrent strength + endurance is largely compatible (interference is narrow)",
     "Concurrent training & hypertrophy 2022 — systematic review + meta-analysis", "strong",
     "https://pmc.ncbi.nlm.nih.gov/articles/PMC9474354/"),
    ("Mostly-easy polarized volume beats threshold-heavy blocks for the aerobic base",
     "Rosenblat et al. 2019 — systematic review + meta-analysis of RCTs", "strong",
     "https://pubmed.ncbi.nlm.nih.gov/29863593/"),
    ("A ~2-week taper with volume cut 41-60% maximizes performance",
     "Bosquet et al. 2007 — meta-analysis (Med Sci Sports Exerc)", "strong",
     "https://pubmed.ncbi.nlm.nih.gov/17762369/"),
    ("The ACWR injury 'sweet spot' is statistically contested — build gradually, do not spike",
     "Impellizzeri et al. 2020 — conceptual critique (IJSPP)", "contested",
     "https://journals.humankinetics.com/view/journals/ijspp/15/6/article-p907.xml"),
    ("Hyrox demands aerobic + anaerobic power + economy under fatigue",
     "Acute responses & determinants in Hyrox 2025 (Frontiers) — limited literature", "emerging",
     "https://pmc.ncbi.nlm.nih.gov/articles/PMC11994925/"),
]
_TIER_CLASS = {"strong": "t-strong", "contested": "t-contested", "emerging": "t-emerging"}


def _methodology_sources_html() -> str:
    """Render the static, vetted sources list (claim, citation link, evidence tier)."""
    rows = []
    for claim, cite, tier, url in _METHODOLOGY_SOURCES:
        tier_class = _TIER_CLASS.get(tier, "t-emerging")
        rows.append(
            f"<div class='src'><div class='src-main'><div class='src-claim'>{escape(claim)}</div>"
            f"<a class='src-cite' href='{escape(url)}' target='_blank' rel='noopener'>{escape(cite)}</a></div>"
            f"<span class='tier {tier_class}'>{tier}</span></div>"
        )
    return f"<div class='src-list'>{''.join(rows)}</div>"


# The page's behaviour, kept out of the document f-string so the braces need no
# doubling. ``__CHART_THEME__`` is substituted by :func:`_page_script`.
_PAGE_SCRIPT = """
  document.querySelectorAll('.tab-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      document.querySelectorAll('.tab-btn').forEach(function (b) { b.classList.remove('active'); });
      document.querySelectorAll('.tab-panel').forEach(function (p) { p.classList.remove('active'); });
      btn.classList.add('active');
      document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
      window.dispatchEvent(new Event('resize'));  // let Plotly size the hidden chart
    });
  });

  // Week pills and periodization-strip cells both select a week.
  function selectWeek(wk) {
    var body = document.getElementById('wk-' + wk);
    if (!body) { return; }
    document.querySelectorAll('.wk-body').forEach(function (b) { b.classList.toggle('active', b === body); });
    document.querySelectorAll('.wk-pill').forEach(function (p) { p.classList.toggle('active', p.dataset.week === wk); });
    document.querySelectorAll('.phase-cell').forEach(function (c) {
      var on = c.dataset.week === wk;
      c.classList.toggle('selected', on);
      c.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }
  document.querySelectorAll('.wk-pill, .phase-cell').forEach(function (el) {
    el.addEventListener('click', function () { selectWeek(el.dataset.week); });
  });
  document.querySelectorAll('.sess-row').forEach(function (row) {
    row.addEventListener('click', function () {
      row.classList.toggle('open');
      document.getElementById('psess' + row.dataset.sess).classList.toggle('open');
    });
  });

  // Plotly bakes its colours into the document at build time, so the figures are
  // recoloured here to match the scheme the browser actually resolved, and again
  // whenever the OS flips it. Axis keys are read off each figure rather than
  // assumed, so a subplot gains no phantom axes.
  var CHART_THEME = __CHART_THEME__;
  var darkQuery = window.matchMedia('(prefers-color-scheme: dark)');
  var AXIS_KEY = /^[xy]axis[0-9]*$/;

  function applyChartTheme() {
    var t = CHART_THEME[darkQuery.matches ? 'dark' : 'light'];
    document.querySelectorAll('.js-plotly-plot').forEach(function (gd) {
      if (!gd.layout) { return; }
      var update = {};
      Object.keys(t.figure).forEach(function (k) { update[k] = t.figure[k]; });
      Object.keys(gd.layout).forEach(function (key) {
        if (!AXIS_KEY.test(key)) { return; }
        Object.keys(t.axis).forEach(function (prop) {
          update[key + '.' + prop] = t.axis[prop];
        });
        if (gd.layout[key].rangeslider) {
          update[key + '.rangeslider.bgcolor'] = 'rgba(0,0,0,0)';
          update[key + '.rangeslider.bordercolor'] = t.axis.linecolor;
        }
      });
      Plotly.relayout(gd, update);
    });
  }

  applyChartTheme();
  darkQuery.addEventListener('change', applyChartTheme);

  // Retire the entrance animation once it has played, so switching tabs shows
  // its data immediately instead of replaying a stagger.
  window.setTimeout(function () { document.body.classList.remove('intro'); }, 900);
"""


def _page_script() -> str:
    """Return the page's JavaScript with the chart palettes substituted in.

    Returns
    -------
    str
        Script body for the document's single inline ``<script>`` element.
    """
    return _PAGE_SCRIPT.replace("__CHART_THEME__", theme.chart_theme_js())


@dataclass(frozen=True)
class PlanView:
    """View model for the training-plan section.

    Holds every generated week for the current race so the dashboard can switch
    between them, rather than only the most recent one.

    Parameters
    ----------
    weeks : list of dict
        ``training_plan_weeks`` rows, oldest first. Empty when no plan exists yet.
    sessions_by_week : dict
        Maps each week's ISO ``week_start`` to its sessions, with a ``status``
        column.
    zones : pandas.DataFrame
        Heart-rate training zones.
    selected_week : str or None
        ISO ``week_start`` shown on load; defaults to the week containing today,
        falling back to the last week before today, then the first week.
    """

    weeks: list[dict]
    sessions_by_week: dict[str, pd.DataFrame]
    zones: pd.DataFrame
    selected_week: str | None = None

    @property
    def week(self) -> dict | None:
        """The selected week's header row, or ``None`` when no plan exists."""
        if not self.weeks:
            return None
        for week in self.weeks:
            if week["week_start"] == self.selected_week:
                return week
        return self.weeks[0]


def default_selected_week(weeks: list[dict], today: date | None = None) -> str | None:
    """Pick the week the plan tab should open on.

    Prefers the week containing ``today``; if today sits outside every planned
    week, falls back to the most recent past week, then to the first week.

    Parameters
    ----------
    weeks : list of dict
        Plan-week rows ordered by ``week_start``.
    today : datetime.date, optional
        Reference date; defaults to today.

    Returns
    -------
    str or None
        ISO ``week_start`` to select, or ``None`` when there are no weeks.
    """
    if not weeks:
        return None

    reference = today or date.today()
    past: str | None = None
    for week in weeks:
        start = date.fromisoformat(week["week_start"])
        if start <= reference <= start + timedelta(days=6):
            return week["week_start"]
        if start < reference:
            past = week["week_start"]
    return past or weeks[0]["week_start"]


def build_figure(load_series: pd.DataFrame, hrv_series: pd.DataFrame) -> go.Figure:
    """Build the two-panel training/recovery figure.

    Parameters
    ----------
    load_series : pandas.DataFrame
        Output of :func:`dashboard.metrics.build_load_series`.
    hrv_series : pandas.DataFrame
        Output of :func:`dashboard.metrics.build_hrv_series`.

    Returns
    -------
    plotly.graph_objects.Figure
        Figure with a load panel (row 1) and an HRV panel (row 2).
    """
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.09,
        row_heights=[0.62, 0.38],
        specs=[[{"secondary_y": True}], [{"secondary_y": False}]],
        subplot_titles=("Training load: fitness, fatigue & form", "HRV vs baseline"),
    )

    # --- Row 1: daily load bars + CTL/ATL lines (primary), TSB (secondary) ---
    fig.add_trace(
        go.Bar(
            x=load_series.index,
            y=load_series["load"],
            name="Daily load",
            marker_color=_COLOR_LOAD,
            opacity=0.7,
            hovertemplate="%{x|%a %d %b}<br>Load: %{y:.0f}<extra></extra>",
        ),
        row=1,
        col=1,
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=load_series.index,
            y=load_series["ctl"],
            name="CTL (fitness)",
            line=dict(color=_COLOR_CTL, width=2.5),
            hovertemplate="%{x|%a %d %b}<br>CTL: %{y:.1f}<extra></extra>",
        ),
        row=1,
        col=1,
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=load_series.index,
            y=load_series["atl"],
            name="ATL (fatigue)",
            line=dict(color=_COLOR_ATL, width=1.8),
            hovertemplate="%{x|%a %d %b}<br>ATL: %{y:.1f}<extra></extra>",
        ),
        row=1,
        col=1,
        secondary_y=False,
    )
    # TSB is unreliable during the CTL warm-up (CTL seeds from zero), so mask
    # that window to NaN: the line simply starts once the value is meaningful.
    tsb_display = load_series["tsb"].copy()
    if len(tsb_display) > CTL_WARMUP_DAYS:
        tsb_display.iloc[:CTL_WARMUP_DAYS] = float("nan")
    fig.add_trace(
        go.Scatter(
            x=load_series.index,
            y=tsb_display,
            name="TSB (form)",
            line=dict(color=_COLOR_TSB, width=1.6, dash="dot"),
            connectgaps=False,
            hovertemplate="%{x|%a %d %b}<br>TSB: %{y:.1f}<extra></extra>",
        ),
        row=1,
        col=1,
        secondary_y=True,
    )

    # Shade the CTL warm-up window where TSB is unreliable.
    if len(load_series) > 0:
        warmup_end = load_series.index[min(CTL_WARMUP_DAYS, len(load_series) - 1)]
        fig.add_vrect(
            x0=load_series.index[0],
            x1=warmup_end,
            fillcolor="rgba(122, 128, 139, 0.12)",
            line_width=0,
            row=1,
            col=1,
            annotation_text="warm-up (TSB unreliable)",
            annotation_position="top left",
            annotation_font_size=10,
        )

    # Zero reference for TSB on the secondary axis.
    fig.add_hline(y=0, line=dict(color=theme.COLOR_REFERENCE, width=1, dash="dash"),
                  row=1, col=1, secondary_y=True)

    # --- Row 2: HRV nightly + baseline band ---
    if not hrv_series.empty:
        fig.add_trace(
            go.Scatter(
                x=hrv_series.index,
                y=hrv_series["baseline_high"],
                name="Baseline high",
                line=dict(width=0),
                showlegend=False,
                hoverinfo="skip",
            ),
            row=2,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=hrv_series.index,
                y=hrv_series["baseline_low"],
                name="Baseline band",
                fill="tonexty",
                fillcolor=_COLOR_BAND,
                line=dict(width=0),
                hoverinfo="skip",
            ),
            row=2,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=hrv_series.index,
                y=hrv_series["hrv_night"],
                name="HRV (last night)",
                mode="lines+markers",
                line=dict(color=_COLOR_HRV, width=2),
                marker=dict(size=5),
                hovertemplate="%{x|%a %d %b}<br>HRV: %{y:.0f} ms<extra></extra>",
            ),
            row=2,
            col=1,
        )

    fig.update_yaxes(title_text="Load / CTL / ATL", row=1, col=1, secondary_y=False)
    fig.update_yaxes(title_text="TSB", row=1, col=1, secondary_y=True)
    fig.update_yaxes(title_text="HRV (ms)", row=2, col=1)
    fig.update_xaxes(rangeslider=dict(visible=True, thickness=0.05), row=2, col=1)

    fig.update_layout(
        **theme.chart_layout(
            height=760,
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.09, xanchor="left", x=0),
            margin=dict(l=60, r=30, t=100, b=30),
            barmode="overlay",
        )
    )
    fig.update_annotations(font=dict(family=theme.FONT_SANS, size=13))
    return fig


# Days the newest data may lag before the dashboard warns it cannot be trusted.
# Mirrors ingest.run.MAX_DATA_AGE_DAYS: Garmin can lag a day, more means broken.
STALE_AFTER_DAYS = 2


def _stale_banner(as_of: date, today: date | None = None) -> str:
    """Render a warning banner when the underlying data has stopped updating.

    Every metric on the page is derived from the newest data in the database, so
    a stalled pipeline does not look like an error — it looks like an athlete
    who stopped training, and reads as misleadingly *good* form. This makes the
    staleness impossible to miss instead of leaving it implicit in the date.

    Parameters
    ----------
    as_of : datetime.date
        Date of the newest data point behind the snapshot.
    today : datetime.date, optional
        Reference date; defaults to the current date.

    Returns
    -------
    str
        An HTML fragment, or an empty string when the data is fresh.

    Examples
    --------
    >>> _stale_banner(date(2026, 8, 9), today=date(2026, 8, 10))
    ''
    >>> _stale_banner(date(2026, 7, 20), today=date(2026, 8, 9)).startswith('<div')
    True
    """
    age = ((today or date.today()) - as_of).days
    if age <= STALE_AFTER_DAYS:
        return ""
    return (
        f'<div class="stale"><strong>Data is {age} days old.</strong> The newest '
        f"reading is from {as_of:%d %B %Y}, so form, ACWR and load below are stale "
        "and will read as falsely fresh. Ingestion has most likely stopped — check "
        "the daily workflow and re-seed the Garmin token if needed.</div>"
    )


def _snapshot_cards(snapshot: ReadinessSnapshot) -> str:
    """Render the header cards (CTL/ATL/TSB/HRV) as an HTML fragment."""
    hrv_text = (
        f"{snapshot.hrv_night:.0f} ms" if snapshot.hrv_night is not None else "n/a"
    )
    hrv_status = snapshot.hrv_status or "n/a"
    cards = [
        ("Fitness (CTL)", f"{snapshot.ctl:.1f}", "42-day load average"),
        ("Fatigue (ATL)", f"{snapshot.atl:.1f}", "7-day load average"),
        ("Form (TSB)", f"{snapshot.tsb:+.1f}", snapshot.tsb_label),
        ("HRV last night", hrv_text, f"status: {hrv_status}"),
    ]
    items = "".join(
        f"""
        <div class="card">
            <div class="card-label">{label}</div>
            <div class="card-value">{value}</div>
            <div class="card-sub">{sub}</div>
        </div>"""
        for label, value, sub in cards
    )
    return f'<div class="cards">{items}</div>'


def _weekly_table(weekly: pd.DataFrame) -> str:
    """Render the weekly summary DataFrame as an HTML table fragment."""
    if weekly.empty:
        return "<p>No weekly data yet.</p>"

    header = (
        "<tr><th>Week of</th><th>Total load</th><th>Training days</th>"
        "<th>Form (TSB)</th><th>HRV status</th></tr>"
    )
    body = "".join(
        f"<tr><td>{r.week_start}</td><td>{r.total_load:.0f}</td>"
        f"<td>{r.training_days}</td><td>{r.end_tsb:+.1f}</td>"
        f"<td>{r.end_hrv_status or '-'}</td></tr>"
        for r in weekly.itertuples()
    )
    return f"<table class='weekly'>{header}{body}</table>"


def _phase_strip(race_date: date, current_week_start: date, start_week: date | None = None) -> str:
    """Render a colored week-by-week periodization strip up to the race.

    The strip always spans the whole remaining block (from ``start_week``), while
    ``current_week_start`` is the cell drawn with the selection ring — so the
    strip stays stable as the reader switches weeks.
    """
    cells = []
    week = start_week or current_week_start
    guard = 0
    while week <= race_date and guard < 60:
        phase_name, _ = plan_phase.phase_for_week(week, race_date)
        color = _PHASE_COLOR.get(phase_name, "#94a3b8")
        selected = week == current_week_start
        cells.append(
            f'<button type="button" class="phase-cell{" selected" if selected else ""}" '
            f'data-week="{week.isoformat()}" aria-pressed="{"true" if selected else "false"}">'
            f'<div class="phase-dot" style="background:{color}"></div>'
            f'<div class="phase-wk">{week.strftime("%d %b")}</div>'
            f'<div class="phase-name">{phase_name}</div></button>'
        )
        week += timedelta(days=7)
        guard += 1
    return f'<div class="phase-strip">{"".join(cells)}</div>'


def _session_steps_html(presc: dict) -> str:
    """Render a session body as Runna-style banded segment rows.

    Typed segments (with ``metric``) render as phase bands + typed rows. Empty
    steps fall back to the one-line prescription detail.
    """
    steps = [s for s in (presc.get("steps") or []) if isinstance(s, dict)]
    typed = [s for s in steps if s.get("metric")]

    if not typed:
        detail = escape(str(presc.get("detail", "") or ""))
        return f"<div class='sess-step'><span class='sk'>Session</span><span class='sv'>{detail}</span></div>"

    out: list[str] = []
    current_phase = "__start__"
    num = 0
    for s in typed:
        phase = s.get("phase")
        if phase != current_phase:
            current_phase = phase
            if phase in _PHASE_BAND:
                label, color = _PHASE_BAND[phase]
                out.append(f"<div class='seg-band' style='background:{color}'>{escape(label)}</div>")
        num += 1
        tag_text, tag_color = _KIND_TAG.get(s.get("kind", "note"), ("", "#94a3b8"))
        metric = escape(str(s.get("metric", "") or ""))
        load = s.get("load")
        load_html = f" <span class='seg-load'>@ {escape(str(load))}</span>" if load else ""
        target = s.get("target")
        target_html = f"<div class='seg-target'>{escape(str(target))}</div>" if target else ""
        tag_html = f"<span class='seg-tag' style='color:{tag_color}'>{tag_text}</span>" if tag_text else ""
        out.append(
            f"<div class='seg-row'><span class='seg-num'>{num}</span>"
            f"<div class='seg-main'><div class='seg-metric'><b>{metric}</b>{load_html}</div>"
            f"{target_html}</div>{tag_html}</div>"
        )
    return f"<div class='seg-list'>{''.join(out)}</div>"


def _plan_list(sessions: pd.DataFrame, key: str = "") -> str:
    """Render the week's sessions as an expandable list of structured cards.

    Parameters
    ----------
    sessions : pandas.DataFrame
        Planned sessions for one week, with a ``status`` column.
    key : str, optional
        Unique prefix for the expand/collapse element ids. Required when several
        weeks are rendered into the same document, or their ids would collide.
    """
    if sessions.empty:
        return "<p>No sessions for this week.</p>"

    items = []
    for i, r in enumerate(sessions.sort_values("session_date").itertuples()):
        day = pd.to_datetime(r.session_date).strftime("%a %d %b")
        presc = r.prescription if isinstance(r.prescription, dict) else {}
        dist, dur = presc.get("distance_m"), presc.get("duration_min")

        meta = [day]
        if r.zone:
            meta.append(escape(str(r.zone)))
        if dist:
            meta.append(f"{dist} m")
        if dur:
            meta.append(f"{dur} min")

        focus = getattr(r, "hyrox_focus", None)
        focus_html = f"<span class='focus'>{escape(str(focus))}</span>" if focus else ""
        dot = _ZONE_DOT.get(r.zone, "#94a3b8")
        intensity_class = _INTENSITY_CLASS.get(r.intensity, "i-none")
        status = getattr(r, "status", "upcoming")
        status_class = _STATUS_CLASS.get(status, "s-upcoming")

        why = str(presc.get("why", "") or "")
        why_html = (
            f"<div class='sess-why'><span class='why-label'>Why this, not more</span>"
            f"{escape(why)}</div>" if why else ""
        )

        sess_id = f"{key}{i}"
        items.append(
            f"<div class='sess'>"
            f"<div class='sess-row' data-sess='{sess_id}'>"
            f"<span class='zdot' style='background:{dot}'></span>"
            f"<div class='sess-main'>"
            f"<div class='sess-title'>{escape(str(r.title or ''))}{focus_html}</div>"
            f"<div class='sess-meta'>{' · '.join(meta)}</div></div>"
            f"<span class='ibadge {intensity_class}'>{escape(str(r.intensity or ''))}</span>"
            f"<span class='sdot {status_class}' title='{status}'></span>"
            f"<span class='chev'>&#9662;</span>"
            f"</div>"
            f"<div class='sess-body' id='psess{sess_id}'>{_session_steps_html(presc)}"
            f"<div class='sess-purpose'>{escape(str(r.purpose or ''))}</div>{why_html}</div>"
            f"</div>"
        )
    return f"<div class='sess-list'>{''.join(items)}</div>"


def _week_pills(weeks: list[dict], selected: str, race_date: date) -> str:
    """Render the week selector as a row of pills, one per generated week."""
    pills = []
    for i, week in enumerate(weeks, start=1):
        week_start = date.fromisoformat(week["week_start"])
        is_race_week = week_start <= race_date <= week_start + timedelta(days=6)
        label = "Race week" if is_race_week else f"Week {i}"
        active = " active" if week["week_start"] == selected else ""
        color = _PHASE_COLOR.get(week["phase"], "#94a3b8")
        pills.append(
            f'<button class="wk-pill{active}" data-week="{escape(week["week_start"])}">'
            f'<span class="wk-dot" style="background:{color}"></span>'
            f'<span class="wk-label">{label}</span>'
            f'<span class="wk-date">{week_start.strftime("%d %b")}</span></button>'
        )
    return f'<div class="wk-pills">{"".join(pills)}</div>'


def _week_body(plan: PlanView, week: dict, selected: str) -> str:
    """Render one week's cards, phase strip, sessions and coach's note."""
    race_date = date.fromisoformat(week["race_date"])
    week_start = date.fromisoformat(week["week_start"])
    block_start = date.fromisoformat(plan.weeks[0]["week_start"])
    days_to = (race_date - date.today()).days
    sessions = plan.sessions_by_week.get(week["week_start"], pd.DataFrame())

    countdown = f"{days_to}d" if days_to >= 0 else "done"
    countdown_sub = "until race day" if days_to >= 0 else "race day passed"
    remaining = week["weeks_to_race"]
    weeks_sub = f"{remaining} week{'' if remaining == 1 else 's'} to race"
    cards = [
        ("Target race", week["target_race"].upper(), race_date.strftime("%d %b %Y")),
        ("Countdown", countdown, countdown_sub),
        ("Phase", week["phase"].title(), weeks_sub),
        (
            "Load target",
            f"{int(week['load_target_low'])}–{int(week['load_target_high'])}",
            "weekly load band",
        ),
    ]
    card_html = "".join(
        f'<div class="card"><div class="card-label">{label}</div>'
        f'<div class="card-value">{value}</div><div class="card-sub">{sub}</div></div>'
        for label, value, sub in cards
    )
    rationale = escape(str(week.get("rationale") or ""))
    active = " active" if week["week_start"] == selected else ""

    return f"""<div class="wk-body{active}" id="wk-{escape(week['week_start'])}">
    <div class="cards">{card_html}</div>
    <div class="panel">
      <div class="section-label">Periodization</div>
      {_phase_strip(race_date, week_start, start_week=block_start)}
    </div>
    <div class="panel">
      <div class="section-label">Week of {week_start.strftime('%d %b %Y')}</div>
      <p class="plan-hint">Hyrox-specific sessions only — running follows her own plan. Click a session to expand.</p>
      {_plan_list(sessions, key=f"{week['week_start']}-")}
      <p class="rationale"><b>Coach's note:</b> {rationale}</p>
    </div>
  </div>"""


def _plan_section(plan: PlanView) -> str:
    """Render the full training-plan section (week switcher, weeks, methodology, zones)."""
    if plan is None or not plan.weeks:
        return (
            "<h2>Training plan</h2><div class='panel'><p>No plan generated yet — "
            "run <code>python -m plan.context</code>, write the week JSON, then "
            "<code>python -m plan.persist</code>.</p></div>"
        )

    selected = plan.selected_week or plan.weeks[0]["week_start"]
    race_date = date.fromisoformat(plan.weeks[0]["race_date"])

    # The methodology note is week-level but near-identical across a block; show
    # the selected week's so it stays in step with what is being read.
    week = plan.week or plan.weeks[0]
    model = escape(str(week.get("model") or ""))
    methodology = escape(str(week.get("methodology") or ""))
    methodology_html = f'<p class="methodology">{methodology}</p>' if methodology else ""

    zone_source = ""
    if not plan.zones.empty and "source" in plan.zones.columns:
        src = str(plan.zones.iloc[0].get("source") or "")
        zone_source = " Estimated from max HR." if src == "max_hr_estimate" else " From Garmin's configured zones."

    bodies = "".join(_week_body(plan, w, selected) for w in plan.weeks)

    return f"""<h2>Training plan</h2>
  {_week_pills(plan.weeks, selected, race_date)}
  {bodies}
  <div class="panel">
    <div class="section-label">Methodology &amp; sources</div>
    {methodology_html}
    {_methodology_sources_html()}
    <p class="src-note">Evidence tiers: strong = meta-analysis / RCT review · contested =
    methodologically debated · emerging = limited or practice-derived.</p>
  </div>
  <div class="panel">
    <div class="section-label">Heart-rate zones</div>
    {zones_mod.zone_table_html(plan.zones)}
    <p class="zone-note">Heart-rate zones used for conditioning intensity.{zone_source} Generated by {model}.</p>
  </div>"""


def render_html(
    fig: go.Figure,
    snapshot: ReadinessSnapshot,
    weekly: pd.DataFrame,
    zones_fig: go.Figure,
    zones_df: pd.DataFrame,
    plan: PlanView | None = None,
) -> str:
    """Assemble the full HTML document.

    Parameters
    ----------
    fig : plotly.graph_objects.Figure
        The two-panel figure from :func:`build_figure`.
    snapshot : ReadinessSnapshot
        Current-state snapshot for the header cards.
    weekly : pandas.DataFrame
        Weekly summary from :func:`dashboard.metrics.weekly_summary`.
    zones_fig : plotly.graph_objects.Figure
        The heart-rate zone band chart from :func:`dashboard.zones.build_zone_figure`.
    zones_df : pandas.DataFrame
        Heart-rate zones from ``training_zones`` (for the zones table).
    plan : PlanView or None, optional
        Training-plan view model; when ``None`` the section is omitted.

    Returns
    -------
    str
        A complete, self-contained HTML document.
    """
    chart_html = fig.to_html(full_html=False, include_plotlyjs="cdn")
    # plotly.js is already loaded by the chart above; don't ship it twice.
    zones_chart_html = zones_fig.to_html(full_html=False, include_plotlyjs=False)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    as_of = snapshot.date.strftime("%A, %d %B %Y")
    plan_html = _plan_section(plan) if plan is not None else ""
    stale_html = _stale_banner(snapshot.date.date())

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>Hyrox Training</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{theme.GOOGLE_FONTS_HREF}" crossorigin>
<style>{styles.stylesheet()}</style>
</head>
<body class="intro">
<div class="wrap">
  <header class="masthead">
    <h1>Hyrox Training</h1>
    <div class="as-of">As of {as_of}</div>
  </header>
  {stale_html}

  <div class="tabs">
    <button class="tab-btn active" data-tab="training">Training load</button>
    <button class="tab-btn" data-tab="plan">Training plan</button>
    <button class="tab-btn" data-tab="zones">Zones</button>
  </div>

  <div class="tab-panel active" id="tab-training">
    {_snapshot_cards(snapshot)}
    <div class="panel">{chart_html}</div>
    <h2>Weekly summary</h2>
    <div class="panel">{_weekly_table(weekly)}</div>
  </div>

  <div class="tab-panel" id="tab-plan">
    {plan_html}
  </div>

  <div class="tab-panel" id="tab-zones">
    <h2>Heart-rate zones</h2>
    <div class="panel">{zones_chart_html}</div>
    <div class="panel">{zones_mod.zone_table_html(zones_df)}</div>
    <p class="note">Five heart-rate zones sourced from Garmin Connect, used to set
    conditioning intensity alongside RPE. There is no lactate test or running pace
    component — running is handled by her own separate plan.</p>
  </div>

  <footer>Generated {generated} · Garmin training load + HRV · TSB bands follow
  TrainingPeaks conventions. CTL warm-up window shaded; treat early TSB with caution.</footer>
</div>
<script>{_page_script()}</script>
</body>
</html>"""
