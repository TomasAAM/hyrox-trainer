-- Training plan: HR zones (from Garmin) + LLM-generated weekly Hyrox prescriptions.
--
-- training_zones is seeded from Garmin's configured heart-rate zones (or a
-- max-HR estimate fallback) by plan.zones. There is no lactate test and no
-- running pace component, so the pace columns are kept nullable and unused.
-- The plan tables are regenerated each run: training_plan_weeks holds one
-- audited row per planned week, planned_sessions holds the daily prescriptions.

-- Five heart-rate training zones (pace columns kept for schema parity, unused).
CREATE TABLE IF NOT EXISTS training_zones (
    zone_index         int PRIMARY KEY,         -- 1..5, ascending intensity
    zone_name          text NOT NULL,
    hr_low             int,                      -- null = open at the low end (Z1)
    hr_high            int,                      -- null = open at the high end (Z5)
    pace_low_s_per_km  int,                      -- unused (no running component); null
    pace_high_s_per_km int,                      -- unused (no running component); null
    source_test_date   date NOT NULL,           -- date the zones were sourced/seeded
    lt2_hr             int,                      -- threshold HR anchor (Garmin LTHR or Z4 floor)
    lt2_pace_s_per_km  int,                      -- unused; null
    lt1_hr             int,                      -- unused; null
    lt1_pace_s_per_km  int,                      -- unused; null
    source             text,                     -- 'garmin' | 'max_hr_estimate'
    updated_at         timestamptz DEFAULT now()
);

-- One row per generated plan week. input_summary + rationale + methodology form
-- the audit trail explaining why the week looks the way it does.
CREATE TABLE IF NOT EXISTS training_plan_weeks (
    week_start       date PRIMARY KEY,           -- Monday of the plan week
    target_race      text NOT NULL,              -- 'hyrox'
    race_date        date,
    phase            text NOT NULL,              -- base | build | peak | taper | off
    weeks_to_race    int,
    load_target_low  numeric,                    -- ACWR-bounded weekly load band
    load_target_high numeric,
    model            text,                       -- generator id used for the week
    input_summary    jsonb,                      -- recent-data snapshot fed to the generator
    rationale        text,                       -- generator's explanation for the week
    methodology      text,                       -- training principles applied
    generated_at     timestamptz DEFAULT now()
);

-- One row per prescribed session. prescription holds the structured detail
-- (rounds, stations, reps, loads, steps, why) as JSON.
CREATE TABLE IF NOT EXISTS planned_sessions (
    week_start    date NOT NULL REFERENCES training_plan_weeks(week_start) ON DELETE CASCADE,
    session_date  date NOT NULL,
    session_type  text NOT NULL,                 -- strength | functional | sim | rest | cross | run
    title         text,
    zone          text,                          -- HR zone name, 'mixed', or null
    intensity     text,                          -- easy | moderate | hard
    prescription  jsonb,                         -- {detail, duration_min, distance_m, steps[], why}
    purpose       text,
    hyrox_focus   text,
    PRIMARY KEY (session_date, session_type)
);

CREATE INDEX IF NOT EXISTS idx_planned_sessions_week ON planned_sessions (week_start);
