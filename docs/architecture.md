# Architecture

```
                         ┌─────────────────────────────────────────────┐
                         │  .claude/workflows/entity-research.js        │
                         │  one bounded agent per entity → verify pass  │
                         │  domain-grouped, chunked, watchdog-gated     │
                         └───────────────┬─────────────────────────────┘
                       calls (Bash)      │
            ┌────────────────────────────┼────────────────────────────┐
            ▼                            ▼                            ▼
  bin/research-search.py        bin/page-digest.py            bin/known-xref.py
  (SEARCH chokepoint)           (FETCH chokepoint)            (optional internal xref)
            │                            │                            │
            │ reads ▼                    │ reads ▼                    ▼
        config/research-waterfall.yaml (the ordered cost policy)   research.known_companies
            │                            │
            ▼                            ▼
  research_engine/                research_engine/
    waterfall.py  (policy + invoke_when)  web.py (native + Tavily)
    search_providers.py (ddg/brave/...)   providers/jina_reader, parallel_extract
    providers/exa_search, parallel_search
            │                            │
            └──────────┬─────────────────┘
                       ▼
          research_engine/research_db.py  ──►  Postgres `research` schema (OPTIONAL)
          cache_get/put · rung_event · entity_telemetry · watchdog       (local or hosted)
```

## Layers

- **Orchestration** (`.claude/workflows/entity-research.js`) — fan-out, verify, domain grouping,
  the trailing-verified-rate watchdog. The research *loop* and the *verify* stay here (your
  Claude agent); they're never delegated to a cheap model.
- **Chokepoints** (`bin/`) — the only two places that read the YAML and walk rungs. They own
  cache-check, cache-write, and `rung_event` for the built-in (keyword/native/tavily) rungs.
- **Engine** (`research_engine/`) — pure, dependency-light: the policy evaluator, the web
  primitives (native fetch + Tavily), the free search rungs, and the optional cache/telemetry.
- **Provider rungs** (`providers/`) — thin wrappers for the escalation rungs (exa, jina,
  parallel). Each auto-skips without its key and writes its own `rung_event`.
- **Storage** (`storage/postgres/`) — the optional `research` schema (cache + telemetry) and the
  optional `known_companies` table for the internal cross-reference.

## Design rules

- **Config drives rungs.** Order/gates/budgets are data (`config/research-waterfall.yaml`), not
  code. The chokepoints are provider-neutral.
- **Cheapest-first, fail-closed.** Escalate only on failure; an unknown `invoke_when` atom skips
  the rung (a typo can't fire a paid one).
- **Telemetry/cache are optional and best-effort.** No DSN, or a DB outage, degrades to "just run"
  — never an error.
- **Verification enums.** Every field is verified+sourced, blank+NOT FOUND, or a labeled guess.
