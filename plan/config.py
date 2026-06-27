"""Immutable Hyrox training-plan configuration.

Holds the race target, the athlete's weekly availability, her division standards,
and how her separate running plan is coordinated. Edit ``DEFAULT_CONFIG`` when the
target race, schedule, or availability changes — everything downstream reads from
here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class PlanConfig:
    """Static configuration for the Hyrox plan generator.

    Parameters
    ----------
    target_race : str
        Race being trained for (``"hyrox"``).
    race_date : datetime.date
        Date of the target race; the periodization phase is computed from it.
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

# Target division and its official station standards. She competes Doubles with
# her brother but trains to be ready for Women's Pro, so station work is
# prescribed at the harder Women's Pro loads (which also cover Doubles).
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
# TODO(confirm with her): replace placeholders with her real capacities.
ATHLETE_LOADS: dict[str, str] = {
    "wall_balls": "6 kg, unbroken count TBC",
    "sandbag_lunges": "20 kg, feel TBC",
}


# Current target: edit RACE_DATE to her actual race. 3 Hyrox-specific sessions/
# week (1 gym-strength + 2 functional/station/sim); she runs on her own plan, so
# the other weekdays default to no Hyrox session. Her run days are read from
# Garmin's scheduled workouts.
# TODO(confirm with her): race_date and sessions_per_week.
DEFAULT_CONFIG = PlanConfig(
    target_race="hyrox",
    race_date=date(2026, 11, 1),  # PLACEHOLDER — set her real race date.
    division=HYROX_DIVISION,
    sessions_per_week=3,
    strength_per_week=1,
    rest_days=("Sunday",),
    model="claude-code",
    recent_window_days=28,
    gym_access="full",
    running_source="garmin_scheduled",
    running_days=(),
)
