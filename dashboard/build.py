"""Dashboard build orchestrator.

Pulls data from Supabase, computes the load and HRV metrics, renders the HTML
report, and writes it to ``public/index.html`` for GitHub Pages.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from dashboard import metrics, query, render, zones
from plan.config import DEFAULT_CONFIG

_PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(_PROJECT_ROOT / ".env", override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_OUTPUT_PATH = _PROJECT_ROOT / "public" / "index.html"


def _build_plan_view(supabase, activities, zones_df, scored: bool = True) -> render.PlanView:
    """Assemble the training-plan view model for every week of the current race.

    Reads all generated weeks targeting the configured race date and their
    sessions (scored for adherence against actual activities), so the dashboard
    can switch between them. Weeks belonging to a previous race are left out.
    Degrades gracefully to an empty view when no plan has been generated yet.

    Parameters
    ----------
    supabase : supabase.Client
        Authenticated Supabase client.
    activities : pandas.DataFrame
        Activities used to score session adherence.
    zones_df : pandas.DataFrame
        Heart-rate zones for the plan section.
    scored : bool, optional
        Whether to score adherence against ``activities``. ``False`` leaves every
        session ``upcoming``, for when the activities belong to someone else.

    Returns
    -------
    render.PlanView
        View model consumed by :func:`dashboard.render.render_html`.
    """
    race_date = DEFAULT_CONFIG.race_date.isoformat()
    weeks = [w for w in query.fetch_plan_weeks(supabase) if w.get("race_date") == race_date]
    if not weeks:
        # No plan for this race yet — skip the sessions query (an empty week_start
        # is an invalid date for PostgREST) and render the empty-plan placeholder.
        return render.PlanView(weeks=[], sessions_by_week={}, zones=zones_df)

    week_starts = [w["week_start"] for w in weeks]
    sessions_by_week = {
        week_start: (
            metrics.compute_adherence(sessions, activities)
            if scored
            else sessions.assign(status="upcoming")
        )
        for week_start, sessions in query.fetch_planned_sessions_multi(
            supabase, week_starts
        ).items()
    }
    logger.info("Plan: %d week(s) for race %s", len(weeks), race_date)
    return render.PlanView(
        weeks=weeks,
        sessions_by_week=sessions_by_week,
        zones=zones_df,
        selected_week=render.default_selected_week(weeks),
        taper_weeks=DEFAULT_CONFIG.taper_weeks,
    )


def main() -> None:
    """Build the dashboard HTML and write it to ``public/index.html``."""
    logger.info("Building dashboard")

    supabase = query.get_supabase_client()

    if not DEFAULT_CONFIG.athlete_data_connected:
        # None of the stored Garmin data is hers, so render the plan alone: no
        # load model, no zones, no adherence scoring against someone else's sessions.
        plan_view = _build_plan_view(supabase, None, pd.DataFrame(), scored=False)
        _OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _OUTPUT_PATH.write_text(render.render_plan_only(plan_view), encoding="utf-8")
        logger.info("Plan-only dashboard written to %s", _OUTPUT_PATH)
        return

    activities = query.fetch_activities(supabase)
    hrv_raw = query.fetch_hrv(supabase)
    zones_df = query.fetch_training_zones(supabase)
    logger.info("Fetched %d activities, %d HRV reading rows", len(activities), len(hrv_raw))

    load_series = metrics.build_load_series(activities)
    hrv_series = metrics.build_hrv_series(hrv_raw)

    if load_series.empty:
        logger.warning("No activity load data available; nothing to render")
        return

    weekly = metrics.weekly_summary(load_series, hrv_series)
    snapshot = metrics.latest_snapshot(load_series, hrv_series)

    plan_view = _build_plan_view(supabase, activities, zones_df)

    fig = render.build_figure(load_series, hrv_series)
    zones_fig = zones.build_zone_figure(zones_df)
    html = render.render_html(fig, snapshot, weekly, zones_fig, zones_df, plan=plan_view)

    _OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _OUTPUT_PATH.write_text(html, encoding="utf-8")
    logger.info("Dashboard written to %s", _OUTPUT_PATH)
    logger.info(
        "Snapshot: CTL=%.1f ATL=%.1f TSB=%+.1f (%s) | HRV=%s status=%s",
        snapshot.ctl,
        snapshot.atl,
        snapshot.tsb,
        snapshot.tsb_label,
        snapshot.hrv_night,
        snapshot.hrv_status,
    )


if __name__ == "__main__":
    main()
