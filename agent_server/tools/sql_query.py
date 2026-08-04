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

# One connection per thread, because the driver says so: databricks-sql-connector
# declares PEP 249 ``threadsafety = 1`` — "threads may share the module, but not
# connections". This used to be a single module-level connection guarded by a
# lock that was held only while HANDING IT OUT, so every worker then ran
# `cursor.execute` on the same connection at the same time. Two things follow
# from that, and both get worse the more workers there are:
#
#   * concurrent statements interleave on one Thrift transport, which the driver
#     does not promise to survive;
#   * a failure in one thread called ``_invalidate_connection()``, which closed
#     the connection every other thread was mid-query on, failing them too —
#     each of which then invalidated in turn.
#
# The specialist pool went from two workers to six for concurrency this release,
# which is what turned a narrow race into a likely one.
#
# Thread-local removes the sharing rather than locking around it. Serialising
# instead would have been correct and useless: the query IS the latency, so a
# global lock would have handed back the concurrency the pool was widened for.
# It also drops two round trips out of the critical section — the old lock was
# held across both the `SELECT 1` health check and a full connection handshake,
# so every thread queued behind whichever one was reconnecting.
#
# Connections are created lazily by threads that actually run SQL, so the count
# tracks real concurrency rather than pool size, and a warehouse multiplexes
# sessions cheaply. Pool threads are long-lived, so a connection is reused for
# the life of the thread rather than reopened per query.
_local = threading.local()
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


# Stamped on every statement this tool runs so the Cost panel can separate the
# app's warehouse usage from everything else sharing the same warehouse. The
# app SP's identity already distinguishes most of it, but an SP can be reused
# across apps and jobs whereas this tag cannot; it also makes the app's queries
# filterable in the Query History UI.
#
# Connector 4.2.5 reads tags out of `session_configuration` (see
# databricks/sql/client.py), not from the top-level `query_tags=` argument that
# newer releases document, so this goes through the session config using the
# documented "key:value,key:value" serialisation.
APP_QUERY_TAG_KEY = "app"
APP_QUERY_TAG_VALUE = "prodline-copilot"
_QUERY_TAGS_SESSION_CONF = {"query_tags": f"{APP_QUERY_TAG_KEY}:{APP_QUERY_TAG_VALUE}"}
_query_tags_supported = True


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

    kwargs = dict(
        server_hostname=host,
        http_path=http_path,
        access_token=token,
        catalog=_CATALOG,
        schema=_SCHEMA,
    )
    # Query tags are Public Preview, and session configuration is applied as a
    # `SET` at session open — so on a workspace that doesn't know the parameter
    # this is the difference between a tagged connection and no connection at
    # all. Cost attribution is not worth taking the app's only data path down
    # for, so an unsupported tag degrades to an untagged session once and stops
    # being retried.
    global _query_tags_supported
    if _query_tags_supported:
        try:
            return dbsql.connect(session_configuration=dict(_QUERY_TAGS_SESSION_CONF), **kwargs)
        except Exception as e:
            _query_tags_supported = False
            logger.warning(
                "Warehouse rejected the query_tags session parameter (%s); reconnecting untagged. "
                "Cost attribution falls back to the app service principal's identity.",
                str(e)[:160],
            )
    return dbsql.connect(**kwargs)


def _get_connection():
    """Return this thread's connection, opening or replacing it as needed.

    Health-checked at most once a minute: a warehouse connection usually
    outlives that, and paying a round trip per query to find out would cost more
    than the occasional retry in ``sql_query``.
    """
    now = time.monotonic()
    connection = getattr(_local, "connection", None)

    if connection is not None:
        if now - getattr(_local, "last_health_check", 0.0) < _HEALTH_CHECK_INTERVAL:
            return connection
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT 1")
            cursor.close()
            _local.last_health_check = now
            return connection
        except Exception:
            logger.info("Cached SQL connection stale, reconnecting")
            _close_quietly(connection, "stale")
            _local.connection = None

    _local.connection = _create_connection()
    _local.last_health_check = now
    return _local.connection


def _invalidate_connection():
    """Drop this thread's connection. Never touches another thread's."""
    connection = getattr(_local, "connection", None)
    if connection is not None:
        _close_quietly(connection, "invalidate")
    _local.connection = None
    _local.last_health_check = 0.0


def _close_quietly(connection, reason: str) -> None:
    """Close on a best-effort basis: the caller is already discarding it."""
    try:
        connection.close()
    except Exception:
        logger.debug("Failed to close SQL connection during %s", reason, exc_info=True)


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
