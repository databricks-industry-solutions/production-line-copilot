"""Tests for the deterministic Cases-vs-plan rendering in ``_quick_snapshot``.

Guards against regressing the "6233% of plan" bug: when ``cumulative_cases``
looks like an unbounded lifetime tally (>> a single-shift plan) the snapshot
must suppress the nonsensical ratio and just report the count.
"""

from __future__ import annotations

import pytest

pytest.importorskip("mlflow")

from agent_server.agent import _quick_snapshot  # noqa: E402


def _ctx(cumulative_cases: float, planned_cases: int = 8400) -> dict:
    return {
        "scenario": {"shift_id": "shift_1_dev", "scenario_name": "Live"},
        "current_kpi": {
            "oee_to_now": 74.6,
            "availability_to_now": 88.0,
            "performance_to_now": 86.1,
            "quality_to_now": 98.5,
            "cumulative_cases": cumulative_cases,
        },
        "production_plan": {"planned_cases": planned_cases},
        "active_events": [],
        "line_state": [],
    }


def test_believable_ratio_is_rendered():
    out = _quick_snapshot(_ctx(6266))
    assert "6,266" in out
    assert "75% of plan" in out


def test_lifetime_tally_suppresses_bogus_percentage():
    out = _quick_snapshot(_ctx(523_550))
    assert "523,550" in out
    assert "% of plan" not in out  # no "6233% of plan"
    assert "plan 8,400/shift" in out


def test_boundary_just_over_150pct_is_suppressed():
    # 12,601 / 8,400 = 150.01% → treated as anomalous.
    out = _quick_snapshot(_ctx(12_601))
    assert "% of plan" not in out
