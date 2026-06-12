# Integrating with gtm-pipeline

[`gtm-pipeline`](https://github.com/kkrlstrm/gtm-pipeline) builds the campaign list
(discover → source → qualify → enrich → activate). `gtm-research` is the **research engine**
beneath the enrichment: a cached, free-first, source-verified way to answer "what do we actually
know about this account / person?" The two share conventions on purpose — same `.claude/workflows`
runtime, same `DATABASE_URL`, same BYOK/local-env rules — so this drops in.

## Two ways to use it

### 1. Standalone, beside gtm-pipeline

Run `gtm-research` on its own for ad-hoc account research, then feed the verified rows into a
gtm-pipeline list. Its output rows (`{entity, <fields>, source_url, verified, note,
internal_status}`) map cleanly onto gtm-pipeline's canonical company/contact records and its
`pipeline_companies.intel` / `sources` columns.

### 2. As a cached `web_research` upgrade

gtm-pipeline ships a builtin `web_research` provider (Sonnet fan-outs, no key) for
`company_search` / `company_enrich` / `people_search`. `gtm-research` is the same job with a
cost waterfall, a shared cache, and cost telemetry in front of it. To wire it in:

1. **Share the database.** gtm-pipeline sets `DATABASE_URL`; `gtm-research` reads
   `RESEARCH_DATABASE_URL` then falls back to `DATABASE_URL`, so apply `storage/postgres/schema.sql`
   to the same database and the `research` cache/telemetry lives alongside `pipeline_*`.
2. **Call the workflow** from a gtm-pipeline enrich agent instead of (or before) the builtin
   `web_research` fan-out: invoke `entity-research` with the companies needing intel, take the
   verified rows, and `upsert_companies` them via gtm-pipeline's `storage/cli.py`.
3. **Reuse the "already know them" signal.** gtm-pipeline's `master_contacts` (people) and
   `gtm-research`'s `known_companies` (companies) are the same pattern — populate both from your
   CRM and you get suppression at the contact level and the account level.

## Field mapping cheat-sheet

| gtm-research output | gtm-pipeline canonical |
|---|---|
| `entity`, `company_domain` | `pipeline_companies.company_name`, `company_domain` |
| arbitrary `fields` (founded_year, employees, …) | `pipeline_companies.intel.{...}` |
| `source_url` (per finding) | `pipeline_companies.sources[]` |
| `verified` | gates whether you trust the field before paid enrichment |
| `internal_status` | account-level suppression (analogous to `db_status`) |

## Why keep them separate repos

The research waterfall is useful outside list-building (account planning, competitive research,
data hygiene), and gtm-pipeline shouldn't have to take a Postgres + cache dependency to build a
list. Separate repos, shared conventions: use either alone, or together.
