"""
research_db — OPTIONAL shared cache + per-entity telemetry.

Backs the `research` schema (storage/postgres/schema.sql) on any Postgres reachable
by a connection string — local or hosted. Resolves the DSN from RESEARCH_DATABASE_URL,
falling back to DATABASE_URL (so dropping this engine into a gtm-pipeline checkout
shares that project's database automatically).

Everything here is BEST-EFFORT and OPTIONAL: if no DSN is set, or psycopg2 isn't
installed, or the DB is unreachable, reads return a miss (None) and writes silently
no-op. The web facts still get researched; you just lose the cache hit / telemetry
row for that call.

Normalization is identical on read and write or every lookup misses:
  url_hash   = sha256(normalize_url(url))
  query_hash = sha256(rung + '|' + normalize_query(query))
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from . import db
from . import waterfall
from .env import env

_TRACKING = re.compile(r"^(utm_|gclid$|fbclid$|mc_eid$|mc_cid$|_hsenc$|_hsmi$)")


def _dsn() -> str | None:
    return env("RESEARCH_DATABASE_URL") or env("DATABASE_URL")


def enabled() -> bool:
    return bool(_dsn()) and db.available()


def _q(sql: str, params=None) -> list[dict] | None:
    dsn = _dsn()
    if not dsn or not db.available():
        return None
    try:
        return db.query_url(dsn, sql, params)
    except Exception as e:  # noqa: BLE001 — telemetry must never raise into a research run
        print(f"[research_db] {e.__class__.__name__}: {str(e)[:160]}", file=sys.stderr)
        return None


# --------------------------------------------------------------------------- #
# normalization + hashing
# --------------------------------------------------------------------------- #
def normalize_url(url: str) -> str:
    s = urlsplit(url.strip())
    scheme = (s.scheme or "https").lower()
    host = (s.hostname or "").lower()
    port = s.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    path = s.path.rstrip("/") or "/"
    query = urlencode([(k, v) for k, v in parse_qsl(s.query, keep_blank_values=True)
                       if not _TRACKING.match(k)])
    return urlunsplit((scheme, host, path, query, ""))


def normalize_query(q: str) -> str:
    return " ".join((q or "").lower().split())


def _uhash(url: str) -> bytes:
    return hashlib.sha256(normalize_url(url).encode()).digest()


def _qhash(rung: str, query: str) -> bytes:
    return hashlib.sha256(f"{rung}|{normalize_query(query)}".encode()).digest()


def _ttls() -> tuple[int, int]:
    c = waterfall.cfg("cache")
    return int(c.get("default_ttl_days", 30)), int(c.get("dead_url_ttl_days", 90))


# --------------------------------------------------------------------------- #
# runs
# --------------------------------------------------------------------------- #
def run_create(entity_type: str, model: str, *, purpose: str = "", brief: str = "",
               entity_count: int = 0, client_schema: str | None = None) -> str | None:
    rows = _q(
        "INSERT INTO research.run (client_schema, entity_type, model, purpose, brief, entity_count) "
        "VALUES (%s,%s,%s,%s,%s,%s) RETURNING run_id::text AS run_id",
        [client_schema, entity_type, model, purpose, brief, entity_count],
    )
    return rows[0]["run_id"] if rows else None


def run_finish(run_id: str, status: str = "done") -> None:
    _q(
        "UPDATE research.run SET status=%s, finished_at=now(), "
        "verified_rate=(SELECT verified_rate FROM research.v_run_verified_rate WHERE run_id=%s) "
        "WHERE run_id=%s",
        [status, run_id, run_id],
    )


# --------------------------------------------------------------------------- #
# page cache
# --------------------------------------------------------------------------- #
def cache_get_page(url: str) -> dict | None:
    rows = _q(
        "SELECT url, http_status, is_dead, provider, digest, raw_excerpt "
        "FROM research.page_cache WHERE url_hash=%s AND expires_at > now()",
        [_uhash(url)],
    )
    if not rows:
        return None
    _q("UPDATE research.page_cache SET fetch_count=fetch_count+1 WHERE url_hash=%s", [_uhash(url)])
    return rows[0]


def cache_put_page(url: str, http_status: int, provider: str, *, digest: str | None = None,
                   raw_excerpt: str | None = None, is_dead: bool = False) -> None:
    default_ttl, dead_ttl = _ttls()
    ttl = dead_ttl if is_dead else default_ttl
    body = (digest or raw_excerpt or "").encode()
    _q(
        "INSERT INTO research.page_cache "
        "(url_hash, url, content_hash, http_status, is_dead, provider, digest, raw_excerpt, expires_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s, now() + (interval '1 day' * %s)) "
        "ON CONFLICT (url_hash) DO UPDATE SET "
        "http_status=EXCLUDED.http_status, is_dead=EXCLUDED.is_dead, provider=EXCLUDED.provider, "
        "digest=EXCLUDED.digest, raw_excerpt=EXCLUDED.raw_excerpt, content_hash=EXCLUDED.content_hash, "
        "fetched_at=now(), expires_at=EXCLUDED.expires_at",
        [_uhash(url), url, hashlib.sha256(body).digest(), http_status, is_dead, provider,
         digest, raw_excerpt, ttl],
    )


# --------------------------------------------------------------------------- #
# search cache
# --------------------------------------------------------------------------- #
def cache_get_search(rung: str, query: str) -> dict | None:
    rows = _q(
        "SELECT query, rung, result_urls, empty FROM research.search_cache "
        "WHERE query_hash=%s AND expires_at > now()",
        [_qhash(rung, query)],
    )
    return rows[0] if rows else None


def cache_put_search(rung: str, query: str, result_urls: list, empty: bool = False) -> None:
    default_ttl, _ = _ttls()
    _q(
        "INSERT INTO research.search_cache (query_hash, query, rung, result_urls, empty, expires_at) "
        "VALUES (%s,%s,%s,%s::jsonb,%s, now() + (interval '1 day' * %s)) "
        "ON CONFLICT (query_hash) DO UPDATE SET "
        "result_urls=EXCLUDED.result_urls, empty=EXCLUDED.empty, fetched_at=now(), "
        "expires_at=EXCLUDED.expires_at",
        [_qhash(rung, query), query, rung, json.dumps(result_urls), empty, default_ttl],
    )


# --------------------------------------------------------------------------- #
# telemetry
# --------------------------------------------------------------------------- #
def rung_event(run_id: str | None, entity_name: str | None, waterfall_name: str, rung: str,
               tier: str, success: bool, *, from_cache: bool = False, latency_ms: int | None = None,
               cost_credits: int = 0, cost_usd: float = 0) -> None:
    _q(
        "INSERT INTO research.rung_event "
        "(run_id, entity_name, waterfall, rung, tier, success, from_cache, latency_ms, cost_credits, cost_usd) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        [run_id, entity_name, waterfall_name, rung, tier, success, from_cache, latency_ms,
         cost_credits, cost_usd],
    )


def entity_telemetry_upsert(run_id: str, entity_name: str, *, entity_domain: str | None = None,
                            orchestrator_turns: int = 0, tokens_in: int = 0, tokens_out: int = 0,
                            rung_hits: dict | None = None, credits_spent: int = 0, usd_spent: float = 0,
                            cache_hits: int = 0, verified_fields: int = 0, unverified_fields: int = 0,
                            internal_status: str | None = None, duration_ms: int | None = None) -> None:
    """One row per (run, entity). Numeric counters accumulate; rung_hits merges; text overwrites."""
    _q(
        "INSERT INTO research.entity_telemetry "
        "(run_id, entity_name, entity_domain, orchestrator_turns, tokens_in, tokens_out, rung_hits, "
        " credits_spent, usd_spent, cache_hits, verified_fields, unverified_fields, internal_status, duration_ms) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (run_id, entity_name) DO UPDATE SET "
        "entity_domain=COALESCE(EXCLUDED.entity_domain, research.entity_telemetry.entity_domain), "
        "orchestrator_turns=research.entity_telemetry.orchestrator_turns+EXCLUDED.orchestrator_turns, "
        "tokens_in=research.entity_telemetry.tokens_in+EXCLUDED.tokens_in, "
        "tokens_out=research.entity_telemetry.tokens_out+EXCLUDED.tokens_out, "
        "rung_hits=research.entity_telemetry.rung_hits || EXCLUDED.rung_hits, "
        "credits_spent=research.entity_telemetry.credits_spent+EXCLUDED.credits_spent, "
        "usd_spent=research.entity_telemetry.usd_spent+EXCLUDED.usd_spent, "
        "cache_hits=research.entity_telemetry.cache_hits+EXCLUDED.cache_hits, "
        "verified_fields=research.entity_telemetry.verified_fields+EXCLUDED.verified_fields, "
        "unverified_fields=research.entity_telemetry.unverified_fields+EXCLUDED.unverified_fields, "
        "internal_status=COALESCE(EXCLUDED.internal_status, research.entity_telemetry.internal_status), "
        "duration_ms=COALESCE(EXCLUDED.duration_ms, research.entity_telemetry.duration_ms)",
        [run_id, entity_name, entity_domain, orchestrator_turns, tokens_in, tokens_out,
         json.dumps(rung_hits or {}), credits_spent, usd_spent, cache_hits, verified_fields,
         unverified_fields, internal_status, duration_ms],
    )


# --------------------------------------------------------------------------- #
# watchdog
# --------------------------------------------------------------------------- #
def watchdog_check(run_id: str, window: int | None = None, min_rate: float | None = None) -> dict:
    """Trailing-window verified-rate for a run. Pauses the run row if it collapses.
    Returns {verified_rate, entities, paused, reason}; paused=False on any DB error."""
    wd = waterfall.cfg("watchdog")
    window = window or int(wd.get("window_entities", 25))
    min_rate = min_rate if min_rate is not None else float(wd.get("min_verified_rate", 0.55))
    rows = _q(
        "SELECT sum(verified_fields)::numeric / nullif(sum(verified_fields+unverified_fields),0) AS rate, "
        "       count(*) AS entities "
        "FROM (SELECT verified_fields, unverified_fields FROM research.entity_telemetry "
        "      WHERE run_id=%s ORDER BY created_at DESC LIMIT %s) t",
        [run_id, window],
    )
    if not rows or rows[0]["rate"] is None:
        return {"verified_rate": None, "entities": 0, "paused": False, "reason": None}
    rate = float(rows[0]["rate"])
    entities = int(rows[0]["entities"])
    paused = entities >= window and rate < min_rate
    reason = None
    if paused:
        reason = f"verified-rate {rate:.2f} < {min_rate} over trailing {entities} entities"
        _q("UPDATE research.run SET status='paused', paused_reason=%s WHERE run_id=%s AND status='running'",
           [reason, run_id])
    return {"verified_rate": rate, "entities": entities, "paused": paused, "reason": reason}


# --------------------------------------------------------------------------- #
# grouping
# --------------------------------------------------------------------------- #
def dedupe_by_domain(entities: list) -> dict[str, list]:
    groups: dict[str, list] = {}
    for e in entities:
        dom = (e.get("domain") or "").strip().lower() if isinstance(e, dict) else ""
        groups.setdefault(dom or f"_nodomain_{len(groups)}", []).append(e)
    return groups
