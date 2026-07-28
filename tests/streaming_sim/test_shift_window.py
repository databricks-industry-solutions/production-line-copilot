"""Unit tests for the producer's per-shift ``cumulative_cases`` window logic.

The producer resets ``cumulative_cases`` at fixed 8h UTC shift boundaries
(00:00 / 08:00 / 16:00) so the Cases tile stays a believable per-shift count
instead of an unbounded lifetime total (the "6233% of plan" bug).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.streaming_sim.producer import (
    SHIFT_WINDOW_MINUTES,
    _shift_window_start,
)


@pytest.mark.parametrize(
    "hour,expected_start_hour",
    [
        (0, 0), (3, 0), (7, 0),      # first shift
        (8, 8), (11, 8), (15, 8),    # second shift
        (16, 16), (20, 16), (23, 16),  # third shift
    ],
)
def test_window_start_aligns_to_8h_boundaries(hour, expected_start_hour):
    now = datetime(2026, 7, 28, hour, 37, 12, tzinfo=timezone.utc)
    start = _shift_window_start(now)
    assert start == datetime(2026, 7, 28, expected_start_hour, 0, 0, tzinfo=timezone.utc)
    assert start.tzinfo is timezone.utc


def test_window_length_matches_planned_cases_basis():
    """480 min at 17.5 cases/min = 8,400 = production_schedule.planned_cases."""
    assert SHIFT_WINDOW_MINUTES == 480
    assert SHIFT_WINDOW_MINUTES * 17.5 == 8400


def test_non_utc_input_is_normalised_before_bucketing():
    """A tz-aware non-UTC timestamp buckets by its UTC hour."""
    plus2 = timezone(timedelta(hours=2))
    now = datetime(2026, 7, 28, 10, 30, tzinfo=plus2)  # 08:30 UTC → second shift
    assert _shift_window_start(now) == datetime(2026, 7, 28, 8, 0, tzinfo=timezone.utc)


def test_rollover_detected_across_boundary():
    """Same-window ticks share a start; crossing a boundary changes it."""
    a = _shift_window_start(datetime(2026, 7, 28, 15, 59, tzinfo=timezone.utc))
    b = _shift_window_start(datetime(2026, 7, 28, 16, 1, tzinfo=timezone.utc))
    assert a != b  # 08:00 window → 16:00 window: producer resets the tally here
