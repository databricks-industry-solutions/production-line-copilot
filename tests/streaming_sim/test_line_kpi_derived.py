"""Tests for Tier-1 line_kpi derived metrics (OEE / A / P / Q)."""

from __future__ import annotations

from src.streaming_sim.line_kpi_derived import (
    ShiftKpiAccumulator,
    compute_tier1_line_kpi_row,
)


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


class TestShiftToDateIsNotThisTick:
    """``*_to_now`` means since the shift began, not right now.

    It used to mean right now. The instant a line stopped, the row claimed
    oee_to_now = 0.0 next to cumulative_cases = 1965 — a shift that had made
    nearly two thousand cases reporting zero OEE for that shift. The dashboard
    and the assistant then gave different answers for the same number.
    """

    @staticmethod
    def _tick(acc, *, running, starved=0, stopped=0, speed, shift="shift_1"):
        return compute_tier1_line_kpi_row(
            shift_id=shift, ts="2026-08-06T12:00:00Z",
            machines_running=running, machines_stopped=stopped,
            machines_degraded=0, machines_starved=starved,
            total_throughput=1.0, avg_speed_pct=speed,
            cumulative_cases=1965.0, accumulator=acc,
        )

    def test_a_stop_does_not_erase_a_good_shift(self):
        acc = ShiftKpiAccumulator(shift_id="shift_1")
        for _ in range(10_800):  # three hours of healthy running
            self._tick(acc, running=19, speed=98.0)
        healthy = self._tick(acc, running=19, speed=98.0)["oee_to_now"]

        for _ in range(600):  # ten minutes fully stopped
            row = self._tick(acc, running=0, starved=18, stopped=1, speed=0.0)

        assert row["oee_to_now"] > 0, "a shift that produced cases cannot have zero OEE"
        assert row["oee_to_now"] < healthy, "downtime has to cost something"

    def test_it_keeps_degrading_the_longer_the_line_is_down(self):
        acc = ShiftKpiAccumulator(shift_id="shift_1")
        for _ in range(3600):
            self._tick(acc, running=19, speed=98.0)
        readings = []
        for _ in range(3):
            for _ in range(1800):
                row = self._tick(acc, running=0, starved=18, stopped=1, speed=0.0)
            readings.append(row["oee_to_now"])
        assert readings == sorted(readings, reverse=True), readings

    def test_a_new_shift_does_not_inherit_the_last_one(self):
        acc = ShiftKpiAccumulator(shift_id="shift_1")
        for _ in range(3600):
            self._tick(acc, running=0, starved=18, stopped=1, speed=0.0)
        assert self._tick(acc, running=19, speed=98.0)["oee_to_now"] < 5

        acc.reset()
        assert self._tick(acc, running=19, speed=98.0)["oee_to_now"] > 50

    def test_a_change_of_shift_id_also_resets(self):
        """Covers a caller tracking several shifts, unlike the producer."""
        acc = ShiftKpiAccumulator(shift_id="shift_1")
        for _ in range(3600):
            self._tick(acc, running=0, starved=19, speed=0.0)
        fresh = self._tick(acc, running=19, speed=98.0, shift="shift_2")
        assert fresh["oee_to_now"] > 50
        assert acc.shift_id == "shift_2"

    def test_availability_is_the_plain_time_average(self):
        acc = ShiftKpiAccumulator(shift_id="shift_1")
        for _ in range(50):
            self._tick(acc, running=19, speed=100.0)
        for _ in range(50):
            row = self._tick(acc, running=0, starved=19, speed=0.0)
        # Half the shift at full availability (88.0), half at zero.
        assert abs(row["availability_to_now"] - 44.0) < 0.2

    def test_performance_is_averaged_over_run_time_not_planned_time(self):
        """A stop is availability's to account for, and only availability's.

        The line above ran at full speed every second it ran, so its
        performance for the shift is full speed. Averaging the zeros from the
        stop into it charges the same downtime twice — once here and once in
        availability — and the product then squares the penalty.
        """
        acc = ShiftKpiAccumulator(shift_id="shift_1")
        for _ in range(50):
            self._tick(acc, running=19, speed=100.0)
        for _ in range(50):
            row = self._tick(acc, running=0, starved=19, speed=0.0)
        assert abs(row["performance_to_now"] - 95.0) < 0.2

    def test_the_factors_multiply_back_to_what_the_line_actually_did(self):
        """A x P must equal the time-average of instantaneous A x P.

        This identity is the whole reason the split is safe to read. Without
        the run-time weighting it does not hold, and the tile understates the
        shift by more the longer the line was down — six points on a real dev
        shift, sixteen at the worst moment of it.
        """
        acc = ShiftKpiAccumulator(shift_id="shift_1")
        instantaneous = []
        for i in range(400):
            down = i % 5 == 0  # a stop every fifth tick
            speed = 0.0 if down else 90.0
            row = self._tick(
                acc,
                running=0 if down else 19,
                starved=19 if down else 0,
                speed=speed,
            )
            a_now = 0.0 if down else 88.0
            instantaneous.append(a_now / 100.0 * (speed * 0.95) / 100.0)

        combined = row["availability_to_now"] / 100.0 * row["performance_to_now"] / 100.0
        assert abs(combined - sum(instantaneous) / len(instantaneous)) < 0.005

    def test_a_shift_that_has_not_started_reports_no_rate(self):
        acc = ShiftKpiAccumulator(shift_id="shift_1")
        row = self._tick(acc, running=0, starved=19, speed=0.0)
        assert row["availability_to_now"] == 0.0
        assert row["performance_to_now"] == 0.0
        assert row["oee_to_now"] == 0.0

    def test_a_slow_shift_is_still_charged_for_being_slow(self):
        """Weighting must not launder a genuine speed loss into a clean score."""
        acc = ShiftKpiAccumulator(shift_id="shift_1")
        for _ in range(200):
            row = self._tick(acc, running=19, speed=50.0)
        assert abs(row["performance_to_now"] - 47.5) < 0.2
        assert row["availability_to_now"] == 88.0

    def test_without_an_accumulator_the_values_stay_instantaneous(self):
        """A caller aggregating a snapshot has no shift history to draw on."""
        row = compute_tier1_line_kpi_row(
            shift_id="shift_1", ts="t", machines_running=0, machines_stopped=1,
            machines_degraded=0, machines_starved=18, total_throughput=1.2,
            avg_speed_pct=0.0,
        )
        assert row["oee_to_now"] == 0.0

    def test_quality_is_unaffected(self):
        acc = ShiftKpiAccumulator(shift_id="shift_1")
        row = self._tick(acc, running=19, speed=98.0)
        assert row["quality_to_now"] == 98.5
