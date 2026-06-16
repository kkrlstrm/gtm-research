#!/usr/bin/env python3
"""
fetch.py — isolated browser-use runner for the gtm-research engine.

Drives a REAL local browser (browser-use + Playwright/Chromium) to load a hard page —
Cloudflare / heavy-JS / interactive — on YOUR OWN IP (no residential proxies) and return
its readable main text. This is the last-resort FETCH rung, invoked over a subprocess
boundary by providers/browser_use_fetch.py so the engine itself never imports browser-use's
heavy dependencies.

This lives in its OWN venv — keep it out of the engine's environment. Setup:

    cd agents/browser-use-fetch
    python3 -m venv .venv && . .venv/bin/activate
    pip install -r requirements.txt
    python -m playwright install chromium

Then point the engine at it (absolute paths):

    export BROWSER_USE_CMD="/ABS/agents/browser-use-fetch/.venv/bin/python /ABS/agents/browser-use-fetch/fetch.py"

Model: a CHEAP model via OpenRouter (OpenAI-compatible endpoint). Configure with:
    BROWSER_USE_MODEL   (default: deepseek/deepseek-chat)
    OPENROUTER_API_KEY  (required)

Usage:
    python fetch.py "<url>" --json
Output (stdout, last line is the JSON):
    {"ok": bool, "content": str, "error": str|null, "usd": float}
Always exits 0 — errors are reported inside the JSON so the caller parses cleanly.

NOTE: browser-use's import surface varies by release. This targets the current API
(`from browser_use import Agent`, `from browser_use.llm import ChatOpenAI`). Pin the
version in requirements.txt and adjust these two imports if your installed version differs.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

DEFAULT_MODEL = os.environ.get("BROWSER_USE_MODEL", "deepseek/deepseek-chat")
OPENROUTER_BASE = "https://openrouter.ai/api/v1"


async def _run(url: str) -> dict:
    try:
        from browser_use import Agent, BrowserSession
        from browser_use.llm import ChatOpenAI
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "content": "", "error": f"browser-use not installed: {e}", "usd": 0.0}

    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return {"ok": False, "content": "", "error": "OPENROUTER_API_KEY not set", "usd": 0.0}

    llm = ChatOpenAI(model=DEFAULT_MODEL, base_url=OPENROUTER_BASE, api_key=key)
    task = (
        f"Open this exact URL: {url}\n"
        "Wait for the page to fully load — if a Cloudflare / 'just a moment' / 'verify you "
        "are human' interstitial appears, wait for it to clear. Then return the full readable "
        "MAIN text content of the page as plain text: headings, paragraphs, lists, tables. "
        "Do NOT summarize, do NOT add commentary or your own words. If the page genuinely "
        "cannot be loaded, return exactly 'LOAD_FAILED'."
    )
    bs = BrowserSession(headless=True)  # server-side rung — never pop a window
    try:
        agent = Agent(task=task, llm=llm, browser_session=bs)
        history = await agent.run()
        content = (history.final_result() or "").strip()
        ok = bool(content) and "LOAD_FAILED" not in content[:64]
        return {
            "ok": ok,
            "content": content if ok else "",
            "error": None if ok else "load failed",
            "usd": 0.0,  # browser-use doesn't surface per-run cost; wrapper logs this nominally
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "content": "", "error": f"{e.__class__.__name__}: {e}", "usd": 0.0}
    finally:
        try:
            await bs.stop()
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    out = asyncio.run(_run(a.url))
    print(json.dumps(out) if a.json else out.get("content", ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
