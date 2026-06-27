-- Initial schema for the Hyrox training dashboard (Garmin-only).
-- Stores raw Garmin wellness time series and activities. Numeric types are used
-- for the fields Garmin returns as floats (e.g. restingHeartRate = 53.0).

-- Garmin daily wellness summary (one row per calendar day)
CREATE TABLE IF NOT EXISTS garmin_daily_wellness (
    date               date PRIMARY KEY,
    resting_hr         numeric,
    min_hr             numeric,
    max_hr             numeric,
    avg_stress         numeric,
    max_stress         numeric,
    avg_spo2           numeric,
    lowest_spo2        numeric,
    body_battery_wake  numeric,
    body_battery_high  numeric,
    body_battery_low   numeric,
    body_battery_now   numeric,
    total_steps        numeric,
    active_calories    numeric,
    total_calories     numeric,
    avg_respiration    numeric,
    fetched_at         timestamptz DEFAULT now()
);

-- Garmin HRV readings (multiple readings per night)
CREATE TABLE IF NOT EXISTS garmin_hrv_readings (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    date              date NOT NULL,
    ts                timestamptz,
    hrv_ms            numeric,
    hrv_avg_night     numeric,
    hrv_weekly_avg    numeric,
    hrv_baseline_low  numeric,
    hrv_baseline_high numeric,
    hrv_status        text,
    UNIQUE (date, ts)
);

-- Garmin per-minute heart rate readings
CREATE TABLE IF NOT EXISTS garmin_heart_rate_readings (
    id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    date    date NOT NULL,
    ts      timestamptz NOT NULL,
    hr_bpm  numeric,
    UNIQUE (date, ts)
);

-- Garmin per-minute stress readings
CREATE TABLE IF NOT EXISTS garmin_stress_readings (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    date         date NOT NULL,
    ts           timestamptz NOT NULL,
    stress_level numeric,
    UNIQUE (date, ts)
);

-- Garmin daily training readiness score
CREATE TABLE IF NOT EXISTS garmin_training_readiness (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    date            date NOT NULL,
    ts              timestamptz,
    context         text,
    score           numeric,
    level           text,
    recovery_time_h numeric,
    acute_load      numeric,
    hrv_weekly_avg  numeric,
    sleep_score     numeric,
    UNIQUE (date, ts)
);

-- Garmin activities: source of truth for training sessions, including unified
-- multisport (HYROX) sessions. Native activityTrainingLoad is the load signal.
-- This table captures ALL her activities, including the runs from her own
-- separate running plan, so the load/recovery model reflects total stress.
CREATE TABLE IF NOT EXISTS garmin_activities (
    activity_id        bigint PRIMARY KEY,
    parent_id          bigint,              -- set on multisport child legs
    start_time         timestamptz NOT NULL, -- from startTimeGMT
    start_time_local   timestamp,
    activity_name      text,
    activity_type      text,                -- activityType.typeKey (e.g. multi_sport)
    parent_type        text,
    duration_s         numeric,
    elapsed_duration_s numeric,
    moving_duration_s  numeric,
    distance_m         numeric,
    elevation_gain_m   numeric,
    avg_hr             numeric,
    max_hr             numeric,
    calories           numeric,
    training_load      numeric,             -- activityTrainingLoad (Garmin's load)
    aerobic_te         numeric,             -- aerobicTrainingEffect
    anaerobic_te       numeric,             -- anaerobicTrainingEffect
    avg_cadence        numeric,
    is_multisport      boolean DEFAULT false,
    fetched_at         timestamptz DEFAULT now()
);

-- Indexes on date / time columns for efficient range queries
CREATE INDEX IF NOT EXISTS idx_garmin_hrv_date            ON garmin_hrv_readings (date);
CREATE INDEX IF NOT EXISTS idx_garmin_hr_date             ON garmin_heart_rate_readings (date);
CREATE INDEX IF NOT EXISTS idx_garmin_stress_date         ON garmin_stress_readings (date);
CREATE INDEX IF NOT EXISTS idx_garmin_readiness_date      ON garmin_training_readiness (date);
CREATE INDEX IF NOT EXISTS idx_garmin_activities_start    ON garmin_activities (start_time);
CREATE INDEX IF NOT EXISTS idx_garmin_activities_parent   ON garmin_activities (parent_id);
CREATE INDEX IF NOT EXISTS idx_garmin_activities_type     ON garmin_activities (activity_type);
