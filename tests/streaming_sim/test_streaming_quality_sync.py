"""Unit tests for ``pipelines.streaming_quality_sync.split_valid_invalid``.

These tests exercise the streaming QC contract: each bronze table's SQL
predicates decide which rows pass to gold and which are dropped into
``quality_rejects`` with a ``rejected_reason``.
"""

from __future__ import annotations

import importlib
import inspect

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


def test_history_sink_stays_out_of_this_job():
    """The producer owns the ``*_history`` write (lakebase_sink.py, ~200 ms).

    This job used to build that SQL and then discard it, which made the sink
    look covered when nothing executed it. The append-only ON CONFLICT
    semantics are genuinely tested against the producer in
    tests/streaming_sim/test_lakebase_sink.py.
    """
    mod = importlib.import_module("pipelines.streaming_quality_sync")
    for sql_name in ("_MS_HISTORY", "_KPI_HISTORY", "_EVT_HISTORY", "_INV_HISTORY"):
        assert not hasattr(mod, sql_name), f"{sql_name} is dead — the producer owns it"
    assert "history" not in inspect.signature(mod._make_dual_sink).parameters


def test_latest_sql_uses_do_update_not_nothing():
    """*_latest tables are upserts (cold-start fallback for the App). The PK
    is the partitioning key only (NO ts), so DO UPDATE is what makes them
    "latest"."""
    mod = importlib.import_module("pipelines.streaming_quality_sync")
    for sql_name in ("_MS_LATEST", "_KPI_LATEST", "_EVT_LATEST", "_INV_LATEST"):
        base_sql, _template = getattr(mod, sql_name)
        assert "ON CONFLICT" in base_sql, f"{sql_name} must declare ON CONFLICT"
        assert "DO UPDATE" in base_sql, f"{sql_name} must use DO UPDATE (upsert semantics)"


def test_latest_sql_uses_execute_values_template():
    """The latest SQL must end with ``VALUES %s`` so psycopg2's
    ``execute_values`` can splice multi-row tuples into one INSERT — this
    is the latency-critical path. The template (second tuple element) must
    use named ``%(...)s`` placeholders matching the SELECT columns so
    ``Row.asDict()`` works without extra mapping.
    """
    mod = importlib.import_module("pipelines.streaming_quality_sync")
    for sql_name in ("_MS_LATEST", "_KPI_LATEST", "_EVT_LATEST", "_INV_LATEST"):
        base_sql, template = getattr(mod, sql_name)
        assert "VALUES %s" in base_sql, f"{sql_name} must end its INSERT with 'VALUES %s'"
        assert template.startswith("(") and template.endswith(")"), (
            f"{sql_name} template must be a single tuple expression"
        )
        assert "%(" in template, (
            f"{sql_name} template must use named %(col)s placeholders"
        )


# ── streaming-silver MVM merge (design B) ─────────────────────────────────
#
# These exercise the governed-model upsert wiring WITHOUT Spark/Delta by
# injecting a fake session + dataframe that record the SQL executed and the
# temp-view lifecycle. They pin the three guarantees that matter for a change
# to the critical streaming job: (1) it does ZERO work when disabled (no added
# latency on the dark-deploy default), (2) it is idempotent + non-fatal, and
# (3) a stream can feed 0..N governed facts via the single-source-of-truth map.


class _FakeCatalog:
    def __init__(self):
        self.dropped: list[str] = []

    def dropTempView(self, name):  # noqa: N802 (Spark API name)
        self.dropped.append(name)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def collect(self):
        return self._rows


class _FakeSession:
    """Records executed SQL; returns canned columns for introspection."""

    def __init__(self, target_cols, fail_on_merge=False):
        self._cols = target_cols
        self.fail_on_merge = fail_on_merge
        self.executed: list[str] = []
        self.catalog = _FakeCatalog()

    def sql(self, q):
        self.executed.append(q)
        if "information_schema.columns" in q:
            return _FakeResult([[c] for c in self._cols])
        if q.startswith("MERGE") and self.fail_on_merge:
            raise RuntimeError("boom")
        return _FakeResult([])

    @property
    def merges(self):
        return [q for q in self.executed if q.startswith("MERGE")]

    @property
    def introspects(self):
        return [q for q in self.executed if "information_schema.columns" in q]


class _FakeDF:
    def __init__(self, sess):
        self.sparkSession = sess
        self.views: list[str] = []

    def createOrReplaceTempView(self, name):  # noqa: N802
        self.views.append(name)


@pytest.fixture()
def sqs(monkeypatch):
    """streaming_quality_sync module with a clean MVM cache + MVM_CATALOG set."""
    mod = importlib.import_module("pipelines.streaming_quality_sync")
    mod._MVM_COLS_CACHE.clear()
    monkeypatch.setattr(mod, "MVM_CATALOG", "cat", raising=False)
    yield mod
    mod._MVM_COLS_CACHE.clear()


# failure_record's installed columns (superset is fine; build_merge filters).
_FR_COLS = {
    "failure_record_id", "equipment_register_id", "production_line_id",
    "failure_number", "failure_datetime", "failure_mode_description",
    "record_status", "created_timestamp",
}


def test_mvm_merge_noop_when_disabled(sqs, monkeypatch):
    """Latency guard: with MVM_SILVER_ENABLED off, the merge does NOTHING —
    no introspect, no MERGE, no temp view. This is the dark-deploy default, so
    the critical streaming job pays zero extra cost until explicitly opted in."""
    monkeypatch.setattr(sqs, "MVM_SILVER_ENABLED", False, raising=False)
    sess = _FakeSession(_FR_COLS)
    df = _FakeDF(sess)
    sqs._mvm_merge(df, "failure_record", batch_id=1)
    assert sess.executed == []
    assert df.views == []


def test_mvm_merge_executes_and_cleans_up_when_enabled(sqs, monkeypatch):
    monkeypatch.setattr(sqs, "MVM_SILVER_ENABLED", True, raising=False)
    sess = _FakeSession(_FR_COLS)
    df = _FakeDF(sess)
    sqs._mvm_merge(df, "failure_record", batch_id=7)
    assert len(sess.merges) == 1
    assert sess.merges[0].startswith("MERGE INTO cat.asset.failure_record t")
    # temp view created then dropped (no leak)
    assert df.views == ["_mvm_src_failure_record_7"]
    assert sess.catalog.dropped == ["_mvm_src_failure_record_7"]


def test_mvm_merge_caches_introspection(sqs, monkeypatch):
    """Column introspection hits information_schema at most once per key — a
    per-batch query would add latency to every micro-batch."""
    monkeypatch.setattr(sqs, "MVM_SILVER_ENABLED", True, raising=False)
    sess = _FakeSession(_FR_COLS)
    for b in (1, 2, 3):
        sqs._mvm_merge(_FakeDF(sess), "failure_record", batch_id=b)
    assert len(sess.introspects) == 1
    assert len(sess.merges) == 3


def test_mvm_merge_skips_when_target_not_installed(sqs, monkeypatch):
    """Empty introspection (model not installed) -> no MERGE, nothing cached
    (so a later batch after setup can pick it up)."""
    monkeypatch.setattr(sqs, "MVM_SILVER_ENABLED", True, raising=False)
    sess = _FakeSession(set())  # target has no columns
    sqs._mvm_merge(_FakeDF(sess), "failure_record", batch_id=1)
    assert sess.merges == []
    assert "failure_record" not in sqs._MVM_COLS_CACHE


def test_mvm_merge_skips_when_merge_key_absent(sqs, monkeypatch):
    """If the surrogate key column is missing there's nothing to match on;
    build_merge returns None and no MERGE runs (view still cleaned up)."""
    monkeypatch.setattr(sqs, "MVM_SILVER_ENABLED", True, raising=False)
    sess = _FakeSession({"failure_number", "record_status"})  # no *_id key
    df = _FakeDF(sess)
    sqs._mvm_merge(df, "failure_record", batch_id=1)
    assert sess.merges == []
    assert sess.catalog.dropped == ["_mvm_src_failure_record_1"]


def test_mvm_merge_is_non_fatal_and_drops_view_on_error(sqs, monkeypatch):
    """A MERGE failure must NEVER propagate (it would crash the streaming job)
    and must still drop the temp view."""
    monkeypatch.setattr(sqs, "MVM_SILVER_ENABLED", True, raising=False)
    sess = _FakeSession(_FR_COLS, fail_on_merge=True)
    df = _FakeDF(sess)
    sqs._mvm_merge(df, "failure_record", batch_id=9)  # must not raise
    assert sess.catalog.dropped == ["_mvm_src_failure_record_9"]


def test_mvm_only_sink_loops_all_keys_and_is_non_fatal(sqs, monkeypatch):
    """quality_checks feeds TWO governed facts; a failure on one must not stop
    the other, and nothing propagates."""
    monkeypatch.setattr(sqs, "MVM_SILVER_ENABLED", True, raising=False)
    calls = []

    def fake_merge(df, k, b):
        calls.append(k)
        if k == "ncr":
            raise RuntimeError("boom")
    monkeypatch.setattr(sqs, "_mvm_merge", fake_merge)
    sink = sqs._make_mvm_only_sink("quality_checks", ("inspection_result", "ncr"))
    sink(_FakeDF(_FakeSession(set())), 1)  # must not raise
    assert calls == ["inspection_result", "ncr"]


def test_stream_to_mvm_keys_are_stream_enabled_mappings():
    """Every governed fact wired into the job must be a stream-enabled mapping
    in silver_mapper — guards against a rename/drift silently disabling a
    stream's MVM upsert."""
    mod = importlib.import_module("pipelines.streaming_quality_sync")
    from agent_server.serving import silver_mapper as sm
    streamable = {m.key for m in sm.MAPPINGS if m.is_streaming}
    for stream_name, keys in mod._STREAM_TO_MVM.items():
        for k in keys:
            assert k in streamable, f"{stream_name}->{k} is not a stream-enabled mapping"
    # quality_checks fans out to both quality facts
    assert set(mod._STREAM_TO_MVM["quality_checks"]) == {"inspection_result", "ncr"}


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
