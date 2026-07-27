"""Unit tests for agent tools that exercise the REAL modules.

Unlike ``test_calculator.py`` (which reimplements the arithmetic inline), these
tests import and call ``agent_server.tools.*`` directly so the modules are
actually covered. Anything that touches a SQL warehouse is monkeypatched.
"""
from __future__ import annotations

import sys
import types

import pytest

# Stub langchain_core.tools ONLY if langchain isn't installed, so the @tool-
# decorated modules import. When langchain IS present we use the real package
# (stubbing would leak into tests that need real langchain_core submodules).
try:
    import langchain_core  # noqa: F401
    import langchain_core.tools  # noqa: F401
except Exception:  # pragma: no cover - only in minimal envs
    _fake_lc = types.ModuleType("langchain_core")
    _fake_tools = types.ModuleType("langchain_core.tools")
    _fake_tools.tool = lambda f: f
    _fake_lc.tools = _fake_tools
    sys.modules.setdefault("langchain_core", _fake_lc)
    sys.modules.setdefault("langchain_core.tools", _fake_tools)


def _invoke(tool_obj, **kwargs):
    """Call a (possibly @tool-wrapped) callable uniformly."""
    if hasattr(tool_obj, "invoke"):
        return tool_obj.invoke(kwargs)
    return tool_obj(**kwargs)


class _FakeSQL:
    """Stand-in for the ``sql_query`` StructuredTool (which is a frozen pydantic
    model, so we can't monkeypatch its ``.invoke`` — we replace the whole name)."""

    def __init__(self, rows):
        self._rows = rows

    def invoke(self, _query):
        return self._rows


# --------------------------------------------------------------------------- #
# calculator
# --------------------------------------------------------------------------- #
from agent_server.tools.calculator import calculator


class TestCalculatorOEE:
    def test_perfect(self):
        r = _invoke(calculator, operation="oee",
                    params={"availability": 1.0, "performance": 1.0, "quality": 1.0})
        assert r["oee"] == 1.0
        assert r["oee_pct"] == 100.0

    def test_typical(self):
        r = _invoke(calculator, operation="OEE",  # case-insensitive
                    params={"availability": 0.9, "performance": 0.85, "quality": 0.98})
        assert 0.74 < r["oee"] < 0.76

    def test_non_numeric_returns_error(self):
        r = _invoke(calculator, operation="oee",
                    params={"availability": "not-a-number", "performance": 1, "quality": 1})
        assert "error" in r


class TestCalculatorRecovery:
    def test_ramp_up_reduces_effective_minutes(self):
        r = _invoke(calculator, operation="recovery_estimate",
                    params={"remaining_cases": 100, "speed_bpm": 400, "remaining_minutes": 60})
        # default ramp_up=10 -> effective 50 min -> 400*50/60 = 333
        assert r["effective_production_minutes"] == 50
        assert r["ramp_up_minutes"] == 10
        assert r["projected_cases"] == 333
        assert r["feasible"] is True

    def test_infeasible(self):
        r = _invoke(calculator, operation="recovery_estimate",
                    params={"remaining_cases": 10000, "speed_bpm": 100, "remaining_minutes": 20})
        assert r["feasible"] is False

    def test_zero_speed(self):
        r = _invoke(calculator, operation="recovery_estimate",
                    params={"remaining_cases": 100, "speed_bpm": 0, "remaining_minutes": 60})
        assert r["feasible"] is False
        assert "reason" in r


class TestCalculatorDepletion:
    def test_will_deplete(self):
        r = _invoke(calculator, operation="depletion_estimate",
                    params={"current_qty": 100, "consumption_rate_per_hour": 50, "remaining_hours": 4})
        assert r["hours_until_empty"] == 2.0
        assert r["will_deplete"] is True

    def test_zero_rate_never_depletes(self):
        r = _invoke(calculator, operation="depletion_estimate",
                    params={"current_qty": 100, "consumption_rate_per_hour": 0, "remaining_hours": 4})
        assert r["will_deplete"] is False
        assert r["hours_until_empty"] == float("inf")


class TestCalculatorCost:
    def test_default_sku_cost(self):
        r = _invoke(calculator, operation="cost_estimate",
                    params={"downtime_minutes": 60, "line_rate_cases_per_hour": 300})
        assert r["lost_cases"] == 300
        assert r["sku_id"] == "DEFAULT"
        assert r["cost_per_case_eur"] == 2.50
        assert r["estimated_cost_eur"] == 750.0

    def test_named_sku_cost(self):
        r = _invoke(calculator, operation="cost_estimate",
                    params={"downtime_minutes": 60, "line_rate_cases_per_hour": 300,
                            "sku_id": "PILSNER_330ML"})
        assert r["sku_id"] == "PILSNER_330ML"
        assert r["cost_per_case_eur"] == 2.80


class TestCalculatorUnknown:
    def test_unknown_operation(self):
        r = _invoke(calculator, operation="does_not_exist", params={})
        assert "error" in r


# --------------------------------------------------------------------------- #
# anomaly_detector
# --------------------------------------------------------------------------- #
from agent_server.tools import anomaly_detector
from agent_server.tools.anomaly_detector import (
    _iqr_anomalies,
    _zscore_anomalies,
    detect_anomalies,
)


class TestZScore:
    def test_too_few_points(self):
        assert _zscore_anomalies([1.0, 2.0, 3.0], ["t0", "t1", "t2"], "vib") == []

    def test_flat_series_no_anomalies(self):
        vals = [10.0] * 6
        assert _zscore_anomalies(vals, [f"t{i}" for i in range(6)], "vib") == []

    def test_detects_outlier(self):
        vals = [10.0] * 9 + [100.0]
        ts = [f"t{i}" for i in range(10)]
        out = _zscore_anomalies(vals, ts, "vib")
        assert len(out) == 1
        assert out[0]["value"] == 100.0
        assert out[0]["method"] == "z_score"


class TestIQR:
    def test_too_few_points(self):
        assert _iqr_anomalies([1.0] * 9, [f"t{i}" for i in range(9)], "vib") == []

    def test_detects_outlier(self):
        vals = [1.0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 1000.0]
        ts = [f"t{i}" for i in range(len(vals))]
        out = _iqr_anomalies(vals, ts, "vib")
        assert len(out) == 1
        assert out[0]["value"] == 1000.0
        assert out[0]["method"] == "iqr"
        assert out[0]["severity"] == "critical"


class TestDetectAnomalies:
    def test_rejects_invalid_machine_id(self):
        r = _invoke(detect_anomalies, machine_id="bad id!", shift_id="S1",
                    timestamp_start="2026-01-01 00:00:00",
                    timestamp_end="2026-01-01 01:00:00")
        assert "error" in r and "machine_id" in r["error"]

    def test_rejects_invalid_timestamp(self):
        r = _invoke(detect_anomalies, machine_id="M1", shift_id="S1",
                    timestamp_start="yesterday",
                    timestamp_end="2026-01-01 01:00:00")
        assert "error" in r and "timestamp_start" in r["error"]

    def test_no_data(self, monkeypatch):
        monkeypatch.setattr(anomaly_detector, "sql_query", _FakeSQL([]))
        r = _invoke(detect_anomalies, machine_id="M1", shift_id="S1",
                    timestamp_start="2026-01-01 00:00:00",
                    timestamp_end="2026-01-01 01:00:00")
        assert r["total_points"] == 0
        assert "error" in r

    def test_detects_from_telemetry(self, monkeypatch):
        rows = [
            {"timestamp": f"2026-01-01 00:{i:02d}:00", "sensor_name": "vibration",
             "value": 4.0}
            for i in range(9)
        ]
        rows.append({"timestamp": "2026-01-01 00:09:00", "sensor_name": "vibration",
                     "value": 40.0})
        monkeypatch.setattr(anomaly_detector, "sql_query", _FakeSQL(rows))
        r = _invoke(detect_anomalies, machine_id="M1", shift_id="S1",
                    timestamp_start="2026-01-01 00:00:00",
                    timestamp_end="2026-01-01 01:00:00", method="zscore")
        assert r["total_sensors_analyzed"] == 1
        assert r["total_anomalies"] >= 1
        assert any(a["value"] == 40.0 for a in r["anomalies"])


# --------------------------------------------------------------------------- #
# sql_query  (critical: single read-only entry point for every specialist)
# --------------------------------------------------------------------------- #
from agent_server.tools import sql_query as sqm
from agent_server.tools.sql_query import sql_query


class _FakeCursor:
    def __init__(self, description, rows, fail_forever=False):
        self._description = description
        self._rows = rows
        self._fail_forever = fail_forever
        self.executed: list[str] = []

    @property
    def description(self):
        return self._description

    def execute(self, q):
        self.executed.append(q)
        if self._fail_forever:
            raise RuntimeError("transient warehouse error")

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def close(self):
        pass


class TestSQLQueryGuards:
    def test_rejects_non_select(self):
        r = sql_query.invoke({"query": "DELETE FROM t WHERE 1=1"})
        assert r == [{"error": "Only SELECT queries are allowed"}]

    def test_rejects_dangerous_keyword_in_select(self):
        r = sql_query.invoke({"query": "SELECT 1; DROP TABLE t"})
        assert "error" in r[0]
        assert "Forbidden SQL keywords" in r[0]["error"]


class TestSQLQueryExecution:
    def test_auto_appends_limit_and_parses_rows(self, monkeypatch):
        cur = _FakeCursor(description=[("a",), ("b",)], rows=[(1, 2), (3, 4)])
        monkeypatch.setattr(sqm, "_get_connection", lambda: _FakeConn(cur))
        r = sql_query.invoke({"query": "SELECT a, b FROM t"})
        assert r == [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
        assert cur.executed[-1].endswith("LIMIT 500")

    def test_preserves_existing_limit(self, monkeypatch):
        cur = _FakeCursor(description=[("a",)], rows=[(1,)])
        monkeypatch.setattr(sqm, "_get_connection", lambda: _FakeConn(cur))
        sql_query.invoke({"query": "SELECT a FROM t LIMIT 5"})
        assert cur.executed[-1].count("LIMIT") == 1
        assert cur.executed[-1].rstrip().endswith("LIMIT 5")

    def test_empty_description_returns_empty(self, monkeypatch):
        cur = _FakeCursor(description=None, rows=[])
        monkeypatch.setattr(sqm, "_get_connection", lambda: _FakeConn(cur))
        assert sql_query.invoke({"query": "SELECT 1"}) == []

    def test_retries_then_returns_error(self, monkeypatch):
        cur = _FakeCursor(description=None, rows=[], fail_forever=True)
        monkeypatch.setattr(sqm, "_get_connection", lambda: _FakeConn(cur))
        monkeypatch.setattr(sqm, "_invalidate_connection", lambda: None)
        r = sql_query.invoke({"query": "SELECT 1"})
        assert "error" in r[0]
        assert len(cur.executed) == 3  # 3 attempts before giving up


class TestSQLHelpers:
    def test_get_host_strips_scheme_and_slash(self, monkeypatch):
        monkeypatch.setenv("DATABRICKS_HOST", "https://foo.cloud.databricks.com/")
        assert sqm._get_host() == "foo.cloud.databricks.com"

    def test_extract_token_dict_path(self):
        w = types.SimpleNamespace(
            config=types.SimpleNamespace(
                authenticate=lambda: {"Authorization": "Bearer tok1"}))
        assert sqm._extract_token(w) == "tok1"

    def test_extract_token_mutating_path(self):
        _S = object()

        def auth(headers=_S):
            if headers is _S:
                raise TypeError("needs a dict")
            headers["Authorization"] = "Bearer tok2"
        w = types.SimpleNamespace(config=types.SimpleNamespace(authenticate=auth))
        assert sqm._extract_token(w) == "tok2"

    def test_extract_token_attribute_path(self):
        def auth(*a):
            raise TypeError
        w = types.SimpleNamespace(
            config=types.SimpleNamespace(authenticate=auth, token="tok3"))
        assert sqm._extract_token(w) == "tok3"


# --------------------------------------------------------------------------- #
# similar_case_retriever  (SQL fallback path carries an injection guard)
# --------------------------------------------------------------------------- #
from agent_server.tools.similar_case_retriever import retrieve_similar_cases


class _CaptureSQL:
    def __init__(self):
        self.last_query: str | None = None

    def invoke(self, q):
        self.last_query = q
        return [{"event_id": "e1"}]


@pytest.fixture
def _force_vs_fallback(monkeypatch):
    """Make the Vector Search import/construction fail so the SQL fallback runs,
    and capture the SQL the fallback issues."""
    fake_parent = types.ModuleType("databricks.vector_search")
    fake_client = types.ModuleType("databricks.vector_search.client")

    class _VSCRaise:
        def __init__(self, *a, **k):
            raise RuntimeError("Vector Search endpoint unavailable")

    fake_client.VectorSearchClient = _VSCRaise
    monkeypatch.setitem(sys.modules, "databricks.vector_search", fake_parent)
    monkeypatch.setitem(sys.modules, "databricks.vector_search.client", fake_client)

    cap = _CaptureSQL()
    monkeypatch.setattr("agent_server.tools.sql_query.sql_query", cap)
    return cap


class TestSimilarCaseFallback:
    def test_builds_safe_where_clause(self, _force_vs_fallback):
        r = _invoke(retrieve_similar_cases, description="filler seized",
                    machine_id="FILLER", event_type="major_stop", top_k=3)
        q = _force_vs_fallback.last_query
        assert q is not None
        assert "machine_id = 'FILLER'" in q
        assert "event_type = 'major_stop'" in q
        assert "LIMIT 3" in q
        assert r == [{"event_id": "e1"}]

    def test_rejects_injection_in_filters(self, _force_vs_fallback):
        _invoke(retrieve_similar_cases, description="x",
                machine_id="FILLER'; DROP TABLE event_log; --", top_k=3)
        q = _force_vs_fallback.last_query
        # unsafe machine_id must NOT be interpolated into the WHERE clause
        assert "DROP TABLE" not in q
        assert "machine_id =" not in q

    def test_clamps_top_k(self, _force_vs_fallback):
        _invoke(retrieve_similar_cases, description="x", top_k=999)
        assert "LIMIT 10" in _force_vs_fallback.last_query


# --------------------------------------------------------------------------- #
# genie_query  (guards + response parsing; external REST is mocked)
# --------------------------------------------------------------------------- #
import requests as _real_requests

from agent_server.tools import genie_query as gq
from agent_server.tools.genie_query import genie_query


class _GenieResp:
    def __init__(self, payload, ok=True):
        self._payload = payload
        self.ok = ok

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class _FakeRequests:
    HTTPError = _real_requests.HTTPError

    def __init__(self):
        self.urls: list[str] = []

    def post(self, url, **_kw):
        self.urls.append(url)
        if "execute-query" in url:
            return _GenieResp({"statement_response": {
                "manifest": {"schema": {"columns": [{"name": "x"}]}},
                "result": {"data_array": [[1], [2]]}}}, ok=True)
        if url.endswith("/conversations"):
            return _GenieResp({"conversation_id": "c1"})
        if url.endswith("/messages"):
            return _GenieResp({"message_id": "m1"})
        return _GenieResp({})


class TestGenieGuards:
    def test_space_not_configured(self, monkeypatch):
        monkeypatch.setattr(gq, "_SPACE_ID", "")
        r = _invoke(genie_query, question="What is the OEE trend?")
        assert "error" in r and "not configured" in r["error"].lower()

    def test_question_too_short(self, monkeypatch):
        monkeypatch.setattr(gq, "_SPACE_ID", "sp1")
        r = _invoke(genie_query, question="hi")
        assert "error" in r


class TestGenieHappyPath:
    def test_parses_answer_sql_and_data(self, monkeypatch):
        monkeypatch.setattr(gq, "_SPACE_ID", "sp1")
        monkeypatch.setattr(gq, "_make_headers",
                            lambda: ({"Content-Type": "application/json"}, "http://host"))
        monkeypatch.setattr(gq, "_poll_message", lambda *a, **k: {
            "status": "COMPLETED",
            "attachments": [{
                "text": {"content": "The OEE is 75%"},
                "query": {"query": "SELECT 1"},
                "id": "att1",
            }],
        })
        monkeypatch.setattr(gq, "requests", _FakeRequests())
        r = _invoke(genie_query, question="What is the OEE trend?")
        assert r["answer"] == "The OEE is 75%"
        assert r["sql"] == "SELECT 1"
        assert r["data"] == [{"x": 1}, {"x": 2}]

    def test_failed_status_returns_error(self, monkeypatch):
        monkeypatch.setattr(gq, "_SPACE_ID", "sp1")
        monkeypatch.setattr(gq, "_make_headers",
                            lambda: ({"Content-Type": "application/json"}, "http://host"))
        monkeypatch.setattr(gq, "_poll_message",
                            lambda *a, **k: {"status": "FAILED", "error": "boom"})
        monkeypatch.setattr(gq, "requests", _FakeRequests())
        r = _invoke(genie_query, question="What is the OEE trend?")
        assert "error" in r
