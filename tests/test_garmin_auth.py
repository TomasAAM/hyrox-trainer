"""Tests for Garmin authentication resolution and the ingestion freshness guard.

These cover the failure that let the pipeline break silently: CI could not log in
(Cloudflare blocks SSO from datacentre IPs) and the orchestrator swallowed the
error, so the job stayed green while the data aged. Nothing here touches the
network — the Garmin and Supabase clients are stubbed.
"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from ingest import garmin, run

# Long enough that garminconnect treats it as token data rather than a path.
VALID_BLOB = "x" * (garmin._TOKEN_DATA_MIN_LEN + 1)


class FakeQuery:
    """Chainable stand-in for the Supabase query builder."""

    def __init__(self, table: FakeTable) -> None:
        self._table = table

    def select(self, *_args, **_kwargs) -> FakeQuery:
        return self

    def eq(self, *_args, **_kwargs) -> FakeQuery:
        return self

    def order(self, *_args, **_kwargs) -> FakeQuery:
        return self

    def limit(self, *_args, **_kwargs) -> FakeQuery:
        return self

    def upsert(self, payload, **_kwargs) -> FakeQuery:
        self._table.upserted.append(payload)
        return self

    def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._table.rows)


class FakeTable:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.upserted: list[dict] = []


class FakeSupabase:
    """Minimal Supabase client double keyed by table name."""

    def __init__(self, **tables: list[dict]) -> None:
        self.tables = {name: FakeTable(rows) for name, rows in tables.items()}

    def table(self, name: str) -> FakeQuery:
        self.tables.setdefault(name, FakeTable([]))
        return FakeQuery(self.tables[name])


class FakeGarmin:
    """Garmin client double that accepts or rejects specific tokenstores."""

    def __init__(self, accepts: set[str] | None = None, **_kwargs) -> None:
        self._accepts = accepts if accepts is not None else {VALID_BLOB}
        self.client = SimpleNamespace(dumps=lambda: "rotated-token", dump=lambda _p: None)
        self.logged_in_with: str | None = None

    def login(self, tokenstore: str | None = None):
        if tokenstore not in self._accepts:
            raise RuntimeError("token rejected")
        self.logged_in_with = tokenstore
        return None, None


@pytest.fixture(autouse=True)
def _isolate_local_tokens(tmp_path, monkeypatch):
    """Hide any real token directories so tests never use the developer's login."""
    monkeypatch.setattr(garmin, "_TOKEN_DIR", tmp_path / "absent")
    monkeypatch.setattr(garmin.Path, "home", staticmethod(lambda: tmp_path / "nohome"))
    monkeypatch.delenv("GARMIN_EMAIL", raising=False)
    monkeypatch.delenv("GARMIN_PASSWORD", raising=False)
    monkeypatch.delenv("GARMIN_TOKENS", raising=False)


def test_stored_token_is_preferred_over_sso(monkeypatch) -> None:
    """The Supabase-stored token authenticates without any credentials present."""
    monkeypatch.setattr(garmin, "Garmin", FakeGarmin)
    supabase = FakeSupabase(garmin_auth=[{"tokens": VALID_BLOB}])

    client = garmin.get_client(supabase)

    assert client.logged_in_with == VALID_BLOB


def test_successful_auth_rotates_the_stored_token(monkeypatch) -> None:
    """Garmin rotates refresh tokens, so the store must be rewritten each run."""
    monkeypatch.setattr(garmin, "Garmin", FakeGarmin)
    supabase = FakeSupabase(garmin_auth=[{"tokens": VALID_BLOB}])

    garmin.get_client(supabase)

    upserted = supabase.tables["garmin_auth"].upserted
    assert len(upserted) == 1
    assert upserted[0]["tokens"] == "rotated-token"


def test_env_token_used_when_store_is_empty(monkeypatch) -> None:
    """GARMIN_TOKENS is the bootstrap escape hatch before the table is seeded."""
    monkeypatch.setattr(garmin, "Garmin", FakeGarmin)
    monkeypatch.setenv("GARMIN_TOKENS", VALID_BLOB)
    supabase = FakeSupabase(garmin_auth=[])

    client = garmin.get_client(supabase)

    assert client.logged_in_with == VALID_BLOB


def test_short_token_is_skipped_not_treated_as_path(monkeypatch) -> None:
    """A truncated blob would be resolved as a filesystem path — reject it first."""
    monkeypatch.setattr(garmin, "Garmin", FakeGarmin)
    supabase = FakeSupabase(garmin_auth=[{"tokens": "too-short"}])

    with pytest.raises(garmin.GarminAuthError):
        garmin.get_client(supabase)


def test_no_token_and_no_credentials_raises(monkeypatch) -> None:
    """This is the CI case: it must fail loudly rather than return unauthenticated."""
    monkeypatch.setattr(garmin, "Garmin", FakeGarmin)
    supabase = FakeSupabase(garmin_auth=[])

    with pytest.raises(garmin.GarminAuthError, match="bootstrap_token"):
        garmin.get_client(supabase)


def test_sso_failure_surfaces_as_auth_error(monkeypatch) -> None:
    """A Cloudflare-blocked SSO login must raise, not fall through silently."""
    monkeypatch.setattr(garmin, "Garmin", lambda **_kw: FakeGarmin(accepts=set()))
    monkeypatch.setenv("GARMIN_EMAIL", "a@b.c")
    monkeypatch.setenv("GARMIN_PASSWORD", "secret")
    supabase = FakeSupabase(garmin_auth=[])

    with pytest.raises(garmin.GarminAuthError):
        garmin.get_client(supabase)


def test_freshness_guard_accepts_recent_data() -> None:
    recent = (date.today() - timedelta(days=1)).isoformat()
    supabase = FakeSupabase(garmin_daily_wellness=[{"date": recent}])

    assert run.assert_data_is_fresh(supabase) == date.fromisoformat(recent)


def test_freshness_guard_rejects_stale_data() -> None:
    """The nineteen-day outage reproduced: stale data must fail the job."""
    stale = (date.today() - timedelta(days=19)).isoformat()
    supabase = FakeSupabase(garmin_daily_wellness=[{"date": stale}])

    with pytest.raises(RuntimeError, match="stale"):
        run.assert_data_is_fresh(supabase)


def test_freshness_guard_rejects_empty_database() -> None:
    supabase = FakeSupabase(garmin_daily_wellness=[])

    with pytest.raises(RuntimeError, match="no wellness rows"):
        run.assert_data_is_fresh(supabase)
