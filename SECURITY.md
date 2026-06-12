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

## Pre-publish gate

`scripts/scrub-check.sh` fails closed on committed secrets, hardcoded local `.env` paths, and
network secret-fetch patterns. Run it (and `gitleaks` if installed) before publishing.

## Reporting a vulnerability

Open a private security advisory on the GitHub repository, or email the maintainer. Please do
not file public issues for security reports.
