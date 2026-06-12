"""
providers — thin wrappers for the optional escalation rungs of the cost waterfall
(config/research-waterfall.yaml). Each exposes the same in/out shape as the built-in
rungs, auto-skips when its API key is absent, and writes its own research.rung_event.
Imported by the chokepoints (bin/research-search.py, bin/page-digest.py).
"""
__all__ = ["exa_search", "jina_reader", "parallel_search", "parallel_extract"]
