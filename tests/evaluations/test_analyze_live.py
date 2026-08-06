"""The tool-result classifier decides what the evaluation reports.

Every tool in this agent catches its own failures and returns them as content,
so a span status of OK says nothing about whether the tool worked. If this
classifier is wrong, a run full of missing tables reads as a clean sweep —
which is exactly the reading that hid the problem before.
"""
from __future__ import annotations

import json

import pytest

from evaluations.analyze_live import _latency_by_specialist, classify


def _tool_message(content) -> str:
    """The LangChain ToolMessage envelope a TOOL span records as its output."""
    return json.dumps({
        "additional_kwargs": {},
        "content": content if isinstance(content, (list, str)) else json.dumps(content),
        "name": "some_tool",
        "status": "success",
        "tool_call_id": "toolu_x",
        "type": "tool",
    })


class TestFailuresHidingInSuccessfulSpans:
    def test_sql_against_a_missing_table_is_an_error(self):
        out = json.dumps([{"error": "[TABLE_OR_VIEW_NOT_FOUND] The table or view "
                                    "`machine_telemetry` cannot be found."}])
        verdict, detail = classify(out)
        assert verdict == "error"
        assert "machine_telemetry" in detail

    def test_an_error_key_inside_a_success_envelope_is_an_error(self):
        out = _tool_message({"anomalies": [], "error": "No telemetry data found",
                             "total_points": 0})
        verdict, detail = classify(out)
        assert verdict == "error"
        assert detail == "No telemetry data found"

    def test_permission_denied_anywhere_in_the_payload_is_an_error(self):
        verdict, _ = classify(_tool_message({"rows": [], "note": "PERMISSION_DENIED on table"}))
        assert verdict == "error"


class TestEmptyIsNotError:
    """Empty can be the right answer, so it must not be counted as a failure."""

    @pytest.mark.parametrize("payload", ["[]", _tool_message([]), _tool_message({"anomalies": []})])
    def test_no_results_reads_as_empty(self, payload):
        assert classify(payload)[0] == "empty"

    def test_none_reads_as_empty(self):
        assert classify(None)[0] == "empty"


class TestRealResults:
    def test_computed_values_read_as_ok(self):
        out = _tool_message({"lost_cases": 3377, "estimated_cost_eur": 8442.5})
        assert classify(out)[0] == "ok"

    def test_rows_read_as_ok(self):
        assert classify('[{"oee": 0.71, "machine": "FILLER"}]')[0] == "ok"

    def test_partly_empty_is_ok_when_something_came_back(self):
        """An empty plan beside real constraints is degraded, not a failure."""
        out = _tool_message({"plan": {}, "constraints": [{"constraint_id": "C001"}]})
        assert classify(out)[0] == "ok"

    def test_error_rate_fields_are_not_mistaken_for_errors(self):
        """A column named like a failure is not one — the marker list is narrow."""
        out = _tool_message({"error_rate": 0.02, "checks": 480})
        assert classify(out)[0] == "ok"


class TestLatencyAttribution:
    """Which lever to pull on a slow specialist depends on this split."""

    def _q(self, actual, total, llm, tool, llm_calls=3, tool_calls=2, error=None):
        return {
            "actual": actual, "latency_ms": total, "llm_ms": llm, "tool_ms": tool,
            "llm_calls": llm_calls, "tool_calls": tool_calls, "error": error,
        }

    def test_a_model_bound_specialist_is_distinguishable_from_a_tool_bound_one(self):
        rows = [
            self._q("strategic_advisor", 100_000, 90_000, 8_000),
            self._q("strategic_advisor", 120_000, 110_000, 8_000),
            self._q("supply_chain_advisor", 80_000, 20_000, 58_000),
        ]
        out = _latency_by_specialist(rows)
        assert out["strategic_advisor"]["llm_share"] > 0.85
        assert out["supply_chain_advisor"]["llm_share"] < 0.3

    def test_timed_out_questions_do_not_skew_the_medians(self):
        """A 180s timeout has no meaningful span breakdown behind it."""
        rows = [
            self._q("oee_coach", 20_000, 10_000, 8_000),
            self._q("oee_coach", 180_000, 0, 0, error="HTTP 502"),
        ]
        out = _latency_by_specialist(rows)
        assert out["oee_coach"]["n"] == 1
        assert out["oee_coach"]["p50_total_ms"] == 20_000

    def test_slowest_specialist_is_reported_first(self):
        rows = [
            self._q("fast", 10_000, 5_000, 4_000),
            self._q("slow", 90_000, 80_000, 5_000),
        ]
        assert list(_latency_by_specialist(rows)) == ["slow", "fast"]


class TestTheFailuresThatUsedToScoreHealthy:
    """Payloads observed in real traces that the classifier once called ``ok``.

    Each of these was present during a 99-question run that reported a 100%
    tool success rate. The tools had caught their own exceptions and returned
    ``status: success`` with the failure in the body, so only the payload can
    tell the difference.
    """

    @pytest.mark.parametrize(
        "payload,expected",
        [
            # Spark names a bad column UNRESOLVED_COLUMN, never NOT_FOUND, so
            # every wrong column in the codebase read as a healthy call.
            ({"status": "success", "result": "UNRESOLVED_COLUMN foo"}, "error"),
            ({"status": "success", "result": "DIVIDE_BY_ZERO"}, "error"),
            # The error key was only read on the first row, and only under
            # "error" — this shape reported success.
            ([{"message": "TABLE_OR_VIEW_NOT_FOUND"}], "error"),
            ([{"ok": 1}, {"detail": "AnalysisException: bad plan"}], "error"),
            ("Request timed out after 60s", "error"),
            # Ran fine, found nothing: not a crash, but not evidence either.
            ({"status": "success", "message": "No telemetry data found", "rows": []}, "empty"),
            # Shallow len() checks saw one key and called it populated.
            ({"data": {"items": []}, "count": 0}, "empty"),
            ({"status": "success"}, "empty"),
        ],
    )
    def test_the_payload_decides_not_the_span(self, payload, expected):
        assert classify(payload)[0] == expected

    @pytest.mark.parametrize(
        "payload",
        [
            {"error_rate": 0.02, "checks": 41},   # "error" as a metric name
            {"rows": [{"a": 1}]},
            [{"oee": 82.1}],
            {"status": "success", "invalid_units": 0, "total": 500},
        ],
    )
    def test_real_results_are_not_scored_as_failures(self, payload):
        """The markers are narrow on purpose: 'error_rate' is a legitimate column."""
        assert classify(payload)[0] == "ok"
