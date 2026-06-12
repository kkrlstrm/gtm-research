#!/usr/bin/env python3
"""
research-search — the SEARCH chokepoint.

The single place that reads config/research-waterfall.yaml, walks the search rungs in
order, checks the shared cache before any network call, writes one rung_event per rung
attempt, and caches the resolved query→results so a repeat is free.

Rungs (from the YAML):  keyword(free, always) → exa(free, semantic+empty) →
                        tavily(credit, empty) → parallel(gated, empty)
The "keyword" rung is the free engine waterfall (ddg→brave→claude_cli→serper); tavily
and parallel are separate explicit rungs so the policy — not the engine — owns the
paid escalation.

    python3 bin/research-search.py query "district fiscal stress 2025" --json
    python3 bin/research-search.py query "companies like Ferret" --intent semantic \
        --run-id $RID --entity "Acme Corp" --domains acme.com --json

Output (JSON): {results:[{title,url,content}], provider, credits, from_cache}.
Telemetry/cache are best-effort — a run never breaks because no database is configured.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from research_engine import research_db, search_providers, waterfall, web  # noqa: E402
from providers import exa_search, parallel_search  # noqa: E402

_CACHE_RUNG = "search"


def _emit(run_id, entity, rung, tier, ok, **kw):
    research_db.rung_event(run_id, entity, "search", rung, tier, ok, **kw)


def run_search(query: str, n: int, *, intent: str, run_id: str | None, entity: str | None,
               domains: list[str] | None, allow_parallel: bool) -> dict:
    hit = research_db.cache_get_search(_CACHE_RUNG, query)
    if hit is not None:
        _emit(run_id, entity, "cache", "free", True, from_cache=True)
        return {"results": hit["result_urls"], "provider": "cache", "credits": 0, "from_cache": True}

    rungs = waterfall.rungs("search_waterfall")
    run_args = {"allow_parallel": True} if allow_parallel else {}
    results: list = []
    provider, credits = "none", 0

    for rung in rungs:
        name = rung.get("rung")
        ctx = {"prev_rungs_empty": not results, "query_intent": intent}
        if not waterfall.should_invoke(rung, ctx):
            continue
        if rung.get("gate") and not waterfall.gate_satisfied(rung, run_args):
            continue

        if name == "keyword":
            t0 = time.time()
            out = search_providers.waterfall(query, n, order=search_providers.FREE_ORDER)
            ok = bool(out["results"])
            _emit(run_id, entity, "keyword", "free", ok, latency_ms=int((time.time() - t0) * 1000))
            if ok:
                results, provider = out["results"], out["provider"]

        elif name == "exa":
            if not exa_search.configured():
                continue
            out = exa_search.search(query, n, run_id=run_id, entity=entity)
            if out["results"]:
                results, provider = out["results"], "exa"

        elif name == "tavily":
            t0 = time.time()
            r = web.search(query, max_results=n, include_domains=domains or None)
            ok = bool(r.get("results"))
            if ok:
                results = [{"title": x.get("title", ""), "url": x.get("url", ""),
                            "content": x.get("content", "")} for x in r["results"][:n]]
                provider, credits = "tavily", 1
            _emit(run_id, entity, "tavily", "credit", ok, cost_credits=1 if ok else 0,
                  latency_ms=int((time.time() - t0) * 1000))

        elif name == "parallel":
            if not parallel_search.configured():
                continue
            out = parallel_search.search(query, n, run_id=run_id, entity=entity)
            if out["results"]:
                results, provider, credits = out["results"], "parallel", 0

        if results:
            break

    research_db.cache_put_search(_CACHE_RUNG, query, results, empty=not results)
    return {"results": results, "provider": provider, "credits": credits, "from_cache": False}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("query", help="run the search waterfall for one query")
    q.add_argument("text")
    q.add_argument("--intent", choices=["keyword", "semantic"], default="keyword")
    q.add_argument("--n", type=int, default=6)
    q.add_argument("--run-id", dest="run_id", default=None)
    q.add_argument("--entity", default=None)
    q.add_argument("--domains", default=None, help="comma-separated include_domains for the tavily rung")
    q.add_argument("--allow-parallel", action="store_true", help="clear the gated Parallel rung")
    q.add_argument("--json", action="store_true")
    a = ap.parse_args()

    domains = [d.strip() for d in a.domains.split(",")] if a.domains else None
    out = run_search(a.text, a.n, intent=a.intent, run_id=a.run_id, entity=a.entity,
                     domains=domains, allow_parallel=a.allow_parallel)
    print(f"[research-search] provider={out['provider']} results={len(out['results'])} "
          f"credits={out['credits']} cache={out['from_cache']}", file=sys.stderr)
    if a.json:
        print(json.dumps(out))
    else:
        for r in out["results"]:
            print(f"- {r['title']}\n  {r['url']}\n  {r['content'][:160]}")
    return 0 if out["results"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
