"""Unit tests for cross-path reconciliation.

The point of the reconciler is to be trustworthy about *why* two paths differ,
so these tests pin the behaviour that makes it trustworthy:

  * a settled window is used (never "up to now"), or normal streaming lag would
    read as permanent data loss;
  * QC-gate rejects count as EXPLAINED and must not inflate the red number;
  * anything unaccounted for counts as UNEXPLAINED in either direction;
  * governed upsert targets are never count-compared against event volumes;
  * incident tables at zero read as expected-empty, not as a failure;
  * the OEE value check catches the x100 scale bug that matching row counts miss.

Both query paths are injected, so nothing here needs a warehouse or Postgres.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_server.serving import reconciliation as rc


NOW = datetime(2026, 7, 30, 20, 0, 0, tzinfo=timezone.utc)
START, END = rc.window_bounds(15, 120, NOW)


# ── window ────────────────────────────────────────────────────────────────
def test_window_ends_in_the_past_so_in_flight_rows_are_excluded():
    start, end = rc.window_bounds(window_minutes=15, lag_guard_seconds=120, now=NOW)
    assert end == datetime(2026, 7, 30, 19, 58, 0, tzinfo=timezone.utc)
    assert start == datetime(2026, 7, 30, 19, 43, 0, tzinfo=timezone.utc)


def test_naive_datetime_is_rejected_rather_than_read_as_local_time():
    """`astimezone` on a naive value silently shifts the window by the host's
    UTC offset — and `datetime.utcnow()` is naive, so it's an easy mistake."""
    with pytest.raises(ValueError):
        rc.window_bounds(15, 120, now=datetime(2026, 7, 30, 20, 0, 0))


def test_window_is_truncated_to_whole_seconds():
    """The producer ticks at 1 Hz; a sub-second bound would split a tick."""
    start, end = rc.window_bounds(15, 120, NOW.replace(microsecond=987654))
    assert start.microsecond == 0 and end.microsecond == 0


def test_window_guard_of_zero_is_allowed_but_window_is_never_empty():
    start, end = rc.window_bounds(window_minutes=1, lag_guard_seconds=0, now=NOW)
    assert end == NOW
    assert (end - start).total_seconds() == 60


def test_both_engines_get_identical_absolute_bounds():
    """Interval-from-now() on each engine would drift by a tick at the boundary.

    The two queries execute at different instants, so a row sitting on the edge
    lands inside one window and outside the other — a permanent phantom
    deviation. Pinned bounds make the comparison exact.
    """
    uc = rc.build_uc_counts_sql(
        "cat", "sch", START, END,
        existing={"machine_state_bronze", "gold_history_machine_state"},
    )
    pg = rc.build_lakebase_counts_sql(START, END)

    assert "TIMESTAMP '2026-07-30 19:43:00+00:00'" in uc
    assert "TIMESTAMP '2026-07-30 19:58:00+00:00'" in uc
    assert "TIMESTAMPTZ '2026-07-30 19:43:00+00:00'" in pg
    assert "TIMESTAMPTZ '2026-07-30 19:58:00+00:00'" in pg
    # Neither side may fall back to its own clock.
    assert "current_timestamp()" not in uc
    assert "now()" not in pg


def test_uc_sql_skips_tables_that_do_not_exist():
    """A gold table only exists once written; a missing one must not break the UNION."""
    sql = rc.build_uc_counts_sql("cat", "sch", START, END, existing={"machine_state_bronze"})
    assert "machine_state_bronze" in sql
    assert "gold_history_machine_state" not in sql
    assert "quality_rejects" not in sql


def test_uc_sql_includes_rejects_and_dqx_when_present():
    sql = rc.build_uc_counts_sql(
        "cat", "sch", START, END, existing={"quality_rejects", "dqx_warnings"}
    )
    assert "'rejected' AS layer" in sql
    assert "'dqx' AS layer" in sql


def test_rejects_are_windowed_on_event_time_not_processing_time():
    """`rejected_at` is processing time; bronze/gold use event time.

    Subtracting a processing-time count from an event-time count only cancels
    out while the reject rate is constant — and it stops being constant during
    exactly the incident someone is demoing.
    """
    sql = rc.build_uc_counts_sql("cat", "sch", START, END, existing={"quality_rejects"})
    assert "payload['ts']" in sql
    assert "payload['timestamp_start']" in sql, "event_log uses its own event column"
    # rejected_at survives only as the fallback when the payload lacks the column.
    assert "COALESCE(try_cast(payload['ts'] AS TIMESTAMP), rejected_at)" in sql


def test_table_discovery_takes_all_schemas_in_one_round_trip():
    """Four per-schema queries would quadruple the latency floor for no benefit."""
    sql = rc.build_existing_tables_sql("mvm", ["asset", "production", "quality"])
    assert sql.count("information_schema.tables") == 1
    for s in ("asset", "production", "quality"):
        assert f"'{s}'" in sql
    # Explicit LIMIT: sql_query auto-appends LIMIT 500, which would truncate.
    assert "LIMIT 5000" in sql


@pytest.mark.parametrize("bad", ["cat; DROP TABLE x", "cat`x", "has space", ""])
def test_catalog_and_schema_identifiers_are_validated(bad):
    """Held to the same standard as shift_id — fail loudly, don't emit bad SQL."""
    with pytest.raises(ValueError):
        rc.build_existing_tables_sql(bad, ["asset"])


# ── shift scoping ─────────────────────────────────────────────────────────
# Dev and prod share ONE Lakebase instance and isolate only by sim_shift_id, so
# an unscoped live count includes the other environment's producer while the
# analytics side (isolated by UC schema) does not. That reads as a permanent
# phantom 2x deviation, which is exactly what happened on the first live run.
def test_lakebase_counts_are_scoped_to_the_configured_shift():
    sql = rc.build_lakebase_counts_sql(START, END, shift_id="shift_1_dev")
    assert sql.count("shift_id = 'shift_1_dev'") == len(rc.ENTITIES)


def test_bronze_and_gold_counts_are_scoped_to_the_same_shift():
    sql = rc.build_uc_counts_sql(
        "cat", "sch", START, END,
        existing={"machine_state_bronze", "gold_history_machine_state"},
        shift_id="shift_1_dev",
    )
    assert sql.count("shift_id = 'shift_1_dev'") == 2


def test_rejects_and_dqx_are_not_shift_filtered():
    """Neither table has a shift_id column; both are already UC-schema scoped."""
    sql = rc.build_uc_counts_sql(
        "cat", "sch", START, END,
        existing={"quality_rejects", "dqx_warnings"},
        shift_id="shift_1_dev",
    )
    assert "shift_id" not in sql


def test_live_oee_is_shift_scoped():
    """Otherwise the newest row may belong to the other environment's producer."""
    sql = rc.build_lakebase_oee_sql("shift_1_dev")
    assert "WHERE shift_id = 'shift_1_dev'" in sql
    assert " AND shift_id" not in sql


def test_no_shift_configured_means_no_filter():
    assert rc.shift_predicate(None) == ""
    assert "shift_id" not in rc.build_lakebase_counts_sql(START, END)


@pytest.mark.parametrize("bad", ["shift'; DROP TABLE x --", "a b", "x' OR '1'='1"])
def test_malformed_shift_is_rejected_rather_than_escaped(bad):
    """Wrong numbers on a trust panel are worse than a visible error."""
    with pytest.raises(ValueError):
        rc.shift_predicate(bad)


def test_reconcile_reports_the_shift_it_compared():
    out = rc.reconcile(
        uc_query=lambda sql: [],
        pg_query=lambda sql: [],
        catalog="cat",
        schema="sch",
        shift_id="shift_1_dev",
    )
    assert out["shift_id"] == "shift_1_dev"


# ── explained vs unexplained ──────────────────────────────────────────────
def test_perfect_agreement_is_clean():
    r = rc.classify_entity(lakebase=1000, bronze=1000, gold=1000, rejected=0)
    assert r["unexplained"] == 0
    assert r["status"] == "ok"


def test_qc_rejects_are_explained_and_do_not_count_as_drift():
    """Gold legitimately trails bronze by the rejected rows — that is by design."""
    r = rc.classify_entity(lakebase=1000, bronze=1000, gold=990, rejected=10)
    assert r["explained_rejected"] == 10
    assert r["qc_delta"] == 0
    assert r["unexplained"] == 0
    assert r["status"] == "ok"


def test_rows_lost_between_live_and_bronze_are_unexplained():
    r = rc.classify_entity(lakebase=1000, bronze=940, gold=940, rejected=0)
    assert r["ingest_delta"] == 60
    assert r["unexplained"] == 60
    assert r["status"] == "error"


def test_rows_vanishing_in_the_qc_hop_without_a_reject_record_are_unexplained():
    r = rc.classify_entity(lakebase=1000, bronze=1000, gold=990, rejected=0)
    assert r["qc_delta"] == 10
    assert r["unexplained"] == 10
    assert r["status"] == "error"


def test_extra_rows_downstream_are_also_drift():
    """Duplicates are as wrong as losses, so magnitude (not sign) drives status."""
    r = rc.classify_entity(lakebase=1000, bronze=1050, gold=1050, rejected=0)
    assert r["ingest_delta"] == -50
    assert r["unexplained"] == 50
    assert r["status"] == "error"


def test_tolerance_downgrades_tiny_drift_to_warn():
    r = rc.classify_entity(
        lakebase=10_000, bronze=9_995, gold=9_995, rejected=0, tolerance_fraction=0.01
    )
    assert r["unexplained"] == 5
    assert r["status"] == "warn"


def test_missing_live_path_yields_unknown_not_false_drift():
    """A Lakebase outage must not be reported as millions of missing rows."""
    r = rc.classify_entity(lakebase=None, bronze=1000, gold=1000, rejected=0)
    assert r["status"] == "unknown"
    assert r["ingest_delta"] is None


# ── OEE value spot-check ──────────────────────────────────────────────────
def test_oee_check_flags_the_x100_scale_bug():
    """The regression that matching row counts completely missed."""
    r = rc.compare_oee(live=74.4, governed=7440.0)
    assert r["status"] == "error"
    assert "range" in (r["note"] or "")


def test_oee_check_passes_when_paths_agree():
    r = rc.compare_oee(live=74.4, governed=74.6)
    assert r["status"] == "ok"


@pytest.mark.parametrize("live,governed", [(1.0, 74.0), (0.5, 50.0), (0.8, 80.0)])
def test_low_live_oee_is_not_mistaken_for_a_scale_bug(live, governed):
    """`oee_to_now` is cumulative shift-to-date: it starts near zero and climbs.

    A ratio-based "is it ~100x?" test flags a healthy line a minute into its
    shift as corrupted, turning the headline verdict red during normal startup.
    Both values here are legitimate percentages, so this must not be an error.
    """
    r = rc.compare_oee(live=live, governed=governed)
    assert r["status"] != "error", r["note"]


def test_oee_out_of_range_is_an_error_regardless_of_ratio():
    """OEE is definitionally 0-100, so >100 is impossible on its own terms."""
    assert rc.compare_oee(live=95.0, governed=101.0)["status"] == "error"
    assert rc.compare_oee(live=150.0, governed=74.0)["status"] == "error"


def test_oee_check_warns_outside_tolerance_without_crying_scale_bug():
    r = rc.compare_oee(live=50.0, governed=70.0)
    assert r["status"] == "warn"


def test_oee_check_unknown_when_a_side_is_missing():
    assert rc.compare_oee(live=None, governed=74.0)["status"] == "unknown"


def test_oee_zero_live_does_not_divide_by_zero():
    assert rc.compare_oee(live=0.0, governed=0.0)["status"] == "ok"


# ── count coercion ────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [(5, 5), ("5", 5), ("5.0", 5), (0, 0), ("0", 0)])
def test_counts_coerce_from_the_shapes_engines_actually_return(raw, expected):
    assert rc._as_int(raw) == expected


@pytest.mark.parametrize("bad", [None, "abc", object(), ""])
def test_unreadable_count_is_unknown_not_zero(bad):
    """Zero would be indistinguishable from "every row is missing"."""
    assert rc._as_int(bad) is None


def test_unreadable_bronze_count_does_not_manufacture_drift():
    r = rc.classify_entity(lakebase=1000, bronze=None, gold=None, rejected=0)
    assert r["unexplained"] == 0
    assert r["status"] == "unknown"


def test_unreadable_reject_count_blocks_a_clean_qc_verdict():
    """Treating it as zero would reclassify explained rows as drift."""
    r = rc.classify_entity(lakebase=100, bronze=100, gold=90, rejected=None)
    assert r["qc_delta"] is None
    assert r["status"] == "unknown"


@pytest.mark.parametrize("bad", ["abc", None, float("nan")])
def test_non_numeric_metric_does_not_raise(bad):
    """A bad OEE value must not take down the counts, which are independent."""
    assert rc._as_float(bad) is None


# ── governed model semantics ──────────────────────────────────────────────
def test_upsert_targets_are_declared_so_they_are_not_count_compared():
    kinds = {g.key: g.kind for g in rc.GOVERNED_TABLES}
    assert kinds["production_line"] == "upsert"
    assert kinds["stock_balance"] == "upsert"
    assert kinds["failure_record"] == "append"


def test_incident_tables_are_marked_incident_driven():
    incident = {g.key for g in rc.GOVERNED_TABLES if g.incident_driven}
    assert incident == {"failure_record", "inspection_result", "ncr"}


def test_only_dual_written_streams_are_reconciled():
    """telemetry / quality_checks have no live twin and must not be faked in."""
    keys = {e.key for e in rc.ENTITIES}
    assert keys == {"machine_state", "line_kpi", "inventory_status", "event_log"}


def test_event_log_uses_its_own_timestamp_column_on_both_paths():
    e = next(e for e in rc.ENTITIES if e.key == "event_log")
    assert e.bronze_ts == "timestamp_start"
    assert e.lakebase_ts == "timestamp_start"


# ── end-to-end assembly with injected queries ─────────────────────────────
def _fake_uc(rows_by_marker: dict[str, list[dict]]):
    def _q(sql: str) -> list[dict]:
        for marker, rows in rows_by_marker.items():
            if marker in sql:
                return rows
        return []

    return _q


def test_reconcile_reports_clean_when_both_paths_agree():
    """A clean verdict requires EVERY dual-written stream to be measurable.

    machine_state carries traffic (with rejects); the sparse streams sit at zero
    on both sides, which is agreement, not absence.
    """
    volumes = {"machine_state": 100, "line_kpi": 20, "inventory_status": 30, "event_log": 0}
    uc = _fake_uc({
        "information_schema": [
            {"table_name": t} for t in (
                "machine_state_bronze", "gold_history_machine_state",
                "line_kpi_bronze", "gold_history_line_kpi",
                "inventory_status_bronze", "gold_history_inventory_status",
                "event_log_bronze", "gold_history_event_log",
                "quality_rejects",
            )
        ],
        "'bronze' AS layer": [
            *[{"entity": k, "layer": "bronze", "n": v} for k, v in volumes.items()],
            # machine_state loses 5 rows to the QC gate, and records why.
            *[
                {"entity": k, "layer": "gold", "n": v - (5 if k == "machine_state" else 0)}
                for k, v in volumes.items()
            ],
            {"entity": "machine_state", "layer": "rejected", "n": 5},
        ],
    })
    out = rc.reconcile(
        uc_query=uc,
        pg_query=lambda sql: [{"entity": k, "n": v} for k, v in volumes.items()],
        catalog="cat",
        schema="sch",
    )
    ms = next(e for e in out["entities"] if e["key"] == "machine_state")
    assert ms["unexplained"] == 0
    assert ms["explained_rejected"] == 5
    assert out["total_unexplained"] == 0
    assert out["overall"] == "ok"


def test_one_unmeasurable_stream_prevents_a_clean_verdict():
    """Partial visibility must not be reported as agreement."""
    uc = _fake_uc({
        "information_schema": [
            {"table_name": t} for t in ("machine_state_bronze", "gold_history_machine_state")
        ],
        "'bronze' AS layer": [
            {"entity": "machine_state", "layer": "bronze", "n": 10},
            {"entity": "machine_state", "layer": "gold", "n": 10},
        ],
    })
    out = rc.reconcile(
        uc_query=uc,
        pg_query=lambda sql: [{"entity": "machine_state", "n": 10}],
        catalog="cat",
        schema="sch",
    )
    assert out["total_unexplained"] == 0
    assert out["overall"] == "unknown"


def test_reconcile_degrades_when_lakebase_is_down():
    """Analytics counts must still come back, flagged rather than failed."""
    def _pg(_sql: str):
        raise RuntimeError("connection refused")

    uc = _fake_uc({
        "information_schema": [{"table_name": "machine_state_bronze"}],
        "'bronze' AS layer": [{"entity": "machine_state", "layer": "bronze", "n": 42}],
    })
    out = rc.reconcile(uc_query=uc, pg_query=_pg, catalog="cat", schema="sch")

    assert out["live_path_available"] is False
    assert any("live-path" in w for w in out["warnings"])
    ms = next(e for e in out["entities"] if e["key"] == "machine_state")
    assert ms["bronze_rows"] == 42
    assert ms["status"] == "unknown"


def test_reconcile_surfaces_unexplained_drift_as_error():
    uc = _fake_uc({
        "information_schema": [
            {"table_name": t} for t in ("line_kpi_bronze", "gold_history_line_kpi")
        ],
        "'bronze' AS layer": [
            {"entity": "line_kpi", "layer": "bronze", "n": 80},
            {"entity": "line_kpi", "layer": "gold", "n": 80},
        ],
    })
    out = rc.reconcile(
        uc_query=uc,
        pg_query=lambda sql: [{"entity": "line_kpi", "n": 100}],
        catalog="cat",
        schema="sch",
    )
    assert out["total_unexplained"] == 20
    assert out["overall"] == "error"


def _governed_discovery_rows():
    """Discovery rows in the real shape: schema-qualified, one row per table."""
    return [
        {"table_schema": g.schema, "table_name": g.table} for g in rc.GOVERNED_TABLES
    ]


class TestQuietStreamsDoNotPoisonTheVerdict:
    """A stream with no traffic is idle, not unmeasurable.

    Caught on a live dev deploy: event_log is empty at steady state (incidents
    are rare), so `gold_history_event_log` had never been created and its count
    came back None. That made the entity "unknown", which propagated to the
    whole panel — the overall verdict read "unknown" forever even though every
    busy stream matched exactly and unexplained was 0.
    """

    def test_a_stream_with_no_rows_on_either_plane_is_idle(self):
        r = rc.classify_entity(lakebase=0, bronze=0, gold=None, rejected=0)
        assert r["status"] == "idle"

    def test_idle_does_not_drag_the_overall_verdict_off_ok(self):
        busy = rc.classify_entity(lakebase=100, bronze=100, gold=100, rejected=0)
        idle = rc.classify_entity(lakebase=0, bronze=0, gold=None, rejected=0)
        assert busy["status"] == "ok"
        assert idle["status"] == "idle"
        assert rc._overall_status([busy, idle], {"status": "ok"}) == "ok"

    def test_every_stream_quiet_is_reported_as_idle_not_ok(self):
        """No traffic means nothing was verified — don't claim agreement."""
        idle = rc.classify_entity(lakebase=0, bronze=0, gold=None, rejected=0)
        assert rc._overall_status([idle, idle], {"status": "ok"}) == "idle"

    def test_a_genuinely_unmeasurable_live_plane_is_still_unknown(self):
        """Idle must not swallow the case the unknown status exists for."""
        r = rc.classify_entity(lakebase=None, bronze=0, gold=0, rejected=0)
        assert r["status"] == "unknown"

    def test_rows_in_gold_with_none_upstream_is_drift_not_idle(self):
        """Duplicates downstream of an empty source are the loudest failure."""
        r = rc.classify_entity(lakebase=0, bronze=0, gold=50, rejected=0)
        assert r["status"] == "error"


def test_reconcile_marks_empty_incident_tables_as_expected_empty():
    uc = _fake_uc({
        "information_schema": _governed_discovery_rows(),
        "AS key": [
            {"key": "failure_record", "n": 0, "last_ts": None},
            {"key": "production_line", "n": 1, "last_ts": "2026-07-30 13:00:00"},
        ],
    })
    out = rc.reconcile(
        uc_query=uc,
        pg_query=lambda sql: [],
        catalog="cat",
        schema="sch",
        mvm_catalog="mvm",
    )
    by_key = {g["key"]: g for g in out["governed"]}
    assert by_key["failure_record"]["status"] == "expected_empty"
    assert by_key["production_line"]["status"] == "populated"
    # Present in discovery but absent from the counts result → genuinely unknown,
    # which must not be reported as an empty (and therefore healthy) table.
    assert by_key["ncr"]["status"] == "absent"


class TestAnUnownedGovernedModelIsNotClaimed:
    """The MVM schemas are catalog-level and carry no environment suffix.

    Two environments pointing at one catalog therefore share a single physical
    governed model. Observed on a live prod deploy: prod does not run the
    streaming-silver writer, yet its panel showed the governed tables populated
    and an OEE spot-check of 74.3 live vs 74.4 governed — a clean-looking
    cross-plane pass built entirely on dev's rows. Numbers that plausible are
    worse than an obvious error, because nobody thinks to question them.
    """

    def _uc(self):
        return _fake_uc({
            "information_schema": _governed_discovery_rows(),
            "AS key": [{"key": "production_line", "n": 1, "last_ts": "2026-07-30 13:00:00"}],
        })

    def test_governed_rows_are_withheld_when_this_env_does_not_write_them(self):
        out = rc.reconcile(
            uc_query=self._uc(), pg_query=lambda sql: [],
            catalog="cat", schema="sch", mvm_catalog="mvm",
            governed_owned=False,
        )
        assert out["governed"] == []
        assert out["governed_owned"] is False

    def test_no_oee_verdict_is_issued_against_another_environments_value(self):
        out = rc.reconcile(
            uc_query=self._uc(), pg_query=lambda sql: [],
            catalog="cat", schema="sch", mvm_catalog="mvm",
            governed_owned=False,
        )
        assert out["oee_check"]["governed"] is None
        assert out["oee_check"]["delta"] is None
        assert out["oee_check"]["status"] == "not_enabled"

    def test_the_governed_tables_are_never_even_queried(self):
        """Withholding must happen at the source, not by filtering afterwards."""
        seen: list[str] = []

        def _uc(sql: str):
            seen.append(sql)
            return []

        rc.reconcile(uc_query=_uc, pg_query=lambda s: [], catalog="cat",
                     schema="sch", mvm_catalog="mvm", governed_owned=False)
        assert not [s for s in seen if "`mvm`" in s]

    def test_not_enabled_does_not_drag_the_overall_verdict_down(self):
        out = rc.reconcile(
            uc_query=self._uc(), pg_query=lambda sql: [],
            catalog="cat", schema="sch", mvm_catalog="mvm",
            governed_owned=False,
        )
        assert out["overall"] in {"ok", "idle", "unknown"}
        assert out["overall"] not in {"warn", "error"}

    def test_owning_the_model_is_still_the_default(self):
        """An ordinary single-environment deploy must be unaffected."""
        out = rc.reconcile(
            uc_query=self._uc(), pg_query=lambda sql: [],
            catalog="cat", schema="sch", mvm_catalog="mvm",
        )
        assert out["governed_owned"] is True
        assert {g["key"] for g in out["governed"]}


def test_governed_tables_are_matched_against_their_own_schema():
    """A table name existing in a DIFFERENT schema must not count as present.

    Without per-schema matching, `quality.ncr` would look installed just because
    some other schema happened to contain a table called `ncr`.
    """
    misfiled = [{"table_schema": "asset", "table_name": g.table} for g in rc.GOVERNED_TABLES]
    out = rc.reconcile(
        uc_query=_fake_uc({"information_schema": misfiled, "AS key": []}),
        pg_query=lambda sql: [],
        catalog="cat", schema="sch", mvm_catalog="mvm",
    )
    by_key = {g["key"]: g for g in out["governed"]}
    assert by_key["ncr"]["status"] == "absent", "quality.ncr is not asset.ncr"
    assert by_key["failure_record"]["status"] == "absent", "counts query returned nothing"


def test_governed_discovery_is_a_single_query_for_all_schemas():
    seen: list[str] = []

    def _uc(sql: str):
        seen.append(sql)
        return _governed_discovery_rows() if "information_schema" in sql else []

    rc.reconcile(uc_query=_uc, pg_query=lambda s: [], catalog="cat", schema="sch",
                 mvm_catalog="mvm")
    discovery = [s for s in seen if "information_schema" in s and "`mvm`" in s]
    assert len(discovery) == 1, f"expected one governed discovery query, got {len(discovery)}"
    for schema in {g.schema for g in rc.GOVERNED_TABLES}:
        assert f"'{schema}'" in discovery[0]


@pytest.mark.parametrize("minutes,guard", [(15, 120), (60, 300), (1, 0)])
def test_reconcile_echoes_the_window_it_actually_used(minutes, guard):
    out = rc.reconcile(
        uc_query=lambda sql: [],
        pg_query=lambda sql: [],
        catalog="cat",
        schema="sch",
        window_minutes=minutes,
        lag_guard_seconds=guard,
        now=NOW,
    )
    start, end = rc.window_bounds(minutes, guard, NOW)
    assert out["window"]["minutes"] == minutes
    assert out["window"]["lag_guard_seconds"] == guard
    assert out["window"]["start"] == start.isoformat()
    assert out["window"]["end"] == end.isoformat()


def test_reconcile_queries_both_engines_with_the_same_bounds():
    """The whole design rests on this; assert it end to end."""
    seen: list[str] = []
    rc.reconcile(
        uc_query=lambda sql: seen.append(sql) or [],
        pg_query=lambda sql: seen.append(sql) or [],
        catalog="cat",
        schema="sch",
        now=NOW,
    )
    stamps = {"2026-07-30 19:43:00+00:00", "2026-07-30 19:58:00+00:00"}
    dated = [s for s in seen if any(t in s for t in stamps)]
    assert dated, "no query carried the pinned bounds"
    for sql in dated:
        assert all(t in sql for t in stamps)
