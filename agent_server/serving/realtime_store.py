"""Lakebase serving-layer reads for the real-time UI.

This module is the single boundary between the App and Postgres for live data.
It exposes read helpers used by the SSE generator and agent context bundle:

1. **Latest reads** (``read_machine_state_latest`` …) — used for SSE warmup
   and for the agent's context bundle. Always reads from ``ai_serving.*_latest``,
   which is per-shift unique on (shift_id, machine_id) so row counts are tiny.

2. **Append-history cursor reads** (``read_*_after``) — the SSE generator
   maintains a per-shift cursor and asks "what's the next distinct ts after
   my cursor?". This produces a true 1 Hz playback of producer ticks, with no
   client-side interpolation. The ``recent_seconds`` guard rejects rows from
   stale producer runs (see ``read_max_history_ts`` for why).

A 3rd layer (``*_synced``) reads from Continuous Synced Tables that mirror
the quality-gate-validated gold Delta. It exists for the future ``SSE_SOURCE=lakebase_autoscale``
path; today the prod App reads from layer 2 (``ai_serving.*_history``) because
the Database Catalog wiring isn't deployed yet.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from agent_server.memory.pg_pool import query, transaction

logger = logging.getLogger(__name__)


def _synced_schema() -> str:
    """Postgres schema where Synced Tables landed (matches the gold schema by default).

    The autoscaling Synced Tables created via ``scripts/create_synced_tables_autoscale.py``
    auto-create a Postgres schema mirroring the UC schema name. Override with
    ``PG_AUTOSCALE_SCHEMA`` if you used a different ``--target-schema``.
    """
    return os.environ.get("PG_AUTOSCALE_SCHEMA", "line_manager_streaming")


def _synced_suffix() -> str:
    """Suffix appended by the synced-tables script (default ``_synced``)."""
    return os.environ.get("PG_AUTOSCALE_TABLE_SUFFIX", "_synced")


def _normalize_line_kpi_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """Gold ``line_kpi`` lacks optional columns present on ``ai_serving.line_kpi_latest``.

    ``setdefault`` alone was not enough. Both readers here select ``*``, so a
    column that exists but is NULL arrives as a present key holding ``None``,
    which ``setdefault`` leaves exactly as it found it. Callers then do
    ``float(kpi["availability_to_now"])`` and the request dies on a
    ``TypeError`` — one NULL in one column taking down the whole answer. A
    missing metric and a null metric mean the same thing to every caller, so
    both become 0 here rather than at each use.
    """
    if not row:
        return None
    out = dict(row)
    for k in (
        "cumulative_cases",
        "availability_to_now",
        "performance_to_now",
        "quality_to_now",
        "oee_to_now",
    ):
        if out.get(k) is None:
            out[k] = 0
    return out


def clear_serving_tables(shift_id: str) -> None:
    """Delete all rows for a given shift — used during sim reset."""
    tables = [
        "ai_serving.machine_state_latest",
        "ai_serving.line_kpi_latest",
        "ai_serving.event_log_recent",
        "ai_serving.inventory_status",
        "ai_serving.scenario_commands",
    ]
    try:
        with transaction() as cur:
            if cur is None:
                return
            for t in tables:
                cur.execute(f"DELETE FROM {t} WHERE shift_id = %s", (shift_id,))
        logger.info("Cleared ai_serving tables for shift_id=%s", shift_id)
    except Exception as e:  # noqa: BLE001 - reset is best-effort
        logger.warning("clear_serving_tables failed: %s", str(e)[:300])


def read_machine_state_latest(shift_id: str) -> list[dict[str, Any]]:
    """Tier-1 dashboard: latest rows from ``ai_serving.machine_state_latest`` only.

    Synced Table mirrors (``public.machine_state``, etc.) are not queried here — Lakebase
    often injects ``LAKEBASE_SYNCED_DATA_SCHEMA=public`` before mirrors exist, which broke SSE.
    """
    return query(
        """
        SELECT shift_id, machine_id, ts, status, throughput_per_min, speed_pct, alarm_count
        FROM ai_serving.machine_state_latest
        WHERE shift_id = %s
        ORDER BY machine_id
        """,
        (shift_id,),
    ) or []


def read_line_kpi_latest(shift_id: str) -> dict[str, Any] | None:
    """Single KPI row for ``shift_id``, normalised to fill optional gold columns."""
    row = query(
        """
        SELECT *
        FROM ai_serving.line_kpi_latest
        WHERE shift_id = %s
        """,
        (shift_id,),
        fetch="one",
    )
    return _normalize_line_kpi_row(row)


def read_event_log_recent(shift_id: str, limit: int = 200) -> list[dict[str, Any]]:
    """Recent events for the EventFeed panel. ``limit`` is clamped to [1,1000]."""
    safe_limit = min(max(int(limit), 1), 1000)
    return query(
        f"""
        SELECT *
        FROM ai_serving.event_log_recent
        WHERE shift_id = %s
        ORDER BY timestamp_start DESC NULLS LAST
        LIMIT {safe_limit}
        """,
        (shift_id,),
    ) or []


def mark_machine_events_resolved(
    shift_id: str,
    machine_id: str,
    resolved_by: str = "operator",
) -> int:
    """Append a resolve marker for a machine's open events. Returns how many it closes.

    ``event_log_recent`` is append-only, mirroring the Zerobus bronze ingest it
    shadows, so an event is closed by a later ``event_type='resolved'`` row for
    the same machine rather than by updating the original. The feed pairs them
    on the machine's most recent marker, so one row closes everything open on
    that machine — which is exactly what the producer emits per target machine.

    This exists because Recover could only ever clear faults a producer command
    was holding open. Events with no command behind them — most visibly the
    carryover event the backfill seeds deliberately — had no route to closure at
    all: Recover reported nothing to clear and the row sat on the operator board
    until an eight-hour age-out hid it. Appending the marker here gives the
    operator the same authority over a seeded row as over a live fault.

    Writes nothing when the machine has no open event, so a repeated click
    cannot litter the feed with markers.
    """
    import uuid

    with transaction() as cur:
        if cur is None:
            logger.warning(
                "mark_machine_events_resolved skipped: Lakebase unavailable "
                "(shift_id=%s machine_id=%s)", shift_id, machine_id,
            )
            return 0
        cur.execute(
            """
            SELECT count(*) FROM ai_serving.event_log_recent
            WHERE shift_id = %s AND machine_id = %s
              AND timestamp_end IS NULL AND event_type <> 'resolved'
            """,
            (shift_id, machine_id),
        )
        open_events = int((cur.fetchone() or [0])[0])
        if not open_events:
            return 0
        cur.execute(
            """
            INSERT INTO ai_serving.event_log_recent (
                event_id, shift_id, machine_id, event_type, severity,
                timestamp_start, timestamp_end, duration_minutes,
                root_cause, description, corrective_action
            ) VALUES (%s, %s, %s, 'resolved', 'info', now(), now(), 0,
                      '', %s, %s)
            """,
            (
                f"EVT-{uuid.uuid4().hex[:10].upper()}",
                shift_id,
                machine_id,
                f"Incident cleared by {resolved_by}",
                "Marked recovered from the operator dashboard",
            ),
        )
    logger.info(
        "Closed %d open event(s) on %s (shift_id=%s) at the request of %s",
        open_events, machine_id, shift_id, resolved_by,
    )
    return open_events


def read_inventory_latest(shift_id: str) -> list[dict[str, Any]]:
    """All current inventory materials for ``shift_id`` (one row per material)."""
    return query(
        """
        SELECT material_id, on_hand_qty, safety_stock, reorder_point,
               status, consumption_rate_per_hour
        FROM ai_serving.inventory_status
        WHERE shift_id = %s
        ORDER BY material_id
        """,
        (shift_id,),
    ) or []


_active_commands_cache: dict[str, Any] = {"ts": 0.0, "shift_id": "", "rows": []}
_ACTIVE_COMMANDS_TTL_S = 1.0


def read_commands_active(shift_id: str) -> list[dict[str, Any]]:
    """Active scenario commands for the resolve UI.

    Reads ``ai_serving.scenario_commands`` directly from Lakebase Postgres
    via the shared ``pg_pool`` connection. This used to read from the UC
    Delta mirror via a SQL warehouse, which paid a ~200-1000 ms round-trip
    on every SSE tick (every 1 s, per active dashboard). Moving to Lakebase
    drops that to <10 ms and removes the SSE stream's only remaining
    SQL-warehouse dependency.

    The ``_active_commands_cache`` TTL is still useful: many SSE clients
    share the same shift_id; caching for ~1 s eliminates redundant queries
    when the dashboard is open in multiple tabs.
    """
    import time

    now = time.monotonic()
    if (
        _active_commands_cache["shift_id"] == shift_id
        and now - _active_commands_cache["ts"] < _ACTIVE_COMMANDS_TTL_S
    ):
        return list(_active_commands_cache["rows"])

    rows: list[dict[str, Any]] = []
    try:
        rows = query(
            """
            SELECT command_id, scenario_id, target_machine_id, severity,
                   status, created_at
            FROM ai_serving.scenario_commands
            WHERE shift_id = %s AND status = 'active'
            ORDER BY created_at DESC
            LIMIT 50
            """,
            (shift_id,),
        ) or []
    except Exception as e:  # noqa: BLE001
        # Don't crash the SSE generator on a Lakebase blip — the resolve UI
        # just stays empty for one tick. pg_pool has already rolled back and
        # retried; anything reaching here is a real query error.
        logger.warning("read_commands_active(Lakebase) failed: %s", str(e)[:200])
        rows = []

    _active_commands_cache["ts"] = now
    _active_commands_cache["shift_id"] = shift_id
    _active_commands_cache["rows"] = rows
    return list(rows)


# ── Append-history reads (1 Hz playback cursor) ────────────────────────────
#
# These read from ``ai_serving.*_history`` tables, which the streaming sync
# job populates with EVERY producer-tick row (no upsert collapse). The App's
# SSE generator maintains a single per-shift cursor; on each poll it asks for
# "the next distinct timestamp after my cursor" and emits the full set of
# rows at that timestamp. Net effect: UI updates at 1 Hz with REAL values
# from real producer ticks, no client-side interpolation.
#
# Cursor query shape (per table):
#   1. Find MIN(ts) where ts > cursor and shift_id matches → next_ts
#   2. Return all rows at that timestamp
#   3. Caller advances cursor to next_ts
#
# When no row exists with ts > cursor → returns empty + ``next_ts`` is None,
# the caller emits a "waiting for new value" indicator and HOLDS the cursor.


def read_machine_state_after(
    shift_id: str, cursor_ts: Any, recent_seconds: int = 300
) -> tuple[list[dict[str, Any]], Any]:
    """Return (rows, next_ts). ``rows`` is one row per machine at the next
    distinct timestamp after ``cursor_ts``. ``next_ts`` is the timestamp
    of those rows (or ``None`` when there's no newer data — caller holds
    cursor and shows waiting indicator).

    The ``recent_seconds`` wall-clock guard excludes rows from older
    producer runs. ``*_history`` accumulates across runs (see
    ``read_max_history_ts`` doc), and without this guard a cursor that
    crossed a stale tail could keep returning rows from yesterday's run.
    """
    rows = query(
        f"""
        WITH next_ts AS (
            SELECT MIN(ts) AS ts
            FROM ai_serving.machine_state_history
            WHERE shift_id = %s
              AND ts > %s
              AND ts > NOW() - INTERVAL '{int(recent_seconds)} seconds'
        )
        SELECT m.shift_id, m.machine_id, m.ts, m.status,
               m.throughput_per_min, m.speed_pct, m.alarm_count
        FROM ai_serving.machine_state_history m, next_ts n
        WHERE m.shift_id = %s
          AND n.ts IS NOT NULL
          AND m.ts = n.ts
        ORDER BY m.machine_id
        """,
        (shift_id, cursor_ts, shift_id),
    ) or []
    next_ts = rows[0]["ts"] if rows else None
    return rows, next_ts


def read_line_kpi_after(
    shift_id: str, cursor_ts: Any, recent_seconds: int = 300
) -> tuple[dict[str, Any] | None, Any]:
    """Single KPI row at the next distinct timestamp after ``cursor_ts``.

    See ``read_machine_state_after`` for the ``recent_seconds`` guard.
    """
    row = query(
        f"""
        SELECT *
        FROM ai_serving.line_kpi_history
        WHERE shift_id = %s
          AND ts > %s
          AND ts > NOW() - INTERVAL '{int(recent_seconds)} seconds'
        ORDER BY ts ASC
        LIMIT 1
        """,
        (shift_id, cursor_ts),
        fetch="one",
    )
    if not row:
        return None, None
    d = _normalize_line_kpi_row(row)
    return d, d.get("ts") if d else None


def read_inventory_after(
    shift_id: str, cursor_ts: Any, recent_seconds: int = 300
) -> tuple[list[dict[str, Any]], Any]:
    """All inventory materials at the next distinct timestamp after
    ``cursor_ts``. Returns ([], None) when nothing newer is available.

    See ``read_machine_state_after`` for the ``recent_seconds`` guard.
    """
    rows = query(
        f"""
        WITH next_ts AS (
            SELECT MIN(ts) AS ts
            FROM ai_serving.inventory_status_history
            WHERE shift_id = %s
              AND ts > %s
              AND ts > NOW() - INTERVAL '{int(recent_seconds)} seconds'
        )
        SELECT i.material_id, i.on_hand_qty, i.safety_stock, i.reorder_point,
               i.status, i.consumption_rate_per_hour, i.ts
        FROM ai_serving.inventory_status_history i, next_ts n
        WHERE i.shift_id = %s
          AND n.ts IS NOT NULL
          AND i.ts = n.ts
        ORDER BY i.material_id
        """,
        (shift_id, cursor_ts, shift_id),
    ) or []
    next_ts = rows[0]["ts"] if rows else None
    return rows, next_ts


def read_max_history_ts(shift_id: str, recent_seconds: int = 300) -> Any:
    """Return ``MAX(ts)`` across machine_state_history for ``shift_id``,
    restricted to the last ``recent_seconds`` of WALL-CLOCK time.

    Why the wall-clock guard: the ``*_history`` tables are append-only and
    accumulate rows from many producer runs over time (every Spark
    restart, every ``/sim/restart`` only clears ``*_latest`` — never
    ``*_history``). An unrestricted ``MAX(ts)`` happily returns a row
    written by a producer-run from yesterday with ``ts ≈ wall_clock_now -
    24 h``, and the SSE cursor anchored there would replay 50+ shifts
    of stale fault patterns before catching up. Restricting to
    ``ts > NOW() - recent_seconds`` only surfaces rows from the CURRENT
    producer run (which writes ``ts ≈ NOW``).

    Returns ``None`` when no rows exist in the recent window — the SSE
    generator falls back to a wall-clock anchor and shows "waiting" until
    the streaming-quality-sync job lands its next batch.
    """
    row = query(
        f"""
        SELECT MAX(ts) AS ts
        FROM ai_serving.machine_state_history
        WHERE shift_id = %s
          AND ts > NOW() - INTERVAL '{int(recent_seconds)} seconds'
        """,
        (shift_id,),
        fetch="one",
    )
    return row.get("ts") if row else None


def clear_history_tables(shift_id: str) -> None:
    """Delete all history rows for a shift. Called by ``/sim/restart``."""
    tables = [
        "ai_serving.machine_state_history",
        "ai_serving.line_kpi_history",
        "ai_serving.inventory_status_history",
        "ai_serving.event_log_history",
    ]
    try:
        with transaction() as cur:
            if cur is None:
                return
            for t in tables:
                cur.execute(f"DELETE FROM {t} WHERE shift_id = %s", (shift_id,))
        logger.info("Cleared ai_serving history tables for shift_id=%s", shift_id)
    except Exception as e:  # noqa: BLE001 - reset is best-effort
        logger.warning("clear_history_tables failed: %s", str(e)[:300])


# ── Lakebase Autoscaling — synced-table reads (EXPERIMENTAL, not executed) ──
#
# Nothing below runs in any current deployment. It is reached only when
# ``SSE_SOURCE=lakebase_autoscale``, and both environments read layer 2
# (``ai_serving.*_latest``) because the Database Catalog wiring these tables
# need is not deployed. Treat it as a worked reference for the autoscale
# variant rather than as a tested code path: it has no coverage against a real
# synced table, so assume it needs debugging before it serves a request.
#
# These mirror the quality-gate-validated gold Delta tables via Continuous Synced Tables. Unlike
# ``ai_serving.*_latest`` (which are pre-collapsed by the streaming sync job),
# synced tables hold the FULL gold history — we collapse to "latest per machine" /
# "most-recent KPI row" inline using window functions.
#
# Table layout (after running ``scripts/create_synced_tables_autoscale.py``):
#   <PG_AUTOSCALE_SCHEMA>.machine_state<suffix>
#   <PG_AUTOSCALE_SCHEMA>.line_kpi<suffix>
#   <PG_AUTOSCALE_SCHEMA>.event_log<suffix>


def read_machine_state_synced(shift_id: str) -> list[dict[str, Any]]:
    """Latest row per ``machine_id`` from the synced machine_state table."""
    schema = _synced_schema()
    table = f"machine_state{_synced_suffix()}"
    sql = f"""
        WITH ranked AS (
            SELECT
                shift_id, machine_id, ts, status,
                throughput_per_min, speed_pct, alarm_count,
                ROW_NUMBER() OVER (PARTITION BY machine_id ORDER BY ts DESC) AS rn
            FROM {schema}.{table}
            WHERE shift_id = %s AND ts >= now() - interval '5 minutes'
        )
        SELECT shift_id, machine_id, ts, status, throughput_per_min, speed_pct, alarm_count
        FROM ranked WHERE rn = 1
        ORDER BY machine_id
    """
    try:
        return query(sql, (shift_id,)) or []
    except Exception as e:
        logger.warning("read_machine_state_synced failed: %s", str(e)[:300])
        return []


def read_line_kpi_synced(shift_id: str) -> dict[str, Any] | None:
    """Most recent KPI row from the synced line_kpi table."""
    schema = _synced_schema()
    table = f"line_kpi{_synced_suffix()}"
    sql = f"""
        SELECT *
        FROM {schema}.{table}
        WHERE shift_id = %s
        ORDER BY ts DESC NULLS LAST
        LIMIT 1
    """
    try:
        return _normalize_line_kpi_row(query(sql, (shift_id,), fetch="one"))
    except Exception as e:
        logger.warning("read_line_kpi_synced failed: %s", str(e)[:300])
        return None


def read_event_log_synced(shift_id: str, limit: int = 200) -> list[dict[str, Any]]:
    """Recent events from the synced event_log table."""
    schema = _synced_schema()
    table = f"event_log{_synced_suffix()}"
    safe_limit = min(max(int(limit), 1), 1000)
    sql = f"""
        SELECT
            event_id, shift_id, machine_id, event_type, severity,
            timestamp_start, timestamp_end, duration_minutes, root_cause,
            description, corrective_action
        FROM {schema}.{table}
        WHERE shift_id = %s
        ORDER BY timestamp_start DESC NULLS LAST
        LIMIT {safe_limit}
    """
    try:
        return query(sql, (shift_id,)) or []
    except Exception as e:
        logger.warning("read_event_log_synced failed: %s", str(e)[:300])
        return []

