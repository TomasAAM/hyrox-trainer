"""Plan-only mode: nothing derived from another person's Garmin data is shown or stored."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from dashboard import render
from plan import persist
from plan.config import DEFAULT_CONFIG


def _week(**overrides) -> dict:
    week = {
        "week_start": "2026-10-26",
        "race_date": "2026-11-14",
        "target_race": "hyrox",
        "phase": "peak",
        "weeks_to_race": 3,
        "load_target_low": None,
        "load_target_high": None,
        "rationale": "Conservative ramp.",
        "methodology": "Taper principle.",
        "model": "claude-code",
    }
    week.update(overrides)
    return week


def _sessions() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "session_date": "2026-10-26",
                "session_type": "strength",
                "title": "Upper strength",
                "zone": None,
                "intensity": "moderate",
                "prescription": {"detail": "Pull-ups", "steps": []},
                "purpose": "Pulling strength",
                "hyrox_focus": "pulling",
                "status": "upcoming",
            }
        ]
    )


def test_load_target_card_shows_a_gap_when_no_band_is_stored() -> None:
    label, value, sub = render._load_target_card(_week())
    assert (label, value) == ("Load target", "n/a")
    assert "no load data" in sub


def test_load_target_card_shows_the_band_when_stored() -> None:
    card = render._load_target_card(_week(load_target_low=377.0, load_target_high=461.0))
    assert card[1] == "377–461"


def test_plan_only_page_omits_every_garmin_derived_section() -> None:
    view = render.PlanView(
        weeks=[_week()],
        sessions_by_week={"2026-10-26": _sessions()},
        zones=pd.DataFrame(),
        selected_week="2026-10-26",
    )
    html = render.render_plan_only(view)
    assert "Training plan" in html
    assert 'data-tab="training"' not in html
    assert 'data-tab="zones"' not in html
    assert "plotly-graph-div" not in html
    assert "cdn.plot.ly" not in html
    assert "Heart-rate zones" not in html


class _FakeTable:
    def __init__(self, store: dict, name: str) -> None:
        self._store, self._name = store, name

    def upsert(self, row, **_kwargs):
        self._store[self._name] = row
        return self

    def delete(self):
        return self

    def eq(self, *_args):
        return self

    def execute(self):
        return None


def _run_persist(monkeypatch, tmp_path: Path, connected: bool) -> dict:
    plan_file = tmp_path / "plan_week_2026-10-26.json"
    plan_file.write_text(
        '{"rationale": "r", "methodology": "m", "sessions": []}', encoding="utf-8"
    )
    bundle = SimpleNamespace(
        week_start=date(2026, 10, 26),
        phase_name="peak",
        weeks_remaining=3,
        load_band=(685.0, 838.0),
        summary={"ctl_fitness": 108.8},
    )
    store: dict = {}
    monkeypatch.setattr(persist.context, "gather", lambda *_a, **_k: bundle)
    monkeypatch.setattr(
        persist.query,
        "get_supabase_client",
        lambda: SimpleNamespace(table=lambda name: _FakeTable(store, name)),
    )
    persist.persist(plan_file, cfg=replace(DEFAULT_CONFIG, athlete_data_connected=connected))
    return store["training_plan_weeks"]


def test_persist_drops_load_band_and_summary_without_athlete_data(monkeypatch, tmp_path) -> None:
    row = _run_persist(monkeypatch, tmp_path, connected=False)
    assert row["load_target_low"] is None and row["load_target_high"] is None
    assert "ctl_fitness" not in row["input_summary"]


def test_persist_keeps_load_band_and_summary_with_athlete_data(monkeypatch, tmp_path) -> None:
    row = _run_persist(monkeypatch, tmp_path, connected=True)
    assert (row["load_target_low"], row["load_target_high"]) == (685.0, 838.0)
    assert row["input_summary"] == {"ctl_fitness": 108.8}
