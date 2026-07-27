"""SQL Warehouse query tool — read-only entry point for every specialist.

This is the only path the agent layer uses to read from Unity Catalog. It
deliberately:
  - holds a single cached connection across requests (warehouse cold-start is
    ~5–8 s and we issue many queries per agent turn)
  - health-checks the cached connection on a 60 s schedule rather than per
    call, to avoid lock contention under load
  - retries up to 3 times with reconnect on transient failures (warehouse
    autoscaling occasionally kills idle sessions)
  - rejects anything that isn't ``SELECT`` / ``WITH`` and any DDL/DML keyword
    in the first-token set (defense in depth — the agent prompts also forbid
    writes)
  - auto-appends ``LIMIT 500`` when missing, capping memory blow-ups from
    naive agent queries (and ``[error]`` warnings when the cap is hit)
"""

import logging
import os
import threading
import time

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

_WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
_CATALOG = os.environ.get("UC_CATALOG", "main")
_SCHEMA = os.environ.get("UC_SCHEMA", "line_manager_ai_kit")

_connection_lock = threading.Lock()
_cached_connection = None
_last_health_check = 0.0
_HEALTH_CHECK_INTERVAL = 60.0


def _extract_token(w) -> str:
    """Extract an OAuth bearer token from a ``WorkspaceClient``.

    Three fallback paths because ``w.config.authenticate`` has changed signature
    across SDK releases used in the field:
      1) zero-arg call returning a headers dict (newer SDK)
      2) one-arg call that mutates a dict in-place (older SDK)
      3) ``w.config.token`` attribute (PAT auth, local dev)

    Returns ``""`` rather than raising so the caller can produce a clear
    "Missing SQL config" error instead of a cryptic SDK trace.
    """
    try:
        headers = w.config.authenticate()
        if isinstance(headers, dict):
            return headers.get("Authorization", "").removeprefix("Bearer ")
    except TypeError:
        pass

    try:
        headers_mut: dict[str, str] = {}
        w.config.authenticate(headers_mut)
        return headers_mut.get("Authorization", "").removeprefix("Bearer ")
    except TypeError:
        pass

    try:
        return w.config.token
    except Exception:
        logger.debug("Failed to read token from w.config.token", exc_info=True)

    return ""


def _get_host() -> str:
    raw = os.environ.get("DATABRICKS_HOST", "")
    return raw.replace("https://", "").replace("http://", "").rstrip("/")


def _create_connection():
    """Create a fresh SQL warehouse connection."""
    from databricks import sql as dbsql

    host = _get_host()
    http_path = f"/sql/1.0/warehouses/{_WAREHOUSE_ID}"

    if not host or not _WAREHOUSE_ID:
        raise RuntimeError(
            f"Missing SQL config: DATABRICKS_HOST={host!r}, "
            f"DATABRICKS_WAREHOUSE_ID={_WAREHOUSE_ID!r}"
        )

    logger.info(
        "SQL connect: host=%s warehouse=%s catalog=%s schema=%s",
        host[:40], _WAREHOUSE_ID[:12], _CATALOG, _SCHEMA,
    )

    token = os.environ.get("DATABRICKS_TOKEN", "")
    if not token:
        # why: pin to app SP so warehouse queries don't inherit the browsing
        # user's OBO token (which won't have CAN_USE on the warehouse).
        # See agent_server/tools/workspace_client.py.
        from agent_server.tools.workspace_client import app_workspace_client
        w = app_workspace_client()
        token = _extract_token(w)
        if not token:
            raise RuntimeError("Could not obtain OAuth token")
        logger.info("Using OAuth token (len=%d)", len(token))

    return dbsql.connect(
        server_hostname=host,
        http_path=http_path,
        access_token=token,
        catalog=_CATALOG,
        schema=_SCHEMA,
    )


def _get_connection():
    """Return a cached connection. Only health-checks periodically to avoid lock contention."""
    global _cached_connection, _last_health_check

    with _connection_lock:
        now = time.monotonic()

        if _cached_connection is not None:
            if now - _last_health_check < _HEALTH_CHECK_INTERVAL:
                return _cached_connection
            try:
                cur = _cached_connection.cursor()
                cur.execute("SELECT 1")
                cur.close()
                _last_health_check = now
                return _cached_connection
            except Exception:
                logger.info("Cached SQL connection stale, reconnecting")
                try:
                    _cached_connection.close()
                except Exception:
                    logger.debug("Failed to close stale SQL connection", exc_info=True)
                _cached_connection = None

        _cached_connection = _create_connection()
        _last_health_check = now
        return _cached_connection


def _invalidate_connection():
    global _cached_connection
    with _connection_lock:
        if _cached_connection:
            try:
                _cached_connection.close()
            except Exception:
                logger.debug("Failed to close cached SQL connection during invalidate", exc_info=True)
        _cached_connection = None


_DANGEROUS_KEYWORDS = {"INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE", "MERGE", "GRANT", "REVOKE"}


@tool
def sql_query(query: str) -> list[dict]:
    """Execute a read-only SQL SELECT query against the Databricks SQL Warehouse.
    Only SELECT statements are allowed. Returns results as a list of dicts."""
    stripped = query.strip()
    upper = stripped.upper()

    if not upper.startswith("SELECT") and not upper.startswith("WITH"):
        return [{"error": "Only SELECT queries are allowed"}]

    # Token-set check — catches DDL/DML keywords appearing as standalone words
    # anywhere in the query (e.g. "SELECT 1; DROP TABLE x"). Not a SQL parser:
    # an attacker who quotes keywords inside string literals could bypass it,
    # but combined with the SELECT/WITH prefix gate and read-only warehouse
    # permissions for the App SP this is the right belt-and-braces level for
    # an LLM-emitted query.
    first_word_set = set(upper.split())
    dangerous = first_word_set & _DANGEROUS_KEYWORDS
    if dangerous:
        return [{"error": f"Forbidden SQL keywords detected: {dangerous}"}]

    if "LIMIT" not in upper:
        query = stripped.rstrip(";") + " LIMIT 500"

    for attempt in range(3):
        try:
            connection = _get_connection()
            cursor = connection.cursor()
            try:
                cursor.execute(query)
                if cursor.description is None:
                    return []
                columns = [desc[0] for desc in cursor.description]
                rows = cursor.fetchall()
                result = [dict(zip(columns, row)) for row in rows]
                if len(result) == 500:
                    logger.info("Query returned exactly 500 rows (limit may have truncated results)")
                return result
            finally:
                cursor.close()
        except Exception as e:
            if attempt < 2:
                logger.warning("SQL attempt %d failed, reconnecting: %s", attempt + 1, str(e)[:100])
                _invalidate_connection()
            else:
                logger.exception("SQL query failed after %d retries: %s", attempt + 1, query[:200])
                return [{"error": str(e)}]

    return [{"error": "unreachable"}]
