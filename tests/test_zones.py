"""Tests for heart-rate zone seeding (no pace, no lactate)."""

from __future__ import annotations

from plan import zones


def test_build_rows_from_max_hr_estimate() -> None:
    rows = zones.build_rows(max_hr_override=190)

    assert len(rows) == 5
    # No pace component anywhere — this is an HR-only project.
    assert all(r["pace_low_s_per_km"] is None for r in rows)
    assert all(r["pace_high_s_per_km"] is None for r in rows)
    assert all(r["source"] == "max_hr_estimate" for r in rows)


def test_estimate_zone_boundaries_are_open_ended_and_ascending() -> None:
    rows = zones.build_rows(max_hr_override=190)

    assert rows[0]["hr_low"] is None      # Z1 opens at the low end
    assert rows[4]["hr_high"] is None     # Z5 opens at the high end
    # Z4 floor is the threshold anchor; 80% of 190 = 152.
    assert rows[3]["lt2_hr"] == 152

    boundaries = [r["hr_high"] for r in rows[:-1]]
    assert boundaries == sorted(boundaries)


def test_floors_from_garmin_payload() -> None:
    payload = [
        {"sport": "RUNNING", "zone1Floor": 95, "zone2Floor": 114,
         "zone3Floor": 133, "zone4Floor": 152, "zone5Floor": 171},
    ]
    floors = zones._floors_from_garmin(payload)
    assert floors == [95, 114, 133, 152, 171]

    rows = zones._zone_rows(floors, "garmin", "2026-06-27")
    assert rows[3]["lt2_hr"] == 152
    assert rows[0]["hr_high"] == 114


def test_floors_from_garmin_missing_returns_none() -> None:
    assert zones._floors_from_garmin([{"sport": "RUNNING"}]) is None
    assert zones._floors_from_garmin(None) is None
