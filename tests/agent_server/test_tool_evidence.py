"""The chat's evidence trail is only worth showing if it is accurate.

These pin the two things that make it trustworthy: it reports what the tools
actually returned, and it never states a row count more precisely than the tool
can support (both tools truncate).
"""

import json

import pytest

from agent_server.tool_evidence import (
    GENIE_ROW_CAP,
    SQL_ROW_CAP,
    evidence_from_call,
    evidence_from_result,
    governed_domain,
)


class TestGovernedDomain:
    @pytest.mark.parametrize(
        "tool_name,expected",
        [
            ("governed_quality_query", "quality"),
            ("governed_asset_query", "asset"),
            ("governed_inventory_query", "inventory"),
            ("governed_production_query", "production"),
        ],
    )
    def test_domain_is_read_off_the_tool_name(self, tool_name, expected):
        assert governed_domain(tool_name) == expected

    @pytest.mark.parametrize("tool_name", ["genie_query", "sql_query", "governed_query", "calculator"])
    def test_non_governed_tools_have_no_domain(self, tool_name):
        assert governed_domain(tool_name) is None


class TestEvidenceFromCall:
    def test_sql_query_carries_the_query_the_model_wrote(self):
        evidence = evidence_from_call("sql_query", {"query": "SELECT * FROM prod.oee_history"})
        assert evidence["sql"] == "SELECT * FROM prod.oee_history"
        assert evidence["tables"] == ["prod.oee_history"]

    def test_genie_has_no_sql_at_call_time(self):
        # Genie is asked a question; its SQL exists only once it has answered.
        assert evidence_from_call("genie_query", {"question": "OEE trend?"}) == {}

    @pytest.mark.parametrize("args", [None, {}, {"query": ""}, {"query": "   "}, {"query": 42}, "notadict"])
    def test_missing_or_unusable_arguments_yield_nothing(self, args):
        assert evidence_from_call("sql_query", args) == {}

    def test_a_very_long_query_is_truncated_rather_than_streamed_whole(self):
        evidence = evidence_from_call("sql_query", {"query": "SELECT " + "x" * 5000})
        assert len(evidence["sql"]) < 5000
        assert evidence["sql"].endswith("truncated")


class TestGenieEvidence:
    def _result(self, **over):
        payload = {
            "answer": "Two NCRs are open.",
            "sql": "SELECT * FROM mvm.quality.ncr",
            "tables": ["mvm.quality.ncr"],
            "data": [{"ncr_id": 1}, {"ncr_id": 2}],
        }
        payload.update(over)
        return json.dumps(payload)

    def test_the_generated_sql_and_tables_survive(self):
        evidence = evidence_from_result("genie_query", self._result())
        assert evidence["sql"] == "SELECT * FROM mvm.quality.ncr"
        assert evidence["tables"] == ["mvm.quality.ncr"]
        assert evidence["rows"] == 2
        assert evidence["rows_capped"] is False

    def test_a_governed_tool_names_the_domain_that_answered(self):
        evidence = evidence_from_result("governed_quality_query", self._result())
        assert evidence["domain"] == "quality"

    def test_a_count_on_the_cap_is_flagged_rather_than_stated_as_the_total(self):
        rows = [{"n": i} for i in range(GENIE_ROW_CAP)]
        evidence = evidence_from_result("genie_query", self._result(data=rows))
        assert evidence["rows"] == GENIE_ROW_CAP
        assert evidence["rows_capped"] is True

    def test_zero_rows_is_reported_as_a_real_answer(self):
        # Quality tables are empty on a healthy line; that is a finding, not a gap.
        evidence = evidence_from_result("genie_query", self._result(data=[]))
        assert evidence["rows"] == 0
        assert evidence["rows_capped"] is False

    def test_a_genie_error_is_surfaced_not_swallowed(self):
        evidence = evidence_from_result(
            "governed_quality_query", json.dumps({"error": "Genie Space not configured"})
        )
        assert "not configured" in evidence["error"]
        assert evidence["domain"] == "quality"
        assert "sql" not in evidence

    def test_a_missing_sql_does_not_invent_one(self):
        evidence = evidence_from_result("genie_query", self._result(sql=None, tables=[]))
        assert "sql" not in evidence
        assert "tables" not in evidence


class TestSqlEvidence:
    def test_row_count_comes_from_the_result(self):
        rows = json.dumps([{"machine": "FILLER"}, {"machine": "CAPPER"}])
        evidence = evidence_from_result("sql_query", rows)
        assert evidence == {"rows": 2, "rows_capped": False}

    def test_hitting_the_limit_is_flagged_as_capped(self):
        rows = json.dumps([{"n": i} for i in range(SQL_ROW_CAP)])
        assert evidence_from_result("sql_query", rows)["rows_capped"] is True

    def test_the_refusal_payload_reads_as_an_error_not_one_row(self):
        rows = json.dumps([{"error": "Only SELECT queries are allowed"}])
        evidence = evidence_from_result("sql_query", rows)
        assert evidence["error"] == "Only SELECT queries are allowed"
        assert "rows" not in evidence

    def test_an_empty_result_arrives_as_a_bare_list(self):
        # LangChain keeps [] as a list rather than JSON-stringifying it, because
        # an empty sequence vacuously passes its content-block check.
        assert evidence_from_result("sql_query", []) == {"rows": 0, "rows_capped": False}


class TestRobustness:
    @pytest.mark.parametrize("content", ["not json", "", None, 42, "{bad"])
    def test_unparseable_tool_output_yields_no_evidence_rather_than_raising(self, content):
        assert evidence_from_result("genie_query", content) == {}
        assert evidence_from_result("sql_query", content) == {}

    def test_tools_without_provenance_are_ignored(self):
        assert evidence_from_result("calculator", json.dumps({"result": 4})) == {}
        assert evidence_from_call("calculator", {"expression": "2+2"}) == {}
