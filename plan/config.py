"""Immutable Hyrox training-plan configuration.

Holds the race target, the athlete's weekly availability, her division standards,
and how her separate running plan is coordinated. Edit ``DEFAULT_CONFIG`` when the
target race, schedule, or availability changes — everything downstream reads from
here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

RacePriority = Literal["A", "B", "C"]
RaceKind = Literal["hybrid", "run"]


@dataclass(frozen=True)
class Race:
    """One race on the calendar.

    Parameters
    ----------
    name : str
        Race name as shown on the dashboard.
    day : datetime.date
        Race day.
    priority : {"A", "B", "C"}
        ``A`` gets a taper in its race week. ``B`` and ``C`` are trained through:
        only the last days before the race are eased. No race is followed by a
        dedicated recovery week.
    kind : {"hybrid", "run"}
        ``hybrid`` is a running + stations format; ``run`` is a pure running race,
        which her own running plan covers.
    category : str
        The category she entered.
    venue : str
        Where the race is held.
    """

    name: str
    day: date
    priority: RacePriority
    kind: RaceKind
    category: str
    venue: str


@dataclass(frozen=True)
class PlanConfig:
    """Static configuration for the Hyrox plan generator.

    Parameters
    ----------
    target_race : str
        Race being trained for (``"hyrox"``).
    race_date : datetime.date
        Date of the target race; the periodization phase is computed from it. With
        ``races`` set, this is the A race the block is built around.
    division : str
        Hyrox division whose station standards are prescribed (see
        ``HYROX_STANDARDS``).
    sessions_per_week : int
        Number of Hyrox-specific sessions per week (strength / functional /
        simulation). Running is NOT counted here — she runs on her own plan.
    strength_per_week : int
        How many of those sessions are gym-strength focused (the rest are
        functional / station / simulation work).
    rest_days : tuple of str
        Weekday names that default to no Hyrox session (her running/recovery
        happens then). The generator may shift these to auto-regulate.
    model : str
        Generator id recorded for audit.
    recent_window_days : int
        How many days of recent training to summarize into the brief.
    gym_access : str
        Equipment availability — "full" enables heavy barbell and plyometric
        prescriptions, not just bodyweight/station circuits.
    running_source : str
        How the generator learns her run days. "garmin_scheduled" reads her
        upcoming scheduled workouts from Garmin Connect; "fixed" falls back to
        ``running_days`` below.
    running_days : tuple of str
        Fallback fixed running weekdays, used only when ``running_source`` is
        "fixed" or Garmin has no scheduled runs for the plan week.
    taper_weeks : int
        Whole weeks before the race that count as taper (``plan.phase``). ``0``
        means no taper week: the race week is the peak and any easing off is only
        the last days inside it. Ignored when ``races`` is set.
    races : tuple of Race
        The race calendar for the block, in date order. When set, each week's phase
        and target race come from the calendar (``plan.phase.calendar_phase``)
        instead of from ``race_date`` and ``taper_weeks``.
    previous_race : Race or None
        The race just before the calendar starts, so the brief for the first week
        can say how recently she raced.
    athlete_data_connected : bool
        Whether the Garmin data in the database belongs to this athlete. When
        ``False`` the plan is stored without a load band or recovery snapshot, and
        the dashboard shows the plan only, so another person's load and zones are
        never presented as hers.
    """

    target_race: str
    race_date: date
    division: str
    sessions_per_week: int
    strength_per_week: int
    rest_days: tuple[str, ...]
    model: str
    recent_window_days: int
    gym_access: str
    running_source: str
    running_days: tuple[str, ...] = field(default_factory=tuple)
    taper_weeks: int = 2
    races: tuple[Race, ...] = ()
    previous_race: Race | None = None
    athlete_data_connected: bool = True

    @property
    def race_dates(self) -> tuple[str, ...]:
        """ISO dates of every race in the block (just ``race_date`` without a calendar)."""
        if not self.races:
            return (self.race_date.isoformat(),)
        return tuple(r.day.isoformat() for r in self.races)

    @property
    def last_race_date(self) -> date:
        """Day of the final race in the block."""
        return max((r.day for r in self.races), default=self.race_date)

    @property
    def block_start(self) -> date:
        """Monday of the week holding the first race in the block."""
        first = min((r.day for r in self.races), default=self.race_date)
        return first - timedelta(days=first.weekday())


# The eight Hyrox stations, in race order, the generator draws functional work from.
HYROX_STATIONS: tuple[str, ...] = (
    "ski_erg",
    "sled_push",
    "sled_pull",
    "burpee_broad_jump",
    "rowing",
    "farmers_carry",
    "sandbag_lunges",
    "wall_balls",
)

# Heavy and explosive gym movements (full-gym access) that drive economy and
# station power. Kept off her hard run days to avoid the one real interference
# risk — explosive-strength loss from same-session/adjacent concurrent training.
STRENGTH_LIBRARY: tuple[str, ...] = (
    "back_squat",
    "trap_bar_deadlift",
    "hip_thrust",
    "walking_lunge",
    "box_jump",
    "hurdle_hop",
    "weighted_step_up",
    "pull_up",
    "overhead_press",
)

# Target division and its official station standards. She entered the Singles Pro
# category in the December races, so station work is prescribed at the Women's Pro
# loads.
# Source: official Hyrox 2025/26 standards (hycrew.com/hyrox/weights, pace-club.com).
HYROX_DIVISION = "Women's Pro"
HYROX_STANDARDS: dict[str, str] = {
    "sled_push": "152 kg / 50 m",
    "sled_pull": "103 kg / 50 m",
    "farmers_carry": "2x24 kg / 200 m",
    "sandbag_lunges": "20 kg / 100 m",
    "wall_balls": "6 kg to 2.7 m, 100 reps",
    "ski_erg": "1000 m",
    "rowing": "1000 m",
    "burpee_broad_jump": "bodyweight, 80 m",
}
# Known athlete capacities (update as she reports a movement feeling too light or
# heavy). Barbell lifts are prescribed by RPE until working weights are provided.
ATHLETE_LOADS: dict[str, str] = {
    "wall_balls": "7 kg, comfortable (heavier than the 6 kg Women's Pro standard)",
    "sandbag_lunges": "20 kg, comfortable (matches Women's Pro standard)",
    "sled": "comfortable at Doubles load; building toward Women's Pro (push 152 kg / pull 103 kg)",
}


# Current block: four races in six weeks after Hybrid Monkey VI (2026-11-14, mixed
# doubles Open, an A race she has just finished). All four are entered as Singles
# Pro. Hyathlon Chile is the A race; Hybrid Games is a B race eight days before it;
# the Corrida (a pure run) and Thund3r (a short 4 or 6.4 km hybrid) are C races to
# train through. Daily, undulated Hyrox program (~6 substantive sessions + an
# active-recovery day). Her interval RUN days are Monday and Wednesday (per her own
# report -- this overrides the Garmin calendar), so running_source is "fixed": on
# those days the Hyrox work is upper-body/technique to spare her running legs, and
# the hard Hyrox sessions land on the other days.
HYBRID_MONKEY_VI = Race(
    name="Hybrid Monkey VI",
    day=date(2026, 11, 14),
    priority="A",
    kind="hybrid",
    category="Mixed doubles Open",
    venue="La Reina, Santiago",
)
RACE_CALENDAR: tuple[Race, ...] = (
    Race(
        name="Carrera Scuola",
        day=date(2026, 11, 22),
        priority="C",
        kind="run",
        category="Run (distance not confirmed)",
        venue="Santiago (not confirmed)",
    ),
    Race(
        name="Hybrid Games",
        day=date(2026, 12, 5),
        priority="B",
        kind="hybrid",
        category="Individual Pro",
        venue="Fundo Colmito, Concon",
    ),
    Race(
        name="Hyathlon Chile",
        day=date(2026, 12, 13),
        priority="A",
        kind="hybrid",
        category="Pro Individual (8x8)",
        venue="Playa del Deporte, Vina del Mar",
    ),
    Race(
        name="Thund3r Hybrid",
        day=date(2026, 12, 26),
        priority="C",
        kind="hybrid",
        category="Individual (Pro, as entered)",
        venue="Estadio Nacional, Nunoa",
    ),
)
DEFAULT_CONFIG = PlanConfig(
    target_race="hyrox",
    race_date=date(2026, 12, 13),
    races=RACE_CALENDAR,
    previous_race=HYBRID_MONKEY_VI,
    division=HYROX_DIVISION,
    sessions_per_week=6,
    strength_per_week=2,
    rest_days=(),
    model="claude-code",
    recent_window_days=28,
    gym_access="full",
    running_source="fixed",
    running_days=("Monday", "Wednesday"),
    # The Garmin account currently ingested is not hers (it carries her partner's
    # life-optimizer workouts). Flip to True once her own Garmin is connected.
    athlete_data_connected=False,
    # Deliberately short taper: she trains through race week and eases off only on
    # the Thursday (activation) and Friday (rest) before the race.
    taper_weeks=0,
)
