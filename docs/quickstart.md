# Quickstart

## 0. Install

```bash
git clone https://github.com/kkrlstrm/gtm-research && cd gtm-research
pip install -r requirements.txt          # requests + PyYAML
cp .env.example .env                      # fill in only the keys you have — zero is fine
set -a && source .env && set +a
```

With **no keys at all**, the free DuckDuckGo search rung and the native fetch rung work; the
paid rungs (Tavily/Exa/Parallel) and the digest simply don't fire.

## 1. A free search and a free page read

```bash
python3 bin/research-search.py query "Acme Corp headquarters" --json
python3 bin/page-digest.py "https://www.acme.com/about" --entity "Acme Corp" --want "HQ city, CEO"
```

`page-digest` prints a `[page-digest] ... tier=native credits=0` line to stderr so you can see
which rung answered and whether a credit was spent.

## 2. Add keys to climb the waterfall (optional)

Put any of these in `.env` — each unlocks a rung that auto-skips without it:

| Key | Rung |
|---|---|
| `BRAVE_API_KEY`, `SERPER_API_KEY` | faster/reliable free keyword search |
| `JINA_API_KEY` | the Jina reader fetch rung (works keyless too; the key lifts rate limits) |
| `TAVILY_API_KEY` | paid Extract + Search escalation |
| `EXA_API_KEY` | semantic discovery (`--intent semantic`) |
| `PARALLEL_API_KEY` | gated last-resort (`--allow-parallel`) |
| `OPENROUTER_API_KEY` | compress long pages to quoted facts |

## 3. Turn on the cache + cost telemetry (optional)

See [local-postgres.md](local-postgres.md). Point `RESEARCH_DATABASE_URL` at local or hosted
Postgres and apply `storage/postgres/schema.sql`. Re-run step 1 twice — the second is a cache
hit (`from_cache: true`, zero spend).

## 4. A full multi-entity run

From Claude Code, invoke the `entity-research` workflow (see
[`.claude/commands/research.md`](../.claude/commands/research.md)):

```js
Workflow({ name: 'entity-research', args: {
  entities: [{ name: 'Acme Corp', domain: 'acme.com' }, { name: 'Globex', domain: 'globex.io' }],
  brief: 'Find HQ city, employee count, and CEO.',
  fields: ['hq_city', 'employee_count', 'ceo'],
}})
```

It fans out one bounded research agent per entity, runs the waterfall + a verify pass, and
returns `{ fields, rows, summary }` — one source-verified row per finding.
