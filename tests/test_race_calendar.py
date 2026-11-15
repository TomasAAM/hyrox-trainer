"""Tests for a block with several races: phases, week list, storage and rendering."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from dashboard import render
from plan import context, persist, phase
from plan.config import DEFAULT_CONFIG, HYBRID_MONKEY_VI, RACE_CALENDAR

_CFG = DEFAULT_CONFIG
_CORRIDA, _GAMES, _HYATHLON, _THUNDER = RACE_CALENDAR


def _phase(week: date) -> phase.CalendarWeek:
    return phase.calendar_phase(week, _CFG.races, _CFG.previous_race)


def test_week_after_an_a_race_is_not_a_recovery_week() -> None:
    """16 Nov follows Hybrid Monkey (A) but is trained through for the Corrida (C)."""
    week = _phase(date(2026, 11, 16))
    assert week.phase == "race"
    assert week.target == _CORRIDA
    assert week.previous == HYBRID_MONKEY_VI


def test_week_after_a_c_race_is_not_recovery() -> None:
    week = _phase(date(2026, 11, 23))
    assert (week.phase, week.target, week.weeks_remaining) == ("build", _GAMES, 2)


def test_b_race_week_is_trained_through() -> None:
    assert _phase(date(2026, 11, 30)).phase == "race"


def test_a_race_week_is_a_taper_and_follows_a_b_race_without_recovery() -> None:
    week = _phase(date(2026, 12, 7))
    assert (week.phase, week.target, week.previous) == ("taper", _HYATHLON, _GAMES)


def test_week_after_hyathlon_builds_toward_thund3r() -> None:
    after_a = _phase(date(2026, 12, 14))
    assert (after_a.phase, after_a.target, after_a.previous) == ("build", _THUNDER, _HYATHLON)
    last = _phase(date(2026, 12, 21))
    assert (last.phase, last.target) == ("race", _THUNDER)


def test_no_race_left_is_off() -> None:
    week = _phase(date(2027, 1, 4))
    assert (week.phase, week.target) == ("off", None)


def test_far_a_race_is_base_not_build() -> None:
    a_race = replace(_HYATHLON, day=date(2027, 3, 1))
    assert phase.calendar_phase(date(2026, 11, 23), (a_race,)).phase == "base"


def test_calendar_is_in_date_order() -> None:
    days = [r.day for r in RACE_CALENDAR]
    assert days == sorted(days)


def test_block_spans_six_weeks_from_first_race_monday() -> None:
    assert _CFG.block_start == date(2026, 11, 16)
    assert _CFG.last_race_date == date(2026, 12, 26)
    weeks = context.weeks_until_race(_CFG, date(2026, 11, 15))
    assert weeks[0] == date(2026, 11, 16)
    assert weeks[-1] == date(2026, 12, 21)
    assert len(weeks) == 6


def test_race_dates_without_a_calendar_is_the_single_race() -> None:
    cfg = replace(_CFG, races=())
    assert cfg.race_dates == ("2026-12-13",)
    assert cfg.last_race_date == date(2026, 12, 13)


def test_persist_all_skips_weeks_outside_the_block(monkeypatch, tmp_path: Path) -> None:
    for week in ("2026-11-09", "2026-11-16", "2026-12-21", "2027-01-04"):
        (tmp_path / f"plan_week_{week}.json").write_text("{}", encoding="utf-8")
    stored: list[str] = []
    monkeypatch.setattr(persist.context, "PLAN_DIR", tmp_path)
    monkeypatch.setattr(persist, "persist", lambda path, _cfg: stored.append(path.stem) or 1)
    persist.persist_all(_CFG)
    assert stored == ["plan_week_2026-11-16", "plan_week_2026-12-21"]


class _Table:
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


def test_persist_stores_the_race_the_week_targets(monkeypatch, tmp_path: Path) -> None:
    plan_file = tmp_path / "plan_week_2026-11-30.json"
    plan_file.write_text('{"rationale": "r", "methodology": "m", "sessions": []}', encoding="utf-8")
    bundle = SimpleNamespace(
        week_start=date(2026, 11, 30),
        phase_name="race",
        weeks_remaining=1,
        load_band=(1.0, 2.0),
        summary={},
        race=_GAMES,
    )
    store: dict = {}
    monkeypatch.setattr(persist.context, "gather", lambda *_a, **_k: bundle)
    monkeypatch.setattr(
        persist.query,
        "get_supabase_client",
        lambda: SimpleNamespace(table=lambda name: _Table(store, name)),
    )
    persist.persist(plan_file, cfg=_CFG)
    row = store["training_plan_weeks"]
    assert (row["target_race"], row["race_date"], row["phase"]) == (
        "Hybrid Games",
        "2026-12-05",
        "race",
    )


def _weeks() -> list[dict]:
    return [
        {"week_start": "2026-11-16", "race_date": "2026-11-22", "target_race": "Carrera Scuola",
         "phase": "race"},
        {"week_start": "2026-11-23", "race_date": "2026-12-05", "target_race": "Hybrid Games",
         "phase": "build"},
        {"week_start": "2026-11-30", "race_date": "2026-12-05", "target_race": "Hybrid Games",
         "phase": "race"},
    ]


def test_phase_strip_names_the_race_only_in_its_own_week() -> None:
    html = render._phase_strip(_weeks(), date(2026, 11, 23))
    assert html.count("phase-sub") == 2
    assert "Carrera Scuola" in html
    assert html.count("Hybrid Games") == 1
    assert "build" in html and "race" in html


def test_week_pills_label_each_race_week() -> None:
    html = render._week_pills(_weeks(), "2026-11-16")
    assert html.count("Race week") == 2
    assert "Week 2" in html
