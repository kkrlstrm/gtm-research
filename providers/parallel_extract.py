#!/usr/bin/env python3
"""
parallel_extract — the GATED terminal FETCH rung (Parallel.ai Extract API).

Last-resort page read for the <2% of URLs that native + Jina + Tavily all fail. Same
gate + reserve economics as parallel_search (one-time free, then ~$0.005/req). Fires
only with explicit clearance (gate `parallel`). Auto-skips when PARALLEL_API_KEY absent.

    from providers import parallel_extract
    out = parallel_extract.read("https://acme.com/about")
    # out -> {"ok":bool, "content":str, "provider":"parallel", "credits":0, "usd":0.005}
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

ENDPOINT = "https://api.parallel.ai/v1beta/extract"
_USD_PER_REQ = 0.005
_MIN_USABLE = 200


def configured() -> bool:
    return bool(env("PARALLEL_API_KEY"))


def read(url: str, *, run_id: str | None = None, entity: str | None = None) -> dict:
    key = env("PARALLEL_API_KEY")
    if not key:
        return {"ok": False, "content": "", "provider": "parallel", "credits": 0, "usd": 0,
                "skipped": "no PARALLEL_API_KEY"}
    t0 = time.time()
    content, ok, usd = "", False, 0.0
    try:
        r = requests.post(ENDPOINT, headers={"x-api-key": key, "content-type": "application/json"},
                          json={"urls": [url], "format": "markdown"}, timeout=60)
        if r.status_code == 200:
            data = r.json()
            items = data.get("results") or data.get("extracts") or []
            if items:
                ex = items[0].get("excerpts") or items[0].get("content") or items[0].get("markdown") or ""
                content = "\n\n".join(ex) if isinstance(ex, list) else str(ex)
            ok = len(content.strip()) >= _MIN_USABLE
            usd = _USD_PER_REQ if ok else 0.0
    except Exception:  # noqa: BLE001
        ok = False
    research_db.rung_event(run_id, entity, "fetch", "parallel", "gated", ok,
                           latency_ms=int((time.time() - t0) * 1000), cost_usd=usd)
    return {"ok": ok, "content": content, "provider": "parallel", "credits": 0, "usd": usd}


if __name__ == "__main__":
    import json
    u = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"
    out = read(u)
    print(json.dumps({**out, "content": out["content"][:500]}, indent=2))
