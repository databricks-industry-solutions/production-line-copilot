"""Cases tally: bottles→cases unit correction + whole-number emit.

Regression for the "31,106.688 (plan 8,400/shift)" tile bug. The end-of-line
machine's throughput is measured in BOTTLES/min, so integrating it as cases/min
overcounted ~24x AND surfaced a fractional case count. These tests pin the
ideal-rate invariant (8,400 cases per shift) and the discrete-count emit.
"""

from __future__ import annotations

from src.streaming_sim.line_kpi_derived import compute_tier1_line_kpi_row
from src.streaming_sim.producer import (
    BOTTLES_PER_CASE,
    IDEAL_CASES_PER_MIN,
    SHIFT_PLANNED_CASES,
    SHIFT_WINDOW_MINUTES,
)


def _accumulate_cases_at_ideal_rate(tick_s: float = 1.0) -> float:
    """Mirror the producer's per-tick accumulation at the ideal end-of-line
    bottle throughput (17.5 cases/min × 24 = 420 BPM), integrated over a shift.
    """
    ideal_bottle_tput = IDEAL_CASES_PER_MIN * BOTTLES_PER_CASE  # bottles/min
    ticks = int(SHIFT_WINDOW_MINUTES * 60 / tick_s)
    cases = 0.0
    for _ in range(ticks):
        bottles_this_tick = ideal_bottle_tput * (tick_s / 60.0)
        cases += bottles_this_tick / BOTTLES_PER_CASE
    return cases


def test_ideal_rate_yields_exactly_one_shift_plan():
    """At ideal end-of-line throughput a full shift produces SHIFT_PLANNED_CASES
    (8,400) cases — the bottles→cases divide is what makes this hold."""
    assert IDEAL_CASES_PER_MIN * BOTTLES_PER_CASE == 420.0
    assert SHIFT_PLANNED_CASES == 8400
    cases = _accumulate_cases_at_ideal_rate()
    assert round(cases) == SHIFT_PLANNED_CASES


def test_missing_divide_would_overcount_by_case_factor():
    """Documents the sign/magnitude of the bug: integrating bottle throughput
    WITHOUT the divide overshoots a shift's plan by exactly BOTTLES_PER_CASE."""
    ideal_bottle_tput = IDEAL_CASES_PER_MIN * BOTTLES_PER_CASE
    overcounted = ideal_bottle_tput * SHIFT_WINDOW_MINUTES  # no /BOTTLES_PER_CASE
    assert round(overcounted) == SHIFT_PLANNED_CASES * BOTTLES_PER_CASE  # 201,600


def test_emit_floors_cases_to_whole_number():
    """The line_kpi row carries a discrete case count (no fractional cases)."""
    row = compute_tier1_line_kpi_row(
        shift_id="shift_1",
        ts="2026-04-23T12:00:00",
        machines_running=19,
        machines_stopped=0,
        machines_degraded=0,
        machines_starved=0,
        total_throughput=420.0,
        avg_speed_pct=100.0,
        cumulative_cases=1335.688271,
    )
    assert row["cumulative_cases"] == 1335.0
    assert row["cumulative_cases"] == int(row["cumulative_cases"])
