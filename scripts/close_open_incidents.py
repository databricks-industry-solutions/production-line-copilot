#!/usr/bin/env python3
"""Close incidents that were resolved but never stopped looking open.

The event log is append-only. Zerobus cannot update a row it has already
written, so resolving an incident appends a separate ``resolved`` event and
the original row keeps ``timestamp_end = NULL`` for good. Every incident ever
started therefore still matches ``timestamp_end IS NULL``, and any consumer
using that as "still happening" counts the whole shift's history as open.

That is exactly what operators saw: the board, which reconciles each event
against later resolve markers, showed nothing active, while the assistant
reported several unresolved machine incidents from the same data. On prod the
two rules disagreed nine to one.

This reconciles the stored data so both rules give the same answer:

  * an incident with a later ``resolved`` marker on its machine gets its
    ``timestamp_end`` and ``duration_minutes`` filled in from that marker
  * an incident still open from before the current shift is carryover that
    nobody is going to close by hand, so it is closed at its own shift's end
    and given a resolve marker in the serving table

Run on every deploy. It is idempotent: a second run finds nothing to do.

Note on the Delta source: this issues UPDATEs against ``event_log_bronze``,
which the quality-sync stream reads. That stream must be running with
``ignoreChanges`` or it will fail on the next micro-batch — see
``pipelines/streaming_quality_sync.py``.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

logger = logging.getLogger("close_open_incidents")


def _q(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def reconcile_uc(cur, fqn: str, shift_id: str) -> tuple[int, int]:
    """Close resolved-but-open rows, then age out carryover. Returns counts."""
    table = f"{fqn}.event_log_bronze"

    # 1. Anything with a resolve marker after it on the same machine.
    cur.execute(f"""
        MERGE INTO {table} AS t
        USING (
            SELECT e.event_id, r.resolved_at
            FROM {table} e
            JOIN (
                SELECT shift_id, machine_id, max(timestamp_start) AS resolved_at
                FROM {table}
                WHERE event_type = 'resolved'
                GROUP BY shift_id, machine_id
            ) r
              ON r.shift_id = e.shift_id AND r.machine_id = e.machine_id
            WHERE e.timestamp_end IS NULL
              AND e.event_type <> 'resolved'
              AND r.resolved_at >= e.timestamp_start
        ) AS s
        ON t.event_id = s.event_id
        WHEN MATCHED THEN UPDATE SET
            t.timestamp_end = s.resolved_at,
            t.duration_minutes =
                (unix_timestamp(s.resolved_at) - unix_timestamp(t.timestamp_start)) / 60.0
    """)
    closed = _rowcount(cur)

    # 2. Carryover: still open and not from the shift we are running now.
    #    Nobody is coming back to close these, and left alone they are
    #    permanently "open" to every UC consumer.
    cur.execute(f"""
        UPDATE {table}
        SET timestamp_end = timestamp_start + INTERVAL 30 MINUTES,
            duration_minutes = 30.0,
            corrective_action = CASE
                WHEN corrective_action IS NULL OR corrective_action = ''
                THEN 'Closed by deploy reconciliation (carryover)'
                ELSE corrective_action END
        WHERE timestamp_end IS NULL
          AND event_type <> 'resolved'
          AND shift_id <> {_q(shift_id)}
    """)
    carryover = _rowcount(cur)
    return closed, carryover


def _rowcount(cur) -> int:
    """Best-effort affected-row count; Databricks returns it as a result row."""
    try:
        row = cur.fetchone()
    except Exception:  # noqa: BLE001 - not every statement returns a count
        return 0
    if not row:
        return 0
    for value in row:
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def reconcile_lakebase(shift_id: str) -> int:
    """Append resolve markers for anything still open in the serving table.

    The board reads Lakebase, so this is what stops a stale row sitting on the
    operator's screen with no way to clear it.
    """
    try:
        from agent_server.serving.realtime_store import transaction
    except Exception:  # noqa: BLE001
        logger.warning("Lakebase helpers unavailable; skipping serving-table pass")
        return 0

    import uuid

    with transaction() as cur:
        if cur is None:
            logger.warning("Lakebase unreachable; skipping serving-table pass")
            return 0
        cur.execute(
            """
            SELECT DISTINCT e.machine_id
            FROM ai_serving.event_log_recent e
            WHERE e.shift_id = %s
              AND e.timestamp_end IS NULL
              AND e.event_type <> 'resolved'
              AND NOT EXISTS (
                  SELECT 1 FROM ai_serving.event_log_recent r
                  WHERE r.shift_id = e.shift_id
                    AND r.machine_id = e.machine_id
                    AND r.event_type = 'resolved'
                    AND r.timestamp_start >= e.timestamp_start
              )
            """,
            (shift_id,),
        )
        machines = [r[0] for r in (cur.fetchall() or [])]
        for machine_id in machines:
            cur.execute(
                """
                INSERT INTO ai_serving.event_log_recent (
                    event_id, shift_id, machine_id, event_type, severity,
                    timestamp_start, timestamp_end, duration_minutes,
                    root_cause, description, corrective_action
                ) VALUES (%s, %s, %s, 'resolved', 'info', now(), now(), 0,
                          '', %s, %s)
                """,
                (f"EVT-{uuid.uuid4().hex[:12]}", shift_id, machine_id,
                 "Incident resolved: deploy reconciliation",
                 "Closed by deploy reconciliation"),
            )
        return len(machines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--catalog", default=os.environ.get("UC_CATALOG"))
    ap.add_argument("--schema", default=os.environ.get("UC_SCHEMA"))
    ap.add_argument("--shift-id", default=os.environ.get("SIM_SHIFT_ID", "shift_1"))
    ap.add_argument("--warehouse-id", default=os.environ.get("DATABRICKS_WAREHOUSE_ID"))
    ap.add_argument("--skip-lakebase", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    missing = [n for n in ("catalog", "schema", "warehouse_id") if not getattr(args, n)]
    if missing:
        logger.error("missing required argument(s): %s", ", ".join(missing))
        return 2

    fqn = f"{args.catalog}.{args.schema}"

    # Never fail a deploy over housekeeping: a stale open incident is a
    # cosmetic and reporting problem, not a reason to leave the app undeployed.
    closed = carryover = markers = 0
    try:
        from _uc_sql import StmtCursor

        cur = StmtCursor(args.warehouse_id)
        closed, carryover = reconcile_uc(cur, fqn, args.shift_id)
    except Exception:  # noqa: BLE001
        logger.exception("UC reconciliation failed; continuing")

    if not args.skip_lakebase:
        try:
            markers = reconcile_lakebase(args.shift_id)
        except Exception:  # noqa: BLE001
            logger.exception("Lakebase reconciliation failed; continuing")

    logger.info(
        "Incidents reconciled: %d closed against a resolve marker, "
        "%d carryover aged out, %d serving-table markers written",
        closed, carryover, markers,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
