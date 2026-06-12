# Telemetry + cache: local or hosted Postgres

The cache and cost telemetry are **optional**. With `RESEARCH_DATABASE_URL` unset (or psycopg2
not installed), the engine runs identically — it just doesn't cache across runs or record cost.
Turn it on by pointing at **any** Postgres: a local one or a hosted one (Neon, RDS, Supabase).

## Option A — local Postgres (zero hosted dependencies)

```bash
# 1. install + start Postgres (example: macOS Homebrew)
brew install postgresql@16 && brew services start postgresql@16

# 2. create a database
createdb gtm_research

# 3. point the engine at it
export RESEARCH_DATABASE_URL="postgresql://localhost:5432/gtm_research"

# 4. apply the schema (cache + telemetry)
psql "$RESEARCH_DATABASE_URL" -f storage/postgres/schema.sql

# 5. (optional) the "do we already know them?" table
psql "$RESEARCH_DATABASE_URL" -f storage/postgres/known-companies-optional.sql
```

Or with Docker:

```bash
docker run -d --name gtm-research-pg -e POSTGRES_PASSWORD=pg -p 5432:5432 postgres:16
export RESEARCH_DATABASE_URL="postgresql://postgres:pg@localhost:5432/postgres"
psql "$RESEARCH_DATABASE_URL" -f storage/postgres/schema.sql
```

Install the driver: `pip install psycopg2-binary`.

## Option B — hosted Postgres

Any provider works — just use its connection string:

```bash
export RESEARCH_DATABASE_URL="postgresql://user:pass@host:5432/dbname?sslmode=require"
psql "$RESEARCH_DATABASE_URL" -f storage/postgres/schema.sql
```

## DSN resolution

`research_engine/research_db.py` reads `RESEARCH_DATABASE_URL` first, then falls back to
`DATABASE_URL`. So if you drop this engine into a `gtm-pipeline` checkout that already sets
`DATABASE_URL`, the `research` schema lives alongside `gtm-pipeline`'s tables in the same
database automatically — no extra config.

## What you get once it's on

```sql
-- second fetch/search of the same URL/query is free
SELECT url, fetch_count FROM research.page_cache ORDER BY fetch_count DESC;

-- the axis check: search credits vs. model turns, per run
SELECT * FROM research.v_run_cost_split;

-- free-pool burn this month, per rung — compare to budgets in the yaml
SELECT * FROM research.v_free_pool_month;
```

A verified-rate **watchdog** (`research.v_run_verified_rate`) lets the workflow pause a run when
a provider silently goes dark instead of producing plausible-but-unverified rows.
