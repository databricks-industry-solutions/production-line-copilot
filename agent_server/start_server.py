"""FastAPI app exposing the Line Manager agent backend.

Top-level structure (in load order):

1. KPI helpers (``_to_float``, ``_kpi_is_empty``, ``_derive_kpi_from_machine_rows``,
   ``_machine_rows_show_activity``, ``_iso_from_payload``) — small pure helpers
   used by the SSE generator and ``/sim/status`` to turn Lakebase rows into
   the dashboard's KPI shape.
2. Pydantic request models — ``InvocationBody``, ``IncidentTriggerBody``,
   ``IncidentResolveBody``, ``RecoverMachineBody``, ``OpsControlActionBody``.
3. ``app = FastAPI(...)`` + CORS middleware + ``@app.on_event('startup')``.
4. Health endpoints — ``/health``, ``/health/deep``, ``/debug/sql``.
5. Cost/usage helpers + in-app live counters (``_LIVE_METRICS`` deque) +
   ``/ops/cost-summary`` and ``/ops/cost-live``.
6. Ops control plane — admin actions on jobs/pipelines (``_ops_*`` helpers,
   ``/ops/dashboard-info``, ``/ops/control/state``, ``/ops/control/action``).
7. Agent endpoints — ``/invocations``, ``/invocations/stream``.
8. Sim control endpoints — ``/sim/status``, ``/sim/restart``, ``/sim/stop``,
   ``/sim/resume``, ``/sim/incident``, ``/sim/resolve``, ``/sim/recover_machine``,
   ``/sim/commands``.
9. SSE — ``/serving/stream`` (the live data feed).
10. ``main()`` for local uvicorn run; production is launched by ``scripts/start-app.sh``.

Why this lives in one big file: Databricks Apps prefers a single FastAPI app
module wired by ``app = FastAPI(...)`` and run with uvicorn. Splitting into
multiple routers adds bundler complexity for no functional gain at this size.
The natural seam is by section number above; if this grows past ~3500 lines,
factor sections 5–6 (cost + ops) into ``agent_server/ops/`` first since they
share no state with the live SSE path.
"""

import argparse
import asyncio
import logging
import math
import os
import signal
import sys
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, List, Optional, Union

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from agent_server.serving import reconciliation

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

_DEBUG_ENABLED = os.environ.get("AGENT_DEBUG", "false").lower() in ("1", "true", "yes")
_BUILD_MARKER = "sse-ai-serving-only-2026-04-24-v3"


def _to_float(v: object, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        n = float(v)
        return n if math.isfinite(n) else default
    except Exception:
        return default


def _kpi_is_empty(row: dict[str, object] | None) -> bool:
    if not row:
        return True
    keys = ("oee_to_now", "availability_to_now", "performance_to_now", "quality_to_now")
    return all(_to_float(row.get(k)) <= 0.0 for k in keys)


def _derive_kpi_from_machine_rows(shift_id: str, machines: list[dict[str, object]]) -> dict[str, object] | None:
    """Derive Tier-1 KPI from Lakebase machine snapshot when KPI row is missing/zero."""
    if not machines:
        return None

    from src.streaming_sim.line_kpi_derived import compute_tier1_line_kpi_row

    running = stopped = degraded = starved = 0
    total_tput = 0.0
    speed_sum = 0.0
    speed_count = 0
    latest_ts = None

    for m in machines:
        status = str(m.get("status") or "running").strip().lower()
        if status == "running":
            running += 1
        elif status == "stopped":
            stopped += 1
        elif status == "degraded":
            degraded += 1
        elif status in ("starved", "blocked"):
            starved += 1

        total_tput += _to_float(m.get("throughput_per_min"))

        mid = str(m.get("machine_id") or "")
        # Keep parity with sim-side KPI: conveyor speeds are excluded.
        if not mid.startswith("CONVEYOR"):
            speed_sum += _to_float(m.get("speed_pct"))
            speed_count += 1

        ts = m.get("ts")
        if ts is not None:
            ts_s = str(ts)
            if latest_ts is None or ts_s > latest_ts:
                latest_ts = ts_s

    avg_speed = round(speed_sum / max(speed_count, 1), 1)
    return compute_tier1_line_kpi_row(
        shift_id=shift_id,
        ts=latest_ts or "",
        machines_running=running,
        machines_stopped=stopped,
        machines_degraded=degraded,
        machines_starved=starved,
        total_throughput=round(total_tput, 2),
        avg_speed_pct=avg_speed,
        cumulative_cases=0.0,
    )


def _machine_rows_show_activity(machines: list[dict[str, object]]) -> bool:
    """True when machine rows suggest production activity (so OEE should not stick at 0)."""
    for m in machines:
        status = str(m.get("status") or "").strip().lower()
        if status in ("running", "degraded", "starved", "blocked"):
            return True
        if _to_float(m.get("throughput_per_min")) > 0.0:
            return True
        if _to_float(m.get("speed_pct")) > 0.0:
            return True
    return False


def _iso_from_payload(
    machines: list[dict[str, object]],
    kpi_row: dict[str, object] | None,
    events: list[dict[str, object]],
) -> str | None:
    """Best-effort latest source timestamp from Lakebase payload."""
    candidates: list[str] = []
    for m in machines:
        ts = m.get("ts")
        if ts:
            candidates.append(str(ts))
    if kpi_row and kpi_row.get("ts"):
        candidates.append(str(kpi_row.get("ts")))
    for e in events:
        ts = e.get("timestamp_start")
        if ts:
            candidates.append(str(ts))
    return max(candidates) if candidates else None


class InvocationBody(BaseModel):
    query: str = Field(default="", max_length=2000)
    shift_id: str = Field(default="shift_1", max_length=50)
    timestamp: str = Field(default="2025-01-01T14:00:00", max_length=30)
    machine_id: Optional[str] = Field(default=None, max_length=50)
    conversation_id: Optional[str] = Field(default=None, max_length=100)
    input: Optional[Union[List, str]] = None


class IncidentTriggerBody(BaseModel):
    shift_id: str = Field(default="shift_1", max_length=50)
    scenario_id: str = Field(max_length=50)
    target_machine_id: Optional[str] = Field(default=None, max_length=50)
    severity: str = Field(default="medium", max_length=20)


class IncidentResolveBody(BaseModel):
    command_id: str = Field(max_length=100)
    resolved_by: str = Field(default="user", max_length=100)
    resolution_notes: Optional[str] = Field(default=None, max_length=500)


class RecoverMachineBody(BaseModel):
    """Resolve any active incident on a machine — does not require a command_id.

    Lets the UI clear faults that surface in the event feed but have no
    matching ``scenario_commands`` row visible to the operator (organic
    cascade ripples, shift_id mismatches, etc.).
    """
    machine_id: str = Field(max_length=50)
    resolved_by: str = Field(default="user", max_length=100)


class OpsControlActionBody(BaseModel):
    target: str = Field(max_length=50)  # producer | lakebase_syncer | setup_job
    action: str = Field(max_length=50)  # pause | resume | restart | start | stop | run_now
    confirm: bool = Field(default=False)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Smoke-test SQL connectivity and log the Lakebase serving module path.

    There is no in-process simulation engine to start — live state flows from
    the external producer via Lakebase. Failures here are logged but do not
    abort startup — the App should be reachable for ``/health`` and
    ``/debug/sql`` so an operator can diagnose even when the warehouse is
    unhealthy.
    """
    logger.info(
        "BUILD_MARKER=%s START_SERVER_FILE=%s",
        _BUILD_MARKER,
        __file__,
    )
    logger.info(
        "ENV: DATABRICKS_HOST=%s WAREHOUSE_ID=%s CATALOG=%s SCHEMA=%s TOKEN_SET=%s",
        os.environ.get("DATABRICKS_HOST", "")[:50],
        os.environ.get("DATABRICKS_WAREHOUSE_ID", "")[:20],
        os.environ.get("UC_CATALOG", ""),
        os.environ.get("UC_SCHEMA", ""),
        bool(os.environ.get("DATABRICKS_TOKEN")),
    )

    # No in-process simulation engine to start: the live data plane is
    # external. The producer job writes to Lakebase ``ai_serving.*`` and both
    # the SSE stream and the agent context read from there (see
    # ``/serving/stream`` and ``agent._overlay_live_state``).
    try:
        import agent_server.serving.realtime_store as _rt

        logger.info("Tier-1 Lakebase SSE reads: ai_serving.* only (realtime_store=%s marker=%s)", _rt.__file__, _BUILD_MARKER)
    except Exception:
        logger.exception("Could not log realtime_store module path")

    try:
        from agent_server.tools.sql_query import sql_query as _sql
        result = _sql.invoke("SELECT COUNT(*) AS cnt FROM scenario_index")
        if result and isinstance(result[0], dict):
            if "error" in result[0]:
                logger.error("STARTUP SQL FAILED: %s", result[0]["error"][:300])
            else:
                logger.info("STARTUP SQL OK: scenario_index has %s rows", result[0].get("cnt"))
        else:
            logger.warning("STARTUP SQL unexpected result: %s", str(result)[:200])
    except Exception:
        logger.exception("STARTUP SQL EXCEPTION")

    yield


app = FastAPI(title="Line Manager Agent Server", lifespan=lifespan)

# CORS policy. The default deployment puts this FastAPI agent behind the
# Express AppKit proxy in the same Databricks App, so all browser traffic is
# same-origin and CORS does not apply. We therefore default to an empty
# allow-list (cross-origin requests get blocked at the browser).
#
# To open this up (e.g. local dev where the React app runs on a different
# origin than the FastAPI server), set ``AGENT_CORS_ALLOW_ORIGINS`` to a
# comma-separated list of origins, or ``*`` for any origin. Wildcards are
# explicit so the broadest possible policy never ships by accident.
_cors_raw = (os.environ.get("AGENT_CORS_ALLOW_ORIGINS") or "").strip()
if _cors_raw == "*":
    _cors_origins: list[str] = ["*"]
else:
    _cors_origins = [o.strip() for o in _cors_raw.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/health/deep")
async def deep_health():
    """Deep health check for canary validation — tests SQL connectivity."""
    checks: dict[str, str] = {"api": "ok"}
    try:
        from agent_server.tools.sql_query import sql_query as _sql_tool
        result = _sql_tool.invoke("SELECT 1 AS probe")
        if result and isinstance(result[0], dict) and result[0].get("probe") == 1:
            checks["sql"] = "ok"
        else:
            checks["sql"] = "degraded"
    except Exception as e:
        checks["sql"] = f"error: {str(e)[:80]}"

    all_ok = all(v == "ok" for v in checks.values())
    status_code = 200 if all_ok else 503
    return JSONResponse({"status": "healthy" if all_ok else "degraded", "checks": checks}, status_code=status_code)


@app.get("/debug/sql")
async def debug_sql():
    """Test SQL connectivity and return table row counts (only in debug mode)."""
    if not _DEBUG_ENABLED:
        return JSONResponse({"error": "Debug endpoint disabled. Set AGENT_DEBUG=true to enable."}, status_code=403)

    from agent_server.tools.sql_query import sql_query as _sql_tool

    env_info = {
        "DATABRICKS_HOST": os.environ.get("DATABRICKS_HOST", "")[:50],
        "DATABRICKS_WAREHOUSE_ID": os.environ.get("DATABRICKS_WAREHOUSE_ID", "")[:20],
        "UC_CATALOG": os.environ.get("UC_CATALOG", ""),
        "UC_SCHEMA": os.environ.get("UC_SCHEMA", ""),
        "DATABRICKS_TOKEN_set": bool(os.environ.get("DATABRICKS_TOKEN")),
    }

    tables = [
        "scenario_index", "event_log", "machine_state",
        "production_schedule", "line_kpi", "quality_checks",
        "inventory_status", "shift_crew", "maintenance_schedule",
    ]
    counts: dict[str, object] = {}
    for t in tables:
        try:
            result = _sql_tool.invoke(f"SELECT COUNT(*) AS cnt FROM {t}")
            if result and isinstance(result[0], dict) and "cnt" in result[0]:
                counts[t] = result[0]["cnt"]
            elif result and isinstance(result[0], dict) and "error" in result[0]:
                counts[t] = f"ERROR: {result[0]['error'][:100]}"
            else:
                counts[t] = f"unexpected: {str(result)[:100]}"
        except Exception as e:
            counts[t] = f"EXCEPTION: {str(e)[:100]}"

    return JSONResponse({"env": env_info, "table_counts": counts})


def _safe_num(v: Any) -> float:
    """Tolerant numeric coercion — used by cost rollups where columns may be NULL."""
    return _to_float(v, default=0.0)


def _truthy(v: str | None) -> bool:
    """Standard env-var truthy check (matches the rest of the codebase)."""
    return (v or "").strip().lower() in {"1", "true", "yes", "on"}


def _unit_to_bytes(unit: str, qty: float) -> float:
    """Normalise system.billing.usage_unit + usage_quantity to bytes (BYTES/KB/MB/GB/TB)."""
    u = (unit or "").strip().upper()
    if qty <= 0:
        return 0.0
    if u in {"BYTE", "BYTES"}:
        return qty
    if u in {"KB", "KIB"}:
        return qty * 1024
    if u in {"MB", "MIB"}:
        return qty * 1024 * 1024
    if u in {"GB", "GIB"}:
        return qty * 1024 * 1024 * 1024
    if u in {"TB", "TIB"}:
        return qty * 1024 * 1024 * 1024 * 1024
    return 0.0


# Ordered (category, keywords) rules for bucketing a system.billing.usage row.
# First rule whose keyword appears in "<billing_origin_product> <sku_name>" wins.
#
# Both the SQL `CASE` used by the components rollup and the Python classifier
# used by the verification panel are generated from this one table. They used
# to be written out by hand in two places and had drifted: the SQL checked only
# some field/keyword pairs (`sku_name` for MODEL but not `billing_origin_product`,
# `billing_origin_product` for SQL but not `sku_name`), so a
# FOUNDATION_MODEL_TRAINING row was priced as "Other" at $0.55/DBU in the
# components table and as "Model Serving" at $0.07/DBU in the verification
# figure directly beneath it — a 7.9x disagreement between two numbers on the
# same screen. Generating both from one list makes that class of drift
# impossible rather than merely fixed.
_USAGE_CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Zerobus", ("ZEROBUS",)),
    ("Lakebase", ("LAKEBASE",)),
    ("Pipelines", ("LAKEFLOW", "PIPELINE", "DLT")),
    ("Model Serving", ("SERVING", "FOUNDATION", "LLM", "MODEL")),
    ("SQL Warehouse", ("SQL", "WAREHOUSE")),
    ("Jobs", ("JOBS",)),
    ("Apps", ("APP",)),
)
_UNCATEGORISED = "Other"

# The same "<origin> <sku>" haystack the Python classifier builds, in SQL.
_USAGE_TEXT_SQL = (
    "upper(concat_ws(' ', coalesce(billing_origin_product, ''), coalesce(sku_name, '')))"
)


def _usage_category(origin: str, sku: str) -> str:
    """Bucket a system.billing.usage row into one of the dashboard cost categories.

    Matches against (origin, sku) text — Databricks SKU naming has shifted
    several times so this checks for the resilient substrings rather than
    exact SKU codes.
    """
    text = f"{origin} {sku}".upper()
    for category, keywords in _USAGE_CATEGORY_RULES:
        if any(keyword in text for keyword in keywords):
            return category
    return _UNCATEGORISED


def _usage_category_sql() -> str:
    """The `CASE` expression equivalent of :func:`_usage_category`.

    Kept generated rather than hand-written so the two classifications cannot
    disagree; ``test_usage_category_sql_matches_python`` pins them together.
    """
    branches = "".join(
        " WHEN {} THEN '{}'".format(
            " OR ".join(f"{_USAGE_TEXT_SQL} LIKE '%{kw}%'" for kw in keywords),
            category,
        )
        for category, keywords in _USAGE_CATEGORY_RULES
    )
    return f"CASE{branches} ELSE '{_UNCATEGORISED}' END"


_DBU_USD_RATES = {
    "Jobs": 0.55,
    "SQL Warehouse": 0.70,
    "Pipelines": 0.36,
    "Model Serving": 0.07,
    "Lakebase": 0.55,
    "Zerobus": 0.55,
    "Apps": 0.55,
    "Other": 0.55,
}


def _estimate_usd(component: str, dbu: float) -> float:
    """Convert DBU to USD using component-specific rate cards from ``_DBU_USD_RATES``.

    Rates are demo-only ballpark numbers ($/DBU) — the real billing dashboard
    in Databricks uses contracted rates per workspace. Used for the cost
    estimate strip in the App; do not surface to customers as authoritative.
    """
    rate = _DBU_USD_RATES.get(component, _DBU_USD_RATES["Other"])
    return round(max(0.0, dbu) * rate, 4)


# ── In-app live cost counters ─────────────────────────────────────────────
# Used for short windows where system.billing.usage hasn't materialized yet
# (system.billing typically lags by ~1-4 hours; for live ops dashboards we
# keep an in-process counter as a stopgap and reconcile against system.billing
# on a 24h rollup window).
_LIVE_LOCK = threading.Lock()
_LIVE_STARTED_AT = time.time()
_LIVE_METRICS: dict[str, float] = {
    "sse_ticks": 0.0,
    "sse_payload_bytes": 0.0,
    "sse_payload_rows": 0.0,
    "lakebase_reads": 0.0,
    "agent_invocations": 0.0,
    "llm_input_tokens": 0.0,
    "llm_output_tokens": 0.0,
    "llm_calls": 0.0,
    "events_published": 0.0,
    # Tool-level counters (Vector Search and Genie are wired into specialist agents).
    "vector_search_calls": 0.0,
    "genie_queries": 0.0,
}
_LIVE_EVENTS: "deque[tuple[float, str, float]]" = deque(maxlen=20000)


def _estimate_tokens(text: object) -> int:
    """Rough token estimate using the 4-chars-per-token rule of thumb."""
    if not text:
        return 0
    try:
        return max(1, int(len(str(text)) / 4))
    except Exception:
        return 0


def _live_record(metric: str, value: float = 1.0) -> None:
    """Record an in-app counter sample. Cheap; safe to call from hot paths."""
    try:
        ts = time.time()
        with _LIVE_LOCK:
            _LIVE_METRICS[metric] = _LIVE_METRICS.get(metric, 0.0) + value
            _LIVE_EVENTS.append((ts, metric, value))
    except Exception:
        pass


def _live_aggregate(minutes: int) -> dict[str, float]:
    """Aggregate counters over the last N minutes."""
    cutoff = time.time() - max(1, minutes) * 60
    sums: dict[str, float] = {k: 0.0 for k in _LIVE_METRICS}
    with _LIVE_LOCK:
        for ts, metric, value in _LIVE_EVENTS:
            if ts >= cutoff:
                sums[metric] = sums.get(metric, 0.0) + value
    return sums


# Approximate in-app cost rates. Documented as estimates — refined over time
# from system.billing.usage averages on small serverless tiers (us-east-1/eu-central-1).
# Real cost depends on cloud, tier, region, discounts.
_LIVE_RATES = {
    # Per-token (Claude / GPT class via Databricks Model Serving)
    "llm_input_usd_per_1k_tokens": 0.003,
    "llm_output_usd_per_1k_tokens": 0.015,
    "llm_dbu_per_1k_tokens": 0.05,
    # Per-hour (amortised over the window when the resource is RUNNING)
    "lakebase_compute_usd_per_hour": 0.50,    # Lakebase Autoscale @ ~1 CU
    "app_compute_usd_per_hour": 0.30,         # Databricks Apps SP runtime
    "sql_warehouse_usd_per_hour": 0.70,       # Serverless SQL Warehouse, Small
    "producer_job_usd_per_hour": 0.55,        # Serverless Job, small worker
    # Continuous classic single-node Spark Streaming job (m6gd.xlarge): reads
    # bronze, applies QC, dual-writes to gold_history Delta + ai_serving Postgres.
    "lakebase_syncer_usd_per_hour": 0.55,
    "synced_table_usd_per_hour": 0.30,        # Lakeflow sync pipeline per active table
    # Vector Search standard endpoint, ~0.5 SCU baseline (~$0.165/h list per SCU).
    "vector_search_usd_per_hour": 0.08,
    # Per-call extras — tiny in this app's volume.
    "vector_search_usd_per_query": 0.0001,
    # Genie answers run as Serverless SQL queries on the configured warehouse;
    # warehouse line already accounts for compute. Per-query overhead is ~0.
    "genie_usd_per_query": 0.0,
    # Lakeflow Connect Zerobus ingest: 0.143 DBU/GB at $0.55/DBU (Jobs Serverless) → ~$0.079/GB.
    "zerobus_usd_per_gb": 0.079,
    # Each agent invocation calls 1+ specialist; conservative average per agent call.
    "avg_bytes_per_event": 1024.0,
}

# Producer's per-tick row count to each Bronze table (used to estimate the
# rows landing in *_bronze when the Producer Job is RUNNING). Counts come
# from src/data_generation/config.py: 19 machines + 66 sensors + 34 materials.
# Quality checks and event_log are incident-driven and ~0 in steady state, so
# they're intentionally excluded from this baseline estimate. Authoritative
# actuals: SELECT COUNT(*) FROM <catalog>.<schema>.<table>_bronze.
_PRODUCER_BRONZE_ROWS_PER_TICK: dict[str, int] = {
    "telemetry_bronze": 66,
    "machine_state_bronze": 19,
    "inventory_status_bronze": 34,
    "line_kpi_bronze": 1,
}
_PRODUCER_TICK_HZ = 1.0
_PRODUCER_BRONZE_ROWS_PER_TICK_TOTAL = sum(_PRODUCER_BRONZE_ROWS_PER_TICK.values())

# Cache for /ops/cost-summary "is the producer/syncer/warehouse running?" probes.
# Hit this on every cost-summary refresh; the fresh probe takes ~1.5s
# (jobs.get_run + warehouses.get calls), the cache TTL is below.
_RESOURCE_STATE_CACHE: dict[str, Any] = {}


def _resource_running_states() -> dict[str, Any]:
    """Best-effort check of which app resources are currently RUNNING.

    Cached for 30 s to avoid calling the SDK on every cost-live request. When a
    resource is RUNNING we amortise its hourly rate over the requested window;
    when STOPPED we cost it as $0. The Synced Tables count comes from the env
    (number of tables created via ``scripts/create_synced_tables_autoscale.py``).
    """
    now = time.time()
    cache = _RESOURCE_STATE_CACHE
    if cache.get("ts") and (now - cache["ts"] < 30):
        return cache["data"]

    out: dict[str, Any] = {
        "warehouse": {"id": os.environ.get("DATABRICKS_WAREHOUSE_ID", "").strip() or None,
                      "running": False, "state": "unknown"},
        "producer_job": {"id": os.environ.get("OPS_PRODUCER_JOB_ID", "").strip() or None,
                         "running": False, "state": "unknown"},
        "lakebase_syncer": {"id": os.environ.get("OPS_LAKEBASE_SYNCER_JOB_ID", "").strip() or None,
                            "running": False, "state": "unknown"},
        "synced_tables": {"count": int(os.environ.get("OPS_SYNCED_TABLES_COUNT", "0") or 0),
                          "active": False},
    }
    def _enum_state(obj: Any) -> str:
        """Return the bare enum name (``"RUNNING"``) regardless of whether the
        SDK gave us an Enum instance (``PipelineState.RUNNING``) or a string
        already (``"RUNNING"``).

        Buggy version did ``str(enum).upper()`` which gave
        ``"WAREHOUSESTATE.RUNNING"`` and then ``state == "RUNNING"`` was always
        False, so the warehouse always showed ``running=false`` even when
        actually running. Stripping anything before the last ``.`` and
        upper-casing produces the canonical name.
        """
        if obj is None:
            return ""
        # Enum has a `.name` attribute that gives "RUNNING" without prefix.
        name = getattr(obj, "name", None)
        if name:
            return str(name).upper()
        s = str(obj)
        return s.split(".")[-1].upper() if "." in s else s.upper()

    try:
        w = _ops_workspace()
        # Each branch sets `state` to one of:
        #   RUNNING / STARTING / IDLE / STOPPED / unknown / "error:<msg>" / "permission_denied"
        # The cost panel reads `state` (not just `running`) so a permission gap or
        # SDK failure surfaces as a visible error instead of a misleading "idle".
        if wh_id := out["warehouse"]["id"]:
            try:
                wh = w.warehouses.get(id=wh_id)
                state = _enum_state(getattr(wh, "state", ""))
                out["warehouse"]["state"] = state or "unknown"
                # STARTING also incurs cost.
                out["warehouse"]["running"] = state in ("RUNNING", "STARTING")
            except Exception as e:
                msg = str(e)
                if "PERMISSION_DENIED" in msg.upper() or "403" in msg or "does not have" in msg.lower():
                    out["warehouse"]["state"] = "permission_denied"
                else:
                    out["warehouse"]["state"] = f"error:{msg[:60]}"
                logger.warning("Warehouse state lookup failed for %s: %s", wh_id, msg[:200])
        # Helper: a job is "running" if any run is in an active life-cycle
        # state (PENDING, RUNNING, QUEUED, BLOCKED, WAITING_FOR_RETRY, TERMINATING).
        # `active_only=True` already filters to non-terminal runs but we also
        # surface the *specific* state so the cost panel doesn't say "IDLE"
        # when in fact the run is QUEUED waiting for compute.
        def _detect_job_state(jid: int) -> tuple[bool, str]:
            try:
                runs = list(w.jobs.list_runs(job_id=jid, limit=1, active_only=True))
                if not runs:
                    return False, "IDLE"
                rs = _ops_to_dict(_ops_to_dict(runs[0]).get("state"))
                # ``life_cycle_state`` may be a ``RunLifeCycleState`` enum or a
                # plain string depending on SDK version. ``_enum_state`` unwraps
                # both shapes so we don't accidentally compare against
                # ``"RUNLIFECYCLESTATE.RUNNING"``.
                life = _enum_state(rs.get("life_cycle_state")) or "RUNNING"
                # PENDING / QUEUED / WAITING_FOR_RETRY are *active* but not yet
                # producing data. We treat them as "running" for cost purposes
                # (compute may already be allocated / billable for QUEUED on
                # serverless) and label them honestly.
                running = life in ("RUNNING", "PENDING", "QUEUED", "BLOCKED",
                                    "WAITING_FOR_RETRY", "TERMINATING")
                return running, life
            except Exception:
                raise

        if job_id := out["producer_job"]["id"]:
            try:
                running, state = _detect_job_state(int(job_id))
                out["producer_job"]["running"] = running
                out["producer_job"]["state"] = state
            except Exception as e:
                msg = str(e)
                if "PERMISSION_DENIED" in msg.upper() or "403" in msg or "does not have" in msg.lower():
                    out["producer_job"]["state"] = "permission_denied"
                else:
                    out["producer_job"]["state"] = f"error:{msg[:60]}"
                logger.warning("Producer job state lookup failed for %s: %s", job_id, msg[:200])
        if syncer_id := out["lakebase_syncer"]["id"]:
            try:
                running, state = _detect_job_state(int(syncer_id))
                out["lakebase_syncer"]["running"] = running
                out["lakebase_syncer"]["state"] = state
            except Exception as e:
                msg = str(e)
                if "PERMISSION_DENIED" in msg.upper() or "403" in msg or "does not have" in msg.lower():
                    out["lakebase_syncer"]["state"] = "permission_denied"
                else:
                    out["lakebase_syncer"]["state"] = f"error:{msg[:60]}"
                logger.warning("Syncer state lookup failed for %s: %s", syncer_id, msg[:200])
        # Synced tables count drives sync compute amortisation.
        out["synced_tables"]["active"] = out["synced_tables"]["count"] > 0
    except Exception:
        pass

    _RESOURCE_STATE_CACHE.update({"ts": now, "data": out})
    return out


def _human_bytes(n: float) -> str:
    """Format a byte count with KB/MB/GB/TB/PB suffix and 2 decimals."""
    if n <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    idx = 0
    val = float(n)
    while val >= 1024 and idx < len(units) - 1:
        val /= 1024.0
        idx += 1
    return f"{val:.2f} {units[idx]}"


_BYTES_UNIT_SQL = (
    "CASE "
    "WHEN upper(coalesce(usage_unit, '')) IN ('BYTES','BYTE') THEN usage_quantity "
    "WHEN upper(coalesce(usage_unit, '')) IN ('KB','KIB') THEN usage_quantity * 1024 "
    "WHEN upper(coalesce(usage_unit, '')) IN ('MB','MIB') THEN usage_quantity * 1024 * 1024 "
    "WHEN upper(coalesce(usage_unit, '')) IN ('GB','GIB') THEN usage_quantity * 1024 * 1024 * 1024 "
    "WHEN upper(coalesce(usage_unit, '')) IN ('TB','TIB') THEN usage_quantity * 1024 * 1024 * 1024 * 1024 "
    "ELSE 0 END"
)


_WORKSPACE_ID_CACHE: dict[str, Any] = {}


def _resolve_workspace_id() -> str | None:
    """The workspace whose billing this app should report, or None if unknown.

    ``DATABRICKS_WORKSPACE_ID`` was read here from the start but is set by
    nothing — not app.yaml, not the bundle, not render_app_yaml.py. An unset
    value silently widened every billing query from "this workspace" to "every
    workspace in the account", so the cost panel in a shared demo account was
    reporting other teams' jobs as though they were the app's. Asking the SDK
    is the fix: the app already authenticates against exactly the workspace it
    is deployed in, so the answer is always available and always right. The env
    var still wins when set, which keeps a manual override for anyone pointing
    a local dev server at a different workspace.
    """
    env_value = (os.environ.get("DATABRICKS_WORKSPACE_ID", "") or "").strip()
    if env_value.isdigit():
        return env_value
    if "id" in _WORKSPACE_ID_CACHE:
        return _WORKSPACE_ID_CACHE["id"]
    resolved: str | None = None
    try:
        from agent_server.tools.workspace_client import app_workspace_client

        candidate = str(app_workspace_client().get_workspace_id()).strip()
        resolved = candidate if candidate.isdigit() else None
    except Exception as e:  # noqa: BLE001 - falls back to account-wide, which we label
        logger.warning("Could not resolve workspace id for cost scoping: %s", str(e)[:200])
    _WORKSPACE_ID_CACHE["id"] = resolved
    return resolved


_APP_IDENTITY_CACHE: dict[str, Any] = {}


def _resolve_app_identity() -> tuple[str | None, str | None]:
    """``(principal_id, principal_name)`` of the identity the app runs SQL as.

    Warehouse queries go out as the app service principal (see
    ``tools/sql_query.py``), and ``system.query.history`` records that in
    ``executed_as_user_id`` / ``executed_as``. Matching on the id is the
    reliable half — the name is an application UUID for a service principal
    and a display name for a user, so both are checked.
    """
    if "id" in _APP_IDENTITY_CACHE:
        return _APP_IDENTITY_CACHE["id"], _APP_IDENTITY_CACHE["name"]
    principal_id: str | None = None
    principal_name: str | None = None
    try:
        from agent_server.tools.workspace_client import app_workspace_client

        me = app_workspace_client().current_user.me()
        principal_id = str(getattr(me, "id", "") or "").strip() or None
        principal_name = str(getattr(me, "user_name", "") or "").strip() or None
    except Exception as e:  # noqa: BLE001 - attribution degrades, panel still renders
        logger.warning("Could not resolve app identity for query attribution: %s", str(e)[:200])
    _APP_IDENTITY_CACHE["id"] = principal_id
    _APP_IDENTITY_CACHE["name"] = principal_name
    return principal_id, principal_name


def _sql_literal(value: str) -> str:
    """Single-quote a value for inline SQL. Inputs here are ids from the SDK
    and the environment rather than request data, but these strings are
    interpolated into a query, so quoting is not optional."""
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


_WH_SHARE_CACHE: dict[str, Any] = {}
_WH_SHARE_LOCK = threading.Lock()
# `system.query.history` holds account-wide records for the whole region and
# offers nothing to prune a warehouse_id filter against, so this aggregate costs
# ~17s of warehouse time — measured, not guessed. Recomputing it on every panel
# refresh would have made the Cost tab the app's single largest warehouse
# consumer, and worse, a self-fulfilling one: the panel's own queries run as the
# app service principal, so each load would inflate the very share it reports.
# The window is 24h and the source table has ingestion lag of its own, so a
# result minutes old is no less true than a fresh one.
_WH_SHARE_TTL_S = 600.0


def _warehouse_query_share(_sql: Any, warehouse_id: str, window_h: int,
                           warehouse_dbu: float | None) -> dict[str, Any] | None:
    """Split a shared warehouse's cost between this app and everything else.

    A SQL warehouse bills for uptime, not for queries, so query history cannot
    measure what the app spent — it can only measure the app's share of the
    work the warehouse did. This apportions the warehouse's DBU by that share,
    which spreads idle time across tenants in proportion to their usage and
    makes every tenant's share sum back to the actual bill. That is a
    convention, not a measurement, and the payload says so.

    Returns None when the warehouse isn't known or query history can't be read;
    the caller treats that as "no attribution available" rather than as zero.
    """
    if not warehouse_id:
        return None

    # Keyed on the window too: the same warehouse over 6h and over 24h are
    # different questions with different answers.
    cache_key = f"{warehouse_id}|{window_h}"
    now = time.time()
    with _WH_SHARE_LOCK:
        hit = _WH_SHARE_CACHE.get(cache_key)
        if hit and (now - hit["at"]) < _WH_SHARE_TTL_S:
            # DBU comes from the caller's fresh billing read, so only the query
            # split is reused; the apportioned figures are recomputed from it.
            return _apportion(hit["split"], warehouse_dbu, age_s=round(now - hit["at"], 1))

    principal_id, principal_name = _resolve_app_identity()
    identity_clauses = []
    if principal_id:
        identity_clauses.append(f"executed_as_user_id = {_sql_literal(principal_id)}")
    if principal_name:
        identity_clauses.append(f"executed_as = {_sql_literal(principal_name)}")

    from agent_server.tools.sql_query import APP_QUERY_TAG_KEY, APP_QUERY_TAG_VALUE

    tag_clause = (
        f"element_at(query_tags, {_sql_literal(APP_QUERY_TAG_KEY)}) = {_sql_literal(APP_QUERY_TAG_VALUE)}"
    )

    def _run(app_predicate: str) -> Any:
        return _sql.invoke(
            f"""
            SELECT
              CASE WHEN {app_predicate} THEN 'app' ELSE 'other' END AS attribution,
              count(*) AS statements,
              coalesce(sum(total_duration_ms), 0) AS duration_ms,
              coalesce(sum(CASE WHEN query_source.genie_space_id IS NOT NULL
                                THEN total_duration_ms ELSE 0 END), 0) AS genie_duration_ms
            FROM system.query.history
            WHERE compute.warehouse_id = {_sql_literal(warehouse_id)}
              AND start_time >= current_timestamp() - INTERVAL {window_h} HOURS
            GROUP BY 1
            """
        )

    def _failed(result: Any) -> bool:
        return not isinstance(result, list) or (
            bool(result) and isinstance(result[0], dict) and "error" in result[0]
        )

    # `query_tags` is Public Preview and absent on older metastores, where
    # referencing it fails the whole statement — so the tag is an upgrade on
    # identity matching, never a dependency.
    attribution_key = "identity+tag"
    rows = _run(" OR ".join([*identity_clauses, tag_clause]))
    if _failed(rows) and identity_clauses:
        attribution_key = "identity"
        rows = _run(" OR ".join(identity_clauses))
    if _failed(rows):
        detail = rows[0].get("error") if isinstance(rows, list) and rows else "unknown error"
        return {"available": False, "warehouse_id": warehouse_id,
                "note": f"Could not read system.query.history: {str(detail)[:160]}"}

    buckets = {"app": {"statements": 0, "duration_ms": 0.0, "genie_duration_ms": 0.0},
               "other": {"statements": 0, "duration_ms": 0.0, "genie_duration_ms": 0.0}}
    for r in rows:
        if not isinstance(r, dict):
            continue
        bucket = buckets.get(str(r.get("attribution")))
        if bucket is None:
            continue
        bucket["statements"] = int(_safe_num(r.get("statements")))
        bucket["duration_ms"] = _safe_num(r.get("duration_ms"))
        bucket["genie_duration_ms"] = _safe_num(r.get("genie_duration_ms"))

    split = {"warehouse_id": warehouse_id, "attribution_key": attribution_key, **buckets}
    with _WH_SHARE_LOCK:
        _WH_SHARE_CACHE[cache_key] = {"at": time.time(), "split": split}
    return _apportion(split, warehouse_dbu, age_s=0.0)


def _apportion(split: dict[str, Any], warehouse_dbu: float | None, age_s: float) -> dict[str, Any]:
    """Turn a cached app/other query-time split into apportioned DBU and USD."""
    app, other = split["app"], split["other"]
    total_ms = app["duration_ms"] + other["duration_ms"]
    # No queries at all means the warehouse was idle for the whole window. Its
    # DBU is then genuinely unattributable, and 0/0 must not become "100% app".
    share = (app["duration_ms"] / total_ms) if total_ms > 0 else None
    app_dbu = round((warehouse_dbu or 0.0) * share, 3) if (share is not None and warehouse_dbu) else None

    return {
        "available": True,
        "warehouse_id": split["warehouse_id"],
        "attribution_key": split["attribution_key"],
        "app_statements": app["statements"],
        "other_statements": other["statements"],
        "app_duration_ms": round(app["duration_ms"], 1),
        "other_duration_ms": round(other["duration_ms"], 1),
        "app_genie_duration_ms": round(app["genie_duration_ms"], 1),
        "app_share": round(share, 4) if share is not None else None,
        "warehouse_dbu": warehouse_dbu,
        "app_dbu": app_dbu,
        "app_estimated_usd": _estimate_usd("SQL Warehouse", app_dbu) if app_dbu is not None else None,
        "split_age_seconds": age_s,
        "note": (
            "Warehouses bill for uptime, so this apportions the warehouse's DBU by each "
            "tenant's share of query time (total_duration_ms). Idle time is spread across "
            "tenants in proportion to their usage rather than measured per tenant."
        ),
    }


@app.get("/ops/cost-summary")
async def ops_cost_summary(hours: int = 24, include_details: bool = False):
    """Offload the rollup to a worker thread.

    The body below is half a dozen sequential warehouse round trips and nothing
    else — no `await` anywhere in it. Declared `async def` and called directly,
    it ran that blocking work *on the event loop*, so for as long as it took,
    the process served nothing else: every other request queued and the
    `/serving/stream` SSE ticks stopped dead. Measured against dev, a cold call
    froze the live stream for 44.9s, versus its normal 1.04s tick.

    Every other handler here already offloads (`asyncio.to_thread`) for exactly
    this reason — see the note above `ops_lakebase_state`.
    """
    return await asyncio.to_thread(_cost_summary_payload, hours, include_details)


def _cost_summary_payload(hours: int = 24, include_details: bool = False):
    """Return cost/usage rollups from system.billing.usage with $ estimates and freshness.

    Always returns: totals, components (DBU + USD), app_attribution, zerobus, data_processed,
    pricing rates, scope, freshness. With include_details=true also returns trend +
    matched/unmatched detail with top unmatched rows.

    Blocking — call it through a thread, never straight from the event loop.
    """
    window_h = max(1, min(hours, 24 * 14))
    try:
        from agent_server.tools.sql_query import sql_query as _sql
    except Exception as e:
        return JSONResponse({"error": f"SQL tool unavailable: {str(e)[:120]}"}, status_code=500)

    workspace_id = _resolve_workspace_id()
    workspace_filter = f" AND workspace_id = {workspace_id}" if workspace_id else ""
    warehouse_id = (os.environ.get("DATABRICKS_WAREHOUSE_ID", "") or "").strip()
    producer_job_id = (os.environ.get("OPS_PRODUCER_JOB_ID", "") or "").strip()
    setup_job_id = (os.environ.get("OPS_SETUP_JOB_ID", "") or "").strip()
    zerobus_enabled = _truthy(os.environ.get("ZEROBUS_INGEST_ENABLED"))
    pipeline_mode = "zerobus" if zerobus_enabled else "no-zerobus"
    # `usage_date` is the partition column; `usage_start_time` is not. Filtering
    # on the timestamp alone made every one of the six-to-eight queries behind a
    # single panel load scan the account's entire billing history. The date
    # predicate is a deliberate superset of the timestamp window (floor to the
    # UTC day) so it prunes without changing which rows survive.
    base_where = (
        f"usage_date >= to_date(current_timestamp() - INTERVAL {window_h} HOURS)"
        f" AND usage_start_time >= current_timestamp() - INTERVAL {window_h} HOURS"
        f"{workspace_filter}"
    )

    summary_rows = _sql.invoke(
        f"""
        SELECT
          coalesce(sum(CASE WHEN upper(coalesce(usage_unit, '')) = 'DBU' THEN usage_quantity ELSE 0 END), 0) AS total_dbu,
          coalesce(sum(CASE WHEN upper(coalesce(usage_unit, '')) = 'DBU' THEN 1 ELSE 0 END), 0) AS total_records,
          coalesce(sum(CASE
            WHEN (upper(coalesce(billing_origin_product, '')) LIKE '%ZEROBUS%' OR upper(coalesce(sku_name, '')) LIKE '%ZEROBUS%')
            THEN {_BYTES_UNIT_SQL}
            ELSE 0 END), 0) AS zerobus_ingested_bytes,
          coalesce(sum({_BYTES_UNIT_SQL}), 0) AS data_processed_bytes,
          coalesce(sum(CASE
            WHEN upper(coalesce(usage_unit, '')) IN ('BYTES','BYTE','KB','KIB','MB','MIB','GB','GIB','TB','TIB')
            THEN 1 ELSE 0 END), 0) AS data_processed_records,
          max(usage_start_time) AS latest_usage_ts
        FROM system.billing.usage
        WHERE {base_where}
        """
    )

    if isinstance(summary_rows, list) and summary_rows and isinstance(summary_rows[0], dict) and "error" in summary_rows[0]:
        err = summary_rows[0]["error"]
        return {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "window_hours": window_h,
            "pipeline_mode": pipeline_mode,
            "totals": {"dbu": 0.0, "billing_rows": 0, "estimated_usd": 0.0},
            "components": [],
            "app_attribution": {
                "warehouse_dbu": None,
                "producer_job_dbu": None,
                "setup_job_dbu": None,
                "total_app_dbu": 0.0,
                "total_app_estimated_usd": 0.0,
            },
            "warehouse_share": None,
            "zerobus": {
                "enabled": zerobus_enabled,
                "ingested_bytes": 0,
                "ingested_gb": 0.0,
                "ingested_human": "0 B",
                "observed_dbu": 0.0,
                "rate_dbu_per_gb": 0.143,
                "estimated_dbu": 0.0,
                "estimated_usd": 0.0,
            },
            "data_processed": {"records": 0, "bytes": 0, "bytes_human": "0 B"},
            "pricing": {"rates_usd_per_dbu": _DBU_USD_RATES, "note": "USD estimates are list-price approximations and vary by SKU and discount."},
            "scope": {
                "workspace_id_filter": workspace_id,
                "attribution_mode": "workspace-scoped" if workspace_id else "account-wide",
                "app_resources": {
                    "warehouse_id": warehouse_id or None,
                    "producer_job_id": producer_job_id or None,
                    "setup_job_id": setup_job_id or None,
                },
            },
            "freshness": {"latest_usage_ts": None, "lag_hours": None},
            "details": None,
            "warning": err,
        }

    row = summary_rows[0] if isinstance(summary_rows, list) and summary_rows else {}
    dbu_total = _safe_num(row.get("total_dbu"))
    dbu_records = int(_safe_num(row.get("total_records")))
    zerobus_ingested_bytes = _safe_num(row.get("zerobus_ingested_bytes"))
    zerobus_ingested_gb = zerobus_ingested_bytes / (1024 * 1024 * 1024)
    latest_ts = row.get("latest_usage_ts")
    latest_ts_str = str(latest_ts) if latest_ts else None
    lag_hours = None
    if latest_ts_str:
        try:
            from datetime import datetime as _dt
            parsed = _dt.fromisoformat(latest_ts_str.replace("Z", "+00:00"))
            # A Spark TIMESTAMP renders without an offset, and `.astimezone()`
            # reads a naive datetime as *local* time. The warehouse returns UTC,
            # so on a UTC+2 machine every billing row looked two hours older
            # than it was and the ">6h stale" warning fired two hours early.
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            lag_seconds = max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())
            lag_hours = round(lag_seconds / 3600.0, 2)
        except Exception:
            lag_hours = None

    components_rows = _sql.invoke(
        f"""
        SELECT
          {_usage_category_sql()} AS component,
          sum(usage_quantity) AS dbu,
          count(*) AS records
        FROM system.billing.usage
        WHERE {base_where} AND upper(coalesce(usage_unit, '')) = 'DBU'
        GROUP BY 1
        ORDER BY 2 DESC
        LIMIT 10
        """
    )
    components: list[dict[str, Any]] = []
    if isinstance(components_rows, list):
        for c in components_rows:
            if isinstance(c, dict) and "error" not in c:
                comp = str(c.get("component") or "Other")
                dbu = round(_safe_num(c.get("dbu")), 3)
                components.append({
                    "component": comp,
                    "dbu": dbu,
                    "estimated_usd": _estimate_usd(comp, dbu),
                    "records": int(_safe_num(c.get("records"))),
                })

    other_rows = _sql.invoke(
        f"""
        SELECT
          CASE
            WHEN upper(coalesce(billing_origin_product, '')) LIKE '%LAKEBASE%' OR upper(coalesce(sku_name, '')) LIKE '%LAKEBASE%' THEN 'Lakebase storage'
            WHEN upper(coalesce(sku_name, '')) LIKE '%STORAGE%' THEN 'Storage'
            WHEN upper(coalesce(sku_name, '')) LIKE '%NETWORK%' OR upper(coalesce(sku_name, '')) LIKE '%EGRESS%' THEN 'Network egress'
            ELSE coalesce(sku_name, 'Other')
          END AS component,
          upper(coalesce(usage_unit, '')) AS unit,
          sum(usage_quantity) AS quantity,
          count(*) AS records
        FROM system.billing.usage
        WHERE {base_where} AND upper(coalesce(usage_unit, '')) NOT IN ('DBU', '')
        GROUP BY 1, 2
        ORDER BY 3 DESC
        LIMIT 10
        """
    )
    other_resources: list[dict[str, Any]] = []
    if isinstance(other_rows, list):
        for r in other_rows:
            if isinstance(r, dict) and "error" not in r:
                other_resources.append({
                    "component": str(r.get("component") or "Other"),
                    "unit": str(r.get("unit") or "—"),
                    "quantity": round(_safe_num(r.get("quantity")), 3),
                    "records": int(_safe_num(r.get("records"))),
                })

    def _query_metadata_dbu(field: str, value: str) -> float | None:
        if not value:
            return None
        rows = _sql.invoke(
            f"""
            SELECT coalesce(sum(usage_quantity), 0) AS dbu
            FROM system.billing.usage
            WHERE {base_where}
              AND upper(coalesce(usage_unit, '')) = 'DBU'
              AND cast(usage_metadata['{field}'] AS string) = '{value}'
            """
        )
        if isinstance(rows, list) and rows and isinstance(rows[0], dict) and "error" not in rows[0]:
            return round(_safe_num(rows[0].get("dbu")), 3)
        return None

    warehouse_dbu = _query_metadata_dbu("warehouse_id", warehouse_id)
    producer_job_dbu = _query_metadata_dbu("job_id", producer_job_id)
    setup_job_dbu = _query_metadata_dbu("job_id", setup_job_id)
    total_app_dbu = round(sum(v or 0.0 for v in (warehouse_dbu, producer_job_dbu, setup_job_dbu)), 3)
    sql_usd = _estimate_usd("SQL Warehouse", warehouse_dbu or 0.0)
    jobs_usd = _estimate_usd("Jobs", (producer_job_dbu or 0.0) + (setup_job_dbu or 0.0))
    total_app_usd = round(sql_usd + jobs_usd, 4)
    # `warehouse_dbu` above credits the app with everything the warehouse burned,
    # which is right for a dedicated warehouse and wrong for a shared one. This
    # sits alongside it rather than replacing it, so the two can be compared.
    warehouse_share = _warehouse_query_share(_sql, warehouse_id, window_h, warehouse_dbu)

    estimated_zerobus_dbu = round(zerobus_ingested_gb * 0.143, 3) if zerobus_enabled else 0.0
    observed_zerobus_dbu = round(sum(c["dbu"] for c in components if c["component"] == "Zerobus"), 3)
    zerobus_payload = {
        "enabled": zerobus_enabled,
        "ingested_bytes": int(zerobus_ingested_bytes) if zerobus_enabled else 0,
        "ingested_gb": round(zerobus_ingested_gb, 6) if zerobus_enabled else 0.0,
        "ingested_human": _human_bytes(zerobus_ingested_bytes) if zerobus_enabled else "0 B",
        "observed_dbu": observed_zerobus_dbu,
        "rate_dbu_per_gb": 0.143,
        "estimated_dbu": estimated_zerobus_dbu,
        "estimated_usd": _estimate_usd("Zerobus", estimated_zerobus_dbu),
    }

    total_estimated_usd = round(sum(c.get("estimated_usd", 0.0) for c in components), 4)

    details: dict[str, Any] | None = None
    if include_details:
        trend: list[dict[str, Any]] = []
        trend_rows = _sql.invoke(
            f"""
            SELECT date_trunc('hour', usage_start_time) AS hour, sum(usage_quantity) AS dbu
            FROM system.billing.usage
            WHERE {base_where} AND upper(coalesce(usage_unit, '')) = 'DBU'
            GROUP BY 1
            ORDER BY 1 DESC
            LIMIT 48
            """
        )
        if isinstance(trend_rows, list):
            for t in reversed(trend_rows):
                if isinstance(t, dict) and "error" not in t:
                    trend.append({"hour": str(t.get("hour") or "")[:13], "dbu": round(_safe_num(t.get("dbu")), 3)})

        attribution_rows = _sql.invoke(
            f"""
            SELECT
              coalesce(billing_origin_product, '') AS billing_origin_product,
              coalesce(sku_name, '') AS sku_name,
              upper(coalesce(usage_unit, '')) AS usage_unit,
              sum(usage_quantity) AS usage_quantity
            FROM system.billing.usage
            WHERE {base_where}
            GROUP BY 1,2,3
            ORDER BY usage_quantity DESC
            LIMIT 500
            """
        )
        matched_dbu = 0.0
        unmatched_dbu = 0.0
        unmatched_rows: list[dict[str, Any]] = []
        verification_warning = None
        if isinstance(attribution_rows, list):
            if attribution_rows and isinstance(attribution_rows[0], dict) and "error" in attribution_rows[0]:
                verification_warning = str(attribution_rows[0].get("error") or "Failed to query attribution rows")
            else:
                for r in attribution_rows:
                    if not isinstance(r, dict):
                        continue
                    unit = str(r.get("usage_unit") or "").upper()
                    if unit != "DBU":
                        continue
                    qty = _safe_num(r.get("usage_quantity"))
                    origin = str(r.get("billing_origin_product") or "")
                    sku = str(r.get("sku_name") or "")
                    cat = _usage_category(origin, sku)
                    if cat == "Other":
                        unmatched_dbu += qty
                        unmatched_rows.append({
                            "billing_origin_product": origin or "—",
                            "sku_name": sku or "—",
                            "usage_unit": unit,
                            "usage_quantity": round(qty, 3),
                        })
                    else:
                        matched_dbu += qty
        details = {
            "trend": trend,
            "matched_dbu": round(matched_dbu, 3),
            "unmatched_dbu": round(unmatched_dbu, 3),
            "top_unmatched_rows": unmatched_rows[:8],
            "verification_warning": verification_warning,
        }

    warning = None
    if dbu_records == 0:
        warning = "No DBU usage rows in window. system.billing.usage typically lags 1-4 hours."
    elif not workspace_id:
        # Silently reporting the whole account as though it were this app is the
        # failure mode that makes the panel actively misleading, so say it.
        warning = (
            "Could not determine this workspace's id, so totals and components cover EVERY "
            "workspace in the account, not just this app. Set DATABRICKS_WORKSPACE_ID to scope them. "
            "The App attribution card is unaffected — it filters on resource id."
        )
    elif lag_hours is not None and lag_hours > 6:
        warning = f"system.billing.usage latest row is {lag_hours} h old; recent activity may not be reflected yet."

    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "window_hours": window_h,
        "pipeline_mode": pipeline_mode,
        "totals": {
            "dbu": round(dbu_total, 3),
            "billing_rows": dbu_records,
            "estimated_usd": total_estimated_usd,
        },
        "components": components,
        "other_resources": other_resources,
        "app_attribution": {
            "warehouse_id": warehouse_id or None,
            "warehouse_dbu": warehouse_dbu,
            "warehouse_estimated_usd": _estimate_usd("SQL Warehouse", warehouse_dbu or 0.0) if warehouse_dbu is not None else None,
            "producer_job_id": producer_job_id or None,
            "producer_job_dbu": producer_job_dbu,
            "setup_job_id": setup_job_id or None,
            "setup_job_dbu": setup_job_dbu,
            "total_app_dbu": total_app_dbu,
            "total_app_estimated_usd": total_app_usd,
        },
        "warehouse_share": warehouse_share,
        "zerobus": zerobus_payload,
        "data_processed": {
            "records": int(_safe_num(row.get("data_processed_records"))),
            "bytes": int(_safe_num(row.get("data_processed_bytes"))),
            "bytes_human": _human_bytes(_safe_num(row.get("data_processed_bytes"))),
        },
        "pricing": {
            "rates_usd_per_dbu": _DBU_USD_RATES,
            "note": "USD numbers are list-price approximations using fixed rates per component. Real cost depends on cloud, tier, and discounts.",
        },
        "scope": {
            "workspace_id_filter": workspace_id,
            "attribution_mode": "workspace-scoped" if workspace_id else "account-wide",
            "app_resources": {
                "warehouse_id": warehouse_id or None,
                "producer_job_id": producer_job_id or None,
                "setup_job_id": setup_job_id or None,
            },
        },
        "freshness": {"latest_usage_ts": latest_ts_str, "lag_hours": lag_hours},
        "details": details,
        "warning": warning,
    }


@app.get("/ops/cost-live")
async def ops_cost_live(minutes: int = 60):
    """Live in-app usage estimate for short windows (system.billing.usage lags 1-4h).

    Aggregates internal counters (SSE ticks, Lakebase reads, agent invocations,
    LLM token usage) over the requested window and converts to a rough USD estimate
    using documented per-unit rates. Does NOT query system.billing.usage.
    """
    win_min = max(1, min(minutes, 360))
    sums = _live_aggregate(win_min)
    elapsed_total_min = max(1.0, (time.time() - _LIVE_STARTED_AT) / 60.0)
    win_min_eff = min(float(win_min), elapsed_total_min)
    zerobus_enabled = _truthy(os.environ.get("ZEROBUS_INGEST_ENABLED"))
    pipeline_mode = "zerobus" if zerobus_enabled else "no-zerobus"

    llm_in = sums.get("llm_input_tokens", 0.0)
    llm_out = sums.get("llm_output_tokens", 0.0)
    llm_calls = sums.get("llm_calls", 0.0)
    sse_ticks = sums.get("sse_ticks", 0.0)
    sse_payload_bytes = sums.get("sse_payload_bytes", 0.0)
    sse_payload_rows = sums.get("sse_payload_rows", 0.0)
    lake_reads = sums.get("lakebase_reads", 0.0)
    agent_calls = sums.get("agent_invocations", 0.0)
    events_pub = sums.get("events_published", 0.0)
    vs_calls = sums.get("vector_search_calls", 0.0)
    genie_calls = sums.get("genie_queries", 0.0)

    llm_in_usd = round((llm_in / 1000.0) * _LIVE_RATES["llm_input_usd_per_1k_tokens"], 4)
    llm_out_usd = round((llm_out / 1000.0) * _LIVE_RATES["llm_output_usd_per_1k_tokens"], 4)
    llm_dbu = round(((llm_in + llm_out) / 1000.0) * _LIVE_RATES["llm_dbu_per_1k_tokens"], 4)

    win_h = win_min_eff / 60.0
    states = _resource_running_states()
    wh_on = bool(states.get("warehouse", {}).get("running"))
    prod_on = bool(states.get("producer_job", {}).get("running"))
    syncer_on = bool(states.get("lakebase_syncer", {}).get("running"))
    synced_count = int(states.get("synced_tables", {}).get("count", 0) or 0)

    def _state_label(key: str, off_text: str) -> str:
        """Friendly text for `idle` rows. Surfaces permission/SDK errors so the
        panel doesn't lie to the user when detection itself is broken."""
        s = str(states.get(key, {}).get("state") or "").lower()
        if s == "permission_denied":
            return ("state hidden — app SP lacks CAN_VIEW. "
                    "Run scripts/grant_app_jobs_view.sh and reload.")
        if s.startswith("error:"):
            return f"state lookup failed ({s[6:]}). USD shown is best-effort 0."
        return off_text

    # Compute amortised hourly costs (only when the resource is currently on).
    lakebase_compute_usd = round(_LIVE_RATES["lakebase_compute_usd_per_hour"] * win_h, 4)
    app_compute_usd = round(_LIVE_RATES["app_compute_usd_per_hour"] * win_h, 4)
    wh_usd = round(_LIVE_RATES["sql_warehouse_usd_per_hour"] * win_h, 4) if wh_on else 0.0
    prod_usd = round(_LIVE_RATES["producer_job_usd_per_hour"] * win_h, 4) if prod_on else 0.0
    sync_usd = round(_LIVE_RATES["synced_table_usd_per_hour"] * synced_count * win_h, 4) if synced_count else 0.0
    syncer_usd = round(_LIVE_RATES["lakebase_syncer_usd_per_hour"] * win_h, 4) if syncer_on else 0.0
    # Vector Search: the endpoint is billed HOURLY for as long as it's online
    # — query count is a separate, tiny per-call charge. Earlier versions only
    # charged the hourly when ``vs_calls > 0`` in the window which under-
    # reported actual cost (an idle-but-live endpoint still bills). Toggle
    # ``OPS_VS_ENDPOINT_ALWAYS_ON=false`` for envs without an index.
    # When the flag is absent, read it off VS_ENDPOINT rather than assuming — the
    # same rule render_app_yaml.py applies when it derives the two together (true
    # iff an endpoint is named). The old default was a bare "true", so an app
    # missing the flag billed ~$58/month of hourly Vector Search against the
    # shipped configuration, which names no endpoint at all. Cost panels are
    # believed, so inventing a line item is worse than omitting one.
    _vs_default = "true" if os.environ.get("VS_ENDPOINT", "").strip() else "false"
    vs_endpoint_always_on = (os.environ.get("OPS_VS_ENDPOINT_ALWAYS_ON", _vs_default).strip().lower()
                             in ("1", "true", "yes", "on"))
    vs_endpoint_live = vs_endpoint_always_on or vs_calls > 0
    vs_endpoint_usd = round(_LIVE_RATES["vector_search_usd_per_hour"] * win_h, 4) if vs_endpoint_live else 0.0
    vs_query_usd = round(vs_calls * _LIVE_RATES["vector_search_usd_per_query"], 4)
    vs_usd = round(vs_endpoint_usd + vs_query_usd, 4)
    # Genie: compute runs on a SQL Warehouse. If ``OPS_GENIE_WAREHOUSE_ID`` is
    # set AND distinct from ``OPS_SQL_WAREHOUSE_ID``, bill it as a separate
    # warehouse line so users can see Genie's compute cost in isolation.
    # Otherwise the SQL Warehouse line above already includes Genie's compute.
    genie_wh_id = (os.environ.get("OPS_GENIE_WAREHOUSE_ID") or "").strip()
    sql_wh_id = (os.environ.get("OPS_SQL_WAREHOUSE_ID") or "").strip()
    genie_has_separate_warehouse = bool(genie_wh_id and genie_wh_id != sql_wh_id)
    genie_usd = round(genie_calls * _LIVE_RATES["genie_usd_per_query"], 4)
    # A dedicated Genie warehouse was billed a full hourly rate unconditionally,
    # while the main warehouse line is gated on `wh_on`. An idle Genie warehouse
    # therefore invented $0.70/h that Databricks never charged. Serverless
    # warehouses auto-stop, so "configured" and "running" are different things
    # and only the second one costs money; absent any query in the window we
    # treat it as stopped, which matches how the Producer job line behaves.
    genie_wh_running = genie_has_separate_warehouse and genie_calls > 0
    genie_wh_usd = (round(_LIVE_RATES["sql_warehouse_usd_per_hour"] * win_h, 4)
                    if genie_wh_running else 0.0)
    # Zerobus: only flows when the Producer Job is running (it's the only
    # writer to Zerobus in this app). When idle → 0 ingest by definition.
    # When running → estimate from producer throughput × running hours.
    # Producer spec: 19 machines × ~5 records/tick × 1 tick/s × ~1 KB ≈ 0.34 GB/h.
    PRODUCER_GB_PER_HOUR = 0.34
    if zerobus_enabled and prod_on:
        zb_gb = PRODUCER_GB_PER_HOUR * win_h
        zb_bytes = zb_gb * (1024.0 ** 3)
        zb_usd = round(zb_gb * _LIVE_RATES["zerobus_usd_per_gb"], 4)
    else:
        zb_bytes = 0.0
        zb_gb = 0.0
        zb_usd = 0.0
    # Bronze ingest row estimate — mirrors zerobus_estimate's "running-only"
    # accounting but expressed in rows/table instead of bytes. Only flows when
    # Producer Job is RUNNING; window is auto-clipped to app uptime via
    # win_min_eff so the count never claims more time than the app has been up.
    if prod_on:
        bronze_seconds = win_min_eff * 60.0
        bronze_per_table = {
            tbl: int(rows_per_tick * _PRODUCER_TICK_HZ * bronze_seconds)
            for tbl, rows_per_tick in _PRODUCER_BRONZE_ROWS_PER_TICK.items()
        }
        bronze_rows_total = sum(bronze_per_table.values())
    else:
        bronze_per_table = {tbl: 0 for tbl in _PRODUCER_BRONZE_ROWS_PER_TICK}
        bronze_rows_total = 0
    # If a separate Genie warehouse is configured we bill it independently of
    # the SQL Warehouse line; otherwise ``genie_usd`` is just the per-query
    # overhead (compute is already in the SQL Warehouse total).
    genie_total_usd = genie_wh_usd if genie_has_separate_warehouse else genie_usd

    def _line(name: str, usd: float, detail: str, *, on: bool | None = None) -> dict[str, Any]:
        row = {"component": name, "usd": usd, "detail": detail}
        if on is not None:
            row["status"] = "running" if on else "idle"
        return row

    producer_detail = (
        f"${_LIVE_RATES['producer_job_usd_per_hour']}/h × {win_h:.2f}h"
        if prod_on
        else _state_label("producer_job", "no active runs in window")
    )

    # App + Lakebase compute are billed for as long as the App is up and the
    # Lakebase Autoscale instance is provisioned — both are ALWAYS-ON for the
    # life of this app, so we surface them with ``on=True`` so the cost panel
    # doesn't leave them looking neutral/idle next to truly idle resources.
    components = [
        _line("App compute", app_compute_usd,
              f"${_LIVE_RATES['app_compute_usd_per_hour']}/h × {win_h:.2f}h "
              f"(Databricks Apps SP runtime — billed continuously while app is RUNNING)",
              on=True),
        _line("Lakebase compute", lakebase_compute_usd,
              f"${_LIVE_RATES['lakebase_compute_usd_per_hour']}/h × {win_h:.2f}h "
              f"(Lakebase Autoscale @ ~1 CU — billed continuously while the instance is provisioned)",
              on=True),
        _line("SQL Warehouse", wh_usd,
              (f"${_LIVE_RATES['sql_warehouse_usd_per_hour']}/h × {win_h:.2f}h"
               + (" — shared by Genie" if not genie_has_separate_warehouse else "")
               if wh_on else _state_label("warehouse", "warehouse STOPPED — no compute charged")),
              on=wh_on),
        _line("Producer job", prod_usd, producer_detail, on=prod_on),
        # This line existed in `total_usd` but had no row of its own, so the
        # Total printed under the table was larger than the rows above it by
        # $0.30/h per synced table with nothing on screen to account for the
        # difference. The total is now summed from these rows, so an unlisted
        # cost is no longer representable.
        _line("Synced tables", sync_usd,
              (f"{synced_count} table(s) × ${_LIVE_RATES['synced_table_usd_per_hour']}/h × {win_h:.2f}h "
               f"(Lakeflow sync pipelines kept warm per synced table)"
               if synced_count
               else "no synced tables configured (set OPS_SYNCED_TABLES_COUNT)"),
              on=bool(synced_count)),
        _line("Streaming QC + Dual-Write", syncer_usd,
              (f"${_LIVE_RATES['lakebase_syncer_usd_per_hour']}/h × {win_h:.2f}h "
               f"(continuous Spark Streaming: bronze → QC → gold_history Delta + "
               f"ai_serving.*_history Postgres via foreachBatch JDBC)"
               if syncer_on
               else (_state_label("lakebase_syncer", "streaming sync IDLE — no compute charged")
                     if states.get("lakebase_syncer", {}).get("id")
                     else "streaming sync not configured (set OPS_LAKEBASE_SYNCER_JOB_ID)")),
              on=syncer_on),
        _line("Vector Search", vs_usd,
              # Endpoint is billed HOURLY whether queried or not. ``vs_calls``
              # adds a tiny per-query overhead on top.
              ((f"endpoint live: ${_LIVE_RATES['vector_search_usd_per_hour']}/h × "
                f"{win_h:.2f}h (~0.5 SCU baseline) + {int(vs_calls)} queries × "
                f"${_LIVE_RATES['vector_search_usd_per_query']}/query")
               if vs_endpoint_live
               else "no Vector Search index configured (OPS_VS_ENDPOINT_ALWAYS_ON=false)"),
              on=vs_endpoint_live),
        _line("Genie",
              # Same value the total uses — this used to be a second copy of the
              # expression, free to drift from the one being summed.
              genie_total_usd,
              ((f"{int(genie_calls)} queries · dedicated Serverless SQL Warehouse "
                f"(OPS_GENIE_WAREHOUSE_ID={genie_wh_id}) — "
                f"${_LIVE_RATES['sql_warehouse_usd_per_hour']}/h × {win_h:.2f}h")
               if genie_wh_running
               else (f"dedicated Genie warehouse (OPS_GENIE_WAREHOUSE_ID={genie_wh_id}) had no queries "
                     f"in this window — assumed auto-stopped, no compute charged")
               if genie_has_separate_warehouse
               else (f"{int(genie_calls)} queries · compute folded into the SQL Warehouse line above "
                     f"(no separate Serverless SQL configured — set OPS_GENIE_WAREHOUSE_ID to split)")),
              on=genie_wh_running),
        _line("Zerobus ingest", zb_usd,
              ("zerobus mode disabled" if not zerobus_enabled
               else (f"~{zb_gb:.3f} GB × ${_LIVE_RATES['zerobus_usd_per_gb']}/GB "
                     f"(0.143 DBU/GB × $0.55/DBU). Producer JOB running for "
                     f"{win_h:.2f}h × ~{PRODUCER_GB_PER_HOUR} GB/h."
                     if prod_on
                     else "Configured but NOT ingesting — Producer JOB is IDLE. "
                          "Start it from the Admin tab to exercise Zerobus.")),
              on=(zerobus_enabled and prod_on)),
        _line("LLM (input)", llm_in_usd,
              f"{int(llm_in)} tokens × ${_LIVE_RATES['llm_input_usd_per_1k_tokens']}/1k"),
        _line("LLM (output)", llm_out_usd,
              f"{int(llm_out)} tokens × ${_LIVE_RATES['llm_output_usd_per_1k_tokens']}/1k"),
    ]
    # Summed from the rows the user can see rather than recomputed alongside
    # them, so the Total is the visible breakdown by construction.
    total_usd = round(sum(c["usd"] for c in components), 4)
    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "window_minutes": win_min,
        "window_minutes_effective": round(win_min_eff, 2),
        "pipeline_mode": pipeline_mode,
        "app_started_at": datetime.fromtimestamp(_LIVE_STARTED_AT, tz=timezone.utc).isoformat(),
        "resources": states,
        "data_in_app": {
            "events_in_window": int(events_pub),
            "rows_in_window": int(sse_payload_rows),
        },
        "counters": {
            "lakebase_reads": int(lake_reads),
            "agent_invocations": int(agent_calls),
            "llm_calls": int(llm_calls),
            "llm_input_tokens": int(llm_in),
            "llm_output_tokens": int(llm_out),
            "events_published": int(events_pub),
            "vector_search_calls": int(vs_calls),
            "genie_queries": int(genie_calls),
            # Kept in payload for debugging — UI no longer surfaces these as
            # they're heartbeat-style and don't drive cost decisions.
            "sse_ticks": int(sse_ticks),
            "sse_payload_bytes": int(sse_payload_bytes),
            "sse_payload_bytes_human": _human_bytes(sse_payload_bytes),
            "sse_payload_rows": int(sse_payload_rows),
        },
        "zerobus_estimate": {
            "enabled": zerobus_enabled,
            "producer_running": bool(prod_on),
            "estimated_bytes": int(zb_bytes),
            "estimated_bytes_human": _human_bytes(zb_bytes),
            "estimated_gb": round(zb_gb, 6),
            "rate_usd_per_gb": _LIVE_RATES["zerobus_usd_per_gb"],
            "estimated_usd": zb_usd,
            "producer_throughput_gb_per_hour": PRODUCER_GB_PER_HOUR,
            "note": (
                "Zerobus only ingests when the Producer Job is RUNNING. "
                "Estimate uses the producer's known throughput (~0.34 GB/h) × running hours. "
                "Authoritative actuals: system.billing.usage Zerobus SKU (Billing tab)."
            ),
        },
        "bronze_estimate": {
            "producer_running": bool(prod_on),
            "rows_per_tick_total": _PRODUCER_BRONZE_ROWS_PER_TICK_TOTAL,
            "rows_per_tick_per_table": _PRODUCER_BRONZE_ROWS_PER_TICK,
            "tick_hz": _PRODUCER_TICK_HZ,
            "window_minutes_effective": round(win_min_eff, 2),
            "estimated_rows_in_window": int(bronze_rows_total),
            "per_table_in_window": bronze_per_table,
            "note": (
                "Rows landing in *_bronze tables. Estimated from the producer's "
                f"known emission rate ({_PRODUCER_BRONZE_ROWS_PER_TICK_TOTAL} rows/tick × "
                f"{_PRODUCER_TICK_HZ:.0f} tick/s) × producer-running seconds in the "
                "window (auto-clipped to app uptime). Zero when Producer Job is IDLE. "
                "Authoritative actuals: SELECT COUNT(*) FROM <catalog>.<schema>."
                "<table>_bronze WHERE <ts column> >= now() - INTERVAL N MINUTE."
            ),
        },
        "tool_callers": {
            "vector_search": [
                "Strategic Advisor",
                "Maintenance Predictor",
                "Downtime Analyst",
                "Quality Specialist",
                "Supply Chain Advisor",
            ],
            "genie": [
                "OEE Coach",
                "Strategic Advisor",
            ],
            "examples": {
                "genie": "What's the OEE trend over the last 5 shifts?",
                "vector_search": "Have we seen a similar bearing failure on F1 before?",
            },
        },
        "estimated_dbu": {"llm": llm_dbu},
        "estimated_usd": {
            "total": total_usd,
            "components": components,
        },
        "rates": _LIVE_RATES,
        "excluded": [
            "Storage (S3/cloud volume) — billed separately by hyperscaler.",
            "Network egress — typically negligible for in-region SSE.",
            "Lakebase Autoscale storage GB-month — small for this workload.",
            "Photon overhead — already included in DBU.",
            ("Vector Search storage — billed per-vector-month (~$0.04/100k vectors). "
             "Index-at-rest is small here; endpoint hourly is the dominant cost."),
            ("Genie compute — billed as Serverless SQL Warehouse hours. Folded into "
             "the SQL Warehouse line above unless OPS_GENIE_WAREHOUSE_ID is set to a "
             "dedicated warehouse, in which case it shows on its own line."),
        ],
        "data_flow": {
            "mode": pipeline_mode,
            "path": (
                "Producer Job (1 Hz) → Zerobus → bronze Delta → Streaming QC "
                "(streaming_quality_sync, Spark Streaming ~1s trigger) → gold_history Delta + "
                "ai_serving.* (Lakebase Postgres) via foreachBatch → App SSE (1 Hz)"
                if zerobus_enabled
                else "Producer Job (1 Hz) → bronze Delta → Streaming QC "
                     "(streaming_quality_sync) → gold_history Delta + ai_serving.* "
                     "(Lakebase Postgres) → App SSE (1 Hz)"
            ),
            "latency_budget_seconds": {
                "producer_tick": 1.0,
                "zerobus_to_bronze": "1-2",
                "qc_gold_commit": "1-2",
                "syncer_to_lakebase": "1-2",
                "app_sse_refresh": 1.0,
                "end_to_end_typical": "3-6",
            },
            "freshness_note": (
                "The quality gate is a continuous Spark Structured Streaming job "
                "(streaming_quality_sync) with a ~1s trigger — it validates bronze rows, "
                "drops failures into quality_rejects, and writes gold_history Delta plus "
                "ai_serving.* Postgres in one foreachBatch. Its micro-batch commit cadence "
                "(~1-2s) is the bottleneck for value freshness; the UI refreshes every 1s "
                "(SSE_ALWAYS_PUSH=true) so the timestamp advances every poll while VALUES "
                "change at the commit cadence."
            ),
        },
        "note": "Live estimate from in-app counters + SDK-detected resource state. Compute lines are amortised at hourly rates while the resource is currently RUNNING. For windows >1h prefer the Billing tab (system.billing.usage) — it's authoritative but lags 1-4h.",
    }


_OPS_LAST_ACTIONS: dict[str, dict[str, Any]] = {}
_OPS_LAST_CALLS: dict[str, float] = {}
_OPS_COOLDOWN_S = int(os.environ.get("OPS_ACTION_COOLDOWN_SECONDS", "20"))
_OPS_PRODUCER_NAME = os.environ.get("OPS_PRODUCER_JOB_NAME", "line-manager-streaming-producer")
_OPS_SETUP_NAME = os.environ.get("OPS_SETUP_JOB_NAME", "line-manager-solution-accelerator-setup")
_OPS_PRODUCER_JOB_ID = os.environ.get("OPS_PRODUCER_JOB_ID", "").strip()
_OPS_SETUP_JOB_ID = os.environ.get("OPS_SETUP_JOB_ID", "").strip()
_OPS_LAKEBASE_SYNCER_JOB_ID = os.environ.get("OPS_LAKEBASE_SYNCER_JOB_ID", "").strip()
_OPS_LAKEBASE_SYNCER_NAME = os.environ.get("OPS_LAKEBASE_SYNCER_JOB_NAME", "line-manager-lakebase-syncer")


def _ops_now_iso() -> str:
    """UTC timestamp for ops audit-log entries."""
    return datetime.now(timezone.utc).isoformat()


def _ops_to_dict(obj: Any) -> dict[str, Any]:
    """Normalise SDK result objects (which can be dicts, dataclasses, or None) to dicts."""
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "as_dict"):
        return obj.as_dict()
    return {}


def _ops_user(req: Request) -> str:
    """Identify the caller for ops audit log (Databricks Apps forwards user via headers)."""
    return (
        req.headers.get("x-ops-user")
        or req.headers.get("x-forwarded-email")
        or req.headers.get("x-user-email")
        or os.environ.get("OPS_DEFAULT_USER")
        or "unknown"
    )


def _ops_role(user: str) -> str:
    admins = {x.strip().lower() for x in os.environ.get("OPS_ADMIN_EMAILS", "").split(",") if x.strip()}
    operators = {x.strip().lower() for x in os.environ.get("OPS_OPERATOR_EMAILS", "").split(",") if x.strip()}
    # Fall back to the least privilege, not the most. Every shipped app yaml sets
    # this to 'viewer'; the code used to disagree and default to 'operator', so
    # any deployment that lost the env var — a bare `uvicorn`, a container that
    # skipped the rendered yaml — promoted every unrecognised caller, including
    # the literal "unknown" that `_ops_user` returns when no identity header
    # arrives, to a role that can POST /ops/control/action. A default is what
    # runs when configuration is missing, which is exactly when it should assume
    # the least about who is calling.
    default_role = os.environ.get("OPS_DEFAULT_ROLE", "viewer").strip().lower() or "viewer"
    u = user.strip().lower()
    if u in admins:
        return "admin"
    if u in operators:
        return "operator"
    return default_role


def _ops_guard_cooldown(key: str) -> str | None:
    now = time.time()
    prev = _OPS_LAST_CALLS.get(key)
    if prev is not None and now - prev < _OPS_COOLDOWN_S:
        wait_s = int(max(1, _OPS_COOLDOWN_S - (now - prev)))
        return f"Cooldown active for {wait_s}s"
    _OPS_LAST_CALLS[key] = now
    return None


def _ops_record(target: str, action: str, user: str, status: str, message: str, result: dict[str, Any] | None = None) -> dict[str, Any]:
    entry = {
        "target": target,
        "action": action,
        "user": user,
        "status": status,
        "message": message,
        "timestamp": _ops_now_iso(),
        "result": result or {},
    }
    _OPS_LAST_ACTIONS[target] = entry
    return entry


def _ops_workspace():
    # why: pin to app SP so jobs / pipelines control uses the app's
    # CAN_MANAGE_RUN grants, not the browsing user's OBO identity.
    # See agent_server/tools/workspace_client.py.
    from agent_server.tools.workspace_client import app_workspace_client

    return app_workspace_client()


def _ops_handle_job(w, target: str, action: str, job_id: int, user: str):
    """Generic pause/resume/restart for a job — supports both continuous and
    schedule-driven configurations.

    Producer + Lakebase Syncer are continuous (`continuous.pause_status`),
    setup_job is one-shot. The legacy CronSchedule path is kept as a fallback.
    """
    job_detail = _ops_to_dict(w.jobs.get(job_id=job_id))
    settings = _ops_to_dict(job_detail.get("settings"))
    cont = _ops_to_dict(settings.get("continuous"))
    sched = _ops_to_dict(settings.get("schedule"))
    is_continuous = bool(cont)

    if action == "restart":
        w.jobs.cancel_all_runs(job_id=job_id)
        run = _ops_to_dict(w.jobs.run_now(job_id=job_id))
        return _ops_record(target, action, user, "ok", f"{target} restarted", {"job_id": job_id, "run": run})

    if action in {"pause", "resume"}:
        from databricks.sdk.service.jobs import Continuous, CronSchedule, JobSettings, PauseStatus

        new_status = PauseStatus.PAUSED if action == "pause" else PauseStatus.UNPAUSED
        new_status_str = "PAUSED" if action == "pause" else "UNPAUSED"
        if is_continuous:
            cur = str(cont.get("pause_status") or "").upper()
            if cur == new_status_str:
                return _ops_record(target, action, user, "noop", f"{target} already {action}d", {"job_id": job_id})
            w.jobs.update(
                job_id=job_id,
                new_settings=JobSettings(continuous=Continuous(pause_status=new_status)),
            )
            return _ops_record(target, action, user, "ok", f"{target} continuous {action}d", {"job_id": job_id})
        if sched and sched.get("quartz_cron_expression") and sched.get("timezone_id"):
            cur = str(sched.get("pause_status") or "").upper()
            if cur == new_status_str:
                return _ops_record(target, action, user, "noop", f"{target} schedule already {action}d", {"job_id": job_id})
            w.jobs.update(
                job_id=job_id,
                new_settings=JobSettings(
                    schedule=CronSchedule(
                        quartz_cron_expression=str(sched.get("quartz_cron_expression")),
                        timezone_id=str(sched.get("timezone_id")),
                        pause_status=new_status,
                    )
                ),
            )
            return _ops_record(target, action, user, "ok", f"{target} schedule {action}d", {"job_id": job_id})
        return JSONResponse(
            {"error": f"{target} has no continuous or schedule trigger to {action}"}, status_code=400
        )

    return JSONResponse({"error": f"unsupported {target} action: {action}"}, status_code=400)


def _ops_find_job_id(w, name: str) -> int | None:
    for j in w.jobs.list(name=name, limit=20):
        d = _ops_to_dict(j)
        n = (_ops_to_dict(d.get("settings")).get("name") or "")
        if n == name:
            jid = d.get("job_id")
            if jid is not None:
                return int(jid)
    # Fallback for workspaces where name filter behavior differs.
    for j in w.jobs.list(limit=50):
        d = _ops_to_dict(j)
        n = (_ops_to_dict(d.get("settings")).get("name") or "")
        if n == name:
            jid = d.get("job_id")
            if jid is not None:
                return int(jid)
    return None


def _ops_job_state(w, job_name: str, env_id: str = "") -> dict[str, Any]:
    try:
        detail = {}
        if env_id.isdigit():
            job_id = int(env_id)
            try:
                detail = _ops_to_dict(w.jobs.get(job_id=job_id))
            except Exception as e:
                return {
                    "name": job_name,
                    "job_id": job_id,
                    "exists": False,
                    "state": "permission-or-api-error",
                    "error": f"configured job_id {job_id} not accessible: {str(e)[:120]}",
                }
        else:
            job_id = _ops_find_job_id(w, job_name)
            if job_id:
                detail = _ops_to_dict(w.jobs.get(job_id=job_id))
        if not job_id:
            return {"name": job_name, "exists": False, "state": "not-visible-or-missing"}
        settings = _ops_to_dict(detail.get("settings"))
        sched = _ops_to_dict(settings.get("schedule"))
        pause_status = str(sched.get("pause_status") or "UNPAUSED")
        run_state = "idle"
        active_runs = 0
        for run in w.jobs.list_runs(job_id=job_id, active_only=True, limit=25):
            rd = _ops_to_dict(run)
            rs = _ops_to_dict(rd.get("state"))
            life = str(rs.get("life_cycle_state") or "")
            if life:
                active_runs += 1
                run_state = life.lower()
        return {
            "name": job_name,
            "job_id": job_id,
            "exists": True,
            "state": run_state,
            "pause_status": pause_status,
            "active_runs": active_runs,
        }
    except Exception as e:
        return {"name": job_name, "exists": False, "state": "permission-or-api-error", "error": str(e)[:160]}


_DASHBOARD_INFO_CACHE: dict[str, Any] = {}


def _resolve_data_quality_dashboard() -> dict[str, Any]:
    """Look up the Data Quality dashboard by display_name (or env override) and cache it.

    Resolution order:
      1. ``OPS_DATA_QUALITY_DASHBOARD_ID`` env (skips lookup, validates id exists)
      2. ``OPS_DATA_QUALITY_DASHBOARD_NAME`` env (defaults to ``"<app_name> — Data Quality"``)

    Returns dict with ``id``, ``display_name``, ``embed_url``, ``host``. Raises on failure.
    """
    if _DASHBOARD_INFO_CACHE.get("id"):
        return _DASHBOARD_INFO_CACHE

    host = (os.environ.get("DATABRICKS_HOST") or "").strip()
    if host and not host.startswith("http"):
        host = f"https://{host}"
    host = host.rstrip("/")

    app_name = (os.environ.get("DATABRICKS_APP_NAME") or "").strip()
    explicit_id = (os.environ.get("OPS_DATA_QUALITY_DASHBOARD_ID") or "").strip()
    # Treat unfilled placeholders as "unset" so a fresh deploy falls back to
    # name lookup instead of calling lakeview.get("SET_ME") and 404-ing. The
    # committed app.yaml ships OPS_DATA_QUALITY_DASHBOARD_ID='SET_ME'; without
    # this guard the truthy placeholder took the explicit-id path and the
    # working name-based fallback below was never reached.
    if explicit_id == "SET_ME" or (explicit_id.startswith("<") and explicit_id.endswith(">")):
        explicit_id = ""
    expected_name = (
        os.environ.get("OPS_DATA_QUALITY_DASHBOARD_NAME")
        or (f"{app_name} — Data Quality" if app_name else "Data Quality")
    ).strip()

    w = _ops_workspace()
    dashboard_id: str | None = None
    display_name: str | None = None

    if explicit_id:
        dashboard_id = explicit_id
        try:
            d = w.lakeview.get(dashboard_id=explicit_id)
            display_name = getattr(d, "display_name", None) or expected_name
        except Exception as e:
            raise RuntimeError(f"explicit OPS_DATA_QUALITY_DASHBOARD_ID not accessible: {e}")
    else:
        # Lakeview list pages; iterate up to a reasonable cap.
        seen = 0
        for d in w.lakeview.list():
            seen += 1
            if seen > 500:
                break
            name = getattr(d, "display_name", None)
            if name == expected_name:
                dashboard_id = getattr(d, "dashboard_id", None)
                display_name = name
                break

    if not dashboard_id:
        raise RuntimeError(
            f"Data Quality dashboard not found by name: {expected_name!r}. "
            "Deploy the bundle (databricks bundle deploy) or set OPS_DATA_QUALITY_DASHBOARD_ID."
        )

    # /embed/dashboardsv3/<id> only resolves for a PUBLISHED dashboard. A bundle
    # deploy creates the draft and stops there; publishing happens in
    # scripts/grant_app_jobs_view.sh, which only runs under scripts/deploy.sh.
    # Anyone who ran `databricks bundle deploy` directly therefore got a
    # dashboard that exists, resolves by name, and renders Databricks' bare
    # "The resource is not available" page inside the iframe — with nothing
    # anywhere to say why. Checking here turns that into an instruction.
    try:
        w.lakeview.get_published(dashboard_id=dashboard_id)
    except Exception as e:
        raise RuntimeError(
            f"The Data Quality dashboard ({dashboard_id}) exists but is not published, "
            "so it cannot be embedded. Publish it with embed_credentials so its "
            "queries run as the owner rather than as the iframe viewer:\n"
            f"  databricks api post /api/2.0/lakeview/dashboards/{dashboard_id}/published "
            '--json \'{"warehouse_id":"<sql-warehouse-id>","embed_credentials":true}\'\n'
            "Or re-run ./scripts/deploy.sh, which publishes and grants as part of the "
            f"deploy. ({type(e).__name__})"
        )

    embed_url = f"{host}/embed/dashboardsv3/{dashboard_id}" if host else None
    info = {
        "id": dashboard_id,
        "display_name": display_name or expected_name,
        "host": host,
        "embed_url": embed_url,
    }
    _DASHBOARD_INFO_CACHE.update(info)
    return info


@app.get("/ops/dashboard-info")
async def ops_dashboard_info():
    """Return the AI/BI Data Quality dashboard ID and embed URL for the iframe."""
    try:
        info = _resolve_data_quality_dashboard()
        return info
    except Exception as e:
        # Surface the hint so the UI can render a fix-it message.
        return JSONResponse({"error": str(e)[:400]}, status_code=404)


@app.get("/ops/models")
async def ops_models():
    """The model roster as resolved in THIS workspace.

    Served rather than hardcoded in the client because the answer is
    deployment-specific: a workspace that doesn't serve one of the six vendors
    silently runs that role on its fallback tier, and the About tab has to show
    what actually answers rather than what we wish answered.

    The first call may list serving endpoints, so it runs off the event loop;
    the result is cached in the roster module for the life of the process.
    """
    from agent_server.model_roster import multi_vendor_enabled, resolved_roster, vendor_summary

    def _read() -> dict:
        roster = resolved_roster()
        return {
            "multi_vendor": multi_vendor_enabled(),
            "vendors": vendor_summary(),
            "substituted": sum(1 for e in roster if e["substituted"]),
            "roles": roster,
        }

    try:
        return await asyncio.to_thread(_read)
    except Exception as e:  # noqa: BLE001 - an About tab must never 500 the app
        logger.exception("ops/models failed")
        return JSONResponse({"error": str(e)[:300]}, status_code=503)


# Which specialists own which governed domain. The System Map reads this rather
# than duplicating the mapping in the client, so the diagram cannot drift from
# the agents actually built in agent_server/specialists/.
_DOMAIN_OWNERS: dict[str, list[str]] = {
    "asset": ["maintenance_predictor", "downtime_analyst"],
    "quality": ["quality_specialist"],
    "inventory": ["supply_chain_advisor"],
    "production": ["oee_coach", "schedule_optimizer", "strategic_advisor"],
}

_DOMAIN_LABELS: dict[str, str] = {
    "asset": "Asset",
    "quality": "Quality",
    "inventory": "Inventory",
    "production": "Production",
}


def _streaming_space_available() -> bool:
    """Whether the streaming Genie space is configured AND still exists.

    Kept tolerant: the System Map must never take the app down, so a failure to
    import or check degrades to "configured", matching the old behaviour rather
    than raising inside a panel.
    """
    if not os.environ.get("GENIE_SPACE_ID", "").strip():
        return False
    try:
        from agent_server.tools.genie_query import space_resolves
        return space_resolves()
    except Exception:  # noqa: BLE001
        logger.debug("Genie space resolution check failed", exc_info=True)
        return True


@app.get("/ops/topology")
async def ops_topology():
    """How agents reach data: the governed domains and which are provisioned.

    Served rather than hardcoded because provisioning is deployment-specific. A
    workspace where a domain's Genie space failed to create still runs — those
    specialists fall back to sql_query — and the System Map has to show that
    honestly instead of drawing a connection that does not exist.
    """
    from agent_server.tools.governed_domain_query import (
        DOMAIN_ENV,
        domain_space_id,
        domain_tables,
    )

    def _read() -> dict:
        domains = []
        for domain in DOMAIN_ENV:
            configured = bool(domain_space_id(domain))
            domains.append(
                {
                    "domain": domain,
                    "label": _DOMAIN_LABELS.get(domain, domain.title()),
                    "configured": configured,
                    "tool": f"governed_{domain}_query" if configured else None,
                    "entities": domain_tables(domain),
                    "specialists": _DOMAIN_OWNERS.get(domain, []),
                }
            )
        configured_count = sum(1 for d in domains if d["configured"])
        return {
            "domains": domains,
            "configured": configured_count,
            "total": len(domains),
            # The streaming space is the live, per-second surface — a different
            # audience from the governed domains, so it is reported separately.
            # Checking that the id resolves, not just that it is set: a trashed
            # space kept this true while every genie_query against it failed.
            "streaming_space": _streaming_space_available(),
            "mvm_silver_enabled": os.environ.get("MVM_SILVER_ENABLED", "") == "1",
        }

    try:
        return await asyncio.to_thread(_read)
    except Exception as e:  # noqa: BLE001 - the System Map must never 500 the app
        logger.exception("ops/topology failed")
        return JSONResponse({"error": str(e)[:300]}, status_code=503)


def _reconciliation_uc_query(sql: str) -> list[dict]:
    """Run a read-only UC query, converting the tool's error shape into a raise.

    ``sql_query`` reports failures as ``[{"error": ...}]`` rather than raising;
    the reconciler relies on exceptions to degrade one source at a time.
    """
    from agent_server.tools.sql_query import sql_query as _sql

    rows = _sql.invoke(sql)
    if rows and isinstance(rows[0], dict) and "error" in rows[0] and len(rows[0]) == 1:
        raise RuntimeError(str(rows[0]["error"])[:200])
    return rows or []


def _make_reconciliation_pg_query():
    """Build a pg_query bound to a dedicated, short-lived read-only connection.

    Deliberately NOT the shared serving connection: these are slow scans, and
    psycopg2 serializes per connection, so reusing it would stall live SSE reads
    and any failure would have to roll back another handler's transaction.
    """
    from agent_server.memory.pg_pool import new_readonly_connection

    conn = new_readonly_connection()
    if conn is None:
        raise RuntimeError("Lakebase not configured")

    def _query(sql: str) -> list[dict]:
        with conn.cursor() as cur:
            cur.execute(sql)
            return [dict(r) for r in cur.fetchall()]

    return conn, _query


# Reconciliation fans out to several warehouse queries, so it is cached well
# below the settled-window length: repeated Reloads and tab switches are free,
# while the data can never be more stale than the window it describes.
_RECONCILIATION_CACHE: dict[str, Any] = {}
_RECONCILIATION_TTL_S = 60.0
_RECONCILIATION_LOCK = threading.Lock()


def _run_reconciliation(catalog: str, schema: str, mvm_catalog: str,
                        governed_owned: bool,
                        window_minutes: int, lag_guard_seconds: int) -> dict:
    """Blocking reconciliation with a TTL cache. Runs on a worker thread."""
    key = (
        f"{catalog}.{schema}|{mvm_catalog}|{governed_owned}"
        f"|{window_minutes}|{lag_guard_seconds}"
    )
    now = time.time()
    with _RECONCILIATION_LOCK:
        hit = _RECONCILIATION_CACHE.get(key)
        if hit and (now - hit["at"]) < _RECONCILIATION_TTL_S:
            return {**hit["payload"], "cached": True}

    conn = None
    try:
        conn, pg_query = _make_reconciliation_pg_query()
    except Exception:
        # Lakebase unreachable: still return the analytics side, marked unknown.
        def pg_query(_sql: str) -> list[dict]:
            raise RuntimeError("Lakebase not configured")

    try:
        payload = reconciliation.reconcile(
            uc_query=_reconciliation_uc_query,
            pg_query=pg_query,
            catalog=catalog,
            schema=schema,
            mvm_catalog=mvm_catalog,
            governed_owned=governed_owned,
            # Dev and prod share one Lakebase instance and are separated only by
            # shift, so an unscoped comparison counts both environments.
            shift_id=os.environ.get("SIM_SHIFT_ID") or None,
            window_minutes=window_minutes,
            lag_guard_seconds=lag_guard_seconds,
        )
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    with _RECONCILIATION_LOCK:
        _RECONCILIATION_CACHE[key] = {"at": now, "payload": payload}
    return {**payload, "cached": False}


@app.get("/ops/reconciliation")
async def ops_reconciliation(
    window_minutes: int = reconciliation.DEFAULT_WINDOW_MINUTES,
    lag_guard_seconds: int = reconciliation.DEFAULT_LAG_GUARD_SECONDS,
):
    """Compare the live (Lakebase) plane against the analytics/governed plane.

    Runs the comparison over a *settled* window so the Zerobus SLA plus one
    streaming cycle can't masquerade as data loss. Each source degrades
    independently, so a Lakebase outage still returns the analytics counts.
    """
    catalog = os.environ.get("UC_CATALOG", "")
    schema = os.environ.get("UC_SCHEMA", "")
    if not catalog or not schema:
        return JSONResponse(
            {"error": "UC_CATALOG / UC_SCHEMA not configured"}, status_code=503
        )
    # An unset MVM catalog means the model was installed into the live catalog
    # (the common case when the metastore denies CREATE CATALOG).
    mvm_catalog = os.environ.get("MVM_CATALOG") or catalog
    # The governed tables carry no environment suffix, so a catalog shared with
    # another environment exposes ITS rows to us. Only claim them when this
    # environment's streaming-silver writer is the one filling them.
    governed_owned = os.environ.get("MVM_SILVER_ENABLED", "0") == "1"
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(
                _run_reconciliation,
                catalog,
                schema,
                mvm_catalog,
                governed_owned,
                max(1, min(int(window_minutes), 24 * 60)),
                max(0, min(int(lag_guard_seconds), 3600)),
            ),
            # Bounded so a cold or wedged warehouse returns a clear error to the
            # UI instead of hanging until the proxy gives up.
            timeout=90,
        )
    except asyncio.TimeoutError:
        return JSONResponse(
            {"error": "reconciliation timed out — the SQL warehouse may be starting"},
            status_code=504,
        )
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)[:400]}, status_code=500)


@app.get("/ops/control/state")
async def ops_control_state(req: Request):
    user = _ops_user(req)
    role = _ops_role(user)
    try:
        w = _ops_workspace()
        producer = _ops_job_state(w, _OPS_PRODUCER_NAME, env_id=_OPS_PRODUCER_JOB_ID)
        setup_job = _ops_job_state(w, _OPS_SETUP_NAME, env_id=_OPS_SETUP_JOB_ID)
        syncer = _ops_job_state(w, _OPS_LAKEBASE_SYNCER_NAME, env_id=_OPS_LAKEBASE_SYNCER_JOB_ID)
        return {
            "user": user,
            "role": role,
            "can_control": role in {"admin", "operator"},
            "cooldown_seconds": _OPS_COOLDOWN_S,
            "resources": {
                "producer": producer,
                "setup_job": setup_job,
                "lakebase_syncer": syncer,
            },
            "last_actions": _OPS_LAST_ACTIONS,
            "generated_at": _ops_now_iso(),
        }
    except Exception as e:
        return JSONResponse({"error": f"control state failed: {str(e)[:200]}"}, status_code=500)


@app.post("/ops/control/action")
async def ops_control_action(body: OpsControlActionBody, req: Request):
    user = _ops_user(req)
    role = _ops_role(user)
    if role not in {"admin", "operator"}:
        return JSONResponse({"error": f"role '{role}' not allowed for control actions"}, status_code=403)
    if not body.confirm:
        return JSONResponse({"error": "confirmation required"}, status_code=400)

    target = body.target.strip().lower()
    action = body.action.strip().lower()
    cooldown_key = f"{target}:{action}"
    cooldown_err = _ops_guard_cooldown(cooldown_key)
    if cooldown_err:
        return JSONResponse({"error": cooldown_err}, status_code=429)

    try:
        w = _ops_workspace()
        if target == "producer":
            if _OPS_PRODUCER_JOB_ID.isdigit():
                job_id = int(_OPS_PRODUCER_JOB_ID)
                try:
                    w.jobs.get(job_id=job_id)
                except Exception:
                    job_id = None
            else:
                job_id = _ops_find_job_id(w, _OPS_PRODUCER_NAME)
            if not job_id:
                return JSONResponse(
                    {
                        "error": "producer job not found or not visible to app identity",
                        "hint": "Grant app service principal CAN_VIEW/CAN_MANAGE on the producer job, or set OPS_PRODUCER_JOB_ID",
                    },
                    status_code=404,
                )
            return _ops_handle_job(w, "producer", action, job_id, user)

        if target == "lakebase_syncer":
            if _OPS_LAKEBASE_SYNCER_JOB_ID.isdigit():
                job_id = int(_OPS_LAKEBASE_SYNCER_JOB_ID)
                try:
                    w.jobs.get(job_id=job_id)
                except Exception:
                    job_id = None
            else:
                job_id = _ops_find_job_id(w, _OPS_LAKEBASE_SYNCER_NAME)
            if not job_id:
                return JSONResponse(
                    {
                        "error": "lakebase syncer job not found or not visible to app identity",
                        "hint": "Grant app SP CAN_VIEW/CAN_MANAGE on the lakebase syncer job, or set OPS_LAKEBASE_SYNCER_JOB_ID",
                    },
                    status_code=404,
                )
            return _ops_handle_job(w, "lakebase_syncer", action, job_id, user)

        if target == "setup_job":
            if _OPS_SETUP_JOB_ID.isdigit():
                job_id = int(_OPS_SETUP_JOB_ID)
                try:
                    w.jobs.get(job_id=job_id)
                except Exception:
                    job_id = None
            else:
                job_id = _ops_find_job_id(w, _OPS_SETUP_NAME)
            if not job_id:
                return JSONResponse(
                    {
                        "error": "setup job not found or not visible to app identity",
                        "hint": "Grant app service principal CAN_VIEW/CAN_MANAGE on setup job, or set OPS_SETUP_JOB_ID",
                    },
                    status_code=404,
                )
            if action != "run_now":
                return JSONResponse({"error": f"unsupported setup_job action: {action}"}, status_code=400)
            run = _ops_to_dict(w.jobs.run_now(job_id=job_id))
            return _ops_record("setup_job", action, user, "ok", "Setup job run triggered", {"job_id": job_id, "run": run})

        return JSONResponse({"error": f"unknown target: {target}"}, status_code=400)
    except Exception as e:
        msg = f"{target}:{action} failed: {str(e)[:200]}"
        _ops_record(target, action, user, "error", msg)
        return JSONResponse({"error": msg}, status_code=500)


@app.post("/invocations")
async def invocations(body: InvocationBody):
    query = body.query

    if not query and body.input:
        messages = body.input
        if isinstance(messages, list) and messages:
            last_msg = messages[-1]
            if isinstance(last_msg, dict):
                query = last_msg.get("content", "")
            elif isinstance(last_msg, str):
                query = last_msg
        elif isinstance(messages, str):
            query = messages

    if not query:
        return JSONResponse({"error": "No query provided"}, status_code=400)

    try:
        from agent_server.agent import invoke
        # Same env-override as /sim/incident: the client hard-codes
        # ``shift_id='shift_1'`` but dev runs ``shift_1_dev``. Without
        # this, the chat agent would query UC tables with the wrong
        # shift filter and surface prod data inside the dev app.
        result = invoke(
            query=query,
            shift_id=_server_shift_id(body.shift_id),
            timestamp=body.timestamp,
            machine_id=body.machine_id,
            conversation_id=body.conversation_id,
        )
        _live_record("agent_invocations")
        _live_record("llm_calls")
        usage = (result or {}).get("usage") if isinstance(result, dict) else None
        if isinstance(usage, dict) and (usage.get("input_tokens") or usage.get("prompt_tokens")):
            _live_record("llm_input_tokens", float(usage.get("input_tokens") or usage.get("prompt_tokens") or 0))
            _live_record("llm_output_tokens", float(usage.get("output_tokens") or usage.get("completion_tokens") or 0))
        else:
            _live_record("llm_input_tokens", float(_estimate_tokens(query)))
            content = (result or {}).get("content", "") if isinstance(result, dict) else ""
            _live_record("llm_output_tokens", float(_estimate_tokens(content)))
        return JSONResponse(result)
    except Exception as e:
        logger.exception("Agent invocation failed")
        return JSONResponse({"error": str(e), "content": f"Sorry, I encountered an error: {e}"}, status_code=500)


@app.post("/invocations/stream")
async def invocations_stream(body: InvocationBody):
    """SSE streaming endpoint — sends snapshot immediately, then full analysis."""
    from starlette.responses import StreamingResponse

    query = body.query
    if not query and body.input:
        messages = body.input
        if isinstance(messages, list) and messages:
            last_msg = messages[-1]
            if isinstance(last_msg, dict):
                query = last_msg.get("content", "")
            elif isinstance(last_msg, str):
                query = last_msg
        elif isinstance(messages, str):
            query = messages

    if not query:
        return JSONResponse({"error": "No query provided"}, status_code=400)

    def _generate():
        try:
            from agent_server.agent import invoke_stream
            _live_record("agent_invocations")
            _live_record("llm_calls")
            _live_record("llm_input_tokens", float(_estimate_tokens(query)))
            output_chars = 0
            for chunk in invoke_stream(
                query=query,
                shift_id=_server_shift_id(body.shift_id),
                timestamp=body.timestamp,
                machine_id=body.machine_id,
                conversation_id=body.conversation_id,
            ):
                try:
                    # Two things must not reach the token estimate. Keep-alive
                    # comments are transport, not model output — counting them
                    # billed roughly a token per second of thinking time. And
                    # `token` frames are a preview of text that arrives again,
                    # in full, on the `answer` frame; counting both bills every
                    # streamed answer twice.
                    if (
                        isinstance(chunk, str)
                        and not chunk.startswith(":")
                        and '"type": "token' not in chunk
                    ):
                        output_chars += len(chunk)
                except Exception:
                    pass
                yield chunk
            if output_chars:
                _live_record("llm_output_tokens", float(max(1, int(output_chars / 4))))
        except Exception as e:
            logger.exception("Streaming agent invocation failed")
            import json as _json
            yield f"data: {_json.dumps({'type': 'error', 'content': str(e)})}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Incident management endpoints ──────────────────────────────────────────

@app.get("/sim/status")
async def sim_status():
    """Diagnostic: live data-plane state read from Lakebase ``ai_serving.*``.

    The in-process SimEngine was retired — live state now flows Producer →
    Zerobus → bronze → streaming sync → Lakebase → App. This reports what the
    App currently sees for its shift, so an operator can confirm the producer +
    streaming sync are healthy without opening the dashboard.
    """
    def _read() -> dict:
        from agent_server.serving.realtime_store import (
            read_commands_active,
            read_line_kpi_latest,
            read_machine_state_latest,
        )
        shift_id = os.environ.get("SIM_SHIFT_ID", "shift_1")
        return {
            "mode": "producer",
            "shift_id": shift_id,
            "machines_count": len(read_machine_state_latest(shift_id)),
            "active_incidents": len(read_commands_active(shift_id)),
            "kpi_raw": read_line_kpi_latest(shift_id) or {},
        }

    try:
        # Off the event loop: these are blocking psycopg2 calls, and when
        # Lakebase is unreachable they wait out a connect timeout — on the
        # loop that would freeze every other request, SSE streams included.
        # jsonable_encoder handles datetime/Decimal values that Lakebase rows
        # carry (raw dict/JSONResponse would raise "not JSON serializable").
        return JSONResponse(jsonable_encoder(await asyncio.to_thread(_read)))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


def _truncate_ai_serving_demo_state(shift_id: str) -> dict:
    """Wipe transient demo state from Lakebase so a restart looks like a
    fresh shift — but only for the SHIFT being restarted.

    With the playback-cursor architecture we MUST also clear the
    ``*_history`` rows for this shift, otherwise the cursor (initialised at
    ``MAX(ts) - buffer_seconds``) would replay the previous shift's data
    until the buffer drains. After clearing, the next SSE poll initialises
    from an empty history → emits ``status: "waiting"`` until the streaming
    sync job lands the first batch of new data (~10–20 s).

    KPI / machine_state ``*_latest`` tables are overwritten by the next
    streaming-sync upsert (cheap, 36 rows) so we leave them as-is — they
    are NOT used as a fallback by the SSE generator anymore (the previous
    cold-start fallback was removed per the "no fallbacks" directive); they
    just stay as a current-state side-output for ad-hoc queries.

    Returns per-table delete counts for telemetry. Best-effort: any error
    is logged in the response so the restart still succeeds.
    """
    out: dict[str, Any] = {"shift_id": shift_id}
    try:
        from agent_server.memory.pg_pool import transaction

        with transaction() as cur:
            if cur is None:
                return {"warning": "lakebase not available; demo state not cleared"}
            # Tables ordered the same as the SQL schema for predictability;
            # all DELETEs are scoped to ``shift_id`` so a multi-shift demo
            # only nukes the row(s) for the shift being restarted.
            tables = (
                "ai_serving.event_log_recent",
                "ai_serving.scenario_commands",
                # Playback-cursor source tables. Removing the rows for this
                # shift forces the cursor to re-init from an empty MAX(ts).
                "ai_serving.machine_state_history",
                "ai_serving.line_kpi_history",
                "ai_serving.inventory_status_history",
                "ai_serving.event_log_history",
            )
            total_deleted = 0
            tables_ok = 0
            for tbl in tables:
                # Savepoint per table so this stays genuinely best-effort. A
                # failed DELETE aborts the whole transaction in Postgres, so
                # without one the remaining tables would all fail and the
                # commit would silently discard the deletes we just reported
                # as successful.
                cur.execute("SAVEPOINT clear_table")
                try:
                    cur.execute(f"DELETE FROM {tbl} WHERE shift_id = %s", (shift_id,))
                    out[tbl] = f"deleted={cur.rowcount}"
                    total_deleted += max(0, int(cur.rowcount or 0))
                    tables_ok += 1
                    cur.execute("RELEASE SAVEPOINT clear_table")
                except Exception as e:  # noqa: BLE001
                    out[tbl] = f"error:{str(e)[:80]}"
                    cur.execute("ROLLBACK TO SAVEPOINT clear_table")
            out["tables_ok"] = tables_ok
            out["tables_total"] = len(tables)
            out["rows_deleted"] = total_deleted
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:200]
    return out


def _abort_pending_uc_commands() -> dict:
    """Wind down every non-terminal row in ``ai_serving.scenario_commands``.

    The function name is kept for backwards compatibility with callers and
    the existing API surface, even though the table is now in Lakebase, not
    Unity Catalog. See the migration note in
    ``agent_server/sim/commands.py`` for why we moved.

    Two-step so the still-running producer cleanly self-resolves any
    in-memory ``active_incidents`` entries BEFORE we kill its run, even
    if the run-now restart that follows fails or is queued behind a
    cluster spin-up:

      * ``active`` / ``resolving`` → ``resolve_requested``. The producer
        polls this status every tick; when it sees the marker it calls
        ``resolve_incident`` on its own state which clears the fault
        out of ``active_incidents``, restocks materials if needed, and
        emits an ``incident_resolved`` event. The row then transitions
        to ``resolved`` via the normal flow.
      * ``pending`` / stale ``resolve_requested`` (older queue) → ``aborted``.
        These never reached the producer's apply path, so there's
        nothing to resolve — terminal-out so the restart doesn't pick
        them up.

    Returns counts so the operator can tell which path each row took.
    Best-effort: any error is logged in the dict instead of raised.
    """
    try:
        from agent_server.memory.pg_pool import transaction

        out: dict[str, Any] = {}
        with transaction() as cur:
            if cur is None:
                return {"error": "Lakebase unavailable"}

            # Step 1: ask the still-running producer to resolve its active
            # incidents. The producer's ``fetch_resolve_requested`` poll runs
            # every tick (1 s) so this completes within a second or two of
            # the commit.
            cur.execute(
                "UPDATE ai_serving.scenario_commands "
                "SET status = 'resolve_requested' "
                "WHERE status IN ('active', 'resolving')"
            )
            resolve_requested_n = int(cur.rowcount or 0)
            out["resolve_requested"] = resolve_requested_n

            # Step 2: terminal-out anything still queued and never
            # delivered, plus stale resolve_requested rows older than
            # 60 s (the producer must have died before processing them).
            cur.execute(
                "UPDATE ai_serving.scenario_commands "
                "SET status = 'aborted' "
                "WHERE status = 'pending' "
                "   OR (status = 'resolve_requested' "
                "       AND created_at < now() - INTERVAL '60 seconds')"
            )
            aborted_n = int(cur.rowcount or 0)
            out["aborted"] = aborted_n
            out["total"] = resolve_requested_n + aborted_n
        return out
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:200]}


def _restart_producer_job_best_effort(*, start_fresh: bool = False) -> dict:
    """Cancel-active + run-now the producer job so a fresh shift starts.

    Uses ``cancel_run`` per active run (fire-and-forget) instead of
    ``cancel_all_runs`` which blocks until every run reaches a terminal
    state — for a continuous job that's 60-120 s and would always trip the
    HTTP proxy timeout.

    ``start_fresh=True`` tries to append ``--start-fresh`` to the
    bundle-defined parameters via ``python_params``. The continuous
    trigger sometimes wins the race and spawns a replacement run with
    bundle defaults BEFORE our ``run_now`` lands, in which case the flag
    silently doesn't reach the producer. That used to be fatal because
    rehydration read from ``inventory_status_bronze`` (which Clean
    Restart cannot wipe) and the new run resumed from the previous
    shift's depleted state.

    Mitigation lives in the producer: rehydration was switched to read
    from ``ai_serving.*_history`` (Lakebase), which Clean Restart wipes
    via ``_truncate_ai_serving_demo_state`` BEFORE this function fires.
    Empty Lakebase ⇒ empty rehydration ⇒ ``init_inventory()`` / 0
    cumulative cases, regardless of whether ``--start-fresh`` actually
    landed. See ``src/streaming_sim/lakebase_sink.py`` (read_latest_*)
    and ``src/streaming_sim/producer.py`` (rehydration blocks). The
    ``python_params`` override is therefore best-effort defence in depth.
    """
    job_id_str = (os.environ.get("OPS_PRODUCER_JOB_ID") or "").strip()
    if not job_id_str.isdigit():
        return {"skipped": "OPS_PRODUCER_JOB_ID not set"}
    try:
        # why: pin to app SP for jobs API — see agent_server/tools/workspace_client.py.
        from agent_server.tools.workspace_client import app_workspace_client
        w = app_workspace_client()
        job_id = int(job_id_str)
        cancelled: list[int] = []
        for r in w.jobs.list_runs(job_id=job_id, active_only=True, limit=10):
            try:
                w.jobs.cancel_run(run_id=r.run_id)
                cancelled.append(int(r.run_id))
            except Exception as ce:  # noqa: BLE001
                logger.warning("cancel_run(run_id=%s) failed: %s", r.run_id, str(ce)[:120])

        # Build python_params override. ``run_now(python_params=…)`` REPLACES
        # the bundle-defined parameters for this run (it doesn't append),
        # so we fetch the current task definition first and concatenate.
        # Falls back to firing without an override if anything fails — the
        # restart still succeeds, just without the start-fresh signal.
        run_kwargs: dict[str, Any] = {"job_id": job_id}
        if start_fresh:
            try:
                job_def = w.jobs.get(job_id=job_id)
                base_params: list[str] = []
                for task in (job_def.settings.tasks or []):
                    spt = getattr(task, "spark_python_task", None)
                    params = getattr(spt, "parameters", None) if spt else None
                    if params:
                        base_params = list(params)
                        break
                run_kwargs["python_params"] = base_params + ["--start-fresh"]
            except Exception as e:  # noqa: BLE001
                logger.warning("get_job(params) failed for start-fresh override: %s", str(e)[:120])

        try:
            run = w.jobs.run_now(**run_kwargs)
            new_run_id = getattr(run, "run_id", None) or getattr(run, "number_in_job", None)
        except Exception as e:  # noqa: BLE001
            # Continuous trigger may queue the new run automatically once the
            # cancelled one terminates; surface a clear note instead of a 500.
            return {"job_id": job_id, "cancelled_runs": cancelled,
                    "warning": f"run_now skipped (continuous trigger will pick up): {str(e)[:120]}"}
        return {
            "job_id": job_id,
            "cancelled_runs": cancelled,
            "new_run_id": new_run_id,
            "start_fresh": bool(start_fresh and "python_params" in run_kwargs),
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("Producer job restart failed: %s", str(e)[:200])
        return {"error": str(e)[:200]}


@app.post("/sim/restart")
async def sim_restart():
    """Restart the demo.

    Clears transient demo state from ``ai_serving.*`` and restarts the producer
    job so a fresh shift begins. There is no in-process engine to reset — the
    producer is the sole writer of live state (Producer → Lakebase → App).
    """
    response: dict[str, Any] = {"status": "restarted", "mode": "producer"}
    # Restart is currently scoped to the env-default shift. If we ever
    # add a query parameter, thread it through here.
    shift_id = os.environ.get("SIM_SHIFT_ID", "shift_1")

    # Order matters:
    #   1. Mark active UC commands as resolve_requested. The currently
    #      running producer polls every tick, sees the marker, and
    #      clears its in-memory ``active_incidents`` dict + emits
    #      ``incident_resolved`` events. This is the only mechanism
    #      that gets faults out of an existing producer's RAM —
    #      without it, even a successful run-now restart still
    #      leaves a 30-60 s window where the dying old run keeps
    #      writing FILLER=stopped to bronze (the symptom we just
    #      hit in dev).
    response["uc_commands_clear"] = await asyncio.to_thread(_abort_pending_uc_commands)

    # 2. Brief pause so the producer's 1 s poll loop actually picks
    #    up the resolve_requested marker before we wipe its data.
    #    2 s = 1 tick + safety margin; longer would just delay UX.
    await asyncio.sleep(2.0)

    # 3. Wipe Lakebase ``ai_serving.*_history`` for this shift so
    #    the SSE cursor restarts from an empty playback window.
    response["lakebase_clear"] = await asyncio.to_thread(_truncate_ai_serving_demo_state, shift_id)

    # 4. Restart the producer with ``--start-fresh`` so even if the
    #    in-memory resolve in step 1 was racy, the brand-new
    #    process boots with a guaranteed-empty state, zero
    #    cumulative cases, and full inventory.
    response["producer_restart"] = await asyncio.to_thread(
        _restart_producer_job_best_effort, start_fresh=True,
    )
    return JSONResponse(response)


def _toggle_producer_job_pause(*, pause: bool) -> dict[str, Any]:
    """Pause/unpause the continuous producer job.

    The producer is the sole writer of live state, so the honest way to stop
    the dashboard is to pause the producer JOB itself, which freezes data at
    the source. The streaming sync stays up so it can resume immediately when
    we unpause; no checkpoint loss.
    """
    job_id_str = (os.environ.get("OPS_PRODUCER_JOB_ID") or "").strip()
    if not job_id_str.isdigit():
        return {"skipped": "OPS_PRODUCER_JOB_ID not set"}
    try:
        # why: pin to app SP for jobs API — see agent_server/tools/workspace_client.py.
        from databricks.sdk.service.jobs import Continuous, JobSettings, PauseStatus

        from agent_server.tools.workspace_client import app_workspace_client
        w = app_workspace_client()
        job_id = int(job_id_str)
        new_status = PauseStatus.PAUSED if pause else PauseStatus.UNPAUSED
        w.jobs.update(
            job_id=job_id,
            new_settings=JobSettings(continuous=Continuous(pause_status=new_status)),
        )
        return {"job_id": job_id, "pause_status": new_status.value}
    except Exception as e:  # noqa: BLE001
        logger.warning("Producer job pause toggle failed: %s", str(e)[:200])
        return {"error": str(e)[:200]}


@app.post("/sim/stop")
async def sim_stop():
    """Stop the simulation by pausing the continuous producer JOB.

    Pausing the producer freezes new bronze rows at the source, which is the
    only honest way to stop the dashboard — live data flows from the external
    producer, not from the app process.
    """
    try:
        response: dict[str, Any] = {"status": "stopped", "mode": "producer"}
        response["producer_pause"] = await asyncio.to_thread(_toggle_producer_job_pause, pause=True)
        return JSONResponse(response)
    except Exception as e:
        logger.exception("Failed to stop sim")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/sim/resume")
async def sim_resume():
    """Resume the simulation by unpausing the continuous producer JOB.

    Symmetrical to ``/sim/stop`` — unpausing the producer resumes new bronze
    rows (the streaming sync stayed up, so there's no checkpoint loss).
    """
    try:
        response: dict[str, Any] = {"status": "resumed", "mode": "producer"}
        response["producer_unpause"] = await asyncio.to_thread(_toggle_producer_job_pause, pause=False)
        return JSONResponse(response)
    except Exception as e:
        logger.exception("Failed to resume sim")
        return JSONResponse({"error": str(e)}, status_code=500)


def _server_shift_id(client_shift_id: str | None) -> str:
    """Return the shift_id the dashboard data plane expects.

    The browser hard-codes ``shift_id='live'`` in /api/sim/incident bodies
    (legacy from when the demo ran a single shift). The server's data
    plane — producer + streaming-quality-sync + ai_serving.* + SSE — is
    keyed by ``SIM_SHIFT_ID`` env (``shift_1`` in prod, ``shift_1_dev``
    in dev, per the dev/prod separation). Mismatched ids meant new
    ``scenario_commands`` rows were filtered out by
    ``read_commands_active(shift_id)`` on the SSE side, so the Incident
    panel stayed empty even though the producer happily applied the
    fault and the schematic turned red — exactly the symptom the
    operator reported.

    Strategy: prefer the env-derived shift_id, fall back to whatever the
    client sent. We ignore the client value rather than try to
    reconcile because the client has no way to know the env-specific
    id without an extra round-trip; the server always does.
    """
    env_sid = (os.environ.get("SIM_SHIFT_ID") or "").strip()
    if env_sid:
        return env_sid
    return (client_shift_id or "shift_1").strip() or "shift_1"


def _persist_scenario_command_safe(cmd) -> None:
    """Background-task wrapper around persist_scenario_command.

    Logs failures with full traceback so they're visible in the app log /
    Cost & Obs audit feed. The /sim/commands endpoint reads recent rows
    from Lakebase, so successful writes show up there as the producer-visible
    audit trail.
    """
    try:
        from agent_server.sim.commands import persist_scenario_command
        persist_scenario_command(cmd)
    except Exception:
        logger.exception(
            "Background INSERT into scenario_commands failed; "
            "in-app engine recorded the trigger but producer will not see it. "
            "command_id=%s scenario_id=%s shift_id=%s",
            getattr(cmd, "command_id", None),
            getattr(cmd, "scenario_id", None),
            getattr(cmd, "shift_id", None),
        )


@app.post("/sim/warmup")
async def sim_warmup(req: Request):
    """Pre-demo cold-start primer — side-effect-free.

    A cold demo pays warehouse cold-start (5–30 s) on the first /sim/incident,
    plus Lakebase OAuth refresh and connection-cache rebuild. This endpoint
    forces those costs to be paid up front by issuing read-only probes
    against every dependency the trigger flow will hit. No rows are
    written and no fault is injected.

    Steps:
      * sql_warehouse        — SELECT 1 against the SQL warehouse
      * scenario_commands    — read 1 row of ``ai_serving.scenario_commands``
                               from Lakebase (same path the producer polls)
      * lakebase_serving     — read ai_serving.machine_state_latest (warms
                               psycopg2 + OAuth token)

    Returns per-step ``ok``/``ms``/``error`` so the caller can tell which
    layer was actually cold. ``total_ms`` is the sum of step timings.
    Authorisation: same admin/operator gate as /ops/control/action — the
    button only makes sense from the /admin page anyway.
    """
    user = _ops_user(req)
    role = _ops_role(user)
    if role not in {"admin", "operator"}:
        return JSONResponse(
            {"error": f"role '{role}' lacks warmup permission"},
            status_code=403,
        )

    import time

    steps: list[dict[str, Any]] = []

    def _step(name: str, fn) -> None:
        t0 = time.monotonic()
        try:
            fn()
            steps.append({"name": name, "ok": True, "ms": int((time.monotonic() - t0) * 1000)})
        except Exception as e:  # noqa: BLE001
            steps.append(
                {
                    "name": name,
                    "ok": False,
                    "ms": int((time.monotonic() - t0) * 1000),
                    "error": str(e)[:200],
                }
            )

    def _sql_probe() -> None:
        from agent_server.tools.sql_query import sql_query as _sql
        result = _sql.invoke("SELECT 1 AS probe")
        if not result or not isinstance(result[0], dict) or result[0].get("probe") != 1:
            raise RuntimeError(f"unexpected: {str(result)[:160]}")

    def _scenario_commands_probe() -> None:
        from agent_server.sim.commands import list_recent_commands
        list_recent_commands(limit=1)

    def _lakebase_probe() -> None:
        if not os.environ.get("PGHOST"):
            return
        from agent_server.serving.realtime_store import read_machine_state_latest
        read_machine_state_latest(os.environ.get("SIM_SHIFT_ID", "shift_1"))

    def _run_probes() -> None:
        _step("sql_warehouse", _sql_probe)
        _step("scenario_commands", _scenario_commands_probe)
        _step("lakebase_serving", _lakebase_probe)

    # Warmup is deliberately slow — it exists to pay the cold-start cost — so
    # it is the last thing that should pay it on the event loop.
    await asyncio.to_thread(_run_probes)

    total_ms = sum(int(s.get("ms") or 0) for s in steps)
    all_ok = all(bool(s.get("ok")) for s in steps)
    logger.info("sim_warmup user=%s role=%s ok=%s total_ms=%s steps=%s",
                user, role, all_ok, total_ms, steps)
    return {"ok": all_ok, "user": user, "role": role, "total_ms": total_ms, "steps": steps}


@app.post("/sim/incident")
async def trigger_incident(body: IncidentTriggerBody, background_tasks: BackgroundTasks):
    """Trigger an incident.

    Writes to Lakebase ``ai_serving.scenario_commands`` so the external
    producer job picks it up and emits the resulting fault. This is the sole
    path — the producer is the only writer of live state.

    Latency: the INSERT is dispatched as a FastAPI BackgroundTask and runs
    after the response is sent. The producer polls ~every 1s, so making the
    user wait on an INSERT against a (potentially cold) Lakebase/warehouse
    would add 1–30s of UI lag with zero data-plane benefit.

    ``shift_id`` is overridden server-side from ``SIM_SHIFT_ID`` so the row
    we write matches what the SSE reader filters on (see ``_server_shift_id``).
    """
    shift_id = _server_shift_id(body.shift_id)
    try:
        from agent_server.sim.commands import build_scenario_command
        cmd = build_scenario_command(
            shift_id=shift_id,
            scenario_id=body.scenario_id,
            target_machine_id=body.target_machine_id,
            severity=body.severity,
        )
        background_tasks.add_task(_persist_scenario_command_safe, cmd)
        return JSONResponse({
            "command_id": cmd.command_id,
            "scenario_id": body.scenario_id,
            "status": "active",
            "shift_id": shift_id,
        })
    except Exception as e:
        logger.exception("Failed to trigger incident")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/sim/resolve")
async def resolve_incident_endpoint(body: IncidentResolveBody):
    """Resolve an active incident — clears fault, cascade recovers.

    Writes ``status = resolve_requested`` to ``ai_serving.scenario_commands``
    in Lakebase so the external producer picks it up, clears its sticky fault,
    and emits a resolved event.
    """
    def _resolve() -> None:
        from agent_server.sim.commands import resolve_command
        resolve_command(command_id=body.command_id)

    try:
        await asyncio.to_thread(_resolve)
        return JSONResponse({"command_id": body.command_id, "status": "resolving"})
    except Exception as e:
        logger.exception("Failed to resolve incident")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/sim/recover_machine")
async def recover_machine_endpoint(body: RecoverMachineBody):
    """Resolve every active incident on ``machine_id`` — no command_id required.

    Mirrors the resolve path but looks up incidents by target machine, which
    is what operators see in the EventFeed (they have ``machine_id`` +
    ``event_type``, not the underlying ``command_id``). It marks matching
    Lakebase ``ai_serving.scenario_commands`` rows as ``resolve_requested`` so
    the external producer emits a resolved event and clears its sticky fault.

    Scoped to this server's shift. Every environment shares one Lakebase
    database, so an unscoped resolve reaches into another environment's
    incidents: it would mark dev's command resolve_requested while the fault
    the operator is looking at in prod stays exactly where it was.
    """
    target_mid = body.machine_id.strip()
    if not target_mid:
        return JSONResponse({"error": "machine_id required"}, status_code=400)

    def _lookup_and_resolve() -> list[str]:
        """Find this machine's open commands and mark them resolve_requested.

        Runs off the event loop: it is one read plus a serial UPDATE per
        matching command, so on a busy line it is easily hundreds of
        milliseconds of blocking psycopg2 — and a connect timeout when
        Lakebase is down.
        """
        from agent_server.memory.pg_pool import query
        from agent_server.sim.commands import resolve_command
        from agent_server.sim.recovery import commands_targeting_machine

        resolved: list[str] = []
        try:
            uc_cmds: list[dict[str, Any]] = []
            try:
                rows = query(
                    """
                    SELECT command_id, target_machine_id
                    FROM ai_serving.scenario_commands
                    WHERE status IN ('pending', 'active', 'resolving')
                      AND shift_id = %s
                    ORDER BY created_at DESC
                    LIMIT 200
                    """,
                    (_server_shift_id(None),),
                )
                uc_cmds = [
                    {"command_id": r["command_id"], "target_machine_id": r.get("target_machine_id")}
                    for r in (rows or [])
                ]
            except Exception as e:  # noqa: BLE001
                logger.warning("recover_machine Lakebase lookup failed: %s", str(e)[:200])
            for c in commands_targeting_machine(uc_cmds, target_mid):
                cid = c["command_id"]
                if cid in resolved:
                    continue
                try:
                    resolve_command(command_id=cid, resolved_by=body.resolved_by)
                    resolved.append(cid)
                except Exception as e:  # noqa: BLE001
                    logger.warning("recover_machine Lakebase resolve failed for %s: %s", cid, str(e)[:200])
        except Exception as e:  # noqa: BLE001
            logger.warning("recover_machine command-targeting lookup failed: %s", str(e)[:200])
        return resolved

    # Look up non-terminal commands targeting this machine from Lakebase (the
    # sole source of truth) and mark them resolve_requested for the producer.
    all_resolved = list(dict.fromkeys(await asyncio.to_thread(_lookup_and_resolve)))
    if all_resolved:
        # The producer owns the resolve marker for its own incidents: it has to
        # clear the sticky fault first, and emitting one here too would close
        # the event before the machine actually recovered.
        return JSONResponse({
            "machine_id": target_mid,
            "resolved_command_ids": all_resolved,
            "count": len(all_resolved),
            "status": "resolving",
        })

    # No command behind it. That is not the same as nothing to do: the backfill
    # seeds one open event deliberately as shift carryover, and a producer that
    # was restarted mid-incident can leave one behind too. Those rows had no
    # route to closure at all — Recover reported nothing to clear and the event
    # stayed on the board until an eight-hour age-out hid it. The app can append
    # the marker itself, because there is no fault to clear first.
    def _close_open_events() -> int:
        from agent_server.serving.realtime_store import mark_machine_events_resolved
        try:
            return mark_machine_events_resolved(
                _server_shift_id(None), target_mid, resolved_by=body.resolved_by or "operator",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("recover_machine event close failed for %s: %s", target_mid, str(e)[:200])
            return 0

    closed = await asyncio.to_thread(_close_open_events)
    return JSONResponse({
        "machine_id": target_mid,
        "resolved_command_ids": [],
        "count": closed,
        "closed_events": closed,
        "status": "resolved" if closed else "no_active_incident",
    })


@app.get("/sim/commands")
async def list_commands():
    """List active incidents from Lakebase ``ai_serving.scenario_commands``."""
    def _read() -> list[dict]:
        from agent_server.serving.realtime_store import read_commands_active
        return read_commands_active(os.environ.get("SIM_SHIFT_ID", "shift_1"))

    try:
        return JSONResponse(jsonable_encoder({"commands": await asyncio.to_thread(_read)}))
    except Exception as e:
        logger.exception("Failed to list commands")
        return JSONResponse({"error": str(e)}, status_code=500)


# ── SSE serving stream ─────────────────────────────────────────────────

@app.get("/serving/stream")
async def serving_stream():
    """SSE endpoint — pushes live dashboard state from Lakebase.

    Reads the ``ai_serving.*_history`` schema in Lakebase Postgres. Data is
    written by the streaming sync job consuming bronze Delta (Producer →
    Zerobus → bronze → streaming_quality_sync → Lakebase).

    There is no silent fallback: if Postgres is configured but unreachable,
    the SSE stream surfaces the error so the UI can show the operator.
    """
    import asyncio
    import json as _json

    from starlette.responses import StreamingResponse

    source = os.environ.get("SSE_SOURCE", "lakebase").lower()
    if source != "lakebase":
        logger.warning(
            "SSE_SOURCE=%r is no longer supported; using lakebase playback cursor",
            source,
        )

    async def _generate_lakebase():
        """Production path: 1 Hz playback cursor over ``ai_serving.*_history``.

        Architecture:
          * The streaming sync job appends every per-second producer-tick row
            to ``*_history`` Lakebase tables.
          * This generator maintains a per-machine + per-KPI + per-inventory
            cursor (single timestamp each). Each SSE tick (``poll_s`` = 1 s)
            it asks ``read_*_after(cursor)`` for the NEXT distinct timestamp's
            rows and advances the cursor.
          * On first connect the cursor is positioned at
            ``MAX(ts) - SSE_BUFFER_SECONDS`` (default 2 s) so we have a small
            buffer of replay queued — this absorbs Zerobus + streaming
            coalescing jitter and keeps the visible cadence smooth at 1 Hz.
          * When no row newer than the cursor exists yet, the payload carries
            ``status: "waiting"`` (no machines/KPI updates) so the UI can show
            "Live · waiting for new value" without blanking out the dashboard.
          * On ``/sim/restart`` the caller wipes both ``*_history`` and the
            cursor dict; the generator's next poll will reposition from the
            new (small) ``MAX(ts)`` and start emitting fresh data within ~20 s.

        ``inventory`` and ``commands`` have their own cadences:
          * inventory ticks at the same rate as machine_state (read via cursor).
          * commands are read fresh every poll — they're sparse and the cursor
            model would needlessly suppress real-time incident notifications.
        """
        from datetime import datetime, timezone

        from agent_server.serving.realtime_store import (
            read_commands_active,
            read_event_log_recent,
            read_inventory_after,
            read_line_kpi_after,
            read_machine_state_after,
            read_max_history_ts,
        )

        mode_label = "lakebase"
        shift_id = os.environ.get("SIM_SHIFT_ID", "shift_1")
        poll_s = float(os.environ.get("SSE_POLL_SECONDS", "1.0"))
        # Buffer depth: how far back of MAX(ts) we position the cursor on first
        # connect. Empirically: bronze commits ~5 s, streaming batch ~5–10 s,
        # processing ~2 s — 20 s gives one full batch of headroom with margin.
        # Tune via env if you observe sustained underruns or want fresher data.
        # Matches the '2' every app yaml ships. The code default was 20, so a run
        # without the rendered env opened the stream 18 s further into the past
        # and replayed its way forward — a dashboard that looked live but lagged,
        # which is the hardest kind of wrong to notice.
        buffer_s = max(1, int(os.environ.get("SSE_BUFFER_SECONDS", "2")))

        # Per-shift cursors. Dict because future multi-shift support stays simple.
        # Keyed by (kind, shift_id) — separate cursors per stream because each
        # has its own bronze commit cadence.
        cursor: dict[str, Any] = {
            "machine": None,
            "kpi": None,
            "inventory": None,
        }
        # Cache last good payload pieces so a "waiting" tick can keep showing
        # the previous frame instead of blanking the dashboard.
        last_machines: list = []
        last_kpi: dict | None = None
        last_inventory: list = []

        lake_tick = 0
        yield "retry: 2000\n\n"

        # ── Cursor positioning (recent-window MAX, with wall-clock fallback) ─
        # The Lakebase ``*_history`` tables are append-only and accumulate
        # rows from MANY producer runs over time (every Spark restart, every
        # ``/sim/restart`` only clears ``*_latest`` — not ``*_history``).
        # An unrestricted ``MAX(ts)`` happily returns a row from a producer
        # run hours-or-days ago, and the cursor anchored there would replay
        # 50+ shifts of stale fault patterns before catching up. The user
        # symptom: line schematic toggles red↔green and OEE jumps 50%↔75%
        # as different runs interleave.
        #
        # Strategy:
        #   * ``read_max_history_ts(shift, recent_seconds=300)`` returns
        #     MAX(ts) restricted to the last 5 minutes of wall-clock — so
        #     ONLY the current producer run is ever visible.
        #   * If that returns None (sync stalled, table truly empty for
        #     this shift), fall back to ``NOW - buffer_s`` so the cursor
        #     stays in the present and waits for fresh rows instead of
        #     latching onto the stale tail.
        #   * The catch-up block below re-applies the same logic every
        #     poll so the cursor never drifts into old replay territory.
        from datetime import timedelta as _td

        # Match the recent-window in ``read_max_history_ts``. 5 min is
        # generous enough to absorb the worst sync hiccup we've observed
        # (~30 s) while still excluding any prior producer-run tail.
        recent_window_s = 300

        def _wallclock_anchor() -> "datetime":
            return datetime.now(timezone.utc) - _td(seconds=buffer_s)

        def _resolve_anchor() -> "datetime":
            """Recent-window MAX(ts) - buffer_s, falling back to NOW - buffer_s."""
            try:
                max_ts = read_max_history_ts(shift_id, recent_seconds=recent_window_s)
            except Exception:
                logger.exception("read_max_history_ts failed in SSE init")
                max_ts = None
            if max_ts is None:
                return _wallclock_anchor()
            # ``max_ts`` is naive on some psycopg2 builds — coerce to UTC.
            if getattr(max_ts, "tzinfo", None) is None:
                max_ts = max_ts.replace(tzinfo=timezone.utc)
            return max_ts - _td(seconds=buffer_s)

        def _read_tick(cur_machine, cur_kpi, cur_inventory, want_anchor):
            """Every Lakebase read for one SSE tick, in one worker-thread hop.

            These are blocking psycopg2 calls. Run inline on the event loop
            they would freeze EVERY request the app is serving — including
            other dashboards' SSE streams — for as long as Postgres takes to
            answer, which during an outage is a connect timeout rather than
            the usual few milliseconds. One hop per tick rather than six keeps
            the thread-pool churn negligible.
            """
            out: dict[str, Any] = {
                "machines": [], "next_machine_ts": None,
                "kpi": None, "next_kpi_ts": None,
                "inventory": [], "next_inv_ts": None,
                "anchor": None,
            }
            if cur_machine is not None:
                out["machines"], out["next_machine_ts"] = read_machine_state_after(shift_id, cur_machine)
            if cur_kpi is not None:
                out["kpi"], out["next_kpi_ts"] = read_line_kpi_after(shift_id, cur_kpi)
            if cur_inventory is not None:
                out["inventory"], out["next_inv_ts"] = read_inventory_after(shift_id, cur_inventory)
            if want_anchor:
                try:
                    out["anchor"] = _resolve_anchor()
                except Exception:
                    logger.exception("SSE cursor catch-up anchor failed")
            out["events"] = read_event_log_recent(shift_id, limit=50)
            out["commands"] = read_commands_active(shift_id)
            return out

        start_cursor = await asyncio.to_thread(_resolve_anchor)
        cursor["machine"] = start_cursor
        cursor["kpi"] = start_cursor
        cursor["inventory"] = start_cursor
        logger.info(
            "SSE cursor initialised (recent-window) shift=%s buffer_s=%s start=%s",
            shift_id, buffer_s, start_cursor,
        )

        while True:
            try:
                # ── Pull next-tick rows from history via cursor ─────────

                # If still None (history empty), fall through with empty
                # arrays — the "waiting" branch below handles it.
                tick = await asyncio.to_thread(
                    _read_tick,
                    cursor["machine"], cursor["kpi"], cursor["inventory"],
                    (lake_tick % 5) == 0,
                )
                machine_rows: list = tick["machines"]
                kpi_row: dict | None = tick["kpi"]
                inventory_rows: list = tick["inventory"]
                next_machine_ts = tick["next_machine_ts"]
                next_kpi_ts = tick["next_kpi_ts"]
                next_inv_ts = tick["next_inv_ts"]

                # NOTE: deliberately NO data-source fallback here. If
                # ``*_history`` is empty (fresh deploy / right after restart)
                # the dashboard shows the "waiting for new value" indicator
                # until the streaming sync job writes the first batch.
                # Reading from ``*_latest`` would mask streaming-sync outages
                # by serving stale state — exactly the kind of fallback the
                # spec explicitly forbids.

                # Advance cursors when new rows landed.
                if next_machine_ts is not None:
                    cursor["machine"] = next_machine_ts
                if next_kpi_ts is not None:
                    cursor["kpi"] = next_kpi_ts
                if next_inv_ts is not None:
                    cursor["inventory"] = next_inv_ts

                # ── Cursor catch-up (recent-window anchored) ────────────
                # Re-resolve the recent-window anchor every 5 polls. If
                # the cursor has fallen more than ``2 * buffer_s + 2``
                # seconds behind it, snap forward. The recent-window
                # MAX(ts) lookup costs one indexed query per 5 s, which
                # is negligible. The wall-clock fallback inside
                # ``_resolve_anchor`` ensures the cursor never strands
                # on an old producer run — even when the streaming sync
                # job is briefly silent.
                if cursor["machine"] is not None and tick["anchor"] is not None:
                    try:
                        target = tick["anchor"]
                        cur_machine = cursor["machine"]
                        if getattr(cur_machine, "tzinfo", None) is None:
                            cur_machine = cur_machine.replace(tzinfo=timezone.utc)
                        behind_s = (target - cur_machine).total_seconds()
                        max_behind_s = float(buffer_s) * 2.0 + 2.0
                        if behind_s > max_behind_s:
                            logger.info(
                                "SSE cursor lag %.1fs > %.1fs threshold — "
                                "snapping %s → %s",
                                behind_s, max_behind_s, cur_machine, target,
                            )
                            cursor["machine"] = target
                            cursor["kpi"] = target
                            cursor["inventory"] = target
                    except Exception:
                        logger.exception("SSE cursor catch-up check failed")

                # Cache last-good frames so a SINGLE waiting tick doesn't blank
                # the panel mid-conversation. The ``waiting`` flag is still set
                # truthfully (no new row this poll), so the UI shows the
                # "waiting" pill — the values are just held over for one frame
                # instead of dropping to zero. This is a UX choice, not a
                # data-source fallback (we never read from a different table).
                if machine_rows:
                    last_machines = machine_rows
                if kpi_row is not None:
                    last_kpi = kpi_row
                if inventory_rows:
                    last_inventory = inventory_rows

                kpi_for_payload = kpi_row or last_kpi
                machines_for_payload = machine_rows or last_machines
                # NOTE: deliberately NO server-side OEE derivation. KPI rows
                # come from the producer (the source of truth) and flow through
                # streaming QC. If ``oee_to_now`` reads 0, it's because the
                # producer wrote 0 — we surface that honestly so the operator
                # sees the actual KPI rather than a derived stand-in.

                # Events + commands always read fresh — they're sparse and
                # users want to see incidents the moment they're inserted.
                events = tick["events"]
                commands = tick["commands"]

                # Build payload. ``waiting=True`` when the cursor produced no
                # new rows AND no usable cached frame is available.
                got_new = bool(machine_rows or kpi_row or inventory_rows)
                no_data_at_all = not (last_machines or last_kpi)
                lake_tick += 1
                payload = {
                    "machines": machines_for_payload,
                    "kpi": kpi_for_payload or {},
                    "events": events,
                    "commands": commands,
                    "inventory": inventory_rows or last_inventory,
                    "tick": lake_tick,
                    "source_mode": mode_label,
                    "status": "waiting" if (no_data_at_all and not got_new) else "live",
                    "waiting": (not got_new),
                    "cursor_lag_seconds": None,
                }

                # Surface event timestamp + ingestion latency for the live ribbon.
                src_ts = _iso_from_payload(machines_for_payload, kpi_for_payload, events)
                if src_ts:
                    try:
                        src_dt = datetime.fromisoformat(str(src_ts).replace("Z", "+00:00"))
                        payload["source_last_event_ts"] = src_dt.isoformat()
                        latency_ms = max(0, int((datetime.now(timezone.utc) - src_dt).total_seconds() * 1000))
                        payload["ingestion_latency_ms"] = latency_ms
                        payload["cursor_lag_seconds"] = round(latency_ms / 1000.0, 1)
                    except Exception:
                        payload["source_last_event_ts"] = str(src_ts)

                payload["stream_last_update_ts"] = datetime.now(timezone.utc).isoformat()
                payload_str = _json.dumps(payload, default=str)
                yield f"data: {payload_str}\n\n"

                _live_record("sse_ticks")
                _live_record("sse_payload_bytes", float(len(payload_str)))
                _live_record(
                    "sse_payload_rows",
                    float(len(machines_for_payload or []) + len(events or [])
                          + len(inventory_rows or last_inventory or []) + len(commands or [])),
                )
                _live_record("lakebase_reads", 4.0)  # 3 cursor reads + 1 events read
                if events and got_new:
                    _live_record("events_published", float(len(events)))
            except GeneratorExit:
                return
            except Exception as exc:
                logger.warning("Lakebase SSE read failed: %s", str(exc)[:200])
                yield f"data: {_json.dumps({'error': f'Lakebase read failed: {str(exc)[:100]}'})}\n\n"

            await asyncio.sleep(poll_s)

    return StreamingResponse(
        _generate_lakebase(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def main():
    parser = argparse.ArgumentParser(description="Line Manager Agent Server")
    parser.add_argument("--port", type=int, default=int(os.environ.get("AGENT_PORT", "8001")))
    args = parser.parse_args()

    def shutdown_handler(signum, frame):
        logger.info("Received signal %s, shutting down...", signum)
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    logger.info("Starting agent server on port %d...", args.port)

    import uvicorn
    uvicorn.run(
        "agent_server.start_server:app",
        host="0.0.0.0",
        port=args.port,
        workers=1,
        log_level="info",
    )


if __name__ == "__main__":
    main()
