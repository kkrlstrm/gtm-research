"""
env — minimal environment resolution (os.environ, then an optional local .env).

BYOK, local-only: secrets are read from your environment. This module never
fetches a key over the network. If python-dotenv is installed it is used to parse
a repo-root .env without mutating os.environ; otherwise a tiny built-in parser is
used. Loading is lazy and cached.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENV_PATH = REPO / ".env"


@lru_cache(maxsize=1)
def _dotenv() -> dict[str, str]:
    out: dict[str, str] = {}
    if not ENV_PATH.exists():
        return out
    try:
        from dotenv import dotenv_values  # optional dependency

        out.update({k: v for k, v in dotenv_values(ENV_PATH).items() if v is not None})
    except Exception:
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip("'\"")
    return out


def env(name: str, required: bool = False) -> str | None:
    """Read a var from os.environ first, then a repo-root .env."""
    val = os.environ.get(name) or _dotenv().get(name)
    if required and not val:
        raise KeyError(f"env var {name!r} not set (checked os.environ and {ENV_PATH})")
    return val
