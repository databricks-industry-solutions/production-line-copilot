"""Scenario command CRUD — Lakebase-backed (``ai_serving.scenario_commands``).

History — read this before reverting to a Unity Catalog Delta path:
  Earlier versions wrote/read ``{catalog}.{schema}.scenario_commands`` via the
  SQL warehouse. The producer's tick loop polled the same table on every
  tick, paying a SQL-warehouse round-trip (200-1000 ms steady, 4+ s on cold
  start) that visibly froze the dashboard SSE stream. Moving the table to
  Lakebase Postgres turns the per-tick poll into a sub-10 ms local query
  and eliminates the cross-workspace warehouse contention. The Zerobus
  ``event_log_bronze`` table remains the audit trail.

The module deliberately uses the singleton Lakebase connection from
``agent_server.memory.pg_pool`` so the FastAPI app shares one pool across
``/sim/incident``, ``/sim/resolve``, ``/sim/commands``, and the SSE
``read_commands_active`` reader.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


_TABLE = "ai_serving.scenario_commands"


@dataclass
class ScenarioCommand:
    command_id: str
    created_at: datetime
    created_by: str
    shift_id: str
    scenario_id: str
    target_machine_id: str | None
    severity: str
    params: dict[str, Any]
    status: str


def build_scenario_command(
    *,
    shift_id: str,
    scenario_id: str,
    created_by: str = "user",
    severity: str = "medium",
    target_machine_id: str | None = None,
    params: dict[str, Any] | None = None,
) -> ScenarioCommand:
    """Construct a pending ScenarioCommand in memory (fast, no IO)."""
    return ScenarioCommand(
        command_id=str(uuid.uuid4()),
        created_at=_utc_now(),
        created_by=created_by,
        shift_id=shift_id,
        scenario_id=scenario_id,
        target_machine_id=target_machine_id,
        severity=severity,
        params=params or {},
        status="pending",
    )


def persist_scenario_command(cmd: ScenarioCommand) -> None:
    """Insert a pre-built ScenarioCommand into Lakebase ``scenario_commands``.

    Split out from ``create_scenario_command`` so callers can run it in a
    FastAPI BackgroundTasks executor and avoid blocking the HTTP response on
    the INSERT round-trip — still useful even on Lakebase because the app's
    tail latency target is <100 ms and a fresh Postgres connection can take
    ~50 ms when the pool needs to refresh the OAuth token.
    """
    from agent_server.memory.pg_pool import transaction

    try:
        with transaction() as cur:
            if cur is None:
                logger.warning(
                    "persist_scenario_command skipped: Lakebase unavailable "
                    "(command_id=%s scenario_id=%s shift_id=%s)",
                    cmd.command_id, cmd.scenario_id, cmd.shift_id,
                )
                return
            cur.execute(
                f"""
                INSERT INTO {_TABLE} (
                    command_id, created_at, created_by, shift_id, scenario_id,
                    target_machine_id, severity, params, status
                ) VALUES (
                    %(command_id)s, %(created_at)s, %(created_by)s,
                    %(shift_id)s, %(scenario_id)s, %(target_machine_id)s,
                    %(severity)s, %(params)s::jsonb, 'pending'
                )
                ON CONFLICT (command_id) DO NOTHING
                """,
                {
                    "command_id": cmd.command_id,
                    "created_at": cmd.created_at,
                    "created_by": cmd.created_by,
                    "shift_id": cmd.shift_id,
                    "scenario_id": cmd.scenario_id,
                    "target_machine_id": cmd.target_machine_id,
                    "severity": cmd.severity,
                    "params": json.dumps(cmd.params),
                },
            )
    except Exception:
        logger.exception(
            "persist_scenario_command failed (command_id=%s scenario_id=%s)",
            cmd.command_id, cmd.scenario_id,
        )
        raise


def create_scenario_command(
    *,
    shift_id: str,
    scenario_id: str,
    created_by: str = "user",
    severity: str = "medium",
    target_machine_id: str | None = None,
    params: dict[str, Any] | None = None,
) -> ScenarioCommand:
    """Build + persist a pending scenario command in one synchronous call.

    Kept for callers that need the row written before continuing (tests,
    scripts). Hot-path ``/sim/incident`` uses build + BackgroundTasks instead
    so the user's click returns in <100ms instead of waiting on the
    Lakebase INSERT.
    """
    cmd = build_scenario_command(
        shift_id=shift_id,
        scenario_id=scenario_id,
        created_by=created_by,
        severity=severity,
        target_machine_id=target_machine_id,
        params=params,
    )
    persist_scenario_command(cmd)
    return cmd


def _json_safe_value(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.isoformat()
    return v


def resolve_command(
    *,
    command_id: str,
    resolved_by: str = "user",
    resolution_notes: str | None = None,
) -> None:
    """Set a command's status to ``resolve_requested``.

    Note: ``resolved_at`` is NOT set here — the producer sets it when
    transitioning to ``resolved`` after the recovery cascade completes.
    We do record ``resolved_by`` and ``resolution_notes`` immediately so
    the audit row stays intact even if the producer never reaches the
    terminal transition (e.g. demo restart).
    """
    from agent_server.memory.pg_pool import transaction

    try:
        with transaction() as cur:
            if cur is None:
                logger.warning(
                    "resolve_command skipped: Lakebase unavailable (command_id=%s)",
                    command_id,
                )
                return
            cur.execute(
                f"""
                UPDATE {_TABLE}
                SET status = 'resolve_requested',
                    resolved_by = %(resolved_by)s,
                    resolution_notes = %(resolution_notes)s
                WHERE command_id = %(command_id)s
                """,
                {
                    "command_id": command_id,
                    "resolved_by": resolved_by,
                    "resolution_notes": resolution_notes or "",
                },
            )
    except Exception:
        logger.exception("resolve_command failed (command_id=%s)", command_id)
        raise


def list_recent_commands(limit: int = 50) -> list[dict[str, Any]]:
    """Read recent scenario commands from Lakebase for the admin/audit view.

    Used by ``/sim/commands`` and the Admin panel. Lakebase is now the
    durable record (the previous UC mirror has been dropped). Volume is
    small (10s-100s rows per demo session) and the new
    ``idx_scenario_commands_status_created`` index keeps the SELECT under
    5 ms even after weeks of accumulated history.
    """
    from agent_server.memory.pg_pool import query

    try:
        rows = query(
            f"""
            SELECT
                command_id, created_at, created_by, shift_id, scenario_id,
                target_machine_id, severity, status, resolved_at, resolved_by
            FROM {_TABLE}
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (int(limit),),
        )
    except Exception:
        # ``query`` retries a broken connection and rolls back a failed
        # statement, so anything reaching here is a real query error. Returning
        # [] is the same fallback the previous UC implementation used.
        logger.exception("list_recent_commands query failed")
        return []
    if rows is None:
        logger.warning("list_recent_commands: Lakebase unavailable; returning empty list")
        return []
    return [{k: _json_safe_value(v) for k, v in r.items()} for r in rows]
