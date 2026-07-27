"""Postgres connection management for Lakebase.

Uses the Databricks SDK to obtain OAuth tokens for authentication.
Falls back gracefully when Postgres is not available.
"""
import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_connection = None
_available = True
_retry_count = 0
_MAX_RETRIES = 5
_last_token_time = 0.0
_TOKEN_REFRESH_SECONDS = 3000

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
"""

_tables_initialized = False


def _is_pg_configured() -> bool:
    return bool(os.environ.get("PGHOST"))


def _get_pg_password() -> str:
    """Get password for Lakebase Autoscaling via ``w.postgres.generate_database_credential``."""
    static_pw = os.environ.get("PGPASSWORD")
    if static_pw:
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


def _init_tables(conn):
    """Create schema and tables if they don't exist."""
    global _tables_initialized
    if _tables_initialized:
        return
    try:
        old_autocommit = conn.autocommit
        conn.autocommit = True
        with conn.cursor() as cur:
            for stmt in _INIT_SQL.strip().split(";"):
                stmt = stmt.strip()
                if stmt:
                    cur.execute(stmt)
        conn.autocommit = old_autocommit
        _tables_initialized = True
        logger.info("Lakebase tables initialized")
    except Exception:
        logger.warning("Could not auto-initialize Lakebase tables (may already exist)")


def get_connection():
    """Get a reusable Postgres connection. Returns None if PG is not configured."""
    global _connection, _available, _last_token_time

    if not _is_pg_configured() or (not _available and _retry_count >= _MAX_RETRIES):
        return None

    with _lock:
        now = time.monotonic()

        if _connection is not None and (now - _last_token_time) < _TOKEN_REFRESH_SECONDS:
            try:
                with _connection.cursor() as cur:
                    cur.execute("SELECT 1")
                return _connection
            except Exception:
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
            _available = False
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
