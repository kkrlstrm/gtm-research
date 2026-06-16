# browser-use-fetch — optional last-resort fetch rung

A self-hosted, **no-proxy** escape hatch for the gtm-research fetch waterfall. It drives a
**real local browser** (browser-use + Playwright/Chromium) on your own IP to load the
handful of Cloudflare / heavy-JS / interactive pages that `native → jina → tavily →
parallel` all fail on.

It is deliberately **isolated**: the heavy dependencies live in this folder's own venv and
are reached only over a subprocess boundary, so the engine stays dependency-light (just
`requests` + `PyYAML`). It is also **gated and off by default** — the engine never touches
it unless you both set `BROWSER_USE_CMD` and clear the gate with `--allow-browser-use`.

## When it fires

`native → jina → tavily → parallel(gated) → **browser_use(gated)** → digest`

Last rung before nothing. Only worth it for genuinely hard pages — it's slower (a real
browser + an LLM loop, ~tens of seconds) and costs a few cheap-model tokens per page.
Tavily already handles most bot-walled pages for ~1 credit; this is the long tail.

## Setup

```bash
cd agents/browser-use-fetch
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

## Wire it into the engine

```bash
# absolute paths; runner python + script
export BROWSER_USE_CMD="$PWD/.venv/bin/python $PWD/fetch.py"
export OPENROUTER_API_KEY=sk-or-...            # the cheap model the browser agent uses
# optional: export BROWSER_USE_MODEL=deepseek/deepseek-chat   (default)
```

Then run a fetch with the gate cleared:

```bash
python3 ../../bin/page-digest.py "<hard-url>" --entity X --want Y --allow-browser-use --json
```

Without `--allow-browser-use` (or with `BROWSER_USE_CMD` unset) the rung auto-skips and the
waterfall behaves exactly as the zero-infra default.

## Cost & posture

- Cost per page = a few cheap-model tokens (OpenRouter) + local compute. No proxy bill.
- Runs on your own residential IP — fine for low volume; this is a long-tail tool, not a
  bulk scraper.
- browser-use is MIT-licensed; this folder calls it as a library in its own venv.
