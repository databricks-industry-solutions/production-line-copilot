"""Postgres connection management for Lakebase.

Uses the Databricks SDK to obtain OAuth tokens for authentication.
Falls back gracefully when Postgres is not available.
"""
import base64
import json
import logging
import os
import threading
import time
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# One reentrant lock guards BOTH the connection's lifecycle and the statements
# run on it. They were briefly separate locks, which was worse in two ways: a
# thread could hold the statement lock and then wait for the lifecycle lock
# while another thread did the reverse (a deadlock waiting for the right
# interleaving), and only reads were serialised, so a writer's INSERT could
# still land between a reader's failed statement and the rollback that cleans
# up after it — silently discarding the write, since both share one
# transaction. A single lock costs nothing here: every caller already funnels
# through ``get_connection`` before running anything, and the shared
# connection can only execute one statement at a time regardless. Slow scans
# belong on ``new_readonly_connection`` and never take this lock.
_lock = threading.RLock()

# How many ``transaction()`` blocks are open on this connection. Reads end their
# own transaction when it is zero (see ``_end_read_transaction``); inside a write
# unit they must not, because the rollback would discard the writer's work.
# ``_lock`` is held for the whole of ``transaction()`` and reacquired by every
# read, so only the block's own thread can observe a non-zero depth.
_write_depth = 0
_connection = None
_last_token_time = 0.0
_TOKEN_REFRESH_SECONDS = 3000

# ── Circuit breaker ────────────────────────────────────────────────────────
# A failed connect is expensive: the primary attempt waits out connect_timeout,
# the workspace-token fallback waits it out again, and each mints an OAuth
# token first. Retrying that on EVERY call during an outage turns a Lakebase
# blip into ~20s of latency per request — and because the SSE generator calls
# these reads synchronously, that latency lands on the shared event loop and
# stalls every other client too.
#
# So after a run of consecutive connect failures we stop trying for a while and
# return None immediately. Callers already treat None as "live plane
# unavailable" and degrade honestly; failing fast is both cheaper and truer
# than blocking. The cooldown backs off so a long outage costs almost nothing,
# and any success resets it.
_CONNECT_FAILURES_BEFORE_OPEN = 3
_BREAKER_COOLDOWN_START_S = 5.0
_BREAKER_COOLDOWN_MAX_S = 120.0
_consecutive_failures = 0
_breaker_open_until = 0.0
_breaker_cooldown_s = _BREAKER_COOLDOWN_START_S

_INIT_SQL = """
CREATE SCHEMA IF NOT EXISTS ai_chatbot;

CREATE TABLE IF NOT EXISTS ai_chatbot.conversations (
    conversation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shift_id TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ai_chatbot.messages (
    message_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID REFERENCES ai_chatbot.conversations(conversation_id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON ai_chatbot.messages(conversation_id);

CREATE SCHEMA IF NOT EXISTS ai_serving;

CREATE TABLE IF NOT EXISTS ai_serving.machine_state_latest (
    shift_id TEXT NOT NULL,
    machine_id TEXT NOT NULL,
    ts TIMESTAMPTZ,
    status TEXT,
    throughput_per_min DOUBLE PRECISION DEFAULT 0,
    speed_pct DOUBLE PRECISION DEFAULT 100,
    alarm_count INTEGER DEFAULT 0,
    PRIMARY KEY (shift_id, machine_id)
);

CREATE TABLE IF NOT EXISTS ai_serving.line_kpi_latest (
    shift_id TEXT PRIMARY KEY,
    ts TIMESTAMPTZ,
    machines_running INTEGER DEFAULT 0,
    machines_stopped INTEGER DEFAULT 0,
    machines_degraded INTEGER DEFAULT 0,
    machines_starved INTEGER DEFAULT 0,
    total_throughput DOUBLE PRECISION DEFAULT 0,
    cumulative_cases DOUBLE PRECISION DEFAULT 0,
    avg_speed_pct DOUBLE PRECISION DEFAULT 0,
    availability_to_now DOUBLE PRECISION DEFAULT 0,
    performance_to_now DOUBLE PRECISION DEFAULT 0,
    quality_to_now DOUBLE PRECISION DEFAULT 0,
    oee_to_now DOUBLE PRECISION DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ai_serving.event_log_recent (
    event_id TEXT PRIMARY KEY,
    shift_id TEXT,
    machine_id TEXT,
    event_type TEXT,
    severity TEXT,
    timestamp_start TIMESTAMPTZ,
    timestamp_end TIMESTAMPTZ,
    duration_minutes DOUBLE PRECISION,
    root_cause TEXT,
    description TEXT,
    corrective_action TEXT
);

CREATE TABLE IF NOT EXISTS ai_serving.inventory_status (
    shift_id TEXT NOT NULL,
    material_id TEXT NOT NULL,
    on_hand_qty DOUBLE PRECISION DEFAULT 0,
    safety_stock INTEGER DEFAULT 0,
    reorder_point INTEGER DEFAULT 0,
    status TEXT DEFAULT 'OK',
    consumption_rate_per_hour DOUBLE PRECISION DEFAULT 0,
    -- Which machine consumes this material. The producer has always emitted
    -- it and UC has always stored it, but Lakebase dropped it on the way in,
    -- so the app could say a material was low without being able to say what
    -- would stop. The process-flow view draws supply lines from it.
    feeds_machine_id TEXT,
    PRIMARY KEY (shift_id, material_id)
);

CREATE TABLE IF NOT EXISTS ai_serving.scenario_commands (
    command_id TEXT PRIMARY KEY,
    shift_id TEXT,
    scenario_id TEXT,
    target_machine_id TEXT,
    severity TEXT,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMPTZ DEFAULT now(),
    -- Migrated from UC Delta `scenario_commands` (2026-06): the producer +
    -- app + SSE all read/write this row. Lakebase is now the source of
    -- truth so the per-tick poll never hits a SQL warehouse.
    created_by TEXT,
    params JSONB,
    resolved_at TIMESTAMPTZ,
    resolved_by TEXT,
    resolution_notes TEXT
);

-- The producer polls by status every tick; covering index avoids a seq
-- scan even when the table grows to thousands of historical rows.
CREATE INDEX IF NOT EXISTS idx_scenario_commands_status_created
    ON ai_serving.scenario_commands(status, created_at);

-- Idempotent migration for existing Lakebases that pre-date the
-- UC -> Lakebase consolidation (2026-06). ``CREATE TABLE IF NOT EXISTS``
-- above does nothing when the table already exists, so without these
-- ALTERs the producer + app would crash trying to write the newer
-- columns (``params``, ``created_by``, ``resolved_at``, ``resolved_by``,
-- ``resolution_notes``). All five statements are no-ops on fresh
-- installs because ``ADD COLUMN IF NOT EXISTS`` matches against the
-- column list we just declared in ``CREATE TABLE``.
ALTER TABLE ai_serving.scenario_commands
    ADD COLUMN IF NOT EXISTS created_by       TEXT;
ALTER TABLE ai_serving.scenario_commands
    ADD COLUMN IF NOT EXISTS params           JSONB;
ALTER TABLE ai_serving.scenario_commands
    ADD COLUMN IF NOT EXISTS resolved_at      TIMESTAMPTZ;
ALTER TABLE ai_serving.scenario_commands
    ADD COLUMN IF NOT EXISTS resolved_by      TEXT;
ALTER TABLE ai_serving.scenario_commands
    ADD COLUMN IF NOT EXISTS resolution_notes TEXT;

-- The legacy ``status`` default was 'active' (workshop-mode mirror) but
-- the producer's lifecycle requires INSERTs land in 'pending' so the
-- pickup phase runs. ``ALTER COLUMN ... SET DEFAULT`` is idempotent.
ALTER TABLE ai_serving.scenario_commands
    ALTER COLUMN status SET DEFAULT 'pending';

-- ── Playback history ─────────────────────────────────────────────────
-- These four back the SSE cursor that drives the live line UI. They used
-- to exist only in scripts/migrate_history_tables.sql, which no deploy
-- step ran, so a fresh Lakebase simply did not have them: the producer's
-- dual-write failed soft on every tick and the dashboard never animated.
-- It worked in environments where someone had run the file by hand once,
-- which is the worst kind of working. Creating them here means the app
-- provisions its own storage on first connect, like every other table
-- above.
CREATE TABLE IF NOT EXISTS ai_serving.machine_state_history (
    shift_id            TEXT        NOT NULL,
    machine_id          TEXT        NOT NULL,
    ts                  TIMESTAMPTZ NOT NULL,
    status              TEXT,
    throughput_per_min  DOUBLE PRECISION,
    speed_pct           DOUBLE PRECISION,
    alarm_count         INTEGER,
    PRIMARY KEY (shift_id, machine_id, ts)
);
-- Cursor query: WHERE shift_id=? AND ts > cursor ORDER BY ts ASC LIMIT 19.
CREATE INDEX IF NOT EXISTS idx_machine_state_history_shift_ts
    ON ai_serving.machine_state_history (shift_id, ts);

CREATE TABLE IF NOT EXISTS ai_serving.line_kpi_history (
    shift_id              TEXT        NOT NULL,
    ts                    TIMESTAMPTZ NOT NULL,
    machines_running      INTEGER,
    machines_stopped      INTEGER,
    machines_degraded     INTEGER,
    machines_starved      INTEGER,
    total_throughput      DOUBLE PRECISION,
    cumulative_cases      DOUBLE PRECISION,
    avg_speed_pct         DOUBLE PRECISION,
    availability_to_now   DOUBLE PRECISION,
    performance_to_now    DOUBLE PRECISION,
    quality_to_now        DOUBLE PRECISION,
    oee_to_now            DOUBLE PRECISION,
    PRIMARY KEY (shift_id, ts)
);
CREATE INDEX IF NOT EXISTS idx_line_kpi_history_shift_ts
    ON ai_serving.line_kpi_history (shift_id, ts);

CREATE TABLE IF NOT EXISTS ai_serving.inventory_status_history (
    shift_id                  TEXT        NOT NULL,
    material_id               TEXT        NOT NULL,
    ts                        TIMESTAMPTZ NOT NULL,
    on_hand_qty               DOUBLE PRECISION,
    safety_stock              DOUBLE PRECISION,
    reorder_point             DOUBLE PRECISION,
    status                    TEXT,
    consumption_rate_per_hour DOUBLE PRECISION,
    feeds_machine_id          TEXT,
    PRIMARY KEY (shift_id, material_id, ts)
);
CREATE INDEX IF NOT EXISTS idx_inventory_status_history_shift_ts
    ON ai_serving.inventory_status_history (shift_id, ts);

-- Same idempotent-migration reasoning as the scenario_commands ALTERs above:
-- ``CREATE TABLE IF NOT EXISTS`` does nothing on a Lakebase provisioned
-- before ``feeds_machine_id`` existed, so the quality-sync job would fail
-- every batch trying to write a column that is not there. No-ops on fresh
-- installs, where the column was just declared.
ALTER TABLE ai_serving.inventory_status
    ADD COLUMN IF NOT EXISTS feeds_machine_id TEXT;
ALTER TABLE ai_serving.inventory_status_history
    ADD COLUMN IF NOT EXISTS feeds_machine_id TEXT;

CREATE TABLE IF NOT EXISTS ai_serving.event_log_history (
    event_id           TEXT NOT NULL,
    shift_id           TEXT NOT NULL,
    machine_id         TEXT,
    event_type         TEXT,
    severity           TEXT,
    timestamp_start    TIMESTAMPTZ,
    timestamp_end      TIMESTAMPTZ,
    duration_minutes   DOUBLE PRECISION,
    root_cause         TEXT,
    description        TEXT,
    corrective_action  TEXT,
    PRIMARY KEY (event_id)
);
CREATE INDEX IF NOT EXISTS idx_event_log_history_shift_ts
    ON ai_serving.event_log_history (shift_id, timestamp_start);
"""

_tables_initialized = False


def _is_pg_configured() -> bool:
    return bool(os.environ.get("PGHOST"))


def _is_expired_token(password: str) -> bool:
    """True when ``password`` is a JWT whose ``exp`` has passed.

    ``PGPASSWORD`` is the documented way to hand this a Lakebase credential, and
    ``scripts/init_lakebase.py`` recommends it. What the documentation does not
    say is that the credential lasts an hour. Once exported it sits in the shell
    for as long as that shell lives, and every later connection presents the
    same dead token and is refused with "OAuth: User is not authorized" — which
    reads like a permissions problem, so the time goes on checking grants and
    roles that were correct all along. It cost exactly that here: the deploy's
    incident reconciliation had been skipping its Lakebase pass for a day and a
    half against a role with SUPERUSER on the right endpoint.

    A static password that is not a JWT (a real Postgres password) has no expiry
    to read and is returned untouched.
    """
    parts = password.split(".")
    if len(parts) != 3:
        return False
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload)).get("exp")
    except Exception:
        return False
    if not isinstance(exp, (int, float)):
        return False
    # A minute of slack: a token about to expire mid-connection is no use either.
    if time.time() < exp - 60:
        return False
    logger.info("PGPASSWORD holds an expired credential; minting a fresh one instead")
    return True


def _get_pg_password() -> str:
    """Get password for Lakebase Autoscaling via ``w.postgres.generate_database_credential``."""
    static_pw = os.environ.get("PGPASSWORD")
    if static_pw and not _is_expired_token(static_pw):
        return static_pw

    endpoint = os.environ.get("LAKEBASE_ENDPOINT", "")

    try:
        # why: Lakebase OAuth tokens are bound to the identity that mints them;
        # PGUSER is the app SP client_id, so we must mint as the app SP too.
        # See agent_server/tools/workspace_client.py.
        from agent_server.tools.workspace_client import app_workspace_client
        w = app_workspace_client()

        # Lakebase Autoscaling — try database credential first, then workspace token
        if endpoint:
            try:
                cred = w.postgres.generate_database_credential(endpoint=endpoint)
                logger.info("Lakebase Autoscaling credential obtained (endpoint=%s, token len=%d)",
                            endpoint, len(cred.token))
                return cred.token
            except AttributeError:
                logger.info("SDK too old for w.postgres — trying workspace token")
            except Exception as e:
                logger.info("generate_database_credential failed (%s) — trying workspace token", str(e)[:100])

        # Workspace OAuth token — works for both Autoscaling and Provisioned
        token = w.config.authenticate()
        if callable(token):
            import requests
            req = requests.Request("GET", "https://dummy")
            prepared = req.prepare()
            token(prepared)
            auth_header = prepared.headers.get("Authorization", "")
            pw = auth_header.replace("Bearer ", "")
            logger.info("Using workspace OAuth token for Lakebase (len=%d)", len(pw))
            return pw
        return str(token)
    except Exception:
        logger.warning("Could not obtain OAuth token for Lakebase", exc_info=True)
        return ""


def _get_pg_user() -> str:
    """Resolve Postgres user for Lakebase.

    For **Lakebase Autoscaling OAuth** (``generate_database_credential``), the DB role
    must match the workspace identity whose token is minted — on Databricks Apps that
    is the app's **service principal** (``DATABRICKS_CLIENT_ID``), not an arbitrary UUID
    from an older Connect modal. See:
    https://docs.databricks.com/aws/en/oltp/projects/tutorial-databricks-apps-autoscaling

    Prefer ``DATABRICKS_CLIENT_ID`` when set; otherwise ``PGUSER``; local dev often sets
    ``PGUSER`` to a Databricks user email with no client id.
    """
    client_id = (os.environ.get("DATABRICKS_CLIENT_ID") or "").strip()
    pguser = (os.environ.get("PGUSER") or "").strip()
    if client_id:
        if pguser and pguser.lower() != client_id.lower():
            logger.info(
                "Postgres user: using DATABRICKS_CLIENT_ID (PGUSER differs; "
                "Lakebase OAuth role must match the app service principal)"
            )
        return client_id
    if pguser:
        return pguser
    return "postgres"


def iter_statements(sql: str):
    """Yield executable statements from ``sql``, ignoring ``--`` comments.

    Splitting on ``;`` alone breaks on a semicolon inside a comment: the line
    "-- polls by status every tick; a covering index avoids a seq scan" becomes
    a comment-only fragment (psycopg2: "can't execute an empty query") followed
    by "a covering index avoids a seq scan" (a syntax error). Stripping the
    comments first is enough here — the DDL contains no string literal holding
    a semicolon, and a full parser would be more machinery than that earns.
    """
    stripped = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    for statement in stripped.split(";"):
        statement = statement.strip()
        if statement:
            yield statement


def _init_tables(conn):
    """Create schema and tables if they don't exist.

    Every statement runs on its own. The app service principal is granted
    CREATE on ai_serving and ai_chatbot but *not* on the database, so the
    leading ``CREATE SCHEMA IF NOT EXISTS`` raises "permission denied for
    database" — and when the whole batch shared one try/except, that first
    statement cost the twenty-five CREATE TABLEs the app was entitled to run.
    The schemas are created by the deploy (scripts/init_lakebase_role.py), so
    that particular failure is expected and harmless; losing everything after
    it was not, and the bland "may already exist" warning made it invisible.
    """
    global _tables_initialized
    if _tables_initialized:
        return
    old_autocommit = conn.autocommit
    try:
        conn.autocommit = True
        failed: list[str] = []
        with conn.cursor() as cur:
            for stmt in iter_statements(_INIT_SQL):
                try:
                    cur.execute(stmt)
                except Exception as exc:  # noqa: BLE001 - reported together below
                    failed.append(f"{stmt.splitlines()[0][:60]} -> {str(exc).strip()[:120]}")
        _tables_initialized = True
        if failed:
            logger.warning(
                "Lakebase init: %d statement(s) failed (the rest applied). "
                "The deploy provisions this schema; these are only a fallback:\n  %s",
                len(failed), "\n  ".join(failed),
            )
        else:
            logger.info("Lakebase tables initialized")
    except Exception:
        logger.warning("Could not auto-initialize Lakebase tables", exc_info=True)
    finally:
        # Restore in a finally: the old code restored only on the success path,
        # so a failed init (routine when the app SP can't CREATE) silently left
        # the shared connection in autocommit mode. Every caller's transaction
        # semantics then depended on whether init happened to succeed.
        try:
            conn.autocommit = old_autocommit
        except Exception:  # noqa: BLE001 - connection already dead; nothing to restore
            pass


def _record_connect_failure() -> None:
    """Trip the breaker once failures stop looking like a one-off blip."""
    global _consecutive_failures, _breaker_open_until, _breaker_cooldown_s
    _consecutive_failures += 1
    if _consecutive_failures >= _CONNECT_FAILURES_BEFORE_OPEN:
        _breaker_open_until = time.monotonic() + _breaker_cooldown_s
        logger.warning(
            "Lakebase circuit breaker OPEN for %.0fs after %d consecutive connect "
            "failures — live reads will return empty until it closes",
            _breaker_cooldown_s, _consecutive_failures,
        )
        _breaker_cooldown_s = min(_breaker_cooldown_s * 2, _BREAKER_COOLDOWN_MAX_S)


def _record_connect_success() -> None:
    global _consecutive_failures, _breaker_open_until, _breaker_cooldown_s
    if _consecutive_failures or _breaker_open_until:
        logger.info("Lakebase reachable again — circuit breaker reset")
    _consecutive_failures = 0
    _breaker_open_until = 0.0
    _breaker_cooldown_s = _BREAKER_COOLDOWN_START_S


def breaker_is_open() -> bool:
    """True while we are deliberately not attempting to reach Lakebase."""
    return time.monotonic() < _breaker_open_until


def get_connection():
    """Get a reusable Postgres connection. Returns None if PG is not configured.

    Also returns None immediately while the circuit breaker is open, so a
    Lakebase outage costs a dictionary lookup rather than two connect timeouts.
    """
    global _connection, _last_token_time

    if not _is_pg_configured() or breaker_is_open():
        return None

    with _lock:
        now = time.monotonic()

        if _connection is not None and (now - _last_token_time) < _TOKEN_REFRESH_SECONDS:
            for probe in (1, 2):
                try:
                    with _connection.cursor() as cur:
                        cur.execute("SELECT 1")
                    return _connection
                except Exception:
                    # An aborted transaction left by some other caller fails
                    # this probe too, and a rollback fixes it in microseconds
                    # — worth trying before paying for a reconnect.
                    if probe == 1:
                        try:
                            _connection.rollback()
                            continue
                        except Exception:  # noqa: BLE001
                            pass
                    break
            try:
                _connection.close()
            except Exception:
                pass
            _connection = None

        if _connection is not None:
            try:
                _connection.close()
            except Exception:
                pass
            _connection = None

        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor

            password = _get_pg_password()
            user = _get_pg_user()
            host = os.environ.get("PGHOST", "localhost")
            port = int(os.environ.get("PGPORT", "5432"))
            dbname = os.environ.get("PGDATABASE", "databricks_postgres")
            sslmode = os.environ.get("PGSSLMODE", "require")

            try:
                _connection = psycopg2.connect(
                    host=host, port=port, user=user, password=password,
                    dbname=dbname, sslmode=sslmode,
                    cursor_factory=RealDictCursor, connect_timeout=10,
                )
            except Exception as first_err:
                logger.info("First PG connect attempt failed (%s) — trying workspace token fallback",
                            str(first_err)[:120])
                # Force workspace token path by clearing endpoint
                saved = os.environ.pop("LAKEBASE_ENDPOINT", None)
                try:
                    password = _get_pg_password()
                    _connection = psycopg2.connect(
                        host=host, port=port, user=user, password=password,
                        dbname=dbname, sslmode=sslmode,
                        cursor_factory=RealDictCursor, connect_timeout=10,
                    )
                finally:
                    if saved is not None:
                        os.environ["LAKEBASE_ENDPOINT"] = saved

            _connection.autocommit = False
            _last_token_time = now
            _record_connect_success()
            logger.info("Lakebase connection established to %s as %s", host, user)

            _init_tables(_connection)
            return _connection
        except Exception as exc:
            msg = str(exc)
            if len(msg) > 400:
                msg = msg[:400] + "…"
            logger.warning(
                "Lakebase not available (%s: %s); persistence disabled (pg_user=%s)",
                type(exc).__name__,
                msg,
                _get_pg_user(),
            )
            _record_connect_failure()
            return None


def _discard_connection() -> None:
    """Drop the pooled connection so the next caller builds a fresh one."""
    global _connection
    with _lock:
        if _connection is not None:
            try:
                _connection.close()
            except Exception:  # noqa: BLE001
                pass
            _connection = None


def _reset_after_error(conn) -> None:
    """Return a connection to a usable state after a failed statement.

    The pooled connection runs with ``autocommit = False`` and is shared by
    every handler, so a statement that errors leaves an ABORTED transaction
    open: Postgres then rejects everything else on that connection with
    ``InFailedSqlTransaction``. Verified against a live Lakebase — one handler's
    bad read made an unrelated handler's ``SELECT 1`` fail. Rolling back here
    keeps one caller's failure from becoming everyone's.
    """
    with _lock:
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001 - connection is gone; the retry reconnects
            pass


def query(sql: str, params: tuple | None = None, *, fetch: str = "all"):
    """Run a read query, retrying once on a broken connection.

    Returns a list of dicts (``fetch="all"``), a single dict/None
    (``fetch="one"``), or None when Lakebase is unreachable — callers already
    treat that as "live plane unavailable" and degrade rather than serve stale
    data.

    The retry exists because the pooled connection can die between
    ``get_connection``'s health check and the statement that follows: token
    expiry, the pooler recycling a backend, an ordinary network blip. Those are
    transient by definition, so surfacing them to the UI is needless. A retry
    also handles the poisoned-connection case, since the first attempt rolls
    back and discards before the second reconnects.

    Errors that are NOT connection problems (bad SQL, missing table) are raised
    after a rollback: retrying cannot help, and swallowing them would hide real
    bugs behind an empty panel.

    The read's transaction is closed before returning. The connection runs with
    ``autocommit = False``, and this used to just return, leaving the backend
    ``idle in transaction`` holding an ACCESS SHARE lock on everything it had
    touched until some later query happened to end it. With the SSE stream
    reading ``scenario_commands`` every second there was almost always one open,
    so a deploy's ``ALTER TABLE`` could never take ACCESS EXCLUSIVE: it queued,
    and because an exclusive request queues ahead of later readers, every
    producer poll then blocked behind the ALTER. One dev deploy stalled 14
    minutes and froze incident handling in both environments while it waited.
    """
    import psycopg2

    for attempt in (1, 2):
        conn = get_connection()
        if conn is None:
            return None
        try:
            with _lock, conn.cursor() as cur:
                cur.execute(sql, params)
                if cur.description is None:
                    _end_read_transaction(conn)
                    return None
                if fetch == "one":
                    row = cur.fetchone()
                    result = dict(row) if row else None
                else:
                    result = [dict(r) for r in cur.fetchall()]
                _end_read_transaction(conn)
                return result
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as exc:
            # The connection itself is broken — rolling it back is pointless,
            # but it must not be handed to the next caller.
            _discard_connection()
            if attempt == 2:
                logger.warning(
                    "Lakebase query failed after reconnect (%s: %s)",
                    type(exc).__name__, str(exc)[:200],
                )
                _record_connect_failure()
                return None
            logger.info(
                "Lakebase connection broke mid-query (%s) — reconnecting and retrying once",
                type(exc).__name__,
            )
        except Exception:
            # A real query error. Roll back so the shared connection stays
            # usable for everyone else, then let the caller see it.
            _reset_after_error(conn)
            raise
    return None


def _end_read_transaction(conn) -> None:
    """Close the implicit transaction a read opened, unless a write owns it.

    Rollback rather than commit: the statement was a read, so there is nothing
    to keep, and rollback is the cheaper way to release the snapshot and the
    ACCESS SHARE locks it holds. Called with ``_lock`` held.
    """
    if _write_depth:
        return
    try:
        conn.rollback()
    except Exception:  # noqa: BLE001 — the read already succeeded; a failure
        # here means the connection died after it, and the next caller's health
        # check will discard it.
        pass


@contextmanager
def transaction():
    """Run a write as one unit on the shared connection, exclusively.

    Yields a cursor, or ``None`` when Lakebase is unreachable — callers must
    check, exactly as they do with ``get_connection``. Commits when the block
    completes, rolls back when it raises, and always closes the cursor.

    Holding the lock across the WHOLE unit (not just each statement) is the
    point. Every caller shares one connection and therefore one transaction,
    so a read that slipped in between a writer's INSERT and its COMMIT would
    join that transaction — and the rollback that read must perform when it
    fails would take the INSERT down with it, with no error anywhere: the
    writer's later ``commit()`` succeeds having committed nothing. Reads now
    run on worker threads, so that interleaving is reachable.

    Do not call ``query`` or ``get_connection`` from inside the block: the
    lock is reentrant so it will not deadlock, but a read nested inside a
    write unit is rolled back with it, which is rarely what the caller means.
    """
    conn = get_connection()
    if conn is None:
        yield None
        return
    global _write_depth
    with _lock:
        cur = conn.cursor()
        _write_depth += 1
        try:
            yield cur
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001 - connection already gone
                pass
            raise
        finally:
            _write_depth -= 1
            try:
                cur.close()
            except Exception:  # noqa: BLE001
                pass


def new_readonly_connection():
    """Open a SEPARATE, autocommit connection for read-only analytics.

    ``get_connection`` hands back one process-wide connection that every
    handler shares, which is fine for the short writes it was built for but
    wrong for slow read-only scans:

      * psycopg2 serializes statements per connection, so a multi-second COUNT
        would block the live SSE reads that share it;
      * a failed query has to be rolled back, and on a shared connection that
        rollback would discard another handler's uncommitted work.

    Autocommit means no transaction is left open when the caller is done.
    Returns None when Lakebase isn't configured. The caller owns the handle and
    must close it.
    """
    if not _is_pg_configured():
        return None
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor

        conn = psycopg2.connect(
            host=os.environ.get("PGHOST", "localhost"),
            port=int(os.environ.get("PGPORT", "5432")),
            user=_get_pg_user(),
            password=_get_pg_password(),
            dbname=os.environ.get("PGDATABASE", "databricks_postgres"),
            sslmode=os.environ.get("PGSSLMODE", "require"),
            cursor_factory=RealDictCursor,
            connect_timeout=10,
        )
        conn.autocommit = True
        return conn
    except Exception as exc:  # noqa: BLE001
        logger.warning("read-only Lakebase connection failed: %s", str(exc)[:200])
        return None


def close_pool():
    """Close the connection pool."""
    global _connection
    with _lock:
        if _connection:
            try:
                _connection.close()
            except Exception:
                pass
            _connection = None
