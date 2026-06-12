"""
db — tiny Postgres query helper for the optional telemetry + cache layer.

Works against ANY Postgres reachable by a connection string — local
(`postgresql://localhost/gtm_research`) or hosted (Neon, RDS, Supabase, …). It
clears libpq env vars before connecting so a DSN can't silently fall back to a
local socket, and returns rows as list[dict].

psycopg2 is an OPTIONAL dependency: if it isn't installed, `available()` returns
False and the caller (research_db) treats telemetry/cache as disabled — the
research itself still runs. Install with:  pip install psycopg2-binary
"""
from __future__ import annotations

import os
from typing import Any, Sequence

from .env import env

_LIBPQ = ("PGDATABASE", "PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGSERVICE")


def available() -> bool:
    try:
        import psycopg2  # noqa: F401
        return True
    except Exception:
        return False


def _connect(dsn: str):
    import psycopg2

    saved = {k: os.environ.pop(k, None) for k in _LIBPQ}
    try:
        return psycopg2.connect(dsn)
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def _run(dsn: str, sql: str, params: Sequence[Any] | None) -> list[dict]:
    import psycopg2.extras

    conn = _connect(dsn)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            if cur.description is None:  # non-SELECT
                conn.commit()
                return [{"rowcount": cur.rowcount}]
            rows = [dict(r) for r in cur.fetchall()]
        conn.commit()
        return rows
    finally:
        conn.close()


def query_url(dsn_or_env: str, sql: str, params: Sequence[Any] | None = None) -> list[dict]:
    """Query an arbitrary DSN, or the name of an env var that holds one."""
    dsn = dsn_or_env if "://" in dsn_or_env else env(dsn_or_env, required=True)
    return _run(dsn, sql, params)
