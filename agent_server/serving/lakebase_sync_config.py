"""Lakebase read routing helpers (no Postgres imports).

``SSE_USE_AI_SERVING_ONLY`` (true/1/yes/on): ignore ``LAKEBASE_SYNCED_DATA_SCHEMA`` for helpers
that still consult ``lakebase_synced_data_schema()`` (e.g. future tooling).

Tier-1 SSE Postgres reads in ``realtime_store`` use **``ai_serving.*`` only** — they never
query Synced Table mirrors (``public.machine_state``, …) so Lakebase-injected schema env
cannot break the live dashboard.
"""

from __future__ import annotations

import os
import re

LAKEBASE_SYNCED_DATA_SCHEMA_ENV = "LAKEBASE_SYNCED_DATA_SCHEMA"
# When true, never read Tier-1 from public.* mirrors (Fix A: ignore UI/Lakebase-injected LAKEBASE_SYNCED_DATA_SCHEMA).
SSE_USE_AI_SERVING_ONLY_ENV = "SSE_USE_AI_SERVING_ONLY"


def _truthy(val: str | None) -> bool:
    return (val or "").strip().lower() in ("1", "true", "yes", "on")


def _bootstrap_preview_tier1_env() -> None:
    """Databricks Apps sometimes omit YAML env for child processes; Lakebase may inject ``public`` reads.

    If the app name looks like the **preview** deployment and ``SSE_USE_AI_SERVING_ONLY`` is
    still unset/empty, default it to **true** so Tier-1 never queries missing ``public.*`` mirrors.

    For environments where the app name does not contain ``preview`` but the
    UC catalog still needs the same defensive default, set
    ``SSE_USE_AI_SERVING_ONLY_AUTO_CATALOGS`` to a comma-separated list of
    catalog names; matching values (case-insensitive) flip the env on too.

    Set ``SSE_USE_AI_SERVING_ONLY_FORCE_OFF=1`` to skip this entirely.
    """
    if _truthy(os.environ.get("SSE_USE_AI_SERVING_ONLY_FORCE_OFF")):
        return
    if (os.environ.get(SSE_USE_AI_SERVING_ONLY_ENV) or "").strip():
        return
    combined = " ".join(
        os.environ.get(k, "") for k in ("DATABRICKS_APP_NAME", "APP_NAME", "DATABRICKS_APP_DEPLOYMENT_NAME")
    ).lower()
    if "preview" in combined:
        os.environ[SSE_USE_AI_SERVING_ONLY_ENV] = "true"
        return
    auto_catalogs_raw = (os.environ.get("SSE_USE_AI_SERVING_ONLY_AUTO_CATALOGS") or "").strip()
    if auto_catalogs_raw:
        auto_catalogs = {c.strip().lower() for c in auto_catalogs_raw.split(",") if c.strip()}
        uc = (os.environ.get("UC_CATALOG") or "").strip().lower()
        if uc and uc in auto_catalogs:
            os.environ[SSE_USE_AI_SERVING_ONLY_ENV] = "true"


def lakebase_synced_data_schema() -> str | None:
    """Schema for Synced Table mirrors in Lakebase (e.g. ``public``), or None to read ``ai_serving``."""
    _bootstrap_preview_tier1_env()
    if _truthy(os.environ.get(SSE_USE_AI_SERVING_ONLY_ENV)):
        return None
    raw = (os.environ.get(LAKEBASE_SYNCED_DATA_SCHEMA_ENV) or "").strip()
    return raw or None


def _iter_exception_chain(exc: BaseException):
    """Yield ``exc`` and chained ``__cause__`` / ``__context__`` (deduplicated)."""
    seen: set[int] = set()
    stack: list[BaseException] = [exc]
    while stack:
        e = stack.pop()
        if e is None or id(e) in seen:
            continue
        seen.add(id(e))
        yield e
        c = getattr(e, "__cause__", None)
        if c is not None:
            stack.append(c)
        ctx = getattr(e, "__context__", None)
        if ctx is not None:
            stack.append(ctx)


def is_missing_synced_relation(exc: BaseException) -> bool:
    """True when Postgres reports a missing relation (synced mirror tables not provisioned)."""
    try:
        import psycopg2
        import psycopg2.errors as pe

        for e in _iter_exception_chain(exc):
            if isinstance(e, pe.UndefinedTable):
                return True
            if isinstance(e, psycopg2.ProgrammingError) and getattr(e, "pgcode", None) == "42P01":
                return True
            if isinstance(e, psycopg2.Error) and getattr(e, "pgcode", None) == "42P01":
                return True
    except ImportError:
        pass
    for e in _iter_exception_chain(exc):
        code = getattr(e, "pgcode", None)
        if code == "42P01":  # undefined_table
            return True
        s = str(e).lower()
        if "does not exist" in s and ("relation" in s or "table" in s):
            return True
        if "undefinedtable" in type(e).__name__.lower():
            return True
    return False


def pg_ident_quoted(name: str) -> str:
    """Allow only unquoted-safe PostgreSQL identifiers (schema / single-segment names)."""
    if not name or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError(f"Invalid PostgreSQL identifier: {name!r}")
    return name
