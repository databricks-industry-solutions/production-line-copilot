"""Plan + constraints loader — single source of shift planning truth for specialists.

Wraps two warehouse queries (``production_schedule`` and ``line_constraints``)
behind one tool call so specialists don't have to compose SQL for the always-
needed planning context. Used by every specialist that reasons about feasibility
(OEE, downtime, supply chain, schedule, maintenance, strategic, shift briefing).
"""

import re

from langchain_core.tools import tool

from agent_server.shift_ids import reference_shift_id
from agent_server.tools.sql_query import sql_query

_SAFE_ID = re.compile(r"^[a-zA-Z0-9_-]+$")


@tool
def load_plan_and_constraints(shift_id: str) -> dict:
    """Load the production plan and line constraints for a given shift.
    Returns {"plan": {...}, "constraints": [...]}."""
    if not _SAFE_ID.match(shift_id):
        return {"error": f"Invalid shift_id: {shift_id!r}", "plan": {}, "constraints": []}

    # Specialists are handed the live shift id (``shift_1_dev`` in dev), but
    # production_schedule is seeded with the base id. Passing the live id
    # straight through matched nothing, so this tool returned an empty plan for
    # every question asked in dev — silently, since an empty plan is not an error.
    # No LIMIT 1 here. A shift is not always one row: a changeover segment
    # carries planned_cases = 0 and the production segment follows it, so
    # shift_4 has two. With an unordered LIMIT 1 the warehouse was free to hand
    # back the changeover, and every feasibility answer for that shift was then
    # computed against a target of zero cases — a plan that is trivially met.
    plan_rows = sql_query.invoke(
        "SELECT * FROM production_schedule WHERE shift_id = "
        f"'{reference_shift_id(shift_id)}' ORDER BY start_time"
    )
    constraint_rows = sql_query.invoke("SELECT * FROM line_constraints")

    # Both failure modes used to leave through the same door as success: the
    # error rows were filtered out and then dropped, so a warehouse outage was
    # reported as "this shift has no plan and the line has no constraints".
    # The model cannot tell those apart, and one of them is recoverable.
    errors = [
        r["error"]
        for r in list(plan_rows or []) + list(constraint_rows or [])
        if isinstance(r, dict) and r.get("error")
    ]

    segments = [
        r for r in (plan_rows or []) if isinstance(r, dict) and "error" not in r
    ]
    plan = _production_segment(segments)
    constraints = [c for c in (constraint_rows or []) if isinstance(c, dict) and "error" not in c]

    result = {"plan": plan, "constraints": constraints}
    # Surface the split rather than hiding it behind one chosen row: a shift
    # that spends 45 minutes in changeover has less time to make its cases, and
    # that is exactly what a feasibility question turns on.
    if len(segments) > 1:
        result["plan_segments"] = segments
        result["planned_cases_total"] = sum(_as_number(s.get("planned_cases")) for s in segments)
    if errors and not (plan or constraints):
        result["error"] = f"Could not load plan or constraints: {errors[0]}"
    return result


def _as_number(value) -> float:
    """Warehouse numerics arrive as int, Decimal or str depending on the driver."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _production_segment(segments: list[dict]) -> dict:
    """The segment a planning question is actually about.

    The one with cases to make. Ties and all-zero shifts fall back to the first
    segment in time order, so this never returns nothing when there is a row.
    """
    if not segments:
        return {}
    return max(segments, key=lambda s: _as_number(s.get("planned_cases")))
