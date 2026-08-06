"""Assembles the context bundle the agent specialists read on every request.

The bundle returned by `build_context()` is a flat dict with these keys:
    scenario           one-row dict from `scenario_index` for the shift
    current_kpi        latest row from `gold_history_line_kpi` (cases, OEE, A/P/Q, …)
    line_topology      static ordered list of machine IDs (line position)
    line_state         latest row per machine from `gold_history_machine_state`
    active_events      last 50 events from `gold_history_event_log`
    production_plan    one row from `production_schedule`
    constraints        full `line_constraints` table (small, static)
    inventory_snapshot rows from `inventory_status`
    quality_checks     last 50 rows from `quality_checks_bronze`
    recommendations    full `kpi_recommendations` table
    scenario_moments   rows from `scenario_moments` <= current time
    cip_schedule       rows from `cip_schedule`
    crew               rows from `shift_crew`
    maintenance        rows from `maintenance_schedule`

All twelve secondary queries run in parallel through `_POOL` so a cold
context build is bounded by the slowest single query rather than the sum.

`agent.py:_overlay_live_state` mutates this bundle in-place to replace the
historical operational slices (line_state, current_kpi, active_events,
inventory_snapshot) with the live ``ai_serving.*`` Lakebase snapshot (the same
source the dashboard SSE stream reads). Reference data (constraints, crew,
schedules, recommendations) is always served from UC because the producer
doesn't model it.

The UC queries here read the `gold_history_*` tables, which are the quality-gated
output of the bronze landing tables. The unsuffixed names (`machine_state`,
`line_kpi`, `event_log`, `telemetry`) still exist in the schema but have never
been written to — they are DDL left behind by an earlier layering. Querying one
returns zero rows rather than an error, which is why they cost us a release of
confidently empty answers before anyone noticed.

ENV VARS:
    DATABRICKS_WAREHOUSE_ID   SQL warehouse for all queries (required in prod)
    UC_CATALOG, UC_SCHEMA     defaults shown below; the deployed app always
                              overrides these via app.yaml
    DATABRICKS_HOST, DATABRICKS_TOKEN  CLI auth fallback; WorkspaceClient OAuth
                                       is used when the token env var is empty
"""
import copy
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from agent_server.uc import DEFAULT_UC_SCHEMA
from agent_server.validation import validate_machine_id as _validate_machine_id
from agent_server.validation import validate_shift_id as _validate_shift_id
from agent_server.validation import validate_timestamp as _validate_timestamp

logger = logging.getLogger(__name__)

from agent_server.shift_ids import ENV_SHIFT_SUFFIXES as _ENV_SHIFT_SUFFIXES  # noqa: F401
from agent_server.shift_ids import reference_shift_id as _reference_shift_id

# 12 secondary queries fan out per context build; 6 workers gives ~2 round-trips
# per build on a cold pool while staying under the warehouse concurrency cap.
_POOL = ThreadPoolExecutor(max_workers=6)

# ── Cache for build_context() ──────────────────────────────────────────────
# Measured: a cold build_context() takes ~50 s end-to-end because every
# parallel worker opens its own thread-local databricks-sql connection
# (OAuth-token mint + Thrift handshake ≈ 1.5 s per worker). The user-visible
# pattern is "every back-to-back agent question reopens the whole fan-out".
#
# The bundle returned here is the HISTORICAL bundle; live operational slices
# (line_state, current_kpi, active_events, inventory_snapshot) are overwritten
# by agent.py:_overlay_live_state on every call from the live Lakebase
# ai_serving.* tables, so a 5-second TTL on the cached bundle is invisible to
# the user — the live numbers stay tick-fresh while the static / reference data
# behind them is served from memory.
#
# Set CONTEXT_CACHE_TTL_SECONDS=0 to disable (useful for local debugging or a
# test that needs a fresh build every call).
_CONTEXT_CACHE_TTL_S = float(os.environ.get("CONTEXT_CACHE_TTL_SECONDS", "5"))

_context_cache: dict[tuple, tuple[float, dict]] = {}
_context_cache_lock = threading.Lock()
# Per-key build locks ensure a thundering herd of identical first requests
# collapses into a single SQL fan-out (single-flight). Without this, ten
# concurrent users on the same shift would each kick off ten parallel
# context-builds and immediately overwhelm the warehouse.
_build_locks: dict[tuple, threading.Lock] = {}


def _get_build_lock(key: tuple) -> threading.Lock:
    with _context_cache_lock:
        lock = _build_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _build_locks[key] = lock
        return lock


def _context_cache_clear() -> None:
    """Drop every cached context bundle.

    Called from tests; also exposed as ``build_context.cache_clear`` so
    ``/sim/restart`` or any state-changing endpoint can invalidate stale
    historical context if it ever needs to.
    """
    with _context_cache_lock:
        _context_cache.clear()

_WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
# Fallbacks are only useful for unit tests; the deployed app always sets these.
_CATALOG = os.environ.get("UC_CATALOG", "main")
_SCHEMA = os.environ.get("UC_SCHEMA", DEFAULT_UC_SCHEMA)

# One databricks-sql connection per worker thread. Long-lived because OAuth-
# refresh on every request would add ~300ms to every context build.
_thread_local = threading.local()


def _get_thread_connection():
    """Return a per-thread `databricks-sql` connection, lazily opened.

    Reuses the connection across calls (cheap) but ping-checks it with
    `SELECT 1` at most once per 60s so a half-closed warehouse connection
    surfaces as a single failure instead of a retry storm.
    """
    conn = getattr(_thread_local, "conn", None)
    last_check = getattr(_thread_local, "last_check", 0.0)
    now = time.monotonic()

    if conn is not None and now - last_check < 60.0:
        return conn
    if conn is not None:
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.close()
            _thread_local.last_check = now
            return conn
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            conn = None

    from databricks import sql as dbsql

    host = os.environ.get("DATABRICKS_HOST", "").replace("https://", "").replace("http://", "").rstrip("/")
    http_path = f"/sql/1.0/warehouses/{_WAREHOUSE_ID}"
    token = os.environ.get("DATABRICKS_TOKEN", "")
    if not token:
        # In Databricks Apps DATABRICKS_TOKEN is unset; fall back to the OAuth
        # token the WorkspaceClient holds for the App's service principal.
        # Pin to app SP so warehouse queries don't inherit the browsing user's
        # OBO token. See agent_server/tools/workspace_client.py.
        from agent_server.tools.sql_query import _extract_token
        from agent_server.tools.workspace_client import app_workspace_client
        w = app_workspace_client()
        token = _extract_token(w)

    conn = dbsql.connect(
        server_hostname=host,
        http_path=http_path,
        access_token=token,
        catalog=_CATALOG,
        schema=_SCHEMA,
    )
    _thread_local.conn = conn
    _thread_local.last_check = now
    return conn


# Hard-coded so machines render in physical-line order regardless of what UC
# returns. Source of truth: line topology in `docs/ai/design/architecture.mmd`.
# If a new machine is added to the line, add it here AND in the producer config.
MACHINE_LINE_POSITION: dict[str, int] = {
    "DEPALLETIZER": 1, "CONVEYOR_1": 2, "UNCASER": 3, "BOTTLE_WASHER": 4,
    "EMPTY_INSPECTOR": 5, "CONVEYOR_2": 6, "FILLER": 7, "CAPPER": 8,
    "PASTEURIZER": 9, "LABELLER": 10, "CODER": 11, "CONVEYOR_3": 12,
    "CASE_PACKER": 13, "SHRINK_WRAPPER": 14, "CASE_SEALER": 15,
    "CHECKWEIGHER": 16, "CONVEYOR_4": 17, "PALLETIZER": 18, "STRETCH_WRAPPER": 19,
}

MACHINE_ORDER = sorted(MACHINE_LINE_POSITION, key=MACHINE_LINE_POSITION.get)  # type: ignore[arg-type]


def _safe_rows(rows: list | None) -> list[dict]:
    """Drop tool-style {"error": …} sentinel rows so specialists never see them."""
    return [r for r in (rows or []) if isinstance(r, dict) and "error" not in r]


def _parameterized_query(sql_text: str, params: dict, label: str = "") -> list[dict]:
    """Execute one parameterized SQL query and return rows as list[dict].

    On any failure (missing table, warehouse paused, OAuth expired) returns []
    so the caller still gets a partial bundle instead of a hard 500. The
    thread-local connection is force-closed so the next call reopens fresh —
    a half-closed warehouse connection otherwise keeps failing in a loop.

    Errors are logged with the table label so missing-relation issues are
    grep-able in the App logs without inspecting every query body.
    """
    tag = label or sql_text[:60]
    t0 = time.monotonic()
    try:
        conn = _get_thread_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(sql_text, params)
            if cursor.description is None:
                return []
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
            result = [dict(zip(columns, row)) for row in rows]
            elapsed = int((time.monotonic() - t0) * 1000)
            logger.info("[%s] OK — %d rows (%dms)", tag, len(result), elapsed)
            return result
        finally:
            cursor.close()
    except Exception as exc:
        elapsed = int((time.monotonic() - t0) * 1000)
        # Distinguish "table missing" (config / seeding bug) from "warehouse down"
        # so a search for "TABLE_OR_VIEW_NOT_FOUND" in logs surfaces the real fix.
        msg = str(exc)
        if "TABLE_OR_VIEW_NOT_FOUND" in msg or "Table or view not found" in msg:
            logger.warning("[%s] table missing in %s.%s (%dms) — returning [] so other queries continue",
                           tag, _CATALOG, _SCHEMA, elapsed)
        else:
            logger.exception("[%s] parameterized query failed (%dms)", tag, elapsed)
        try:
            conn = getattr(_thread_local, "conn", None)
            if conn:
                conn.close()
        except Exception:
            pass
        _thread_local.conn = None
        return []


def _parallel_param_queries(queries: dict[str, tuple[str, dict, str]]) -> dict[str, list[dict]]:
    """Run multiple named parameterized queries in parallel. Returns {name: rows}."""
    results: dict[str, list[dict]] = {}
    futures = {}
    for name, (sql_text, params, label) in queries.items():
        futures[_POOL.submit(_parameterized_query, sql_text, params, label)] = name
    for future in as_completed(futures):
        name = futures[future]
        try:
            results[name] = future.result()
        except Exception:
            logger.exception("Parallel query %s raised exception", name)
            results[name] = []
    return results


def _build_context_uncached(
    shift_id: str, timestamp: str, machine_id: str | None = None
) -> dict:
    """Inner builder — fan out the 13 SQL queries and assemble the bundle.

    Callers should use :func:`build_context`, which wraps this with a short
    TTL cache so back-to-back agent questions on the same shift don't redo
    the full warehouse fan-out (the connection-establishment overhead
    dominates a warm-warehouse build).

    See :func:`build_context` for argument semantics and bundle shape.
    """
    t_start = time.monotonic()
    sid = _validate_shift_id(shift_id)
    ts_str = _validate_timestamp(timestamp)
    mid_arg = _validate_machine_id(machine_id)

    try:
        ts = datetime.fromisoformat(ts_str)
    except ValueError:
        logger.warning("Could not parse timestamp %r, using current time", ts_str)
        ts = datetime.now()

    # Base (env-agnostic) id for the bootstrap-seeded reference/framing tables;
    # keep the suffixed `sid` for producer-written live tables.
    ref_sid = _reference_shift_id(sid)

    logger.info(
        "Building context: shift=%s (ref=%s) ts=%s machine=%s",
        sid, ref_sid, ts_str, mid_arg,
    )

    # `scenario_index` carries the shift framing (name, type, start/end times).
    # Fetched separately because the elapsed/remaining math below needs it
    # before the parallel batch fires.
    scenario_rows = _parameterized_query(
        "SELECT * FROM scenario_index WHERE shift_id = %(sid)s LIMIT 1",
        {"sid": ref_sid},
        "scenario_index",
    )
    scenario = _safe_rows(scenario_rows)[0] if _safe_rows(scenario_rows) else {}

    shift_start = scenario.get("shift_start", ts_str)
    shift_end = scenario.get("shift_end", ts_str)
    try:
        start_dt = datetime.fromisoformat(str(shift_start))
        end_dt = datetime.fromisoformat(str(shift_end))
        elapsed = (ts - start_dt).total_seconds() / 60
        remaining = (end_dt - ts).total_seconds() / 60
    except (ValueError, TypeError):
        # Default to a standard 8h shift if the scenario row is missing or
        # malformed; specialists then fall back to "treat as new shift".
        elapsed = 0
        remaining = 480

    # Most queries take the same {sid, ts} dict; declared once to avoid drift.
    # `p`/`sid` target producer-written live tables (env-suffixed id); the
    # `_ref` variants target bootstrap-seeded reference tables (base id).
    p = {"sid": sid, "ts": ts_str}
    p_ref = {"sid": ref_sid, "ts": ts_str}

    # The whole batch fires in parallel via _parallel_param_queries. Each entry
    # is (sql, params, log_label). Keep this dict small and readable — the
    # comments below explain non-obvious clauses.
    batch = {
        # Latest row per machine: ROW_NUMBER + rn=1 is faster than DISTINCT ON
        # in the Photon SQL warehouse and avoids a sort over the entire
        # partition.
        "machine_state": (
            "WITH ranked AS ("
            "  SELECT *, ROW_NUMBER() OVER (PARTITION BY machine_id ORDER BY ts DESC) AS rn"
            "  FROM gold_history_machine_state"
            "  WHERE shift_id = %(sid)s AND ts <= %(ts)s"
            ") SELECT * FROM ranked WHERE rn = 1",
            p,
            "gold_history_machine_state",
        ),
        # Recent events — 50 is enough for the active_events panel without
        # blowing the context-bundle token budget.
        "event_log": (
            "SELECT * FROM gold_history_event_log WHERE shift_id = %(sid)s "
            "AND timestamp_start <= %(ts)s ORDER BY timestamp_start DESC LIMIT 50",
            p,
            "gold_history_event_log",
        ),
        # `production_schedule` usually has 1 row per shift, but shift splits
        # can produce 2; we take the larger planned_cases so the agent reasons
        # against the original target rather than a partial after-the-fact entry.
        "production_schedule": (
            "SELECT * FROM production_schedule WHERE shift_id = %(sid)s "
            "ORDER BY planned_cases DESC LIMIT 1",
            {"sid": ref_sid},
            "production_schedule",
        ),
        # Static reference data; small enough (<50 rows) that pulling the whole
        # table is cheaper than maintaining a where-clause taxonomy.
        "line_constraints": (
            "SELECT * FROM line_constraints",
            {},
            "line_constraints",
        ),
        "inventory_status": (
            "SELECT * FROM inventory_status WHERE shift_id = %(sid)s",
            {"sid": sid},
            "inventory_status",
        ),
        "quality_checks": (
            "SELECT * FROM quality_checks_bronze WHERE shift_id = %(sid)s "
            "AND timestamp <= %(ts)s ORDER BY timestamp DESC LIMIT 50",
            p,
            "quality_checks_bronze",
        ),
        # Static recommendations keyed off KPI thresholds — pulled in full and
        # filtered by specialists according to current_kpi.
        "kpi_recommendations": (
            "SELECT * FROM kpi_recommendations",
            {},
            "kpi_recommendations",
        ),
        "scenario_moments": (
            "SELECT * FROM scenario_moments WHERE shift_id = %(sid)s "
            "AND timestamp <= %(ts)s ORDER BY timestamp DESC",
            p_ref,
            "scenario_moments",
        ),
        "cip_schedule": (
            "SELECT * FROM cip_schedule WHERE shift_id = %(sid)s LIMIT 20",
            {"sid": ref_sid},
            "cip_schedule",
        ),
        "shift_crew": (
            "SELECT * FROM shift_crew WHERE shift_id = %(sid)s",
            {"sid": ref_sid},
            "shift_crew",
        ),
        # `maintenance_schedule` is seeded by scripts/bootstrap_reference_data.py
        # in the streaming workspace and lives natively in the seed workspace. If the table is
        # missing (fresh env, seed not run yet) _parameterized_query logs a
        # TABLE_OR_VIEW_NOT_FOUND warning and the slot stays empty — agents
        # then say "no PM data on file" instead of hard-failing.
        "maintenance_schedule": (
            "SELECT * FROM maintenance_schedule WHERE shift_id = %(sid)s",
            {"sid": ref_sid},
            "maintenance_schedule",
        ),
        "line_kpi": (
            "SELECT * FROM gold_history_line_kpi WHERE shift_id = %(sid)s "
            "AND ts <= %(ts)s ORDER BY ts DESC LIMIT 1",
            p,
            "gold_history_line_kpi",
        ),
    }

    r = _parallel_param_queries(batch)

    # Tag each row with its physical line position so the UI / agents can
    # reason about up/downstream impact without a separate join.
    line_state_raw = _safe_rows(r.get("machine_state", []))
    line_state = []
    for m in line_state_raw:
        m["line_position"] = MACHINE_LINE_POSITION.get(m.get("machine_id", ""))
        line_state.append(m)
    # 99 sorts unknown machines to the end without a None comparison error.
    line_state.sort(key=lambda x: x.get("line_position") or 99)
    active_events = _safe_rows(r.get("event_log", []))
    safe_plan = _safe_rows(r.get("production_schedule", []))
    plan = safe_plan[0] if safe_plan else {}
    constraints = _safe_rows(r.get("line_constraints", []))
    inventory = _safe_rows(r.get("inventory_status", []))
    quality_checks = _safe_rows(r.get("quality_checks", []))
    recommendations = _safe_rows(r.get("kpi_recommendations", []))
    moments = _safe_rows(r.get("scenario_moments", []))
    cip_schedule = _safe_rows(r.get("cip_schedule", []))
    crew = _safe_rows(r.get("shift_crew", []))
    maintenance = _safe_rows(r.get("maintenance_schedule", []))
    kpi_rows = _safe_rows(r.get("line_kpi", []))
    kpi = kpi_rows[0] if kpi_rows else {}

    bundle = {
        "scenario": {
            "shift_id": sid,
            "scenario_name": scenario.get("scenario_name", "Unknown"),
            "scenario_type": scenario.get("scenario_type", "unknown"),
            "current_timestamp": ts_str,
            "elapsed_minutes": round(elapsed, 1),
            "remaining_minutes": round(remaining, 1),
        },
        "current_kpi": {
            "oee_to_now": kpi.get("oee_to_now"),
            "availability_to_now": kpi.get("availability_to_now"),
            "performance_to_now": kpi.get("performance_to_now"),
            "quality_to_now": kpi.get("quality_to_now"),
            "cumulative_cases": kpi.get("cumulative_cases"),
            "total_throughput": kpi.get("total_throughput"),
            "machines_running": kpi.get("machines_running"),
            "machines_stopped": kpi.get("machines_stopped"),
        },
        "line_topology": MACHINE_ORDER,
        "line_state": line_state,
        "active_events": active_events,
        "production_plan": plan,
        "constraints": constraints,
        "inventory_snapshot": inventory,
        "quality_checks": quality_checks,
        "recommendations": recommendations,
        "scenario_moments": moments,
        "cip_schedule": cip_schedule,
        "crew": crew,
        "maintenance": maintenance,
    }

    total_ms = int((time.monotonic() - t_start) * 1000)
    # 4 chars ≈ 1 token is the OpenAI rule-of-thumb; close enough for budgets.
    approx_tokens = len(json.dumps(bundle, default=str)) // 4
    logger.info(
        "Context built in %dms: shift=%s ts=%s machines=%d events=%d ~%d tokens",
        total_ms, sid, ts_str, len(line_state), len(active_events), approx_tokens,
    )
    # 15K is the budget after which specialists tend to truncate context mid-row
    # and produce bad answers. _trim_context in agent.py keeps the per-specialist
    # slice well under this; a warning here means a query is returning unbounded
    # rows (usually an unfiltered reference table).
    if approx_tokens > 15000:
        logger.warning("Context bundle ~%d tokens, exceeds 15K budget", approx_tokens)

    return bundle


def build_context(shift_id: str, timestamp: str, machine_id: str | None = None) -> dict:
    """Assemble the historical context bundle for an agent query.

    The returned dict is later overlaid with live Lakebase ``ai_serving.*``
    state in ``agent.py:_overlay_live_state`` whenever the shift is producing,
    so operational slices (line_state, current_kpi, active_events,
    inventory_snapshot) always reflect the latest tick regardless of caching.

    Caching: bundles are memoized per ``(shift_id, timestamp, machine_id)``
    for ``CONTEXT_CACHE_TTL_SECONDS`` (default 5 s). A shallow copy is
    returned on every call so the orchestrator can safely overwrite top-level
    keys via ``_overlay_live_state`` without corrupting the cached bundle.
    Concurrent first-callers for the same key coalesce through a per-key
    lock so the fan-out runs once.

    Set ``CONTEXT_CACHE_TTL_SECONDS=0`` to disable.

    Args:
        shift_id: e.g. "shift_1" — validated to a known shift_id pattern.
        timestamp: ISO-format point-in-time used to bound queries.
                   Pre-shift briefings pass shift_start, post-shift pass shift_end.
        machine_id: optional machine focus (unused by most queries today —
                    specialists filter on it themselves via context.machine_id).

    Returns: bundle dict; see module docstring for key shapes.
    """
    if _CONTEXT_CACHE_TTL_S <= 0:
        return _build_context_uncached(shift_id, timestamp, machine_id)

    key = (shift_id, timestamp, machine_id)
    now = time.monotonic()

    with _context_cache_lock:
        entry = _context_cache.get(key)
        if entry is not None and entry[0] > now:
            logger.info(
                "Context cache HIT: shift=%s ts=%s ttl_remaining_ms=%d",
                shift_id, timestamp, int((entry[0] - now) * 1000),
            )
            return copy.copy(entry[1])

    # Cache miss — single-flight so concurrent first-callers don't all fan
    # out independently. The per-key lock is acquired without holding the
    # global cache lock to avoid serializing unrelated shifts.
    build_lock = _get_build_lock(key)
    with build_lock:
        # Double-check: a peer thread may have built and cached while we
        # were waiting for the build lock.
        with _context_cache_lock:
            entry = _context_cache.get(key)
            if entry is not None and entry[0] > time.monotonic():
                return copy.copy(entry[1])

        bundle = _build_context_uncached(shift_id, timestamp, machine_id)
        expiry = time.monotonic() + _CONTEXT_CACHE_TTL_S
        with _context_cache_lock:
            _context_cache[key] = (expiry, bundle)
        return copy.copy(bundle)


# Exposed so tests and (future) state-changing endpoints (e.g. /sim/restart)
# can drop stale historical bundles. Mirrors ``functools.lru_cache``'s API.
build_context.cache_clear = _context_cache_clear  # type: ignore[attr-defined]
