#!/usr/bin/env python3
"""
research-run — run lifecycle + telemetry CLI.

The way a shell-less workflow (.claude/workflows/entity-research.js) touches the
telemetry DB: it spawns a one-shot agent that runs these subcommands and parses the
JSON on stdout.

    python3 bin/research-run.py create --entity-type company --model sonnet \
        --entity-count 12 --purpose "sales CSV" --brief "find CEO + martech" --json
    python3 bin/research-run.py telemetry --run-id $RID --entity "Acme Corp" \
        --entity-domain acme.com --verified 3 --unverified 1 --internal-status net-new --json
    python3 bin/research-run.py watchdog --run-id $RID --json
    python3 bin/research-run.py finish --run-id $RID --status done --json

All writes are best-effort: with no database configured the commands return a benign
result so the workflow keeps running without telemetry.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from research_engine import research_db  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create")
    c.add_argument("--entity-type", default="company", choices=["company", "person"])
    c.add_argument("--model", default="sonnet")
    c.add_argument("--purpose", default="")
    c.add_argument("--brief", default="")
    c.add_argument("--entity-count", type=int, default=0)
    c.add_argument("--client-schema", default=None)

    t = sub.add_parser("telemetry")
    t.add_argument("--run-id", required=True)
    t.add_argument("--entity", required=True)
    t.add_argument("--entity-domain", default=None)
    t.add_argument("--turns", type=int, default=0)
    t.add_argument("--tokens-in", type=int, default=0)
    t.add_argument("--tokens-out", type=int, default=0)
    t.add_argument("--credits", type=int, default=0)
    t.add_argument("--usd", type=float, default=0.0)
    t.add_argument("--cache-hits", type=int, default=0)
    t.add_argument("--verified", type=int, default=0)
    t.add_argument("--unverified", type=int, default=0)
    t.add_argument("--internal-status", default=None)
    t.add_argument("--duration-ms", type=int, default=None)

    w = sub.add_parser("watchdog")
    w.add_argument("--run-id", required=True)

    fin = sub.add_parser("finish")
    fin.add_argument("--run-id", required=True)
    fin.add_argument("--status", default="done", choices=["done", "aborted", "paused"])

    for p in (c, t, w, fin):
        p.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.cmd == "create":
        rid = research_db.run_create(a.entity_type, a.model, purpose=a.purpose, brief=a.brief,
                                     entity_count=a.entity_count, client_schema=a.client_schema)
        out = {"run_id": rid, "telemetry_enabled": research_db.enabled()}
    elif a.cmd == "telemetry":
        research_db.entity_telemetry_upsert(
            a.run_id, a.entity, entity_domain=a.entity_domain, orchestrator_turns=a.turns,
            tokens_in=a.tokens_in, tokens_out=a.tokens_out, credits_spent=a.credits, usd_spent=a.usd,
            cache_hits=a.cache_hits, verified_fields=a.verified, unverified_fields=a.unverified,
            internal_status=a.internal_status, duration_ms=a.duration_ms)
        out = {"ok": True}
    elif a.cmd == "watchdog":
        out = research_db.watchdog_check(a.run_id)
    elif a.cmd == "finish":
        research_db.run_finish(a.run_id, a.status)
        out = {"ok": True}
    else:
        out = {"error": "unknown command"}

    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
