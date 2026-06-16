#!/usr/bin/env python3
"""
browser_use_fetch — the GATED, LAST-RESORT FETCH rung (real local browser, no proxies).

After native + jina + tavily + parallel all fail on a hard Cloudflare/JS page, this shells
out to an ISOLATED browser-use runner (agents/browser-use-fetch/, its own venv) that drives
a real local browser on your own IP. The heavy deps (Playwright/Chromium, browser-use) never
enter this engine — we invoke the runner over a subprocess boundary and parse its JSON.

Gated like parallel: fires only with explicit clearance (`--allow-browser-use` /
run.args.allow_browser_use). Auto-skips (no telemetry, no subprocess) when BROWSER_USE_CMD
is unset, so the zero-infra default is unchanged.

    from providers import browser_use_fetch
    out = browser_use_fetch.read("https://acme.com/team")
    # out -> {"ok":bool, "content":str, "provider":"browser_use", "credits":0, "usd":float}
"""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from research_engine import research_db  # noqa: E402
from research_engine.env import env  # noqa: E402

_TIMEOUT_S = 90        # a real browser + LLM loop is slow; hard-cap it
_MIN_USABLE = 200      # a near-empty body is a miss → caller already exhausted everything


def configured() -> bool:
    return bool(env("BROWSER_USE_CMD"))


def read(url: str, *, run_id: str | None = None, entity: str | None = None) -> dict:
    cmd = env("BROWSER_USE_CMD")
    if not cmd:
        return {"ok": False, "content": "", "provider": "browser_use", "credits": 0, "usd": 0,
                "skipped": "no BROWSER_USE_CMD"}
    t0 = time.time()
    content, ok, usd = "", False, 0.0
    try:
        proc = subprocess.run(shlex.split(cmd) + [url, "--json"],
                              capture_output=True, text=True, timeout=_TIMEOUT_S)
        if proc.returncode == 0 and proc.stdout.strip():
            data = json.loads(proc.stdout.strip().splitlines()[-1])  # last line is the JSON
            content = data.get("content", "") or ""
            usd = float(data.get("usd") or 0.0)
            ok = bool(data.get("ok")) and len(content.strip()) >= _MIN_USABLE
    except Exception:  # noqa: BLE001 — a provider must never raise into the waterfall
        ok = False
    research_db.rung_event(run_id, entity, "fetch", "browser_use", "gated", ok,
                           latency_ms=int((time.time() - t0) * 1000), cost_usd=usd)
    return {"ok": ok, "content": content, "provider": "browser_use", "credits": 0, "usd": usd}


if __name__ == "__main__":
    u = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"
    out = read(u)
    print(json.dumps({**out, "content": out["content"][:500]}, indent=2))
