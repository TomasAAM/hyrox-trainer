"""Garmin Connect ingestion module.

Fetches daily wellness data and raw time-series readings from
Garmin Connect and upserts them into Supabase.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from garminconnect import Garmin
from supabase import Client

logger = logging.getLogger(__name__)


def _to_iso_ts(value: Any) -> str | None:
    """Normalize a Garmin timestamp into an ISO-8601 UTC string.

    Garmin returns per-reading timestamps in mixed formats: epoch milliseconds
    (e.g. heart rate and stress arrays) or already-formatted GMT strings (e.g.
    some HRV payloads). PostgREST needs an explicit ISO timestamp for
    ``timestamptz`` columns.

    Parameters
    ----------
    value : Any
        Epoch-milliseconds int/float, or a string timestamp, or ``None``.

    Returns
    -------
    str or None
        ISO-8601 UTC timestamp, or ``None`` if the input is falsy.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).isoformat()
    # Already a string: ensure it has a 'T' separator; assume GMT if no offset.
    text = str(value).replace(" ", "T")
    if text.endswith("Z") or "+" in text:
        return text
    return text + "+00:00"

# Cache directory for garth OAuth tokens — avoids re-authenticating on every run
# and prevents hitting Garmin's SSO rate limit.
_TOKEN_DIR = Path(__file__).parent.parent / ".garth_tokens"


def get_client() -> Garmin:
    """Authenticate with Garmin Connect, using a cached token when available.

    Token lookup order:
    1. ``.garth_tokens/`` in the project root (written by this script after
       a successful login).
    2. ``~/.garminconnect`` — the default location used by the Garmin MCP
       server, so a shared desktop session can bootstrap the pipeline without
       a fresh SSO login.
    3. Full SSO login using GARMIN_EMAIL / GARMIN_PASSWORD (writes a new
       token to ``.garth_tokens/`` for future runs).

    Returns
    -------
    Garmin
        Authenticated Garmin client.
    """
    email = os.environ["GARMIN_EMAIL"]
    password = os.environ["GARMIN_PASSWORD"]

    # Directories to probe for cached tokens, in priority order.
    _MCP_TOKEN_DIR = Path.home() / ".garminconnect"
    token_candidates = [_TOKEN_DIR, _MCP_TOKEN_DIR]

    for token_dir in token_candidates:
        if token_dir.exists():
            try:
                client = Garmin(email=email, password=password)
                client.login(str(token_dir))
                logger.info("Authenticated with Garmin Connect (token from %s)", token_dir)
                # Mirror to project-local cache so the MCP dir isn't a hard dependency.
                if token_dir != _TOKEN_DIR:
                    _TOKEN_DIR.mkdir(parents=True, exist_ok=True)
                    client.client.dump(str(_TOKEN_DIR))
                    logger.info("Token mirrored to %s", _TOKEN_DIR)
                return client
            except Exception:  # noqa: BLE001
                logger.info("Token at %s invalid or expired — trying next source", token_dir)

    logger.info("No valid cached token found — performing full SSO login")
    client = Garmin(email=email, password=password)
    client.login()
    _TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    client.client.dump(str(_TOKEN_DIR))
    logger.info("Authenticated with Garmin Connect (full login, token saved to %s)", _TOKEN_DIR)
    return client


def _date_range(since: date, until: date) -> list[date]:
    """Generate a list of dates from since to until inclusive.

    Parameters
    ----------
    since : date
        Start date.
    until : date
        End date.

    Returns
    -------
    list[date]
        All dates in the range.
    """
    days = (until - since).days + 1
    return [since + timedelta(days=i) for i in range(days)]


def _ingest_daily_wellness(garmin: Garmin, supabase: Client, target_date: date) -> None:
    """Fetch and upsert daily wellness summary for one date.

    Parameters
    ----------
    garmin : Garmin
        Authenticated Garmin client.
    supabase : Client
        Authenticated Supabase client.
    target_date : date
        The date to fetch data for.
    """
    date_str = target_date.isoformat()
    try:
        summary = garmin.get_user_summary(date_str)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not fetch daily summary for %s: %s", date_str, exc)
        return

    row = {
        "date": date_str,
        "resting_hr": summary.get("restingHeartRate"),
        "min_hr": summary.get("minHeartRate"),
        "max_hr": summary.get("maxHeartRate"),
        "avg_stress": summary.get("averageStressLevel"),
        "max_stress": summary.get("maxStressLevel"),
        "avg_spo2": summary.get("averageSpo2"),
        "lowest_spo2": summary.get("lowestSpo2"),
        "body_battery_wake": summary.get("bodyBatteryAtWakeTime"),
        "body_battery_high": summary.get("bodyBatteryHighestValue"),
        "body_battery_low": summary.get("bodyBatteryLowestValue"),
        "body_battery_now": summary.get("bodyBatteryMostRecentValue"),
        "total_steps": summary.get("totalSteps"),
        "active_calories": summary.get("activeKilocalories"),
        "total_calories": summary.get("totalKilocalories"),
        "avg_respiration": summary.get("avgWakingRespirationValue"),
    }
    supabase.table("garmin_daily_wellness").upsert(row, on_conflict="date").execute()
    logger.info("Upserted daily wellness for %s", date_str)


def _ingest_hrv(garmin: Garmin, supabase: Client, target_date: date) -> None:
    """Fetch and upsert HRV readings for one date.

    Parameters
    ----------
    garmin : Garmin
        Authenticated Garmin client.
    supabase : Client
        Authenticated Supabase client.
    target_date : date
        The date to fetch data for.
    """
    date_str = target_date.isoformat()
    try:
        data = garmin.get_hrv_data(date_str)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not fetch HRV for %s: %s", date_str, exc)
        return

    if not data or "hrvSummary" not in data:
        return

    summary = data["hrvSummary"]
    readings = data.get("hrvReadings", [])

    rows = [
        {
            "date": date_str,
            "ts": _to_iso_ts(r.get("readingTimeGMT")),
            "hrv_ms": r.get("hrvValue"),
            "hrv_avg_night": summary.get("lastNightAvg"),
            "hrv_weekly_avg": summary.get("weeklyAvg"),
            "hrv_baseline_low": summary.get("baseline", {}).get("lowUpper"),
            "hrv_baseline_high": summary.get("baseline", {}).get("balancedUpper"),
            "hrv_status": summary.get("status"),
        }
        for r in readings
        if r.get("readingTimeGMT")
    ]

    if rows:
        supabase.table("garmin_hrv_readings").upsert(rows, on_conflict="date,ts").execute()
        logger.info("Upserted %d HRV readings for %s", len(rows), date_str)


def _ingest_heart_rate(garmin: Garmin, supabase: Client, target_date: date) -> None:
    """Fetch and upsert heart rate readings for one date.

    Parameters
    ----------
    garmin : Garmin
        Authenticated Garmin client.
    supabase : Client
        Authenticated Supabase client.
    target_date : date
        The date to fetch data for.
    """
    date_str = target_date.isoformat()
    try:
        data = garmin.get_heart_rates(date_str)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not fetch heart rates for %s: %s", date_str, exc)
        return

    values = data.get("heartRateValues") or []
    rows = [
        {"date": date_str, "ts": _to_iso_ts(v[0]), "hr_bpm": v[1]}
        for v in values
        if v[1] is not None
    ]

    if rows:
        supabase.table("garmin_heart_rate_readings").upsert(rows, on_conflict="date,ts").execute()
        logger.info("Upserted %d HR readings for %s", len(rows), date_str)


def _ingest_stress(garmin: Garmin, supabase: Client, target_date: date) -> None:
    """Fetch and upsert stress readings for one date.

    Parameters
    ----------
    garmin : Garmin
        Authenticated Garmin client.
    supabase : Client
        Authenticated Supabase client.
    target_date : date
        The date to fetch data for.
    """
    date_str = target_date.isoformat()
    try:
        data = garmin.get_stress_data(date_str)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not fetch stress for %s: %s", date_str, exc)
        return

    values = data.get("stressValuesArray") or []
    rows = [
        {"date": date_str, "ts": _to_iso_ts(v[0]), "stress_level": v[1]}
        for v in values
    ]

    if rows:
        supabase.table("garmin_stress_readings").upsert(rows, on_conflict="date,ts").execute()
        logger.info("Upserted %d stress readings for %s", len(rows), date_str)


def _ingest_training_readiness(garmin: Garmin, supabase: Client, target_date: date) -> None:
    """Fetch and upsert training readiness snapshots for one date.

    Parameters
    ----------
    garmin : Garmin
        Authenticated Garmin client.
    supabase : Client
        Authenticated Supabase client.
    target_date : date
        The date to fetch data for.
    """
    date_str = target_date.isoformat()
    try:
        data = garmin.get_training_readiness(date_str)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not fetch training readiness for %s: %s", date_str, exc)
        return

    if not data:
        return

    snapshots = data if isinstance(data, list) else [data]
    rows = [
        {
            "date": date_str,
            "ts": _to_iso_ts(s.get("timestamp")),
            "context": s.get("context"),
            "score": s.get("score"),
            "level": s.get("level"),
            "recovery_time_h": s.get("recoveryTime"),
            "acute_load": s.get("acuteLoad"),
            "hrv_weekly_avg": s.get("hrvWeeklyAverage"),
            "sleep_score": s.get("sleepScore"),
        }
        for s in snapshots
        if s.get("timestamp")
    ]

    if rows:
        supabase.table("garmin_training_readiness").upsert(rows, on_conflict="date,ts").execute()
        logger.info("Upserted %d readiness snapshots for %s", len(rows), date_str)


def ingest(supabase: Client, since: date, client: Garmin | None = None) -> None:
    """Fetch new Garmin data and upsert into Supabase.

    Parameters
    ----------
    supabase : Client
        Authenticated Supabase client.
    since : date
        Fetch data from this date onwards.
    client : Garmin, optional
        An already-authenticated Garmin client to reuse. If omitted, a new
        client is created via :func:`get_client`. Passing a shared client
        avoids a second SSO login when wellness and activity ingestion run
        in the same pipeline.
    """
    garmin = client or get_client()
    today = date.today()
    dates = _date_range(since, today)

    logger.info("Ingesting Garmin data for %d days (%s to %s)", len(dates), since, today)

    for target_date in dates:
        _ingest_daily_wellness(garmin, supabase, target_date)
        _ingest_hrv(garmin, supabase, target_date)
        _ingest_heart_rate(garmin, supabase, target_date)
        _ingest_stress(garmin, supabase, target_date)
        _ingest_training_readiness(garmin, supabase, target_date)

    logger.info("Garmin ingestion complete")


def _months_in_range(start: date, end: date) -> list[tuple[int, int]]:
    """List the (year, month) pairs the inclusive date range spans."""
    months: list[tuple[int, int]] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.append((year, month))
        month += 1
        if month > 12:
            month = 1
            year += 1
    return months


def get_scheduled_runs(client: Garmin, start: date, end: date) -> list[dict[str, Any]]:
    """Read upcoming scheduled workouts from Garmin Connect for a date range.

    The athlete follows her own running plan; when it lives in Garmin Connect as
    scheduled workouts (Garmin Coach, an imported plan, or manually scheduled),
    this surfaces her planned run days so the Hyrox generator can place hard
    station/strength work away from them.

    Parameters
    ----------
    client : Garmin
        Authenticated Garmin client.
    start : date
        Inclusive start of the range to scan (typically the plan week's Monday).
    end : date
        Inclusive end of the range to scan (typically the plan week's Sunday).

    Returns
    -------
    list of dict
        One entry per scheduled item in range: ``{"date", "title"}``, sorted by
        date. Empty when Garmin has no scheduled workouts — the generator then
        falls back to :attr:`plan.config.PlanConfig.running_days`.
    """
    items: list[dict[str, Any]] = []
    # Garmin's per-month calendar includes adjacent-month days, so a date in the
    # range can appear in two month queries — dedupe on (date, title, id).
    seen: set[tuple] = set()
    for year, month in _months_in_range(start, end):
        try:
            # Garmin's calendar endpoint is queried per month (1-indexed).
            calendar = client.get_scheduled_workouts(year=year, month=month)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Could not fetch scheduled workouts for %d-%02d: %s", year, month, exc
            )
            continue
        for item in calendar.get("calendarItems") or []:
            raw = item.get("date")
            if not raw:
                continue
            try:
                item_date = date.fromisoformat(raw)
            except ValueError:
                continue
            if not (start <= item_date <= end):
                continue
            key = (item_date.isoformat(), item.get("title"), item.get("id"))
            if key in seen:
                continue
            seen.add(key)
            items.append({"date": item_date.isoformat(), "title": item.get("title")})
    return sorted(items, key=lambda i: i["date"])


# Garmin's configured HR zones live behind the biometric service; the
# garminconnect wrapper has no first-class method, so we hit it via connectapi.
_HR_ZONES_PATH = "/biometric-service/heartRateZones"


def get_hr_zones(client: Garmin) -> list[dict[str, Any]] | None:
    """Fetch the athlete's configured heart-rate zones from Garmin Connect.

    Uses the raw ``connectapi`` request because garminconnect has no first-class
    wrapper for the biometric heart-rate-zones endpoint. Returns the raw payload
    for :mod:`plan.zones` to map into zone rows, or ``None`` when the endpoint is
    unavailable so the caller can fall back to a max-HR estimate.

    Parameters
    ----------
    client : Garmin
        Authenticated Garmin client.

    Returns
    -------
    list of dict or None
        Garmin's per-sport heart-rate-zone configuration, or ``None`` on failure.
    """
    try:
        data = client.connectapi(_HR_ZONES_PATH)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not fetch Garmin HR zones: %s", exc)
        return None
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    return None
