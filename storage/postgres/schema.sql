-- gtm-research — telemetry + shared cache schema (OPTIONAL).
--
-- Apply to ANY Postgres reachable by a connection string — local or hosted:
--     psql "$RESEARCH_DATABASE_URL" -f storage/postgres/schema.sql
--   or, to share a gtm-pipeline database:
--     psql "$DATABASE_URL"          -f storage/postgres/schema.sql
--
-- Everything here is OPTIONAL: with no database configured the engine still runs,
-- just without the cross-run cache or per-run cost telemetry. Web facts are
-- client-agnostic, so ONE shared cache maximizes the hit rate across every run.

create schema if not exists research;
create extension if not exists pgcrypto;   -- gen_random_uuid()

-- ============================ CACHE ============================
-- Shared page cache, keyed on a hash of the normalized URL.
create table if not exists research.page_cache (
  url_hash      bytea       primary key,                 -- sha256(normalized_url)
  url           text        not null,
  content_hash  bytea,                                   -- sha256(body) for change detection
  http_status   int         not null,
  is_dead       boolean     not null default false,      -- negative cache: 404/410/401
  provider      text        not null,                    -- which rung resolved it
  digest        text,                                    -- compressed/extracted content
  raw_excerpt   text,                                    -- short verbatim for short pages
  fetched_at    timestamptz not null default now(),
  expires_at    timestamptz not null,
  fetch_count   int         not null default 1           -- how many times this entry saved a fetch
);
create index if not exists ix_page_cache_expires on research.page_cache (expires_at);
create index if not exists ix_page_cache_url     on research.page_cache (url);

-- Search-query cache, including negative results ("searched X, found nothing").
create table if not exists research.search_cache (
  query_hash   bytea       primary key,                  -- sha256(rung || '|' || normalized_query)
  query        text        not null,
  rung         text        not null,
  result_urls  jsonb       not null default '[]'::jsonb,
  empty        boolean     not null default false,
  fetched_at   timestamptz not null default now(),
  expires_at   timestamptz not null
);
create index if not exists ix_search_cache_expires on research.search_cache (expires_at);

-- ============================ RUNS ============================
create table if not exists research.run (
  run_id        uuid        primary key default gen_random_uuid(),
  client_schema text,
  entity_type   text        not null,                    -- company | person
  model         text        not null,
  purpose       text,
  brief         text,
  entity_count  int         not null,
  started_at    timestamptz not null default now(),
  finished_at   timestamptz,
  status        text        not null default 'running',  -- running | done | paused | aborted
  verified_rate numeric,
  paused_reason text
);

-- ============== PER-ENTITY TELEMETRY ==============
create table if not exists research.entity_telemetry (
  id                 uuid       primary key default gen_random_uuid(),
  run_id             uuid       not null references research.run(run_id) on delete cascade,
  entity_name        text       not null,
  entity_domain      text,
  orchestrator_turns int        not null default 0,
  tokens_in          int        not null default 0,
  tokens_out         int        not null default 0,
  rung_hits          jsonb      not null default '{}'::jsonb,
  credits_spent      int        not null default 0,
  usd_spent          numeric    not null default 0,
  cache_hits         int        not null default 0,
  verified_fields    int        not null default 0,
  unverified_fields  int        not null default 0,
  internal_status    text,
  duration_ms        int,
  created_at         timestamptz not null default now(),
  unique (run_id, entity_name)
);
create index if not exists ix_entity_telemetry_run on research.entity_telemetry (run_id);
create index if not exists ix_entity_telemetry_internal on research.entity_telemetry (internal_status);

-- ============== ONE ROW PER RUNG INVOCATION ==============
create table if not exists research.rung_event (
  id           bigserial   primary key,
  run_id       uuid        references research.run(run_id) on delete cascade,
  entity_name  text,
  waterfall    text        not null,                     -- search | fetch
  rung         text        not null,                     -- keyword|exa|tavily|parallel|native|jina|digest|cache
  tier         text        not null,                     -- free | credit | gated | cheap_model
  success      boolean     not null,
  from_cache   boolean     not null default false,
  latency_ms   int,
  cost_credits int         not null default 0,
  cost_usd     numeric     not null default 0,
  ts           timestamptz not null default now()
);
create index if not exists ix_rung_event_run  on research.rung_event (run_id, ts);
create index if not exists ix_rung_event_rung on research.rung_event (rung, success);

-- ============================ VIEWS ============================

-- Watchdog feed: verified-rate per run.
create or replace view research.v_run_verified_rate as
select run_id,
       sum(verified_fields)::numeric
         / nullif(sum(verified_fields + unverified_fields), 0) as verified_rate,
       count(*) as entities
from research.entity_telemetry
group by run_id;

-- Free-pool burn this calendar month per rung — compare against budgets in the yaml.
create or replace view research.v_free_pool_month as
select rung,
       date_trunc('month', ts) as month,
       count(*) as calls
from research.rung_event
where tier = 'free'
group by rung, date_trunc('month', ts);

-- The axis check: are search credits or model turns the real spend per run?
create or replace view research.v_run_cost_split as
select r.run_id,
       r.model,
       sum(t.credits_spent)               as tavily_credits,
       sum(t.usd_spent)                   as api_usd,
       sum(t.tokens_in + t.tokens_out)    as orchestrator_tokens,
       sum(t.orchestrator_turns)          as orchestrator_turns,
       sum(t.cache_hits)                  as cache_hits
from research.run r
join research.entity_telemetry t using (run_id)
group by r.run_id, r.model;
