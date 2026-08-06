"""The Lakebase schema has to exist before anything tries to write to it.

Two failures compounded on a fresh workspace:

1. ``_INIT_SQL`` was split on ``;`` with no regard for comments, so the line
   "-- polls by status every tick; a covering index avoids a seq scan" produced
   an empty fragment and a syntax error, and six of twenty-seven fragments were
   junk.

2. Every statement shared one try/except. The app service principal holds
   CREATE on ai_serving and ai_chatbot but not on the *database*, so the
   leading ``CREATE SCHEMA IF NOT EXISTS`` raised "permission denied for
   database" — and took the twenty-five CREATE TABLEs after it down too. The
   handler logged "Could not auto-initialize Lakebase tables (may already
   exist)" and dropped the exception, so nothing said what went wrong.

The visible result was an empty ai_serving, which meant the streaming job died
on its first micro-batch with `relation "ai_serving.event_log_recent" does not
exist`, no gold_history tables were ever written, and the UI reported a line
with zero machines. Nothing logged an error above WARNING.
"""
from __future__ import annotations

import logging

import pytest

from agent_server.memory import pg_pool


class TestTheSplitterSurvivesComments:
    def test_no_fragment_is_junk(self):
        statements = list(pg_pool.iter_statements(pg_pool._INIT_SQL))
        assert statements, "the init SQL produced no statements at all"
        for stmt in statements:
            assert stmt.upper().startswith(
                ("CREATE", "ALTER", "INSERT", "DO", "COMMENT", "GRANT", "DROP")
            ), f"not an executable statement: {stmt[:80]!r}"

    def test_a_semicolon_inside_a_comment_does_not_split(self):
        sql = """
        -- polls by status every tick; a covering index avoids a seq scan
        CREATE TABLE a (id INT);
        """
        assert list(pg_pool.iter_statements(sql)) == ["CREATE TABLE a (id INT)"]

    def test_trailing_comments_do_not_become_empty_statements(self):
        sql = "CREATE TABLE a (id INT); -- done\n"
        assert list(pg_pool.iter_statements(sql)) == ["CREATE TABLE a (id INT)"]

    def test_the_real_init_sql_still_defines_what_the_stream_writes(self):
        """Named explicitly: these are the tables foreachBatch dual-writes to."""
        sql = pg_pool._INIT_SQL
        for table in (
            "ai_serving.event_log_recent",
            "ai_serving.machine_state_latest",
            "ai_serving.line_kpi_latest",
            "ai_serving.inventory_status",
        ):
            assert table in sql, f"{table} is written by the streaming job but never created"


class _Cursor:
    """Rejects CREATE SCHEMA the way the app SP's role does."""

    def __init__(self, reject: str = "CREATE SCHEMA"):
        self.executed: list[str] = []
        self._reject = reject

    def execute(self, stmt: str):
        if stmt.upper().startswith(self._reject):
            raise RuntimeError("permission denied for database databricks_postgres")
        self.executed.append(stmt)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Conn:
    def __init__(self, cursor):
        self.autocommit = False
        self._cursor = cursor

    def cursor(self):
        return self._cursor


@pytest.fixture(autouse=True)
def _reset_init_flag():
    pg_pool._tables_initialized = False
    yield
    pg_pool._tables_initialized = False


class TestOneForbiddenStatementDoesNotCostTheRest:
    def test_the_tables_are_created_even_when_create_schema_is_denied(self):
        cur = _Cursor()
        pg_pool._init_tables(_Conn(cur))

        created = [s for s in cur.executed if s.upper().startswith("CREATE TABLE")]
        assert len(created) > 5, (
            "a denied CREATE SCHEMA aborted the CREATE TABLEs the app is "
            "allowed to run — this is the bug that left ai_serving empty"
        )
        assert any("ai_serving.event_log_recent" in s for s in created)

    def test_the_failure_is_reported_with_its_reason(self, caplog):
        with caplog.at_level(logging.WARNING):
            pg_pool._init_tables(_Conn(_Cursor()))
        text = caplog.text
        assert "permission denied" in text, (
            "the actual error was swallowed; 'may already exist' told the "
            "operator nothing and hid a fresh-workspace showstopper"
        )

    def test_a_clean_run_says_so_and_warns_about_nothing(self, caplog):
        class _AllowAll(_Cursor):
            def execute(self, stmt: str):
                self.executed.append(stmt)

        cur = _AllowAll()
        with caplog.at_level(logging.INFO):
            pg_pool._init_tables(_Conn(cur))
        assert "Lakebase tables initialized" in caplog.text
        assert "failed" not in caplog.text
        assert len(cur.executed) == len(list(pg_pool.iter_statements(pg_pool._INIT_SQL)))

    def test_autocommit_is_restored(self):
        conn = _Conn(_Cursor())
        conn.autocommit = False
        pg_pool._init_tables(conn)
        assert conn.autocommit is False


class TestTheDeployProvisionsTheSchemaItself:
    """The app cannot be the thing that creates what the stream depends on."""

    def test_init_lakebase_role_runs_the_shared_ddl(self):
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[2] / "scripts" / "init_lakebase_role.py"
        ).read_text(encoding="utf-8")
        assert "_INIT_SQL" in source and "iter_statements" in source, (
            "the deploy no longer creates the Lakebase tables, so the streaming "
            "job races the app's lazy init again"
        )
        assert "_create_app_tables" in source
