"""``feeds_machine_id`` has to survive every hop, or the app cannot say what
runs out.

The producer has always known which machine consumes each material, and Unity
Catalog has always stored it. Lakebase did not: the quality-sync job selected
every inventory column except that one, the serving tables had no column to put
it in, and the SSE readers never asked for it. The result was an app that could
tell an operator a material was low but not what would stop when it ran out --
and a process-flow view that could not draw a supply line at all without
inventing the mapping client-side.

Five hops, each of which silently drops an unknown column rather than failing:

    inventory_engine.snapshot_inventory   dict emitted per material
      -> inventory_status_bronze          UC Delta, via Zerobus
      -> streaming_quality_sync           select_cols + the Lakebase upsert
      -> ai_serving.inventory_status*     Lakebase DDL
      -> read_inventory_after/_latest     the SSE payload

These tests walk the whole chain so a future column addition that stops halfway
fails here rather than showing up as an empty field in the UI.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_server.serving import realtime_store as rs

REPO = Path(__file__).resolve().parents[2]

COLUMN = "feeds_machine_id"


def block(source: str, start: str, end: str) -> str:
    """The text between ``start`` and the next ``end`` after it."""
    begin = source.index(start) + len(start)
    return source[begin : source.index(end, begin)]


@pytest.fixture()
def captured(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Collect the SQL each reader issues instead of touching Postgres."""
    sql: list[str] = []

    def fake_query(q: str, params=None, fetch: str = "all"):
        sql.append(" ".join(q.split()))
        return None if fetch == "one" else []

    monkeypatch.setattr(rs, "query", fake_query)
    return sql


def test_the_producer_emits_it() -> None:
    from src.streaming_sim.inventory_engine import InventoryState, snapshot_inventory

    inv = InventoryState(
        material_id="CROWNS",
        on_hand_qty=1000.0,
        safety_stock=100,
        reorder_point=200,
        consumption_rate_per_hour=250.0,
        machine_id="CAPPER",
    )
    rows = snapshot_inventory({"CROWNS": inv}, "shift_1")

    assert rows[0][COLUMN] == "CAPPER"


def test_unity_catalog_has_somewhere_to_put_it() -> None:
    ddl = (REPO / "scripts/init_realtime_uc.py").read_text()
    create = block(ddl, "CREATE TABLE IF NOT EXISTS {{fqn}}.inventory_status_bronze (", ")")
    assert COLUMN in create


class TestTheSyncJobCarriesItToLakebase:
    """The hop that actually dropped it."""

    source = (REPO / "pipelines/streaming_quality_sync.py").read_text()

    def test_it_is_selected_off_bronze(self) -> None:
        assert COLUMN in block(self.source, "inv_sink = _make_dual_sink(", "\n    )")

    def test_it_is_written_to_the_latest_table(self) -> None:
        assert COLUMN in block(self.source, "_INV_LATEST_COLS = [", "]")

    def test_a_changed_value_updates_rather_than_being_ignored(self) -> None:
        # The latest table upserts; a column missing from DO UPDATE SET keeps
        # whatever landed first, forever.
        conflict = block(self.source, "ON CONFLICT (shift_id, material_id) DO UPDATE SET", '"""')
        assert f"{COLUMN} = EXCLUDED.{COLUMN}" in conflict

    def test_an_older_bronze_table_still_syncs(self) -> None:
        # Bronze written before the column existed would otherwise fail the
        # whole micro-batch on a missing column rather than backfilling null.
        sink = block(self.source, "inv_sink = _make_dual_sink(", "\n    )")
        assert f'optional_cols=("{COLUMN}",)' in sink


class TestLakebaseHasTheColumn:
    schema = (REPO / "agent_server/memory/pg_pool.py").read_text()

    @pytest.mark.parametrize("table", ["inventory_status", "inventory_status_history"])
    def test_a_fresh_install_declares_it(self, table: str) -> None:
        create = block(self.schema, f"CREATE TABLE IF NOT EXISTS ai_serving.{table} (", ");")
        assert COLUMN in create

    @pytest.mark.parametrize("table", ["inventory_status", "inventory_status_history"])
    def test_an_existing_install_is_migrated(self, table: str) -> None:
        # CREATE TABLE IF NOT EXISTS does nothing on a Lakebase provisioned
        # before the column existed, so the sync job would fail every batch.
        assert (
            f"ALTER TABLE ai_serving.{table}\n    ADD COLUMN IF NOT EXISTS {COLUMN} TEXT;"
            in self.schema
        )


class TestTheServingReadsAskForIt:
    def test_the_warmup_read_selects_it(self, captured: list[str]) -> None:
        rs.read_inventory_latest("shift_1")
        assert COLUMN in captured[0]

    def test_the_streaming_cursor_selects_it(self, captured: list[str]) -> None:
        rs.read_inventory_after("shift_1", None)
        assert COLUMN in captured[0]


def test_the_client_expects_it_on_the_payload() -> None:
    hook = (REPO / "client/src/lib/useSSELiveData.ts").read_text()
    assert COLUMN in block(hook, "export interface InventoryItem {", "}")


def test_the_flow_view_refuses_to_guess_when_it_is_absent() -> None:
    # Without the column the honest answer is to draw no supply line, not to
    # fall back to a hardcoded bill of materials that can drift from the source.
    state = (REPO / "client/src/lib/processFlowState.ts").read_text()
    assert "if (!machineId) continue;" in state
