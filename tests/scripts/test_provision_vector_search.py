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
        # By content, not position: the builder also runs a duplicate check and a
        # count, and pinning an index made this fail for adding a statement.
        merge = next(s for s in captured if "MERGE INTO" in s)
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


class TestOneRowPerEventReachesTheIndex:
    """gold keeps a snapshot per stage of an event; the index keeps one row per key.

    A single incident appears in `gold_history_event_log` two or three times as
    it progresses, and those snapshots carry *different* root causes and
    corrective actions — the early theory and the eventual finding. Feeding all
    of them to a MERGE keyed on `event_id` did two bad things: the first run
    inserted every snapshot (nothing matches an empty target, and Delta does not
    enforce the declared primary key), leaving the index to keep whichever one it
    happened to see; and the second run died on
    DELTA_MULTIPLE_SOURCE_ROW_MATCHING_TARGET_ROW_IN_MERGE, so a script whose
    docstring promises it is "safe on every deploy" could be run exactly once.

    Observed in prod before the fix: 103 library rows for 44 events.
    """

    class _Warehouse:
        def __init__(self, dupes=0):
            self.statements: list[str] = []
            self._dupes = dupes

        def run(self, sql: str):
            self.statements.append(sql)
            if "HAVING count(*) > 1" in sql and sql.strip().upper().startswith("SELECT"):
                return [[self._dupes]]
            return [[44]]

        def table_exists(self, fqn: str) -> bool:
            return True

    def test_the_source_is_reduced_to_one_row_per_event(self, provisioner):
        wh = self._Warehouse()
        provisioner.build_retrieval_table(wh, "cat.sch", dry_run=False)
        merge = next(s for s in wh.statements if "MERGE INTO" in s)
        assert "ROW_NUMBER() OVER" in merge
        assert "PARTITION BY e.event_id" in merge
        assert "rn = 1" in merge

    def test_the_latest_closed_snapshot_wins(self, provisioner):
        """A resolved event is the useful one; ordering must prefer it."""
        wh = self._Warehouse()
        provisioner.build_retrieval_table(wh, "cat.sch", dry_run=False)
        merge = next(s for s in wh.statements if "MERGE INTO" in s)
        order = merge[merge.index("ORDER BY"):merge.index("rn = 1")]
        assert "e.timestamp_end DESC NULLS LAST" in order
        # Deterministic tie-break, so re-running does not churn the index.
        assert order.count("DESC NULLS LAST") >= 3

    def test_a_library_left_duplicated_by_older_runs_is_rebuilt(self, provisioner):
        wh = self._Warehouse(dupes=35)
        provisioner.build_retrieval_table(wh, "cat.sch", dry_run=False)
        deletes = [s for s in wh.statements if s.strip().upper().startswith("DELETE")]
        assert deletes, "duplicated events must be cleared; MERGE cannot repair them"
        assert "HAVING count(*) > 1" in deletes[0], "only the duplicated events"

    def test_a_clean_library_is_not_deleted_from(self, provisioner):
        wh = self._Warehouse(dupes=0)
        provisioner.build_retrieval_table(wh, "cat.sch", dry_run=False)
        assert not [s for s in wh.statements if s.strip().upper().startswith("DELETE")]


class TestAnExistingIndexIsAskedToSync:
    """TRIGGERED indexes sync when told to and never otherwise.

    `ensure_index` used to see an existing index, print that it existed, and
    return — so every run after the first refreshed the case library and left the
    index frozen at whatever it captured on creation. Dev drifted to 24 indexed
    rows against a 37-row library while `retrieve_similar_cases` went on
    reporting `retrieval: vector_search`, which reads as the healthy path.
    """

    class _Indexes:
        def __init__(self, exists=True):
            self._exists = exists
            self.synced: list[str] = []
            self.created: list[str] = []

        def get_index(self, fqn):
            if not self._exists:
                raise RuntimeError("not found")
            return type("I", (), {"status": type("S", (), {
                "ready": True, "indexed_row_count": 24})()})()

        def sync_index(self, index_name):
            self.synced.append(index_name)

        def create_index(self, **kw):
            self.created.append(kw["name"])

    def _client(self, indexes):
        return type("W", (), {"vector_search_indexes": indexes})()

    def test_an_existing_index_is_synced(self, provisioner):
        idx = self._Indexes(exists=True)
        assert provisioner.ensure_index(
            self._client(idx), "ep", "cat.sch.event_case_index",
            "cat.sch.event_case_library", "emb", dry_run=False) is True
        assert idx.synced == ["cat.sch.event_case_index"], "a stale index stays stale"
        assert not idx.created

    def test_dry_run_does_not_sync(self, provisioner):
        idx = self._Indexes(exists=True)
        provisioner.ensure_index(self._client(idx), "ep", "i", "s", "emb", dry_run=True)
        assert idx.synced == []

    def test_a_sync_already_running_is_not_fatal(self, provisioner):
        class Busy(self._Indexes.__mro__[0]):
            def sync_index(self, index_name):
                raise RuntimeError("sync already in progress")

        idx = Busy(exists=True)
        assert provisioner.ensure_index(
            self._client(idx), "ep", "i", "s", "emb", dry_run=False) is True

    def test_a_missing_index_is_created_not_synced(self, provisioner):
        idx = self._Indexes(exists=False)
        provisioner.ensure_index(
            self._client(idx), "ep", "cat.sch.event_case_index",
            "cat.sch.event_case_library", "emb", dry_run=False)
        assert idx.created == ["cat.sch.event_case_index"]
        assert idx.synced == [], "creation performs the first sync itself"


class TestTheIndexHasToBeOnTheEndpointTheAppQueries:
    """Index names are unique per metastore, not per endpoint.

    Measured while moving dev between endpoints: an index left on the old
    endpoint answered `get_index` perfectly well, so the deploy reported it as
    provisioned on the configured endpoint, the app queried an endpoint holding
    nothing, and `retrieve_similar_cases` reported `retrieval: vector_search` —
    the healthy path — over an empty result. An index cannot be re-pointed.
    """

    class _Indexes:
        def __init__(self, served_from: str | None):
            self.served_from = served_from
            self.synced: list[str] = []
            self.created: list[tuple[str, str]] = []
            self.deleted: list[str] = []

        def get_index(self, fqn):
            if self.served_from is None:
                raise RuntimeError("not found")
            return type("I", (), {
                "endpoint_name": self.served_from,
                "status": type("S", (), {"ready": True, "indexed_row_count": 24})(),
            })()

        def delete_index(self, index_name):
            self.deleted.append(index_name)
            self.served_from = None

        def create_index(self, **kw):
            self.created.append((kw["name"], kw["endpoint_name"]))

        def sync_index(self, index_name):
            self.synced.append(index_name)

    def _client(self, indexes):
        return type("W", (), {"vector_search_indexes": indexes})()

    def test_an_index_on_another_endpoint_is_rebuilt_where_it_is_needed(self, provisioner):
        idx = self._Indexes(served_from="the-old-endpoint")
        provisioner.ensure_index(
            self._client(idx), "the-configured-endpoint", "cat.sch.event_case_index",
            "cat.sch.event_case_library", "emb", dry_run=False)
        assert idx.deleted == ["cat.sch.event_case_index"]
        assert idx.created == [("cat.sch.event_case_index", "the-configured-endpoint")]
        assert idx.synced == [], "syncing it would have left it on the wrong endpoint"

    def test_an_index_already_in_the_right_place_is_only_synced(self, provisioner):
        idx = self._Indexes(served_from="the-configured-endpoint")
        provisioner.ensure_index(
            self._client(idx), "the-configured-endpoint", "cat.sch.event_case_index",
            "cat.sch.event_case_library", "emb", dry_run=False)
        assert idx.deleted == [] and idx.created == []
        assert idx.synced == ["cat.sch.event_case_index"]

    def test_a_dry_run_deletes_nothing(self, provisioner, capsys):
        idx = self._Indexes(served_from="the-old-endpoint")
        provisioner.ensure_index(
            self._client(idx), "the-configured-endpoint", "cat.sch.event_case_index",
            "cat.sch.event_case_library", "emb", dry_run=True)
        assert idx.deleted == [] and idx.created == []
        assert "the-old-endpoint" in capsys.readouterr().out, (
            "a dry run is where you want to be told the index is in the wrong place"
        )
