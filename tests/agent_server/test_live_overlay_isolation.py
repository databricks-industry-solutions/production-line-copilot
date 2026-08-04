"""The live overlay must not write into the bundle the cache is holding.

``build_context`` hands callers a ``copy.copy`` of its cached entry, which
protects top-level keys and nothing below them: ``context["scenario"]`` is the
same dict object the cache holds. ``_overlay_live_state`` used to set
``scenario_name`` and ``current_timestamp`` in place, so the first live request
stamped the cached bundle as live for the rest of its TTL.

The damage lands on the fallback path, one request later. If Lakebase then
returns no rows the overlay bails out early, on the documented promise of
leaving "the historical UC bundle untouched" — but it isn't untouched, so the
specialist gets historical UC readings labelled with a live scenario name and a
current timestamp. Silently stale beats visibly stale in no situation, and this
function's own docstring says a partial overlay is worse than a clean fallback.
"""

from __future__ import annotations

import pytest

pytest.importorskip("mlflow")

from agent_server import agent  # noqa: E402


@pytest.fixture
def live_rows(monkeypatch):
    """Make the overlay believe Lakebase is up, without touching Lakebase."""
    from agent_server.serving import realtime_store

    monkeypatch.setattr(realtime_store, "read_machine_state_latest",
                        lambda *_a, **_k: [{"machine_id": "FILLER-01", "status": "running"}])
    monkeypatch.setattr(realtime_store, "read_line_kpi_latest", lambda *_a, **_k: {"oee_to_now": 80.0})
    monkeypatch.setattr(realtime_store, "read_event_log_recent", lambda *_a, **_k: [])
    monkeypatch.setattr(realtime_store, "read_inventory_latest", lambda *_a, **_k: [])
    monkeypatch.setattr(realtime_store, "read_commands_active", lambda *_a, **_k: [])


def _cached_bundle() -> tuple[dict, dict]:
    """A bundle and the shallow copy a caller would receive, sharing `scenario`."""
    import copy

    cached = {
        "scenario": {"scenario_name": "Historical UC", "current_timestamp": "2026-01-01T00:00:00", "shift_id": "s1"},
        "line_state": [{"machine_id": "OLD"}],
    }
    return cached, copy.copy(cached)


class TestTheCachedBundleSurvives:
    def test_the_scenario_name_is_not_written_into_the_cache(self, live_rows):
        cached, handed_out = _cached_bundle()
        agent._overlay_live_state(handed_out)
        assert cached["scenario"]["scenario_name"] == "Historical UC"

    def test_the_timestamp_is_not_written_into_the_cache(self, live_rows):
        cached, handed_out = _cached_bundle()
        agent._overlay_live_state(handed_out)
        assert cached["scenario"]["current_timestamp"] == "2026-01-01T00:00:00"

    def test_the_caller_still_sees_the_live_labels(self, live_rows):
        # The isolation must not cost the overlay its actual job.
        _cached, handed_out = _cached_bundle()
        out = agent._overlay_live_state(handed_out)
        assert out["scenario"]["scenario_name"] == "Live Real-Time (Lakebase)"
        assert out["scenario"]["current_timestamp"] != "2026-01-01T00:00:00"
        assert out["line_state"][0]["machine_id"] == "FILLER-01"

    def test_unrelated_scenario_fields_are_carried_over(self, live_rows):
        _cached, handed_out = _cached_bundle()
        assert agent._overlay_live_state(handed_out)["scenario"]["shift_id"] == "s1"

    def test_a_bundle_with_no_scenario_gains_one(self, live_rows):
        out = agent._overlay_live_state({})
        assert out["scenario"]["scenario_name"] == "Live Real-Time (Lakebase)"


class TestWhenLakebaseIsEmpty:
    def test_the_bundle_is_returned_exactly_as_given(self, monkeypatch):
        """The early return's promise, which the in-place write had voided."""
        from agent_server.serving import realtime_store

        monkeypatch.setattr(realtime_store, "read_machine_state_latest", lambda *_a, **_k: [])
        cached, handed_out = _cached_bundle()
        out = agent._overlay_live_state(handed_out)
        assert out["scenario"]["scenario_name"] == "Historical UC"
        assert cached["scenario"]["scenario_name"] == "Historical UC"

    def test_a_second_request_is_not_poisoned_by_the_first(self, monkeypatch, live_rows):
        """The sequence that actually produced the wrong answer.

        Request A overlays successfully; request B, served from the same cached
        entry, finds Lakebase empty and falls back. B must see historical data
        under a historical label, not historical data under A's live label.
        """
        cached, first = _cached_bundle()
        agent._overlay_live_state(first)

        from agent_server.serving import realtime_store

        monkeypatch.setattr(realtime_store, "read_machine_state_latest", lambda *_a, **_k: [])
        import copy

        second = agent._overlay_live_state(copy.copy(cached))
        assert second["scenario"]["scenario_name"] == "Historical UC"
        assert second["line_state"] == [{"machine_id": "OLD"}]
