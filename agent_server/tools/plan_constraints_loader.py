"""Plan + constraints loader — single source of shift planning truth for specialists.

Wraps two warehouse queries (``production_schedule`` and ``line_constraints``)
behind one tool call so specialists don't have to compose SQL for the always-
needed planning context. Used by every specialist that reasons about feasibility
(OEE, downtime, supply chain, schedule, maintenance, strategic, shift briefing).
"""

import re

from langchain_core.tools import tool

from agent_server.tools.sql_query import sql_query

_SAFE_ID = re.compile(r"^[a-zA-Z0-9_-]+$")


@tool
def load_plan_and_constraints(shift_id: str) -> dict:
    """Load the production plan and line constraints for a given shift.
    Returns {"plan": {...}, "constraints": [...]}."""
    if not _SAFE_ID.match(shift_id):
        return {"error": f"Invalid shift_id: {shift_id!r}", "plan": {}, "constraints": []}

    plan_rows = sql_query.invoke(
        f"SELECT * FROM production_schedule WHERE shift_id = '{shift_id}' LIMIT 1"
    )
    constraint_rows = sql_query.invoke("SELECT * FROM line_constraints")

    plan = plan_rows[0] if plan_rows and isinstance(plan_rows[0], dict) and "error" not in plan_rows[0] else {}
    constraints = [c for c in (constraint_rows or []) if isinstance(c, dict) and "error" not in c]

    return {"plan": plan, "constraints": constraints}
