"""Unit tests for optimization engines — no langchain dependency."""
from __future__ import annotations

import sys
import types

import pytest

# Stub langchain_core.tools so the optimizer modules can be imported in
# environments that don't have langchain installed. In environments that
# DO have it, fall back to the real package — installing a stub here would
# leak into later tests (test_shift_briefing.py, test_smoke_endpoints.py)
# that depend on real submodules like ``langchain_core.messages``.
try:
    import langchain_core  # noqa: F401
    import langchain_core.tools  # noqa: F401
except Exception:
    _fake_lc = types.ModuleType("langchain_core")
    _fake_tools = types.ModuleType("langchain_core.tools")
    _fake_tools.tool = lambda f: f
    _fake_lc.tools = _fake_tools
    sys.modules.setdefault("langchain_core", _fake_lc)
    sys.modules.setdefault("langchain_core.tools", _fake_tools)

from agent_server.optimizers.oee_loss_prioritizer import OEELossPrioritizer
from agent_server.optimizers.schedule_recovery import ScheduleRecoveryOptimizer

HAS_ORTOOLS = True
try:
    from ortools.linear_solver import pywraplp  # noqa: F401
except ImportError:
    HAS_ORTOOLS = False


@pytest.mark.skipif(not HAS_ORTOOLS, reason="ortools not installed")
class TestScheduleRecovery:
    def setup_method(self):
        self.optimizer = ScheduleRecoveryOptimizer()

    def test_feasible_no_overtime(self):
        result = self.optimizer.optimize(
            remaining_cases=100, remaining_minutes=120,
            min_speed_bpm=200, max_speed_bpm=450,
            current_speed_bpm=380, allow_overtime=False,
        )
        assert result["feasible"] is True
        assert result["overtime_minutes"] == 0
        assert result["projected_cases"] >= 100

    def test_infeasible(self):
        result = self.optimizer.optimize(
            remaining_cases=10000, remaining_minutes=10,
            min_speed_bpm=200, max_speed_bpm=450,
            current_speed_bpm=380, allow_overtime=False,
        )
        assert result["feasible"] is False

    def test_with_overtime(self):
        result = self.optimizer.optimize(
            remaining_cases=500, remaining_minutes=30,
            min_speed_bpm=200, max_speed_bpm=450,
            current_speed_bpm=380, allow_overtime=True, max_overtime_minutes=60,
        )
        assert result["feasible"] is True

    def test_minimum_speed_respected(self):
        result = self.optimizer.optimize(
            remaining_cases=50, remaining_minutes=480,
            min_speed_bpm=200, max_speed_bpm=450,
            current_speed_bpm=380, allow_overtime=False,
        )
        assert result["feasible"] is True
        assert result["recommended_speed_bpm"] >= 200


class TestOEELossPrioritizer:
    def setup_method(self):
        self.prioritizer = OEELossPrioritizer()

    def test_basic_pareto(self):
        shifts = [
            {"shift_id": "s1", "availability": 80, "performance": 90, "quality": 98,
             "oee": 70, "downtime_minutes": 40, "speed_loss_minutes": 20, "reject_count": 10},
            {"shift_id": "s2", "availability": 70, "performance": 85, "quality": 95,
             "oee": 56, "downtime_minutes": 80, "speed_loss_minutes": 30, "reject_count": 30},
        ]
        result = self.prioritizer.prioritize(shifts)
        assert "total_oee_gap" in result
        assert "pareto" in result
        assert len(result["pareto"]) == 3
        # OEE = A*P*Q, so individual loss contributions aren't strictly additive
        assert result["pareto"][-1]["cumulative_pct"] > 0

    def test_empty_shifts(self):
        result = self.prioritizer.prioritize([])
        assert result["total_oee_gap"] == 0
        assert result["pareto"] == []

    def test_roi_estimates(self):
        shifts = [
            {"shift_id": "s1", "availability": 60, "performance": 70, "quality": 90,
             "oee": 38, "downtime_minutes": 120, "speed_loss_minutes": 60, "reject_count": 50}
        ]
        result = self.prioritizer.prioritize(shifts)
        for cat in result["pareto"]:
            assert "roi_estimate" in cat
            assert "improvement_potential_pct" in cat["roi_estimate"]
            assert "estimated_annual_value_eur" in cat["roi_estimate"]

    def test_single_shift_perfect(self):
        shifts = [
            {"shift_id": "s1", "availability": 100, "performance": 100, "quality": 100,
             "oee": 100, "downtime_minutes": 0, "speed_loss_minutes": 0, "reject_count": 0}
        ]
        result = self.prioritizer.prioritize(shifts)
        assert result["total_oee_gap"] == 0
        for cat in result["pareto"]:
            assert cat["contribution_pct"] == 0
