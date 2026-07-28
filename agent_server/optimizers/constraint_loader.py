"""Loads line_constraints from Unity Catalog and provides typed lookups.

Optimizers should call ``get_constraints()`` to retrieve constraint values
instead of relying on hardcoded defaults or LLM parameter passing.
"""
import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

_cache_lock = threading.Lock()
_cache: dict[str, str] = {}
_cache_ts: float = 0.0
_CACHE_TTL = 300.0  # 5 min

_CATALOG = os.environ.get("UC_CATALOG", "main")
_SCHEMA = os.environ.get("UC_SCHEMA", "line_manager_ai_kit")


def _refresh_cache() -> dict[str, str]:
    """Fetch all line_constraints rows and cache as {constraint_key: value}."""
    global _cache, _cache_ts
    try:
        from agent_server.tools.sql_query import sql_query
        rows = sql_query.invoke("SELECT constraint_key, value FROM line_constraints")
        new_cache: dict[str, str] = {}
        for r in rows:
            if isinstance(r, dict) and "error" not in r:
                new_cache[r.get("constraint_key", "")] = str(r.get("value", ""))
        with _cache_lock:
            _cache = new_cache
            _cache_ts = time.monotonic()
        logger.info("Refreshed line_constraints cache: %d entries", len(new_cache))
        return new_cache
    except Exception:
        logger.exception("Failed to refresh line_constraints cache")
        return _cache


def get_constraints() -> dict[str, str]:
    """Return the cached constraint dict, refreshing if stale."""
    with _cache_lock:
        if _cache and (time.monotonic() - _cache_ts) < _CACHE_TTL:
            return dict(_cache)
    return _refresh_cache()


def get_float(key: str, default: float) -> float:
    """Retrieve a single numeric constraint, with fallback."""
    c = get_constraints()
    raw = c.get(key, "")
    try:
        return float(raw) if raw else default
    except (ValueError, TypeError):
        return default


def get_bool(key: str, default: bool) -> bool:
    c = get_constraints()
    raw = c.get(key, "").lower()
    if raw in ("true", "1", "yes"):
        return True
    if raw in ("false", "0", "no"):
        return False
    return default
