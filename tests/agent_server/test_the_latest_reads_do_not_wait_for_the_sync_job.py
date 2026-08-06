"""The "latest" reads must not be gated by the quality-sync job's cycle.

Two writers land live data in Lakebase at very different rates. The producer
dual-writes ``ai_serving.*_history`` at 1 Hz. The streaming quality-sync job
upserts ``ai_serving.*_latest`` (and ``inventory_status``) once per
``cycle_sleep_s`` pass, which on serverless is 60 s at the default
``TRIGGER_MILLIS`` plus per-cycle spin-up.

Reading the sync-job tables here cost roughly a minute of staleness on two
user-visible paths: SSE warmup painted a minute-old dashboard before the
playback cursor took over, and the agent's context bundle quoted a KPI row
that contradicted the tiles rendered beside it. These tests pin the reads to
the producer-written history tables so that gap cannot come back.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent_server.serving import realtime_store as rs

# Written only by pipelines/streaming_quality_sync.py, on its cycle loop.
SYNC_JOB_TABLES = (
    "ai_serving.machine_state_latest",
    "ai_serving.line_kpi_latest",
    "ai_serving.inventory_status",
)


@pytest.fixture()
def captured(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Collect the SQL each reader issues instead of touching Postgres."""
    sql: list[str] = []

    def fake_query(q: str, params=None, fetch: str = "all"):
        sql.append(" ".join(q.split()))
        return None if fetch == "one" else []

    monkeypatch.setattr(rs, "query", fake_query)
    return sql


def test_machine_state_reads_the_producer_history(captured: list[str]) -> None:
    rs.read_machine_state_latest("shift_1")
    assert len(captured) == 1
    assert "ai_serving.machine_state_history" in captured[0]
    assert "ai_serving.machine_state_latest" not in captured[0]


def test_machine_state_collapses_to_one_row_per_machine(captured: list[str]) -> None:
    rs.read_machine_state_latest("shift_1")
    sql = captured[0]
    # DISTINCT ON only returns the newest row per machine if the sort agrees.
    assert "DISTINCT ON (machine_id)" in sql
    assert "ORDER BY machine_id, ts DESC" in sql


def test_line_kpi_reads_the_producer_history(captured: list[str]) -> None:
    rs.read_line_kpi_latest("shift_1")
    sql = captured[0]
    assert "ai_serving.line_kpi_history" in sql
    assert "ai_serving.line_kpi_latest" not in sql
    assert "ORDER BY ts DESC" in sql
    assert "LIMIT 1" in sql


def test_inventory_reads_the_producer_history(captured: list[str]) -> None:
    rs.read_inventory_latest("shift_1")
    sql = captured[0]
    assert "ai_serving.inventory_status_history" in sql
    assert "DISTINCT ON (material_id)" in sql
    assert "ORDER BY material_id, ts DESC" in sql


@pytest.mark.parametrize(
    "reader",
    [rs.read_machine_state_latest, rs.read_line_kpi_latest, rs.read_inventory_latest],
)
def test_no_latest_read_touches_a_sync_job_table(reader, captured: list[str]) -> None:
    reader("shift_1")
    for sql in captured:
        for table in SYNC_JOB_TABLES:
            # "inventory_status" is a prefix of "inventory_status_history", so
            # match on the word boundary the FROM clause actually produces.
            assert f"FROM {table} " not in f"{sql} ", f"{reader.__name__} reads {table}"


@pytest.mark.parametrize(
    "reader",
    [rs.read_machine_state_latest, rs.read_line_kpi_latest, rs.read_inventory_latest],
)
def test_a_dead_producers_rows_are_not_called_latest(reader, captured: list[str]) -> None:
    # *_history accumulates across producer runs, so without a wall-clock guard
    # "newest row" could be yesterday's. Callers treat an empty result as
    # "waiting for fresh data", which is the honest answer.
    reader("shift_1", recent_seconds=42)
    assert "NOW() - INTERVAL '42 seconds'" in captured[0]


@pytest.mark.parametrize(
    "reader",
    [rs.read_machine_state_latest, rs.read_line_kpi_latest, rs.read_inventory_latest],
)
def test_the_recency_window_defaults_to_the_cursor_window(reader, captured: list[str]) -> None:
    reader("shift_1")
    assert f"NOW() - INTERVAL '{rs.LATEST_RECENT_SECONDS} seconds'" in captured[0]


class TestTheEventFeedReadsBothSuppliers:
    """A triggered incident reached the line tiles in a second and the feed
    beside them a minute later, because only the sync job writes
    ``event_log_recent`` while the producer dual-writes ``event_log_history``.
    """

    def test_it_reads_the_producers_table(self, captured: list[str]) -> None:
        rs.read_event_log_recent("shift_1")
        assert "ai_serving.event_log_history" in captured[0]

    def test_it_still_reads_the_bronze_taxonomy(self, captured: list[str]) -> None:
        # Micro stops, quality holds, changeovers and CIP cycles are never
        # dual-written; dropping this table would drop them from the feed.
        rs.read_event_log_recent("shift_1")
        assert "ai_serving.event_log_recent" in captured[0]

    def test_a_dual_written_event_appears_once(self, captured: list[str]) -> None:
        rs.read_event_log_recent("shift_1")
        assert "DISTINCT ON (event_id)" in captured[0]

    def test_the_closed_copy_wins(self, captured: list[str]) -> None:
        # Bronze can land a row already carrying its end while the producer's
        # live copy still has it open. Showing the open one reopens the event.
        rs.read_event_log_recent("shift_1")
        assert "(timestamp_end IS NOT NULL) DESC" in captured[0]

    def test_the_feed_stays_newest_first(self, captured: list[str]) -> None:
        rs.read_event_log_recent("shift_1")
        assert "ORDER BY timestamp_start DESC NULLS LAST" in captured[0]

    @pytest.mark.parametrize("limit,expected", [(0, 1), (5, 5), (99999, 1000)])
    def test_the_limit_is_still_clamped(
        self, limit: int, expected: int, captured: list[str]
    ) -> None:
        rs.read_event_log_recent("shift_1", limit=limit)
        assert f"LIMIT {expected}" in captured[0]

    def test_both_union_branches_are_scoped_to_the_shift(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Every environment shares one Lakebase database, so an unscoped branch
        # leaks the other environment's board into this one.
        seen: dict[str, Any] = {}

        def fake_query(q, params=None, fetch="all"):
            seen["sql"], seen["params"] = " ".join(q.split()), params
            return []

        monkeypatch.setattr(rs, "query", fake_query)
        rs.read_event_log_recent("shift_1_dev")
        assert seen["params"] == ("shift_1_dev", "shift_1_dev")
        assert seen["sql"].count("WHERE shift_id = %s") == 2
