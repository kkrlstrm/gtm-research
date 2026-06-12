# Security

## Bring-Your-Own-Keys, local environment only

`gtm-research` reads every secret from your **local environment** (`os.environ`, or a
gitignored `.env`). It **never fetches a key over the network**, has no remote secret store,
and phones nothing home. Each API key is sent only to that provider's own API over HTTPS.

- `.env` is gitignored; only `.env.example` (placeholders) is committed.
- A provider rung is used only if its env var is set and non-empty — unset keys are skipped
  with a log line, so a partial key set just gives a thinner, cheaper waterfall.
- The optional telemetry/cache database is reached by a DSN you control
  (`RESEARCH_DATABASE_URL`); nothing is written to it unless you configure it.

## What gets sent where

- **Search/fetch providers** (DuckDuckGo, Brave, Serper, Exa, Jina, Tavily, Parallel) receive
  the query or URL you research, over HTTPS, authenticated with your key.
- **OpenRouter** (optional digest) receives already-fetched page text for compression — the
  model never touches the network itself.
- **Your Postgres** (optional) receives cache entries (URLs, page digests, search results) and
  telemetry rows. Web facts are client-agnostic; no secret material is stored.

## Hostile pages & prompt injection

This engine fetches **arbitrary, untrusted web pages** and feeds their text to a digest model.
A hostile page can attempt prompt injection — embedding text that tries to make the model emit
a **fabricated `verified=true` field** or a **poisoned `source_url`** pointing somewhere it
controls. The architecture treats this as a first-class threat:

- **Fetched page text is DATA, never instructions.** The digest model's only job is to
  compress/quote what was already fetched; it has no tools, no network, and no authority to set
  any output field. It cannot decide verification.
- **`verified=true` is never set by page-controlled content.** Verification is **non-delegable**
  and stays with the orchestrator (your Claude agent), which sets `verified` based on a page *it*
  opened — not on anything a value, a digest, or a page asks it to assert.
- **The verify pass re-reads the raw page, bypassing the digest model.** It re-opens each
  `source_url` with `page-digest.py --no-digest` (raw fetch, no model in the loop), so a digest
  injected to invent a fact can't survive — a claim not present in the actually-fetched page is
  blanked and marked unverified.
- **A poisoned `source_url` doesn't self-certify.** The verify step independently fetches the URL
  and checks it supports the field; a link the source page merely *claims* proves nothing until
  re-opened.

**Residual risk (stated honestly):** verification confirms *the source says it*, not that it is
true in the world — a page can still state a confident falsehood. The guardrails mitigate this
with primary-source preference (a company's own site over aggregators) and a two-independent-
sources rule for high-stakes fields, but a determined liar on a primary domain is a limit of any
web-research tool. The contract is integrity of provenance — every `verified` fact traces to a
real page that was actually opened — not omniscience.

## Pre-publish gate

`scripts/scrub-check.sh` fails closed on committed secrets, hardcoded local `.env` paths, and
network secret-fetch patterns. Run it (and `gitleaks` if installed) before publishing.

## Reporting a vulnerability

Open a private security advisory on the GitHub repository, or email the maintainer. Please do
not file public issues for security reports.
