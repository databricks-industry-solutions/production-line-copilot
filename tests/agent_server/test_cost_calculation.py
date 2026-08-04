"""Arithmetic and classification guards for the Cost & Observability panel.

The cost panel is the one screen in this app whose output people are tempted to
quote in a budget conversation, and every number on it is derived rather than
measured: DBU come from ``system.billing.usage``, dollars come from a rounded
list-price table, and the categories come from substring matching against SKU
names that Databricks has renamed several times. Nothing here can verify the
rate card is right — that depends on the customer's contract — but these tests
pin the parts that are supposed to be internally consistent, because each one
of them had drifted:

* the SQL ``CASE`` and the Python classifier disagreed about
  FOUNDATION_MODEL_TRAINING, pricing it two ways on one screen;
* the live-mode Total included a synced-tables charge that had no row;
* freshness read UTC timestamps as local time.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

import agent_server.start_server as ss

# Realistic (billing_origin_product, sku_name) pairs. Names follow the shapes
# Databricks actually emits rather than being invented, so a future SKU rename
# shows up here as a failing expectation instead of a silent reclassification.
REAL_SKUS: list[tuple[str, str, str]] = [
    ("JOBS", "PREMIUM_JOBS_SERVERLESS_COMPUTE_EU_WEST_1", "Jobs"),
    ("SQL", "PREMIUM_SQL_SERVERLESS_COMPUTE_EU_WEST_1", "SQL Warehouse"),
    ("SQL", "PREMIUM_SQL_PRO_COMPUTE_EU_WEST_1", "SQL Warehouse"),
    ("MODEL_SERVING", "PREMIUM_SERVERLESS_REAL_TIME_INFERENCE_EU_WEST_1", "Model Serving"),
    ("FOUNDATION_MODEL_TRAINING", "PREMIUM_GPU_SERVERLESS_EU_WEST_1", "Model Serving"),
    ("LAKEBASE", "PREMIUM_LAKEBASE_CAPACITY_EU_WEST_1", "Lakebase"),
    ("APPS", "PREMIUM_APPS_EU_WEST_1", "Apps"),
    ("DLT", "PREMIUM_DLT_ADVANCED_COMPUTE", "Pipelines"),
    ("SERVERLESS_PIPELINE", "PREMIUM_SERVERLESS_PIPELINE_COMPUTE", "Pipelines"),
    ("LAKEFLOW_CONNECT", "PREMIUM_LAKEFLOW_CONNECT_ZEROBUS_EU_WEST_1", "Zerobus"),
    ("ALL_PURPOSE", "PREMIUM_ALL_PURPOSE_COMPUTE", "Other"),
    ("VECTOR_SEARCH", "PREMIUM_VECTOR_SEARCH_EU_WEST_1", "Other"),
    ("NETWORKING", "PREMIUM_NETWORKING_EU_WEST_1", "Other"),
]


def _sql_case_eval(origin: str, sku: str) -> str:
    """Evaluate the generated ``CASE`` the way Spark would, in Python.

    The expression is built from ``_USAGE_CATEGORY_RULES`` as a flat sequence of
    ``WHEN <text> LIKE '%KW%' ... THEN 'Category'`` branches over one haystack,
    so replaying it is a matter of walking the same rules in the same order
    against the same string. This asserts the *generated SQL text* is faithful,
    which is the thing that could rot; it does not re-implement the classifier.
    """
    import re

    haystack = f"{origin} {sku}".upper()
    sql = ss._usage_category_sql()
    branches = re.findall(r"WHEN (.+?) THEN '([^']+)'", sql)
    assert branches, f"no WHEN branches parsed out of: {sql[:200]}"
    for condition, category in branches:
        keywords = re.findall(r"LIKE '%([^%]+)%'", condition)
        assert keywords, f"branch for {category} has no LIKE keywords"
        if any(kw in haystack for kw in keywords):
            return category
    return "Other"


@pytest.mark.parametrize(("origin", "sku", "expected"), REAL_SKUS)
def test_usage_category_classifies_real_skus(origin: str, sku: str, expected: str) -> None:
    assert ss._usage_category(origin, sku) == expected


@pytest.mark.parametrize(("origin", "sku", "expected"), REAL_SKUS)
def test_usage_category_sql_matches_python(origin: str, sku: str, expected: str) -> None:
    """The components table and the verification figure must agree.

    They are rendered a few hundred pixels apart and are supposed to describe
    the same rows. When these two drifted, a FOUNDATION_MODEL_TRAINING row was
    "Other" at $0.55/DBU in one and "Model Serving" at $0.07/DBU in the other.
    """
    assert _sql_case_eval(origin, sku) == ss._usage_category(origin, sku) == expected


def test_every_category_has_a_rate() -> None:
    """A category with no rate silently falls back to the "Other" price."""
    for category, _keywords in ss._USAGE_CATEGORY_RULES:
        assert category in ss._DBU_USD_RATES, f"{category} has no $/DBU rate"
    assert ss._UNCATEGORISED in ss._DBU_USD_RATES


def test_estimate_usd_is_dbu_times_the_published_rate() -> None:
    """The panel publishes its rate card, so the arithmetic must match it."""
    for component, rate in ss._DBU_USD_RATES.items():
        assert ss._estimate_usd(component, 100.0) == pytest.approx(100.0 * rate)
    # The exact figures from the reported panel, kept as a worked example.
    assert ss._estimate_usd("SQL Warehouse", 480.0) == pytest.approx(336.0)
    assert ss._estimate_usd("Other", 183.036) == pytest.approx(100.6698)
    assert ss._estimate_usd("Jobs", 178.028) == pytest.approx(97.9154)


def test_unknown_component_falls_back_to_the_other_rate() -> None:
    assert ss._estimate_usd("Nonexistent", 10.0) == pytest.approx(10.0 * ss._DBU_USD_RATES["Other"])


def test_billing_window_prunes_on_the_partition_column() -> None:
    """``usage_start_time`` is not the partition column; ``usage_date`` is.

    Without a date predicate each panel load scanned the account's whole
    billing history six to eight times over.
    """
    import inspect

    # The billing queries live in the worker, not the route: the route is now a
    # thin `asyncio.to_thread` wrapper so the rollup stops blocking the event
    # loop. See tests/agent_server/test_event_loop_blocking.py.
    source = inspect.getsource(ss._cost_summary_payload)
    assert "usage_date >=" in source, "billing queries lost their partition-pruning predicate"
    assert "usage_start_time >=" in source, "the exact window filter must remain"


class TestFreshnessLag:
    """``usage_start_time`` comes back as a naive UTC timestamp."""

    @staticmethod
    def _lag_hours(latest_ts_str: str) -> float:
        """The freshness computation as `ops_cost_summary` performs it."""
        parsed = datetime.fromisoformat(latest_ts_str.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        lag = max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())
        return round(lag / 3600.0, 2)

    def test_a_row_written_now_reads_as_zero_lag(self) -> None:
        """Regression: `.astimezone()` treats naive input as local time.

        On a UTC+2 laptop that made every fresh billing row look two hours
        stale, and the ">6h" staleness banner fire two hours early.
        """
        now_naive_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        assert self._lag_hours(now_naive_utc.isoformat(sep=" ")) == pytest.approx(0.0, abs=0.02)

    def test_a_three_hour_old_row_reads_as_three_hours(self) -> None:
        stale = (datetime.now(timezone.utc) - timedelta(hours=3)).replace(tzinfo=None)
        assert self._lag_hours(stale.isoformat(sep=" ")) == pytest.approx(3.0, abs=0.02)

    def test_an_explicit_offset_is_still_honoured(self) -> None:
        aware = datetime.now(timezone.utc) - timedelta(hours=1)
        assert self._lag_hours(aware.isoformat()) == pytest.approx(1.0, abs=0.02)


class TestLiveTotalMatchesItsBreakdown:
    """The Total under the live table must be the rows above it, summed.

    It was computed as a second, parallel expression, and that expression
    carried a synced-tables charge with no corresponding row — so with synced
    tables configured the Total exceeded the visible breakdown by
    $0.30/h per table and nothing on screen explained the gap.
    """

    @staticmethod
    def _live(monkeypatch: pytest.MonkeyPatch, **states: object) -> dict:
        import asyncio
        from collections import deque

        # The live counters are one process-wide deque, so anything else in the
        # suite that exercised an agent or a Genie call would otherwise leak in
        # and make these assertions depend on test ordering.
        monkeypatch.setattr(ss, "_LIVE_EVENTS", deque(maxlen=20000))
        base = {
            "warehouse": {"id": "wh1", "running": False, "state": "STOPPED"},
            "producer_job": {"id": "1", "running": False, "state": "IDLE"},
            "lakebase_syncer": {"id": "2", "running": False, "state": "IDLE"},
            "synced_tables": {"count": 0, "active": False},
        }
        base.update(states)
        monkeypatch.setattr(ss, "_resource_running_states", lambda: base)
        return asyncio.run(ss.ops_cost_live(minutes=60))

    def test_total_equals_sum_of_rows_when_idle(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = self._live(monkeypatch)
        rows = payload["estimated_usd"]["components"]
        assert payload["estimated_usd"]["total"] == pytest.approx(sum(r["usd"] for r in rows))

    def test_total_equals_sum_of_rows_with_synced_tables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = self._live(monkeypatch, synced_tables={"count": 4, "active": True})
        rows = payload["estimated_usd"]["components"]
        labels = [r["component"] for r in rows]
        assert "Synced tables" in labels, "synced-tables cost is in the total but has no row"
        assert payload["estimated_usd"]["total"] == pytest.approx(sum(r["usd"] for r in rows))

    def test_total_equals_sum_of_rows_when_everything_runs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = self._live(
            monkeypatch,
            warehouse={"id": "wh1", "running": True, "state": "RUNNING"},
            producer_job={"id": "1", "running": True, "state": "RUNNING"},
            lakebase_syncer={"id": "2", "running": True, "state": "RUNNING"},
            synced_tables={"count": 3, "active": True},
        )
        rows = payload["estimated_usd"]["components"]
        assert payload["estimated_usd"]["total"] == pytest.approx(sum(r["usd"] for r in rows))
        assert payload["estimated_usd"]["total"] > 0

    def test_idle_dedicated_genie_warehouse_is_not_billed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Configured is not running. Serverless warehouses auto-stop."""
        monkeypatch.setenv("OPS_GENIE_WAREHOUSE_ID", "genie-wh")
        monkeypatch.setenv("OPS_SQL_WAREHOUSE_ID", "main-wh")
        payload = self._live(monkeypatch)
        genie = next(r for r in payload["estimated_usd"]["components"] if r["component"] == "Genie")
        assert genie["usd"] == 0.0, "an idle Genie warehouse was charged a full hourly rate"
        assert genie["status"] == "idle"

    def test_a_queried_dedicated_genie_warehouse_is_billed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The other half: not billing a warehouse that did run would understate."""
        monkeypatch.setenv("OPS_GENIE_WAREHOUSE_ID", "genie-wh")
        monkeypatch.setenv("OPS_SQL_WAREHOUSE_ID", "main-wh")
        monkeypatch.setattr(ss, "_live_aggregate", lambda _m: {"genie_queries": 3.0})
        payload = self._live(monkeypatch)
        genie = next(r for r in payload["estimated_usd"]["components"] if r["component"] == "Genie")
        assert genie["usd"] > 0.0
        assert genie["status"] == "running"
        rows = payload["estimated_usd"]["components"]
        assert payload["estimated_usd"]["total"] == pytest.approx(sum(r["usd"] for r in rows))


class TestQueryTagging:
    """The app tags its statements so the Cost panel can find them later.

    Query tags are Public Preview and session configuration is applied as a
    `SET` when the session opens, so on a workspace that rejects the parameter
    this is the difference between a tagged connection and no connection. The
    tag is worth having and not worth losing the app's only data path for.
    """

    @staticmethod
    def _patch_env(monkeypatch: pytest.MonkeyPatch) -> None:
        import agent_server.tools.sql_query as sq

        monkeypatch.setattr(sq, "_WAREHOUSE_ID", "wh-1")
        monkeypatch.setattr(sq, "_query_tags_supported", True)
        monkeypatch.setenv("DATABRICKS_HOST", "https://example.cloud.databricks.com")
        monkeypatch.setenv("DATABRICKS_TOKEN", "dapi-test")

    def test_a_tagged_session_is_attempted_first(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import agent_server.tools.sql_query as sq

        self._patch_env(monkeypatch)
        calls: list[dict] = []

        def _connect(**kwargs: object) -> str:
            calls.append(kwargs)
            return "connection"

        monkeypatch.setattr("databricks.sql.connect", _connect)
        assert sq._create_connection() == "connection"
        assert len(calls) == 1
        tags = calls[0]["session_configuration"]["query_tags"]
        assert tags == f"{sq.APP_QUERY_TAG_KEY}:{sq.APP_QUERY_TAG_VALUE}"

    def test_a_rejected_tag_degrades_to_an_untagged_connection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import agent_server.tools.sql_query as sq

        self._patch_env(monkeypatch)
        calls: list[dict] = []

        def _connect(**kwargs: object) -> str:
            calls.append(kwargs)
            if "session_configuration" in kwargs:
                raise RuntimeError("Unknown configuration parameter: query_tags")
            return "connection"

        monkeypatch.setattr("databricks.sql.connect", _connect)
        assert sq._create_connection() == "connection", "app must still get a working connection"
        assert len(calls) == 2
        assert "session_configuration" not in calls[1]

    def test_the_tag_is_not_retried_after_it_is_known_unsupported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Otherwise every reconnect pays a failed handshake."""
        import agent_server.tools.sql_query as sq

        self._patch_env(monkeypatch)
        calls: list[dict] = []

        def _connect(**kwargs: object) -> str:
            calls.append(kwargs)
            if "session_configuration" in kwargs:
                raise RuntimeError("Unknown configuration parameter: query_tags")
            return "connection"

        monkeypatch.setattr("databricks.sql.connect", _connect)
        sq._create_connection()
        sq._create_connection()
        assert sum("session_configuration" in c for c in calls) == 1


class TestWarehouseQueryShare:
    """Apportioning a shared warehouse by each tenant's share of query time."""

    WAREHOUSE = "abc123"

    @staticmethod
    def _sql(rows: object) -> object:
        """A stand-in for the `sql_query` tool that returns `rows` verbatim."""

        class _Tool:
            def __init__(self) -> None:
                self.queries: list[str] = []

            def invoke(self, query: str) -> object:
                self.queries.append(query)
                return rows

        return _Tool()

    @pytest.fixture(autouse=True)
    def _identity(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            ss, "_APP_IDENTITY_CACHE", {"id": "2967555311742259", "name": "sp-uuid"}, raising=False
        )
        # Cross-test pollution otherwise: the cache is keyed on warehouse+window,
        # which several of these tests share.
        monkeypatch.setattr(ss, "_WH_SHARE_CACHE", {}, raising=False)

    def test_the_split_is_cached_because_the_query_costs_17_seconds(self) -> None:
        rows = [{"attribution": "app", "statements": 10, "duration_ms": 1000, "genie_duration_ms": 0},
                {"attribution": "other", "statements": 10, "duration_ms": 3000, "genie_duration_ms": 0}]
        tool = self._sql(rows)
        first = ss._warehouse_query_share(tool, self.WAREHOUSE, 24, 480.0)
        second = ss._warehouse_query_share(tool, self.WAREHOUSE, 24, 480.0)
        assert first is not None and second is not None
        assert len(tool.queries) == 1, "second call must not re-query query history"  # type: ignore[attr-defined]
        assert second["app_share"] == first["app_share"]
        assert second["split_age_seconds"] >= 0.0

    def test_a_cache_hit_still_reprices_against_fresh_dbu(self) -> None:
        """Billing is read fresh every load; only the expensive split is reused.

        Serving a stale DBU figure alongside fresh totals would put two
        different warehouse costs on the same screen again.
        """
        rows = [{"attribution": "app", "statements": 10, "duration_ms": 1000, "genie_duration_ms": 0},
                {"attribution": "other", "statements": 10, "duration_ms": 3000, "genie_duration_ms": 0}]
        tool = self._sql(rows)
        ss._warehouse_query_share(tool, self.WAREHOUSE, 24, 480.0)
        second = ss._warehouse_query_share(tool, self.WAREHOUSE, 24, 800.0)
        assert second is not None
        assert second["warehouse_dbu"] == 800.0
        assert second["app_dbu"] == pytest.approx(200.0), "25% of the newer 800 DBU"

    def test_different_windows_do_not_share_a_cache_entry(self) -> None:
        rows = [{"attribution": "app", "statements": 10, "duration_ms": 1000, "genie_duration_ms": 0}]
        tool = self._sql(rows)
        ss._warehouse_query_share(tool, self.WAREHOUSE, 24, 480.0)
        ss._warehouse_query_share(tool, self.WAREHOUSE, 6, 480.0)
        assert len(tool.queries) == 2  # type: ignore[attr-defined]

    def test_an_expired_entry_is_refetched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [{"attribution": "app", "statements": 10, "duration_ms": 1000, "genie_duration_ms": 0}]
        tool = self._sql(rows)
        ss._warehouse_query_share(tool, self.WAREHOUSE, 24, 480.0)
        # `ss.time` is the stdlib module, so the replacement must close over the
        # real clock rather than call the name it is about to shadow.
        real_time = time.time
        monkeypatch.setattr(ss.time, "time", lambda: real_time() + ss._WH_SHARE_TTL_S + 1)
        ss._warehouse_query_share(tool, self.WAREHOUSE, 24, 480.0)
        assert len(tool.queries) == 2  # type: ignore[attr-defined]

    def test_a_failure_is_not_cached(self) -> None:
        """A transient permission or availability error must not pin the panel
        to an error for the next ten minutes."""
        tool = self._sql([{"error": "TEMPORARILY_UNAVAILABLE"}])
        ss._warehouse_query_share(tool, self.WAREHOUSE, 24, 480.0)
        assert ss._WH_SHARE_CACHE == {}

    def test_share_apportions_warehouse_dbu_by_query_time(self) -> None:
        rows = [
            {"attribution": "app", "statements": 120, "duration_ms": 300000, "genie_duration_ms": 40000},
            {"attribution": "other", "statements": 8, "duration_ms": 100000, "genie_duration_ms": 0},
        ]
        out = ss._warehouse_query_share(self._sql(rows), self.WAREHOUSE, 24, 480.0)
        assert out is not None and out["available"] is True
        # 300k of 400k ms is the app's, so 75% of the warehouse's 480 DBU.
        assert out["app_share"] == pytest.approx(0.75)
        assert out["app_dbu"] == pytest.approx(360.0)
        assert out["app_estimated_usd"] == pytest.approx(360.0 * 0.70)
        assert out["app_statements"] == 120
        assert out["other_statements"] == 8

    def test_an_idle_warehouse_attributes_nothing_to_anyone(self) -> None:
        """0 ms of app time out of 0 ms total is not 100%.

        A warehouse can bill for a window in which nothing ran, and dividing
        by that zero would have handed the app the entire idle bill.
        """
        out = ss._warehouse_query_share(self._sql([]), self.WAREHOUSE, 24, 480.0)
        assert out is not None
        assert out["app_share"] is None
        assert out["app_dbu"] is None
        assert out["app_estimated_usd"] is None

    def test_sole_tenant_gets_the_whole_warehouse(self) -> None:
        rows = [{"attribution": "app", "statements": 20, "duration_ms": 5000, "genie_duration_ms": 0}]
        out = ss._warehouse_query_share(self._sql(rows), self.WAREHOUSE, 24, 480.0)
        assert out is not None
        assert out["app_share"] == pytest.approx(1.0)
        assert out["app_dbu"] == pytest.approx(480.0)

    def test_it_falls_back_to_identity_when_query_tags_are_unavailable(self) -> None:
        """`query_tags` is Public Preview; referencing it can fail the statement."""

        class _Tool:
            def __init__(self) -> None:
                self.queries: list[str] = []

            def invoke(self, query: str) -> object:
                self.queries.append(query)
                if "query_tags" in query:
                    return [{"error": "UNRESOLVED_COLUMN: query_tags"}]
                return [{"attribution": "app", "statements": 3, "duration_ms": 1000,
                         "genie_duration_ms": 0}]

        tool = _Tool()
        out = ss._warehouse_query_share(tool, self.WAREHOUSE, 24, 100.0)
        assert out is not None and out["available"] is True
        assert out["attribution_key"] == "identity"
        assert len(tool.queries) == 2, "should retry once without the tag predicate"
        assert "executed_as_user_id" in tool.queries[-1]

    def test_a_persistent_failure_is_reported_not_swallowed(self) -> None:
        out = ss._warehouse_query_share(
            self._sql([{"error": "PERMISSION_DENIED on system.query.history"}]), self.WAREHOUSE, 24, 480.0
        )
        assert out is not None
        assert out["available"] is False
        assert "PERMISSION_DENIED" in out["note"]

    def test_no_warehouse_configured_means_no_attribution(self) -> None:
        assert ss._warehouse_query_share(self._sql([]), "", 24, 480.0) is None

    def test_identifiers_are_quoted_into_the_query(self) -> None:
        """These come from the SDK and env rather than requests, but they are
        still interpolated, so a quote must not end the literal."""
        assert ss._sql_literal("o'brien") == "'o\\'brien'"
        tool = self._sql([])
        ss._warehouse_query_share(tool, "wh-1", 24, 1.0)
        assert "compute.warehouse_id = 'wh-1'" in tool.queries[0]  # type: ignore[attr-defined]


def test_workspace_scoping_prefers_the_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ss, "_WORKSPACE_ID_CACHE", {}, raising=False)
    monkeypatch.setenv("DATABRICKS_WORKSPACE_ID", "1444828305810485")
    assert ss._resolve_workspace_id() == "1444828305810485"


def test_workspace_scoping_ignores_a_non_numeric_env_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hostname in that variable must not be interpolated into the SQL."""
    monkeypatch.setattr(ss, "_WORKSPACE_ID_CACHE", {"id": None}, raising=False)
    monkeypatch.setenv("DATABRICKS_WORKSPACE_ID", "e2-demo-field-eng.cloud.databricks.com")
    assert ss._resolve_workspace_id() is None


def test_workspace_scoping_falls_back_to_the_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing sets the env var, so the SDK is the path that actually runs."""
    monkeypatch.setattr(ss, "_WORKSPACE_ID_CACHE", {}, raising=False)
    monkeypatch.delenv("DATABRICKS_WORKSPACE_ID", raising=False)

    class _FakeClient:
        def get_workspace_id(self) -> int:
            return 2556758628403379

    import agent_server.tools.workspace_client as wc

    monkeypatch.setattr(wc, "app_workspace_client", lambda: _FakeClient())
    assert ss._resolve_workspace_id() == "2556758628403379"


def test_workspace_scoping_degrades_to_account_wide_when_the_sdk_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unavailable is a valid answer; a wrong workspace id would not be."""
    monkeypatch.setattr(ss, "_WORKSPACE_ID_CACHE", {}, raising=False)
    monkeypatch.delenv("DATABRICKS_WORKSPACE_ID", raising=False)

    import agent_server.tools.workspace_client as wc

    def _boom() -> None:
        raise RuntimeError("no credentials")

    monkeypatch.setattr(wc, "app_workspace_client", _boom)
    assert ss._resolve_workspace_id() is None
