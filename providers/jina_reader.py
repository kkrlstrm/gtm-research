#!/usr/bin/env python3
"""
jina_reader — the FETCH rung between native fetch and Tavily Extract.

On a JS-shell / bot-wall body, fetch https://r.jina.ai/<url> — Jina renders the page
server-side and returns clean markdown for free. A JINA_API_KEY lifts the read limit
(20rpm -> 500rpm) but is optional. Returns failure so the caller escalates to Tavily.

The 404/410/401 hard-stop lives in bin/page-digest.py (native fetch surfaces the dead
status); Jina is only tried on a live-but-shell body.

    from providers import jina_reader
    out = jina_reader.read("https://acme.com/about")
    # out -> {"ok":bool, "content":str, "provider":"jina", "credits":0}
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

_MIN_USABLE = 500


def configured() -> bool:
    return True  # works without a key (lower rate limit); key only lifts rpm


def read(url: str, *, run_id: str | None = None, entity: str | None = None) -> dict:
    headers = {"User-Agent": "Mozilla/5.0", "X-Return-Format": "markdown"}
    key = env("JINA_API_KEY")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    t0 = time.time()
    content, ok = "", False
    try:
        r = requests.get(f"https://r.jina.ai/{url}", headers=headers, timeout=45)
        if r.status_code == 200 and len(r.text.strip()) >= _MIN_USABLE:
            content, ok = r.text, True
    except Exception:  # noqa: BLE001
        ok = False
    research_db.rung_event(run_id, entity, "fetch", "jina", "free", ok,
                           latency_ms=int((time.time() - t0) * 1000))
    return {"ok": ok, "content": content, "provider": "jina", "credits": 0}


if __name__ == "__main__":
    import json
    u = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"
    out = read(u)
    print(json.dumps({**out, "content": out["content"][:500]}, indent=2))
