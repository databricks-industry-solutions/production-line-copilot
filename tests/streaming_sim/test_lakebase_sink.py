"""Unit tests for ``src.streaming_sim.lakebase_sink``.

We don't exercise a real Postgres here — the goal is to lock in:
  1. SQL shape (correct table, correct ON CONFLICT clause, ``VALUES %s``
     for execute_values).
  2. Row preparation (timestamp coercion, key renames, NULL safety).
  3. Failure isolation (sink swallows errors, never raises into the
     producer's Zerobus tick loop).
  4. Disable knob: ``maybe_build_sink_from_env`` honours
     ``SIM_LAKEBASE_DIRECT_WRITE=false`` and missing env vars.

These guard the producer's tick loop against a regression that would
either bubble Lakebase errors into the Zerobus path or silently change
the SQL shape so ``execute_values`` no longer concatenates rows into a
single INSERT.
"""

from __future__ import annotations

import unittest.mock as mock

import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Strip any inherited Lakebase env so each test sets exactly what it needs."""
    for k in (
        "SIM_LAKEBASE_DIRECT_WRITE", "PGHOST", "PGUSER", "PGDATABASE",
        "PGPORT", "PGSSLMODE", "LAKEBASE_ENDPOINT",
    ):
        monkeypatch.delenv(k, raising=False)


# ── SQL shape (regression guards) ─────────────────────────────────────────


def test_history_sql_uses_execute_values_template():
    """All four _history INSERTs must end with ``VALUES %s`` so psycopg2's
    ``execute_values`` can splice multi-row tuples into one round-trip.
    Catches accidental find/replace that would degrade to per-row INSERTs.
    """
    from src.streaming_sim import lakebase_sink as ls
    for name in ("_MS_HISTORY_SQL", "_KPI_HISTORY_SQL", "_INV_HISTORY_SQL", "_EVT_HISTORY_SQL"):
        sql = getattr(ls, name)
        assert "VALUES %s" in sql, f"{name} must use 'VALUES %s' for execute_values"
        assert "ON CONFLICT" in sql, f"{name} must declare ON CONFLICT"
        assert "DO NOTHING" in sql, f"{name} must use DO NOTHING (append semantics)"
        assert "DO UPDATE" not in sql, f"{name} must NOT update on conflict"


def test_history_sql_targets_history_tables():
    """Sanity check: each SQL targets the right ai_serving._history table.
    Catches copy/paste swaps that would silently misroute writes."""
    from src.streaming_sim import lakebase_sink as ls
    assert "ai_serving.machine_state_history" in ls._MS_HISTORY_SQL
    assert "ai_serving.line_kpi_history" in ls._KPI_HISTORY_SQL
    assert "ai_serving.inventory_status_history" in ls._INV_HISTORY_SQL
    assert "ai_serving.event_log_history" in ls._EVT_HISTORY_SQL


# ── Row preparation ───────────────────────────────────────────────────────


def test_us_to_dt_roundtrips_to_utc():
    """Producer ts is microseconds-since-epoch (Spark/Arrow scale); we must
    convert to a tz-aware UTC datetime for psycopg2 → TIMESTAMPTZ."""
    from datetime import datetime, timezone

    from src.streaming_sim.lakebase_sink import _us_to_dt
    target = datetime(2026, 4, 29, 7, 0, 0, tzinfo=timezone.utc)
    ts_us = int(target.timestamp() * 1_000_000)
    dt = _us_to_dt(ts_us)
    assert dt == target
    assert dt.tzinfo is not None and dt.utcoffset().total_seconds() == 0


def test_write_machine_state_renames_throughput_and_coerces_ts(monkeypatch):
    """machine_state rows from snapshot_to_dicts use 'throughput' (not
    'throughput_per_min') and ts as int microseconds. The sink must rename
    the key and convert the ts before handing rows to execute_values."""
    from src.streaming_sim import lakebase_sink as ls

    captured = {}

    def fake_execute_values(self_, sql, template, rows):
        captured["sql"] = sql
        captured["template"] = template
        captured["rows"] = rows
        return len(rows)

    monkeypatch.setattr(ls.LakebaseSink, "_execute_values", fake_execute_values)

    sink = ls.LakebaseSink(
        pg_host="h", pg_user="u", pg_database="d", lakebase_endpoint="e",
    )
    sink.write_machine_state([
        {
            "shift_id": "shift_1",
            "machine_id": "F1",
            "ts": 1777705200_000_000,
            "status": "running",
            "throughput": 100.5,  # snapshot_to_dicts uses 'throughput' not 'throughput_per_min'
            "speed_pct": 95.0,
            "alarm_count": 0,
        },
    ])
    assert captured["sql"] is ls._MS_HISTORY_SQL
    row = captured["rows"][0]
    assert row["throughput_per_min"] == 100.5
    assert row["ts"].year == 2026  # ts coerced to datetime
    assert row["alarm_count"] == 0


def test_write_event_handles_null_timestamp_end():
    """incident_start events have timestamp_end=None — must NOT raise on
    _us_to_dt(None). The sink should pass None through to Postgres."""
    from src.streaming_sim import lakebase_sink as ls

    captured = {}

    def fake_execute_values(self_, sql, template, rows):
        captured["rows"] = rows
        return len(rows)

    sink = ls.LakebaseSink(
        pg_host="h", pg_user="u", pg_database="d", lakebase_endpoint="e",
    )
    with mock.patch.object(ls.LakebaseSink, "_execute_values", fake_execute_values):
        sink.write_event({
            "event_id": "EVT-1",
            "shift_id": "shift_1",
            "machine_id": "FILLER",
            "event_type": "machine_breakdown",
            "severity": "high",
            "timestamp_start": 1777705200_000_000,
            "timestamp_end": None,
            "duration_minutes": 0.0,
            "root_cause": "machine_breakdown",
            "description": "Incident started: machine_breakdown",
            "corrective_action": "",
        })
    row = captured["rows"][0]
    assert row["timestamp_end"] is None
    assert row["timestamp_start"] is not None


def test_write_inventory_attaches_tick_ts():
    """snapshot_inventory does NOT carry ts (the producer adds it inline for
    Zerobus). The sink must attach the tick's now_us so PK is complete."""
    from src.streaming_sim import lakebase_sink as ls

    captured = {}

    def fake_execute_values(self_, sql, template, rows):
        captured["rows"] = rows
        return len(rows)

    sink = ls.LakebaseSink(
        pg_host="h", pg_user="u", pg_database="d", lakebase_endpoint="e",
    )
    with mock.patch.object(ls.LakebaseSink, "_execute_values", fake_execute_values):
        sink.write_inventory(
            [
                {
                    "shift_id": "shift_1",
                    "material_id": "CARTON_24PACK",
                    "on_hand_qty": 10500.0,
                    "safety_stock": 1000.0,
                    "reorder_point": 2000.0,
                    "status": "OK",
                    "consumption_rate_per_hour": 50.0,
                },
            ],
            ts_us=1777705200_000_000,
        )
    row = captured["rows"][0]
    assert row["ts"].year == 2026  # attached from ts_us
    assert row["material_id"] == "CARTON_24PACK"


# ── Per-tick batched write (commit round-trip optimization) ──────────────


def test_write_tick_batches_three_inserts_in_one_transaction():
    """write_tick must call _execute_values_batched ONCE with all three
    (machine_state, line_kpi, inventory) ops, not three separate writes.
    This is what saves the producer 2 commit round-trips per tick.
    """
    from src.streaming_sim import lakebase_sink as ls

    captured = []

    def fake_batched(self_, ops):
        captured.append(ops)
        return sum(len(rows) for _, _, rows in ops)

    sink = ls.LakebaseSink(
        pg_host="h", pg_user="u", pg_database="d", lakebase_endpoint="e",
    )
    with mock.patch.object(ls.LakebaseSink, "_execute_values_batched", fake_batched):
        n = sink.write_tick(
            machine_rows=[
                {"shift_id": "s", "machine_id": "F1", "ts": 1777705200_000_000,
                 "status": "running", "throughput": 100.0, "speed_pct": 95.0,
                 "alarm_count": 0},
            ],
            kpi_row={
                "shift_id": "s", "ts": 1777705200_000_000,
                "machines_running": 1, "machines_stopped": 0,
                "machines_degraded": 0, "machines_starved": 0,
                "total_throughput": 100.0, "cumulative_cases": 5.0,
                "avg_speed_pct": 95.0, "availability_to_now": 0.99,
                "performance_to_now": 0.95, "quality_to_now": 1.0,
                "oee_to_now": 0.94,
            },
            inventory_rows=[
                {"shift_id": "s", "material_id": "M1",
                 "on_hand_qty": 1000.0, "safety_stock": 100.0,
                 "reorder_point": 200.0, "status": "OK",
                 "consumption_rate_per_hour": 50.0},
            ],
            ts_us=1777705200_000_000,
        )

    assert len(captured) == 1, "write_tick must call _execute_values_batched exactly once"
    ops = captured[0]
    assert len(ops) == 3, "must batch all three INSERTs into one transaction"
    # Verify each op targets its expected table.
    sqls = [op[0] for op in ops]
    assert any("machine_state_history" in sql for sql in sqls)
    assert any("line_kpi_history" in sql for sql in sqls)
    assert any("inventory_status_history" in sql for sql in sqls)
    assert n == 3  # 1 machine + 1 KPI + 1 inventory


def test_write_tick_skips_empty_categories():
    """Empty machine_rows / inventory_rows / None kpi_row must NOT add empty
    ops to the batch — saves cursor work and keeps the SQL plan cache warm.
    Producer ticks where, e.g., inventory is unchanged (no consumption) hit
    this path."""
    from src.streaming_sim import lakebase_sink as ls

    captured = []
    sink = ls.LakebaseSink(
        pg_host="h", pg_user="u", pg_database="d", lakebase_endpoint="e",
    )
    with mock.patch.object(ls.LakebaseSink, "_execute_values_batched",
                           lambda self_, ops: captured.append(ops) or 0):
        sink.write_tick(machine_rows=[], kpi_row=None, inventory_rows=[], ts_us=0)

    # write_tick still calls the batched method (single dispatch), but with
    # an empty ops list so the actual Postgres trip is skipped inside.
    assert captured == [[]]


# ── Failure isolation ─────────────────────────────────────────────────────


def test_execute_values_swallows_exceptions(monkeypatch):
    """Lakebase failures must NEVER raise into the producer's tick loop —
    they'd take down the Zerobus path too. Sink should reset its connection
    and return 0, so the next tick gets a fresh connect attempt.
    """
    from src.streaming_sim import lakebase_sink as ls

    sink = ls.LakebaseSink(
        pg_host="h", pg_user="u", pg_database="d", lakebase_endpoint="e",
    )

    fake_conn = mock.MagicMock()
    fake_conn.cursor.side_effect = RuntimeError("connection refused")
    monkeypatch.setattr(sink, "_ensure_connection", lambda: fake_conn)

    n = sink._execute_values("INSERT ... VALUES %s", "(%(a)s)", [{"a": 1}])
    assert n == 0  # never raised
    assert sink._conn is None  # connection reset for next attempt


def test_empty_input_short_circuits():
    """Empty row list must not even try to connect — saves a Lakebase call
    on the producer's idle ticks (e.g. when no event was emitted)."""
    from src.streaming_sim import lakebase_sink as ls

    sink = ls.LakebaseSink(
        pg_host="h", pg_user="u", pg_database="d", lakebase_endpoint="e",
    )
    # Even without any psycopg2 mock these must return 0 cleanly.
    assert sink.write_machine_state([]) == 0
    assert sink.write_inventory([], ts_us=0) == 0
    assert sink.write_line_kpi({}) == 0
    assert sink.write_event({}) == 0


# ── Disable knob ──────────────────────────────────────────────────────────


def test_maybe_build_sink_disabled_via_env_flag(monkeypatch):
    """SIM_LAKEBASE_DIRECT_WRITE=false short-circuits even when env is fully
    populated. Lets us roll back to the consumer-driven _history sink path
    without redeploying the bundle."""
    from src.streaming_sim import lakebase_sink as ls

    monkeypatch.setenv("SIM_LAKEBASE_DIRECT_WRITE", "false")
    monkeypatch.setenv("PGHOST", "h")
    monkeypatch.setenv("PGUSER", "u")
    monkeypatch.setenv("LAKEBASE_ENDPOINT", "e")
    assert ls.maybe_build_sink_from_env() is None


def test_maybe_build_sink_skipped_if_missing_env(monkeypatch):
    """Workshop / smoke contexts run the producer without Lakebase env.
    Sink must skip cleanly (no exception, no partial config).
    """
    from src.streaming_sim import lakebase_sink as ls

    # Only PGHOST set — PGUSER + LAKEBASE_ENDPOINT missing
    monkeypatch.setenv("PGHOST", "h")
    assert ls.maybe_build_sink_from_env() is None


def test_maybe_build_sink_constructed_when_env_complete(monkeypatch):
    """All three required env vars present → sink instantiates with the
    expected attributes. Doesn't mint a token (lazy on first write)."""
    from src.streaming_sim import lakebase_sink as ls

    monkeypatch.setenv("PGHOST", "host.example.com")
    monkeypatch.setenv("PGUSER", "user@example.com")
    monkeypatch.setenv("LAKEBASE_ENDPOINT", "projects/p/branches/b/endpoints/primary")
    monkeypatch.setenv("PGDATABASE", "databricks_postgres")

    sink = ls.maybe_build_sink_from_env()
    assert sink is not None
    assert sink._host == "host.example.com"
    assert sink._user == "user@example.com"
    assert sink._database == "databricks_postgres"
    assert sink._endpoint == "projects/p/branches/b/endpoints/primary"
    assert sink._conn is None  # lazy
    assert sink._token is None  # not minted yet


# ── Rehydration reads (Clean Restart correctness) ─────────────────────────
#
# Producer rehydration was switched from bronze (which Clean Restart cannot
# wipe) to Lakebase ``ai_serving.*_history`` (which Clean Restart DOES wipe
# via ``_truncate_ai_serving_demo_state``). That makes the producer's
# "resume from previous state" path naturally fall back to ``init_inventory()``
# / ``cumulative_cases=0.0`` on Clean Restart, regardless of whether the
# ``--start-fresh`` python_params override actually landed.
#
# These tests pin the new SELECT shapes + failure isolation so a future
# refactor can't silently re-introduce the "previous values, depleting"
# bug after Clean Restart.


def _stub_conn(*, fetchone=None, fetchall=None):
    """Build a minimal psycopg2-like cursor/connection stub."""
    cur = mock.MagicMock()
    cur.fetchone.return_value = fetchone
    cur.fetchall.return_value = fetchall or []
    cur.__enter__ = mock.MagicMock(return_value=cur)
    cur.__exit__ = mock.MagicMock(return_value=None)
    conn = mock.MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


def test_read_latest_cumulative_cases_returns_float():
    """Happy path: SELECT returns a single cumulative_cases value."""
    from src.streaming_sim import lakebase_sink as ls

    sink = ls.LakebaseSink(
        pg_host="h", pg_user="u", pg_database="d", lakebase_endpoint="e",
    )
    conn, cur = _stub_conn(fetchone=(12_345.0,))
    with mock.patch.object(ls.LakebaseSink, "_ensure_connection", return_value=conn):
        got = sink.read_latest_cumulative_cases("shift_1")
    assert got == 12_345.0
    sql = cur.execute.call_args.args[0]
    assert "FROM ai_serving.line_kpi_history" in sql
    assert "WHERE shift_id = %s" in sql
    assert "ORDER BY ts DESC LIMIT 1" in sql
    # SELECT-only path — must rollback to avoid hanging transactions.
    conn.rollback.assert_called_once()


def test_read_latest_cumulative_cases_scopes_to_since_ts():
    """``since_ts`` scopes rehydration to the current shift window."""
    from datetime import datetime, timezone

    from src.streaming_sim import lakebase_sink as ls

    sink = ls.LakebaseSink(
        pg_host="h", pg_user="u", pg_database="d", lakebase_endpoint="e",
    )
    window_start = datetime(2026, 7, 28, 8, 0, tzinfo=timezone.utc)
    conn, cur = _stub_conn(fetchone=(4_200.0,))
    with mock.patch.object(ls.LakebaseSink, "_ensure_connection", return_value=conn):
        got = sink.read_latest_cumulative_cases("shift_1", since_ts=window_start)
    assert got == 4_200.0
    sql, params = cur.execute.call_args.args
    # Window-scoped variant uses an explicit ts >= %s bound, not the interval.
    assert "ts >= %s" in sql
    assert "INTERVAL" not in sql
    assert params == ("shift_1", window_start)


def test_read_latest_cumulative_cases_returns_none_on_empty():
    """Empty Lakebase (post-Clean-Restart) → None → caller keeps init 0.0."""
    from src.streaming_sim import lakebase_sink as ls

    sink = ls.LakebaseSink(
        pg_host="h", pg_user="u", pg_database="d", lakebase_endpoint="e",
    )
    conn, _cur = _stub_conn(fetchone=None)
    with mock.patch.object(ls.LakebaseSink, "_ensure_connection", return_value=conn):
        got = sink.read_latest_cumulative_cases("shift_1")
    assert got is None


def test_read_latest_cumulative_cases_swallows_query_errors():
    """A psycopg2 hiccup must never crash the producer's startup path."""
    from src.streaming_sim import lakebase_sink as ls

    sink = ls.LakebaseSink(
        pg_host="h", pg_user="u", pg_database="d", lakebase_endpoint="e",
    )
    with mock.patch.object(
        ls.LakebaseSink, "_ensure_connection",
        side_effect=RuntimeError("connection refused"),
    ):
        assert sink.read_latest_cumulative_cases("shift_1") is None


def test_read_latest_inventory_returns_rows_via_distinct_on():
    """SELECT DISTINCT ON (material_id) collapses to latest row per material."""
    from src.streaming_sim import lakebase_sink as ls

    sink = ls.LakebaseSink(
        pg_host="h", pg_user="u", pg_database="d", lakebase_endpoint="e",
    )
    conn, cur = _stub_conn(fetchall=[
        ("CARTON_24PACK", 10_500.0, "OK"),
        ("MALT", 800.0, "LOW"),
    ])
    with mock.patch.object(ls.LakebaseSink, "_ensure_connection", return_value=conn):
        got = sink.read_latest_inventory("shift_1")
    assert got == [
        {"material_id": "CARTON_24PACK", "on_hand_qty": 10_500.0, "status": "OK"},
        {"material_id": "MALT", "on_hand_qty": 800.0, "status": "LOW"},
    ]
    sql = cur.execute.call_args.args[0]
    assert "DISTINCT ON (material_id)" in sql
    assert "FROM ai_serving.inventory_status_history" in sql
    assert "ORDER BY material_id, ts DESC" in sql


def test_read_latest_inventory_returns_empty_after_wipe():
    """Clean Restart wipes the history → SELECT yields zero rows → []."""
    from src.streaming_sim import lakebase_sink as ls

    sink = ls.LakebaseSink(
        pg_host="h", pg_user="u", pg_database="d", lakebase_endpoint="e",
    )
    conn, _cur = _stub_conn(fetchall=[])
    with mock.patch.object(ls.LakebaseSink, "_ensure_connection", return_value=conn):
        assert sink.read_latest_inventory("shift_1") == []


def test_read_latest_inventory_swallows_query_errors():
    """Any failure → []; producer falls back to init_inventory()."""
    from src.streaming_sim import lakebase_sink as ls

    sink = ls.LakebaseSink(
        pg_host="h", pg_user="u", pg_database="d", lakebase_endpoint="e",
    )
    with mock.patch.object(
        ls.LakebaseSink, "_ensure_connection",
        side_effect=RuntimeError("connection refused"),
    ):
        assert sink.read_latest_inventory("shift_1") == []
