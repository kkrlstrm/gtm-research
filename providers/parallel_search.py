#!/usr/bin/env python3
"""
parallel_search — the GATED terminal SEARCH rung (Parallel.ai Search API).

Reserve, not a monthly budget: a one-time free tier, then ~$0.005/req — fine for the
<2% of rows that reach it. Fires ONLY when every free + credit rung came back empty
AND the run carries explicit clearance (gate `parallel` in research-waterfall.yaml).
Auto-skips when PARALLEL_API_KEY is absent.

    from providers import parallel_search
    out = parallel_search.search("...", 6)
    # out -> {"results":[{title,url,content}], "provider":"parallel", "credits":0, "usd":0.005}
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from research_engine import research_db  # noqa: E402
from research_engine.env import env  # noqa: E402

ENDPOINT = "https://api.parallel.ai/v1beta/search"
_USD_PER_REQ = 0.005


def configured() -> bool:
    return bool(env("PARALLEL_API_KEY"))


def search(query: str, n: int = 6, *, run_id: str | None = None, entity: str | None = None) -> dict:
    key = env("PARALLEL_API_KEY")
    if not key:
        return {"results": [], "provider": "parallel", "credits": 0, "usd": 0, "skipped": "no PARALLEL_API_KEY"}
    t0 = time.time()
    results, ok, usd = [], False, 0.0
    try:
        r = requests.post(
            ENDPOINT,
            headers={"x-api-key": key, "content-type": "application/json"},
            json={"objective": query, "search_queries": [query], "max_results": n, "processor": "base"},
            timeout=45,
        )
        if r.status_code == 200:
            for x in r.json().get("results", [])[:n]:
                ex = x.get("excerpts") or x.get("excerpt") or x.get("content") or ""
                content = (" ".join(ex) if isinstance(ex, list) else str(ex))[:600]
                results.append({"title": x.get("title", ""), "url": x.get("url", ""), "content": content})
            ok = bool(results)
            usd = _USD_PER_REQ if ok else 0.0
    except Exception:  # noqa: BLE001
        ok = False
    research_db.rung_event(run_id, entity, "search", "parallel", "gated", ok,
                           latency_ms=int((time.time() - t0) * 1000), cost_usd=usd)
    return {"results": results, "provider": "parallel", "credits": 0, "usd": usd}


if __name__ == "__main__":
    import json
    q = sys.argv[1] if len(sys.argv) > 1 else "test query"
    print(json.dumps(search(q), indent=2))
