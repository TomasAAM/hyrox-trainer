"""Tests for multi-week plan generation, storage routing, and week selection.

These cover the paths that let a whole block of weeks be planned at once: the
week list up to the race, the per-week file naming that tells ``plan.persist``
which week a JSON belongs to, and the dashboard's choice of which week to open.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pandas as pd

from dashboard import render
from plan import context
from plan.config import DEFAULT_CONFIG

_SEP_RACE = replace(DEFAULT_CONFIG, race_date=date(2026, 9, 5), races=())


def test_weeks_until_race_covers_block_to_race_week() -> None:
    weeks = context.weeks_until_race(_SEP_RACE, date(2026, 8, 16))
    assert weeks == [date(2026, 8, 17), date(2026, 8, 24), date(2026, 8, 31)]


def test_weeks_until_race_includes_week_containing_race() -> None:
    """The race-week Monday is included even though the race is mid-week."""
    weeks = context.weeks_until_race(_SEP_RACE, date(2026, 8, 31))
    assert weeks == [date(2026, 8, 31)]


def test_weeks_until_race_empty_once_race_has_passed() -> None:
    assert context.weeks_until_race(_SEP_RACE, date(2026, 9, 7)) == []


def test_plan_file_round_trips_week_start() -> None:
    week = date(2026, 8, 24)
    assert context.week_start_from_path(context.plan_file_for(week)) == week


def test_week_start_from_default_path_is_none() -> None:
    """The un-dated default filename carries no week; persist falls back to config."""
    assert context.week_start_from_path(Path("data/plan_week.json")) is None


def test_week_start_from_malformed_path_is_none() -> None:
    assert context.week_start_from_path(Path("data/plan_week_not-a-date.json")) is None


def _weeks() -> list[dict]:
    return [
        {"week_start": "2026-08-17", "race_date": "2026-09-05", "phase": "peak"},
        {"week_start": "2026-08-24", "race_date": "2026-09-05", "phase": "taper"},
        {"week_start": "2026-08-31", "race_date": "2026-09-05", "phase": "taper"},
    ]


def test_default_selected_week_picks_current_week() -> None:
    assert render.default_selected_week(_weeks(), date(2026, 8, 27)) == "2026-08-24"


def test_default_selected_week_picks_last_day_of_week() -> None:
    """Sunday still belongs to the week that started the preceding Monday."""
    assert render.default_selected_week(_weeks(), date(2026, 8, 23)) == "2026-08-17"


def test_default_selected_week_before_block_picks_first() -> None:
    assert render.default_selected_week(_weeks(), date(2026, 8, 10)) == "2026-08-17"


def test_default_selected_week_after_block_picks_last() -> None:
    assert render.default_selected_week(_weeks(), date(2026, 9, 20)) == "2026-08-31"


def test_default_selected_week_no_weeks() -> None:
    assert render.default_selected_week([], date(2026, 8, 20)) is None


def test_plan_view_week_resolves_selection() -> None:
    view = render.PlanView(
        weeks=_weeks(),
        sessions_by_week={},
        zones=pd.DataFrame(),
        selected_week="2026-08-31",
    )
    assert view.week["phase"] == "taper"
    assert view.week["week_start"] == "2026-08-31"


def test_plan_view_session_ids_are_unique_across_weeks() -> None:
    """Each week's cards get a namespaced id, or expanding one would toggle another."""
    sessions = pd.DataFrame(
        [
            {
                "session_date": "2026-08-18",
                "session_type": "strength",
                "title": "Heavy lower",
                "zone": None,
                "intensity": "hard",
                "prescription": {"detail": "5x3 back squat", "steps": []},
                "purpose": "Max strength",
                "hyrox_focus": "sled",
                "status": "upcoming",
            }
        ]
    )
    first = render._plan_list(sessions, key="2026-08-17-")
    second = render._plan_list(sessions, key="2026-08-24-")
    assert "psess2026-08-17-0" in first
    assert "psess2026-08-24-0" in second
