# Cost model — where the money actually goes

The engine is cheap because the part people assume is expensive — "an LLM running the
search/fetch waterfall" — **doesn't exist**. The waterfall is deterministic Python.

## Three places, and only three, that cost anything

| Layer | What it is | Cost |
|---|---|---|
| **The waterfall** (`bin/research-search.py`, `bin/page-digest.py`) | Plain `if/elif` rung dispatch reading `config/research-waterfall.yaml`. No model in the loop. | **$0** |
| **Free retrieval rungs** | native fetch, DuckDuckGo, Jina (keyless), `claude_cli` (flat-rate on your plan) | **$0 / flat-rate** |
| **Paid retrieval rungs** | Tavily (~1 credit ≈ $0.006), Parallel (~$0.005), and the optional `browser_use` (a few cheap-model tokens) | **metered, small** |
| **Page digest** | DeepSeek-V4-Flash via OpenRouter, only on pages >8K chars | **~$0.002 / page** |
| **The Claude agents** | per-entity **research** + **verify** (deciding what to fetch, checking claims) | **see below** |

## The Claude agents: subscription vs API

This is the part that flips the whole cost picture depending on how you run it.

- **As Claude Code subagents (the normal path):** the research/verify agents are **flat-rate**
  on your Max plan. Their marginal dollar cost is ~$0. The real constraint is your plan's
  **usage / rate limits**, not money. So per entity, the only marginal dollars are the
  ~2–3¢ of Tavily + the ~$0.002 digest. **"Free" really is nearly free.**
- **Headless via the API:** now the agents are metered and **dominate** — roughly $0.10–0.30
  per entity on Sonnet, vs ~2–3¢ of retrieval. Here, model choice is 5–10× the retrieval
  spend, so the routing below is what controls cost.

## Model routing (the lever)

The `entity-research` workflow routes by tier on purpose:

| Role | Model | Why |
|---|---|---|
| Top orchestrator (your session) | **Opus 4.8** | Plans the run; small fixed overhead |
| Per-entity **research** | **Sonnet 4.6** | Judging sources + deciding what to fetch is where model quality pays off |
| Per-entity **verify** | **Haiku 4.5** (default) | Constrained re-checking — confirm/blank fields against a re-opened source |
| Setup / watchdog / finish | **Haiku 4.5** | Mechanical housekeeping |

Approx API prices (per 1M tokens): Opus 4.8 $5/$25 · Sonnet 4.6 $3/$15 · Haiku 4.5 $1/$5.

**Verify defaults to Haiku** (`verifyModel`). To keep the cheap pass honest, set
`verifySpotCheckModel` + `verifySpotCheckEvery` (e.g. `sonnet`, `20`) to re-route every Nth
verify to a stronger model. Raise the whole pass with `verifyModel: "sonnet"` when accuracy
matters more than throughput.

## Practical takeaways

- On the subscription, optimize **retrieval credits** (stay free-first, pay a little Tavily) and
  **rate-limit headroom** (keep research on Sonnet, verify on Haiku) — not dollars.
- Going headless/at-scale flips it: the **model is the cost**. Keep research on Sonnet, verify on
  Haiku with a spot-check, and reserve Opus for the orchestrator.
- `browser_use` and `parallel` are gated and off by default — they never cost anything unless you
  explicitly clear their gate.
