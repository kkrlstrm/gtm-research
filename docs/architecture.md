# Architecture

`gtm-research` is five thin layers: an orchestration workflow, two deterministic
chokepoints, a dependency-light engine, optional provider rungs, and optional storage.
Each layer has one job, and the boundaries are where the design rules live.

## The shape

```
                  ┌─────────────────────────────────────────────┐
                  │  .claude/workflows/entity-research.js        │
                  │  one bounded agent per entity → verify pass  │
                  │  domain-grouped, chunked, watchdog-gated     │
                  └───────────────┬─────────────────────────────┘
                calls (Bash)      │
     ┌────────────────────────────┼────────────────────────────┐
     ▼                            ▼                            ▼
  research-search.py        page-digest.py               known-xref.py
  (SEARCH chokepoint)       (FETCH chokepoint)           (optional internal xref)
     │                            │                            │
     │ reads ▼                    │ reads ▼                    ▼
   config/research-waterfall.yaml (the ordered cost policy)  research.known_companies
     │                            │
     ▼                            ▼
   research_engine/  waterfall · web · search_providers · research_db
   providers/        exa_search · jina_reader · parallel_search · parallel_extract
     │
     ▼
   Postgres `research` schema (OPTIONAL — local or hosted)
   cache · rung_event · entity_telemetry · watchdog views
```

## The five layers

### 1. Orchestration — `.claude/workflows/entity-research.js`

Fans out one bounded research agent per entity, groups them by domain (so company-level
facts are resolved once and shared with siblings), runs them in watchdog-gated chunks, and
follows each with a verify pass.

The research **loop** (deciding what to fetch) and the **verify** (deciding whether a claim
is supported) stay here, with your Claude agent. They are never delegated to a cheap model.

### 2. Chokepoints — `bin/`

The only two places that read the YAML policy and walk the rungs:

- **`research-search.py`** — the SEARCH chokepoint.
- **`page-digest.py`** — the FETCH chokepoint.

They own the cache check (before any rung), the cache write (after a success), and the
`rung_event` record for the built-in rungs (keyword, native, tavily). A third optional CLI,
**`known-xref.py`**, adds the internal cross-reference column.

### 3. Engine — `research_engine/`

Pure and dependency-light (just `requests` + `PyYAML`; `psycopg2`/`bs4` optional):

- **`waterfall.py`** — parses the policy and evaluates `invoke_when` (fail-closed, no `eval()`).
- **`web.py`** — the web primitives: free native fetch + Tavily extract/search.
- **`search_providers.py`** — the free keyword search rungs (ddg → brave → claude_cli → serper).
- **`research_db.py`** — the optional cache + telemetry, all best-effort.

### 4. Provider rungs — `providers/`

Thin wrappers for the escalation rungs: `exa_search`, `jina_reader`, `parallel_search`,
`parallel_extract`. Each auto-skips when its API key is absent and writes its own `rung_event`.
Adding a new provider is a wrapper here plus a line in the YAML — never a change to a chokepoint.

### 5. Storage — `storage/postgres/`

Both optional, both plain Postgres (local or hosted):

- **`schema.sql`** — the `research` schema: page/search cache, run + entity telemetry, rung
  events, and the cost/watchdog views.
- **`known-companies-optional.sql`** — the `known_companies` table that powers the internal
  cross-reference.

## How one entity flows through it

1. The workflow opens a run row (if a DB is configured) and dispatches a research agent for the entity.
2. The agent calls `research-search.py` to find sources — cache → free keyword → Exa → Tavily → Parallel, stopping at the first hit.
3. The agent calls `page-digest.py` to read each source — cache → native → Jina → Tavily → Parallel, then compresses the page if it's long.
4. For a company, the agent runs `known-xref.py` once and copies the `internal_status` verbatim into every finding.
5. A verify agent re-opens each `source_url` (raw, `--no-digest`) and confirms or blanks every field.
6. The workflow writes per-entity telemetry and checks the watchdog; if the trailing verified-rate collapses, it pauses the run.

## Design rules

- **Config drives rungs.** Order, gates, and budgets are data (`config/research-waterfall.yaml`),
  not code. The chokepoints are provider-neutral.
- **Cheapest-first, fail-closed.** A rung escalates only on failure; an unknown `invoke_when`
  atom makes the rung skip, so a typo can never fire a paid rung.
- **Telemetry and cache are optional and best-effort.** No DSN — or a DB outage — degrades to
  "just run," never an error.
- **Verification enums.** Every output field is verified+sourced, blank+"NOT FOUND", or a
  labeled `UNVERIFIED` guess. Never invented.
