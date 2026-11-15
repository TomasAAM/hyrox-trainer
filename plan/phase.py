"""Deterministic periodization logic.

The phase a given week sits in is a pure function of how many weeks remain until
the race — no model involved, so it is fully reproducible and unit-testable. The
generator uses the phase to bias the week's structure and weekly-load target.

Phase map (by whole weeks remaining until the race):

* ``>= 9``  base       — aerobic volume, build the engine
* ``4..8``  build      — threshold + race-specific intensity
* ``3``     peak        — highest specificity, race simulation
* ``1..2``  taper       — cut volume, retain intensity, arrive fresh
* ``<= 0``  off         — race done / transition

The default above assumes a two-week taper. ``taper_weeks`` shortens it: with
``taper_weeks=0`` the race week is the peak and there is no taper week at all.

A block with several races uses :func:`calendar_phase` instead, which adds a
``race`` week (a B or C race trained through). There is no automatic recovery week:
loading resumes straight after a race.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

from plan.config import Race

# Weekly-load multipliers applied to the chronic (CTL-derived) weekly load to set
# the target band for each phase. Taper deliberately drops volume.
_PHASE_LOAD_MULT: dict[str, float] = {
    "base": 1.00,
    "build": 1.10,
    "peak": 1.00,
    "race": 0.90,
    "taper": 0.55,
    "off": 0.30,
}


def weeks_to_race(week_start: date, race_date: date) -> int:
    """Whole weeks remaining from the start of a plan week to the race.

    Parameters
    ----------
    week_start : datetime.date
        Monday of the plan week.
    race_date : datetime.date
        Date of the target race.

    Returns
    -------
    int
        Number of whole weeks until the race (0 once the race week has passed).

    Examples
    --------
    >>> weeks_to_race(date(2026, 6, 22), date(2026, 8, 2))
    6
    >>> weeks_to_race(date(2026, 7, 27), date(2026, 8, 2))
    1
    """
    days = (race_date - week_start).days
    if days <= 0:
        return 0
    return math.ceil(days / 7)


def classify_phase(weeks_remaining: int, taper_weeks: int = 2) -> str:
    """Map whole weeks remaining to a periodization phase.

    Parameters
    ----------
    weeks_remaining : int
        Output of :func:`weeks_to_race`.
    taper_weeks : int, optional
        How many whole weeks before the race count as taper. The single week
        before them is the peak. ``0`` means no taper week: the race week itself
        is the peak, and any easing off is only the last days inside it.

    Returns
    -------
    str
        One of ``base``, ``build``, ``peak``, ``taper``, ``off``.

    Examples
    --------
    >>> classify_phase(1)
    'taper'
    >>> classify_phase(1, taper_weeks=0)
    'peak'
    >>> classify_phase(2, taper_weeks=0)
    'build'
    """
    if weeks_remaining <= 0:
        return "off"
    if weeks_remaining <= taper_weeks:
        return "taper"
    if weeks_remaining == taper_weeks + 1:
        return "peak"
    if weeks_remaining <= 8:
        return "build"
    return "base"


def phase_for_week(
    week_start: date, race_date: date, taper_weeks: int = 2
) -> tuple[str, int]:
    """Return the phase and weeks-remaining for a plan week.

    Parameters
    ----------
    week_start : datetime.date
        Monday of the plan week.
    race_date : datetime.date
        Date of the target race.
    taper_weeks : int, optional
        Whole weeks of taper; see :func:`classify_phase`.

    Returns
    -------
    tuple of (str, int)
        The phase label and the whole weeks remaining until the race.
    """
    remaining = weeks_to_race(week_start, race_date)
    return classify_phase(remaining, taper_weeks), remaining


@dataclass(frozen=True)
class CalendarWeek:
    """Phase of one plan week within a multi-race calendar.

    Parameters
    ----------
    phase : str
        One of ``base``, ``build``, ``race``, ``taper``, ``off``.
    weeks_remaining : int
        Whole weeks from the week's Monday to ``target`` (0 when there is none).
    target : Race or None
        The first race on or after the week's Monday; ``None`` once the calendar
        is over.
    previous : Race or None
        The last race before the week's Monday.
    """

    phase: str
    weeks_remaining: int
    target: Race | None
    previous: Race | None


def calendar_phase(
    week_start: date, races: tuple[Race, ...], previous_race: Race | None = None
) -> CalendarWeek:
    """Classify a plan week against a calendar of several races.

    A week is classified by the first of these that applies:

    1. ``taper`` -- the week holds an A race.
    2. ``race`` -- the week holds a B or C race (trained through).
    3. ``build`` -- otherwise, when the next A race is at most 8 weeks away
       (or there is no A race left); ``base`` when it is further out.
    4. ``off`` -- no race is left on the calendar.

    There is no recovery week: the week after a race is ``build`` or ``race`` like
    any other, and the plan itself keeps the first days after the race easy.

    Parameters
    ----------
    week_start : datetime.date
        Monday of the plan week.
    races : tuple of Race
        The calendar, in date order.
    previous_race : Race, optional
        The race before the calendar starts, used when no calendar race precedes
        the week.

    Returns
    -------
    CalendarWeek
        The phase together with the race the week targets.

    Examples
    --------
    >>> from plan.config import Race
    >>> a = Race("A", date(2026, 12, 13), "A", "hybrid", "Pro", "Vina")
    >>> calendar_phase(date(2026, 12, 7), (a,)).phase
    'taper'
    >>> calendar_phase(date(2026, 12, 14), (a,)).phase
    'off'
    """
    before = [r for r in races if r.day < week_start]
    previous = before[-1] if before else previous_race
    upcoming = [r for r in races if r.day >= week_start]
    if not upcoming:
        return CalendarWeek("off", 0, None, previous)

    target = upcoming[0]
    remaining = weeks_to_race(week_start, target.day)

    if target.day <= week_start + timedelta(days=6):
        phase_name = "taper" if target.priority == "A" else "race"
        return CalendarWeek(phase_name, remaining, target, previous)

    next_a = next((r for r in upcoming if r.priority == "A"), target)
    phase_name = "build" if weeks_to_race(week_start, next_a.day) <= 8 else "base"
    return CalendarWeek(phase_name, remaining, target, previous)


def load_target_band(weekly_chronic_load: float, phase: str) -> tuple[float, float]:
    """Compute an ACWR-bounded weekly training-load target for a phase.

    The midpoint is the chronic (CTL-derived) weekly load scaled by the phase
    multiplier; the band spans roughly an acute:chronic ratio of 0.9 to 1.1
    around that midpoint to keep week-to-week progression safe.

    Parameters
    ----------
    weekly_chronic_load : float
        Chronic training load expressed per week (CTL * 7).
    phase : str
        Phase label from :func:`classify_phase`.

    Returns
    -------
    tuple of (float, float)
        Lower and upper weekly-load targets.
    """
    mult = _PHASE_LOAD_MULT.get(phase, 1.0)
    midpoint = weekly_chronic_load * mult
    return round(midpoint * 0.9, 0), round(midpoint * 1.1, 0)


def upcoming_monday(today: date) -> date:
    """Return the Monday of the week to plan for.

    If ``today`` is already a Monday, that day is returned; otherwise the next
    Monday is returned. Run on a Sunday this yields the week about to start; run
    mid-week it yields next week.

    Parameters
    ----------
    today : datetime.date
        Reference date.

    Returns
    -------
    datetime.date
        Monday of the week to generate.
    """
    days_ahead = (0 - today.weekday()) % 7
    return today if days_ahead == 0 else date.fromordinal(today.toordinal() + days_ahead)
