"""Tests for Genie SQL provenance — the tables behind an answer.

Covers:
  * extract_tables() SQL provenance parsing (pure function).
  * genie_query returning a `tables` field alongside answer/sql/data.

Provenance is what lets the chat answer say which tables a number came from
(see tool_evidence.build_evidence), so it is worth pinning even though the
parsing looks trivial.

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
        from agent_server.tools.genie_query import genie_query
        from tests.agent_server.test_agent_tools import _FakeRequests, _invoke  # reuse fakes

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
