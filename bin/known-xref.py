#!/usr/bin/env python3
"""
known-xref — the OPTIONAL "do we already know this company?" lookup.

The portable, generic version of an internal CRM/known-accounts cross-reference. It
matches a company (by normalized domain, then name) against a user-supplied
`research.known_companies` table — the company-level analog of gtm-pipeline's
master_contacts. You populate that table from your CRM / sending tool; this script
only reads it. See storage/postgres/known-companies-optional.sql.

It is a deterministic DB join, NOT an LLM call. With no database / no table configured
it returns `net-new` for everything (graceful) so a research run never depends on it.

    python3 bin/known-xref.py "Acme Corp" --domain acme.com --json
    # -> {"entity","domain","internal_status","internal_ref","internal_note"}

internal_status is whatever your table's `status` column says (default 'net-new' on a
miss). A common convention: customer | prior-contact | do-not-contact | net-new.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from research_engine import db  # noqa: E402
from research_engine.env import env  # noqa: E402


def _dsn() -> str | None:
    return env("RESEARCH_DATABASE_URL") or env("DATABASE_URL")


def normalize_domain(d: str) -> str:
    d = (d or "").strip().lower()
    d = re.sub(r"^https?://", "", d)
    d = d.split("/")[0]
    d = re.sub(r"^www\.", "", d)
    return d.rstrip(".")


def xref(name: str, domain: str | None = None) -> dict:
    out = {"entity": name, "domain": domain or "", "internal_status": "net-new",
           "internal_ref": "", "internal_note": ""}
    dsn = _dsn()
    if not dsn or not db.available():
        out["internal_note"] = "no known-companies DB configured"
        return out
    try:
        rows = []
        nd = normalize_domain(domain) if domain else ""
        if nd:
            rows = db.query_url(
                dsn,
                "SELECT company_name, status, notes FROM research.known_companies "
                "WHERE domain_normalized = %s LIMIT 1",
                [nd],
            )
        if not rows and name:
            rows = db.query_url(
                dsn,
                "SELECT company_name, status, notes FROM research.known_companies "
                "WHERE lower(company_name) = lower(%s) LIMIT 1",
                [name.strip()],
            )
        if rows:
            r = rows[0]
            out["internal_status"] = r.get("status") or "net-new"
            out["internal_ref"] = r.get("company_name") or (nd or name)
            out["internal_note"] = r.get("notes") or "matched research.known_companies"
    except Exception as e:  # noqa: BLE001 — a missing table / down DB just means net-new
        out["internal_note"] = f"known-companies lookup unavailable ({e.__class__.__name__})"
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name")
    ap.add_argument("--domain", default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    out = xref(a.name, a.domain)
    print(json.dumps(out) if a.json else json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
