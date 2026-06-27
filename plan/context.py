"""Gather the data brief for the weekly Hyrox plan generation.

No LLM API is called here. This script assembles everything a Claude Code agent
(driven by ``/loop`` or ``/schedule``) needs to write the upcoming week: the
deterministic periodization phase, the Garmin heart-rate zones, a summary of
recent training and recovery, the athlete's scheduled runs (from her own running
plan, read from Garmin), and the guardrails. The agent reads this brief, writes a
``PlannedWeek`` JSON file, then runs ``plan.persist`` to save it.

Running is NOT prescribed: she follows her own running plan. This brief tells the
generator to schedule Hyrox strength/station work around her scheduled runs.

Run with ``python -m plan.context`` to print the brief.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv

from dashboard import metrics, query
from ingest import garmin as ingest_garmin
from plan import phase
from plan.config import (
    ATHLETE_LOADS,
    DEFAULT_CONFIG,
    HYROX_DIVISION,
    HYROX_STANDARDS,
    HYROX_STATIONS,
    STRENGTH_LIBRARY,
    PlanConfig,
)

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(_PROJECT_ROOT / ".env", override=True)

PLAN_FILE = _PROJECT_ROOT / "data" / "plan_week.json"


@dataclass(frozen=True)
class ContextBundle:
    """Everything needed to generate and later persist a plan week.

    Parameters
    ----------
    cfg : PlanConfig
        The active plan configuration.
    week_start : datetime.date
        Monday of the week being planned.
    phase_name : str
        Deterministic periodization phase.
    weeks_remaining : int
        Whole weeks until the race.
    load_band : tuple of float
        ACWR-bounded weekly training-load target.
    summary : dict
        Recent training and recovery snapshot (the audit input).
    zones : pandas.DataFrame
        Garmin heart-rate zones.
    runs : list of dict
        Her scheduled runs for the plan week (date + title), from Garmin.
    running_days : list of str
        Weekday names she is expected to run (derived from ``runs`` or config).
    """

    cfg: PlanConfig
    week_start: date
    phase_name: str
    weeks_remaining: int
    load_band: tuple[float, float]
    summary: dict[str, Any]
    zones: pd.DataFrame
    runs: list[dict[str, Any]]
    running_days: list[str]


def _recent_summary(
    activities: pd.DataFrame,
    load_series: pd.DataFrame,
    snapshot: metrics.ReadinessSnapshot,
    readiness: pd.DataFrame,
    window_days: int,
) -> dict[str, Any]:
    """Summarize recent training and recovery into a compact, auditable dict."""
    acwr = round(snapshot.atl / snapshot.ctl, 2) if snapshot.ctl > 0 else None
    cutoff = pd.Timestamp(snapshot.date) - pd.Timedelta(days=window_days)
    recent = load_series[load_series.index >= cutoff]
    last7 = load_series[load_series.index >= pd.Timestamp(snapshot.date) - pd.Timedelta(days=7)]

    sessions: list[dict[str, Any]] = []
    if not activities.empty:
        act = activities.copy()
        act["d"] = pd.to_datetime(act["start_time_local"]).dt.normalize()
        act = act[act["d"] >= cutoff].sort_values("d")
        for r in act.itertuples():
            sessions.append(
                {
                    "date": r.d.date().isoformat(),
                    "type": r.activity_type,
                    "name": r.activity_name,
                    "load": None if pd.isna(r.training_load) else round(float(r.training_load)),
                }
            )

    latest_readiness: dict[str, Any] = {}
    if not readiness.empty:
        rd = readiness.sort_values("date").iloc[-1]
        latest_readiness = {
            "date": str(rd.get("date")),
            "score": None if pd.isna(rd.get("score")) else int(rd["score"]),
            "level": rd.get("level"),
            "recovery_time_h": None
            if pd.isna(rd.get("recovery_time_h"))
            else int(rd["recovery_time_h"]),
        }

    return {
        "as_of": snapshot.date.date().isoformat(),
        "ctl_fitness": snapshot.ctl,
        "atl_fatigue": snapshot.atl,
        "tsb_form": snapshot.tsb,
        "tsb_label": snapshot.tsb_label,
        "acwr": acwr,
        "load_last_7d": round(float(last7["load"].sum())),
        f"load_last_{window_days}d": round(float(recent["load"].sum())),
        f"training_days_last_{window_days}d": int((recent["load"] > 0).sum()),
        "hrv_last_night": snapshot.hrv_night,
        "hrv_status": snapshot.hrv_status,
        "readiness": latest_readiness,
        "recent_sessions": sessions,
    }


def _scheduled_runs(cfg: PlanConfig, week_start: date) -> list[dict[str, Any]]:
    """Read her scheduled runs for the plan week from Garmin (best-effort)."""
    if cfg.running_source != "garmin_scheduled":
        return []
    week_end = week_start + timedelta(days=6)
    try:
        client = ingest_garmin.get_client()
        return ingest_garmin.get_scheduled_runs(client, week_start, week_end)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read scheduled runs from Garmin: %s", exc)
        return []


def gather(cfg: PlanConfig = DEFAULT_CONFIG) -> ContextBundle:
    """Pull recent data and compute the deterministic plan context.

    Parameters
    ----------
    cfg : PlanConfig
        Plan configuration (race date, availability, etc.).

    Returns
    -------
    ContextBundle
        The computed phase/load context plus recent-data summary, zones, and her
        scheduled runs for the week.
    """
    supabase = query.get_supabase_client()
    activities = query.fetch_activities(supabase)
    if activities.empty:
        raise RuntimeError("No activities available; cannot build plan context")

    readiness = query.fetch_readiness(supabase)
    zones = query.fetch_training_zones(supabase)
    hrv_series = metrics.build_hrv_series(query.fetch_hrv(supabase))
    load_series = metrics.build_load_series(activities)
    snapshot = metrics.latest_snapshot(load_series, hrv_series)

    week_start = phase.upcoming_monday(date.today())
    phase_name, weeks_remaining = phase.phase_for_week(week_start, cfg.race_date)
    load_band = phase.load_target_band(snapshot.ctl * 7.0, phase_name)
    summary = _recent_summary(activities, load_series, snapshot, readiness, cfg.recent_window_days)

    runs = _scheduled_runs(cfg, week_start)
    if runs:
        running_days = sorted(
            {date.fromisoformat(r["date"]).strftime("%A") for r in runs},
            key=lambda d: ["Monday", "Tuesday", "Wednesday", "Thursday",
                           "Friday", "Saturday", "Sunday"].index(d),
        )
    else:
        running_days = list(cfg.running_days)
    summary["scheduled_runs"] = runs

    return ContextBundle(
        cfg=cfg,
        week_start=week_start,
        phase_name=phase_name,
        weeks_remaining=weeks_remaining,
        load_band=load_band,
        summary=summary,
        zones=zones,
        runs=runs,
        running_days=running_days,
    )


def _format_zones(zones: pd.DataFrame) -> str:
    """Render the heart-rate zone table as a compact text block."""
    if zones.empty:
        return "  (no zones — run plan.zones first)"
    lines = []
    for z in zones.sort_values("zone_index").itertuples():
        low = int(z.hr_low) if pd.notna(z.hr_low) else "<"
        high = int(z.hr_high) if pd.notna(z.hr_high) else ">"
        lines.append(f"  Z{z.zone_index} {z.zone_name}: HR {low}-{high} bpm")
    return "\n".join(lines)


def _format_runs(runs: list[dict[str, Any]], running_days: list[str]) -> str:
    """Render her scheduled runs (or the fixed-day fallback) as a text block."""
    if runs:
        lines = []
        for r in runs:
            weekday = date.fromisoformat(r["date"]).strftime("%a")
            lines.append(f"  {weekday} {r['date']}: {r.get('title') or 'run'}")
        return "\n".join(lines)
    if running_days:
        return f"  Fixed running days (from config): {', '.join(running_days)}"
    return (
        "  (no scheduled runs found in Garmin — assume she may run on any day; keep at "
        "least one genuinely easy/buffer day and avoid stacking two hard days back-to-back)"
    )


def render_brief(bundle: ContextBundle) -> str:
    """Render the human/agent-readable generation brief.

    Parameters
    ----------
    bundle : ContextBundle
        Output of :func:`gather`.

    Returns
    -------
    str
        The full brief: role, periodization, zones, recent data, her scheduled
        runs, guardrails, and the exact JSON shape to write to ``data/plan_week.json``.
    """
    cfg = bundle.cfg

    schema_example = {
        "rationale": "2-4 sentences: how this week reflects the phase, recent load/recovery, "
        "her scheduled runs, and any auto-regulation applied.",
        "methodology": "3-5 sentences naming the principles applied (station specificity at "
        "race standards, strength-endurance for compromised work, heavy/explosive strength for "
        "economy and sled power kept off her hard run days, gradual load, taper near race). "
        "Principles only — no invented citations.",
        "sessions": [
            {
                "day": "Monday",
                "session_type": "strength | functional | sim | rest | cross | run",
                "title": "e.g. Compromised sled + wall balls",
                "zone": "Recovery|Endurance|Aerobic|Threshold|Maximum|mixed|null",
                "intensity": "easy | moderate | hard",
                "duration_min": 60,
                "distance_m": 2000,
                "prescription": "Full detail (one-line fallback): rounds, stations, reps, "
                "loads, target HR zone / RPE, recoveries.",
                "steps": [
                    {"phase": "warmup", "kind": "note", "metric": "8 min easy bike + mobility",
                     "target": "Z1-Z2", "load": None},
                    {"phase": "main", "kind": "station", "metric": "sled push 4x12.5 m",
                     "target": "Z4, then 60s walk", "load": "152 kg"},
                    {"phase": "main", "kind": "station", "metric": "40 wall balls",
                     "target": "unbroken if able", "load": "6 kg"},
                    {"phase": "main", "kind": "rest", "metric": "90s rest", "target": None,
                     "load": None},
                    {"phase": "cooldown", "kind": "note", "metric": "5 min easy + stretch",
                     "target": "Z1", "load": None},
                ],
                "purpose": "One sentence on the training purpose.",
                "why": "Why this session at this dose today, and why not more — tied to a "
                "principle (e.g. 'station specificity at race load; held to 4 rounds because "
                "her Wed run is a hard interval session and acute load is already high').",
                "hyrox_focus": "sled | wall balls | compromised running | grip / carries | ... | null",
            }
        ],
    }

    return f"""You are an expert HYROX strength & conditioning coach. Write one focused training \
week for a female athlete training toward HYROX. She competes DOUBLES but trains to be ready for \
WOMEN'S PRO, so prescribe station work at the Women's Pro standards below. \
CRITICAL: she follows her OWN separate running plan — do NOT prescribe standalone runs. Your job \
is the Hyrox-specific work: strength, station/functional conditioning, and race simulations. \
Express intensity with HEART-RATE ZONES (below) and RPE — never running pace. Auto-regulate: when \
recovery is poor (low readiness, strongly negative TSB/form, rising load), cut intensity and \
volume rather than pushing on. Her runs already show up in the recent-load data below, so respect \
total stress.

TARGET: {cfg.target_race.upper()} on {cfg.race_date.isoformat()} | division trained: {HYROX_DIVISION}
WEEK TO PLAN: Monday {bundle.week_start.isoformat()}

PERIODIZATION (deterministic — do not override):
  Phase: {bundle.phase_name}   Weeks to race: {bundle.weeks_remaining}
  Weekly training-load target band: {int(bundle.load_band[0])}-{int(bundle.load_band[1])} (Garmin units, TOTAL incl. her runs)

HEART-RATE ZONES (from Garmin; use for conditioning intensity, alongside RPE):
{_format_zones(bundle.zones)}

HER SCHEDULED RUNS THIS WEEK (her own plan — schedule Hyrox HARD days AROUND these):
{_format_runs(bundle.runs, bundle.running_days)}

RECENT TRAINING & RECOVERY (auto-regulate off this; includes her runs):
{json.dumps(bundle.summary, indent=2)}

AVAILABILITY & STRUCTURE:
  {cfg.sessions_per_week} Hyrox-specific sessions/week: ~{cfg.strength_per_week} gym-strength + \
the rest functional/station/simulation. The other weekdays are her running/recovery days — mark \
them session_type "rest" with a short note (do NOT program runs).
  Default non-Hyrox day(s): {", ".join(cfg.rest_days)}.
  Gym access: {cfg.gym_access} — program heavy barbell and explosive/plyometric work, not only \
bodyweight circuits.
  Hyrox stations: {", ".join(HYROX_STATIONS)}.
  Strength/explosive movements: {", ".join(STRENGTH_LIBRARY)}.

LOADS ({HYROX_DIVISION}) — prescribe station work AT these competition standards:
{chr(10).join(f"  {k}: {v}" for k, v in HYROX_STANDARDS.items())}
  Known athlete capacity: {", ".join(f"{k} {v}" for k, v in ATHLETE_LOADS.items())}.
  For barbell lifts, prescribe by RPE (e.g. 4-5 reps @ RPE 7-8) — exact working weights not yet known.

GUARDRAILS:
  - Exactly 7 entries, one per weekday Monday..Sunday. Use session_type "rest" for her
    running/recovery days (with a one-line note like "Your own run / recovery — no Hyrox session").
  - Do NOT prescribe standalone runs. Short shuttle/transition runs are allowed only INSIDE a
    Hyrox simulation (kind "run"), to drill compromised running between stations.
  - Keep "hard" Hyrox days separated by >= 1 easy/rest day AND off her hard run days (see her
    scheduled runs above) — concurrent heavy/explosive work next to hard running blunts both.
  - Use the full gym: at least one session should include heavy compound or explosive lifts
    (squat, trap-bar deadlift, hip thrust, jumps) for economy and sled power.
  - In build/peak include >= 1 compromised-conditioning / station-circuit session and >= 1
    strength-endurance or simulation session.
  - In taper, cut volume but keep some race-load station intensity.
  - If readiness is LOW or TSB is strongly negative, downgrade the hardest session(s) and say so.
  - STEPS = typed segments rendered as Runna-style rows. Each segment has: phase (warmup/main/
    cooldown, or null), kind (station/strength/run/rest/note), metric (the bold dose), target
    (sub-line: HR zone / RPE or a note), load (kg for station/strength, else null). List a
    repeated block (e.g. 4 rounds) as its individual work + rest segments, all phase "main".
  - DETAIL & CONSISTENCY: be explicit and unambiguous. State the exact number of rounds/sets/reps —
    never leave the reader guessing how many times to repeat a block. `distance_m` and
    `duration_min` MUST equal the sum across the steps.
  - LOADS: give a concrete weight for every strength/station movement — station work at the Women's
    Pro standard above, barbell lifts by RPE. State reps and rest. Never write a loadless
    "sled push" or "wall balls" without the kg.
  - Fill `why` for every session (the justification AND the trade-off — why not more), and the
    week-level `methodology` (principles only). Do NOT invent citations; sources are curated separately.

OUTPUT: write JSON matching this shape to {PLAN_FILE}, then run `python -m plan.persist`:
{json.dumps(schema_example, indent=2)}
"""


def main() -> None:
    """Print the generation brief to stdout."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    print(render_brief(gather()))


if __name__ == "__main__":
    main()
