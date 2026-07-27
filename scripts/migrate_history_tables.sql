-- Lakebase: append-history tables for the 1 Hz playback architecture.
--
-- Rationale (see docs/ai/design/data-flow.md):
--   The previous *_latest tables only ever held the most recent row per
--   (shift_id, machine_id) — the streaming sync collapsed batches before
--   upsert. That meant the App could only show one new value per ~10 s when
--   the upstream pipeline coalesced commits. The history tables retain
--   every per-second row produced upstream (one row per machine per second)
--   so the App can replay them at a true 1 Hz cadence via a playback cursor.
--
-- Storage budget (per shift, 7-day TTL):
--   machine_state_history    ~19 rows/s × 86 400 × 7 = 11.5 M rows ≈ 1.7 GB
--   line_kpi_history         ~1  row /s × 86 400 × 7 =   605 K rows ≈ 90 MB
--   inventory_status_history ~16 rows/s × 86 400 × 7 = 9.7 M rows ≈ 1.4 GB
--   event_log_history        sparse (~10 rows/min)   ≈ negligible
--
-- Run as a user with CREATE on schema ai_serving:
--   databricks psql --project <project> --branch <branch> --endpoint primary
--                   --profile <profile> -- -d databricks_postgres
--                   -f scripts/migrate_history_tables.sql

CREATE SCHEMA IF NOT EXISTS ai_serving;

-- ── machine_state_history ────────────────────────────────────────────────
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
-- Indexed on (shift_id, ts) for fast forward scans.
CREATE INDEX IF NOT EXISTS idx_machine_state_history_shift_ts
    ON ai_serving.machine_state_history (shift_id, ts);

-- ── line_kpi_history ─────────────────────────────────────────────────────
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

-- ── inventory_status_history ─────────────────────────────────────────────
-- Inventory changes more slowly than machine state (~once per consumption
-- event) but we still keep timestamped history so the App's playback cursor
-- can replay it in lockstep with the machine grid.
CREATE TABLE IF NOT EXISTS ai_serving.inventory_status_history (
    shift_id                  TEXT        NOT NULL,
    material_id               TEXT        NOT NULL,
    ts                        TIMESTAMPTZ NOT NULL,
    on_hand_qty               DOUBLE PRECISION,
    safety_stock              DOUBLE PRECISION,
    reorder_point             DOUBLE PRECISION,
    status                    TEXT,
    consumption_rate_per_hour DOUBLE PRECISION,
    PRIMARY KEY (shift_id, material_id, ts)
);
CREATE INDEX IF NOT EXISTS idx_inventory_status_history_shift_ts
    ON ai_serving.inventory_status_history (shift_id, ts);

-- ── event_log_history ────────────────────────────────────────────────────
-- Events are inherently sparse and already keyed by event_id. We still
-- retain a history table (rather than reusing event_log_recent) so the
-- 7-day TTL can prune both tables on the same schedule, and the playback
-- cursor logic stays uniform.
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
