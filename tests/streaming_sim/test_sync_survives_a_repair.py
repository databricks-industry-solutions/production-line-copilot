"""One UPDATE to a bronze row must not stop the live event feed for six hours.

An operator closed a stuck event in ``event_log_bronze`` by setting
``timestamp_end``. A Delta streaming source rejects a commit that rewrites
files unless told otherwise, so the event_log stream raised
DELTA_SOURCE_TABLE_IGNORE_CHANGES and died — on that cycle and on every cycle
after, because the offset never advanced past the rewrite.

What made it a six-hour outage rather than a one-minute one is that nothing
said so. The reader carried ``ignoreDeletes``, which covers a delete-only
commit and not an update. The cycle summary was logged off the list of streams
the loop had *started*, before any of them had run, so a stream failing every
single cycle appeared in ``drained streams=[...]`` every single cycle. And the
loop awaited ``spark.streams.active``, which a failed query has already left,
so the one log line that would have named it never fired: "terminated with
error" appears zero times in a log with seven consecutive failures in it.

These are source-level assertions on purpose. The behaviour they pin is a
Delta reader option and a control-flow shape, both of which are cheap to
revert by accident and neither of which shows up without a live warehouse.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

SOURCE = pathlib.Path(__file__).resolve().parents[2] / "pipelines" / "streaming_quality_sync.py"


@pytest.fixture(scope="module")
def source() -> str:
    return SOURCE.read_text()


@pytest.fixture(scope="module")
def tree(source):
    return ast.parse(source)


class TestARewriteDoesNotKillTheStream:
    def test_the_reader_tolerates_changed_files(self, source):
        assert '.option("ignoreChanges", "true")' in source

    def test_it_no_longer_relies_on_the_delete_only_option(self, source):
        # ignoreDeletes covers a delete-only commit. An UPDATE is not one, and
        # that is the commit that took the feed down.
        assert '.option("ignoreDeletes"' not in source

    def test_every_delta_reader_carries_it(self, source):
        readers = source.count("spark.readStream.format(\"delta\")")
        assert readers == source.count('.option("ignoreChanges", "true")'), (
            "a reader without ignoreChanges is a stream that dies on the first repair"
        )

    def test_the_sinks_upsert_so_a_re_emitted_row_is_safe(self, source):
        # ignoreChanges re-emits the whole rewritten file. That is only correct
        # because every sink is idempotent on its key — and it is what carries
        # the repair through to the serving table.
        builders = re.findall(r"_build_values_sql\(\s*\n?\s*\"([^\"]+)\"", source)
        assert builders, "expected the sink SQL builders to be discoverable"
        for target in builders:
            idx = source.index(f'"{target}"')
            window = source[idx: idx + 1200]
            assert "ON CONFLICT" in window, f"{target} does not upsert"


class TestTheCycleReportsWhatHappened:
    def test_it_awaits_the_queries_it_started(self, source):
        assert "started_queries" in source

    def test_it_does_not_await_the_active_list(self, source):
        # A query that has already failed is not in spark.streams.active, which
        # is exactly why the failure produced no log line.
        assert "for q in spark.streams.active:" not in source

    def test_a_failure_is_logged_at_error(self, source):
        assert "logger.error(" in source
        assert "FAILED=%s" in source

    def test_the_summary_is_built_from_what_drained_not_what_started(self, source):
        drained_log = source.index("Cycle %d: drained streams=%s skipped=%s")
        await_call = source.index("q.awaitTermination()")
        assert await_call < drained_log, (
            "the summary is logged before the streams have run, which is how a "
            "stream that failed every cycle was reported as drained every cycle"
        )

    def test_the_error_says_the_serving_table_is_now_stale(self, source):
        # The symptom the operator sees is a frozen board, so the log has to
        # connect the dead stream to it.
        assert "stale" in source[source.index("terminated with error"):][:400]

    def test_the_handles_do_not_leak_between_cycles(self, source):
        assert "started_queries.clear()" in source


class TestTheLoopStillHoldsTogether:
    def test_the_module_parses(self, tree):
        assert isinstance(tree, ast.Module)

    def test_any_is_imported_for_the_handle_map(self, source):
        assert "from typing import Any" in source

    def test_a_missing_bronze_table_is_still_skipped_not_failed(self, source):
        assert "bronze table %s not found — skipping" in source
