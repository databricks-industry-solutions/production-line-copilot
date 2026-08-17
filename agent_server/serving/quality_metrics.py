"""The figures behind the Data Integrity tab's quality-gate panel.

These six aggregates used to reach the browser inside an embedded AI/BI
dashboard. The iframe was the fragile part of that tab and the reason the
dashboard had to be published with ``embed_credentials``, which runs its queries
as the *publisher* rather than as the person looking at the page — a quiet
privilege elevation for anyone who could open the app. Serving the same
aggregates from here keeps the app's own permission story: the app service
principal runs six fixed, read-only SELECTs and returns rows nobody can widen
from the client.

The dashboard itself is still deployed and still linked from the tab. It remains
the better tool for exploring this data — cross-filtering, drill-down, export —
and this module deliberately does not try to be that. It answers one question
("did the gate hold in the last 24 hours?") in the page the operator is already
on.

The SQL below is copied verbatim from ``dashboards/data_quality.lvdash.json``,
tokens and all, so the two surfaces cannot answer the same question differently.
``tests/agent_server/test_quality_metrics.py`` compares them and fails on any
divergence, which is why editing a query here without editing the dashboard is a
test failure rather than a discrepancy someone notices in a demo.

Each dataset degrades on its own. One unreadable table costs the panel that one
figure and names itself in ``warnings``; it does not blank the tab, because the
tab's whole purpose is to be trustworthy about what it does and does not know.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)

WINDOW_HOURS = 24

CATALOG_TOKEN = "{{UC_CATALOG}}"
SCHEMA_TOKEN = "{{UC_SCHEMA}}"

# Keyed by the dataset ``name`` in the dashboard file, which is what the drift
# test matches on. Display names live in the dashboard; the UI labels its own
# panels, so they are not duplicated here.
QUERIES: dict[str, str] = {
    "summary_kpis": (
        "WITH bronze AS ( SELECT COUNT(*) AS cnt, MAX(ts) AS latest_ts FROM "
        "{{UC_CATALOG}}.{{UC_SCHEMA}}.machine_state_bronze WHERE ts >= dateadd(hour, -24, "
        "current_timestamp()) UNION ALL SELECT COUNT(*), MAX(ts) FROM "
        "{{UC_CATALOG}}.{{UC_SCHEMA}}.line_kpi_bronze WHERE ts >= dateadd(hour, -24, "
        "current_timestamp()) UNION ALL SELECT COUNT(*), MAX(timestamp_start) FROM "
        "{{UC_CATALOG}}.{{UC_SCHEMA}}.event_log_bronze WHERE timestamp_start >= dateadd(hour, "
        "-24, current_timestamp()) UNION ALL SELECT COUNT(*), MAX(ts) FROM "
        "{{UC_CATALOG}}.{{UC_SCHEMA}}.inventory_status_bronze WHERE ts >= dateadd(hour, -24, "
        "current_timestamp()) ), rejects AS ( SELECT COUNT(*) AS cnt FROM "
        "{{UC_CATALOG}}.{{UC_SCHEMA}}.quality_rejects WHERE rejected_at >= dateadd(hour, -24, "
        "current_timestamp()) ) SELECT CAST(SUM(b.cnt) AS BIGINT) AS bronze_rows_24h, "
        "CAST((SELECT cnt FROM rejects) AS BIGINT) AS dropped_rows_24h, CASE WHEN SUM(b.cnt) > 0 "
        "THEN CAST(SUM(b.cnt) - (SELECT cnt FROM rejects) AS DOUBLE) / SUM(b.cnt) ELSE 1.0 END "
        "AS pass_rate, MAX(b.latest_ts) AS latest_gold_ts FROM bronze b"
    ),
    "table_health_24h": (
        "WITH bronze AS ( SELECT 'machine_state' AS table_name, COUNT(*) AS cnt, MAX(ts) AS "
        "latest_ts FROM {{UC_CATALOG}}.{{UC_SCHEMA}}.machine_state_bronze WHERE ts >= "
        "dateadd(hour, -24, current_timestamp()) UNION ALL SELECT 'line_kpi', COUNT(*), MAX(ts) "
        "FROM {{UC_CATALOG}}.{{UC_SCHEMA}}.line_kpi_bronze WHERE ts >= dateadd(hour, -24, "
        "current_timestamp()) UNION ALL SELECT 'event_log', COUNT(*), MAX(timestamp_start) FROM "
        "{{UC_CATALOG}}.{{UC_SCHEMA}}.event_log_bronze WHERE timestamp_start >= dateadd(hour, "
        "-24, current_timestamp()) UNION ALL SELECT 'inventory_status', COUNT(*), MAX(ts) FROM "
        "{{UC_CATALOG}}.{{UC_SCHEMA}}.inventory_status_bronze WHERE ts >= dateadd(hour, -24, "
        "current_timestamp()) ), rejects AS ( SELECT bronze_table AS table_name, COUNT(*) AS cnt "
        "FROM {{UC_CATALOG}}.{{UC_SCHEMA}}.quality_rejects WHERE rejected_at >= dateadd(hour, "
        "-24, current_timestamp()) GROUP BY bronze_table ) SELECT b.table_name, CAST(b.cnt AS "
        "BIGINT) AS bronze_rows, CAST(coalesce(r.cnt, 0) AS BIGINT) AS dropped_rows, CASE WHEN "
        "b.cnt > 0 THEN CAST(b.cnt - coalesce(r.cnt, 0) AS DOUBLE) / b.cnt ELSE 1.0 END AS "
        "pass_rate, b.latest_ts, CAST((unix_timestamp(current_timestamp()) - "
        "unix_timestamp(b.latest_ts)) / 60.0 AS DOUBLE) AS lag_minutes FROM bronze b LEFT JOIN "
        "rejects r ON b.table_name = r.table_name ORDER BY b.table_name"
    ),
    "hourly_throughput": (
        "SELECT date_trunc('HOUR', ts) AS hour, CAST(COUNT(*) AS BIGINT) AS row_count FROM "
        "{{UC_CATALOG}}.{{UC_SCHEMA}}.machine_state_bronze WHERE ts >= dateadd(hour, -24, "
        "current_timestamp()) GROUP BY 1 ORDER BY 1"
    ),
    "status_dist_5m": (
        "WITH latest AS ( SELECT machine_id, status, ts, ROW_NUMBER() OVER (PARTITION BY "
        "machine_id ORDER BY ts DESC) AS rn FROM "
        "{{UC_CATALOG}}.{{UC_SCHEMA}}.machine_state_bronze WHERE ts >= dateadd(minute, -5, "
        "current_timestamp()) ) SELECT status, CAST(COUNT(DISTINCT machine_id) AS BIGINT) AS "
        "machines FROM latest WHERE rn = 1 GROUP BY status ORDER BY machines DESC"
    ),
    "rejects_by_reason_24h": (
        "SELECT bronze_table, rejected_reason, CAST(COUNT(*) AS BIGINT) AS rejected_rows FROM "
        "{{UC_CATALOG}}.{{UC_SCHEMA}}.quality_rejects WHERE rejected_at >= dateadd(hour, -24, "
        "current_timestamp()) GROUP BY bronze_table, rejected_reason ORDER BY rejected_rows DESC "
        "LIMIT 50"
    ),
    "qc_failures_24h": (
        "SELECT timestamp AS ts, machine_id, parameter, value, spec_min, spec_max, result FROM "
        "{{UC_CATALOG}}.{{UC_SCHEMA}}.quality_checks_bronze WHERE lower(result) = 'fail' AND "
        "timestamp >= dateadd(hour, -24, current_timestamp()) ORDER BY timestamp DESC LIMIT 50"
    ),
}

# What a failed dataset costs the reader, in the words the panel uses. Keeping
# them here rather than in the client means the warning names the missing figure
# rather than the query id, which is the only part an operator can act on.
_LABELS: dict[str, str] = {
    "summary_kpis": "pass rate and row totals",
    "table_health_24h": "per-table health",
    "hourly_throughput": "hourly throughput",
    "status_dist_5m": "current status mix",
    "rejects_by_reason_24h": "rejects by reason",
    "qc_failures_24h": "QC failures",
}

UcQuery = Callable[[str], list[dict]]


def _stream_labels() -> dict[str, str]:
    """"machine_state" -> "Machine state", from the reconciliation entity list.

    The reconciliation rail sits beside this panel on the same tab and has
    always named these four streams. Deriving the labels from it rather than
    writing them again keeps one screen speaking one vocabulary, and the
    chart's category axis truncates at 15 characters — which "inventory_status"
    exceeds and "Inventory" does not.
    """
    from agent_server.serving.reconciliation import ENTITIES

    return {e.key: e.label for e in ENTITIES}


def render(dataset: str, catalog: str, schema: str) -> str:
    """The dashboard's SQL, bound to this deployment's catalog and schema."""
    return QUERIES[dataset].replace(CATALOG_TOKEN, catalog).replace(SCHEMA_TOKEN, schema)


def _plain(value: Any) -> Any:
    """Types the warehouse returns that JSON does not have.

    Timestamps become ISO strings and decimals become floats, here rather than
    in the response layer, so the shape a test asserts on is the shape the
    browser receives.
    """
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _rows(uc_query: UcQuery, dataset: str, catalog: str, schema: str) -> list[dict]:
    return [
        {k: _plain(v) for k, v in row.items()}
        for row in uc_query(render(dataset, catalog, schema))
    ]


def collect(uc_query: UcQuery, catalog: str, schema: str) -> dict:
    """Run all six datasets, letting each one fail on its own.

    A partial answer is the useful one here. If ``quality_rejects`` is
    unreadable but the bronze tables are not, "rejects by reason is missing"
    beats an empty tab, and it beats a zero — a zero on this tab reads as "the
    gate caught nothing", which is the opposite of "we could not look".
    """
    payload: dict[str, Any] = {
        "window_hours": WINDOW_HOURS,
        "summary": None,
        "tables": [],
        "throughput": [],
        "status_distribution": [],
        "rejects_by_reason": [],
        "qc_failures": [],
        "warnings": [],
    }
    into: dict[str, str] = {
        "table_health_24h": "tables",
        "hourly_throughput": "throughput",
        "status_dist_5m": "status_distribution",
        "rejects_by_reason_24h": "rejects_by_reason",
        "qc_failures_24h": "qc_failures",
    }

    labels = _stream_labels()
    # The column holding a stream name, per dataset that has one.
    named_by = {"table_health_24h": "table_name", "rejects_by_reason_24h": "bronze_table"}

    for dataset in QUERIES:
        try:
            rows = _rows(uc_query, dataset, catalog, schema)
        except Exception as e:  # noqa: BLE001 - one dataset must not take the tab down
            logger.warning("quality metrics: %s failed: %s", dataset, e)
            payload["warnings"].append(f"{_LABELS[dataset]} unavailable: {str(e)[:160]}")
            continue
        if (column := named_by.get(dataset)) is not None:
            for row in rows:
                row["label"] = labels.get(row.get(column), row.get(column))
        if dataset == "summary_kpis":
            payload["summary"] = rows[0] if rows else None
        else:
            payload[into[dataset]] = rows

    return payload
