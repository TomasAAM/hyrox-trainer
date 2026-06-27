# Hyrox Trainer

A Hyrox-only training dashboard: ingests Garmin Connect data into Supabase,
computes training-load and recovery metrics, and generates an evidence-graded,
periodized weekly **Hyrox** plan rendered as a static HTML dashboard.

This is a standalone sibling of the `life-optimizer-dashboard` project, focused
purely on the training subsystem and adapted for an athlete who:

- trains for **Hyrox** (Women's Pro station standards — also covers Doubles), and
- **follows her own separate running plan** — so this app **does not prescribe
  runs**. Her runs still flow in via Garmin (feeding the load/recovery model),
  and the generator reads her **upcoming scheduled runs from Garmin** to place
  hard Hyrox work away from her hard run days.

## Architecture

- **Garmin Connect** — daily wellness (HRV, sleep, stress, body battery, HR,
  respiration), training readiness, activities (incl. multisport/HYROX), and the
  athlete's configured heart-rate zones + scheduled workouts.
- **Supabase** — raw data storage + the generated plan.
- **Plan generator** — deterministic periodization (`plan/phase.py`) + a
  Claude-Code-written weekly `PlannedWeek` validated against `plan/models.py`.
- **Dashboard** — a self-contained `public/index.html` (Plotly) with three tabs:
  Training load, Training plan, Heart-rate zones.
- **GitHub Actions** — weekly ingestion + build, deployed to GitHub Pages.

There is **no lactate test and no running pace component**: intensity is
expressed with heart-rate zones (from Garmin) and RPE.

## Setup

### 1. Environment

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env           # fill in the four values below
```

`.env` keys:

- `GARMIN_EMAIL`, `GARMIN_PASSWORD` — her Garmin Connect login.
- `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` — her own Supabase project.

### 2. Database

Apply the migrations in `migrations/` to her Supabase project (SQL editor or
CLI), in order: `0001_initial_schema.sql`, then `0002_training_plan.sql`.

### 3. Configure the plan

Edit `plan/config.py` → `DEFAULT_CONFIG`:

- `race_date` — her real race date (currently a placeholder).
- `sessions_per_week` / `strength_per_week` — her weekly Hyrox availability.
- `ATHLETE_LOADS` — her known station capacities.

### 4. Run the pipeline

```bash
python -m ingest.run        # backfill ~90 days of Garmin data
python -m plan.zones        # seed HR zones from Garmin (max-HR estimate fallback)
python -m plan.context      # print the generation brief (read her scheduled runs)
# write the PlannedWeek JSON to data/plan_week.json (Claude Code agent), then:
python -m plan.persist      # validate + store the week
python -m dashboard.build   # render public/index.html
```

## GitHub Actions

The workflow runs every Sunday 09:00 UTC (and on manual dispatch): ingests Garmin
data, refreshes HR zones (best-effort), builds the dashboard, and deploys to
Pages.

Required secrets: `GARMIN_EMAIL`, `GARMIN_PASSWORD`, `SUPABASE_URL`,
`SUPABASE_SERVICE_ROLE_KEY`.

> Note: CI performs a fresh Garmin SSO login. If her account has MFA enabled,
> seed a token locally first (`.garth_tokens/`) or disable MFA for automation —
> otherwise run ingestion locally.

## Tests

```bash
pytest
```

## Database schema

| Table | Description |
|---|---|
| `garmin_daily_wellness` | Daily biometric summary |
| `garmin_hrv_readings` | Nightly HRV (multiple readings/night) |
| `garmin_heart_rate_readings` | All-day HR |
| `garmin_stress_readings` | All-day stress |
| `garmin_training_readiness` | Readiness snapshots |
| `garmin_activities` | Activities incl. multisport/HYROX (source of training load) |
| `training_zones` | Five heart-rate zones (from Garmin) |
| `training_plan_weeks` | One audited row per generated week |
| `planned_sessions` | Daily Hyrox prescriptions |
