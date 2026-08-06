"""The app must create every Lakebase table it reads, on first connect.

A table whose DDL lives only in a hand-run .sql file is a table that exists in
whichever environment someone remembered to prepare. The playback history
tables were exactly that: `scripts/migrate_history_tables.sql` was in the repo,
no deploy step invoked it, and the producer's dual-write failed soft on every
tick against a fresh Lakebase — so the live line stayed empty and nothing in
the logs said why. These tests keep the app's own `_INIT_SQL` authoritative.
"""
from __future__ import annotations

import re
from pathlib import Path

from agent_server.memory.pg_pool import _INIT_SQL

REPO = Path(__file__).resolve().parents[2]
MIGRATION = REPO / "scripts" / "migrate_history_tables.sql"

# Read by agent_server/serving/realtime_store.py to drive the SSE cursor.
PLAYBACK_TABLES = (
    "ai_serving.machine_state_history",
    "ai_serving.line_kpi_history",
    "ai_serving.inventory_status_history",
    "ai_serving.event_log_history",
)


def _created(sql: str) -> set[str]:
    return set(re.findall(r"CREATE TABLE IF NOT EXISTS\s+([\w.]+)", sql))


class TestTheAppProvisionsItsOwnStorage:
    def test_every_playback_table_is_created_at_startup(self):
        missing = [t for t in PLAYBACK_TABLES if t not in _created(_INIT_SQL)]
        assert not missing, (
            f"_INIT_SQL does not create {missing}. The producer dual-writes these "
            "every tick and swallows the failure, so a fresh Lakebase shows an "
            "empty line with no error anywhere."
        )

    def test_each_playback_table_is_indexed_for_the_cursor_scan(self):
        """The SSE query is a forward scan on (shift_id, ts) every second."""
        for table in PLAYBACK_TABLES:
            bare = table.split(".")[-1]
            assert re.search(rf"CREATE INDEX IF NOT EXISTS\s+\w+\s+ON\s+{re.escape(table)}", _INIT_SQL), (
                f"{bare} has no index; the playback cursor sequential-scans it once a second"
            )

    def test_the_chat_schema_is_created_too(self):
        assert "ai_chatbot" in _INIT_SQL, "conversation storage has no schema to live in"


class TestTheStandaloneMigrationHasNotDrifted:
    """Two copies of a schema disagree eventually; this is when we find out."""

    def test_the_migration_file_creates_nothing_the_app_does_not(self):
        drift = _created(MIGRATION.read_text()) - _created(_INIT_SQL)
        assert not drift, (
            f"scripts/migrate_history_tables.sql creates {sorted(drift)} but the "
            "app does not, so those tables exist only where someone ran the file "
            "by hand. Add them to _INIT_SQL."
        )
