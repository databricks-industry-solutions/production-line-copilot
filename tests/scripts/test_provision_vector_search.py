"""The index and the tool that reads it are two files that must agree.

Vector Search fails the whole query if you ask for a column the index does not
carry — there is no partial result and no warning. So the tool's column list,
the provisioner's `columns_to_sync` and the primary key are a contract split
across two files, and nothing at runtime checks it before a user does.

These tests are that check. They are cheap and they encode the specific way
this feature was broken before: it named an index nobody built, over columns
the source table did not have.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from agent_server.tools.similar_case_retriever import _DEFAULT_INDEX, _INDEX_COLUMNS

ROOT = Path(__file__).resolve().parents[2]


def _load_provisioner():
    spec = importlib.util.spec_from_file_location(
        "provision_vector_search", ROOT / "scripts" / "provision_vector_search.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def provisioner():
    return _load_provisioner()


class TestTheIndexCarriesWhatTheToolAsksFor:
    def test_every_column_the_tool_reads_is_synced(self, provisioner):
        missing = set(_INDEX_COLUMNS) - set(provisioner.SYNCED_COLUMNS)
        assert not missing, (
            f"retrieve_similar_cases reads {sorted(missing)} but the index does not "
            "sync them — similarity_search would fail outright, not degrade"
        )

    def test_the_tool_and_the_provisioner_name_the_same_index(self, provisioner):
        assert _DEFAULT_INDEX == provisioner.DEFAULT_INDEX

    def test_the_outcome_columns_are_carried(self, provisioner):
        """The reason the curated source table exists at all."""
        for column in ("resolution_minutes", "outcome_oee_impact", "outcome_description"):
            assert column in provisioner.SYNCED_COLUMNS

    def test_the_embedded_column_is_synced_but_not_read_back(self, provisioner):
        """`case_text` is concatenated narrative — useful to embed, noise to return."""
        assert "case_text" in provisioner.SYNCED_COLUMNS
        assert "case_text" not in _INDEX_COLUMNS


class TestTheSourceTableIsBuildable:
    def test_the_primary_key_is_a_synced_column(self, provisioner):
        assert "event_id" in provisioner.SYNCED_COLUMNS

    def test_change_data_feed_is_enabled_on_the_source(self, provisioner):
        """A delta-sync index cannot follow a table without the feed."""
        captured: list[str] = []

        class FakeWarehouse:
            def run(self, sql: str):
                captured.append(sql)
                return [[27]]

            def table_exists(self, fqn: str) -> bool:
                return True

        provisioner.build_retrieval_table(FakeWarehouse(), "cat.sch", dry_run=False)
        ddl = captured[0]
        assert "delta.enableChangeDataFeed = true" in ddl
        assert "PRIMARY KEY (event_id)" in ddl

    def test_the_source_joins_bronze_for_the_outcome_columns(self, provisioner):
        """Gold alone drops them, which is what made the old index unbuildable."""
        captured: list[str] = []

        class FakeWarehouse:
            def run(self, sql: str):
                captured.append(sql)
                return [[27]]

            def table_exists(self, fqn: str) -> bool:
                return True

        provisioner.build_retrieval_table(FakeWarehouse(), "cat.sch", dry_run=False)
        merge = captured[1]
        assert "gold_history_event_log" in merge
        assert "event_log_bronze" in merge
        assert "MERGE INTO" in merge, "CREATE OR REPLACE would resync the whole corpus"

    def test_an_empty_case_library_is_reported_not_indexed(self, provisioner, capsys):
        """An index over zero rows is worse than none: it bills and returns nothing."""
        class EmptyWarehouse:
            def run(self, sql: str):
                return [[0]]

            def table_exists(self, fqn: str) -> bool:
                return True

        rows = provisioner.build_retrieval_table(EmptyWarehouse(), "cat.sch", dry_run=False)
        assert rows == 0

    def test_dry_run_touches_nothing(self, provisioner):
        class ExplodingWarehouse:
            def run(self, sql: str):
                raise AssertionError("dry-run must not execute SQL")

            def table_exists(self, fqn: str) -> bool:
                raise AssertionError("dry-run must not query the catalog")

        assert provisioner.build_retrieval_table(ExplodingWarehouse(), "cat.sch", dry_run=True) == -1


class TestTheDeployDoesNotRaceTheStreamingJob:
    """gold_history_event_log is created by the quality-sync job, not the deploy.

    Both start at roughly the same moment on a fresh workspace and the deploy
    usually wins, so the MERGE hit TABLE_OR_VIEW_NOT_FOUND and took down the
    whole provisioning run — including the endpoint and index, which take 15-40
    minutes to build and are the only part that matters. The case history is
    enrichment and can arrive on any later run.
    """

    class _NoGoldWarehouse:
        def __init__(self):
            self.statements: list[str] = []

        def run(self, sql: str):
            self.statements.append(sql)
            return [[0]]

        def table_exists(self, fqn: str) -> bool:
            return not fqn.endswith("gold_history_event_log")

    def test_a_missing_gold_table_does_not_abort_provisioning(self, provisioner):
        wh = self._NoGoldWarehouse()
        rows = provisioner.build_retrieval_table(wh, "cat.sch", dry_run=False)
        assert rows == 0, "provisioning should return a count, not raise"

    def test_the_merge_is_skipped_rather_than_attempted(self, provisioner):
        wh = self._NoGoldWarehouse()
        provisioner.build_retrieval_table(wh, "cat.sch", dry_run=False)
        assert not any("MERGE INTO" in s for s in wh.statements), (
            "the MERGE ran against a table that does not exist yet"
        )

    def test_the_source_table_is_still_created(self, provisioner):
        """The index needs its source to exist even before there is history."""
        wh = self._NoGoldWarehouse()
        provisioner.build_retrieval_table(wh, "cat.sch", dry_run=False)
        assert any("CREATE TABLE IF NOT EXISTS" in s for s in wh.statements)

    def test_the_operator_is_told_why_and_what_to_do(self, provisioner, capsys):
        provisioner.build_retrieval_table(self._NoGoldWarehouse(), "cat.sch", dry_run=False)
        out = capsys.readouterr().out
        assert "gold_history_event_log" in out
        assert "quality-sync" in out, "the message should name the job that creates it"
        assert "re-run" in out.lower(), "the operator needs to know this is recoverable"
