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

- `GARMIN_EMAIL`, `GARMIN_PASSWORD` — her Garmin Connect login. Only needed for
  the initial login; afterwards runs resume from a stored token.
- `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` — her own Supabase project.
- `GARMIN_TOKENS` — optional, serialized Garmin token JSON. An escape hatch for
  environments that cannot reach the Supabase token store.

### 2. Database

Apply the migrations in `migrations/` to her Supabase project (SQL editor or
CLI), in order: `0001_initial_schema.sql`, `0002_training_plan.sql`, then
`0003_garmin_auth.sql`.

### 3. Configure the plan

Edit `plan/config.py` → `DEFAULT_CONFIG`:

- `race_date` — her real race date (currently a placeholder).
- `sessions_per_week` / `strength_per_week` — her weekly Hyrox availability.
- `ATHLETE_LOADS` — her known station capacities.

### 4. Run the pipeline

```bash
python -m ingest.bootstrap_token  # once: seed the Garmin token store (see below)
python -m ingest.run        # backfill ~90 days of Garmin data
python -m plan.zones        # seed HR zones from Garmin (max-HR estimate fallback)
python -m plan.context      # print the generation brief (read her scheduled runs)
# write the PlannedWeek JSON to data/plan_week.json (Claude Code agent), then:
python -m plan.persist      # validate + store the week
python -m dashboard.build   # render public/index.html
```

### Planning several weeks at once

A whole block up to the race can be planned in one pass. Each week is written to
its own `data/plan_week_<Monday>.json`; the date in the filename is what tells
`plan.persist` which week the JSON belongs to.

```bash
python -m plan.context --all                    # a brief per remaining week
python -m plan.persist --all                    # store every data/plan_week_*.json
```

Single weeks still work as before, and `--week-start` overrides the filename:

```bash
python -m plan.context --week-start 2026-08-24
python -m plan.persist data/plan_week_2026-08-24.json
```

The dashboard's Training plan tab shows a week selector across every stored week
for the current `race_date`, so the whole block is browsable from one page.

> A week planned far in advance is built on **today's** recovery snapshot — the
> load band and any auto-regulation reflect the day it was generated, not the day
> it will be trained. Re-running the generator for a later week refreshes it
> against current data.

## GitHub Actions

The workflow runs daily at 09:00 UTC (and on manual dispatch): ingests Garmin
data, refreshes HR zones (best-effort), builds the dashboard, and deploys to
Pages. Running daily also keeps the Supabase project from being paused for
inactivity.

Required secrets: `GARMIN_EMAIL`, `GARMIN_PASSWORD`, `SUPABASE_URL`,
`SUPABASE_SERVICE_ROLE_KEY`.

### Garmin authentication in CI

Garmin answers SSO password logins from datacentre IP ranges with a Cloudflare
bot challenge (HTTP 403), so a GitHub runner can never log in from scratch — it
can only resume from a token issued elsewhere. Authentication therefore resolves
in this order:

1. the `garmin_auth` row in Supabase (the only route that works in CI),
2. a `GARMIN_TOKENS` env var holding serialized token JSON,
3. local `.garth_tokens/`, then `~/.garminconnect` (the Garmin MCP server's
   default location),
4. a full SSO login — residential connections only.

Seed the store once from a machine that can log in:

```bash
python -m ingest.bootstrap_token
```

Garmin rotates the refresh token, so every successful run writes the new one
back to `garmin_auth`. Re-run the bootstrap only if the stored token is ever
rejected — the ingestion job will say so explicitly.

> The `garmin_auth.tokens` value is a live credential for the Garmin account.
> The table has RLS enabled with no policies, so only the service-role key can
> read it. Never expose it through a client-side key.

### Failure behaviour

Ingestion failures propagate and fail the job, and the run additionally asserts
that the newest wellness row is at most two days old. Both exist because the
opposite behaviour hid a nineteen-day outage: the login was failing, the error
was logged and swallowed, the job exited 0, and the dashboard kept serving stale
data that read as *excellent* form (TSB +54.9 against an actual −26.8). If the
data does go stale anyway, the dashboard renders a warning banner above the
metrics rather than presenting them as current.

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
| `garmin_auth` | Rotating Garmin OAuth token (service-role only) |
