"""Unit tests for the silver mapper's pure SQL builders + drift tolerance.

The SDK-backed execution path is not exercised (integration concern); everything
here is network-free and validates the INSERT generation, column filtering, and
per-mapping orchestration via injected fakes.
"""
from __future__ import annotations

import pytest

from agent_server.serving import silver_mapper as sm


class TestIntrospectSql:
    def test_uses_information_schema(self):
        sql = sm.introspect_sql("mycat", "asset", "equipment_register")
        assert "mycat.information_schema.columns" in sql
        assert "table_schema = 'asset'" in sql
        assert "table_name = 'equipment_register'" in sql


class TestBuildInsert:
    def _mapping(self):
        return sm.Mapping(
            schema="asset",
            table="equipment_register",
            key="equipment_register",
            from_clause="FROM {src}.machine_registry mr",
            columns={
                "equipment_register_id": "xxhash64(mr.machine_id)",
                "equipment_name": "mr.machine_name",
                "asset_number": "mr.machine_id",
            },
        )

    def test_builds_insert_overwrite_with_existing_columns_only(self):
        m = self._mapping()
        # target is missing `asset_number` -> it must be dropped from the insert.
        existing = {"equipment_register_id", "equipment_name"}
        stmt = sm.build_insert(m, "cat", "main.line_stream", existing)
        assert stmt is not None
        assert stmt.startswith("INSERT OVERWRITE cat.asset.equipment_register (")
        assert "equipment_register_id" in stmt
        assert "equipment_name" in stmt
        assert "asset_number" not in stmt
        # {src} substituted
        assert "main.line_stream.machine_registry" in stmt
        assert "{src}" not in stmt

    def test_returns_none_when_no_columns_match(self):
        m = self._mapping()
        assert sm.build_insert(m, "cat", "src.sch", {"totally_other_col"}) is None

    def test_column_order_follows_mapping_declaration(self):
        m = self._mapping()
        existing = set(m.columns)
        stmt = sm.build_insert(m, "cat", "s.s", existing)
        header = stmt.split("\n", 1)[0]
        assert header == (
            "INSERT OVERWRITE cat.asset.equipment_register "
            "(equipment_register_id, equipment_name, asset_number)"
        )

    def test_scalar_mapping_has_no_top_level_from_clause(self):
        line = next(m for m in sm.MAPPINGS if m.key == "production_line")
        assert line.from_clause == ""
        stmt = sm.build_insert(line, "cat", "s.s", set(line.columns))
        # Scalar subqueries may contain FROM, but there is no top-level FROM clause
        # (which build_insert appends as a leading-newline "\nFROM ...").
        assert "\nFROM " not in stmt


class TestBuildMerge:
    def _stream_mapping(self):
        return sm.Mapping(
            schema="asset",
            table="failure_record",
            key="failure_record",
            from_clause="FROM {src}.event_log_bronze e",
            merge_key="failure_record_id",
            stream_from="FROM {batch} e",
            columns={
                "failure_record_id": "xxhash64(e.event_id)",
                "equipment_register_id": "xxhash64(e.machine_id)",
                "failure_mode_description": "e.event_type",
            },
        )

    def test_returns_none_for_non_streaming_mapping(self):
        # A dims-only mapping (no merge_key/stream_from) is not stream-enabled.
        loc = next(m for m in sm.MAPPINGS if m.key == "location")
        assert loc.is_streaming is False
        assert sm.build_merge(loc, "cat", "v", set(loc.columns)) is None

    def test_builds_merge_with_key_match_and_updates(self):
        m = self._stream_mapping()
        existing = set(m.columns)
        stmt = sm.build_merge(m, "cat", "_batch_v", existing)
        assert stmt is not None
        assert stmt.startswith("MERGE INTO cat.asset.failure_record t")
        # ON matches the surrogate key
        assert "ON t.failure_record_id = s.failure_record_id" in stmt
        assert "WHEN MATCHED THEN UPDATE SET" in stmt
        assert "WHEN NOT MATCHED THEN INSERT" in stmt
        # {batch} substituted, {src} never appears on the streaming path
        assert "FROM _batch_v e" in stmt
        assert "{batch}" not in stmt

    def test_key_not_in_update_set(self):
        m = self._stream_mapping()
        stmt = sm.build_merge(m, "cat", "v", set(m.columns))
        update_block = stmt.split("WHEN MATCHED THEN UPDATE SET")[1].split("WHEN NOT MATCHED")[0]
        assert "failure_record_id" not in update_block
        assert "equipment_register_id" in update_block

    def test_returns_none_when_merge_key_absent_from_target(self):
        m = self._stream_mapping()
        # target lacks the surrogate key column -> nothing to match on
        assert sm.build_merge(m, "cat", "v", {"failure_mode_description"}) is None

    def test_only_existing_columns_are_written(self):
        m = self._stream_mapping()
        existing = {"failure_record_id", "failure_mode_description"}
        stmt = sm.build_merge(m, "cat", "v", existing)
        assert "equipment_register_id" not in stmt
        assert "failure_mode_description" in stmt

    def test_production_line_uses_stream_columns_without_oee_times_100(self):
        line = next(m for m in sm.MAPPINGS if m.key == "production_line")
        assert line.is_streaming is True
        stmt = sm.build_merge(line, "cat", "kpi_v", set(line.stream_cols()))
        assert stmt is not None
        # streaming pulls the batch's freshest KPI row
        assert "ROW_NUMBER() OVER (ORDER BY ts DESC)" in stmt
        # oee_to_now is already a percentage — must NOT be multiplied by 100
        assert "* 100" not in stmt
        assert "oee_to_now" in stmt


class TestStreamingMappingCoverage:
    def test_expected_fact_tables_are_stream_enabled(self):
        streaming = {m.key for m in sm.MAPPINGS if m.is_streaming}
        assert streaming == {
            "failure_record", "stock_balance",
            "production_line", "inspection_result", "ncr",
        }

    def test_dimensions_are_batch_seed_only(self):
        for key in ("location", "equipment_register", "material_master",
                    "schedule", "pm_schedule", "work_center", "run"):
            m = next(mm for mm in sm.MAPPINGS if mm.key == key)
            assert m.is_streaming is False

    def test_registry_has_twelve_mappings(self):
        # 8 original + 4 (pm_schedule, work_center, run, ncr) = 12 governed entities.
        assert len(sm.MAPPINGS) == 12

    def test_new_mappings_align_foreign_keys(self):
        by = {m.key: m for m in sm.MAPPINGS}
        # work_center_id must match equipment_register.work_center_id
        assert by["work_center"].columns["work_center_id"] == sm._sk("mr.machine_id")
        assert by["equipment_register"].columns["work_center_id"] == sm._sk("mr.machine_id")
        # run.schedule_id must match schedule.schedule_id
        assert by["run"].columns["schedule_id"] == by["schedule"].columns["schedule_id"]
        # pm_schedule + ncr reference equipment_register via the same surrogate
        assert by["pm_schedule"].columns["equipment_register_id"] == sm._sk("mw.machine_id")
        assert by["ncr"].columns["equipment_register_id"] == sm._sk("q.machine_id")

    def test_batch_oee_no_longer_multiplies_by_100(self):
        # The INSERT OVERWRITE (setup seed) path had a *100 bug that inflated
        # OEE to 0-10000; it now stores the already-percentage value directly.
        line = next(m for m in sm.MAPPINGS if m.key == "production_line")
        assert "* 100" not in line.columns["actual_oee_percentage"]

    def test_oee_source_is_already_a_percentage(self):
        """Pins the SCALE of the source, not just the absence of a `* 100`.

        Both mapper paths store `oee_to_now` verbatim, so if the producer ever
        switched to emitting a 0..1 fraction the governed OEE would silently
        become 100x too small and every string-matching test would still pass.
        """
        from src.streaming_sim.line_kpi_derived import compute_tier1_line_kpi_row

        row = compute_tier1_line_kpi_row(
            shift_id="shift_1", ts="2026-07-30T12:00:00Z",
            machines_running=19, machines_stopped=0, machines_degraded=0,
            machines_starved=0, total_throughput=400.0, avg_speed_pct=95.0,
        )
        assert 0.0 <= row["oee_to_now"] <= 100.0
        assert row["oee_to_now"] > 1.0, "a 0..1 fraction would break both mapper paths"

    def test_per_shift_capacity_scales_the_per_minute_rate(self):
        """`nominal_capacity` is units/MIN; the target column is per SHIFT.

        Writing the raw rate understated governed capacity by 480x and skewed
        every utilisation metric view built on work_center.
        """
        wc = next(m for m in sm.MAPPINGS if m.key == "work_center")
        expr = wc.columns["available_capacity_per_shift"]
        assert f"* {sm.SHIFT_MINUTES}" in expr
        assert sm.SHIFT_MINUTES == 480


class TestMappingsRegistry:
    def test_all_mappings_have_unique_keys(self):
        keys = [m.key for m in sm.MAPPINGS]
        assert len(keys) == len(set(keys))
        assert all(keys)  # no empty keys

    def test_surrogate_key_helper_handles_nulls(self):
        assert sm._sk("mr.machine_id") == "xxhash64(COALESCE(CAST(mr.machine_id AS STRING), ''))"

    def test_merge_never_rewrites_creation_or_lifecycle_columns(self):
        """A replayed micro-batch must be a genuine no-op.

        `created_timestamp` in UPDATE SET would restamp existing rows to "now",
        and `ncr_status` would re-open a non-conformance report that something
        downstream had closed.
        """
        for m in sm.MAPPINGS:
            if not m.is_streaming:
                continue
            stmt = sm.build_merge(m, "cat", "b", set(m.stream_cols()))
            assert stmt is not None, f"{m.key} produced no MERGE"
            assert "UPDATE SET" in stmt, f"{m.key} produced no UPDATE clause"
            update_block = stmt.split("UPDATE SET")[1].split("WHEN NOT MATCHED")[0]
            for col in sm.INSERT_ONLY_COLUMNS:
                assert f"t.{col} =" not in update_block, f"{m.key} rewrites {col}"

    def test_insert_only_columns_are_still_written_on_insert(self):
        """Excluding them from UPDATE must not drop them from the INSERT."""
        ncr = next(m for m in sm.MAPPINGS if m.key == "ncr")
        stmt = sm.build_merge(ncr, "cat", "b", set(ncr.stream_cols()))
        assert stmt is not None
        insert_block = stmt.split("WHEN NOT MATCHED THEN INSERT")[1]
        assert "created_timestamp" in insert_block
        assert "ncr_status" in insert_block

    def test_foreign_keys_align_across_tables(self):
        # failure_record.equipment_register_id must be the SAME surrogate expr as
        # equipment_register.equipment_register_id so the FK actually joins.
        eqp = next(m for m in sm.MAPPINGS if m.key == "equipment_register")
        fail = next(m for m in sm.MAPPINGS if m.key == "failure_record")
        assert eqp.columns["equipment_register_id"] == sm._sk("mr.machine_id")
        assert fail.columns["equipment_register_id"] == sm._sk("e.machine_id")
        # both reduce to xxhash64(COALESCE(CAST(<machine_id> AS STRING), '')) form
        assert "xxhash64(COALESCE(CAST(" in eqp.columns["equipment_register_id"]
        assert "xxhash64(COALESCE(CAST(" in fail.columns["equipment_register_id"]


class TestRefreshSilverModel:
    def test_skips_when_target_not_installed(self):
        executed = []

        def query_all(sql):
            return []  # no columns -> target not installed

        def exec_one(sql):
            executed.append(sql)

        results = sm.refresh_silver_model(
            "cat", "main", "line_stream", "wh", only=["equipment_register"],
            exec_one=exec_one, query_all=query_all,
        )
        assert results == [("equipment_register", "skipped (target asset.equipment_register not found)")]
        assert executed == []  # nothing written

    def test_executes_insert_for_installed_target(self):
        executed = []

        def query_all(sql):
            # pretend the target has exactly these two columns
            return [["equipment_register_id"], ["equipment_name"]]

        def exec_one(sql):
            executed.append(sql)

        results = sm.refresh_silver_model(
            "cat", "main", "line_stream", "wh", only=["equipment_register"],
            exec_one=exec_one, query_all=query_all,
        )
        assert results == [("equipment_register", "ok")]
        assert len(executed) == 1
        assert executed[0].startswith("INSERT OVERWRITE cat.asset.equipment_register")

    def test_failure_is_captured_and_does_not_abort_others(self):
        def query_all(sql):
            return [["location_id"], ["location_name"]]

        calls = {"n": 0}

        def exec_one(sql):
            calls["n"] += 1
            raise RuntimeError("boom")

        results = sm.refresh_silver_model(
            "cat", "main", "s", "wh", only=["location", "equipment_register"],
            exec_one=exec_one, query_all=query_all,
        )
        assert len(results) == 2
        assert all(status.startswith("FAILED") for _, status in results)
        assert calls["n"] == 2  # kept going after the first failure

    def test_only_filter_selects_subset(self):
        seen = []

        def query_all(sql):
            return [["x"]]

        def exec_one(sql):
            seen.append(sql)

        results = sm.refresh_silver_model(
            "cat", "main", "s", "wh", only=["location"],
            exec_one=exec_one, query_all=query_all,
        )
        assert [k for k, _ in results] == ["location"]
