"""
research_engine — the config-driven web-research cost waterfall.

A small, dependency-light engine that turns "research these entities" into
source-verified rows, spending the cheapest provider rung that works and caching
results across runs. Standalone, and a drop-in cached upgrade for gtm-pipeline's
`web_research` capability.

    from research_engine import waterfall, web, research_db, search_providers

Nothing here reaches the network at import time. Telemetry/cache are optional —
unset the database DSN and the engine runs exactly the same, just without them.
"""
__all__ = ["env", "db", "web", "waterfall", "research_db", "search_providers"]
