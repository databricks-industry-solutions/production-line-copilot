"""Extra tool coverage — connection management + auth/poll paths not exercised
by test_agent_tools.py.

Targets the previously-uncovered branches:
  * sql_query: _create_connection (missing config / token / OAuth), cached +
    stale reconnect in _get_connection, _invalidate_connection, 500-row log
  * genie_query: _make_headers (dict / token / fallback / error), _poll_message
    (completed / timeout), TIMEOUT + no-answer fallbacks, HTTPError handler
  * similar_case_retriever: the Vector Search happy path (parses index results)
"""
from __future__ import annotations

import sys
import time
import types

import pytest

# Ensure the @tool-decorated modules import even in a minimal env.
try:
    import langchain_core  # noqa: F401
    import langchain_core.tools  # noqa: F401
except Exception:  # pragma: no cover
    _fake_lc = types.ModuleType("langchain_core")
    _fake_tools = types.ModuleType("langchain_core.tools")
    _fake_tools.tool = lambda f: f
    _fake_lc.tools = _fake_tools
    sys.modules.setdefault("langchain_core", _fake_lc)
    sys.modules.setdefault("langchain_core.tools", _fake_tools)


def _invoke(tool_obj, **kwargs):
    if hasattr(tool_obj, "invoke"):
        return tool_obj.invoke(kwargs)
    return tool_obj(**kwargs)


# =========================================================================== #
# sql_query — connection management
# =========================================================================== #
from agent_server.tools import sql_query as sqm
from agent_server.tools.sql_query import sql_query


@pytest.fixture(autouse=True)
def _reset_sql_conn(monkeypatch):
    """Isolate the module-global cached connection between tests."""
    monkeypatch.setattr(sqm, "_cached_connection", None, raising=False)
    monkeypatch.setattr(sqm, "_last_health_check", 0.0, raising=False)
    yield


def test_create_connection_missing_config_raises(monkeypatch):
    monkeypatch.setattr(sqm, "_WAREHOUSE_ID", "")
    monkeypatch.setenv("DATABRICKS_HOST", "")
    with pytest.raises(RuntimeError, match="Missing SQL config"):
        sqm._create_connection()


def test_create_connection_uses_token_env(monkeypatch):
    import databricks.sql as dbsql

    sentinel = object()
    monkeypatch.setattr(sqm, "_WAREHOUSE_ID", "wh123")
    monkeypatch.setenv("DATABRICKS_HOST", "https://ws.cloud.databricks.com")
    monkeypatch.setenv("DATABRICKS_TOKEN", "pat-token")
    monkeypatch.setattr(dbsql, "connect", lambda **kw: sentinel)
    assert sqm._create_connection() is sentinel


def test_create_connection_oauth_path(monkeypatch):
    import databricks.sql as dbsql

    sentinel = object()
    monkeypatch.setattr(sqm, "_WAREHOUSE_ID", "wh123")
    monkeypatch.setenv("DATABRICKS_HOST", "https://ws.cloud.databricks.com")
    monkeypatch.setenv("DATABRICKS_TOKEN", "")
    monkeypatch.setattr(
        "agent_server.tools.workspace_client.app_workspace_client",
        lambda: object(),
    )
    monkeypatch.setattr(sqm, "_extract_token", lambda w: "oauth-token")
    monkeypatch.setattr(dbsql, "connect", lambda **kw: sentinel)
    assert sqm._create_connection() is sentinel


def test_get_connection_returns_cached_within_interval(monkeypatch):
    cached = object()
    monkeypatch.setattr(sqm, "_cached_connection", cached)
    monkeypatch.setattr(sqm, "_last_health_check", time.monotonic())
    # would blow up if it tried to reconnect
    monkeypatch.setattr(sqm, "_create_connection", lambda: pytest.fail("reconnected"))
    assert sqm._get_connection() is cached


def test_get_connection_reconnects_when_stale(monkeypatch):
    class _BadConn:
        def cursor(self):
            raise RuntimeError("half-closed")

        def close(self):
            pass

    fresh = object()
    monkeypatch.setattr(sqm, "_cached_connection", _BadConn())
    monkeypatch.setattr(sqm, "_last_health_check", 0.0)  # forces health check
    monkeypatch.setattr(sqm, "_create_connection", lambda: fresh)
    assert sqm._get_connection() is fresh


def test_invalidate_connection_closes_and_clears(monkeypatch):
    closed = {"v": False}

    class _Conn:
        def close(self):
            closed["v"] = True

    monkeypatch.setattr(sqm, "_cached_connection", _Conn())
    sqm._invalidate_connection()
    assert closed["v"] is True
    assert sqm._cached_connection is None


def test_sql_query_500_rows_logged(monkeypatch):
    class _Cur:
        description = [("a",)]

        def execute(self, q):
            pass

        def fetchall(self):
            return [(i,) for i in range(500)]

        def close(self):
            pass

    class _Conn:
        def cursor(self):
            return _Cur()

    monkeypatch.setattr(sqm, "_get_connection", lambda: _Conn())
    out = sql_query.invoke({"query": "SELECT a FROM t LIMIT 500"})
    assert len(out) == 500


def test_extract_token_falls_through_to_empty():
    """All three auth strategies fail → returns '' (never raises)."""

    def auth(*a, **k):
        raise TypeError

    class _NoToken:
        authenticate = staticmethod(auth)

        @property
        def token(self):
            raise RuntimeError("no token")

    w = types.SimpleNamespace(config=_NoToken())
    assert sqm._extract_token(w) == ""


# =========================================================================== #
# genie_query — auth + poll + error paths
# =========================================================================== #
import requests as _real_requests

from agent_server.tools import genie_query as gq
from agent_server.tools.genie_query import genie_query


def test_make_headers_dict_auth(monkeypatch):
    w = types.SimpleNamespace(
        config=types.SimpleNamespace(
            host="https://ws.cloud.databricks.com/",
            authenticate=lambda: {"Authorization": "Bearer tok"},
        )
    )
    monkeypatch.setattr(
        "agent_server.tools.workspace_client.app_workspace_client", lambda: w
    )
    headers, host = gq._make_headers()
    assert headers["Authorization"] == "Bearer tok"
    assert headers["Content-Type"] == "application/json"
    assert host == "https://ws.cloud.databricks.com"


def test_make_headers_string_token(monkeypatch):
    w = types.SimpleNamespace(
        config=types.SimpleNamespace(
            host="https://ws.cloud.databricks.com",
            authenticate=lambda: "rawtoken",
        )
    )
    monkeypatch.setattr(
        "agent_server.tools.workspace_client.app_workspace_client", lambda: w
    )
    headers, _ = gq._make_headers()
    assert headers["Authorization"] == "Bearer rawtoken"


def test_make_headers_falls_back_to_token_env(monkeypatch):
    def _boom():
        raise RuntimeError("no app client")

    monkeypatch.setattr(
        "agent_server.tools.workspace_client.app_workspace_client", _boom
    )
    monkeypatch.setenv("DATABRICKS_HOST", "https://ws.cloud.databricks.com")
    monkeypatch.setenv("DATABRICKS_TOKEN", "pat")
    headers, host = gq._make_headers()
    assert headers["Authorization"] == "Bearer pat"


def test_make_headers_raises_when_no_auth(monkeypatch):
    def _boom():
        raise RuntimeError("no app client")

    monkeypatch.setattr(
        "agent_server.tools.workspace_client.app_workspace_client", _boom
    )
    monkeypatch.setenv("DATABRICKS_HOST", "")
    monkeypatch.setenv("DATABRICKS_TOKEN", "")
    with pytest.raises(RuntimeError):
        gq._make_headers()


def test_poll_message_completed(monkeypatch):
    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"status": "COMPLETED", "attachments": []}

    fake_requests = types.SimpleNamespace(get=lambda url, **kw: _Resp())
    monkeypatch.setattr(gq, "requests", fake_requests)
    out = gq._poll_message({}, "http://h", "sp", "c", "m")
    assert out["status"] == "COMPLETED"


def test_poll_message_timeout(monkeypatch):
    monkeypatch.setattr(gq, "_MAX_POLL_SECONDS", 0)
    out = gq._poll_message({}, "http://h", "sp", "c", "m")
    assert out == {"status": "TIMEOUT"}


def test_genie_query_timeout_returns_error(monkeypatch):
    monkeypatch.setattr(gq, "_SPACE_ID", "sp1")
    monkeypatch.setattr(gq, "_make_headers", lambda: ({}, "http://h"))
    monkeypatch.setattr(gq, "_poll_message", lambda *a, **k: {"status": "TIMEOUT"})

    class _R:
        def __init__(self, payload):
            self._p = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._p

    def post(url, **kw):
        return _R({"conversation_id": "c1", "message": {"message_id": "m1"}})

    monkeypatch.setattr(gq, "requests", types.SimpleNamespace(post=post, HTTPError=_real_requests.HTTPError))
    r = _invoke(genie_query, question="What is the OEE trend today?")
    assert "error" in r and "60 seconds" in r["error"]


def test_genie_query_no_text_summary(monkeypatch):
    monkeypatch.setattr(gq, "_SPACE_ID", "sp1")
    monkeypatch.setattr(gq, "_make_headers", lambda: ({}, "http://h"))
    monkeypatch.setattr(
        gq, "_poll_message", lambda *a, **k: {"status": "COMPLETED", "attachments": []}
    )

    class _R:
        def __init__(self, payload):
            self._p = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._p

    def post(url, **kw):
        return _R({"conversation_id": "c1", "message": {"message_id": "m1"}})

    monkeypatch.setattr(gq, "requests", types.SimpleNamespace(post=post, HTTPError=_real_requests.HTTPError))
    r = _invoke(genie_query, question="Any downtime this shift?")
    assert r["answer"] == "Genie processed the query but returned no text summary."


def test_genie_query_http_error(monkeypatch):
    monkeypatch.setattr(gq, "_SPACE_ID", "sp1")
    monkeypatch.setattr(gq, "_make_headers", lambda: ({}, "http://h"))

    def post(url, **kw):
        err = _real_requests.HTTPError()
        err.response = types.SimpleNamespace(status_code=503, text="unavailable")
        raise err

    monkeypatch.setattr(
        gq, "requests", types.SimpleNamespace(post=post, HTTPError=_real_requests.HTTPError)
    )
    r = _invoke(genie_query, question="What is the OEE trend today?")
    assert "error" in r and "503" in r["error"]


# =========================================================================== #
# similar_case_retriever — Vector Search happy path
# =========================================================================== #
from agent_server.tools.similar_case_retriever import retrieve_similar_cases


def test_vector_search_happy_path(monkeypatch):
    class _Index:
        def similarity_search(self, **kw):
            return {
                "result": {
                    "columns": [{"name": "event_id"}, {"name": "machine_id"}],
                    "data_array": [["e1", "FILLER"], ["e2", "CAPPER"]],
                }
            }

    class _VSC:
        def __init__(self, *a, **k):
            pass

        def get_index(self, endpoint_name, index_name):
            return _Index()

    fake_parent = types.ModuleType("databricks.vector_search")
    fake_client = types.ModuleType("databricks.vector_search.client")
    fake_client.VectorSearchClient = _VSC
    monkeypatch.setitem(sys.modules, "databricks.vector_search", fake_parent)
    monkeypatch.setitem(sys.modules, "databricks.vector_search.client", fake_client)

    out = _invoke(
        retrieve_similar_cases, description="filler seized", machine_id="FILLER", top_k=2
    )
    assert out == [
        {"event_id": "e1", "machine_id": "FILLER"},
        {"event_id": "e2", "machine_id": "CAPPER"},
    ]
