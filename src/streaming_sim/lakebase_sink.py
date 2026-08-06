"""Producer-side Lakebase direct-write sink (Tier-1 serving path).

Architecture: producer dual-writes per tick.

  * Zerobus → bronze Delta            (Tier-2 analytics, ~5-30 s materialization)
  * THIS MODULE → ai_serving.*_history (Tier-1 serving, ~200-500 ms)

Why dual-write at the source instead of letting streaming_quality_sync drive
the Lakebase _history sink:

  Zerobus' bronze materialization SLA (P50 ≤ 5 s, P95 ≤ 30 s) is the floor
  for any consumer that reads bronze. With the consumer-driven _history
  sink, the live UI inherited that 5-30 s lag, producing the visible
  "Live · waiting for new value" gaps. Direct producer → Lakebase writes
  bypass that floor: the same row that's queued to Zerobus is also
  inserted into Lakebase, so the SSE cursor sees it within one tick.

  The Spark Structured Streaming consumer still runs and owns the analytics
  path (Gold Delta + ai_serving.*_latest + quality_rejects) — it just no
  longer serves the live UI's _history reads. See pipelines/streaming_quality_sync.py
  for what it now writes.

Identity / auth:

  Producer continuous job runs as the deploying user (no run_as override
  in DAB), so we mint OAuth tokens via ``WorkspaceClient()`` (default
  credentials = job runtime identity = deploying user). The deploying
  user already holds Lakebase grants because streaming_quality_sync uses
  the same identity. No additional role registration required.

  Lakebase Autoscale OAuth tokens last ~1 h; this sink refreshes on a
  50-minute TTL via a single check-and-refresh on every write call (no
  extra threads — keeps the producer's single-threaded loop easy to
  reason about).

Idempotency: every INSERT carries ``ON CONFLICT … DO NOTHING`` so
producer restart / duplicate ticks (if any) are harmless.

Failure isolation: every write is wrapped in try/except; on any error
the connection is reset and the next tick reconnects fresh. The producer's
Zerobus write path is unaffected, so a Lakebase outage degrades the live
UI to the streaming-consumer-driven _latest fallback (same UX as before
this dual-write was introduced) without losing analytics data.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ── Per-table SQL (matches schemas in scripts/migrate_history_tables.sql) ──
_MS_HISTORY_SQL = (
    "INSERT INTO ai_serving.machine_state_history "
    "(shift_id, machine_id, ts, status, throughput_per_min, speed_pct, alarm_count) "
    "VALUES %s ON CONFLICT (shift_id, machine_id, ts) DO NOTHING"
)
_MS_HISTORY_TPL = (
    "(%(shift_id)s, %(machine_id)s, %(ts)s, %(status)s, "
    "%(throughput_per_min)s, %(speed_pct)s, %(alarm_count)s)"
)

_KPI_HISTORY_SQL = (
    "INSERT INTO ai_serving.line_kpi_history "
    "(shift_id, ts, machines_running, machines_stopped, machines_degraded, machines_starved, "
    "total_throughput, cumulative_cases, avg_speed_pct, "
    "availability_to_now, performance_to_now, quality_to_now, oee_to_now) "
    "VALUES %s ON CONFLICT (shift_id, ts) DO NOTHING"
)
_KPI_HISTORY_TPL = (
    "(%(shift_id)s, %(ts)s, %(machines_running)s, %(machines_stopped)s, %(machines_degraded)s, "
    "%(machines_starved)s, %(total_throughput)s, %(cumulative_cases)s, %(avg_speed_pct)s, "
    "%(availability_to_now)s, %(performance_to_now)s, %(quality_to_now)s, %(oee_to_now)s)"
)

_INV_HISTORY_SQL = (
    "INSERT INTO ai_serving.inventory_status_history "
    "(shift_id, material_id, ts, on_hand_qty, safety_stock, reorder_point, status, consumption_rate_per_hour) "
    "VALUES %s ON CONFLICT (shift_id, material_id, ts) DO NOTHING"
)
_INV_HISTORY_TPL = (
    "(%(shift_id)s, %(material_id)s, %(ts)s, %(on_hand_qty)s, %(safety_stock)s, "
    "%(reorder_point)s, %(status)s, %(consumption_rate_per_hour)s)"
)

_EVT_HISTORY_SQL = (
    "INSERT INTO ai_serving.event_log_history "
    "(event_id, shift_id, machine_id, event_type, severity, "
    "timestamp_start, timestamp_end, duration_minutes, root_cause, description, corrective_action) "
    "VALUES %s ON CONFLICT (event_id) DO NOTHING"
)
_EVT_HISTORY_TPL = (
    "(%(event_id)s, %(shift_id)s, %(machine_id)s, %(event_type)s, %(severity)s, "
    "%(timestamp_start)s, %(timestamp_end)s, %(duration_minutes)s, "
    "%(root_cause)s, %(description)s, %(corrective_action)s)"
)


def _us_to_dt(ts_us: int | float) -> datetime:
    """Convert producer's microsecond-since-epoch timestamps to aware datetime.

    Zerobus payloads carry ``ts`` as int microseconds (Spark/Arrow scale).
    Postgres TIMESTAMPTZ accepts ``datetime`` directly via psycopg2.
    """
    return datetime.fromtimestamp(float(ts_us) / 1_000_000.0, tz=timezone.utc)


def _throughput(row: dict[str, Any]) -> float:
    """Read a machine row's rate, whatever the producer called it.

    ``snapshot_to_dicts`` emits ``throughput_per_min``; the cascade's own
    ``MachineState`` field is ``throughput``. This sink read only the latter,
    so every row it wrote carried 0.0 while the same tick reached bronze at
    ~300/min. Nothing failed — the column simply defaulted — so the UI showed
    a healthy line where all nineteen machines produced nothing, and every
    specialist reading the serving layer saw the same. Accept both names.
    """
    for key in ("throughput_per_min", "throughput"):
        if row.get(key) is not None:
            return float(row[key])
    return 0.0


class LakebaseSink:
    """Direct producer → ai_serving.*_history writer.

    Lifecycle: ``__init__`` is cheap (no I/O); first ``write_*`` call mints
    a token + opens the connection. Subsequent calls reuse the connection
    until a write fails or the token expires. ``close()`` is best-effort.
    """

    # Tokens nominally last 1 h; refresh well before that.
    _TOKEN_TTL_S = 50 * 60

    def __init__(
        self,
        *,
        pg_host: str,
        pg_user: str,
        pg_database: str,
        lakebase_endpoint: str,
        pg_port: int = 5432,
        pg_sslmode: str = "require",
    ) -> None:
        self._host = pg_host
        self._user = pg_user
        self._database = pg_database
        self._port = pg_port
        self._sslmode = pg_sslmode
        self._endpoint = lakebase_endpoint
        self._conn = None
        self._token: str | None = None
        self._token_minted_at: float = 0.0

    # ── Connection management ──────────────────────────────────────────────

    def _mint_token(self) -> str:
        """OAuth token via WorkspaceClient (default credentials).

        On a Databricks Job task this resolves to the run-as identity. The
        deploying user (default run-as) already has Lakebase grants for the
        syncer job, so the same token works for INSERTs into ai_serving.*.
        """
        from databricks.sdk import WorkspaceClient

        w = WorkspaceClient()
        if self._endpoint:
            try:
                cred = w.postgres.generate_database_credential(endpoint=self._endpoint)
                return cred.token
            except Exception as e:  # noqa: BLE001
                logger.info(
                    "Lakebase generate_database_credential failed (%s) — "
                    "falling back to workspace token",
                    str(e)[:120],
                )
        # Workspace OAuth token fallback (works for both Provisioned + Autoscaling).
        token = w.config.authenticate()
        if callable(token):
            import requests

            req = requests.Request("GET", "https://dummy")
            prepared = req.prepare()
            token(prepared)
            auth_header = prepared.headers.get("Authorization", "")
            return auth_header.replace("Bearer ", "")
        return str(token)

    def _ensure_connection(self) -> Any:
        """Return a healthy psycopg2 connection, reconnecting if needed.

        Reconnect triggers:
          1. Never connected (first call).
          2. Token TTL expired.
          3. Previous write raised — connection cleared by the caller.
        """
        import psycopg2

        now = time.monotonic()
        token_stale = (now - self._token_minted_at) >= self._TOKEN_TTL_S

        if self._conn is not None and not token_stale:
            return self._conn

        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

        self._token = self._mint_token()
        self._token_minted_at = now
        self._conn = psycopg2.connect(
            host=self._host,
            port=self._port,
            user=self._user,
            password=self._token,
            dbname=self._database,
            sslmode=self._sslmode,
            connect_timeout=10,
        )
        self._conn.autocommit = False
        logger.info(
            "LakebaseSink connected to %s as %s (token len=%d)",
            self._host, self._user, len(self._token or ""),
        )
        return self._conn

    def _execute_values(self, sql: str, template: str, rows: list[dict[str, Any]]) -> int:
        """Standalone single-table write. Each call commits its own transaction.

        Used for the event-log path (incident start / resolved) where there's
        only one INSERT per call, so transaction batching wouldn't save
        anything. For the per-tick three-table path use ``write_tick`` which
        batches all three INSERTs in one transaction (saves ~2 round-trips
        per tick).
        """
        return self._execute_values_batched([(sql, template, rows)])

    def _execute_values_batched(
        self,
        ops: list[tuple[str, str, list[dict[str, Any]]]],
    ) -> int:
        """Run multiple ``execute_values`` INSERTs in a single transaction.

        ``ops`` is a list of ``(sql, template, rows)`` tuples. We open one
        cursor, execute every non-empty INSERT against it, and commit once
        at the end. That converts ``len(ops)`` independent commits into a
        single COMMIT round-trip — material when this loop is the producer's
        tick budget (currently 3 INSERTs × ~80 ms each = 240 ms saved on the
        commit path alone).

        psycopg2 import is INSIDE the try so a missing driver (e.g. workshop
        env without psycopg2-binary) degrades to "Lakebase not available"
        instead of crashing the producer. Same failure-isolation contract
        as a real connection error.
        """
        non_empty = [(sql, tpl, rows) for sql, tpl, rows in ops if rows]
        if not non_empty:
            return 0
        try:
            from psycopg2.extras import execute_values
            conn = self._ensure_connection()
            total = 0
            with conn.cursor() as cur:
                for sql, tpl, rows in non_empty:
                    execute_values(cur, sql, rows, template=tpl, page_size=1000)
                    total += len(rows)
            conn.commit()
            return total
        except Exception as e:  # noqa: BLE001
            # Reset the connection so the next call reconnects fresh. Never
            # raise — Lakebase outages must not crash the producer.
            logger.warning("LakebaseSink write failed: %s", str(e)[:300])
            try:
                if self._conn is not None:
                    self._conn.close()
            except Exception:
                pass
            self._conn = None
            return 0

    # ── Public write API ───────────────────────────────────────────────────

    def write_machine_state(
        self,
        rows: list[dict[str, Any]],
    ) -> int:
        """Insert per-machine state rows for one tick.

        Expected keys per row (from snapshot_to_dicts): shift_id, machine_id,
        ts (microseconds since epoch), status, throughput_per_min, speed_pct,
        alarm_count. We convert ts to datetime to match the _history schema.
        """
        if not rows:
            return 0
        prepared = [
            {
                "shift_id": r["shift_id"],
                "machine_id": r["machine_id"],
                "ts": _us_to_dt(r["ts"]),
                "status": r.get("status"),
                "throughput_per_min": _throughput(r),
                "speed_pct": float(r.get("speed_pct", 0.0) or 0.0),
                "alarm_count": int(r.get("alarm_count", 0) or 0),
            }
            for r in rows
        ]
        return self._execute_values(_MS_HISTORY_SQL, _MS_HISTORY_TPL, prepared)

    def write_line_kpi(self, row: dict[str, Any]) -> int:
        """Insert one line_kpi row per tick."""
        if not row:
            return 0
        prepared = [{
            "shift_id": row["shift_id"],
            "ts": _us_to_dt(row["ts"]),
            "machines_running": int(row.get("machines_running", 0) or 0),
            "machines_stopped": int(row.get("machines_stopped", 0) or 0),
            "machines_degraded": int(row.get("machines_degraded", 0) or 0),
            "machines_starved": int(row.get("machines_starved", 0) or 0),
            "total_throughput": float(row.get("total_throughput", 0.0) or 0.0),
            "cumulative_cases": float(row.get("cumulative_cases", 0.0) or 0.0),
            "avg_speed_pct": float(row.get("avg_speed_pct", 0.0) or 0.0),
            "availability_to_now": float(row.get("availability_to_now", 0.0) or 0.0),
            "performance_to_now": float(row.get("performance_to_now", 0.0) or 0.0),
            "quality_to_now": float(row.get("quality_to_now", 0.0) or 0.0),
            "oee_to_now": float(row.get("oee_to_now", 0.0) or 0.0),
        }]
        return self._execute_values(_KPI_HISTORY_SQL, _KPI_HISTORY_TPL, prepared)

    def write_inventory(self, rows: list[dict[str, Any]], ts_us: int) -> int:
        """Insert inventory snapshot rows for one tick.

        snapshot_inventory does NOT include ts (it's added inline in the
        producer's Zerobus payload). We attach the tick's ts here so the
        _history table's PK (shift_id, material_id, ts) is satisfied.
        """
        if not rows:
            return 0
        ts_dt = _us_to_dt(ts_us)
        prepared = [
            {
                "shift_id": r["shift_id"],
                "material_id": r["material_id"],
                "ts": ts_dt,
                "on_hand_qty": float(r.get("on_hand_qty", 0.0) or 0.0),
                "safety_stock": float(r.get("safety_stock", 0.0) or 0.0),
                "reorder_point": float(r.get("reorder_point", 0.0) or 0.0),
                "status": r.get("status"),
                "consumption_rate_per_hour": float(
                    r.get("consumption_rate_per_hour", 0.0) or 0.0
                ),
            }
            for r in rows
        ]
        return self._execute_values(_INV_HISTORY_SQL, _INV_HISTORY_TPL, prepared)

    def write_tick(
        self,
        machine_rows: list[dict[str, Any]],
        kpi_row: dict[str, Any] | None,
        inventory_rows: list[dict[str, Any]],
        ts_us: int,
    ) -> int:
        """One transaction for all three per-tick INSERTs.

        Replaces three separate ``write_machine_state`` / ``write_line_kpi``
        / ``write_inventory`` calls. Cuts commit round-trips from 3 to 1,
        which materially shortens the producer's tick budget when the
        Postgres round-trip is the dominant cost (~80 ms each on the
        Lakebase Autoscale endpoint we use). Net savings: ~160 ms per
        tick → ~62 % vs the prior shape.

        Idempotency invariant preserved: ``ON CONFLICT DO NOTHING`` makes
        every INSERT safe under retry, and we only commit if all three
        succeed. A failure on any of them rolls the whole tick back, so
        we never end up with partial data (e.g. machine_state inserted
        but line_kpi missing).
        """
        ops: list[tuple[str, str, list[dict[str, Any]]]] = []

        if machine_rows:
            ops.append((
                _MS_HISTORY_SQL, _MS_HISTORY_TPL,
                [
                    {
                        "shift_id": r["shift_id"],
                        "machine_id": r["machine_id"],
                        "ts": _us_to_dt(r["ts"]),
                        "status": r.get("status"),
                        "throughput_per_min": _throughput(r),
                        "speed_pct": float(r.get("speed_pct", 0.0) or 0.0),
                        "alarm_count": int(r.get("alarm_count", 0) or 0),
                    }
                    for r in machine_rows
                ],
            ))

        if kpi_row:
            ops.append((
                _KPI_HISTORY_SQL, _KPI_HISTORY_TPL,
                [{
                    "shift_id": kpi_row["shift_id"],
                    "ts": _us_to_dt(kpi_row["ts"]),
                    "machines_running": int(kpi_row.get("machines_running", 0) or 0),
                    "machines_stopped": int(kpi_row.get("machines_stopped", 0) or 0),
                    "machines_degraded": int(kpi_row.get("machines_degraded", 0) or 0),
                    "machines_starved": int(kpi_row.get("machines_starved", 0) or 0),
                    "total_throughput": float(kpi_row.get("total_throughput", 0.0) or 0.0),
                    "cumulative_cases": float(kpi_row.get("cumulative_cases", 0.0) or 0.0),
                    "avg_speed_pct": float(kpi_row.get("avg_speed_pct", 0.0) or 0.0),
                    "availability_to_now": float(kpi_row.get("availability_to_now", 0.0) or 0.0),
                    "performance_to_now": float(kpi_row.get("performance_to_now", 0.0) or 0.0),
                    "quality_to_now": float(kpi_row.get("quality_to_now", 0.0) or 0.0),
                    "oee_to_now": float(kpi_row.get("oee_to_now", 0.0) or 0.0),
                }],
            ))

        if inventory_rows:
            ts_dt = _us_to_dt(ts_us)
            ops.append((
                _INV_HISTORY_SQL, _INV_HISTORY_TPL,
                [
                    {
                        "shift_id": r["shift_id"],
                        "material_id": r["material_id"],
                        "ts": ts_dt,
                        "on_hand_qty": float(r.get("on_hand_qty", 0.0) or 0.0),
                        "safety_stock": float(r.get("safety_stock", 0.0) or 0.0),
                        "reorder_point": float(r.get("reorder_point", 0.0) or 0.0),
                        "status": r.get("status"),
                        "consumption_rate_per_hour": float(
                            r.get("consumption_rate_per_hour", 0.0) or 0.0
                        ),
                    }
                    for r in inventory_rows
                ],
            ))

        return self._execute_values_batched(ops)

    def write_event(self, event: dict[str, Any]) -> int:
        """Insert a single incident_start / incident_resolved event.

        Producer emits these via _emit_incident_* with timestamp_start and
        timestamp_end as microseconds. We convert to datetime for Postgres.
        """
        if not event:
            return 0
        prepared = [{
            "event_id": event["event_id"],
            "shift_id": event["shift_id"],
            "machine_id": event.get("machine_id"),
            "event_type": event.get("event_type"),
            "severity": event.get("severity"),
            "timestamp_start": (
                _us_to_dt(event["timestamp_start"]) if event.get("timestamp_start") else None
            ),
            "timestamp_end": (
                _us_to_dt(event["timestamp_end"]) if event.get("timestamp_end") else None
            ),
            "duration_minutes": float(event.get("duration_minutes", 0.0) or 0.0),
            "root_cause": event.get("root_cause"),
            "description": event.get("description"),
            "corrective_action": event.get("corrective_action"),
        }]
        return self._execute_values(_EVT_HISTORY_SQL, _EVT_HISTORY_TPL, prepared)

    # ── Public read API (warm-restart rehydration) ─────────────────────────

    def read_latest_cumulative_cases(
        self, shift_id: str, lookback_hours: int = 12,
        since_ts: datetime | None = None,
    ) -> float | None:
        """Most recent ``cumulative_cases`` for ``shift_id`` from Lakebase.

        Used by the producer on startup to resume the running tally so the
        dashboard Cases tile doesn't drop from e.g. 12,179 → 0 every time
        the continuous job auto-restarts on the 30 MB stdout limit.

        We READ FROM LAKEBASE (not bronze) so Clean Restart "just works":
        ``_truncate_ai_serving_demo_state`` wipes ``ai_serving.line_kpi_history``
        for the shift before restarting the producer; the new run finds no
        rows here → returns ``None`` → caller keeps the default 0.0. No
        race with the producer job's continuous trigger.

        ``since_ts`` scopes the lookup to the current shift window: on a warm
        restart the producer only resumes the tally that belongs to the shift
        it's currently in, so ``cumulative_cases`` stays a *per-shift* count
        rather than an unbounded lifetime total. When ``since_ts`` is ``None``
        we fall back to the ``lookback_hours`` interval (legacy behaviour).
        Returns ``None`` on cold start / any query error. (The sink-disabled
        case is handled at construction time by ``maybe_build_sink_from_env`` —
        if you're holding a ``LakebaseSink`` instance, reads are enabled.)
        """
        try:
            conn = self._ensure_connection()
            with conn.cursor() as cur:
                if since_ts is not None:
                    cur.execute(
                        "SELECT cumulative_cases FROM ai_serving.line_kpi_history "
                        "WHERE shift_id = %s AND ts >= %s "
                        "ORDER BY ts DESC LIMIT 1",
                        (shift_id, since_ts),
                    )
                else:
                    cur.execute(
                        "SELECT cumulative_cases FROM ai_serving.line_kpi_history "
                        "WHERE shift_id = %s "
                        f"AND ts >= NOW() - INTERVAL '{int(lookback_hours)} hours' "
                        "ORDER BY ts DESC LIMIT 1",
                        (shift_id,),
                    )
                row = cur.fetchone()
            # SELECT-only path — explicit rollback so we don't leave a dangling
            # transaction on the connection for the next writer to inherit.
            try:
                conn.rollback()
            except Exception:
                pass
            return float(row[0]) if row and row[0] is not None else None
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "LakebaseSink.read_latest_cumulative_cases failed: %s", str(e)[:200]
            )
            try:
                if self._conn is not None:
                    self._conn.close()
            except Exception:
                pass
            self._conn = None
            return None

    def read_latest_inventory(
        self, shift_id: str, lookback_hours: int = 24,
    ) -> list[dict[str, Any]]:
        """Most recent ``on_hand_qty`` per ``material_id`` for ``shift_id``.

        Same rationale as ``read_latest_cumulative_cases``: read from
        Lakebase (Clean Restart wipes it) instead of bronze (Clean Restart
        does NOT wipe it) so a Clean Restart deterministically produces a
        fresh inventory baseline. Without this, the new producer rehydrates
        from ``inventory_status_bronze`` which still has the depleted values
        from the previous shift, and the UI shows "previous values, depleting".

        Returns ``[]`` on any failure — caller falls back to ``init_inventory()``.
        """
        try:
            conn = self._ensure_connection()
            with conn.cursor() as cur:
                # Postgres DISTINCT ON: take the highest ts row per material.
                cur.execute(
                    "SELECT DISTINCT ON (material_id) material_id, on_hand_qty, status "
                    "FROM ai_serving.inventory_status_history "
                    "WHERE shift_id = %s "
                    f"AND ts >= NOW() - INTERVAL '{int(lookback_hours)} hours' "
                    "ORDER BY material_id, ts DESC",
                    (shift_id,),
                )
                rows = cur.fetchall()
            try:
                conn.rollback()
            except Exception:
                pass
            return [
                {"material_id": r[0], "on_hand_qty": r[1], "status": r[2]}
                for r in rows
            ]
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "LakebaseSink.read_latest_inventory failed: %s", str(e)[:200]
            )
            try:
                if self._conn is not None:
                    self._conn.close()
            except Exception:
                pass
            self._conn = None
            return []

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None


def maybe_build_sink_from_env() -> LakebaseSink | None:
    """Construct a LakebaseSink if all required env vars are set, else None.

    Disable knob: ``SIM_LAKEBASE_DIRECT_WRITE=false`` short-circuits even when
    env is fully populated, so we can fall back to the streaming-consumer-only
    path without redeploying the bundle.
    """
    if (os.environ.get("SIM_LAKEBASE_DIRECT_WRITE") or "true").strip().lower() in (
        "0", "false", "no", "off",
    ):
        logger.info("LakebaseSink disabled via SIM_LAKEBASE_DIRECT_WRITE=false")
        return None
    host = (os.environ.get("PGHOST") or "").strip()
    user = (os.environ.get("PGUSER") or "").strip()
    endpoint = (os.environ.get("LAKEBASE_ENDPOINT") or "").strip()
    if not (host and user and endpoint):
        logger.info(
            "LakebaseSink disabled: missing one of PGHOST=%s PGUSER=%s LAKEBASE_ENDPOINT=%s",
            bool(host), bool(user), bool(endpoint),
        )
        return None
    return LakebaseSink(
        pg_host=host,
        pg_user=user,
        pg_database=(os.environ.get("PGDATABASE") or "databricks_postgres"),
        lakebase_endpoint=endpoint,
        pg_port=int(os.environ.get("PGPORT", "5432")),
        pg_sslmode=(os.environ.get("PGSSLMODE") or "require"),
    )
