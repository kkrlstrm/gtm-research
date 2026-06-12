#!/usr/bin/env python3
"""
page-digest — the FETCH chokepoint + cost-waterfall page reader.

Reads config/research-waterfall.yaml and walks the fetch rungs in order, checking the
shared cache before any network call and writing telemetry as it goes:

    cache → native (free) → jina (free, on JS-shell/bot-wall) → tavily extract (1 credit)
          → parallel extract (gated) → [post-process] digest if >8000 chars

Rungs change by editing the YAML, not this file. native + tavily rung_events are written
here (the chokepoint owns paid-rung telemetry); jina + parallel write their own. A
404/410/401 is hard-stopped and negative-cached (is_dead) — never retried.

The digest step compresses a long page to quoted, entity-grounded facts via OpenRouter
(model from RESEARCH_DIGEST_MODEL, default deepseek/deepseek-chat) instead of dumping
12K+ tokens into the research agent. Compliant: the page is already fetched; the model
never touches the network. Skipped entirely if OPENROUTER_API_KEY is unset (--no-digest
behaviour), in which case a long page is returned truncated.

    python3 bin/page-digest.py https://acme.com/about \
        --entity "Acme Corp" --want "employee count, HQ city, CEO" --run-id $RID
    python3 bin/page-digest.py URL --entity X --want Y --json

Output: digest (or raw short page) on stdout. With --json a dict
    {entity, source, tier, tavily_credits, from_cache, digested, digest_usd, chars_in, content}.
Exit 0 if content was produced, 3 if the page could not be fetched at all.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from research_engine import web, research_db, waterfall  # noqa: E402
from research_engine.env import env  # noqa: E402
from providers import jina_reader, parallel_extract  # noqa: E402

DIGEST_THRESHOLD = int(os.environ.get("PAGE_DIGEST_THRESHOLD", "8000"))
DIGEST_MODEL = env("RESEARCH_DIGEST_MODEL") or "deepseek/deepseek-chat"
_RAW_EXCERPT_MAX = 4000
_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def _emit(run_id, entity, rung, tier, ok, **kw):
    research_db.rung_event(run_id, entity, "fetch", rung, tier, ok, **kw)


def _fetch(target: str, *, run_id: str | None = None, entity: str | None = None,
           allow_parallel: bool = False) -> dict:
    if not target.startswith(("http://", "https://")):
        p = Path(target)
        if p.exists():
            return {"ok": True, "content": p.read_text(errors="replace"), "source": str(p),
                    "tier": "file", "tavily_credits": 0, "from_cache": False, "error": None, "status": None}
        return {"ok": False, "content": "", "source": target, "tier": "missing",
                "tavily_credits": 0, "from_cache": False, "error": "not a URL and not a file", "status": None}

    cached = research_db.cache_get_page(target)
    if cached is not None:
        _emit(run_id, entity, "cache", "free", True, from_cache=True)
        if cached["is_dead"]:
            return {"ok": False, "content": "", "source": target, "tier": "dead", "tavily_credits": 0,
                    "from_cache": True, "error": f"dead URL (HTTP {cached['http_status']}) — re-search",
                    "status": cached["http_status"]}
        body = cached["digest"] or cached["raw_excerpt"] or ""
        return {"ok": bool(body), "content": body, "source": target, "tier": "cache", "tavily_credits": 0,
                "from_cache": True, "error": None, "status": cached["http_status"]}

    run_args = {"allow_parallel": True} if allow_parallel else {}
    prev_failed = False
    is_js_shell = False
    native_content = ""

    for rung in waterfall.rungs("fetch_waterfall"):
        name = rung.get("rung")
        ctx = {"prev_rung_failed": prev_failed, "is_js_shell": is_js_shell}
        if name != "native" and not waterfall.should_invoke(rung, ctx):
            continue
        if rung.get("gate") and not waterfall.gate_satisfied(rung, run_args):
            continue

        if name == "native":
            t0 = time.time()
            nat = web.fetch(target, prefer="fallback")
            status = nat.get("status")
            native_content = nat.get("content", "") or ""
            usable = nat["ok"] and web._usable(native_content)
            _emit(run_id, entity, "native", "free", usable, latency_ms=int((time.time() - t0) * 1000))
            if usable:
                research_db.cache_put_page(target, status or 200, "native",
                                           raw_excerpt=native_content[:_RAW_EXCERPT_MAX])
                return {"ok": True, "content": native_content, "source": target, "tier": "native",
                        "tavily_credits": 0, "from_cache": False, "error": None, "status": status}
            if status in web._DEAD_STATUSES:
                research_db.cache_put_page(target, status, "native", is_dead=True)
                return {"ok": False, "content": "", "source": target, "tier": "dead", "tavily_credits": 0,
                        "from_cache": False, "error": f"dead URL (HTTP {status}) — re-search, do not retry",
                        "status": status}
            prev_failed = True
            is_js_shell = bool(native_content.strip())

        elif name == "jina":
            j = jina_reader.read(target, run_id=run_id, entity=entity)
            if j["ok"]:
                research_db.cache_put_page(target, 200, "jina", raw_excerpt=j["content"][:_RAW_EXCERPT_MAX])
                return {"ok": True, "content": j["content"], "source": target, "tier": "jina",
                        "tavily_credits": 0, "from_cache": False, "error": None, "status": 200}
            prev_failed = True

        elif name == "tavily":
            if not web.tavily_available():
                _emit(run_id, entity, "tavily", "credit", False)
                prev_failed = True
                continue
            t0 = time.time()
            got, exhausted, _err = web._tavily_extract([target], "basic", "markdown")
            ok = target in got
            _emit(run_id, entity, "tavily", "credit", ok, cost_credits=1 if ok else 0,
                  latency_ms=int((time.time() - t0) * 1000))
            if ok:
                research_db.cache_put_page(target, 200, "tavily", raw_excerpt=got[target][:_RAW_EXCERPT_MAX])
                return {"ok": True, "content": got[target], "source": target, "tier": "tavily",
                        "tavily_credits": 1, "from_cache": False, "error": None, "status": 200}
            prev_failed = True

        elif name == "parallel":
            pe = parallel_extract.read(target, run_id=run_id, entity=entity)
            if pe["ok"]:
                research_db.cache_put_page(target, 200, "parallel", raw_excerpt=pe["content"][:_RAW_EXCERPT_MAX])
                return {"ok": True, "content": pe["content"], "source": target, "tier": "parallel",
                        "tavily_credits": 0, "from_cache": False, "error": None, "status": 200}
            prev_failed = True

    if native_content.strip():
        return {"ok": True, "content": native_content, "source": target, "tier": "native-thin",
                "tavily_credits": 0, "from_cache": False,
                "error": "thin native body; escalation rungs could not improve it", "status": None}
    return {"ok": False, "content": "", "source": target, "tier": "failed", "tavily_credits": 0,
            "from_cache": False, "error": "no usable content", "status": None}


def _digest(entity: str, want: str, content: str) -> tuple[str | None, float]:
    """Compress a long page via OpenRouter. Returns (text|None, usd_cost). usd is best-effort."""
    key = env("OPENROUTER_API_KEY")
    if not key:
        return None, 0.0
    instruction = (
        f"From the page below, extract ONLY facts about {entity} that are relevant to: {want}. "
        f"For each fact, quote the exact sentence from the page that supports it. If a wanted "
        f"field is absent from the page, write 'ABSENT' for it. Do not infer or add anything "
        f"not present on the page."
    )
    try:
        resp = requests.post(
            _OPENROUTER_URL,
            headers={"Authorization": f"Bearer {key}", "content-type": "application/json"},
            json={"model": DIGEST_MODEL, "temperature": 0, "usage": {"include": True},
                  "messages": [{"role": "user", "content": f"{instruction}\n\n---\n{content}"}]},
            timeout=180,
        )
    except Exception as e:  # noqa: BLE001
        print(f"[page-digest] OpenRouter call failed ({e.__class__.__name__}); returning raw", file=sys.stderr)
        return None, 0.0
    if resp.status_code != 200:
        print(f"[page-digest] OpenRouter {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
        return None, 0.0
    data = resp.json()
    text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    usd = 0.0
    usage = data.get("usage") or {}
    if isinstance(usage.get("cost"), (int, float)):  # OpenRouter returns USD cost when usage.include=true
        usd = float(usage["cost"])
    if usd:
        print(f"[page-digest] digest cost ${usd:.5f} ({DIGEST_MODEL})", file=sys.stderr)
    return (text or None), usd


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("target", help="URL or local file path")
    ap.add_argument("--entity", required=True)
    ap.add_argument("--want", required=True, help="fields/brief the digest should focus on")
    ap.add_argument("--threshold", type=int, default=DIGEST_THRESHOLD)
    ap.add_argument("--run-id", dest="run_id", default=None)
    ap.add_argument("--allow-parallel", action="store_true")
    ap.add_argument("--no-digest", action="store_true", help="never delegate; return raw page")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    f = _fetch(a.target, run_id=a.run_id, entity=a.entity, allow_parallel=a.allow_parallel)
    print(f"[page-digest] {f['source']} → tier={f['tier']} credits={f['tavily_credits']} "
          f"cache={f['from_cache']} chars={len(f['content'])}", file=sys.stderr)
    if not f["ok"] and not f["content"]:
        print(f"[page-digest] could not fetch: {f['error']}", file=sys.stderr)
        if a.json:
            print(json.dumps({**f, "entity": a.entity, "digested": False, "digest_usd": 0, "chars_in": 0}))
        return 3

    content = f["content"]
    digested = False
    digest_usd = 0.0
    if not a.no_digest and not f["from_cache"] and len(content) > a.threshold:
        d, digest_usd = _digest(a.entity, a.want, content)
        if d:
            content, digested = d, True
            research_db.cache_put_page(a.target, f.get("status") or 200, f["tier"], digest=content)
            research_db.rung_event(a.run_id, a.entity, "fetch", "digest", "cheap_model", True, cost_usd=digest_usd)
        else:
            content = content[: a.threshold] + "\n\n[...truncated; digest unavailable...]"

    if a.json:
        print(json.dumps({
            "entity": a.entity, "source": f["source"], "tier": f["tier"],
            "tavily_credits": f["tavily_credits"], "from_cache": f["from_cache"],
            "digested": digested, "digest_usd": digest_usd,
            "chars_in": len(f["content"]), "content": content,
        }))
    else:
        print(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
