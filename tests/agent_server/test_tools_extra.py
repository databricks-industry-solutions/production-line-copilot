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
def _reset_sql_conn():
    """Clear this thread's cached connection between tests.

    Thread-local rather than a module global, because the connector declares
    PEP 249 ``threadsafety = 1``: connections must not be shared between
    threads.
    """
    sqm._local.connection = None
    sqm._local.last_health_check = 0.0
    yield
    sqm._local.connection = None
    sqm._local.last_health_check = 0.0


def _seed_connection(connection, checked_at: float = 0.0) -> None:
    sqm._local.connection = connection
    sqm._local.last_health_check = checked_at


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
    _seed_connection(cached, time.monotonic())
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
    _seed_connection(_BadConn(), 0.0)  # zero forces the health check
    monkeypatch.setattr(sqm, "_create_connection", lambda: fresh)
    assert sqm._get_connection() is fresh


def test_invalidate_connection_closes_and_clears():
    closed = {"v": False}

    class _Conn:
        def close(self):
            closed["v"] = True

    _seed_connection(_Conn())
    sqm._invalidate_connection()
    assert closed["v"] is True
    assert sqm._local.connection is None


def test_invalidate_survives_a_connection_that_will_not_close():
    """The caller is discarding it either way; a raising close must not escape."""

    class _StubbornConn:
        def close(self):
            raise RuntimeError("already gone")

    _seed_connection(_StubbornConn())
    sqm._invalidate_connection()
    assert sqm._local.connection is None


# --------------------------------------------------------------------------- #
# Thread isolation.
#
# databricks-sql-connector declares PEP 249 threadsafety = 1: threads may share
# the module but NOT connections. The specialist pool runs six workers, so these
# pin the property the pool depends on.
# --------------------------------------------------------------------------- #
def test_the_driver_still_forbids_sharing_a_connection():
    """If this ever reaches 2, per-thread connections stop being necessary."""
    import databricks.sql as dbsql

    assert dbsql.threadsafety == 1, (
        f"connector now declares threadsafety={dbsql.threadsafety}; "
        "re-read whether per-thread connections are still required"
    )


def test_each_thread_gets_its_own_connection(monkeypatch):
    import threading as _threading

    workers = 4
    monkeypatch.setattr(sqm, "_create_connection", object)

    # A barrier, so all four hold a connection at the same moment. Without it
    # the threads can run one after another, and thread ids are recycled once a
    # thread exits — which would let a shared connection pass unnoticed.
    ready = _threading.Barrier(workers, timeout=5)
    seen: list[object] = [None] * workers

    def _worker(slot: int):
        connection = sqm._get_connection()
        ready.wait()
        seen[slot] = connection

    threads = [_threading.Thread(target=_worker, args=(i,)) for i in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert all(c is not None for c in seen), "a worker never got a connection"
    assert len({id(c) for c in seen}) == workers, "threads shared a connection"


def test_one_thread_invalidating_leaves_the_others_alone(monkeypatch):
    """The cascade this replaces: a failure closed everyone's connection.

    One thread hitting a stale socket used to call `_invalidate_connection()` on
    the single shared connection, failing every query in flight — each of which
    then invalidated in turn.
    """
    import threading as _threading

    monkeypatch.setattr(sqm, "_create_connection", lambda: object())

    other_thread_state: dict[str, object] = {}
    reached_invalidate = _threading.Event()
    may_finish = _threading.Event()

    def _other():
        other_thread_state["before"] = sqm._get_connection()
        reached_invalidate.wait(timeout=5)
        other_thread_state["after"] = sqm._get_connection()
        may_finish.set()

    thread = _threading.Thread(target=_other)
    thread.start()

    # This thread throws its own connection away while the other holds one.
    sqm._get_connection()
    sqm._invalidate_connection()
    reached_invalidate.set()

    assert may_finish.wait(timeout=5)
    thread.join()
    assert other_thread_state["before"] is other_thread_state["after"]


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


def _install_fake_vs(monkeypatch, vsc_cls):
    fake_parent = types.ModuleType("databricks.vector_search")
    fake_client = types.ModuleType("databricks.vector_search.client")
    fake_client.VectorSearchClient = vsc_cls
    monkeypatch.setitem(sys.modules, "databricks.vector_search", fake_parent)
    monkeypatch.setitem(sys.modules, "databricks.vector_search.client", fake_client)


# The exact envelope a Databricks index returns, captured from a live
# similarity_search. Column names live under `manifest`; `result` carries only
# row_count and data_array. The previous fixture here invented a
# `result.columns` key, so the parser was written against a response no server
# produces: every real call raised KeyError, the caller's except turned that
# into a keyword-search fallback, and the test suite reported the semantic path
# as covered and green.
def _envelope(rows):
    names = ["event_id", "machine_id", "description", "corrective_action", "score"]
    return {
        "manifest": {"column_count": len(names), "columns": [{"name": n} for n in names]},
        "result": {"row_count": len(rows), "data_array": rows},
    }


def test_vector_search_happy_path(monkeypatch):
    class _Index:
        def similarity_search(self, **kw):
            return _envelope([
                ["e1", "FILLER", "bearing overheated", "replaced bearing", 0.91],
                ["e2", "CAPPER", "cap misfeed", "re-timed starwheel", 0.72],
            ])

    class _VSC:
        def __init__(self, *a, **k):
            pass

        def get_index(self, endpoint_name, index_name):
            return _Index()

    _install_fake_vs(monkeypatch, _VSC)
    # The semantic path is only attempted when an endpoint is configured; with
    # VS_ENDPOINT unset the tool goes straight to keyword matching.
    monkeypatch.setenv("VS_ENDPOINT", "vs-test")

    out = _invoke(
        retrieve_similar_cases, description="filler seized", machine_id="FILLER", top_k=2
    )
    assert [r["event_id"] for r in out] == ["e1", "e2"]
    assert all(r["retrieval"] == "vector_search" for r in out)
    assert out[0]["corrective_action"] == "replaced bearing"
    # Renamed from `score` so callers need not know which path answered; the
    # keyword fallback has always called it match_score.
    assert out[0]["match_score"] == 0.91 and "score" not in out[0]


def test_a_response_without_a_column_manifest_is_an_error_not_empty_results(monkeypatch):
    """Returning [] here would read as "no similar cases", which is a lie."""
    class _Index:
        def similarity_search(self, **kw):
            return {"result": {"row_count": 1, "data_array": [["e1"]]}}

    class _VSC:
        def __init__(self, *a, **k):
            pass

        def get_index(self, **kw):
            return _Index()

    _install_fake_vs(monkeypatch, _VSC)
    monkeypatch.setenv("VS_ENDPOINT", "vs-test")
    monkeypatch.setattr(
        "agent_server.tools.similar_case_retriever._keyword_search",
        lambda *a, **k: [{"retrieval": "keyword"}],
    )
    out = _invoke(retrieve_similar_cases, description="x", top_k=2)
    assert out == [{"retrieval": "keyword"}], "should fall back, not report zero cases"


class TestTheClientGetsCredentialsTheRuntimeActuallyHas:
    """A Databricks App has OAuth client credentials and no PAT.

    ``VectorSearchClient()`` with no arguments resolves credentials via MLflow,
    which wants ``DATABRICKS_TOKEN``. In the app that raised on every call, and
    the fallback swallowed it — so the endpoint stayed online, billed hourly,
    and served nothing. The old fixture's ``__init__(self, *a, **k)`` accepted
    any arguments, which is why no test noticed.
    """

    def _spy(self):
        seen = {}

        class _VSC:
            def __init__(self, **kwargs):
                seen.update(kwargs)

            def get_index(self, **kw):
                class _I:
                    def similarity_search(self, **k):
                        return _envelope([])
                return _I()

        return seen, _VSC

    def test_app_service_principal_is_passed_explicitly(self, monkeypatch):
        seen, vsc = self._spy()
        _install_fake_vs(monkeypatch, vsc)
        monkeypatch.setenv("VS_ENDPOINT", "vs-test")
        monkeypatch.setenv("DATABRICKS_CLIENT_ID", "sp-id")
        monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "sp-secret")
        monkeypatch.setenv("DATABRICKS_HOST", "example.cloud.databricks.com")

        _invoke(retrieve_similar_cases, description="x", top_k=1)

        assert seen["service_principal_client_id"] == "sp-id"
        assert seen["service_principal_client_secret"] == "sp-secret"
        # The library rejects a bare hostname; Apps inject it without a scheme.
        assert seen["workspace_url"] == "https://example.cloud.databricks.com"

    def test_a_token_in_the_environment_is_passed_rather_than_rediscovered(self, monkeypatch):
        seen, vsc = self._spy()
        _install_fake_vs(monkeypatch, vsc)
        monkeypatch.setenv("VS_ENDPOINT", "vs-test")
        monkeypatch.delenv("DATABRICKS_CLIENT_ID", raising=False)
        monkeypatch.delenv("DATABRICKS_CLIENT_SECRET", raising=False)
        monkeypatch.setenv("DATABRICKS_HOST", "https://example.cloud.databricks.com")
        monkeypatch.setenv("DATABRICKS_TOKEN", "dapi-xxx")

        _invoke(retrieve_similar_cases, description="x", top_k=1)

        assert seen["personal_access_token"] == "dapi-xxx"


def test_no_configured_endpoint_skips_vector_search_entirely(monkeypatch):
    """No index is provisioned in either environment today.

    Attempting the search anyway cost a failed round-trip on every call and
    recorded a vector_search_calls metric for work that never happened.
    """
    called = []

    class _VSC:
        def __init__(self, *a, **k):
            called.append("constructed")

    fake_parent = types.ModuleType("databricks.vector_search")
    fake_client = types.ModuleType("databricks.vector_search.client")
    fake_client.VectorSearchClient = _VSC
    monkeypatch.setitem(sys.modules, "databricks.vector_search", fake_parent)
    monkeypatch.setitem(sys.modules, "databricks.vector_search.client", fake_client)
    monkeypatch.delenv("VS_ENDPOINT", raising=False)

    from agent_server.tools import similar_case_retriever as scr

    class _FakeSQL:
        def invoke(self, _q):
            return [{"event_id": "e1"}]

    monkeypatch.setattr("agent_server.tools.sql_query.sql_query", _FakeSQL())
    out = _invoke(retrieve_similar_cases, description="filler seized", top_k=1)
    assert called == []
    assert out[0]["retrieval"] == "keyword"
    assert scr is not None
