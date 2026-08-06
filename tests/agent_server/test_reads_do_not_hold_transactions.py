"""A read must not leave the connection ``idle in transaction``.

The pooled connection runs with ``autocommit = False``, and ``query`` used to
return straight after fetching. Postgres had already opened a transaction for
the statement, so the backend sat ``idle in transaction`` holding an ACCESS
SHARE lock on everything it had touched until some unrelated later query
happened to end it.

The SSE stream reads ``ai_serving.scenario_commands`` roughly every second, so
one was almost always open. A deploy's ``ALTER TABLE`` on that table could
therefore never acquire ACCESS EXCLUSIVE — and because an exclusive request
queues ahead of readers that arrive after it, every producer poll then blocked
behind the ALTER. Seen on a dev deploy: pg_stat_activity showed the ALTER
waiting on an idle-in-transaction reader while both producers waited on the
ALTER. It stalled 14 minutes and took incident handling down in both
environments; it was still reproducing on the next attempt, from a connection
opened one second earlier.
"""
from __future__ import annotations

import threading

import pytest

from agent_server.memory import pg_pool


class FakeCursor:
    def __init__(self, conn, rows, description=(("a",),)):
        self._conn = conn
        self._rows = rows
        self.description = description

    def execute(self, sql, params=None):
        self._conn.executed.append(sql)

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


class FakeConn:
    """Records the transaction calls a real backend would see."""

    def __init__(self, rows=None, description=(("a",),)):
        self.rows = rows if rows is not None else [{"a": 1}]
        self.description = description
        self.executed: list[str] = []
        self.calls: list[str] = []

    def cursor(self):
        return FakeCursor(self, self.rows, self.description)

    def rollback(self):
        self.calls.append("rollback")

    def commit(self):
        self.calls.append("commit")


@pytest.fixture
def conn(monkeypatch):
    c = FakeConn()
    monkeypatch.setattr(pg_pool, "get_connection", lambda: c)
    monkeypatch.setattr(pg_pool, "_write_depth", 0, raising=False)
    return c


class TestAReadClosesItsOwnTransaction:
    def test_fetch_all_ends_the_transaction(self, conn):
        assert pg_pool.query("SELECT 1") == [{"a": 1}]
        assert conn.calls == ["rollback"]

    def test_fetch_one_ends_the_transaction(self, conn):
        assert pg_pool.query("SELECT 1", fetch="one") == {"a": 1}
        assert conn.calls == ["rollback"]

    def test_an_empty_result_still_ends_it(self, monkeypatch):
        c = FakeConn(rows=[])
        monkeypatch.setattr(pg_pool, "get_connection", lambda: c)
        assert pg_pool.query("SELECT 1") == []
        assert c.calls == ["rollback"]

    def test_a_statement_returning_nothing_still_ends_it(self, monkeypatch):
        # description is None for a statement with no result set; that path
        # returned early and was the easiest one to leave open.
        c = FakeConn(description=None)
        monkeypatch.setattr(pg_pool, "get_connection", lambda: c)
        assert pg_pool.query("SET x = 1") is None
        assert c.calls == ["rollback"]

    def test_it_rolls_back_rather_than_commits(self, conn):
        # Nothing to keep, and rollback releases the snapshot and its locks.
        pg_pool.query("SELECT 1")
        assert "commit" not in conn.calls

    def test_a_dead_connection_after_a_good_read_is_not_fatal(self, monkeypatch):
        class Dying(FakeConn):
            def rollback(self):
                raise RuntimeError("server closed the connection")

        c = Dying()
        monkeypatch.setattr(pg_pool, "get_connection", lambda: c)
        # The rows were already fetched; the caller gets them.
        assert pg_pool.query("SELECT 1") == [{"a": 1}]


class TestAReadInsideAWriteLeavesTheWriteAlone:
    """``_lock`` is reentrant, so a same-thread read nested in a write unit is
    reachable. Ending its transaction would silently discard the writer's work —
    the exact failure the pool's single-connection design already warns about.
    """

    def test_the_nested_read_does_not_roll_back(self, conn):
        with pg_pool.transaction() as cur:
            assert cur is not None
            pg_pool.query("SELECT 1")
            assert "rollback" not in conn.calls
        assert conn.calls == ["commit"]

    def test_the_depth_returns_to_zero(self, conn):
        with pg_pool.transaction():
            pass
        assert pg_pool._write_depth == 0

    def test_it_returns_to_zero_after_a_failure(self, conn):
        with pytest.raises(ValueError):
            with pg_pool.transaction():
                raise ValueError("boom")
        assert pg_pool._write_depth == 0

    def test_reads_resume_ending_their_own_transaction_afterwards(self, conn):
        with pg_pool.transaction():
            pg_pool.query("SELECT 1")
        conn.calls.clear()
        pg_pool.query("SELECT 1")
        assert conn.calls == ["rollback"]


class TestTheDeployDoesNotWaitForeverOnALock:
    def test_the_role_script_bounds_its_ddl_wait(self):
        from pathlib import Path

        src = Path(__file__).resolve().parents[2] / "scripts" / "init_lakebase_role.py"
        # Comments in this file discuss the ALTER, so read the code only.
        code = "\n".join(
            line for line in src.read_text().splitlines()
            if not line.lstrip().startswith("#")
        )
        assert 'cur.execute("SET lock_timeout' in code
        # It has to be set on the connection before any DDL runs on it, or it
        # protects nothing.
        assert code.index('cur.execute("SET lock_timeout') < code.index("_create_app_tables(cur)")


def test_concurrent_reads_each_close_their_own_transaction(monkeypatch):
    # The SSE stream reads from worker threads; a leak from any one of them is
    # enough to park a deploy's DDL.
    c = FakeConn()
    monkeypatch.setattr(pg_pool, "get_connection", lambda: c)
    threads = [threading.Thread(target=lambda: pg_pool.query("SELECT 1")) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert c.calls == ["rollback"] * 8
