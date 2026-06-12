# Contributing

Thanks for improving `gtm-research`. The design has one organizing rule:

> **Rung order, budgets, and gates are CONFIG. The engine is provider-neutral.**

Where changes belong:

- **A new search/fetch rung** → add a wrapper in `providers/` (auto-skips when its key is
  absent, writes its own `rung_event`) and a rung entry in `config/research-waterfall.yaml`.
  Don't hardcode provider order in the chokepoints.
- **Change which rung runs when / in what order** → edit `config/research-waterfall.yaml`,
  never the Python.
- **New telemetry / cache columns** → `storage/postgres/schema.sql` + the matching helper in
  `research_engine/research_db.py` (keep it best-effort: a DB outage must never break a run).

## Invariants (don't regress these)

- **Cheapest-first, fail-closed.** A rung escalates only when the cheaper one fails. The
  `invoke_when` grammar is evaluated deterministically (never `eval()`) and an unknown atom
  makes a rung **skip**, so a typo can't fire a paid rung.
- **Verification enums.** Every output field is `value+verified+source_url`, or blank+NOT FOUND,
  or a labeled UNVERIFIED guess. Never invent a value.
- **Telemetry/cache are optional.** With no `RESEARCH_DATABASE_URL` (or no psycopg2) the engine
  runs identically, just without the cache/telemetry. Keep every DB call best-effort.
- **BYOK, local env only.** Read secrets from `os.environ` / `.env`; never fetch a key over the
  network; never commit a real secret.
- **The loop and the verify stay with the orchestrator.** A cheap model only compresses
  already-fetched pages — it never decides what to fetch or whether a claim is verified.

## Before a PR

```bash
bash scripts/selftest.sh      # no-network smoke: imports, predicate eval, py_compile, workflow syntax
bash scripts/scrub-check.sh   # secret/leak gate — must exit 0
```

Add any new key to `.env.example`, and document a new rung in `docs/the-waterfall.md`.

## License

By contributing you agree your contributions are licensed under [Apache 2.0](LICENSE).
