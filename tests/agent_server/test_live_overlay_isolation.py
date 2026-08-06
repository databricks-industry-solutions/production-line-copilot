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


class TestTheOverlayReadsTheShiftTheCallerAsked_About:
    """The live half and the historical half must describe the same shift.

    `build_context(shift_id, …)` used the caller's argument while the overlay
    read `SIM_SHIFT_ID` from the environment, so the two could disagree: past
    events for one shift, live machine state, KPIs and active incidents for
    another. Routing reads `active_incidents`, so the specialist could be
    chosen by an incident on a shift nobody asked about.

    The HTTP path hid it — `_server_shift_id` overrides whatever the browser
    sends with the environment's id, so both halves matched by coincidence.
    `evaluations/run_eval.py` calls `invoke` directly with the shift from its
    dataset, which is how a mixed-shift context reaches the judges.
    """

    @pytest.fixture
    def asked_shift(self, monkeypatch):
        seen: list[str] = []
        from agent_server.serving import realtime_store

        monkeypatch.setattr(
            realtime_store, "read_machine_state_latest",
            lambda sid, *a, **k: (seen.append(sid), [{"machine_id": "F", "status": "running"}])[1],
        )
        for name in ("read_line_kpi_latest",):
            monkeypatch.setattr(realtime_store, name, lambda *a, **k: {"oee_to_now": 80.0})
        for name in ("read_event_log_recent", "read_inventory_latest", "read_commands_active"):
            monkeypatch.setattr(realtime_store, name, lambda *a, **k: [])
        return seen

    def test_the_argument_wins_over_the_environment(self, asked_shift, monkeypatch):
        monkeypatch.setenv("SIM_SHIFT_ID", "shift_1_dev")
        _, handed_out = _cached_bundle()
        agent._overlay_live_state(handed_out, "shift_7")
        assert asked_shift == ["shift_7"], (
            "the overlay read the environment's shift while the historical "
            "context came from the caller's — the two halves disagree"
        )

    def test_the_environment_is_the_fallback_for_callers_without_one(
        self, asked_shift, monkeypatch
    ):
        monkeypatch.setenv("SIM_SHIFT_ID", "shift_1_dev")
        _, handed_out = _cached_bundle()
        agent._overlay_live_state(handed_out)
        assert asked_shift == ["shift_1_dev"]

    def test_an_empty_shift_id_falls_back_rather_than_querying_for_nothing(
        self, asked_shift, monkeypatch
    ):
        monkeypatch.setenv("SIM_SHIFT_ID", "shift_1_dev")
        _, handed_out = _cached_bundle()
        agent._overlay_live_state(handed_out, "   ")
        assert asked_shift == ["shift_1_dev"]

    def test_both_entry_points_pass_the_shift_they_were_given(self):
        """Threading it through the function is only half the fix."""
        import inspect

        src = inspect.getsource(agent)
        assert src.count("_overlay_live_state(context, shift_id)") == 2, (
            "invoke() and invoke_stream() must both forward their shift_id"
        )
        assert "_overlay_live_state(context)\n" not in src
