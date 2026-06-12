#!/usr/bin/env python3
"""
exa_search — the semantic-discovery SEARCH rung (Exa neural search).

Fires ONLY when the free keyword rungs came back empty AND the query is conceptual
(`query.intent == 'semantic'` in config/research-waterfall.yaml) — discovery, not a
brave/serper duplicate. Auto-skips (returns empty) when EXA_API_KEY is absent.

    from providers import exa_search
    out = exa_search.search("companies like Ferret doing GTM research", 6)
    # out -> {"results":[{title,url,content}], "provider":"exa", "credits":0}
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

ENDPOINT = "https://api.exa.ai/search"


def configured() -> bool:
    return bool(env("EXA_API_KEY"))


def search(query: str, n: int = 6, *, run_id: str | None = None, entity: str | None = None) -> dict:
    key = env("EXA_API_KEY")
    if not key:
        return {"results": [], "provider": "exa", "credits": 0, "skipped": "no EXA_API_KEY"}
    t0 = time.time()
    results, ok = [], False
    try:
        r = requests.post(
            ENDPOINT,
            headers={"x-api-key": key, "content-type": "application/json"},
            json={"query": query, "numResults": n, "type": "auto",
                  "contents": {"text": {"maxCharacters": 600}}},
            timeout=30,
        )
        if r.status_code == 200:
            for x in r.json().get("results", [])[:n]:
                results.append({"title": x.get("title", ""), "url": x.get("url", ""),
                                "content": (x.get("text") or x.get("snippet") or "")[:600]})
            ok = bool(results)
    except Exception:  # noqa: BLE001
        ok = False
    research_db.rung_event(run_id, entity, "search", "exa", "free", ok,
                           latency_ms=int((time.time() - t0) * 1000))
    return {"results": results, "provider": "exa", "credits": 0}


if __name__ == "__main__":
    import json
    q = sys.argv[1] if len(sys.argv) > 1 else "test query"
    print(json.dumps(search(q), indent=2))
