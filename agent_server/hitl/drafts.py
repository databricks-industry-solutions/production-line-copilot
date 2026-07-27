from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from agent_server.uc import get_uc_location


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _table(name: str) -> str:
    return get_uc_location().table(name)


def create_draft_artifacts(*, decision_id: str, specialist: str, recommendation: str) -> list[dict[str, Any]]:
    """Create 3 copy/paste-ready draft artifacts linked to a decision."""
    now = _utc_now()

    drafts: list[dict[str, Any]] = [
        {
            "artifact_type": "cmms_work_order",
            "title": f"Draft work order ({specialist})",
            "content_markdown": f"""## Draft CMMS Work Order\n\n**Requested by**: Line manager copilot\n\n**Summary**:\n{recommendation}\n\n**Evidence**:\n- Specialist: `{specialist}`\n- Decision ID: `{decision_id}`\n\n**Next steps**:\n- Confirm root cause on equipment\n- Attach photos/PLC snapshot\n- Assign technician and ETA\n""",
            "content_json": {
                "type": "cmms_work_order",
                "decision_id": decision_id,
                "specialist": specialist,
                "summary": recommendation,
                "priority": "medium",
            },
        },
        {
            "artifact_type": "qms_deviation",
            "title": "Draft deviation / hold",
            "content_markdown": f"""## Draft QMS Deviation / Hold\n\n**Reason**:\n{recommendation}\n\n**Decision ID**: `{decision_id}`\n\n**Proposed checks**:\n- Inspect last 30 minutes output\n- Verify SPC signals and re-test\n- Release/hold decision by Quality\n""",
            "content_json": {
                "type": "qms_deviation",
                "decision_id": decision_id,
                "reason": recommendation,
                "status": "draft",
            },
        },
        {
            "artifact_type": "mes_shift_note",
            "title": "Draft shift note",
            "content_markdown": f"""## Draft MES / Shift Handover Note\n\n**What happened**:\n{recommendation}\n\n**Decision ID**: `{decision_id}`\n\n**Owner**: Line manager\n\n**Follow-ups**:\n- Confirm recovery outcome\n- Log lessons learned\n""",
            "content_json": {
                "type": "mes_shift_note",
                "decision_id": decision_id,
                "note": recommendation,
            },
        },
    ]

    from agent_server.tools import sql_query as _sql_mod

    conn = _sql_mod._get_connection()
    cur = conn.cursor()
    try:
        out: list[dict[str, Any]] = []
        for d in drafts:
            artifact_id = str(uuid.uuid4())
            cur.execute(
                f"""
                INSERT INTO {_table('draft_artifacts')} (
                  artifact_id, decision_id, created_at,
                  artifact_type, title, content_markdown, content_json
                )
                VALUES (
                  %(artifact_id)s, %(decision_id)s, %(created_at)s,
                  %(artifact_type)s, %(title)s, %(content_markdown)s, %(content_json)s
                )
                """,
                {
                    "artifact_id": artifact_id,
                    "decision_id": decision_id,
                    "created_at": now,
                    "artifact_type": d["artifact_type"],
                    "title": d["title"],
                    "content_markdown": d["content_markdown"],
                    "content_json": json.dumps(d["content_json"]),
                },
            )
            out.append({**d, "artifact_id": artifact_id, "created_at": now.isoformat()})
        conn.commit()
        return out
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def list_drafts(decision_id: str) -> list[dict[str, Any]]:
    from agent_server.tools import sql_query as _sql_mod

    conn = _sql_mod._get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            SELECT artifact_id, decision_id, created_at, artifact_type, title, content_markdown, content_json
            FROM {_table('draft_artifacts')}
            WHERE decision_id = %(decision_id)s
            ORDER BY created_at DESC
            """,
            {"decision_id": decision_id},
        )
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in rows:
            if r.get("content_json"):
                try:
                    r["content_json"] = json.loads(r["content_json"])
                except Exception:
                    pass
        return rows
    finally:
        cur.close()

