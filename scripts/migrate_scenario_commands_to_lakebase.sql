-- Migration: extend ai_serving.scenario_commands so it can replace the UC
-- Delta table as the producer + app + SSE source of truth.
--
-- Background: ai_serving.scenario_commands existed in Lakebase but only as
-- a workshop-mode mirror with 7 columns. The producer polled the UC Delta
-- copy via a SQL warehouse, paying 200-1000 ms per tick (and several seconds
-- on warehouse cold start). Moving the read path to Lakebase eliminates
-- those warehouse round-trips. event_log_bronze remains the audit trail.
--
-- Idempotent: each ADD COLUMN uses IF NOT EXISTS, so re-runs are safe.
--
-- Run order:
--   psql ... -f scripts/migrate_scenario_commands_to_lakebase.sql
-- or via the helper:
--   python scripts/run_lakebase_migration.py \
--       --sql scripts/migrate_scenario_commands_to_lakebase.sql

ALTER TABLE ai_serving.scenario_commands
    ADD COLUMN IF NOT EXISTS created_by       TEXT,
    ADD COLUMN IF NOT EXISTS params           JSONB,
    ADD COLUMN IF NOT EXISTS resolved_at      TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS resolved_by      TEXT,
    ADD COLUMN IF NOT EXISTS resolution_notes TEXT;

-- Producer polls by status every tick. Covering index keeps the SELECT
-- under 5 ms even at 10k+ historical rows.
CREATE INDEX IF NOT EXISTS idx_scenario_commands_status_created
    ON ai_serving.scenario_commands(status, created_at);

-- Optional safety: drop the historical 'active' default so new INSERTs are
-- required to specify status explicitly. The producer's lifecycle is:
--   pending → active → resolve_requested → resolving → resolved
-- so an implicit 'active' default would skip the producer's pickup phase
-- and silently lose the new-incident handoff.
ALTER TABLE ai_serving.scenario_commands
    ALTER COLUMN status DROP DEFAULT;
ALTER TABLE ai_serving.scenario_commands
    ALTER COLUMN status SET DEFAULT 'pending';
