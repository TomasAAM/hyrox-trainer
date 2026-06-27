"""Pydantic schema for the LLM-generated weekly Hyrox plan.

These models define the strict structured-output contract validated before we
persist a generated week. Keeping the shape flat (no free-form dicts) makes the
JSON-schema constraint reliable.

This is a Hyrox-only plan: standalone runs are NOT prescribed (the athlete runs
on her own separate plan). Intensity is expressed with heart-rate zones + RPE,
never running pace.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

DayName = Literal[
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"
]
# 'rest' on a day means "no Hyrox session" — it may be her own running/recovery day.
# 'run' is only used for short shuttle/transition runs inside a Hyrox simulation.
SessionType = Literal["strength", "functional", "sim", "rest", "cross", "run"]
Intensity = Literal["easy", "moderate", "hard"]


StepKind = Literal["station", "strength", "run", "rest", "note"]
StepPhase = Literal["warmup", "main", "cooldown"]


class Step(BaseModel):
    """One typed segment of a session, rendered as a Runna-style row.

    Consecutive segments that share a ``phase`` are grouped under one colored band
    (Warm-up / Main set / Cool-down). A repeated block (e.g. 4 rounds) is listed
    as its individual work + rest segments, all with phase "main".
    """

    phase: Optional[StepPhase] = Field(
        default=None,
        description="Band grouping: warmup, main, or cooldown. Null for a single-effort "
        "session that needs no bands.",
    )
    kind: StepKind = Field(default="note", description="Segment type — drives icon and tag.")
    metric: str = Field(
        description="The bold primary line: the dose, e.g. '4x12.5 m sled push', "
        "'40 wall balls', '1000 m row', '5 reps back squat', '90s rest'.",
    )
    target: Optional[str] = Field(
        default=None,
        description="Sub-line: target HR zone / RPE or a short note, e.g. 'Z4, 155-165 bpm', "
        "'RPE 8', or 'then 60s walk'.",
    )
    load: Optional[str] = Field(
        default=None,
        description="Weight for strength/station moves, e.g. '6 kg', '152 kg', '2x24 kg'. "
        "Null for runs/rest.",
    )


class PlannedSession(BaseModel):
    """A single prescribed session within the week."""

    day: DayName
    session_type: SessionType
    title: str = Field(description="Short session title, e.g. 'Compromised sled + wall balls'.")
    zone: Optional[str] = Field(
        default=None,
        description="Target HR zone name (Recovery/Endurance/Aerobic/Threshold/Maximum), "
        "'mixed' for sessions spanning zones, or null for rest/pure-strength.",
    )
    intensity: Intensity
    duration_min: Optional[int] = Field(
        default=None, description="Planned total duration in minutes, if applicable."
    )
    distance_m: Optional[int] = Field(
        default=None,
        description="Planned total metres of locomotion (erg/row/sled/carry/lunge/shuttle), "
        "if applicable.",
    )
    prescription: str = Field(
        description="Full human-readable detail: structure, rounds, stations, reps, loads, "
        "recoveries. Used as a fallback when steps is empty."
    )
    steps: list[Step] = Field(
        default_factory=list,
        description="The session broken into labelled blocks (warm-up / main set / "
        "cool-down, or rounds). Drives the structured card; leave empty only for a "
        "trivial single-block session or a rest day.",
    )
    purpose: str = Field(description="One sentence on the training purpose.")
    why: str = Field(
        description="The justification AND the trade-off: why this session, at this "
        "dose, today — and why not more (more volume / intensity / load). Tie to the "
        "training principle it serves (e.g. station specificity, strength-endurance for "
        "compromised work, heavy strength for economy, concurrent-training recovery).",
    )
    hyrox_focus: Optional[str] = Field(
        default=None,
        description="Which Hyrox demand this targets (e.g. 'sled', 'wall balls', "
        "'compromised running', 'grip / carries'), or null.",
    )


class PlannedWeek(BaseModel):
    """A full week of prescribed sessions plus the generator's rationale."""

    rationale: str = Field(
        description="2-4 sentences explaining how this week reflects the phase, the "
        "athlete's recent load/recovery, her scheduled runs, and any auto-regulation applied."
    )
    methodology: str = Field(
        description="3-5 sentences naming the training PRINCIPLES this week applies "
        "(station specificity at race standards, strength-endurance for compromised work, "
        "heavy/explosive strength for economy and sled power kept off her hard run days, "
        "gradual load progression, taper near the race) and why they fit a Hyrox athlete "
        "who runs on a separate plan. Reference principles only — do NOT invent citations; "
        "the sources are curated separately.",
    )
    sessions: list[PlannedSession]
