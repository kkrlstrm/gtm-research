---
name: research
description: Research a company or person (or a list) for factual fields going into a sales CSV/CRM — verified against real sources, via a config-driven cost waterfall with a shared cache.
---

# /research — entity research, built as a cost waterfall

Given a company or person (or a list), research the requested fields, verify them
against real sources, and (for companies) add an optional "do we already know them?"
column. Routed **cheapest-first** so paid credits are spent only when free fetching fails.

## Routing

| Situation | Do this |
|---|---|
| **1 entity, or a list** | Invoke the **`entity-research` workflow** (Workflow tool) with the args below. One agent per entity runs the waterfall + verify. |
| **People-only contact lookup** | Same workflow with `entityType:'person'` (turns the internal cross-reference off). |

```js
Workflow({ name: 'entity-research', args: {
  entities: [{ name: 'Acme Corp', domain: 'acme.com', location: 'Austin, TX' }, ...],
  brief: 'Find HQ city, employee count, CEO, and the marketing-automation tool they use.',
  fields: ['hq_city', 'employee_count', 'ceo', 'martech_tool'],
  purpose: 'Going into a sales CSV — accuracy matters.',
  entityType: 'company',      // 'person' turns crossReference off
  crossReference: true,       // default true for companies (optional known-companies join)
  maxFetches: 6, maxSearches: 4,
  model: 'sonnet',            // 'opus' for max thoroughness; 'haiku' for cheap smoke tests
  verify: true,
}})
```
It returns `{ fields, rows, summary }` — one row per finding, each carrying `source_url`,
`verified`, `note`, and (for companies) `internal_status` / `internal_ref` / `internal_note`.

## The cost waterfall (config/research-waterfall.yaml)

Two chokepoints read the policy, check a shared cross-run cache before any network call,
walk rungs (first success wins), and record telemetry when a database is configured:

- **Read a page** — `bin/page-digest.py`: cache → native (free) → **Jina** (free) on a
  JS-shell → **Tavily** Extract (1 credit) only if both fail → **Parallel** (gated). Long
  pages auto-compress to quoted facts via OpenRouter. Dead URLs (404/410/401) hard-stop.
- **Search** — `bin/research-search.py`: cache → **keyword** (free ddg→brave→claude→serper)
  → **Exa** (free, only on a keyword miss with `--intent semantic`) → **Tavily** → **Parallel**.

Reorder/add a rung by editing the YAML, not code. The research **loop** and the **verify**
stay with your Claude agent; a cheap model only compresses already-fetched pages.

## Internal cross-reference (optional GTM column)

For companies, `bin/known-xref.py` adds an `internal_status` column by matching against your
own `research.known_companies` table (you populate it from your CRM; see
`storage/postgres/known-companies-optional.sql`). With no table configured it returns
`net-new` for everything — the research still runs. This is a deterministic DB join, not an
LLM call.

## Guardrails

- **Verification enums, never invention.** Every field is value+`verified=true`+`source_url`,
  or `""`+`verified=false`+"NOT FOUND — searched X", or guess+`verified=false`+"UNVERIFIED".
  Emails must be confirmed against a real directory, never guessed-and-verified.
- **Primary source over aggregator.** The entity's own site/filing/profile establishes;
  ZoomInfo/RocketReach only corroborate.
- **Don't delegate the loop or the verify.** Deciding what to fetch and whether a claim is
  verified stays with your orchestrator.

## Output

The workflow returns rows. Persist where the user wants (CSV default, a Postgres table, etc.).
Surface the `summary` and flag any `internal_status` that indicates an existing relationship —
don't prospect a company you already serve without saying so.
