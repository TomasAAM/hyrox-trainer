"""Seed the ``training_zones`` table from Garmin's heart-rate zones.

There is no lactate test and no running pace component, so zones are five
heart-rate bands sourced from Garmin Connect's configured zones. When Garmin's
configured floors cannot be read, we fall back to a max-HR %-based estimate
(Garmin's default zone model: floors at 50/60/70/80/90 % of max HR). The pace
columns in ``training_zones`` are left NULL.

Run directly to (re)seed: ``python -m plan.zones``.
"""

from __future__ import annotations

import logging
import os
from datetime import date
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from supabase import Client, create_client

from ingest import garmin

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent

# Zone names (easiest → hardest). Z4 floor is treated as the threshold anchor.
_ZONE_NAMES = ["Recovery", "Endurance", "Aerobic", "Threshold", "Maximum"]
# Garmin's default %HRmax floors for Z1..Z5, used for the estimate fallback.
_PCT_HRMAX_FLOORS = [0.50, 0.60, 0.70, 0.80, 0.90]
# Last-resort max HR if neither Garmin zones nor a measured max are available.
_DEFAULT_MAX_HR = 190
# Garmin sport keys preferred when several zone configs are returned.
_PREFERRED_SPORTS = ("RUNNING", "DEFAULT")


def _get_client() -> Client:
    """Create a Supabase client from environment variables."""
    load_dotenv(_PROJECT_ROOT / ".env", override=True)
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])


def _zone_rows(
    floors: list[int], source: str, source_date: str
) -> list[dict[str, Any]]:
    """Build five ``training_zones`` rows from ascending zone floors (bpm).

    Parameters
    ----------
    floors : list of int
        Five ascending HR floors, one per zone (Z1..Z5).
    source : str
        Provenance tag stored on each row ("garmin" or "max_hr_estimate").
    source_date : str
        ISO date the zones were sourced.

    Returns
    -------
    list of dict
        Rows ready to upsert into ``training_zones`` (pace columns NULL).
    """
    rows: list[dict[str, Any]] = []
    for i in range(5):
        # Boundaries: Z1 opens at the low end; Z5 opens at the high end.
        hr_low = None if i == 0 else floors[i]
        hr_high = None if i == 4 else floors[i + 1]
        rows.append(
            {
                "zone_index": i + 1,
                "zone_name": _ZONE_NAMES[i],
                "hr_low": hr_low,
                "hr_high": hr_high,
                "pace_low_s_per_km": None,
                "pace_high_s_per_km": None,
                "source_test_date": source_date,
                "lt2_hr": floors[3],  # Z4 floor = threshold anchor
                "lt2_pace_s_per_km": None,
                "lt1_hr": None,
                "lt1_pace_s_per_km": None,
                "source": source,
            }
        )
    return rows


def _floors_from_garmin(payload: list[dict[str, Any]] | None) -> list[int] | None:
    """Extract five ascending zone floors from Garmin's HR-zones payload.

    Garmin returns one config per sport with ``zone1Floor``..``zone5Floor`` keys.
    Returns ``None`` when the expected floors are absent so the caller can fall
    back to a max-HR estimate.
    """
    if not payload:
        return None

    def _rank(entry: dict[str, Any]) -> int:
        sport = str(entry.get("sport", "")).upper()
        return _PREFERRED_SPORTS.index(sport) if sport in _PREFERRED_SPORTS else len(_PREFERRED_SPORTS)

    for entry in sorted(payload, key=_rank):
        floors: list[int] = []
        for i in range(1, 6):
            value = entry.get(f"zone{i}Floor")
            if value is None:
                floors = []
                break
            floors.append(int(round(float(value))))
        if len(floors) == 5 and floors == sorted(floors):
            return floors
    return None


def _max_hr_from_garmin(payload: list[dict[str, Any]] | None) -> int | None:
    """Read the max HR Garmin used for its zones, if present in the payload."""
    if not payload:
        return None
    for entry in payload:
        value = entry.get("maxHeartRateUsed") or entry.get("maxHeartRate")
        if value:
            return int(round(float(value)))
    return None


def _floors_from_max_hr(max_hr: int) -> list[int]:
    """Estimate five ascending zone floors from a max HR using %HRmax buckets."""
    return [int(round(pct * max_hr)) for pct in _PCT_HRMAX_FLOORS]


def build_rows(
    client: Any | None = None, max_hr_override: int | None = None
) -> list[dict[str, Any]]:
    """Compute zone rows, preferring Garmin's configured zones.

    Parameters
    ----------
    client : garminconnect.Garmin, optional
        Authenticated Garmin client. When omitted, one is created via
        :func:`ingest.garmin.get_client`. Pass ``max_hr_override`` to skip Garmin
        entirely (useful for offline testing).
    max_hr_override : int, optional
        Force the max-HR estimate path with this max HR (skips Garmin calls).

    Returns
    -------
    list of dict
        Five ``training_zones`` rows.
    """
    source_date = date.today().isoformat()

    if max_hr_override is not None:
        logger.info("Building zones from max-HR override (%d bpm)", max_hr_override)
        return _zone_rows(_floors_from_max_hr(max_hr_override), "max_hr_estimate", source_date)

    client = client or garmin.get_client()
    payload = garmin.get_hr_zones(client)

    floors = _floors_from_garmin(payload)
    if floors is not None:
        logger.info("Using Garmin's configured HR zones: %s", floors)
        return _zone_rows(floors, "garmin", source_date)

    max_hr = _max_hr_from_garmin(payload) or _DEFAULT_MAX_HR
    logger.warning(
        "Garmin HR-zone floors unavailable; estimating from max HR = %d bpm", max_hr
    )
    return _zone_rows(_floors_from_max_hr(max_hr), "max_hr_estimate", source_date)


def seed(
    supabase: Client | None = None, rows: list[dict[str, Any]] | None = None
) -> int:
    """Upsert heart-rate training zones into Supabase.

    Parameters
    ----------
    supabase : supabase.Client, optional
        Authenticated client; created from the environment when omitted.
    rows : list of dict, optional
        Zone rows to upsert; computed via :func:`build_rows` when omitted.

    Returns
    -------
    int
        Number of zone rows written.
    """
    supabase = supabase or _get_client()
    if rows is None:
        rows = build_rows()
    supabase.table("training_zones").upsert(rows, on_conflict="zone_index").execute()
    logger.info("Seeded %d HR zones (source: %s)", len(rows), rows[0]["source"])
    return len(rows)


def main() -> None:
    """Seed zones from Garmin (or a max-HR estimate)."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    seed()


if __name__ == "__main__":
    main()
