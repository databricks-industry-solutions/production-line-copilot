"""Tests for OT display metric enrichment (sim / SSE)."""

from __future__ import annotations

from src.streaming_sim.ot_display_metrics import enrich_machine_state_row


def _row(status: str, throughput: float, speed_pct: float, alarms: int) -> dict:
    return {
        "machine_id": "FILLER",
        "status": status,
        "throughput_per_min": throughput,
        "speed_pct": speed_pct,
        "alarm_count": alarms,
    }


def test_filler_returns_three_fields():
    r = enrich_machine_state_row("FILLER", _row("running", 400.0, 95.0, 0))
    assert r["primary_sensor_1"] is not None
    assert r["material_level_pct"] is not None


def test_conveyor_belt_scales_with_speed():
    r = enrich_machine_state_row(
        "CONVEYOR_1",
        {"machine_id": "CONVEYOR_1", "status": "running", "throughput_per_min": 100.0, "speed_pct": 50.0, "alarm_count": 0},
    )
    assert r["primary_sensor_1"] == 12.5  # 25 * 0.5
    assert r["primary_sensor_2"] is not None
