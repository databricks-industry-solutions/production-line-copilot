"""The line makes what leaves the last station, not the sum of every station.

A packaging line is a series: one bottle passes through all nineteen machines.
Adding their per-minute rates counts that bottle nineteen times. The tile read
5,680/min on a line actually running at ~300, next to a Cases tile that made
the reading look like cases — and the column is documented to Genie as
"aggregate cases per minute", which it was out by a factor of roughly 450.

Nothing asserted the old behaviour, which is why it shipped.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.streaming_sim.cascade import (
    MachineSpec,
    apply_fault,
    build_line,
    cascade_tick,
    compute_line_kpi,
    snapshot_to_dicts,
)

TS = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)


def _line(n: int = 19, capacity: float = 300.0):
    specs = [
        MachineSpec(id=f"M{i:02d}", line_position=i, nominal_capacity=capacity)
        for i in range(1, n + 1)
    ]
    return specs, build_line(specs)


def _kpi(state, **kw):
    return compute_line_kpi(state, "shift_1", TS, **kw)


class TestItReportsTheLineNotTheStations:
    def test_a_balanced_line_reports_one_station_worth(self):
        specs, state = _line()
        for _ in range(30):
            cascade_tick(specs, state, tick_seconds=1.0)
        tput = _kpi(state)["total_throughput"]
        assert 250.0 < tput < 320.0, (
            f"a line of 19 machines at ~300/min makes ~300/min, not {tput}"
        )

    def test_adding_machines_does_not_add_throughput(self):
        """The bug scaled with line length. A correct line does not."""
        short_specs, short = _line(3)
        long_specs, long = _line(19)
        for _ in range(30):
            cascade_tick(short_specs, short, tick_seconds=1.0)
            cascade_tick(long_specs, long, tick_seconds=1.0)
        assert abs(_kpi(short)["total_throughput"] - _kpi(long)["total_throughput"]) < 30.0

    def test_it_matches_the_rate_of_the_final_station(self):
        specs, state = _line()
        for _ in range(30):
            cascade_tick(specs, state, tick_seconds=1.0)
        last = list(state.machines)[-1]
        assert abs(_kpi(state)["total_throughput"] - state.machines[last].throughput) < 0.01

    def test_the_jittered_rows_agree_with_the_state(self):
        """Both branches of compute_line_kpi must mean the same thing."""
        specs, state = _line()
        for _ in range(30):
            cascade_tick(specs, state, tick_seconds=1.0)
        rows = snapshot_to_dicts(state, "shift_1", TS)
        assert abs(_kpi(state)["total_throughput"] - _kpi(state, machine_rows=rows)["total_throughput"]) < 1.0


class TestItStillMovesWhenTheLineDoes:
    def test_stopping_the_end_of_the_line_stops_the_output(self):
        specs, state = _line()
        for _ in range(30):
            cascade_tick(specs, state, tick_seconds=1.0)
        assert _kpi(state)["total_throughput"] > 100.0

        apply_fault(state, list(state.machines)[-1], "stopped", speed_pct=0.0)
        for _ in range(10):
            cascade_tick(specs, state, tick_seconds=1.0)
        assert _kpi(state)["total_throughput"] == 0.0

    def test_a_bottleneck_upstream_throttles_the_output(self):
        """Buffers absorb a slowdown for a while, then the end of the line feels it."""
        specs, state = _line()
        for _ in range(30):
            cascade_tick(specs, state, tick_seconds=1.0)
        healthy = _kpi(state)["total_throughput"]

        apply_fault(state, "M05", "degraded", speed_pct=30.0)
        for _ in range(600):
            cascade_tick(specs, state, tick_seconds=1.0)
        assert _kpi(state)["total_throughput"] < healthy


class TestFindingTheEndOfTheLine:
    def test_it_does_not_depend_on_dict_insertion_order(self):
        specs = [
            MachineSpec(id="C", line_position=3, nominal_capacity=100.0),
            MachineSpec(id="A", line_position=1, nominal_capacity=100.0),
            MachineSpec(id="B", line_position=2, nominal_capacity=100.0),
        ]
        state = build_line(specs)
        for _ in range(30):
            cascade_tick(specs, state, tick_seconds=1.0)
        assert abs(_kpi(state)["total_throughput"] - state.machines["C"].throughput) < 0.01

    def test_a_single_machine_line_reports_that_machine(self):
        specs = [MachineSpec(id="ONLY", line_position=1, nominal_capacity=100.0)]
        state = build_line(specs)
        for _ in range(10):
            cascade_tick(specs, state, tick_seconds=1.0)
        assert abs(_kpi(state)["total_throughput"] - state.machines["ONLY"].throughput) < 0.01
