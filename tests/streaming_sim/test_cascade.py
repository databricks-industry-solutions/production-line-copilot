"""Tests for the cascade engine — buffer-based fault propagation and recovery."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.streaming_sim.cascade import (
    LineState,
    MachineSpec,
    apply_fault,
    build_line,
    cascade_tick,
    clear_fault,
    compute_line_kpi,
    snapshot_to_dicts,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _three_machine_specs() -> list[MachineSpec]:
    """A → B → C, each 100 items/min nominal."""
    return [
        MachineSpec(id="A", line_position=1, nominal_capacity=100.0),
        MachineSpec(id="B", line_position=2, nominal_capacity=100.0),
        MachineSpec(id="C", line_position=3, nominal_capacity=100.0),
    ]


def _three_machine_line() -> tuple[list[MachineSpec], LineState]:
    specs = _three_machine_specs()
    return specs, build_line(specs, buffer_seconds=5.0)


# ---------------------------------------------------------------------------
# build_line
# ---------------------------------------------------------------------------

class TestBuildLine:
    def test_all_machines_running(self):
        specs, state = _three_machine_line()
        assert len(state.machines) == 3
        for ms in state.machines.values():
            assert ms.status == "running"

    def test_buffers_created_between_adjacent(self):
        specs, state = _three_machine_line()
        assert ("A", "B") in state.buffers
        assert ("B", "C") in state.buffers
        assert ("A", "C") not in state.buffers

    def test_initial_buffer_level(self):
        _, state = _three_machine_line()
        buf = state.buffers[("A", "B")]
        raw_cap = 100.0 * 5.0 / 60.0  # max(cap_a, cap_b) * buffer_seconds / 60
        expected_cap = max(raw_cap, 10.0)  # min floor
        assert buf.capacity == pytest.approx(expected_cap)
        assert buf.level == pytest.approx(expected_cap * 0.8)  # 80%


# ---------------------------------------------------------------------------
# cascade_tick — healthy line
# ---------------------------------------------------------------------------

class TestHealthyLine:
    def test_all_running_stable_throughput(self):
        specs, state = _three_machine_line()
        cascade_tick(specs, state, tick_seconds=1.0)
        for ms in state.machines.values():
            assert ms.status == "running"
            assert ms.throughput > 0

    def test_multiple_ticks_stay_stable(self):
        specs, state = _three_machine_line()
        for _ in range(10):
            cascade_tick(specs, state, tick_seconds=1.0)
        for ms in state.machines.values():
            assert ms.status == "running"


# ---------------------------------------------------------------------------
# cascade_tick — fault propagation
# ---------------------------------------------------------------------------

class TestFaultPropagation:
    def test_stopped_machine_zero_throughput(self):
        specs, state = _three_machine_line()
        apply_fault(state, "B")
        cascade_tick(specs, state, tick_seconds=1.0)
        assert state.machines["B"].throughput == 0.0
        assert state.machines["B"].status == "stopped"

    def test_downstream_starves_after_buffer_drains(self):
        specs, state = _three_machine_line()
        apply_fault(state, "B")
        # Run enough ticks for buffer B→C to drain.
        for _ in range(200):
            cascade_tick(specs, state, tick_seconds=1.0)
        assert state.machines["C"].status == "starved"
        assert state.machines["C"].throughput < 1.0

    def test_upstream_blocks_when_buffer_fills(self):
        specs, state = _three_machine_line()
        apply_fault(state, "B")
        # Run enough ticks for buffer A→B to fill.
        for _ in range(200):
            cascade_tick(specs, state, tick_seconds=1.0)
        buf_ab = state.buffers[("A", "B")]
        assert buf_ab.fill_ratio() >= 0.9
        assert state.machines["A"].status in ("blocked", "degraded")

    def test_cascade_spreads_in_both_directions(self):
        """Stopping the middle machine should affect both ends eventually."""
        specs, state = _three_machine_line()
        apply_fault(state, "B")
        for _ in range(200):
            cascade_tick(specs, state, tick_seconds=1.0)
        assert state.machines["A"].status != "running"
        assert state.machines["C"].status != "running"

    def test_gradual_decline_downstream(self):
        """After upstream stops, downstream speed should decline gradually, not jump to 0."""
        specs, state = _three_machine_line()
        apply_fault(state, "A")
        speeds = []
        for _ in range(10):
            cascade_tick(specs, state, tick_seconds=1.0)
            speeds.append(state.machines["B"].speed_pct)
        # Speed should be monotonically non-increasing, not all-or-nothing
        for i in range(1, len(speeds)):
            assert speeds[i] <= speeds[i - 1] + 0.01
        # Should have partially declined but not all at once
        assert speeds[0] > 10.0, "First tick should still have buffer headroom"
        assert speeds[-1] < speeds[0], "Should be declining over time"


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------

class TestRecovery:
    def test_clear_fault_restores_running(self):
        specs, state = _three_machine_line()
        apply_fault(state, "B")
        for _ in range(50):
            cascade_tick(specs, state, tick_seconds=1.0)
        clear_fault(state, "B")
        # Run enough recovery ticks for buffers to refill
        for _ in range(300):
            cascade_tick(specs, state, tick_seconds=1.0)
        for ms in state.machines.values():
            assert ms.status == "running"
            assert ms.throughput > 50.0

    def test_buffers_valid_after_recovery(self):
        specs, state = _three_machine_line()
        apply_fault(state, "B")
        for _ in range(100):
            cascade_tick(specs, state, tick_seconds=1.0)
        clear_fault(state, "B")
        for _ in range(300):
            cascade_tick(specs, state, tick_seconds=1.0)
        for buf in state.buffers.values():
            assert 0.0 <= buf.level <= buf.capacity


# ---------------------------------------------------------------------------
# Degraded (partial fault)
# ---------------------------------------------------------------------------

class TestDegraded:
    def test_degraded_reduces_throughput(self):
        specs, state = _three_machine_line()
        apply_fault(state, "B", fault_status="degraded", speed_pct=50.0, extra_alarms=3)
        cascade_tick(specs, state, tick_seconds=1.0)
        assert state.machines["B"].status == "degraded"
        assert state.machines["B"].throughput < 100.0
        assert state.machines["B"].alarm_count == 3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class TestSnapshot:
    def test_snapshot_row_count(self):
        specs, state = _three_machine_line()
        cascade_tick(specs, state)
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        rows = snapshot_to_dicts(state, "shift_1", now)
        assert len(rows) == 3
        assert all(r["shift_id"] == "shift_1" for r in rows)
        assert all(r["ts"] == now for r in rows)

    def test_snapshot_keys(self):
        specs, state = _three_machine_line()
        cascade_tick(specs, state)
        rows = snapshot_to_dicts(state, "s1", None)
        expected_keys = {"shift_id", "machine_id", "ts", "status",
                         "throughput_per_min", "speed_pct", "alarm_count"}
        for r in rows:
            assert set(r.keys()) == expected_keys


class TestLineKpi:
    def test_healthy_line_all_running(self):
        specs, state = _three_machine_line()
        cascade_tick(specs, state)
        kpi = compute_line_kpi(state, "s1", None)
        assert kpi["machines_running"] == 3
        assert kpi["machines_stopped"] == 0
        assert kpi["total_throughput"] > 0
        assert "oee_to_now" in kpi and kpi["oee_to_now"] > 0

    def test_one_stopped(self):
        specs, state = _three_machine_line()
        apply_fault(state, "B")
        cascade_tick(specs, state)
        kpi = compute_line_kpi(state, "s1", None)
        assert kpi["machines_stopped"] == 1
        assert kpi["machines_running"] <= 2


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_single_machine_line(self):
        specs = [MachineSpec(id="ONLY", line_position=1, nominal_capacity=100.0)]
        state = build_line(specs)
        cascade_tick(specs, state)
        assert state.machines["ONLY"].status == "running"

    def test_empty_line_no_crash(self):
        state = LineState()
        cascade_tick([], state)

    def test_apply_fault_unknown_machine_no_crash(self):
        _, state = _three_machine_line()
        apply_fault(state, "NONEXISTENT")
