"""Tests for Tier-1 line_kpi derived metrics (OEE / A / P / Q)."""

from __future__ import annotations

from src.streaming_sim.line_kpi_derived import compute_tier1_line_kpi_row


def test_healthy_line_has_nonzero_oee():
    row = compute_tier1_line_kpi_row(
        shift_id="shift_1",
        ts="2026-04-23T12:00:00",
        machines_running=18,
        machines_stopped=1,
        machines_degraded=0,
        machines_starved=0,
        total_throughput=380.0,
        avg_speed_pct=96.0,
        cumulative_cases=100.0,
    )
    assert row["oee_to_now"] > 50
    assert 0 <= row["availability_to_now"] <= 100
    assert 0 <= row["performance_to_now"] <= 100
    assert 0 <= row["quality_to_now"] <= 100
    assert "cumulative_cases" in row


def test_all_stopped_oee_near_zero():
    row = compute_tier1_line_kpi_row(
        shift_id="shift_1",
        ts="2026-04-23T12:00:00",
        machines_running=0,
        machines_stopped=19,
        machines_degraded=0,
        machines_starved=0,
        total_throughput=0.0,
        avg_speed_pct=0.0,
    )
    assert row["oee_to_now"] < 5
