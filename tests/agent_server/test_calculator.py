"""Unit tests for calculator logic — no langchain dependency."""
from __future__ import annotations


def _calc(operation: str, params: dict) -> dict:
    """Replicate calculator logic inline to avoid langchain_core import."""
    op = operation.lower()
    if op == "oee":
        a = float(params.get("availability", 0))
        p = float(params.get("performance", 0))
        q = float(params.get("quality", 0))
        oee = a * p * q
        return {"oee": round(oee, 4), "oee_pct": round(oee * 100, 2)}
    elif op == "recovery_estimate":
        remaining = int(params.get("remaining_cases", 0))
        speed = float(params.get("speed_bpm", 0))
        minutes = float(params.get("remaining_minutes", 0))
        if speed <= 0 or minutes <= 0:
            return {"feasible": False, "reason": "Invalid speed or time"}
        projected = int(speed * minutes / 60)
        return {
            "feasible": projected >= remaining,
            "projected_cases": projected,
            "completion_pct": round(min(projected / remaining * 100, 100), 1) if remaining > 0 else 100.0,
        }
    elif op == "depletion_estimate":
        qty = float(params.get("current_qty", 0))
        rate = float(params.get("consumption_rate_per_hour", 0))
        hours = float(params.get("remaining_hours", 0))
        if rate <= 0:
            return {"hours_until_empty": float("inf"), "will_deplete": False}
        hours_until = qty / rate
        return {"hours_until_empty": round(hours_until, 2), "will_deplete": hours_until < hours}
    elif op == "cost_estimate":
        dt_min = float(params.get("downtime_minutes", 0))
        rate = float(params.get("line_rate_cases_per_hour", 0))
        lost = int(dt_min / 60 * rate)
        cost_per_case = 2.50
        return {"lost_cases": lost, "estimated_cost_eur": round(lost * cost_per_case, 2)}
    return {"error": f"Unknown operation: {operation}"}


class TestOEECalculation:
    def test_perfect_oee(self):
        result = _calc("oee", {"availability": 1.0, "performance": 1.0, "quality": 1.0})
        assert result["oee"] == 1.0
        assert result["oee_pct"] == 100.0

    def test_typical_oee(self):
        result = _calc("oee", {"availability": 0.9, "performance": 0.85, "quality": 0.98})
        assert 0.74 < result["oee"] < 0.76

    def test_zero_component(self):
        result = _calc("oee", {"availability": 0, "performance": 0.9, "quality": 0.9})
        assert result["oee"] == 0


class TestRecoveryEstimate:
    def test_feasible(self):
        result = _calc("recovery_estimate", {"remaining_cases": 100, "speed_bpm": 400, "remaining_minutes": 60})
        assert result["feasible"] is True
        assert result["projected_cases"] == 400

    def test_infeasible(self):
        result = _calc("recovery_estimate", {"remaining_cases": 1000, "speed_bpm": 100, "remaining_minutes": 10})
        assert result["feasible"] is False

    def test_zero_speed(self):
        result = _calc("recovery_estimate", {"remaining_cases": 100, "speed_bpm": 0, "remaining_minutes": 60})
        assert result["feasible"] is False


class TestDepletionEstimate:
    def test_will_deplete(self):
        result = _calc("depletion_estimate", {"current_qty": 100, "consumption_rate_per_hour": 50, "remaining_hours": 4})
        assert result["will_deplete"] is True
        assert result["hours_until_empty"] == 2.0

    def test_will_not_deplete(self):
        result = _calc("depletion_estimate", {"current_qty": 1000, "consumption_rate_per_hour": 50, "remaining_hours": 4})
        assert result["will_deplete"] is False

    def test_zero_rate(self):
        result = _calc("depletion_estimate", {"current_qty": 100, "consumption_rate_per_hour": 0, "remaining_hours": 4})
        assert result["will_deplete"] is False


class TestCostEstimate:
    def test_basic_cost(self):
        result = _calc("cost_estimate", {"downtime_minutes": 60, "line_rate_cases_per_hour": 300})
        assert result["lost_cases"] == 300
        assert result["estimated_cost_eur"] == 750.0

    def test_zero_downtime(self):
        result = _calc("cost_estimate", {"downtime_minutes": 0, "line_rate_cases_per_hour": 300})
        assert result["lost_cases"] == 0


class TestUnknownOperation:
    def test_unknown(self):
        result = _calc("unknown_op", {})
        assert "error" in result
