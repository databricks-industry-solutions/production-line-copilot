"""Unit tests for ``pipelines.streaming_quality_sync.split_valid_invalid``.

These tests exercise the streaming QC contract: each bronze table's SQL
predicates decide which rows pass to gold and which are dropped into
``quality_rejects`` with a ``rejected_reason``.
"""

from __future__ import annotations

import importlib

import pytest

# We import the module under test lazily because it sets sys.path / pulls
# heavy Spark deps at import. Tests skip cleanly if PySpark isn't available.
pyspark = pytest.importorskip("pyspark")
from pyspark.sql import SparkSession  # noqa: E402


@pytest.fixture(scope="module")
def spark() -> SparkSession:
    # importorskip above only proves the pyspark *package* is importable; a live
    # session still needs a working JVM/Java. Skip (don't error) when that's
    # missing so a fresh clone with pyspark but no Java doesn't fail the suite.
    try:
        return (
            SparkSession.builder
            .master("local[2]")
            .appName("test_streaming_quality_sync")
            .config("spark.sql.shuffle.partitions", "2")
            .config("spark.sql.session.timeZone", "UTC")
            .getOrCreate()
        )
    except Exception as exc:  # pragma: no cover - environment without a JVM
        pytest.skip(f"Spark session unavailable (needs Java/JVM): {exc}")


@pytest.fixture(scope="module")
def split_fn(spark):
    # Late-import — fixture forces the spark session up first.
    mod = importlib.import_module("pipelines.streaming_quality_sync")
    return mod.split_valid_invalid, mod.QC_RULES


def _to_dicts(df) -> list[dict]:
    return [r.asDict(recursive=True) for r in df.collect()]


# ── machine_state ────────────────────────────────────────────────────────


def test_machine_state_passes_clean_rows(spark, split_fn):
    split_valid_invalid, _ = split_fn
    df = spark.createDataFrame([
        ("F1", "shift_1", "2026-04-29T07:00:00", "running", 100.0, 95.0, 0),
    ], ["machine_id", "shift_id", "ts", "status", "throughput_per_min", "speed_pct", "alarm_count"])
    valid, invalid = split_valid_invalid(df, "machine_state")
    assert valid.count() == 1
    assert invalid.count() == 0


def test_machine_state_rejects_invalid_status(spark, split_fn):
    split_valid_invalid, _ = split_fn
    df = spark.createDataFrame([
        ("F1", "shift_1", "2026-04-29T07:00:00", "frobnicating", 100.0, 95.0, 0),
    ], ["machine_id", "shift_id", "ts", "status", "throughput_per_min", "speed_pct", "alarm_count"])
    valid, invalid = split_valid_invalid(df, "machine_state")
    assert valid.count() == 0
    rows = _to_dicts(invalid)
    assert len(rows) == 1
    assert rows[0]["rejected_reason"] == "valid_status"
    assert rows[0]["bronze_table"] == "machine_state"


def test_machine_state_rejects_speed_out_of_range(spark, split_fn):
    split_valid_invalid, _ = split_fn
    df = spark.createDataFrame([
        ("F1", "shift_1", "2026-04-29T07:00:00", "running", 100.0, 250.0, 0),
        ("F2", "shift_1", "2026-04-29T07:00:00", "running", 100.0, -1.0, 0),
    ], ["machine_id", "shift_id", "ts", "status", "throughput_per_min", "speed_pct", "alarm_count"])
    valid, invalid = split_valid_invalid(df, "machine_state")
    assert valid.count() == 0
    assert invalid.count() == 2
    reasons = {r["rejected_reason"] for r in _to_dicts(invalid)}
    assert reasons == {"valid_speed"}


def test_machine_state_rejects_null_machine_id(spark, split_fn):
    split_valid_invalid, _ = split_fn
    df = spark.createDataFrame([
        (None, "shift_1", "2026-04-29T07:00:00", "running", 100.0, 95.0, 0),
    ], ["machine_id", "shift_id", "ts", "status", "throughput_per_min", "speed_pct", "alarm_count"])
    valid, invalid = split_valid_invalid(df, "machine_state")
    assert valid.count() == 0
    assert _to_dicts(invalid)[0]["rejected_reason"] == "valid_machine"


def test_machine_state_first_failure_wins(spark, split_fn):
    """When multiple rules fail, the FIRST listed rule is the recorded reason.
    Ensures dashboards group rejects deterministically rather than randomly
    across rule names."""
    split_valid_invalid, _ = split_fn
    # NULL machine_id AND invalid status — valid_machine is listed first.
    df = spark.createDataFrame([
        (None, "shift_1", "2026-04-29T07:00:00", "frobnicating", 100.0, 95.0, 0),
    ], ["machine_id", "shift_id", "ts", "status", "throughput_per_min", "speed_pct", "alarm_count"])
    _, invalid = split_valid_invalid(df, "machine_state")
    assert _to_dicts(invalid)[0]["rejected_reason"] == "valid_machine"


# ── line_kpi ─────────────────────────────────────────────────────────────


def test_line_kpi_passes_minimal_row(spark, split_fn):
    split_valid_invalid, _ = split_fn
    df = spark.createDataFrame([
        ("shift_1", "2026-04-29T07:00:00"),
    ], ["shift_id", "ts"])
    valid, invalid = split_valid_invalid(df, "line_kpi")
    assert valid.count() == 1
    assert invalid.count() == 0


def test_line_kpi_rejects_null_ts(spark, split_fn):
    split_valid_invalid, _ = split_fn
    df = spark.createDataFrame([
        ("shift_1", None),
    ], "shift_id STRING, ts STRING")
    _, invalid = split_valid_invalid(df, "line_kpi")
    assert _to_dicts(invalid)[0]["rejected_reason"] == "valid_ts"


# ── inventory_status ─────────────────────────────────────────────────────


def test_inventory_rejects_negative_qty(spark, split_fn):
    split_valid_invalid, _ = split_fn
    df = spark.createDataFrame([
        ("shift_1", "RAW_001", -5.0, "2026-04-29T07:00:00"),
        ("shift_1", "RAW_002",  3.0, "2026-04-29T07:00:00"),
    ], ["shift_id", "material_id", "on_hand_qty", "ts"])
    valid, invalid = split_valid_invalid(df, "inventory_status")
    assert valid.count() == 1
    invs = _to_dicts(invalid)
    assert invs[0]["rejected_reason"] == "non_negative_qty"


# ── event_log ────────────────────────────────────────────────────────────


def test_event_log_rejects_null_event_id(spark, split_fn):
    split_valid_invalid, _ = split_fn
    df = spark.createDataFrame([
        (None, "F1"),
        ("evt_001", "F1"),
    ], "event_id STRING, machine_id STRING")
    valid, invalid = split_valid_invalid(df, "event_log")
    assert valid.count() == 1
    assert _to_dicts(invalid)[0]["rejected_reason"] == "valid_event"


# ── reject schema invariants ─────────────────────────────────────────────


def test_invalid_carries_metadata_columns(spark, split_fn):
    """Every reject row must have ``bronze_table``, ``rejected_reason`` and
    ``rejected_at`` so the dashboard can group/aggregate consistently."""
    split_valid_invalid, _ = split_fn
    df = spark.createDataFrame([
        (None, "shift_1", "2026-04-29T07:00:00", "running", 100.0, 95.0, 0),
    ], ["machine_id", "shift_id", "ts", "status", "throughput_per_min", "speed_pct", "alarm_count"])
    _, invalid = split_valid_invalid(df, "machine_state")
    cols = set(invalid.columns)
    assert "bronze_table" in cols
    assert "rejected_reason" in cols
    assert "rejected_at" in cols


def test_qc_rules_cover_all_bronze_tables():
    """Compile-time guard: the rules dict must include every bronze table the
    quality-gate is responsible for. Adding a new bronze table without a
    matching rule entry would silently let invalid rows through to Lakebase
    and gold_history.
    """
    mod = importlib.import_module("pipelines.streaming_quality_sync")
    assert set(mod.QC_RULES.keys()) == {
        "machine_state", "line_kpi", "event_log", "inventory_status",
    }
    # Spot-check critical predicates didn't drift.
    assert any(name == "valid_speed" for name, _ in mod.QC_RULES["machine_state"])
    assert any(name == "non_negative_qty" for name, _ in mod.QC_RULES["inventory_status"])


# ── dual-write semantics (append history vs upsert latest) ────────────────


def test_history_sql_uses_do_nothing_not_update():
    """*_history tables are append-only — duplicates from Spark batch retries
    must be silently ignored, NOT overwrite existing rows. ``DO NOTHING`` makes
    retries safe (idempotent at-least-once delivery).

    This guards against an accidental find/replace turning history sinks into
    upsert sinks, which would corrupt the playback cursor (last-write-wins
    instead of append-only)."""
    mod = importlib.import_module("pipelines.streaming_quality_sync")
    for sql_name in ("_MS_HISTORY", "_KPI_HISTORY", "_EVT_HISTORY", "_INV_HISTORY"):
        base_sql, _template = getattr(mod, sql_name)
        assert "ON CONFLICT" in base_sql, f"{sql_name} must declare ON CONFLICT"
        assert "DO NOTHING" in base_sql, f"{sql_name} must use DO NOTHING (append semantics)"
        assert "DO UPDATE" not in base_sql, f"{sql_name} must NOT update on conflict"


def test_latest_sql_uses_do_update_not_nothing():
    """*_latest tables are upserts (cold-start fallback for the App). The PK
    is the partitioning key only (NO ts), so DO UPDATE is what makes them
    "latest"."""
    mod = importlib.import_module("pipelines.streaming_quality_sync")
    for sql_name in ("_MS_LATEST", "_KPI_LATEST", "_EVT_LATEST", "_INV_LATEST"):
        base_sql, _template = getattr(mod, sql_name)
        assert "ON CONFLICT" in base_sql, f"{sql_name} must declare ON CONFLICT"
        assert "DO UPDATE" in base_sql, f"{sql_name} must use DO UPDATE (upsert semantics)"


def test_history_sql_targets_history_tables():
    """Sanity check: the history SQL must INSERT into the *_history tables,
    not the *_latest tables. Catches accidental copy/paste of SQL strings."""
    mod = importlib.import_module("pipelines.streaming_quality_sync")
    assert "ai_serving.machine_state_history" in mod._MS_HISTORY[0]
    assert "ai_serving.line_kpi_history" in mod._KPI_HISTORY[0]
    assert "ai_serving.event_log_history" in mod._EVT_HISTORY[0]
    assert "ai_serving.inventory_status_history" in mod._INV_HISTORY[0]


def test_history_sql_uses_execute_values_template():
    """The history SQL must end with ``VALUES %s`` so psycopg2's
    ``execute_values`` can splice multi-row tuples into one INSERT — this
    is the latency-critical path. The template (second tuple element) must
    use named ``%(...)s`` placeholders matching the SELECT columns so
    ``Row.asDict()`` works without extra mapping.
    """
    mod = importlib.import_module("pipelines.streaming_quality_sync")
    for sql_name in ("_MS_HISTORY", "_KPI_HISTORY", "_EVT_HISTORY", "_INV_HISTORY"):
        base_sql, template = getattr(mod, sql_name)
        assert "VALUES %s" in base_sql, f"{sql_name} must end its INSERT with 'VALUES %s'"
        assert template.startswith("(") and template.endswith(")"), (
            f"{sql_name} template must be a single tuple expression"
        )
        assert "%(" in template, (
            f"{sql_name} template must use named %(col)s placeholders"
        )


def test_latest_per_key_collapses_to_one_row(spark):
    """``_latest_per_key`` is used ONLY for the *_latest sink. It must keep the
    freshest row per PK and drop earlier ones — ``valid_p`` itself stays
    full-fidelity for the history + gold_history sinks."""
    mod = importlib.import_module("pipelines.streaming_quality_sync")
    df = spark.createDataFrame([
        ("shift_1", "F1", "2026-04-29T07:00:00", "running"),
        ("shift_1", "F1", "2026-04-29T07:00:01", "running"),  # newest for F1
        ("shift_1", "F2", "2026-04-29T07:00:00", "stopped"),  # only one for F2
    ], ["shift_id", "machine_id", "ts", "status"])
    collapsed = mod._latest_per_key(df, ["shift_id", "machine_id"], "ts")
    rows = sorted(_to_dicts(collapsed), key=lambda r: r["machine_id"])
    assert len(rows) == 2
    assert rows[0]["machine_id"] == "F1" and rows[0]["ts"] == "2026-04-29T07:00:01"
    assert rows[1]["machine_id"] == "F2" and rows[1]["ts"] == "2026-04-29T07:00:00"
