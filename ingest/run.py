"""Ingestion orchestrator (Garmin-only).

Determines the sync window from what is already in Supabase, then runs Garmin
wellness and activity ingestion in sequence. Safe to re-run — all upserts are
idempotent.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

from ingest import garmin, garmin_activities

# Resolve .env relative to this file's project root so it works whether called as
# `python -m ingest.run` or via GitHub Actions.
_PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(_PROJECT_ROOT / ".env", override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

DEFAULT_LOOKBACK_DAYS = 90

# Garmin sometimes lags a day in publishing wellness data, so one day of slack
# is normal; beyond this the pipeline is considered broken rather than quiet.
MAX_DATA_AGE_DAYS = 2


def get_supabase_client():
    """Create an authenticated Supabase client from environment variables.

    Returns
    -------
    supabase.Client
        Authenticated Supabase client using the service role key.
    """
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return create_client(url, key)


def get_last_synced_date(supabase) -> date:
    """Find the most recent Garmin wellness date already in Supabase.

    Parameters
    ----------
    supabase : supabase.Client
        Authenticated Supabase client.

    Returns
    -------
    date
        The date to start syncing from (defaults to a 90-day lookback on first run).
    """
    default_since = date.today() - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    try:
        result = (
            supabase.table("garmin_daily_wellness")
            .select("date")
            .order("date", desc=True)
            .limit(1)
            .execute()
        )
        since = date.fromisoformat(result.data[0]["date"]) if result.data else default_since
    except Exception:  # noqa: BLE001
        since = default_since

    logger.info("Syncing from %s", since)
    return since


def assert_data_is_fresh(supabase, max_age_days: int = MAX_DATA_AGE_DAYS) -> date:
    """Verify the newest synced wellness date is recent enough to trust.

    A successful-looking run that ingested nothing is the failure mode that hid
    a nineteen-day outage: every downstream metric is computed from whatever is
    in the database, so stale data reads as an athlete who stopped training
    rather than as a broken pipeline. This turns that silence into a hard error.

    Parameters
    ----------
    supabase : supabase.Client
        Authenticated Supabase client.
    max_age_days : int
        Maximum tolerated lag between today and the newest wellness row.

    Returns
    -------
    date
        The newest wellness date present after ingestion.

    Raises
    ------
    RuntimeError
        If no wellness rows exist or the newest one is older than ``max_age_days``.
    """
    result = (
        supabase.table("garmin_daily_wellness")
        .select("date")
        .order("date", desc=True)
        .limit(1)
        .execute()
    )
    if not result.data:
        raise RuntimeError("Ingestion produced no wellness rows at all")

    newest = date.fromisoformat(result.data[0]["date"])
    age = (date.today() - newest).days
    if age > max_age_days:
        raise RuntimeError(
            f"Ingested data is stale: newest wellness date is {newest} ({age} days old, "
            f"limit {max_age_days}). Garmin authentication most likely failed — check the "
            "stored token and re-seed it with `python -m ingest.bootstrap_token`."
        )

    logger.info("Data freshness OK: newest wellness date is %s (%d days old)", newest, age)
    return newest


def main() -> None:
    """Run the full Garmin ingestion pipeline.

    Raises
    ------
    Exception
        Any ingestion failure is re-raised so the process exits non-zero. The
        scheduled workflow must go red when this breaks — swallowing the error
        here is what let the dashboard silently serve three-week-old data.
    """
    logger.info("Starting ingestion pipeline")

    supabase = get_supabase_client()
    since = get_last_synced_date(supabase)

    logger.info("Running Garmin ingestion from %s", since)
    try:
        # Authenticate once and reuse the client for both wellness and activity
        # ingestion to avoid a second login. Passing the Supabase client lets
        # get_client resume from the stored token and rotate it afterwards.
        garmin_client = garmin.get_client(supabase)
        garmin.ingest(supabase, since=since, client=garmin_client)
        garmin_activities.ingest(supabase, garmin_client, since=since)
    except Exception:
        logger.error("Garmin ingestion failed", exc_info=True)
        raise

    assert_data_is_fresh(supabase)
    logger.info("Ingestion pipeline complete")


if __name__ == "__main__":
    main()
