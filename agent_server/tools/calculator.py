"""Production math tool — deterministic calculations the LLM should never reason about itself.

Specialist agents call ``calculator`` for any quantitative claim (OEE math,
recovery feasibility, depletion ETA, cost-of-downtime). Doing the arithmetic
in Python eliminates the class of LLM hallucinations where the model emits a
plausible-sounding but wrong number.

All EUR/case costs and the OEE-value-per-percent are env-overridable so each
deployment (streaming workspace vs seed workspace) can ship with locale-correct economics
without code changes.
"""
import os

from langchain_core.tools import tool

from agent_server.optimizers._units import as_fraction, cases_from_bpm

SKU_COST_EUR: dict[str, float] = {
    "LAGER_330ML": float(os.environ.get("COST_LAGER_330ML", "2.50")),
    "WHEAT_500ML": float(os.environ.get("COST_WHEAT_500ML", "3.20")),
    "PILSNER_330ML": float(os.environ.get("COST_PILSNER_330ML", "2.80")),
    "DEFAULT": float(os.environ.get("COST_DEFAULT_CASE", "2.50")),
}

OEE_VALUE_EUR_PER_PCT = float(os.environ.get("OEE_VALUE_EUR_PER_PCT", "5000"))


def _get_cost_per_case(sku_id: str | None = None) -> float:
    if sku_id and sku_id in SKU_COST_EUR:
        return SKU_COST_EUR[sku_id]
    return SKU_COST_EUR["DEFAULT"]


@tool
def calculator(operation: str, params: dict) -> dict:
    """Perform production calculations.

    Operations:
    - oee: {availability, performance, quality} -> {oee, oee_pct}
    - recovery_estimate: {remaining_cases, speed_bpm, remaining_minutes, ramp_up_minutes=10} -> {feasible, projected_cases, completion_pct}
    - depletion_estimate: {current_qty, consumption_rate_per_hour, remaining_hours} -> {hours_until_empty, will_deplete}
    - cost_estimate: {downtime_minutes, line_rate_cases_per_hour, sku_id?} -> {lost_cases, estimated_cost_eur, cost_per_case}
    """
    params = params or {}
    op = operation.lower()

    if op == "oee":
        try:
            a = float(params.get("availability", 0))
            p = float(params.get("performance", 0))
            q = float(params.get("quality", 0))
        except (ValueError, TypeError):
            return {"error": "availability, performance, and quality must be numbers"}
        # The context and the specialist prompts carry these as 0-100
        # percentages, so `oee(85, 90, 98)` is the natural call and used to
        # return an oee_pct of 74,970,000 — from the one tool whose whole
        # purpose is to stop bad arithmetic reaching the operator.
        a, p, q = (as_fraction(v, field=n) for v, n in ((a, "availability"), (p, "performance"), (q, "quality")))
        oee = a * p * q
        return {"oee": round(oee, 4), "oee_pct": round(oee * 100, 2)}

    elif op == "recovery_estimate":
        try:
            remaining = int(params.get("remaining_cases", 0))
            speed = float(params.get("speed_bpm", 0))
            minutes = float(params.get("remaining_minutes", 0))
            ramp_up = float(params.get("ramp_up_minutes", 10))
        except (ValueError, TypeError):
            return {"feasible": False, "reason": "Invalid numeric parameters"}
        if speed <= 0 or minutes <= 0:
            return {"feasible": False, "reason": "Invalid speed or time"}
        effective_minutes = max(0, minutes - ramp_up)
        projected = int(cases_from_bpm(speed, effective_minutes))
        return {
            "feasible": projected >= remaining,
            "projected_cases": projected,
            "completion_pct": (
                round(min(projected / remaining * 100, 100), 1) if remaining > 0 else 100.0
            ),
            "ramp_up_minutes": ramp_up,
            "effective_production_minutes": effective_minutes,
        }

    elif op == "depletion_estimate":
        try:
            qty = float(params.get("current_qty", 0))
            rate = float(params.get("consumption_rate_per_hour", 0))
            raw_horizon = params.get("remaining_hours")
            hours = None if raw_horizon in (None, "") else float(raw_horizon)
        except (ValueError, TypeError):
            return {"error": "current_qty, consumption_rate_per_hour, and remaining_hours must be numbers"}
        if rate <= 0:
            return {"hours_until_empty": float("inf"), "will_deplete": False}
        hours_until = qty / rate
        # No horizon means the question "will it run out before the end?"
        # has not been asked, not that the answer is no. This defaulted to 0,
        # so an omitted argument made every positive stock read as safe —
        # a material two hours from empty reported will_deplete False on an
        # eight-hour shift. The caller here is an LLM and remaining_hours
        # looks optional, so say we do not know instead of guessing wrong.
        if hours is None or hours <= 0:
            return {
                "hours_until_empty": round(hours_until, 2),
                "will_deplete": None,
                "note": "remaining_hours not supplied, so depletion within the shift could not be determined",
            }
        return {
            "hours_until_empty": round(hours_until, 2),
            "will_deplete": hours_until < hours,
        }

    elif op == "cost_estimate":
        try:
            dt_min = float(params.get("downtime_minutes", 0))
            rate = float(params.get("line_rate_cases_per_hour", 0))
        except (ValueError, TypeError):
            return {"error": "downtime_minutes and line_rate_cases_per_hour must be numbers"}
        sku_id = params.get("sku_id")
        cost_per_case = _get_cost_per_case(sku_id)
        lost = int(dt_min / 60 * rate)
        return {
            "lost_cases": lost,
            "estimated_cost_eur": round(lost * cost_per_case, 2),
            "cost_per_case_eur": cost_per_case,
            "sku_id": sku_id or "DEFAULT",
        }

    return {"error": f"Unknown operation: {operation}"}
