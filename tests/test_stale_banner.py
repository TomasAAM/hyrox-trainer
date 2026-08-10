"""Tests for the dashboard staleness banner."""

from __future__ import annotations

from datetime import date

from dashboard import render


def test_no_banner_when_data_is_current() -> None:
    assert render._stale_banner(date(2026, 8, 9), today=date(2026, 8, 10)) == ""


def test_no_banner_within_tolerance() -> None:
    """Garmin can lag a day; two days is still considered healthy."""
    assert render._stale_banner(date(2026, 8, 8), today=date(2026, 8, 10)) == ""


def test_banner_when_data_is_stale() -> None:
    """The outage case: 2026-07-20 data still being served on 2026-08-09."""
    html = render._stale_banner(date(2026, 7, 20), today=date(2026, 8, 9))

    assert "20 days old" in html
    assert "20 July 2026" in html
    assert "falsely fresh" in html
