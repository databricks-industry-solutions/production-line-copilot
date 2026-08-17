"""The tab and the dashboard must answer the same question the same way.

Two surfaces now report the quality gate: the panel the app draws itself, and
the AI/BI dashboard still deployed beside it and linked from that panel. They
read the same tables over the same window, so the failure worth guarding is not
a crash — it is the two of them quietly disagreeing, which is the one thing a
"can I trust these numbers?" tab cannot afford. The SQL is therefore copied
verbatim and compared here.

The rest is about degrading honestly: an unreadable table has to cost its own
figure and say so, because a zero on this tab reads as "the gate caught
nothing".
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agent_server.serving import quality_metrics as qm

REPO = Path(__file__).resolve().parents[2]
DASHBOARD = REPO / "dashboards" / "data_quality.lvdash.json"


def _dashboard_queries() -> dict[str, str]:
    doc = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    return {
        ds["name"]: "\n".join(ds.get("queryLines") or [])
        for ds in doc.get("datasets", [])
    }


def _normalised(sql: str) -> str:
    """Whitespace is formatting, not meaning."""
    return re.sub(r"\s+", " ", sql).strip()


class TestTheTwoSurfacesAgree:
    def test_every_dashboard_dataset_is_served_by_the_app(self):
        assert set(qm.QUERIES) == set(_dashboard_queries()), (
            "a dataset exists on one surface only, so the tab and the dashboard "
            "answer different questions"
        )

    @pytest.mark.parametrize("dataset", sorted(qm.QUERIES))
    def test_the_sql_is_the_dashboards_sql(self, dataset: str):
        assert _normalised(qm.QUERIES[dataset]) == _normalised(_dashboard_queries()[dataset])

    def test_every_dataset_has_a_label_for_its_warning(self):
        assert set(qm._LABELS) == set(qm.QUERIES), (
            "a dataset that fails would report an unnamed figure"
        )

    def test_rendering_binds_the_catalog_and_schema(self):
        sql = qm.render("hourly_throughput", "cat", "sch")
        assert "cat.sch.machine_state_bronze" in sql
        assert "{{" not in sql, "an unbound token reaches the warehouse as a syntax error"

    def test_the_queries_are_read_only(self):
        """These run as the app service principal, so the guard matters here."""
        forbidden = {"INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "MERGE"}
        for dataset, sql in qm.QUERIES.items():
            upper = re.sub(r"'[^']*'", " ", sql.upper())
            hits = forbidden & set(re.findall(r"[A-Z_]+", upper))
            assert not hits, f"{dataset} contains {hits}"
            assert upper.lstrip().startswith(("SELECT", "WITH"))


class TestOneBadTableDoesNotBlankTheTab:
    def _collect(self, fail: set[str]) -> dict:
        def uc_query(sql: str) -> list[dict]:
            for dataset in qm.QUERIES:
                if qm.render(dataset, "c", "s") == sql:
                    if dataset in fail:
                        raise RuntimeError("TABLE_OR_VIEW_NOT_FOUND: quality_rejects")
                    return [{"dataset": dataset}]
            raise AssertionError(f"unexpected sql: {sql[:80]}")

        return qm.collect(uc_query, "c", "s")

    def test_a_healthy_run_fills_every_panel_and_warns_about_nothing(self):
        out = self._collect(fail=set())
        assert out["warnings"] == []
        assert out["summary"] == {"dataset": "summary_kpis"}
        assert out["tables"] and out["throughput"] and out["qc_failures"]

    def test_a_failed_dataset_is_named_and_the_others_survive(self):
        out = self._collect(fail={"rejects_by_reason_24h"})
        assert out["rejects_by_reason"] == []
        assert len(out["warnings"]) == 1
        assert "rejects by reason" in out["warnings"][0]
        assert "TABLE_OR_VIEW_NOT_FOUND" in out["warnings"][0]
        assert out["summary"] is not None, "an unrelated dataset was dropped too"

    def test_an_empty_summary_stays_none_rather_than_becoming_a_zero(self):
        """No rows means "we could not measure", which is not a 0% pass rate."""

        def uc_query(_sql: str) -> list[dict]:
            return []

        assert qm.collect(uc_query, "c", "s")["summary"] is None


class TestOneScreenSpeaksOneVocabulary:
    """The reconciliation rail sits beside this panel and names the same streams.

    It has always called them "Machine state" and "Inventory"; the quality SQL
    calls them `machine_state` and `inventory_status`. Two vocabularies on one
    tab is the confusing outcome, and the chart axis truncates at 15 characters
    on top of that, so the labels are taken from the rail's own entity list.
    """

    def _labelled(self, dataset: str, column: str, value: str) -> dict:
        def uc_query(sql: str) -> list[dict]:
            if sql == qm.render(dataset, "c", "s"):
                return [{column: value}]
            return []

        key = "tables" if dataset == "table_health_24h" else "rejects_by_reason"
        return qm.collect(uc_query, "c", "s")[key][0]

    def test_a_stream_carries_the_name_the_rail_uses(self):
        from agent_server.serving.reconciliation import ENTITIES

        expected = {e.key: e.label for e in ENTITIES}
        assert expected["inventory_status"] != "inventory_status", "no label to share"
        assert self._labelled("table_health_24h", "table_name", "inventory_status")["label"] == (
            expected["inventory_status"]
        )
        assert self._labelled("rejects_by_reason_24h", "bronze_table", "line_kpi")["label"] == (
            expected["line_kpi"]
        )

    def test_every_label_fits_the_charts_axis(self):
        from agent_server.serving.reconciliation import ENTITIES

        # appkit-ui truncates category labels at 15 characters, with no prop to
        # change it, so a longer label silently becomes "inventory_statu...".
        for e in ENTITIES:
            assert len(e.label) <= 15, f"{e.label!r} will be truncated on the axis"

    def test_an_unknown_stream_keeps_its_raw_name(self):
        """A fifth bronze table should appear, not vanish into an empty cell."""
        row = self._labelled("table_health_24h", "table_name", "telemetry")
        assert row["label"] == "telemetry"


class TestTheWarehouseTypesSurviveJson:
    def test_timestamps_and_decimals_become_json(self):
        from datetime import datetime
        from decimal import Decimal

        def uc_query(_sql: str) -> list[dict]:
            return [{"ts": datetime(2026, 8, 17, 14, 30), "pass_rate": Decimal("0.997")}]

        row = qm.collect(uc_query, "c", "s")["tables"][0]
        assert row["ts"] == "2026-08-17T14:30:00"
        assert row["pass_rate"] == pytest.approx(0.997)
