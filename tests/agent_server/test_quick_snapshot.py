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


class TestNullsFromTheLiveTables:
    """A NULL column must not take down the whole answer.

    Both live readers do ``SELECT *``, so a nullable column arrives as a
    present key holding ``None`` — which is why the ``.get(key, 0)`` defaults
    here and the ``setdefault`` in ``_normalize_line_kpi_row`` both looked like
    they covered this and neither did. ``float(None)`` raised ``TypeError``
    out of ``_quick_snapshot``, which is called unguarded on the request path,
    so one NULL in one column returned a raw Python error to the user instead
    of an answer.

    The existing tests above never caught it because they build fully
    populated KPI dicts, which is exactly the shape the bug is not.
    """

    def test_a_null_kpi_metric_does_not_raise(self):
        ctx = _ctx(4200)
        ctx["current_kpi"]["availability_to_now"] = None
        assert "OEE:" in _quick_snapshot(ctx)

    def test_a_null_metric_reads_as_zero_not_none(self):
        ctx = _ctx(4200)
        ctx["current_kpi"]["performance_to_now"] = None
        out = _quick_snapshot(ctx)
        assert "P: 0.0%" in out
        assert "None" not in out

    def test_every_kpi_metric_null_at_once(self):
        ctx = _ctx(4200)
        for key in ("availability_to_now", "performance_to_now", "quality_to_now"):
            ctx["current_kpi"][key] = None
        assert "None" not in _quick_snapshot(ctx)

    def test_a_null_oee_skips_the_line_rather_than_rendering_none(self):
        # oee gates the whole line, and None means "no reading", not "zero".
        ctx = _ctx(4200)
        ctx["current_kpi"]["oee_to_now"] = None
        out = _quick_snapshot(ctx)
        assert "OEE:" not in out
        assert "None" not in out

    def test_a_null_event_description_does_not_raise(self):
        # `.get('description', '')[:80]` on a present-but-null column was
        # `None[:80]` — TypeError: 'NoneType' object is not subscriptable.
        ctx = _ctx(4200)
        ctx["active_events"] = [
            {"machine_id": "FILLER-01", "event_type": "breakdown",
             "severity": "high", "description": None},
        ]
        out = _quick_snapshot(ctx)
        assert "FILLER-01" in out
        assert "None" not in out
