"""Tests for /ops/quality-metrics, the endpoint behind the quality-gate panel.

This endpoint exists because the panel used to be an embedded AI/BI dashboard.
The properties worth pinning are the ones that made the iframe worth replacing:
the app answers with its own service principal, six fixed queries and nothing a
caller can widen; a cold warehouse produces a message rather than a hang; and
repeated reloads do not re-run the fan-out.
"""
from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch) -> Iterator[TestClient]:
    from agent_server import start_server as ss

    monkeypatch.setattr(ss, "_startup_sql_probe", lambda: None, raising=False)
    monkeypatch.setenv("UC_CATALOG", "cat")
    monkeypatch.setenv("UC_SCHEMA", "sch")
    with TestClient(ss.app) as c:
        yield c


@pytest.fixture(autouse=True)
def empty_cache():
    from agent_server import start_server as ss

    ss._QUALITY_METRICS_CACHE.clear()
    yield
    ss._QUALITY_METRICS_CACHE.clear()


def _served(monkeypatch, rows_for=lambda sql: [{"ok": 1}]) -> list[str]:
    """Point the endpoint at a fake warehouse and record what it asked for."""
    from agent_server import start_server as ss

    seen: list[str] = []

    def fake_query(sql: str) -> list[dict]:
        seen.append(sql)
        return rows_for(sql)

    monkeypatch.setattr(ss, "_reconciliation_uc_query", fake_query)
    return seen


class TestItAnswersFromTheWarehouse:
    def test_it_runs_the_six_dashboard_queries_and_nothing_else(self, client, monkeypatch):
        from agent_server.serving import quality_metrics as qm

        seen = _served(monkeypatch)
        body = client.get("/ops/quality-metrics").json()

        assert len(seen) == len(qm.QUERIES) == 6
        assert set(seen) == {qm.render(d, "cat", "sch") for d in qm.QUERIES}
        assert body["warnings"] == []
        assert body["window_hours"] == 24

    def test_the_queries_are_bound_to_this_deployments_schema(self, client, monkeypatch):
        seen = _served(monkeypatch)
        client.get("/ops/quality-metrics")
        assert all("cat.sch." in sql for sql in seen)

    def test_an_unconfigured_catalog_says_so_instead_of_querying(self, client, monkeypatch):
        seen = _served(monkeypatch)
        monkeypatch.delenv("UC_CATALOG", raising=False)
        res = client.get("/ops/quality-metrics")
        assert res.status_code == 503
        assert "UC_CATALOG" in res.json()["error"]
        assert seen == []


class TestItDegradesInsteadOfDisappearing:
    def test_one_unreadable_table_costs_one_figure(self, client, monkeypatch):
        from agent_server.serving import quality_metrics as qm

        def rows_for(sql: str):
            if sql == qm.render("qc_failures_24h", "cat", "sch"):
                raise RuntimeError("PERMISSION_DENIED: quality_checks_bronze")
            return [{"ok": 1}]

        _served(monkeypatch, rows_for)
        body = client.get("/ops/quality-metrics").json()

        assert body["qc_failures"] == []
        assert any("QC failures unavailable" in w for w in body["warnings"])
        assert body["tables"], "an unrelated dataset was dropped too"

    def test_a_wedged_warehouse_is_reported_not_raised(self, client, monkeypatch):
        from agent_server import start_server as ss

        monkeypatch.setattr(
            ss, "_reconciliation_uc_query",
            lambda _sql: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        # Every dataset failing is still a 200 with six warnings: the panel says
        # which figures are missing, which beats a bare error page.
        body = client.get("/ops/quality-metrics").json()
        assert len(body["warnings"]) == 6
        assert body["summary"] is None


class TestItDoesNotHammerTheWarehouse:
    def test_a_second_call_is_served_from_cache(self, client, monkeypatch):
        seen = _served(monkeypatch)
        assert client.get("/ops/quality-metrics").json()["cached"] is False
        assert client.get("/ops/quality-metrics").json()["cached"] is True
        assert len(seen) == 6, "the fan-out ran twice"

    def test_a_degraded_reading_is_cached_too(self, client, monkeypatch):
        """A warehouse that just refused a table will refuse it again."""
        seen = _served(monkeypatch, lambda _sql: (_ for _ in ()).throw(RuntimeError("no")))
        client.get("/ops/quality-metrics")
        client.get("/ops/quality-metrics")
        assert len(seen) == 6
