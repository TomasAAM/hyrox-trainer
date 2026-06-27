"""Tests for the generation brief — it must be Hyrox-only (no running, no pace)."""

from __future__ import annotations

from datetime import date

import pandas as pd

from plan import context
from plan.config import DEFAULT_CONFIG


def _bundle(runs: list[dict] | None = None, running_days: list[str] | None = None):
    """Build a minimal ContextBundle for rendering the brief offline."""
    zones = pd.DataFrame(
        [
            {"zone_index": 1, "zone_name": "Recovery", "hr_low": None, "hr_high": 114},
            {"zone_index": 2, "zone_name": "Endurance", "hr_low": 114, "hr_high": 133},
            {"zone_index": 3, "zone_name": "Aerobic", "hr_low": 133, "hr_high": 152},
            {"zone_index": 4, "zone_name": "Threshold", "hr_low": 152, "hr_high": 171},
            {"zone_index": 5, "zone_name": "Maximum", "hr_low": 171, "hr_high": None},
        ]
    )
    return context.ContextBundle(
        cfg=DEFAULT_CONFIG,
        week_start=date(2026, 6, 29),
        phase_name="build",
        weeks_remaining=6,
        load_band=(400.0, 500.0),
        summary={"ctl_fitness": 50.0, "scheduled_runs": runs or []},
        zones=zones,
        runs=runs or [],
        running_days=running_days or [],
    )


def test_brief_forbids_standalone_runs() -> None:
    brief = context.render_brief(_bundle())
    assert "do NOT prescribe standalone runs" in brief


def test_brief_has_no_running_pace() -> None:
    brief = context.render_brief(_bundle())
    # No pace *prescriptions* (e.g. "4:34/km") — intensity is HR zones + RPE.
    assert "/km" not in brief
    # The brief should explicitly instruct against using running pace.
    assert "never running pace" in brief


def test_brief_uses_hr_zones_and_division() -> None:
    brief = context.render_brief(_bundle())
    assert "HEART-RATE ZONES" in brief
    assert "Women's Pro" in brief
    assert "152 kg / 50 m" in brief  # sled push at Women's Pro standard


def test_brief_surfaces_scheduled_runs() -> None:
    runs = [{"date": "2026-07-01", "title": "Intervals 6x800m"}]
    brief = context.render_brief(_bundle(runs=runs, running_days=["Wednesday"]))
    assert "Intervals 6x800m" in brief
    assert "schedule Hyrox HARD days AROUND these" in brief
