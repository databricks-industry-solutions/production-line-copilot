"""Two ways a healthy machine reported as if it were not running.

The Lakebase sink read ``throughput``, but the snapshot it is handed emits
``throughput_per_min``. ``dict.get`` with a default does not fail, so every
machine row written to the serving layer carried 0.0 while the same tick
reached bronze at ~300/min. The UI and every specialist read the serving
layer, so all nineteen machines looked like they were producing nothing.

Conveyors carry product rather than process it, so they are given a sentinel
capacity to keep them from bottlenecking. That sentinel reached ``speed_pct``
as flow/9999 and a healthy belt reported 2 %.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.streaming_sim.cascade import (
    MachineSpec,
    apply_fault,
    build_line,
    cascade_tick,
    compute_line_kpi,
    snapshot_to_dicts,
)
from src.streaming_sim.lakebase_sink import _throughput

TS = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)


class TestTheSinkReadsTheKeyTheProducerWrites:
    def test_it_reads_the_name_the_snapshot_emits(self):
        assert _throughput({"throughput_per_min": 296.8}) == pytest.approx(296.8)

    def test_it_still_reads_the_cascade_field_name(self):
        assert _throughput({"throughput": 301.2}) == pytest.approx(301.2)

    def test_a_row_with_neither_is_zero_not_a_crash(self):
        assert _throughput({"machine_id": "FILLER"}) == 0.0

    def test_an_explicit_zero_survives(self):
        """A stopped machine really does make nothing."""
        assert _throughput({"throughput_per_min": 0.0}) == 0.0

    def test_a_null_falls_through_to_the_other_name(self):
        assert _throughput({"throughput_per_min": None, "throughput": 12.5}) == pytest.approx(12.5)

    def test_a_real_snapshot_row_survives_the_sink(self):
        """The end-to-end shape: what the producer emits, the sink can read."""
        specs = [
            MachineSpec(id=f"M{i}", line_position=i, nominal_capacity=300.0)
            for i in range(1, 4)
        ]
        state = build_line(specs)
        for _ in range(20):
            cascade_tick(specs, state, tick_seconds=1.0)
        rows = snapshot_to_dicts(state, "shift_1", TS)
        assert all(_throughput(r) > 0 for r in rows), (
            "a running line must not reach the serving layer as zeros"
        )


class TestAConveyorIsNotNearlyStopped:
    def _line(self):
        specs = [
            MachineSpec(id="FILLER", line_position=1, nominal_capacity=420.0),
            MachineSpec(id="CONVEYOR_1", line_position=2, nominal_capacity=9999.0,
                        pass_through=True),
            MachineSpec(id="CAPPER", line_position=3, nominal_capacity=420.0),
        ]
        return specs, build_line(specs)

    def test_a_running_belt_does_not_report_two_percent(self):
        specs, state = self._line()
        for _ in range(30):
            cascade_tick(specs, state, tick_seconds=1.0)
        assert state.machines["CONVEYOR_1"].speed_pct > 90.0

    def test_a_processing_machine_still_reports_its_real_rate(self):
        """The exemption must not spread to machines that do have a rate."""
        specs, state = self._line()
        apply_fault(state, "FILLER", "degraded", speed_pct=40.0)
        for _ in range(120):  # long enough for the buffers either side to drain
            cascade_tick(specs, state, tick_seconds=1.0)
        assert state.machines["CAPPER"].speed_pct < 90.0

    def test_a_stopped_belt_still_reads_stopped(self):
        specs, state = self._line()
        apply_fault(state, "CONVEYOR_1", "stopped", speed_pct=0.0)
        for _ in range(10):
            cascade_tick(specs, state, tick_seconds=1.0)
        assert state.machines["CONVEYOR_1"].speed_pct == 0.0
        assert state.machines["CONVEYOR_1"].status == "stopped"

    def test_a_faulted_belt_keeps_the_speed_the_fault_gave_it(self):
        specs, state = self._line()
        apply_fault(state, "CONVEYOR_1", "degraded", speed_pct=55.0)
        for _ in range(10):
            cascade_tick(specs, state, tick_seconds=1.0)
        assert state.machines["CONVEYOR_1"].speed_pct == pytest.approx(55.0)

    def test_the_line_average_is_unchanged_by_the_fix(self):
        """Conveyors were already excluded, so the headline must not move."""
        specs, state = self._line()
        for _ in range(30):
            cascade_tick(specs, state, tick_seconds=1.0)
        kpi = compute_line_kpi(state, "shift_1", TS, exclude_from_speed={"CONVEYOR_1"})
        both = (state.machines["FILLER"].speed_pct + state.machines["CAPPER"].speed_pct) / 2
        assert kpi["avg_speed_pct"] == pytest.approx(both, abs=0.1)


class TestABufferIsSizedOnWhatFlowsThroughIt:
    """The sentinel must not size the buffers either.

    ``build_line`` takes the faster neighbour, which included the conveyor's
    9999. A buffer beside a conveyor came out at 833 units where its real
    neighbours justify about 25, so a fault took roughly twenty-four times
    longer to cross a conveyor than any other pair.
    """

    def _specs(self):
        return [
            MachineSpec(id="FILLER", line_position=1, nominal_capacity=420.0),
            MachineSpec(id="CONVEYOR_1", line_position=2, nominal_capacity=9999.0,
                        pass_through=True),
            MachineSpec(id="CAPPER", line_position=3, nominal_capacity=420.0),
        ]

    def test_a_conveyor_buffer_matches_its_real_neighbours(self):
        state = build_line(self._specs(), buffer_seconds=5.0)
        expected = 420.0 * 5.0 / 60.0
        for pair, buf in state.buffers.items():
            assert buf.capacity == pytest.approx(expected), pair

    def test_it_is_not_sized_on_the_sentinel(self):
        state = build_line(self._specs(), buffer_seconds=5.0)
        assert all(b.capacity < 100.0 for b in state.buffers.values())

    def test_a_fault_crosses_a_conveyor_in_the_same_time_as_any_pair(self):
        with_belt = build_line(self._specs(), buffer_seconds=5.0)
        plain_specs = [
            MachineSpec(id="FILLER", line_position=1, nominal_capacity=420.0),
            MachineSpec(id="MIDDLE", line_position=2, nominal_capacity=420.0),
            MachineSpec(id="CAPPER", line_position=3, nominal_capacity=420.0),
        ]
        plain = build_line(plain_specs, buffer_seconds=5.0)

        def ticks_until_the_end_slows(specs, state):
            apply_fault(state, "FILLER", "stopped", speed_pct=0.0)
            for i in range(2000):
                cascade_tick(specs, state, tick_seconds=1.0)
                if state.machines["CAPPER"].speed_pct < 90.0:
                    return i
            return None

        belt = ticks_until_the_end_slows(self._specs(), with_belt)
        bare = ticks_until_the_end_slows(plain_specs, plain)
        assert belt is not None and bare is not None
        assert abs(belt - bare) <= 5, (
            f"a conveyor delayed the cascade by {belt - bare} ticks"
        )

    def test_the_real_line_has_no_outsized_buffer(self):
        from src.streaming_sim.producer import _machine_specs

        state = build_line(_machine_specs())
        assert max(b.capacity for b in state.buffers.values()) < 100.0

    def test_two_conveyors_together_still_get_a_buffer(self):
        """Neither has a real rate, so fall back rather than divide by nothing."""
        specs = [
            MachineSpec(id="C1", line_position=1, nominal_capacity=9999.0, pass_through=True),
            MachineSpec(id="C2", line_position=2, nominal_capacity=9999.0, pass_through=True),
        ]
        state = build_line(specs)
        assert state.buffers[("C1", "C2")].capacity > 0


class TestTheProducerMarksTheRightMachines:
    def test_every_conveyor_is_a_pass_through_and_nothing_else_is(self):
        from src.data_generation.config import MACHINES
        from src.streaming_sim.producer import _machine_specs

        by_id = {s.id: s for s in _machine_specs()}
        for m in MACHINES:
            assert by_id[m.id].pass_through is (m.type == "conveyor"), m.id
