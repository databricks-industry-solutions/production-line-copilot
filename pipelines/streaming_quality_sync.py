"""Streaming Quality + Side-Outputs — single Structured Streaming job.

Tier-2 analytics consumer. Reads bronze Delta tables (Zerobus-fed), applies
QC predicates, and writes **every valid row per micro-batch** to:

  1. ``<catalog>.<schema>.gold_history.<table>`` — Delta append (governed
     lakehouse: agent SQL, Genie, and rolling-KPI queries).
  2. ``ai_serving.<table>_latest``              — Lakebase Postgres upsert
     (cheap, ≤ 36 rows total; current-state cache for cold-start fallback
     and ad-hoc Genie/notebook queries that just want "current state").
  3. ``<catalog>.<schema>.quality_rejects``     — Delta append for the DQ
     dashboard (rules + reasons).

What this job NO LONGER writes (and why):

  ``ai_serving.<table>_history`` — the live UI's playback cursor target.
  These rows are now written by the producer directly via psycopg2
  (see ``src/streaming_sim/lakebase_sink.py``). Rationale: Zerobus' bronze
  materialization SLA (P50 ≤ 5 s, P95 ≤ 30 s) was the floor for any
  consumer-driven path into Lakebase, surfacing as 5-30 s "Live · waiting
  for new value" gaps in the live ribbon. Producer dual-writes bypass
  that floor — the same row queued to Zerobus is also inserted into
  Lakebase in the same tick (~200 ms). This job continues to own the
  analytics path and the ``_latest`` cache.

Architecture::

    Producer ─► Zerobus ─► <schema>.machine_state_bronze            ┐
                           <schema>.line_kpi_bronze                 ├─► this job
                           <schema>.event_log_bronze                │
                           <schema>.inventory_status_bronze         ┘
                                                                        │
                          ┌─────────────────────────────────────────────┤
                          │                                             │
                          ▼  per row: QC predicates                     │
              ┌──────────────────────────┐                              │
              │ ✓ valid                   │                             │
              │   ├─► gold_history.*  (Delta append, analytics)         │
              │   └─► ai_serving.*_latest (Lakebase UPSERT, ≤36 rows)   │
              │ ✗ rejected                │                             │
              │   └─► quality_rejects (Delta append, w/ rule + reason)  │
              └──────────────────────────┘                              │
                                                                        │
    Producer ─► Lakebase ─► ai_serving.*_history  (live UI hot path) ◄──┘
                            (psycopg2 dual-write, same tick as Zerobus)

Quality rules drop rows that fail the per-table predicates and emit a
``rejected_reason`` column to ``<schema>.quality_rejects`` so the DQ dashboard
can break failures down by reason.

DQX augmentation (opt-in, warning-only): ``pipelines/dqx_augment.py`` provides
an additive layer that runs DQX freshness + non-negative + referential
(``asset.equipment_register``) checks BESIDE the hand-rolled path and appends
flagged rows to the normalized ``<schema>.dqx_warnings`` audit table. It is now
wired into ``_sink`` but gated by ``DQX_ENABLED`` (default OFF), so it deploys
dark and is turned on per-target via the env var. The valid-row path is never
affected — DQX is observability-only and its failures are swallowed.

Idempotency: Delta sinks are checkpointed; ``_latest`` upsert uses
``ON CONFLICT … DO UPDATE``. Spark retries the whole batch on failure —
all sinks are idempotent so duplicates are no-ops.

Compute: classic single-node ``m6gd.2xlarge`` (8 vCPU, 32 GiB). We
tried serverless task envs to dodge cluster cold-start + idle cost,
but serverless rejects ``Trigger.processingTime`` and the
``Trigger.AvailableNow`` workaround paid ~12 s of Spark-Connect
overhead per drain. Classic keeps all 4 streams hot in one JVM
with sub-second processingTime ticks. After dropping the _history
sink the per-batch work is small enough that the previous
``m6gd.8xlarge`` dev bump is no longer needed (analytics path can
tolerate multi-second batch latency).

Required env (CLI args mirror to env, parsed by ``_apply_cli_env``):

  UC_CATALOG, UC_SCHEMA               — Source bronze tables, gold_history,
                                         and quality_rejects sinks
  PGHOST, PGPORT, PGDATABASE, PGUSER  — Lakebase connection
  LAKEBASE_ENDPOINT                   — Lakebase Autoscale OAuth endpoint
  CHECKPOINT_VOLUME                   — UC Volume root for checkpoints
  TRIGGER_PROCESSING_MILLIS           — Optional, default "1000".
                                         processingTime trigger interval.
                                         Requires classic compute;
                                         serverless task envs reject this
                                         trigger type.
  HISTORY_WRITE_PARTITIONS            — Deprecated; ignored. Was used by
                                         the now-removed _history sink.
                                         Kept in the docstring as a
                                         hint that pre-trim deployments
                                         may still set it harmlessly.
  LAKEBASE_TOKEN_REFRESH_SECONDS      — Optional, default "1800" (30 min).
                                         Background-thread interval for
                                         refreshing the Lakebase OAuth
                                         token; tokens last ~1 h so the
                                         default keeps a fresh one always
                                         in hand.
"""
from __future__ import annotations

import argparse
import logging
import os
import pathlib
import sys
import time


def _ensure_repo_on_path() -> None:
    """Make ``agent_server.*`` importable when running on a Databricks Job."""
    try:
        here = pathlib.Path(__file__).resolve()
        candidates = [here.parent] + list(here.parents)
    except NameError:
        candidates = [pathlib.Path.cwd().resolve()] + list(pathlib.Path.cwd().resolve().parents)
    for p in candidates:
        if (p / "agent_server").is_dir() and (p / "src").is_dir():
            if str(p) not in sys.path:
                sys.path.insert(0, str(p))
            return


_ensure_repo_on_path()

from pyspark.sql import DataFrame, SparkSession  # noqa: E402
from pyspark.sql import functions as F  # noqa: E402
from pyspark.sql.window import Window  # noqa: E402

try:
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    sys.stderr.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
except Exception:
    pass

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    stream=sys.stdout,
    force=True,
)


# ─── CLI / env ───────────────────────────────────────────────────────────


def _apply_cli_env() -> None:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--catalog", default=None)
    p.add_argument("--schema", default=None)
    p.add_argument("--pg-host", default=None)
    p.add_argument("--pg-user", default=None)
    p.add_argument("--pg-database", default=None)
    p.add_argument("--pg-port", default=None)
    p.add_argument("--pg-sslmode", default=None)
    p.add_argument("--lakebase-endpoint", default=None)
    p.add_argument("--checkpoint-volume", default=None)
    p.add_argument("--trigger-millis", default=None)
    # Trigger mode: 'processingTime' (classic compute, hot-loop,
    # sub-second cadence) or 'availableNow' (serverless-compatible,
    # one-pass drain per cycle, sleep between cycles). The serverless
    # path is required on workspaces that disallow classic compute
    # (serverless-only) where Trigger.processingTime fails
    # with INFINITE_STREAMING_TRIGGER_NOT_SUPPORTED.
    p.add_argument(
        "--trigger-mode",
        default=None,
        choices=("processingTime", "availableNow"),
    )
    # DQX augmentation toggle + the MVM catalog its referential check resolves.
    # Serverless task envs don't reliably propagate job env vars to the driver,
    # so (like the Lakebase params above) we pass them as CLI flags and promote
    # them to the env vars dqx_augment reads (DQX_ENABLED / MVM_CATALOG).
    p.add_argument("--dqx-enabled", default=None)
    p.add_argument("--mvm-catalog", default=None)
    # Streaming-silver toggle: when on, each QC'd micro-batch is MERGE-upserted
    # into the governed MVM fact tables (asset.failure_record, inventory.stock_
    # balance, production.production_line, quality.inspection_result) at streaming
    # latency — replacing the old 30-min batch refresh job. Off by default so it
    # deploys dark, like DQX.
    p.add_argument("--mvm-silver", default=None)
    ns, _ = p.parse_known_args()
    mapping = {
        "UC_CATALOG": ns.catalog,
        "UC_SCHEMA": ns.schema,
        "PGHOST": ns.pg_host,
        "PGUSER": ns.pg_user,
        "PGDATABASE": ns.pg_database,
        "PGPORT": ns.pg_port,
        "PGSSLMODE": ns.pg_sslmode,
        "LAKEBASE_ENDPOINT": ns.lakebase_endpoint,
        "CHECKPOINT_VOLUME": ns.checkpoint_volume,
        "TRIGGER_PROCESSING_MILLIS": ns.trigger_millis,
        "STREAM_TRIGGER_MODE": ns.trigger_mode,
        "DQX_ENABLED": ns.dqx_enabled,
        "MVM_CATALOG": ns.mvm_catalog,
        "MVM_SILVER_ENABLED": ns.mvm_silver,
    }
    for k, v in mapping.items():
        if v:
            os.environ[k] = v


_apply_cli_env()


CATALOG = os.environ.get("UC_CATALOG", "main")
SCHEMA = os.environ.get("UC_SCHEMA", "line_manager_streaming")
TRIGGER_MILLIS = max(100, int(os.environ.get("TRIGGER_PROCESSING_MILLIS", "1000")))
# why: Spark can thrash below 100 ms with four concurrent streams.
TRIGGER_MODE = os.environ.get("STREAM_TRIGGER_MODE", "processingTime")
# 'processingTime' = classic-compute hot-loop (sub-second cadence).
# 'availableNow'   = serverless-compatible one-pass drain per cycle,
#                     wrapped in a sleep loop in main(); each cycle
#                     pays Spark-Connect query-plan + worker spin-up
#                     overhead (~10-15 s) so cadence is per-minute,
#                     not per-second. Suitable for the analytics
#                     path (gold_history Delta + DQ rejects) since
#                     the live UI reads ai_serving.* (producer
#                     dual-writes those directly).
if TRIGGER_MODE not in ("processingTime", "availableNow"):
    TRIGGER_MODE = "processingTime"
CHECKPOINT_ROOT = os.environ.get(
    "CHECKPOINT_VOLUME",
    f"/Volumes/{CATALOG}/{SCHEMA}/_checkpoints",
).rstrip("/")
# Bumped to v2 because semantics changed (append + dual-write, no collapse).
# A fresh checkpoint dir prevents the new code from inheriting offsets that
# were committed by the v1 collapsing version.
JOB_CHECKPOINT_ROOT = f"{CHECKPOINT_ROOT}/streaming_quality_sync_v2"

REJECTS_TABLE = f"`{CATALOG}`.`{SCHEMA}`.`quality_rejects`"
# DQX audit sink (see pipelines/dqx_augment.py). saveAsTable wants an unquoted
# catalog.schema.table identifier. Written only when DQX_ENABLED=1.
DQX_WARNINGS_TABLE = f"{CATALOG}.{SCHEMA}.dqx_warnings"

# DQX config is resolved HERE, at module import on the client/driver, right
# after ``_apply_cli_env()`` has promoted the ``--dqx-enabled`` / ``--mvm-catalog``
# CLI flags into the process env. These MUST NOT be read from ``os.environ``
# inside the ``foreachBatch`` sink: on serverless (Spark Connect) the batch
# function executes server-side where the CLI-promoted env vars do not exist,
# so a runtime ``os.environ`` read there silently returns the default (DQX off,
# wrong MVM catalog). Capturing them as module constants makes cloudpickle
# serialize the resolved VALUES into the closure — the same reason CATALOG /
# SCHEMA / DQX_WARNINGS_TABLE above work inside the sink.
DQX_ENABLED = os.environ.get("DQX_ENABLED", "0") == "1"
MVM_CATALOG = os.environ.get("MVM_CATALOG") or None
# Streaming silver: MERGE each micro-batch into the governed MVM fact tables.
# Resolved on the driver at import (same reason as DQX_ENABLED above) so the
# value is serialized into the server-side foreachBatch closure. Requires
# MVM_CATALOG to be set (the model must be installed).
MVM_SILVER_ENABLED = os.environ.get("MVM_SILVER_ENABLED", "0") == "1"
DBX_HOST = os.environ.get("DATABRICKS_HOST") or None
DBX_TOKEN = os.environ.get("DATABRICKS_TOKEN") or None
# Capture a working credential on the DRIVER (where default auth resolves) so
# the DQX WorkspaceClient can authenticate inside the server-side foreachBatch
# closure, where default auth is NOT configured. authenticate() returns the
# Authorization header for the resolved auth (PAT or minted OAuth bearer).
try:  # pragma: no cover - only meaningful on a Databricks driver
    from databricks.sdk import WorkspaceClient as _WC
    _dw = _WC()
    DBX_HOST = DBX_HOST or _dw.config.host
    if not DBX_TOKEN:
        _auth = _dw.config.authenticate() or {}
        _bearer = _auth.get("Authorization", "")
        DBX_TOKEN = _bearer[len("Bearer "):] if _bearer.startswith("Bearer ") else None
except Exception:  # noqa: BLE001
    pass


def _bronze(name: str) -> str:
    return f"`{CATALOG}`.`{SCHEMA}`.`{name}`"


def _gold_history(name: str) -> str:
    """Per-table gold history — append-only, fuels Genie SQL and the DQ dashboard."""
    return f"`{CATALOG}`.`{SCHEMA}`.`gold_history_{name}`"


# ─── Quality predicates ──────────────────────────────────────────────────
#
# Each entry maps a bronze table → list of (rule_name, SQL predicate). A row
# passes only if ALL predicates evaluate to TRUE; otherwise the FIRST failing
# rule name is recorded as ``rejected_reason``.
#
# A failing row is dropped from gold and appended to quality_rejects.

QC_RULES: dict[str, list[tuple[str, str]]] = {
    "machine_state": [
        ("valid_machine", "machine_id IS NOT NULL"),
        ("valid_status", "status IN ('running','stopped','degraded','starved','blocked')"),
        ("valid_ts", "ts IS NOT NULL"),
        ("valid_speed", "speed_pct >= 0 AND speed_pct <= 200"),
    ],
    "line_kpi": [
        ("valid_shift", "shift_id IS NOT NULL"),
        ("valid_ts", "ts IS NOT NULL"),
    ],
    "event_log": [
        ("valid_event", "event_id IS NOT NULL"),
        ("valid_machine", "machine_id IS NOT NULL"),
    ],
    "inventory_status": [
        ("valid_shift", "shift_id IS NOT NULL"),
        ("valid_material", "material_id IS NOT NULL"),
        ("non_negative_qty", "on_hand_qty >= 0"),
        ("valid_ts", "ts IS NOT NULL"),
    ],
}


def split_valid_invalid(df: DataFrame, table_name: str) -> tuple[DataFrame, DataFrame]:
    """Split a batch DataFrame into (valid, invalid) using QC_RULES[table_name].

    Invalid rows get a ``rejected_reason`` column (the FIRST failing rule),
    a ``bronze_table`` tag, and a ``rejected_at`` timestamp. Valid rows are
    returned unchanged. Pure transformation — no I/O, easy to unit test.
    """
    rules = QC_RULES.get(table_name, [])
    if not rules:
        # No rules → everything passes; emit empty rejects schema.
        return df, df.limit(0).withColumn("rejected_reason", F.lit("")).withColumn(
            "bronze_table", F.lit(table_name)
        ).withColumn("rejected_at", F.current_timestamp())

    failure_exprs = [
        F.when(~F.expr(predicate), F.lit(rule_name)) for rule_name, predicate in rules
    ]
    annotated = df.withColumn(
        "_first_failure",
        F.coalesce(*failure_exprs) if failure_exprs else F.lit(None).cast("string"),
    )

    valid = annotated.filter(F.col("_first_failure").isNull()).drop("_first_failure")
    invalid = (
        annotated.filter(F.col("_first_failure").isNotNull())
        .withColumnRenamed("_first_failure", "rejected_reason")
        .withColumn("bronze_table", F.lit(table_name))
        .withColumn("rejected_at", F.current_timestamp())
    )
    return valid, invalid


# ─── Lakebase connection helpers ─────────────────────────────────────────
#
# Token-fetch + connection are split because the executor side
# (foreachPartition) cannot make Databricks SDK calls — it only sees the
# pre-minted token broadcast through the closure / env. The driver side
# uses both: ``_mint_lakebase_token`` for the OAuth fetch and then
# ``_connect_with_token`` to open the *_latest upsert connection with the
# same token.


def _execute_values(conn, base_sql: str, template: str, rows: list[dict], page_size: int = 1000) -> int:
    """One-RTT multi-VALUES insert.

    ``base_sql`` is the prefix WITHOUT a VALUES clause:

        INSERT INTO foo (a, b) VALUES %s ON CONFLICT (a) DO NOTHING

    ``template`` is the per-row tuple, accepting either dict or tuple
    inputs:

        ``"(%(a)s, %(b)s)"``  (dicts)
        ``"(%s, %s)"``        (tuples)

    Replaces the previous ``execute_batch`` pattern, which issued one
    INSERT per row (N round-trips per batch). ``execute_values`` issues
    ONE INSERT carrying every row as multi-VALUES tuples — typically
    ~30× fewer parse/plan cycles on the Postgres side for our 50-row
    micro-batches.
    """
    if not rows:
        return 0
    from psycopg2.extras import execute_values
    with conn.cursor() as cur:
        execute_values(cur, base_sql, rows, template=template, page_size=page_size)
    conn.commit()
    return len(rows)


def _connect_with_token(token: str, pg_conf: dict):
    """Build a fresh psycopg2 connection from an explicit ``pg_conf`` dict
    + a pre-minted Lakebase OAuth ``token``.

    Why the dict instead of ``os.environ``: serverless Spark Connect
    runs ``foreachBatch`` and ``foreachPartition`` user functions in
    *separate* Python worker processes. Those workers DO NOT inherit
    the launcher's env vars, so anything the user function needs has
    to be passed explicitly (closure capture or function args). This
    is the same class of issue we already fixed for the executor
    closure — we now apply it driver-side too.
    """
    import psycopg2
    conn = psycopg2.connect(
        host=pg_conf["host"],
        port=pg_conf["port"],
        user=pg_conf["user"],
        password=token,
        dbname=pg_conf["dbname"],
        sslmode=pg_conf["sslmode"],
        connect_timeout=10,
    )
    conn.autocommit = False
    return conn


def _pg_conf_from_env() -> dict:
    """Snapshot the Lakebase connection params from driver env into a dict
    that can be captured by closure and shipped to executors.

    Executor Python workers don't inherit driver env vars (true on
    both classic and serverless), so we must serialise these values
    explicitly. Only host/port/dbname/user/sslmode go here — the OAuth
    token is held separately in a mutable cell on the driver and
    refreshed on a TTL by a background thread, then re-captured into
    each foreachBatch invocation.
    """
    return {
        "host": os.environ.get("PGHOST", "localhost"),
        "port": int(os.environ.get("PGPORT", "5432")),
        "dbname": os.environ.get("PGDATABASE", "databricks_postgres"),
        "user": os.environ.get("PGUSER", ""),
        "sslmode": os.environ.get("PGSSLMODE", "require"),
    }


def _mint_lakebase_token(lakebase_endpoint: str | None = None) -> str:
    """Mint a fresh OAuth token via the Databricks SDK.

    Reuses ``agent_server.memory.pg_pool._get_pg_password``, which
    reads the endpoint from ``LAKEBASE_ENDPOINT``. Because this is
    called inside the foreachBatch user function — which on serverless
    Spark Connect runs in a worker process that does NOT inherit env
    vars — we accept ``lakebase_endpoint`` as an explicit parameter,
    set it on the worker's ``os.environ`` before calling the helper,
    and restore the previous value on exit. The endpoint is captured
    in the foreachBatch closure on the driver side and shipped to the
    worker as part of the pickled function.

    Tokens live ~1 hour; we mint once per micro-batch (cheap local SDK
    call) so streams running for hours never expire.
    """
    saved = os.environ.get("LAKEBASE_ENDPOINT")
    if lakebase_endpoint:
        os.environ["LAKEBASE_ENDPOINT"] = lakebase_endpoint
    try:
        from agent_server.memory.pg_pool import _get_pg_password
        return _get_pg_password()
    finally:
        if lakebase_endpoint:
            if saved is None:
                os.environ.pop("LAKEBASE_ENDPOINT", None)
            else:
                os.environ["LAKEBASE_ENDPOINT"] = saved


# ─── Per-table SQL ───────────────────────────────────────────────────────
#
# Each entry is a (base_sql, template) pair tuned for psycopg2's
# ``execute_values`` API:
#
#   * base_sql ends with ``VALUES %s`` — execute_values fills the %s
#     placeholder with concatenated per-row tuples in ONE query.
#   * template uses dict-style ``%(name)s`` placeholders so foreachBatch
#     can pass ``Row.asDict()`` straight through.
#
# *_history tables are append-only with ``ON CONFLICT … DO NOTHING`` —
# re-running a Spark micro-batch on retry just no-ops duplicates.
# *_latest / *_recent tables UPSERT for cold-start fallback in the App.


def _build_values_sql(
    table_fqn: str, cols: list[str], conflict: str
) -> tuple[str, str]:
    """Produce ``(base_sql, template)`` for ``_execute_values``.

    Example::

        base_sql, template = _build_values_sql(
            "ai_serving.machine_state_history",
            ["shift_id", "machine_id", "ts", "status"],
            "ON CONFLICT (shift_id, machine_id, ts) DO NOTHING",
        )
        # base_sql:
        #   INSERT INTO ai_serving.machine_state_history (shift_id, machine_id, ts, status)
        #   VALUES %s ON CONFLICT (shift_id, machine_id, ts) DO NOTHING
        # template:
        #   (%(shift_id)s, %(machine_id)s, %(ts)s, %(status)s)
    """
    col_list = ", ".join(cols)
    placeholders = ", ".join(f"%({c})s" for c in cols)
    base_sql = f"INSERT INTO {table_fqn} ({col_list}) VALUES %s {conflict}".strip()
    template = f"({placeholders})"
    return base_sql, template


_MS_COLS = ["shift_id", "machine_id", "ts", "status", "throughput_per_min", "speed_pct", "alarm_count"]
_MS_LATEST = _build_values_sql(
    "ai_serving.machine_state_latest", _MS_COLS,
    """ON CONFLICT (shift_id, machine_id) DO UPDATE SET
      ts = EXCLUDED.ts,
      status = EXCLUDED.status,
      throughput_per_min = EXCLUDED.throughput_per_min,
      speed_pct = EXCLUDED.speed_pct,
      alarm_count = EXCLUDED.alarm_count""",
)

_KPI_COLS = [
    "shift_id", "ts", "machines_running", "machines_stopped", "machines_degraded",
    "machines_starved", "total_throughput", "cumulative_cases", "avg_speed_pct",
    "availability_to_now", "performance_to_now", "quality_to_now", "oee_to_now",
]
_KPI_LATEST = _build_values_sql(
    "ai_serving.line_kpi_latest", _KPI_COLS,
    """ON CONFLICT (shift_id) DO UPDATE SET
      ts = EXCLUDED.ts,
      machines_running = EXCLUDED.machines_running,
      machines_stopped = EXCLUDED.machines_stopped,
      machines_degraded = EXCLUDED.machines_degraded,
      machines_starved = EXCLUDED.machines_starved,
      total_throughput = EXCLUDED.total_throughput,
      cumulative_cases = EXCLUDED.cumulative_cases,
      avg_speed_pct = EXCLUDED.avg_speed_pct,
      availability_to_now = EXCLUDED.availability_to_now,
      performance_to_now = EXCLUDED.performance_to_now,
      quality_to_now = EXCLUDED.quality_to_now,
      oee_to_now = EXCLUDED.oee_to_now""",
)

_EVT_COLS = [
    "event_id", "shift_id", "machine_id", "event_type", "severity",
    "timestamp_start", "timestamp_end", "duration_minutes", "root_cause",
    "description", "corrective_action",
]
_EVT_LATEST = _build_values_sql(
    "ai_serving.event_log_recent", _EVT_COLS,
    """ON CONFLICT (event_id) DO UPDATE SET
      shift_id = EXCLUDED.shift_id,
      machine_id = EXCLUDED.machine_id,
      event_type = EXCLUDED.event_type,
      severity = EXCLUDED.severity,
      timestamp_start = EXCLUDED.timestamp_start,
      timestamp_end = EXCLUDED.timestamp_end,
      duration_minutes = EXCLUDED.duration_minutes,
      root_cause = EXCLUDED.root_cause,
      description = EXCLUDED.description,
      corrective_action = EXCLUDED.corrective_action""",
)

_INV_HIST_COLS = [
    "shift_id", "material_id", "ts", "on_hand_qty", "safety_stock",
    "reorder_point", "status", "consumption_rate_per_hour",
]

_INV_LATEST_COLS = [
    "shift_id", "material_id", "on_hand_qty", "safety_stock",
    "reorder_point", "status", "consumption_rate_per_hour",
]
_INV_LATEST = _build_values_sql(
    "ai_serving.inventory_status", _INV_LATEST_COLS,
    """ON CONFLICT (shift_id, material_id) DO UPDATE SET
      on_hand_qty = EXCLUDED.on_hand_qty,
      safety_stock = EXCLUDED.safety_stock,
      reorder_point = EXCLUDED.reorder_point,
      status = EXCLUDED.status,
      consumption_rate_per_hour = EXCLUDED.consumption_rate_per_hour""",
)


# ─── DataFrame helpers ───────────────────────────────────────────────────


def _normalize_ts(df: DataFrame) -> DataFrame:
    """Normalize the event time: prefer ``ts``, fall back to legacy ``timestamp``."""
    cols = set(df.columns)
    if "ts" in cols and "timestamp" in cols:
        return df.withColumn("ts", F.coalesce(F.col("ts"), F.col("timestamp")))
    if "ts" in cols:
        return df
    if "timestamp" in cols:
        return df.withColumnRenamed("timestamp", "ts")
    return df.withColumn("ts", F.lit(None).cast("timestamp"))


_MACHINE_PK = ["shift_id", "machine_id"]
_KPI_PK = ["shift_id"]
_INV_PK = ["shift_id", "material_id"]


def _latest_per_key(df: DataFrame, key_cols: list[str], order_col: str = "ts") -> DataFrame:
    """Collapse to one row per PK, keeping the freshest by ``order_col``.

    Used ONLY for the *_latest side-output sinks; *_history and gold_history
    keep every per-second row.
    """
    w = Window.partitionBy(*key_cols).orderBy(F.col(order_col).desc_nulls_last())
    return df.withColumn("_rn", F.row_number().over(w)).filter("_rn = 1").drop("_rn")


# ─── Sink writers ────────────────────────────────────────────────────────


def _write_rejects(invalid_df: DataFrame, name: str) -> None:
    if invalid_df.isEmpty():
        return
    src_cols = [c for c in invalid_df.columns if c not in ("rejected_reason", "bronze_table", "rejected_at")]
    payload = F.create_map(*[
        x for c in src_cols for x in (F.lit(c), F.col(c).cast("string"))
    ]) if src_cols else F.create_map()
    out = invalid_df.select(
        F.col("bronze_table"),
        F.col("rejected_reason"),
        F.col("rejected_at"),
        payload.alias("payload"),
    )
    (out.write
        .format("delta")
        .mode("append")
        .option("mergeSchema", "true")
        .saveAsTable(f"{CATALOG}.{SCHEMA}.quality_rejects"))


def _write_gold_history(valid_df: DataFrame, name: str, select_cols: list[str]) -> bool:
    """Append the (already QC'd, already projected) ``valid_df`` to the
    per-table gold_history Delta.

    Caller has already verified non-emptiness via ``take(1)`` on the
    cached frame and applied the column projection, so we avoid the
    extra Spark actions that the previous version ran here. Saving 1-2 s
    per stream per batch on classic single-node — material at our 1 Hz
    target trigger interval.

    Returns ``True`` unconditionally so the caller's existing logging
    branches keep their meaning; an empty append is a no-op anyway.
    """
    target = f"{CATALOG}.{SCHEMA}.gold_history_{name}"
    (valid_df.write
        .format("delta")
        .mode("append")
        .option("mergeSchema", "true")
        .saveAsTable(target))
    return True


def _ensure_rejects_table(spark: SparkSession) -> None:
    spark.sql(f"""
        CREATE TABLE IF NOT EXISTS {REJECTS_TABLE} (
            bronze_table   STRING,
            rejected_reason STRING,
            rejected_at    TIMESTAMP,
            payload        MAP<STRING, STRING>
        ) USING DELTA
        TBLPROPERTIES (
            'delta.enableChangeDataFeed' = 'true',
            'delta.autoOptimize.optimizeWrite' = 'true'
        )
    """)


def _ensure_gold_history_tables(spark: SparkSession) -> None:
    """No-op: tables are created from the streaming DataFrame on first write.

    Earlier versions used ``CREATE TABLE … (…)`` with hand-typed columns, but
    bronze type drift (e.g. ``alarm_count BIGINT`` from int64 vs hand-typed
    ``INT``) causes ``DELTA_FAILED_TO_MERGE_FIELDS`` on the first append.
    Letting ``saveAsTable`` infer schemas from the bronze dataframe guarantees
    perfect parity. The trade-off: downstream readers (Genie SQL, the DQ
    dashboard) see ``TABLE_OR_VIEW_NOT_FOUND`` until the first batch lands;
    the gap is < 30 s and self-heals once gold_history is created.
    """
    return


# ─── Streaming-silver MVM merge ──────────────────────────────────────────
#
# Design B: instead of a separate 30-min batch job, each QC'd micro-batch is
# MERGE-upserted into the governed MVM fact tables from inside foreachBatch —
# so the industry model rides the SAME Zerobus→bronze streaming path (seconds
# latency, no third data path). Dimensions (equipment_register, location,
# material_master, schedule) are slow-changing and seeded once at setup; only
# the fact tables stream here.
#
# Gated by MVM_SILVER_ENABLED (deploys dark) and, like DQX, fully non-fatal:
# a merge failure NEVER takes the streaming/analytics path down.

# stream name → silver_mapper mapping key(s) (stream-enabled fact tables).
# quality_checks feeds TWO governed facts from one bronze stream: every check
# becomes an inspection_result, and failing checks additionally become an ncr.
_STREAM_TO_MVM = {
    "event_log": ("failure_record",),
    "inventory_status": ("stock_balance",),
    "line_kpi": ("production_line",),
    "quality_checks": ("inspection_result", "ncr"),
}
# Introspected target columns per mapping key, cached after first (non-empty)
# lookup so we don't hit information_schema every micro-batch.
_MVM_COLS_CACHE: dict[str, set[str]] = {}


def _mvm_merge(source_df: DataFrame, mvm_key: str, batch_id: int) -> None:
    """Upsert one micro-batch into the MVM fact table for ``mvm_key``.

    Non-fatal by contract: any failure (model not installed, column drift,
    transient SQL error) is logged and swallowed so the governed-model write
    never affects the QC/gold/Lakebase path. Idempotent — the MERGE matches on
    a stable surrogate key, so a Spark batch retry re-upserts the same rows.
    """
    if not (MVM_SILVER_ENABLED and MVM_CATALOG):
        return
    try:
        from agent_server.serving.silver_mapper import (
            MAPPINGS, build_merge, introspect_sql,
        )
    except Exception:
        logger.exception("[mvm:%s] silver_mapper import failed (non-fatal)", mvm_key)
        return
    m = next((mm for mm in MAPPINGS if mm.key == mvm_key), None)
    if m is None or not m.is_streaming:
        return

    sess = source_df.sparkSession
    cols = _MVM_COLS_CACHE.get(mvm_key)
    if not cols:
        try:
            rows = sess.sql(introspect_sql(MVM_CATALOG, m.schema, m.table)).collect()
            cols = {str(r[0]) for r in rows}
        except Exception:
            logger.exception("[mvm:%s] introspect failed (target installed?)", mvm_key)
            return
        if not cols:
            # Target not installed yet — don't cache; a later batch (after setup)
            # will re-check and pick it up.
            return
        _MVM_COLS_CACHE[mvm_key] = cols

    view = f"_mvm_src_{mvm_key}_{batch_id}"
    try:
        source_df.createOrReplaceTempView(view)
        stmt = build_merge(m, MVM_CATALOG, view, cols)
        if stmt:
            sess.sql(stmt)
            print(f"[mvm:{mvm_key}] batch={batch_id} merged", flush=True)
    except Exception:
        logger.exception("[mvm:%s] merge failed (non-fatal)", mvm_key)
    finally:
        try:
            sess.catalog.dropTempView(view)
        except Exception:
            pass


def _make_mvm_only_sink(name: str, mvm_keys: tuple[str, ...]):
    """foreachBatch sink that ONLY upserts governed MVM fact table(s).

    Used for bronze streams (e.g. quality_checks) that feed the industry model
    but have no Lakebase ``_latest`` cache or gold_history sink of their own.
    A single stream may feed several MVM tables (quality_checks → inspection_
    result + ncr). Inert unless MVM_SILVER_ENABLED.
    """
    def _sink(batch_df: DataFrame, batch_id: int) -> None:
        for mvm_key in mvm_keys:
            try:
                _mvm_merge(batch_df, mvm_key, batch_id)
            except Exception:
                # _mvm_merge is already fail-safe; this is belt-and-braces so a
                # quality_checks batch can never crash the job.
                logger.exception("[%s] mvm-only sink failed for %s (non-fatal)",
                                 name, mvm_key)
    return _sink


def _make_dual_sink(
    name: str,
    latest: tuple[str, str],
    pk_for_latest: list[str],
    select_cols: list[str],
    pg_conf: dict,
    token_holder: dict,
    optional_cols: tuple[str, ...] = (),
):
    """Return a foreachBatch closure that writes valid rows to up to FOUR sinks.

    Sink layout (and where each runs):

      1. ``gold_history.<name>``         Delta append    — Spark distributed
      2. ``ai_serving.<name>_latest``    Lakebase upsert — driver, collapsed
                                                            to ≤ 36 rows by
                                                            _latest_per_key
      3. ``quality_rejects``             Delta append    — Spark distributed
      4. governed MVM fact(s)            Delta MERGE     — streaming silver,
                                                            _STREAM_TO_MVM[name],
                                                            only when
                                                            MVM_SILVER_ENABLED;
                                                            non-fatal (never
                                                            takes the job down)

    Sink 4 (design B) upserts each micro-batch into the governed industry-model
    fact table(s) so the MVM rides this same streaming path at seconds latency
    instead of a 30-min batch. It runs AFTER the live-cache + gold writes and is
    fully fail-safe, so it can neither delay nor break the analytics/live paths.
    It is inert on the dark-deploy default (MVM_SILVER_ENABLED off).

    Note: ``ai_serving.<name>_history`` (the previous Sink 2) is no longer
    written here. The producer dual-writes those rows directly via
    ``src/streaming_sim/lakebase_sink.py`` so the live UI sees them in
    ~200 ms instead of waiting for Zerobus' 5-30 s bronze materialization
    SLA followed by this consumer's micro-batch.

    ``latest`` is a ``(base_sql, template)`` pair from ``_build_values_sql``.
    ``pg_conf`` (host/port/dbname/user/sslmode) is captured by closure once.

    ``token_holder`` is a mutable ``{"token": str}`` dict shared by all
    sinks and refreshed by a background thread on the driver. On every
    micro-batch we read its current value to open the latest-upsert
    connection. Without _history we no longer need a per-batch fresh
    token captured into a foreachPartition closure — only the driver-side
    upsert uses it now.

    Spark retries the WHOLE batch on any failure → every sink is
    idempotent, so duplicates are no-ops.
    """
    latest_sql, latest_tpl = latest

    def _sink(batch_df: DataFrame, batch_id: int) -> None:
        try:
            valid, invalid = split_valid_invalid(batch_df, name)

            # ── DQX augmentation (optional, observability-only) ────────────
            # Runs BESIDE the QC gate: flags suspect rows into the separate
            # ``dqx_warnings`` audit table without touching the valid/reject
            # decision. Inert unless DQX_ENABLED=1. Never raises — DQX must not
            # take the streaming job down (see apply_dqx_warnings' fail-safe).
            #
            # Everything DQX needs is resolved on the DRIVER at import and passed
            # in by value; NOTHING here reads os.environ or default SDK auth,
            # because this closure executes server-side on serverless where
            # neither is available:
            #   * DQX_ENABLED / MVM_CATALOG — module constants (not is_enabled()).
            #   * DBX_HOST / DBX_TOKEN — driver-resolved workspace creds; the DQX
            #     DQEngine needs an authenticated WorkspaceClient and default auth
            #     is not configured in the closure.
            if DQX_ENABLED:
                try:
                    from pipelines.dqx_augment import apply_dqx_warnings
                    apply_dqx_warnings(
                        batch_df.sparkSession, batch_df, name, DQX_WARNINGS_TABLE,
                        mvm_catalog=MVM_CATALOG,
                        ws_host=DBX_HOST, ws_token=DBX_TOKEN,
                    )
                except Exception:
                    logger.exception("[%s] DQX augmentation failed (non-fatal)", name)

            # Backfill any optional columns the producer omitted (e.g. KPI
            # availability/performance/quality/oee on first emit) so the
            # SELECT below doesn't fail.
            for c in optional_cols:
                if c not in valid.columns:
                    valid = valid.withColumn(c, F.lit(None))

            # ``persist()`` is not supported on serverless / Spark Connect
            # (``NOT_SUPPORTED_WITH_SERVERLESS``). Both consumers below
            # (the _latest collect and the gold_history append) therefore
            # re-evaluate the ``select(*select_cols)`` chain. The cost is
            # a single in-memory filter+projection over the already-
            # materialized ``batch_df`` — sub-second at our trigger sizes.
            valid_p = valid.select(*select_cols)
            rejected_n = 0
            latest_n = 0
            gold_written = False

            token = token_holder["token"]

            # ── Sink 2: ai_serving.*_latest (driver, collapsed) ─
            # Run FIRST so its emptiness doubles as the empty-batch
            # short-circuit for gold_history below. ``_latest_per_key``
            # of an empty frame is empty, and for ``event_log`` (no
            # PK) we set ``latest_df = valid_p`` so the same logic
            # holds: ``latest_rows`` is empty IFF ``valid_p`` is empty.
            if pk_for_latest and "ts" in valid_p.columns:
                latest_df = _latest_per_key(valid_p, pk_for_latest, "ts")
            else:
                latest_df = valid_p
            latest_rows = [r.asDict(recursive=True) for r in latest_df.collect()]

            if latest_rows:
                conn = _connect_with_token(token, pg_conf)
                try:
                    latest_n = _execute_values(conn, latest_sql, latest_tpl, latest_rows)
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass

                # ── Sink 1: gold_history Delta append ──────────────
                try:
                    gold_written = _write_gold_history(valid_p, name, select_cols)
                except Exception:
                    logger.exception("[%s] gold_history append failed", name)
                    print(f"[{name}] batch={batch_id} GOLD-WRITE-FAILED", flush=True)
                    raise  # let Spark retry the batch

                # ── Sink 4: governed MVM fact upsert (streaming silver) ─
                # Non-fatal; runs on the full-column ``valid`` frame (the MVM
                # mapping references raw bronze columns, not the projection).
                # Driven by _STREAM_TO_MVM (single source of truth) so a stream
                # can feed 0..N governed facts. Inert unless MVM_SILVER_ENABLED.
                for _mvm_key in _STREAM_TO_MVM.get(name, ()):
                    _mvm_merge(valid, _mvm_key, batch_id)

            # ── Sink 3: quality_rejects ────────────────────────────────────
            try:
                if invalid.take(1):
                    _write_rejects(invalid, name)
                    try:
                        rejected_n = invalid.count()
                    except Exception:
                        rejected_n = -1
            except Exception:
                logger.exception("[%s] rejects-table write failed", name)
                print(f"[{name}] batch={batch_id} REJECT-WRITE-FAILED", flush=True)

            gold_label = "ok" if gold_written else "skip"
            print(f"[{name}] batch={batch_id} gold={gold_label} latest={latest_n} rejected={rejected_n}", flush=True)
            logger.info("[%s] batch=%s gold=%s latest=%s rejected=%s",
                        name, batch_id, gold_label, latest_n, rejected_n)
        except Exception as e:
            print(f"[{name}] batch={batch_id} UNEXPECTED-FAILURE: {e!r}", flush=True)
            logger.exception("[%s] unexpected sink failure", name)
            raise  # Spark retries the batch

    return _sink


# ─── Stream wiring ───────────────────────────────────────────────────────


def _table_exists(spark: SparkSession, fq: str) -> bool:
    try:
        spark.sql(f"SELECT 1 FROM {fq} LIMIT 0").collect()
        return True
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "TABLE_OR_VIEW_NOT_FOUND" in msg or "does not exist" in msg.lower():
            return False
        raise


def _start_stream(
    spark: SparkSession,
    bronze_table: str,
    sink_fn,
    *,
    name: str,
    trigger_millis: int,
    checkpoint_root: str,
    transform=None,
):
    """Start a long-running ``Trigger.processingTime`` query on classic
    compute. Returns the started ``StreamingQuery`` so ``main()`` can
    keep them all alive via ``awaitAnyTermination``.

    Why classic + processingTime instead of serverless + AvailableNow:
    serverless task envs reject ``Trigger.processingTime`` (the
    INFINITE_STREAMING_TRIGGER_NOT_SUPPORTED error), and the
    AvailableNow-in-a-loop workaround paid ~12 s per drain in
    Spark-Connect query-plan + worker spin-up overhead — we measured
    ~50 s end-to-end across 4 streams, well above the 4-5 s target.
    Classic compute keeps the streams hot in a single JVM, so each
    micro-batch is sub-second once the cluster is warm.
    """
    checkpoint_dir = f"{checkpoint_root}/{name}"
    # NOTE: intentionally NO ``startingVersion=latest``. Under the serverless
    # AvailableNow-in-a-loop model, ``main()`` recreates each reader every cycle.
    # ``startingVersion`` is only honoured when the checkpoint has no committed
    # offset yet, and ``latest`` there means "process only rows that arrive AFTER
    # this query starts". Sparse streams (event_log, quality_checks) are empty at
    # steady state, so a cycle's brief AvailableNow run sees nothing, commits no
    # offset, and the NEXT cycle again resets to "latest" — perpetually straddling
    # and skipping any incident rows written during the inter-cycle sleep. They
    # therefore never commit a first offset and never merge into the governed MVM
    # facts. Omitting the option makes a fresh (uncommitted) reader ingest the
    # full current table snapshot on its first batch, so existing + future rows are
    # caught and the first offset is committed; once a checkpoint offset exists the
    # option would be ignored anyway, so high-volume streams (line_kpi/inventory)
    # that already have committed offsets are unaffected (no history replay).
    # ``ignoreDeletes`` so a delete-only commit on the bronze source (GDPR
    # erasure, retention pruning, OPTIMIZE/VACUUM rewrites, or an operator
    # cleaning out synthetic rows) is skipped instead of failing the whole
    # continuous stream with "Detected deleted data ... not supported". We only
    # ever append to bronze in normal operation, so this changes nothing at
    # steady state; it just keeps the stream alive across incidental deletes.
    reader = (
        spark.readStream.format("delta")
             .option("maxFilesPerTrigger", 50)
             .option("ignoreDeletes", "true")
             .table(bronze_table)
    )
    if transform is not None:
        reader = transform(reader)

    writer = (
        reader.writeStream
              .queryName(f"streaming_quality_sync_{name}")
              .outputMode("update")
              .foreachBatch(sink_fn)
              .option("checkpointLocation", checkpoint_dir)
    )
    if TRIGGER_MODE == "availableNow":
        writer = writer.trigger(availableNow=True)
        trigger_label = "availableNow"
    else:
        writer = writer.trigger(processingTime=f"{trigger_millis} milliseconds")
        trigger_label = f"processingTime={trigger_millis}ms"
    query = writer.start()
    logger.info(
        "[%s] stream started (id=%s checkpoint=%s trigger=%s)",
        name, query.id, checkpoint_dir, trigger_label,
    )
    return query


_TOKEN_REFRESH_INTERVAL_S = max(
    60, int(os.environ.get("LAKEBASE_TOKEN_REFRESH_SECONDS", "1800"))
)
# why: refresh interval must stay below token TTL (~1h) with outage headroom.


def _start_token_refresher(
    token_holder: dict,
    lakebase_endpoint: str,
    interval_s: int = _TOKEN_REFRESH_INTERVAL_S,
):
    """Spawn a daemon thread that refreshes ``token_holder["token"]`` on
    a fixed interval.

    Lakebase OAuth tokens last ~1 h. We refresh every 30 min by default
    so a stale token never leaves the driver, and the foreachBatch
    sinks read the current value from the holder once per micro-batch.

    The refresher swallows transient mint failures and just keeps the
    previous token — a partial outage of the SDK auth path shouldn't
    take down a perfectly-running stream.

    Daemon=True so this thread won't block process exit when
    ``awaitAnyTermination`` returns.
    """
    import threading
    import time as _time

    def _loop() -> None:
        while True:
            _time.sleep(interval_s)
            try:
                new_token = _mint_lakebase_token(lakebase_endpoint)
            except Exception:
                logger.exception(
                    "Background Lakebase token refresh raised — keeping previous"
                )
                continue
            if not new_token:
                logger.warning(
                    "Background Lakebase token refresh returned empty — "
                    "keeping previous token (len=%d)",
                    len(token_holder.get("token", "")),
                )
                continue
            token_holder["token"] = new_token
            logger.info(
                "Lakebase token refreshed by background thread (len=%d, "
                "next in %ds)",
                len(new_token), interval_s,
            )

    t = threading.Thread(
        target=_loop, name="lakebase-token-refresher", daemon=True,
    )
    t.start()
    return t


def main() -> None:
    spark = SparkSession.builder.getOrCreate()
    logger.info(
        "Streaming Quality + Dual-Write starting: catalog=%s schema=%s "
        "trigger=%sms checkpoint_root=%s",
        CATALOG, SCHEMA, TRIGGER_MILLIS, JOB_CHECKPOINT_ROOT,
    )

    _ensure_rejects_table(spark)
    _ensure_gold_history_tables(spark)

    # Snapshot the Lakebase connection params on the driver. We then
    # mint a token and stash it in a mutable holder; a background
    # thread refreshes the holder on a TTL. Every foreachBatch reads
    # the current value from the holder and captures it into the
    # foreachPartition closure for that batch only — so executor
    # Python workers (which don't inherit driver env on classic OR
    # serverless) always ship with a fresh, valid token.
    pg_conf = _pg_conf_from_env()
    lakebase_endpoint = os.environ.get("LAKEBASE_ENDPOINT", "")
    if not lakebase_endpoint:
        logger.warning(
            "LAKEBASE_ENDPOINT is empty on the driver — token minting "
            "will fall back to workspace-token auth, which only works "
            "for human users. Pass --lakebase-endpoint via the bundle."
        )
    logger.info(
        "Lakebase target: host=%s db=%s user=%s endpoint=%s",
        pg_conf["host"], pg_conf["dbname"], pg_conf["user"],
        lakebase_endpoint or "<unset>",
    )

    initial_token = _mint_lakebase_token(lakebase_endpoint)
    if not initial_token:
        raise SystemExit(
            "Lakebase token mint returned empty — Lakebase writes would "
            "fail. Check LAKEBASE_ENDPOINT, run-as identity, and SDK install."
        )
    logger.info(
        "Lakebase token minted at startup (len=%d, refresh every %ds)",
        len(initial_token), _TOKEN_REFRESH_INTERVAL_S,
    )

    # Shared mutable holder. Every sink reads ``token_holder["token"]``
    # on each micro-batch; the background refresher rewrites the
    # ``"token"`` slot in place. Spark's foreachBatch on classic runs
    # in the driver Python process so the dict is shared by reference.
    token_holder: dict[str, str] = {"token": initial_token}
    _start_token_refresher(token_holder, lakebase_endpoint)

    machine_sink = _make_dual_sink(
        "machine_state", _MS_LATEST, _MACHINE_PK,
        select_cols=[*_MACHINE_PK, "ts", "status", "throughput_per_min",
                     "speed_pct", "alarm_count"],
        pg_conf=pg_conf, token_holder=token_holder,
    )
    kpi_sink = _make_dual_sink(
        "line_kpi", _KPI_LATEST, _KPI_PK,
        select_cols=[
            "shift_id", "ts", "machines_running", "machines_stopped",
            "machines_degraded", "machines_starved", "total_throughput",
            "cumulative_cases", "avg_speed_pct", "availability_to_now",
            "performance_to_now", "quality_to_now", "oee_to_now",
        ],
        pg_conf=pg_conf, token_holder=token_holder,
        optional_cols=("cumulative_cases", "availability_to_now",
                       "performance_to_now", "quality_to_now", "oee_to_now"),
    )
    event_sink = _make_dual_sink(
        "event_log", _EVT_LATEST, [],
        select_cols=[
            "event_id", "shift_id", "machine_id", "event_type", "severity",
            "timestamp_start", "timestamp_end", "duration_minutes",
            "root_cause", "description", "corrective_action",
        ],
        pg_conf=pg_conf, token_holder=token_holder,
        optional_cols=("timestamp_end", "duration_minutes",
                       "root_cause", "corrective_action"),
    )
    # ``inventory_status_history`` keeps ts; ``inventory_status`` (latest)
    # drops it. The latest sink runs through ``_latest_per_key(... "ts")``
    # first and writes without ts in the column list, matching the
    # legacy schema.
    inv_sink = _make_dual_sink(
        "inventory_status", _INV_LATEST, _INV_PK,
        select_cols=[*_INV_PK, "ts", "on_hand_qty", "safety_stock",
                     "reorder_point", "status", "consumption_rate_per_hour"],
        pg_conf=pg_conf, token_holder=token_holder,
    )

    candidate_tasks = [
        ("machine_state_bronze", machine_sink, "machine_state", _normalize_ts),
        ("line_kpi_bronze", kpi_sink, "line_kpi", _normalize_ts),
        ("event_log_bronze", event_sink, "event_log", None),
        ("inventory_status_bronze", inv_sink, "inventory_status", _normalize_ts),
    ]

    # quality_checks_bronze feeds ONLY the governed model (quality.inspection_
    # result); it has no Lakebase _latest cache, so it uses a merge-only sink.
    # Added to the stream set only when streaming-silver is on so we don't spin
    # an idle stream when the model isn't wired.
    if MVM_SILVER_ENABLED:
        quality_sink = _make_mvm_only_sink(
            "quality_checks", _STREAM_TO_MVM["quality_checks"]
        )
        candidate_tasks.append(
            ("quality_checks_bronze", quality_sink, "quality_checks", None)
        )

    def _drain_once() -> tuple[list[str], list[str]]:
        """Start every available stream once, await all to terminate.

        On ``processingTime`` (classic) this returns once the streams
        run continuously (awaitAnyTermination blocks until a query dies).
        On ``availableNow`` (serverless) every started query terminates
        when its current backlog is drained; this returns when ALL
        queries have terminated, ready for the outer sleep loop.
        """
        started_local: list[str] = []
        skipped_local: list[str] = []
        for bronze_name, sink, name, transform in candidate_tasks:
            bronze_fq = _bronze(bronze_name)
            if not _table_exists(spark, bronze_fq):
                skipped_local.append(name)
                logger.warning("[%s] bronze table %s not found — skipping",
                               name, bronze_fq)
                continue
            try:
                _start_stream(
                    spark, bronze_fq, sink,
                    name=name,
                    trigger_millis=TRIGGER_MILLIS,
                    checkpoint_root=JOB_CHECKPOINT_ROOT,
                    transform=transform,
                )
                started_local.append(name)
            except Exception:
                logger.exception("[%s] failed to start stream", name)
        return started_local, skipped_local

    if TRIGGER_MODE == "availableNow":
        # Serverless cycle loop. Each pass drains every bronze stream
        # once via Trigger.AvailableNow, then sleeps. Bronze tables that
        # don't exist on early cycles are silently skipped — when the
        # producer creates them, subsequent cycles pick them up.
        cycle_sleep_s = max(15, int(TRIGGER_MILLIS / 1000.0 * 60))
        # Default 60s cadence (TRIGGER_MILLIS=1000 → 60s) — the
        # query-plan + worker spin-up overhead per cycle is ~10-15s
        # on serverless, so anything under 30s is mostly overhead.
        logger.info(
            "Trigger mode = availableNow (serverless). Cycle sleep = %ds. "
            "Live-UI latency is NOT gated by this job; producer dual-writes "
            "ai_serving.* directly to Lakebase.", cycle_sleep_s,
        )
        cycle = 0
        while True:
            cycle += 1
            cycle_started_at = time.time()
            started, skipped = _drain_once()
            if not started:
                logger.warning(
                    "Cycle %d: no bronze tables found (skipped=%s). "
                    "Waiting %ds before retry — producer may not have run yet.",
                    cycle, skipped, cycle_sleep_s,
                )
            else:
                logger.info(
                    "Cycle %d: drained streams=%s skipped=%s",
                    cycle, started, skipped,
                )
                # Wait for ALL streams to drain and terminate (each
                # availableNow query is single-shot).
                for q in spark.streams.active:
                    try:
                        q.awaitTermination()
                    except Exception:
                        logger.exception(
                            "Cycle %d: stream %s terminated with error",
                            cycle, q.name,
                        )
            elapsed = time.time() - cycle_started_at
            sleep_for = max(1.0, cycle_sleep_s - elapsed)
            logger.info(
                "Cycle %d done in %.1fs; sleeping %.1fs before next pass",
                cycle, elapsed, sleep_for,
            )
            sys.stdout.flush()
            time.sleep(sleep_for)
    else:
        started, skipped = _drain_once()
        if not started:
            raise SystemExit(
                f"No streams started. Candidates skipped={skipped}. Check that "
                f"the producer has run at least once and bronze tables exist "
                f"in {CATALOG}.{SCHEMA}."
            )

        logger.info(
            "All streams launched (started=%s skipped=%s) — entering "
            "awaitAnyTermination", started, skipped,
        )
        sys.stdout.flush()

        spark.streams.awaitAnyTermination()


if __name__ == "__main__":
    main()
