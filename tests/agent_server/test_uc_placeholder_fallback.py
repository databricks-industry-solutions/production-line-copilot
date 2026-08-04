"""Sanity checks for the scenario_commands module surface.

Migration history (2026-06): ``scenario_commands`` lived in Unity Catalog
and the module exported a ``_table()`` helper that resolved bundle-placeholder
env vars. The table is now in Lakebase Postgres at the fixed FQN
``ai_serving.scenario_commands`` (see ``agent_server/sim/commands.py``),
so the placeholder-fallback path is no longer relevant for this table.

These tests now verify the new module-level constant + that the Lakebase
write path no-ops gracefully when the pool is unavailable (workshop /
local-dev contexts), preserving the public API contract.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def pg_unavailable(monkeypatch):
    """Make the pool report Lakebase as unreachable.

    Patched at ``pg_pool`` rather than in ``commands``: the write paths go
    through ``pg_pool.transaction`` / ``pg_pool.query`` now, so a patch on a
    helper inside ``commands`` would no longer be consulted and these tests
    would pass without exercising anything.
    """
    from agent_server.memory import pg_pool

    monkeypatch.setattr(pg_pool, "get_connection", lambda: None)


def test_scenario_commands_target_table_is_lakebase():
    from agent_server.sim import commands as cmd_mod

    assert cmd_mod._TABLE == "ai_serving.scenario_commands"


def test_persist_no_ops_when_pg_unavailable(pg_unavailable):
    """When ``pg_pool.get_connection()`` returns None (no PGHOST configured)
    ``persist_scenario_command`` must log and return without raising.

    This mirrors the previous UC-path's tolerance for placeholder envs and
    keeps the FastAPI background task from blowing up smoke tests.
    """
    from agent_server.sim import commands as cmd_mod

    cmd = cmd_mod.build_scenario_command(
        shift_id="shift_1", scenario_id="conveyor_jam"
    )
    # Should not raise; logger.warning is the only side-effect we care about.
    cmd_mod.persist_scenario_command(cmd)


def test_list_recent_returns_empty_when_pg_unavailable(pg_unavailable):
    from agent_server.sim import commands as cmd_mod

    assert cmd_mod.list_recent_commands(limit=5) == []


def test_resolve_no_ops_when_pg_unavailable(pg_unavailable):
    from agent_server.sim import commands as cmd_mod

    cmd_mod.resolve_command(command_id="abc", resolved_by="tester")
