"""Tests for the first-class "ask the line" Genie data path (Theme 3b).

Covers:
  * extract_tables() SQL provenance parsing (pure function).
  * genie_query returning a `tables` field alongside answer/sql/data.
  * agent.ask_line() shaping the run_genie_query result (+ error passthrough).

All external REST is mocked; no workspace connection required.
"""
from __future__ import annotations

import pytest

pytest.importorskip("mlflow")

from agent_server.tools import genie_query as gq  # noqa: E402
from agent_server.tools.genie_query import extract_tables  # noqa: E402


class TestExtractTables:
    def test_simple_from(self):
        assert extract_tables("SELECT * FROM main.streaming.line_kpi") == ["main.streaming.line_kpi"]

    def test_from_and_join_dedup_ordered(self):
        sql = (
            "SELECT a.x FROM cat.sch.machine_state a "
            "JOIN cat.sch.event_log b ON a.id = b.id "
            "JOIN cat.sch.machine_state c ON a.id = c.id"
        )
        assert extract_tables(sql) == ["cat.sch.machine_state", "cat.sch.event_log"]

    def test_backtick_quoted(self):
        assert extract_tables("SELECT 1 FROM `main.streaming.line_kpi`") == ["main.streaming.line_kpi"]

    def test_none_and_no_from(self):
        assert extract_tables(None) == []
        assert extract_tables("SELECT 1") == []


class TestGenieTablesProvenance:
    def test_happy_path_includes_tables(self, monkeypatch):
        from tests.agent_server.test_agent_tools import _FakeRequests, _invoke  # reuse fakes
        from agent_server.tools.genie_query import genie_query

        monkeypatch.setattr(gq, "_SPACE_ID", "sp1")
        monkeypatch.setattr(gq, "_make_headers",
                            lambda: ({"Content-Type": "application/json"}, "http://host"))
        monkeypatch.setattr(gq, "_poll_message", lambda *a, **k: {
            "status": "COMPLETED",
            "attachments": [{
                "text": {"content": "OEE is trending up"},
                "query": {"query": "SELECT oee FROM main.streaming.line_kpi"},
                "attachment_id": "att1",
            }],
        })
        monkeypatch.setattr(gq, "requests", _FakeRequests())
        r = _invoke(genie_query, question="What is the OEE trend?")
        assert r["tables"] == ["main.streaming.line_kpi"]


class TestAskLine:
    def test_shapes_success(self, monkeypatch):
        monkeypatch.setattr(
            gq, "run_genie_query",
            lambda q: {"answer": "up", "sql": "SELECT 1 FROM t", "tables": ["t"], "data": [{"a": 1}]},
        )
        from agent_server.agent import ask_line
        out = ask_line("What is the OEE trend over the last 5 shifts?")
        assert out == {"answer": "up", "sql": "SELECT 1 FROM t", "tables": ["t"], "data": [{"a": 1}]}

    def test_error_passthrough(self, monkeypatch):
        monkeypatch.setattr(gq, "run_genie_query", lambda q: {"error": "Genie not configured"})
        from agent_server.agent import ask_line
        out = ask_line("some analytical question")
        assert out["error"] == "Genie not configured"
        assert out["answer"] == "" and out["sql"] is None and out["tables"] == [] and out["data"] == []
