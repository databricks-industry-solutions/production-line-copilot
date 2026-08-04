"""What the Lakebase read path does when Postgres misbehaves.

The live dashboard reads Lakebase on every SSE tick through one shared
connection, so the failure modes that matter are not "does a good query
work" but: does a blip cascade to unrelated handlers, does an outage cost a
connect timeout per request, and does a broken connection get reused.
"""

from __future__ import annotations

import threading
import time

import psycopg2
import pytest

from agent_server.memory import pg_pool


@pytest.fixture(autouse=True)
def _reset_pool_state():
    """Each test starts with a closed breaker and no pooled connection."""
    pg_pool._connection = None
    pg_pool._consecutive_failures = 0
    pg_pool._breaker_open_until = 0.0
    pg_pool._breaker_cooldown_s = pg_pool._BREAKER_COOLDOWN_START_S
    yield
    pg_pool._connection = None
    pg_pool._consecutive_failures = 0
    pg_pool._breaker_open_until = 0.0
    pg_pool._breaker_cooldown_s = pg_pool._BREAKER_COOLDOWN_START_S


class FakeCursor:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        self._conn.cursors_closed += 1

    def execute(self, sql, params=None):
        self._conn.executed.append(sql)
        if self._conn.aborted:
            raise psycopg2.errors.InFailedSqlTransaction("current transaction is aborted")
        err = self._conn.raise_next.pop(0) if self._conn.raise_next else None
        if err is not None:
            # Postgres aborts the whole transaction on a statement error;
            # only a rollback clears it.
            if not isinstance(err, (psycopg2.OperationalError, psycopg2.InterfaceError)):
                self._conn.aborted = True
            raise err

    @property
    def description(self):
        return [("col",)]

    def fetchall(self):
        return [{"col": 1}]

    def fetchone(self):
        return {"col": 1}


class FakeConn:
    def __init__(self, raise_next=None):
        self.raise_next = list(raise_next or [])
        self.executed: list[str] = []
        self.aborted = False
        self.closed_count = 0
        self.rollbacks = 0
        self.commits = 0
        self.cursors_closed = 0
        self.autocommit = False

    def cursor(self):
        return FakeCursor(self)

    def rollback(self):
        self.rollbacks += 1
        self.aborted = False

    def commit(self):
        self.commits += 1
        self.aborted = False

    def close(self):
        self.closed_count += 1


def _serve(monkeypatch, *conns):
    """Hand out the given connections in order, like successive connects."""
    queue = list(conns)
    handed: list[FakeConn] = []

    def fake_get_connection():
        if queue:
            handed.append(queue.pop(0))
        # Mirror the real pool: whatever is handed out is also what is pooled,
        # so discarding a broken connection is observable.
        pg_pool._connection = handed[-1] if handed else None
        return pg_pool._connection

    monkeypatch.setattr(pg_pool, "get_connection", fake_get_connection)
    return handed


class TestOneBadReadDoesNotBreakEveryoneElse:
    """The connection is shared, so a failed statement is everyone's problem
    unless it is rolled back."""

    def test_a_query_error_leaves_the_connection_usable(self, monkeypatch):
        conn = FakeConn(raise_next=[psycopg2.ProgrammingError('relation "nope" does not exist')])
        _serve(monkeypatch, conn)

        with pytest.raises(psycopg2.ProgrammingError):
            pg_pool.query("SELECT * FROM nope")

        assert conn.rollbacks == 1
        assert pg_pool.query("SELECT 1") == [{"col": 1}]

    def test_without_the_rollback_the_next_caller_would_have_failed(self, monkeypatch):
        # Pins the fake's fidelity: an un-rolled-back error really does poison
        # the connection, which is what the production bug looked like.
        conn = FakeConn(raise_next=[psycopg2.ProgrammingError("boom")])
        conn.aborted = True
        _serve(monkeypatch, conn)
        with pytest.raises(psycopg2.errors.InFailedSqlTransaction):
            pg_pool.query("SELECT 1")

    def test_a_real_query_error_is_raised_not_swallowed(self, monkeypatch):
        # Bad SQL or a missing table must surface; retrying cannot fix it and
        # hiding it behind an empty panel hides the bug.
        conn = FakeConn(raise_next=[psycopg2.ProgrammingError("syntax error")])
        _serve(monkeypatch, conn)
        with pytest.raises(psycopg2.ProgrammingError):
            pg_pool.query("SELEC 1")
        assert len(conn.executed) == 1, "a query error must not be retried"


class TestATransientDropIsRetriedOnce:
    def test_a_dead_connection_is_replaced_and_the_query_succeeds(self, monkeypatch):
        dead = FakeConn(raise_next=[psycopg2.OperationalError("server closed the connection")])
        fresh = FakeConn()
        _serve(monkeypatch, dead, fresh)

        assert pg_pool.query("SELECT 1") == [{"col": 1}]
        assert dead.closed_count == 1, "the dead connection must not be pooled"
        assert fresh.executed == ["SELECT 1"]

    def test_the_broken_connection_is_discarded_from_the_pool(self, monkeypatch):
        dead = FakeConn(raise_next=[psycopg2.InterfaceError("closed")])
        _serve(monkeypatch, dead, FakeConn())
        pg_pool.query("SELECT 1")
        assert pg_pool._connection is not dead

    def test_it_gives_up_after_one_retry_rather_than_looping(self, monkeypatch):
        a = FakeConn(raise_next=[psycopg2.OperationalError("gone")])
        b = FakeConn(raise_next=[psycopg2.OperationalError("still gone")])
        _serve(monkeypatch, a, b)

        assert pg_pool.query("SELECT 1") is None, "unreachable Lakebase reads as no data"
        assert len(a.executed) == 1 and len(b.executed) == 1

    def test_fetch_one_returns_a_single_row(self, monkeypatch):
        _serve(monkeypatch, FakeConn())
        assert pg_pool.query("SELECT 1", fetch="one") == {"col": 1}


class TestAnOutageStopsCostingAConnectTimeout:
    """Connecting to a dead Lakebase burns two connect timeouts. Doing that on
    every SSE tick is what turned an outage into an app-wide stall."""

    def test_the_breaker_opens_after_a_run_of_failures(self):
        for _ in range(pg_pool._CONNECT_FAILURES_BEFORE_OPEN):
            pg_pool._record_connect_failure()
        assert pg_pool.breaker_is_open() is True

    def test_one_blip_does_not_open_it(self):
        pg_pool._record_connect_failure()
        assert pg_pool.breaker_is_open() is False

    def test_an_open_breaker_short_circuits_the_connect(self, monkeypatch):
        monkeypatch.setattr(pg_pool, "_is_pg_configured", lambda: True)
        attempted = []
        monkeypatch.setattr(pg_pool, "_get_pg_user", lambda: attempted.append(1) or "u")

        for _ in range(pg_pool._CONNECT_FAILURES_BEFORE_OPEN):
            pg_pool._record_connect_failure()

        assert pg_pool.get_connection() is None
        assert attempted == [], "no connect should be attempted while open"

    def test_recovery_closes_it_and_resets_the_backoff(self):
        for _ in range(pg_pool._CONNECT_FAILURES_BEFORE_OPEN):
            pg_pool._record_connect_failure()
        pg_pool._record_connect_success()

        assert pg_pool.breaker_is_open() is False
        assert pg_pool._consecutive_failures == 0
        assert pg_pool._breaker_cooldown_s == pg_pool._BREAKER_COOLDOWN_START_S

    def test_a_longer_outage_backs_off_instead_of_hammering(self):
        cooldowns = []
        for _ in range(3):
            for _ in range(pg_pool._CONNECT_FAILURES_BEFORE_OPEN):
                pg_pool._record_connect_failure()
            cooldowns.append(pg_pool._breaker_cooldown_s)
        assert cooldowns == sorted(cooldowns) and cooldowns[0] < cooldowns[-1]

    def test_the_backoff_is_capped(self):
        for _ in range(200):
            pg_pool._record_connect_failure()
        assert pg_pool._breaker_cooldown_s <= pg_pool._BREAKER_COOLDOWN_MAX_S


class TestAPoisonedConnectionIsRolledBackNotRebuilt:
    """Reconnecting costs an OAuth token mint plus a TLS handshake. When the
    only thing wrong is an aborted transaction, a rollback is the fix."""

    def test_the_health_probe_rolls_back_and_keeps_the_connection(self, monkeypatch):
        monkeypatch.setattr(pg_pool, "_is_pg_configured", lambda: True)
        conn = FakeConn()
        conn.aborted = True  # left behind by some other caller's failed read
        pg_pool._connection = conn
        pg_pool._last_token_time = pg_pool.time.monotonic()

        assert pg_pool.get_connection() is conn
        assert conn.rollbacks == 1
        assert conn.closed_count == 0, "a rollback should have avoided a reconnect"

    def test_a_truly_dead_connection_is_still_replaced(self, monkeypatch):
        monkeypatch.setattr(pg_pool, "_is_pg_configured", lambda: True)

        class Dead(FakeConn):
            def cursor(self):
                raise psycopg2.OperationalError("server closed the connection")

        dead = Dead()
        pg_pool._connection = dead
        pg_pool._last_token_time = pg_pool.time.monotonic()
        # No credentials in the test env, so the reconnect fails — the point is
        # that the dead connection was not handed back.
        monkeypatch.setattr(pg_pool, "_get_pg_password", lambda: (_ for _ in ()).throw(RuntimeError("no creds")))

        assert pg_pool.get_connection() is not dead
        assert dead.closed_count == 1


class TestTheDashboardDegradesRatherThanBreaks:
    """``query`` signals "Lakebase unreachable" with None. Every dashboard read
    has to turn that into an empty frame — the UI shows its waiting state — and
    never into a ``TypeError`` halfway through building an SSE payload."""

    @pytest.fixture
    def unreachable(self, monkeypatch):
        from agent_server.serving import realtime_store

        monkeypatch.setattr(realtime_store, "query", lambda *a, **k: None)
        return realtime_store

    def test_machine_state_reads_as_no_machines(self, unreachable):
        assert unreachable.read_machine_state_latest("shift_1") == []

    def test_the_kpi_row_reads_as_absent(self, unreachable):
        assert unreachable.read_line_kpi_latest("shift_1") is None

    def test_the_event_feed_reads_as_empty(self, unreachable):
        assert unreachable.read_event_log_recent("shift_1") == []

    def test_inventory_reads_as_empty(self, unreachable):
        assert unreachable.read_inventory_latest("shift_1") == []

    def test_the_playback_cursor_does_not_advance(self, unreachable):
        assert unreachable.read_machine_state_after("shift_1", None) == ([], None)
        assert unreachable.read_line_kpi_after("shift_1", None) == (None, None)
        assert unreachable.read_inventory_after("shift_1", None) == ([], None)

    def test_the_cursor_anchor_is_unknown_rather_than_epoch(self, unreachable):
        # None sends the SSE generator to its wall-clock fallback; a bogus
        # timestamp would have it replay stale history.
        assert unreachable.read_max_history_ts("shift_1") is None


class TestAWriteSurvivesAConcurrentFailedRead:
    """Every caller shares one connection and therefore one transaction. Reads
    now run on worker threads, so a read can land in the middle of a write —
    and the rollback a failed read must perform would take the write with it."""

    def test_a_read_cannot_roll_back_a_write_in_flight(self, monkeypatch):
        conn = FakeConn()
        _serve(monkeypatch, conn)

        order: list[str] = []
        writing = threading.Event()
        finish_write = threading.Event()

        def writer():
            with pg_pool.transaction() as cur:
                cur.execute("INSERT INTO ai_chatbot.messages VALUES (1)")
                writing.set()
                finish_write.wait(5)
            order.append("write committed")

        def reader():
            writing.wait(5)
            conn.raise_next.append(psycopg2.ProgrammingError('relation "nope" does not exist'))
            try:
                pg_pool.query("SELECT * FROM nope")
            except psycopg2.ProgrammingError:
                pass
            order.append("read rolled back")

        threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
        for t in threads:
            t.start()
        writing.wait(5)
        # The reader is now blocked on the pool lock. If it were not, it would
        # have rolled back within this window and destroyed the INSERT.
        time.sleep(0.1)
        assert conn.rollbacks == 0, "a read rolled back a write that was still open"
        assert conn.commits == 0
        finish_write.set()
        for t in threads:
            t.join(5)

        assert order == ["write committed", "read rolled back"]
        assert conn.commits == 1

    def test_a_write_commits_when_the_block_completes(self, monkeypatch):
        conn = FakeConn()
        _serve(monkeypatch, conn)
        with pg_pool.transaction() as cur:
            cur.execute("UPDATE t SET x = 1")
        assert conn.commits == 1 and conn.rollbacks == 0
        assert conn.cursors_closed == 1

    def test_a_failed_write_rolls_back_and_still_raises(self, monkeypatch):
        conn = FakeConn(raise_next=[psycopg2.ProgrammingError("bad column")])
        _serve(monkeypatch, conn)
        with pytest.raises(psycopg2.ProgrammingError):
            with pg_pool.transaction() as cur:
                cur.execute("UPDATE t SET nope = 1")
        assert conn.rollbacks == 1 and conn.commits == 0
        assert conn.cursors_closed == 1, "the cursor must be closed on the error path too"

    def test_an_unreachable_lakebase_yields_none_instead_of_raising(self, monkeypatch):
        monkeypatch.setattr(pg_pool, "get_connection", lambda: None)
        with pg_pool.transaction() as cur:
            assert cur is None

    def test_returning_early_from_the_block_still_commits(self, monkeypatch):
        # ``save_message`` returns from inside the block to skip its in-memory
        # fallback; that must not skip the commit.
        conn = FakeConn()
        _serve(monkeypatch, conn)

        def write_and_return() -> str:
            with pg_pool.transaction() as cur:
                cur.execute("INSERT INTO t VALUES (1)")
                return "done"

        assert write_and_return() == "done"
        assert conn.commits == 1


class TestFailedTableInitDoesNotChangeTransactionSemantics:
    """``_init_tables`` flips autocommit on to run DDL. It must flip it back
    even when the DDL fails, which it routinely does when the app's service
    principal has no CREATE grant."""

    def test_autocommit_is_restored_when_init_fails(self, monkeypatch):
        monkeypatch.setattr(pg_pool, "_tables_initialized", False)

        class Failing(FakeConn):
            def cursor(self):
                raise psycopg2.ProgrammingError("permission denied for schema")

        conn = Failing()
        conn.autocommit = False
        pg_pool._init_tables(conn)
        assert conn.autocommit is False

    def test_autocommit_is_restored_when_init_succeeds(self, monkeypatch):
        monkeypatch.setattr(pg_pool, "_tables_initialized", False)
        conn = FakeConn()
        conn.autocommit = False
        pg_pool._init_tables(conn)
        assert conn.autocommit is False
