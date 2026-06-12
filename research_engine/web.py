"""
web — Tavily-first / native-fallback web access with a cost-aware waterfall.

`fetch(url, prefer="cheap")` runs a free native HTTP fetch first and only escalates
to Tavily Extract (a paid credit) when the native body is an unusable JS-shell /
bot-wall; it hard-stops 404/410/401. `search()` wraps Tavily Search (which adds
include_domains / country the native engines lack). Each fetch result carries
`tier` + `tavily_credits` so a caller can see whether a credit was actually spent.

Credit handling: on a Tavily plan / pay-as-you-go / rate-limit response
(HTTP 432 / 433 / 429) the helper trips a short cooldown file so the next calls skip
Tavily and use the native fallback instead of re-hitting an exhausted account. The
cooldown directory defaults to the system temp dir; override with RESEARCH_CACHE_DIR.
Cooldown length is TAVILY_COOLDOWN_SECS (default 3600).

Dependencies: `requests` (required); `beautifulsoup4` (optional — cleaner native
HTML→text; without it the raw HTML is returned).
"""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Any

import requests

from .env import env

_EXTRACT_URL = "https://api.tavily.com/extract"
_SEARCH_URL = "https://api.tavily.com/search"
_MAX_URLS_PER_CALL = 20  # Tavily Extract hard limit

_RATE = 429
_PLAN = 432
_PAYG = 433
_EXHAUSTED = (_PLAN, _PAYG)

_COOLDOWN_DIR = Path(env("RESEARCH_CACHE_DIR") or tempfile.gettempdir())
_COOLDOWN_FILE = _COOLDOWN_DIR / "gtm-research-tavily-cooldown"
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _key() -> str | None:
    return env("TAVILY_API_KEY")


def _cooldown_secs() -> int:
    try:
        return int(env("TAVILY_COOLDOWN_SECS") or 3600)
    except ValueError:
        return 3600


def cooldown_remaining() -> int:
    try:
        until = float(_COOLDOWN_FILE.read_text().strip())
    except (OSError, ValueError):
        return 0
    return max(0, int(until - time.time()))


def _trip_cooldown(reason: str) -> None:
    try:
        _COOLDOWN_FILE.parent.mkdir(parents=True, exist_ok=True)
        _COOLDOWN_FILE.write_text(str(time.time() + _cooldown_secs()))
    except OSError:
        pass


def clear_cooldown() -> None:
    _COOLDOWN_FILE.unlink(missing_ok=True)


def tavily_available() -> bool:
    return bool(_key()) and cooldown_remaining() == 0


# --------------------------------------------------------------------------- #
# native fallback
# --------------------------------------------------------------------------- #
def _html_to_text(html: str) -> str:
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return html
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    lines = [ln.strip() for ln in soup.get_text("\n").splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _plain_fetch(url: str, timeout: int = 30) -> dict[str, Any]:
    try:
        r = requests.get(url, headers={"User-Agent": _UA}, timeout=timeout, allow_redirects=True)
        status = r.status_code
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        content = _html_to_text(r.text) if "html" in ctype else r.text
        return {"url": url, "ok": True, "source": "native", "content": content,
                "error": None, "credits_exhausted": False, "status": status}
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        return {"url": url, "ok": False, "source": "native", "content": "",
                "error": f"HTTPError {status}", "credits_exhausted": False, "status": status}
    except Exception as e:  # noqa: BLE001 — fallback must never raise
        return {"url": url, "ok": False, "source": "native", "content": "",
                "error": f"{e.__class__.__name__}: {e}", "credits_exhausted": False, "status": None}


# --------------------------------------------------------------------------- #
# Tavily Extract
# --------------------------------------------------------------------------- #
def _tavily_extract(urls: list[str], depth: str, fmt: str) -> tuple[dict[str, str], bool, str | None]:
    key = _key()
    if not key:
        return {}, False, "no TAVILY_API_KEY"
    got: dict[str, str] = {}
    for i in range(0, len(urls), _MAX_URLS_PER_CALL):
        batch = urls[i: i + _MAX_URLS_PER_CALL]
        try:
            resp = requests.post(_EXTRACT_URL, headers={"Authorization": f"Bearer {key}"},
                                 json={"urls": batch, "extract_depth": depth, "format": fmt}, timeout=90)
        except Exception as e:  # noqa: BLE001
            return got, False, f"request error: {e.__class__.__name__}: {e}"
        if resp.status_code in _EXHAUSTED:
            _trip_cooldown(f"extract {resp.status_code}")
            return got, True, f"credits exhausted (HTTP {resp.status_code})"
        if resp.status_code == _RATE:
            _trip_cooldown("extract 429")
            return got, True, "rate limited (HTTP 429)"
        if resp.status_code == 401:
            return got, False, "invalid TAVILY_API_KEY (HTTP 401)"
        if resp.status_code != 200:
            return got, False, f"HTTP {resp.status_code}: {resp.text[:200]}"
        for item in resp.json().get("results", []):
            u, content = item.get("url"), item.get("raw_content")
            if u and content:
                got[u] = content
    return got, False, None


# --------------------------------------------------------------------------- #
# cost-aware fetch waterfall (prefer="cheap")
# --------------------------------------------------------------------------- #
_WATERFALL_MIN_LEN = 500
_DEAD_STATUSES = (401, 404, 410)
_SHELL_MARKERS = (
    "enable javascript", "please enable js", "you need to enable javascript",
    "checking your browser", "captcha", "are you a human", "access denied",
    "cf-browser-verification", "request unsuccessful", "incapsula", "ddos protection",
)


def _usable(content: str) -> bool:
    body = (content or "").strip()
    if len(body) < _WATERFALL_MIN_LEN:
        return False
    if len(body) < 3000 and any(m in body[:4000].lower() for m in _SHELL_MARKERS):
        return False
    return True


def _fetch_waterfall(urls: list[str], *, depth: str, fmt: str) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    pending: dict[str, dict[str, Any]] = {}
    for u in urls:
        r = _plain_fetch(u)
        if r["ok"] and _usable(r["content"]):
            r.update(tier="native", tavily_credits=0)
            out[u] = r
            continue
        if r.get("status") in _DEAD_STATUSES:
            out[u] = {"url": u, "ok": False, "source": "native", "content": "",
                      "error": f"dead URL (HTTP {r['status']}) — re-search, do not retry",
                      "credits_exhausted": False, "status": r["status"],
                      "tier": "dead", "tavily_credits": 0}
            continue
        pending[u] = r

    if pending and tavily_available():
        got, exhausted, _err = _tavily_extract(list(pending), depth, fmt)
        credits = 2 if depth == "advanced" else 1
        for u, native_r in pending.items():
            if u in got:
                out[u] = {"url": u, "ok": True, "source": "tavily", "content": got[u],
                          "error": None, "credits_exhausted": exhausted,
                          "tier": "tavily", "tavily_credits": credits}
            elif native_r["content"].strip():
                native_r.update(tier="native-thin", tavily_credits=0,
                                error="thin native body; Tavily could not improve it")
                out[u] = native_r
            else:
                out[u] = {"url": u, "ok": False, "source": "tavily", "content": "",
                          "error": "no usable content" + (" (Tavily credits exhausted)" if exhausted else ""),
                          "credits_exhausted": exhausted, "status": native_r.get("status"),
                          "tier": "failed", "tavily_credits": 0}
    else:
        for u, native_r in pending.items():
            if native_r["content"].strip():
                native_r.update(tier="native-thin", tavily_credits=0)
                out[u] = native_r
            else:
                native_r.update(tier="failed", tavily_credits=0)
                out[u] = native_r

    return [out[u] for u in urls]


def fetch(url_or_urls: str | list[str], *, depth: str = "basic", fmt: str = "markdown",
          fallback: bool = True, prefer: str = "tavily") -> dict[str, Any] | list[dict[str, Any]]:
    """Fetch one URL (-> dict) or many (-> list[dict]).

    prefer="cheap"   — cost-aware waterfall: free native first, Tavily only on a shell.
    prefer="tavily"  — Tavily first, native fallback (clean output, a credit per URL).
    prefer="fallback"— native plain fetch only (no Tavily).
    """
    single = isinstance(url_or_urls, str)
    urls = [url_or_urls] if single else list(url_or_urls)

    if prefer == "cheap":
        ordered = _fetch_waterfall(urls, depth=depth, fmt=fmt)
        return ordered[0] if single else ordered

    use_tavily = prefer == "tavily" and tavily_available()
    results: dict[str, dict[str, Any]] = {}
    exhausted = False
    if use_tavily:
        got, exhausted, _err = _tavily_extract(urls, depth, fmt)
        for u in urls:
            if u in got:
                results[u] = {"url": u, "ok": True, "source": "tavily", "content": got[u],
                              "error": None, "credits_exhausted": exhausted}
    for u in urls:
        if u in results:
            continue
        if fallback:
            r = _plain_fetch(u)
            r["credits_exhausted"] = exhausted
            results[u] = r
        else:
            results[u] = {"url": u, "ok": False, "source": "tavily" if use_tavily else "skipped",
                          "content": "", "error": "not extracted (fallback off)",
                          "credits_exhausted": exhausted}
    ordered = [results[u] for u in urls]
    return ordered[0] if single else ordered


# --------------------------------------------------------------------------- #
# Tavily Search
# --------------------------------------------------------------------------- #
def search(query: str, *, depth: str = "basic", max_results: int = 5,
           include_answer: bool | str = False, include_raw_content: bool | str = False,
           include_domains: list[str] | None = None, exclude_domains: list[str] | None = None,
           topic: str = "general", country: str | None = None,
           time_range: str | None = None) -> dict[str, Any]:
    """Tavily Search. Returns {ok, source, answer, results, credits_exhausted, error}.

    On credit exhaustion returns ok=False, credits_exhausted=True — a caller should
    fall back to a free keyword engine (a script can't call a native WebSearch tool).
    """
    key = _key()
    if not key:
        return {"ok": False, "credits_exhausted": False, "error": "no TAVILY_API_KEY",
                "source": "tavily", "answer": None, "results": []}
    if cooldown_remaining() > 0:
        return {"ok": False, "credits_exhausted": True, "source": "tavily",
                "error": f"in cooldown ({cooldown_remaining()}s left)", "answer": None, "results": []}
    body: dict[str, Any] = {"query": query, "search_depth": depth, "max_results": max_results,
                            "include_answer": include_answer, "include_raw_content": include_raw_content,
                            "topic": topic}
    if include_domains:
        body["include_domains"] = include_domains
    if exclude_domains:
        body["exclude_domains"] = exclude_domains
    if country:
        body["country"] = country
    if time_range:
        body["time_range"] = time_range
    try:
        resp = requests.post(_SEARCH_URL, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=60)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "credits_exhausted": False, "source": "tavily",
                "error": f"request error: {e.__class__.__name__}: {e}", "answer": None, "results": []}
    if resp.status_code in _EXHAUSTED or resp.status_code == _RATE:
        _trip_cooldown(f"search {resp.status_code}")
        return {"ok": False, "credits_exhausted": True, "source": "tavily",
                "error": f"credits/rate limit (HTTP {resp.status_code})", "answer": None, "results": []}
    if resp.status_code != 200:
        return {"ok": False, "credits_exhausted": False, "source": "tavily",
                "error": f"HTTP {resp.status_code}: {resp.text[:200]}", "answer": None, "results": []}
    data = resp.json()
    return {"ok": True, "credits_exhausted": False, "source": "tavily",
            "answer": data.get("answer"), "results": data.get("results", []), "error": None}
