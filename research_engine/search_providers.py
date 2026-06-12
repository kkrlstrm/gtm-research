"""
search_providers — the free-first keyword-search waterfall.

Provider order:  ddg → brave → claude_cli → serper
  - ddg        : DuckDuckGo lite via POST + browser headers (free, no key)
  - brave      : Brave Search API (free ~2k/mo, needs BRAVE_API_KEY)
  - claude_cli : `claude -p --allowed-tools WebSearch` (free via a Claude Code plan; optional)
  - serper     : Serper.dev (free ~2.5k/mo, needs SERPER_API_KEY)
Providers with no key (or no `claude` CLI) are skipped automatically.

Each provider returns list[{title,url,content}] or None on failure/empty.
`waterfall(query, n)` walks the order behind a circuit Breaker and returns
{results, provider, credits, attempts}. The paid Tavily rung is NOT here — the
fetch/search chokepoints add it as an explicit YAML rung after these free ones.
"""
from __future__ import annotations

import json
import os
import random
import re
import subprocess
import threading
import time
from collections import defaultdict

import requests

from .env import env

ORDER = ["ddg", "brave", "claude_cli", "serper"]
# The free slice used by the fan-out chokepoint (research-search.py), which lets the
# YAML waterfall drive the tavily/parallel escalation as its own explicit rungs.
FREE_ORDER = ["ddg", "brave", "claude_cli", "serper"]


def _key(name: str) -> str | None:
    return env(name)


HAVE_CLAUDE = bool(subprocess.run(["which", "claude"], capture_output=True).returncode == 0)
_DDG_SEM = threading.Semaphore(4)  # be gentle on DDG so it doesn't rate-limit us


def configured(p: str) -> bool:
    return {"ddg": True,
            "brave": bool(_key("BRAVE_API_KEY")),
            "claude_cli": HAVE_CLAUDE,
            "serper": bool(_key("SERPER_API_KEY"))}.get(p, False)


# ----------------------------- providers -----------------------------
_BROWSER = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml", "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://lite.duckduckgo.com/", "Origin": "https://lite.duckduckgo.com",
            "content-type": "application/x-www-form-urlencoded"}
_DDG_LINK = re.compile(r'<a rel="nofollow" href="([^"]+)" class=\'result-link\'>(.*?)</a>', re.S)
_DDG_SNIP = re.compile(r"class='result-snippet'[^>]*>(.*?)</td>", re.S)
_TAG = re.compile(r"<[^>]+>")


def _txt(s):
    return _TAG.sub("", s or "").strip()


def ddg(q, n):
    with _DDG_SEM:
        time.sleep(0.15 + random.random() * 0.25)
        r = requests.post("https://lite.duckduckgo.com/lite/", headers=_BROWSER, data={"q": q}, timeout=15)
    body = r.text
    if r.status_code != 200 or any(w in body.lower() for w in ("anomaly", "botnet", "challenge-form")):
        return None
    links = _DDG_LINK.findall(body)
    snips = _DDG_SNIP.findall(body)
    out = []
    for i, (url, title) in enumerate(links[:n]):
        if "duckduckgo.com/l/" in url:
            m = re.search(r"uddg=([^&]+)", url)
            if m:
                import urllib.parse
                url = urllib.parse.unquote(m.group(1))
        out.append({"title": _txt(title), "url": url, "content": _txt(snips[i]) if i < len(snips) else ""})
    return out or None


def brave(q, n):
    r = requests.get("https://api.search.brave.com/res/v1/web/search",
                     headers={"X-Subscription-Token": _key("BRAVE_API_KEY"), "Accept": "application/json"},
                     params={"q": q, "count": n}, timeout=20)
    if r.status_code != 200:
        return None
    res = (r.json().get("web") or {}).get("results", [])
    return [{"title": x.get("title", ""), "url": x.get("url", ""),
             "content": _txt(x.get("description", ""))} for x in res[:n]] or None


def claude_cli(q, n):
    prompt = (f"Web-search for: {q}\nReturn ONLY a JSON array of up to {n} results, each an object "
              '{"title","url","snippet"}. No prose, no markdown fences.')
    cli_env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    model = env("RESEARCH_CLAUDE_CLI_MODEL") or "claude-haiku-4-5"
    p = subprocess.run(["claude", "-p", "--model", model, "--allowed-tools", "WebSearch",
                        "--output-format", "json", prompt],
                       capture_output=True, text=True, timeout=120, env=cli_env)
    if p.returncode != 0 or not p.stdout.strip():
        return None
    txt = p.stdout
    try:
        envj = json.loads(txt)
        txt = envj.get("result", txt) if isinstance(envj, dict) else txt
    except Exception:
        pass
    m = re.search(r"\[.*\]", txt, re.S)
    if not m:
        return None
    try:
        arr = json.loads(m.group(0))
    except Exception:
        return None
    out = [{"title": x.get("title", ""), "url": x.get("url", ""), "content": x.get("snippet", "")}
           for x in arr if isinstance(x, dict)]
    return out[:n] or None


def serper(q, n):
    r = requests.post("https://google.serper.dev/search",
                      headers={"X-API-KEY": _key("SERPER_API_KEY"), "content-type": "application/json"},
                      json={"q": q, "num": n}, timeout=20)
    if r.status_code != 200:
        return None
    return [{"title": x.get("title", ""), "url": x.get("link", ""), "content": x.get("snippet", "")}
            for x in r.json().get("organic", [])[:n]] or None


PROVIDERS = {"ddg": ddg, "brave": brave, "claude_cli": claude_cli, "serper": serper}


# ----------------------------- circuit breaker -----------------------------
class Breaker:
    def __init__(self, threshold=2, cooldown=120):
        self.threshold, self.cooldown = threshold, cooldown
        self.fails = defaultdict(int)
        self.open_until = defaultdict(float)
        self.lock = threading.Lock()

    def available(self, p):
        with self.lock:
            return time.time() >= self.open_until[p]

    def record(self, p, ok):
        with self.lock:
            if ok:
                self.fails[p] = 0
                self.open_until[p] = 0.0
            else:
                self.fails[p] += 1
                if self.fails[p] >= self.threshold:
                    self.open_until[p] = time.time() + self.cooldown
                    self.fails[p] = 0


_DEFAULT_BREAKER = Breaker()


def waterfall(query, n, breaker: Breaker | None = None, order: list[str] | None = None):
    """Walk the provider order, starting at the first healthy one; escalate on snag.
    Returns {results, provider, credits, attempts}."""
    breaker = breaker or _DEFAULT_BREAKER
    attempts = []
    for p in (order or ORDER):
        if not configured(p) or not breaker.available(p):
            continue
        try:
            res = PROVIDERS[p](query, n)
        except Exception:
            res = None
        breaker.record(p, bool(res))
        attempts.append({"provider": p, "ok": bool(res)})
        if res:
            return {"results": res, "provider": p, "credits": 0, "attempts": attempts}
    return {"results": [], "provider": "none", "credits": 0, "attempts": attempts}
