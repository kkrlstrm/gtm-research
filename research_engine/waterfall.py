"""
waterfall — load and evaluate the cost policy (config/research-waterfall.yaml).

The two chokepoints (bin/research-search.py for search, bin/page-digest.py for
fetch) read the ordered rung list from here and ask `should_invoke()` whether each
rung fires, given a small context dict. `invoke_when` is a CLOSED grammar — we
evaluate it deterministically, never with eval(), and fail CLOSED: any atom we
don't recognize makes the rung skip, so a typo in the YAML can never silently fire
a paid rung.

    from research_engine import waterfall
    for rung in waterfall.rungs("search_waterfall"):
        if not waterfall.should_invoke(rung, ctx): continue
        if rung.get("gate") and not waterfall.gate_satisfied(rung, run_args): continue
        ...

Context keys understood by the grammar:
    prev_rungs_empty   bool   — every prior rung returned no result
    prev_rung_failed   bool   — the immediately-prior rung failed/returned empty
    is_js_shell        bool   — the prior fetch body was a JS-shell/bot-wall  (prev.is_js_shell)
    query_intent       str    — 'semantic' | 'keyword'                       (query.intent == 'semantic')
    fetched_chars      int    — length of the fetched page                   (fetched_chars > 8000)
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO / "config"

_LHS = {
    "prev.is_js_shell": "is_js_shell",
    "query.intent": "query_intent",
    "fetched_chars": "fetched_chars",
}


@lru_cache(maxsize=4)
def load(name: str = "research-waterfall") -> dict:
    """Parse config/<name>.yaml (cached)."""
    path = CONFIG_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"{path} missing")
    return yaml.safe_load(path.read_text()) or {}


def _alias(waterfall: str) -> str:
    return {"search": "search_waterfall", "fetch": "fetch_waterfall"}.get(waterfall, waterfall)


def rungs(waterfall: str, name: str = "research-waterfall") -> list[dict]:
    """Ordered rung list for 'search_waterfall' | 'fetch_waterfall' (aliases: search|fetch)."""
    return list(load(name).get(_alias(waterfall), []))


def cfg(section: str, name: str = "research-waterfall") -> dict:
    """A top-level config section (cache | budgets | gates | watchdog | fetch_compression | defaults)."""
    return dict(load(name).get(section, {}) or {})


# --------------------------------------------------------------------------- #
# invoke_when evaluation — closed grammar, fail-closed
# --------------------------------------------------------------------------- #
def _atom(tok: str, ctx: dict) -> bool | None:
    tok = tok.strip()
    if not tok:
        return None
    if tok == "always":
        return True
    if tok in ("prev_rungs_empty", "prev_rung_failed"):
        return bool(ctx.get(tok, False))
    if tok == "prev.is_js_shell":
        return bool(ctx.get("is_js_shell", False))
    m = re.fullmatch(r"(\S+)\s*==\s*'([^']*)'", tok)
    if m and m.group(1) in _LHS:
        return ctx.get(_LHS[m.group(1)]) == m.group(2)
    m = re.fullmatch(r"(\S+)\s*>\s*(\d+)", tok)
    if m and m.group(1) in _LHS:
        try:
            return float(ctx.get(_LHS[m.group(1)], 0) or 0) > float(m.group(2))
        except (TypeError, ValueError):
            return False
    return None  # unknown atom → caller treats as skip


def should_invoke(rung: dict, ctx: dict) -> bool:
    """True if this rung's `invoke_when` holds for ctx. Unknown grammar → False (skip)."""
    expr = (rung.get("invoke_when") or "").strip()
    if not expr:
        return False
    for or_clause in re.split(r"\s+or\s+", expr):
        vals = [_atom(a, ctx) for a in re.split(r"\s+and\s+", or_clause)]
        if any(v is None for v in vals):
            return False  # fail-closed on any unrecognized atom
        if all(vals):
            return True
    return False


def gate_satisfied(rung: dict, run_args: dict | None) -> bool:
    """A gated rung fires only with clearance (user invocation or run.args.allow_<gate>)."""
    gate = rung.get("gate")
    if not gate:
        return True
    run_args = run_args or {}
    if run_args.get("allow_parallel") is True or run_args.get(f"allow_{gate}") is True:
        return True
    if run_args.get("user_invocation"):
        return True
    return False
