"""Seed the Supabase Garmin token store from a machine that can log in.

Garmin serves a Cloudflare bot challenge to password logins from datacentre IPs,
so the scheduled GitHub Action can never authenticate from scratch — it can only
resume from a token issued elsewhere. Run this once from a residential
connection to put a valid token in the ``garmin_auth`` table; from then on the
workflow refreshes and rewrites it on every run.

Re-run it if the stored token is ever rejected (the ingestion job will say so
explicitly rather than failing quietly).

Run with ``python -m ingest.bootstrap_token``.
"""

from __future__ import annotations

import logging
import sys

from ingest import garmin
from ingest.run import get_supabase_client

logger = logging.getLogger(__name__)


def main() -> int:
    """Authenticate locally and store the resulting token in Supabase.

    Returns
    -------
    int
        Process exit code — 0 on success, 1 if authentication failed.
    """
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    supabase = get_supabase_client()

    try:
        client = garmin.get_client(supabase)
    except garmin.GarminAuthError as exc:
        logger.error("Could not authenticate with Garmin: %s", exc)
        return 1

    # get_client already stores the token; confirm it landed so the operator is
    # not left guessing whether CI will actually be able to resume.
    result = supabase.table("garmin_auth").select("updated_at").eq("id", 1).execute()
    if not result.data:
        logger.error("Authentication succeeded but no token row was written")
        return 1

    logger.info("Token stored (updated_at=%s)", result.data[0]["updated_at"])
    logger.info("Verifying the token works: %s", client.get_full_name())
    return 0


if __name__ == "__main__":
    sys.exit(main())
