# The cost waterfall

The whole engine is one idea: **try the cheapest rung that could work, and only escalate when
it fails.** Two waterfalls — one for search, one for fetch — plus a shared cache that wraps both.

The policy lives in [`config/research-waterfall.yaml`](../config/research-waterfall.yaml).
`research_engine/waterfall.py` parses it and decides, per rung, whether it fires.

## Search waterfall

| # | Rung | Cost | Fires when |
|---|------|------|-----------|
| — | cache | $0 | always checked first; a hit skips the network |
| 1 | `keyword` (ddg → brave → claude_cli → serper) | $0 | always |
| 2 | `exa` | $0 (free pool) | keyword empty **and** `--intent semantic` |
| 3 | `tavily` | 1 credit | everything above empty |
| 4 | `parallel` | ~$0.005 | empty **and** `--allow-parallel` (gated) |

## Fetch waterfall

| # | Rung | Cost | Fires when |
|---|------|------|-----------|
| — | cache | $0 | always checked first |
| 1 | `native` (plain HTTP) | $0 | always |
| 2 | `jina` (r.jina.ai) | $0 | native body is a JS-shell / failed |
| 3 | `tavily` extract | 1 credit | native + jina failed |
| 4 | `parallel` extract | ~$0.005 | failed **and** `--allow-parallel` (gated) |
| — | `digest` (post-process) | ~$0.002 | fetched page > 8000 chars — compress to quoted facts |

`digest` is **not** an escalation rung — it compresses whatever a fetch rung returned, only
when the page is long. A 404/410/401 hard-stops the fetch waterfall and is negative-cached.

## The `invoke_when` grammar

A small closed grammar, evaluated deterministically (never `eval()`), **fail-closed** — an
unrecognized atom makes the rung skip, so a typo can't fire a paid rung:

```
always | prev_rungs_empty | prev_rung_failed | prev.is_js_shell
query.intent == 'semantic' | fetched_chars > 8000      (joined by and / or)
```

## Gates and budgets

- **Gates** reserve paid web-connected backups (Parallel) for explicit clearance —
  `--allow-parallel`, or `allowParallel:true` in the workflow args.
- **Budgets** record each free pool's monthly cap; `research.v_free_pool_month` shows burn so
  you can prefer another free rung before a pool runs out.

## Adding or reordering a rung

Edit the YAML. To add a brand-new provider rung: drop a wrapper in `providers/` with the same
shape as `exa_search.py` (auto-skip without its key, write its own `rung_event`), then add a
rung entry pointing at it. No change to the chokepoints.
