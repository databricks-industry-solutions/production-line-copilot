from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from agent_server.uc import get_uc_location


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _table(name: str) -> str:
    return get_uc_location().table(name)


def insert_decision(payload: dict[str, Any]) -> None:
    from agent_server.tools import sql_query as _sql_mod

    conn = _sql_mod._get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            INSERT INTO {_table('decision_log')} (
              decision_id, created_at, shift_id, machine_id, timestamp,
              user_action, specialist, model_used, response_time_ms,
              query, recommendation, reasoning_steps_json, trace_spans_json
            )
            VALUES (
              %(decision_id)s, %(created_at)s, %(shift_id)s, %(machine_id)s, %(timestamp)s,
              %(user_action)s, %(specialist)s, %(model_used)s, %(response_time_ms)s,
              %(query)s, %(recommendation)s, %(reasoning_steps_json)s, %(trace_spans_json)s
            )
            """,
            {
                "decision_id": payload.get("decision_id"),
                "created_at": _utc_now(),
                "shift_id": payload.get("shift_id"),
                "machine_id": payload.get("machine_id"),
                "timestamp": payload.get("timestamp"),
                "user_action": payload.get("user_action"),
                "specialist": payload.get("specialist"),
                "model_used": payload.get("model_used"),
                "response_time_ms": payload.get("response_time_ms"),
                "query": payload.get("query"),
                "recommendation": payload.get("recommendation"),
                "reasoning_steps_json": json.dumps(payload.get("reasoning_steps") or []),
                "trace_spans_json": json.dumps(payload.get("trace_spans") or []),
            },
        )
    finally:
        cur.close()


def list_recent(limit: int = 50) -> list[dict[str, Any]]:
    from agent_server.tools import sql_query as _sql_mod

    conn = _sql_mod._get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            SELECT
              decision_id,
              created_at,
              shift_id,
              machine_id,
              timestamp,
              user_action,
              specialist,
              model_used,
              response_time_ms,
              query,
              recommendation,
              reasoning_steps_json,
              trace_spans_json
            FROM {_table('decision_log')}
            ORDER BY created_at DESC
            LIMIT {int(limit)}
            """
        )
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in rows:
            for k in ("reasoning_steps_json", "trace_spans_json"):
                if r.get(k):
                    try:
                        r[k] = json.loads(r[k])
                    except Exception:
                        pass
        return rows
    finally:
        cur.close()

