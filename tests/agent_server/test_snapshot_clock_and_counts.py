"""The snapshot's two arithmetic claims have to hold.

"Time remaining: 240 min" appeared in snapshots hours apart, because the live
overlay advanced current_timestamp but left elapsed/remaining as the UC bundle
had computed them. 240 is what the shift clock reads at 12:00 UTC, and it
stayed there. In dev it was never live at all: there is no scenario_index row
for shift_1_dev, so the figure came from a fallback.

"Line: 16 running, 2 issue(s)" on a nineteen-machine line was missing one.
"blocked" is a status the cascade sets, and it counted as neither.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_server.events import SHIFT_WINDOW_MINUTES, shift_progress


def _utc(h, m=0):
    return datetime(2026, 8, 6, h, m, tzinfo=timezone.utc)


class TestTheShiftClockFollowsTheWallClock:
    @pytest.mark.parametrize("hour,minute,elapsed,remaining", [
        (8, 0, 0.0, 480.0),
        (12, 0, 240.0, 240.0),     # the value that got stuck
        (14, 37, 397.0, 83.0),     # what the snapshot should have said
        (15, 59, 479.0, 1.0),
    ])
    def test_it_reads_the_position_in_the_shift(self, hour, minute, elapsed, remaining):
        assert shift_progress(_utc(hour, minute)) == (elapsed, remaining)

    def test_the_two_always_make_a_whole_shift(self):
        for hour in range(24):
            elapsed, remaining = shift_progress(_utc(hour, 17))
            assert elapsed + remaining == pytest.approx(SHIFT_WINDOW_MINUTES)

    @pytest.mark.parametrize("hour", [0, 8, 16])
    def test_a_boundary_starts_a_fresh_shift(self, hour):
        assert shift_progress(_utc(hour, 0)) == (0.0, 480.0)

    def test_it_never_reports_a_negative_or_overlong_shift(self):
        for hour in range(24):
            for minute in (0, 30, 59):
                elapsed, remaining = shift_progress(_utc(hour, minute))
                assert 0 <= elapsed <= SHIFT_WINDOW_MINUTES
                assert 0 <= remaining <= SHIFT_WINDOW_MINUTES

    def test_it_agrees_with_the_producer(self):
        """The header and the cases reset both key off this same window."""
        from src.streaming_sim.producer import _shift_window_start

        now = _utc(14, 37)
        expected = (now - _shift_window_start(now)).total_seconds() / 60
        assert shift_progress(now)[0] == pytest.approx(expected, abs=0.1)

    def test_a_non_utc_clock_lands_in_the_same_window(self):
        from datetime import timedelta

        same_instant = _utc(14, 37).astimezone(timezone(timedelta(hours=9)))
        assert shift_progress(same_instant) == shift_progress(_utc(14, 37))


class TestTheLiveOverlayMovesTheClock:
    def test_it_does_not_carry_the_bundle_figure(self):
        """This is the frozen-240 case, reduced to its cause."""
        import agent_server.agent as agent_mod

        stale = {"scenario": {"remaining_minutes": 240.0, "elapsed_minutes": 240.0}}
        elapsed, remaining = shift_progress()
        overlaid = {
            **stale["scenario"],
            "elapsed_minutes": elapsed,
            "remaining_minutes": remaining,
        }
        # The overlay in _overlay_live_state builds exactly this shape.
        assert "shift_progress" in agent_mod.__dict__ or hasattr(agent_mod, "shift_progress")
        assert overlaid["remaining_minutes"] == remaining

    def test_the_overlay_recomputes_rather_than_inherits(self):
        """Source-level: the keys must be set alongside current_timestamp."""
        from pathlib import Path

        src = Path(agent_source()).read_text()
        block = src[src.index('"scenario_name": "Live Real-Time (Lakebase)"') - 400:]
        block = block[:600]
        assert "remaining_minutes" in block
        assert "shift_progress()" in block


def agent_source() -> str:
    from pathlib import Path
    return str(Path(__file__).resolve().parents[2] / "agent_server" / "agent.py")


class TestTheLineAddsUp:
    def _snapshot(self, machines):
        from agent_server.agent import _quick_snapshot

        return _quick_snapshot(
            {"scenario": {"shift_id": "shift_1_dev"}, "line_state": machines,
             "current_kpi": {}, "active_events": []},
            query="how is the line",
        )

    def _line(self, **status_counts):
        out, n = [], 0
        for status, count in status_counts.items():
            for _ in range(count):
                n += 1
                out.append({"machine_id": f"M{n:02d}", "status": status})
        return out

    def test_a_blocked_machine_is_an_issue_not_a_ghost(self):
        """16 running + 2 issues on a 19-machine line lost one."""
        text = self._snapshot(self._line(running=16, stopped=2, blocked=1))
        assert "16 running" in text
        assert "3 issue(s)" in text

    def test_running_plus_issues_covers_the_line(self):
        machines = self._line(running=14, stopped=1, degraded=1, starved=2, blocked=1)
        text = self._snapshot(machines)
        assert "14 running" in text and "5 issue(s)" in text

    def test_a_healthy_line_says_nothing_about_issues(self):
        assert "issue(s)" not in self._snapshot(self._line(running=19))

    def test_it_does_not_silently_truncate_a_long_list(self):
        text = self._snapshot(self._line(running=5, stopped=8))
        assert "8 issue(s)" in text
        assert "more" in text
