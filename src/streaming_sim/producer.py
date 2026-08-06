"""Streaming producer — generates live bottling line data via Zerobus.

Ingest targets are **Bronze** Delta tables (``*_bronze``); the streaming
quality-gate job (``pipelines/streaming_quality_sync.py``) reads those streams
and writes validated **Gold** tables + the Lakebase ``ai_serving.*`` cache.

Runs as a long-lived Databricks Job.  Two modes in the same tick loop:
  1. Baseline: all machines emit normal operating data every tick
  2. Incident: when scenario_commands arrive, faults are applied and the
     cascade engine propagates effects across the line in real time

Incident lifecycle (driven by scenario_commands.status):
  pending → active → resolve_requested → resolving → resolved

Environment variables (required for Zerobus baseline streaming):
  ZEROBUS_SERVER_ENDPOINT, DATABRICKS_WORKSPACE_URL,
  DATABRICKS_CLIENT_ID, DATABRICKS_CLIENT_SECRET

Optional:
  UC_CATALOG (default: main)
  UC_SCHEMA  (default: line_manager_streaming)
  SIM_SHIFT_ID (default: shift_1)
  SIM_TICK_SECONDS (default: 1)
  DATABRICKS_HOST, DATABRICKS_WAREHOUSE_ID — if both set, polls ``scenario_commands``
  via DBSQL (service-principal OAuth) for live incidents; otherwise baseline-only.
"""

from __future__ import annotations

import pathlib
import sys


def _ensure_src_on_path() -> None:
    """Ensure bundle root (parent of `src/`) is on `sys.path`.

    On serverless workflows, Databricks executes the file via `exec(...)` and
    does not define `__file__`. Fall back to walking up from CWD to find `src/`.
    """
    try:
        here = pathlib.Path(__file__).resolve()
        candidates = [here.parent] + list(here.parents)
    except NameError:
        cwd = pathlib.Path.cwd().resolve()
        candidates = [cwd] + list(cwd.parents)

    for p in candidates:
        if (p / "src").is_dir():
            if str(p) not in sys.path:
                sys.path.insert(0, str(p))
            return


_ensure_src_on_path()

import argparse
import base64
import json
import logging
import math
import os
import random
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from src.data_generation.config import MACHINES
from src.streaming_sim.cascade import (
    LineState,
    MachineSpec,
    build_line,
    cascade_tick,
    compute_line_kpi,
    snapshot_to_dicts,
)
from src.streaming_sim.incidents import (
    apply_incident,
    incident_target_machines,
    resolve_incident,
)
from src.streaming_sim.line_kpi_derived import ShiftKpiAccumulator

logger = logging.getLogger(__name__)


def _sanitize_for_json(obj: Any) -> Any:
    """Replace NaN/Inf with 0.0 so strict JSON encoders don't choke."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else 0.0
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(v) for v in obj]
    return obj


# Errors that indicate the underlying Zerobus stream has been closed by the
# server (graceful rotation or recovery race) and the in-flight call landed on
# the now-defunct stream. Caught in ``_ingest`` and recovered by re-opening.
_STREAM_CLOSED_TOKENS = (
    "Stream is closed",
    "Stream closed",
)


def _ingest(streams: "ZerobusStreams", record: dict[str, Any], *, label: str) -> None:
    """Ingest one record on ``streams.<label>``; recover transparently if the
    Zerobus server rotated the stream mid-flight.

    Background: Zerobus performs a server-initiated stream rotation roughly
    every 16-30 minutes. The SDK auto-recovers (creates a replacement stream
    behind the scenes), but a record submitted to the *old* stream a few
    hundred milliseconds before the rotation completes raises
    ``ZerobusException: Stream is closed``. Without recovery here the producer
    job crashes and the continuous-job restart backoff produces a visible
    multi-minute gap in the SSE stream.

    Recovery strategy: catch the closed-stream error once, ask
    ``streams.reopen(label)`` for a fresh stream, retry the same record. If
    the retry also fails (or any non-stream-closed error fires), log the
    payload and re-raise so the workload can be inspected.
    """
    payload = json.dumps(_sanitize_for_json(record), allow_nan=False, default=str)
    try:
        getattr(streams, label).ingest_record_offset(payload)
        return
    except Exception as e:  # noqa: BLE001 — SDK raises ZerobusException
        if not any(tok in str(e) for tok in _STREAM_CLOSED_TOKENS):
            try:
                debug = json.dumps(record, default=str)
            except Exception:
                debug = str(record)
            logger.error("Zerobus ingest failed (%s): %s payload=%s", label, e, debug)
            raise
        logger.warning(
            "Zerobus stream %s closed mid-flight (%s); reopening and retrying once",
            label, str(e)[:120],
        )

    try:
        streams.reopen(label)
        getattr(streams, label).ingest_record_offset(payload)
    except Exception as e:
        try:
            debug = json.dumps(record, default=str)
        except Exception:
            debug = str(record)
        logger.error(
            "Zerobus ingest failed (%s) after stream reopen: %s payload=%s",
            label, e, debug,
        )
        raise


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, default)
    if v is None or v == "":
        raise ValueError(f"Missing required env var: {name}")
    # Support bundle/job secret references like {{secrets/scope/key}} even when
    # passed via task parameters (which are not interpolated by DAB).
    s = str(v).strip()
    if s.startswith("{{secrets/") and s.endswith("}}"):
        try:
            inner = s[len("{{") : -len("}}")]
            _prefix, scope, key = inner.split("/", 2)
            # Prefer dbutils when available (works in job compute contexts).
            try:
                from databricks.sdk.runtime import dbutils  # type: ignore

                return dbutils.secrets.get(scope=scope, key=key)
            except Exception:
                from databricks.sdk import WorkspaceClient

                w = WorkspaceClient()
                secret = w.secrets.get_secret(scope=scope, key=key)
                # SDK returns base64-encoded bytes in `value`
                decoded = base64.b64decode(secret.value).decode("utf-8")
                return decoded
        except Exception as e:
            raise ValueError(f"Could not resolve secret reference for {name}: {s}") from e
    return v


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# Fixed 8-hour shift windows aligned to UTC 00:00 / 08:00 / 16:00 — a realistic
# 3-shift factory day. ``cumulative_cases`` resets at each boundary so the Cases
# tile is a believable *per-shift* count (0 → ~planned_cases) instead of an
# unbounded lifetime total. 480 min at the line's 17.5 cases/min ideal rate =
# 8,400 cases, which is exactly ``production_schedule.planned_cases``.
SHIFT_WINDOW_MINUTES = 480

# The line's ideal end-of-line rate: 17.5 cases/min × 480 min = 8,400, which is
# exactly a full shift's ``production_schedule.planned_cases``. Used only to
# sanity-check a rehydrated tally (see ``_plausible_seed_cases``).
IDEAL_CASES_PER_MIN = 17.5
SHIFT_PLANNED_CASES = round(IDEAL_CASES_PER_MIN * SHIFT_WINDOW_MINUTES)

# Bottles per physical case (24-pack). The cascade tracks flow in BOTTLES/min
# end to end (CASE_PACKER's cases/min capacity is scaled to bottle-equivalents
# so a 17.5 cases/min packer doesn't throttle a 420 BPM bottle line). The
# end-of-line machine's throughput is therefore in bottles/min, so cases
# produced = end-of-line bottle throughput / BOTTLES_PER_CASE. Ideal end-of-line
# rate 420 BPM ÷ 24 = 17.5 cases/min → 8,400 cases/shift (SHIFT_PLANNED_CASES).
BOTTLES_PER_CASE = 24


def _shift_window_start(now: datetime) -> datetime:
    """UTC-aligned start of the fixed 8h shift window containing ``now``.

    Boundaries at 00:00, 08:00 and 16:00 UTC. Pure function so the rollover /
    reset logic is unit-testable without a running producer.
    """
    now_utc = now.astimezone(timezone.utc)
    start_hour = (now_utc.hour // 8) * 8
    return now_utc.replace(hour=start_hour, minute=0, second=0, microsecond=0)


def _plausible_seed_cases(seed_cases: float | None, elapsed_min: float) -> float:
    """Sanity-check a rehydrated per-shift ``cumulative_cases`` seed.

    ``cumulative_cases`` is a *per-shift* tally (0 → ~``planned_cases``). Older
    builds wrote an unbounded *lifetime* tally, so a warm restart that reads the
    latest value for the current window can inherit hundreds of thousands of
    cases (e.g. 553,790) — nonsensical for a single shift. Guard against it: if
    the rehydrated value exceeds 1.5× a full shift's planned output, discard it
    and fall back to an elapsed-time estimate (elapsed × ideal rate), capped at
    the same ceiling. Returns ``0.0`` for ``None`` / negative. Pure + testable.
    """
    ceiling = SHIFT_PLANNED_CASES * 1.5
    if seed_cases is None or seed_cases < 0:
        return 0.0
    if seed_cases > ceiling:
        return min(max(0.0, elapsed_min) * IDEAL_CASES_PER_MIN, ceiling)
    return float(seed_cases)


def _ts_us(dt: datetime) -> int:
    """Event-time as microseconds-since-epoch.

    Zerobus' bronze ingest path serializes records to JSON; TIMESTAMP fields
    must be **integers in microseconds** (the Spark/Arrow internal scale).
    Sending milliseconds gives 1970 dates (off by 1000); sending ISO strings
    breaks Zerobus' decoder ("invalid digit found in string").
    """
    # why: Zerobus TIMESTAMP decoding expects microseconds, not milliseconds.
    return int(dt.timestamp() * 1_000_000)


def _iso_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


# ---------------------------------------------------------------------------
# Machine specs from config.py
# ---------------------------------------------------------------------------

def _machine_specs() -> list[MachineSpec]:
    """Convert config MACHINES to cascade MachineSpecs (capacity in BPM).

    Unit conversion notes:
      * conveyors: belt-speed m/min → effectively unbounded for capacity (cap 9999).
      * CASE_PACKER: cases/min × 24 bottles/case.
      * BOTTLE_WASHER (washer): tunnel-speed m/min → BPM; modern beverage washers
        process ~12-20 bottles per linear metre, so 25 m/min ≈ 420 BPM (matching
        FILLER/CAPPER/LABELLER, the line's intended bottleneck-balanced speed).
        Without this conversion the washer artificially throttles the entire
        line to ~25 BPM and OEE collapses to ~10 %.
      * everything else with a numeric nominal_speed: treated as BPM directly.
    """
    specs: list[MachineSpec] = []
    for m in MACHINES:
        if m.nominal_speed is None:
            cap = 300.0
        elif m.type == "conveyor":
            cap = 9999.0
        elif m.id == "CASE_PACKER":
            cap = m.nominal_speed * BOTTLES_PER_CASE  # cases/min → bottle-equivalents
        elif m.type == "washer":  # tunnel m/min → BPM
            cap = m.nominal_speed * 16.8  # ≈ bottles per linear metre
        else:
            cap = float(m.nominal_speed)
        specs.append(MachineSpec(
            id=m.id,
            line_position=m.line_position,
            nominal_capacity=cap,
            pass_through=m.type == "conveyor",
        ))
    return specs


def _machine_ids() -> list[str]:
    raw = os.environ.get("SIM_MACHINES", "").strip()
    if raw:
        return [m.strip() for m in raw.split(",") if m.strip()]
    return [m.id for m in MACHINES]


# ---------------------------------------------------------------------------
# Active incident tracker
# ---------------------------------------------------------------------------

@dataclass
class ActiveIncident:
    command_id: str
    scenario_id: str
    target_machines: list[str]
    severity: str


def incident_for_resolve(
    cmd: dict[str, Any],
    active_incidents: dict[str, ActiveIncident],
    state: LineState,
) -> ActiveIncident | None:
    """The incident a resolve request refers to, popped or rebuilt.

    Normally the incident is still in ``active_incidents`` and this is a pop.
    It is absent whenever the producer restarted after the incident started —
    routine, since every deploy cycles the job — and rehydration does not cover
    it, because ``fetch_unresolved`` reloads only 'active' and 'resolving'.

    Before the rebuild, that gap sent the command straight to its terminal
    UPDATE with no resolve marker emitted. The dashboard reads those markers to
    pair an event closed, so the event stayed active forever while its command
    was terminal, and Recover — which only offers itself for machines with a
    live command — had nothing left to clear. The operator was left with a red
    incident no button could remove. Rebuilding from the command row makes
    recovery idempotent across restarts.

    Returns None only when the row names no machine on this line, in which case
    the caller marks it resolved without an event and the original event stays
    open. That is a real failure, so the caller logs it.
    """
    incident = active_incidents.pop(str(cmd["command_id"]), None)
    if incident is not None:
        return incident

    scenario_id = str(cmd.get("scenario_id") or "")
    named = incident_target_machines(
        scenario_id,
        str(cmd["target_machine_id"]) if cmd.get("target_machine_id") else None,
    )
    targets = [mid for mid in named if mid in state.machines]
    if not targets:
        logger.warning(
            "Resolve request for command %s (scenario=%r, names %r) matches no "
            "machine on this line; marking it resolved without an event, which "
            "leaves the original event open in the dashboard",
            cmd.get("command_id"), scenario_id, named,
        )
        return None

    logger.info(
        "Rebuilt incident %s on %s from its command row — not in memory, so "
        "this producer restarted since it started",
        scenario_id, targets,
    )
    return ActiveIncident(
        command_id=str(cmd["command_id"]),
        scenario_id=scenario_id,
        target_machines=targets,
        severity=str(cmd.get("severity") or "medium"),
    )


# ---------------------------------------------------------------------------
# Command store (polls scenario_commands via SQL)
# ---------------------------------------------------------------------------

class CommandStore:
    """Lakebase-backed scenario-commands store for the producer's tick loop.

    History — read this before reverting to a UC SQL-warehouse path:
      Earlier versions of this class polled ``{catalog}.{schema}.scenario_commands``
      via ``databricks.sql`` (a SQL-warehouse round-trip on every tick). With a
      Small starter warehouse the per-call latency was 200-1000 ms and a cold
      warehouse spin-up could stall the data-tick loop for 4+ seconds. That
      showed up in the live UI as multi-second freezes in the SSE stream — the
      gap pattern this migration was meant to remove. ``event_log_bronze``
      (Zerobus -> UC Delta) is the audit trail; ``scenario_commands`` only
      needs to be a fast, governed inbox.

    Auth + connection: mirrors ``LakebaseSink``. Token is OAuth via
    ``w.postgres.generate_database_credential`` when ``LAKEBASE_ENDPOINT`` is
    set, otherwise the workspace OAuth token. Connection is reopened when the
    token TTL nears expiry (50 min) or when a query fails.

    The constructor signature accepts the historical ``host`` / ``warehouse_id``
    / ``catalog`` / ``schema`` args so callers in the producer's bootstrap
    don't need a coordinated edit; they're now ignored. Lakebase config comes
    from the same ``PGHOST`` / ``PGUSER`` / ``LAKEBASE_ENDPOINT`` env vars the
    sink uses, plus ``ai_serving.scenario_commands`` as the fixed FQN.
    """

    _TOKEN_TTL_S = 50 * 60

    def __init__(
        self,
        host: str | None = None,
        warehouse_id: str | None = None,
        catalog: str | None = None,
        schema: str | None = None,
        shift_id: str | None = None,
    ) -> None:
        # Every environment shares one Lakebase database, so dev and prod poll
        # the same ai_serving.scenario_commands table. Unfiltered, whichever
        # producer polls first claims the row: it applies the fault to its own
        # line state, emits the event into its own bronze schema, and flips the
        # shared row to 'active' — after which the environment the operator was
        # actually looking at never sees it as pending. That is why a trigger
        # sometimes did nothing at all, and why it was intermittent: it was a
        # race between two producers. Scope the inbox to this producer's shift.
        self._shift_id = shift_id
        self._pg_host = _env("PGHOST")
        self._pg_user = _env("PGUSER")
        self._pg_database = os.environ.get("PGDATABASE", "databricks_postgres")
        self._pg_port = int(os.environ.get("PGPORT", "5432"))
        self._pg_sslmode = os.environ.get("PGSSLMODE", "require")
        self._endpoint = os.environ.get("LAKEBASE_ENDPOINT", "")
        self._conn = None
        self._token: str | None = None
        self._token_minted_at: float = 0.0
        self._fqn = "ai_serving.scenario_commands"

    def _mint_token(self) -> str:
        """OAuth token via WorkspaceClient — same path as ``LakebaseSink``."""
        from databricks.sdk import WorkspaceClient

        w = WorkspaceClient()
        if self._endpoint:
            try:
                cred = w.postgres.generate_database_credential(endpoint=self._endpoint)
                return cred.token
            except Exception as e:  # noqa: BLE001
                logger.info(
                    "CommandStore generate_database_credential failed (%s) — "
                    "falling back to workspace token",
                    str(e)[:120],
                )
        token = w.config.authenticate()
        if callable(token):
            import requests

            req = requests.Request("GET", "https://dummy")
            prepared = req.prepare()
            token(prepared)
            return prepared.headers.get("Authorization", "").replace("Bearer ", "")
        return str(token)

    def _ensure_connection(self):
        import psycopg2

        now = time.monotonic()
        token_stale = (now - self._token_minted_at) >= self._TOKEN_TTL_S
        if self._conn is not None and not token_stale:
            return self._conn

        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

        self._token = self._mint_token()
        self._token_minted_at = now
        self._conn = psycopg2.connect(
            host=self._pg_host,
            port=self._pg_port,
            user=self._pg_user,
            password=self._token,
            dbname=self._pg_database,
            sslmode=self._pg_sslmode,
            connect_timeout=10,
        )
        # Short SELECT/UPDATE statements; commit-per-call keeps the locking
        # window minimal so the app's INSERTs are never blocked behind a
        # producer transaction.
        self._conn.autocommit = True
        logger.info(
            "CommandStore connected to Lakebase %s as %s (token len=%d)",
            self._pg_host, self._pg_user, len(self._token or ""),
        )
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def _reset_on_error(self) -> None:
        """Drop the connection so the next call reconnects fresh.

        Same isolation contract as ``LakebaseSink._execute_values_batched``:
        a transient Lakebase blip must not crash the producer's tick loop;
        the next tick reconnects and resumes.
        """
        try:
            if self._conn is not None:
                self._conn.close()
        except Exception:
            pass
        self._conn = None

    def _select_by_status(self, status: str) -> list[dict[str, Any]]:
        try:
            conn = self._ensure_connection()
            columns = (
                "SELECT command_id, shift_id, scenario_id, target_machine_id, "
                "       severity, status, created_at, created_by, params, "
                "       resolved_at, resolved_by "
                f"FROM {self._fqn} WHERE status = %s"
            )
            with conn.cursor() as cur:
                if self._shift_id:
                    cur.execute(
                        f"{columns} AND shift_id = %s ORDER BY created_at LIMIT 50",
                        (status, self._shift_id),
                    )
                else:
                    cur.execute(
                        f"{columns} ORDER BY created_at LIMIT 50",
                        (status,),
                    )
                cols = [c[0] for c in cur.description]
                return [dict(zip(cols, r)) for r in cur.fetchall()]
        except Exception as e:  # noqa: BLE001
            logger.warning("CommandStore SELECT (%s) failed: %s", status, str(e)[:200])
            self._reset_on_error()
            return []

    def fetch_pending(self) -> list[dict[str, Any]]:
        return self._select_by_status("pending")

    def fetch_resolve_requested(self) -> list[dict[str, Any]]:
        return self._select_by_status("resolve_requested")

    def fetch_unresolved(self) -> list[dict[str, Any]]:
        """Commands still in flight, for rehydration after a restart.

        The producer holds active incidents in memory, so a restart loses them:
        the machine returns to running while the row stays 'active' forever and
        a later resolve finds nothing to clear.
        """
        return self._select_by_status("active") + self._select_by_status("resolving")

    def set_status(self, command_id: str, status: str) -> None:
        """Flip ``status`` and stamp ``resolved_at`` on terminal transitions.

        The terminal-state stamp mirrors the prior UC behaviour so the
        resolve-requested -> resolved transition still records when the
        producer finished applying the recovery cascade.
        """
        try:
            conn = self._ensure_connection()
            with conn.cursor() as cur:
                if status == "resolved":
                    cur.execute(
                        f"UPDATE {self._fqn} "
                        f"SET status = %s, resolved_at = now() "
                        f"WHERE command_id = %s",
                        (status, command_id),
                    )
                else:
                    cur.execute(
                        f"UPDATE {self._fqn} "
                        f"SET status = %s WHERE command_id = %s",
                        (status, command_id),
                    )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "CommandStore UPDATE (cid=%s status=%s) failed: %s",
                command_id, status, str(e)[:200],
            )
            self._reset_on_error()


# ---------------------------------------------------------------------------
# Zerobus stream manager
# ---------------------------------------------------------------------------

class ZerobusStreams:
    """Wraps the 6 Zerobus ingest streams (inventory_status flows end-to-end so the producer is the sole live-state writer)."""

    # Maps the in-code label (also the attribute name on this class) to the
    # bronze table the Zerobus stream targets. Single source of truth — used
    # by both initial open and ``reopen`` for stream-closed recovery.
    _LABEL_TO_BRONZE = {
        "machine_state":    "machine_state_bronze",
        "event_log":        "event_log_bronze",
        "quality_checks":   "quality_checks_bronze",
        "telemetry":        "telemetry_bronze",
        "line_kpi":         "line_kpi_bronze",
        # ``inventory_status_bronze`` lets the producer be the sole writer to
        # bronze Delta and still flow inventory through
        # producer → streaming quality-gate → ai_serving.
        "inventory_status": "inventory_status_bronze",
    }

    def __init__(self, catalog: str, schema: str) -> None:
        from zerobus.sdk.shared import RecordType, StreamConfigurationOptions, TableProperties
        from zerobus.sdk.sync import ZerobusSdk

        endpoint = _env("ZEROBUS_SERVER_ENDPOINT")
        workspace = _env("DATABRICKS_WORKSPACE_URL")
        client_id = _env("DATABRICKS_CLIENT_ID")
        client_secret = _env("DATABRICKS_CLIENT_SECRET")

        self._fqn = f"{catalog}.{schema}"
        self._opts = StreamConfigurationOptions(record_type=RecordType.JSON)
        self._sdk = ZerobusSdk(endpoint, workspace)
        self._client_id = client_id
        self._client_secret = client_secret
        self._TableProperties = TableProperties

        # Bronze Delta tables — the streaming quality-gate job reads these and writes validated Gold.
        for label in self._LABEL_TO_BRONZE:
            setattr(self, label, self._open(self._LABEL_TO_BRONZE[label]))

    def _open(self, table: str):
        """Create a Zerobus stream with retry-on-401.

        The Unity Catalog RAR token endpoint rate-limits burst mints from a
        single service principal: when we open 6 streams back-to-back, some
        calls come back as ``invalid_authorization_details`` (HTTP 401) even
        though grants and entitlements are correct. Reproduced consistently
        locally; retrying with backoff resolves it. See:
        ``Specified UC token is in invalid format: Client error (401):
          {"error":"invalid_authorization_details", ...}``
        """
        props = self._TableProperties(f"{self._fqn}.{table}")
        delay = 2.0
        for attempt in range(1, 7):
            try:
                return self._sdk.create_stream(
                    self._client_id, self._client_secret, props, self._opts
                )
            except Exception as exc:  # noqa: BLE001 — SDK raises ZerobusException
                msg = str(exc)
                if attempt < 6 and (
                    "invalid_authorization_details" in msg or "401" in msg
                ):
                    logger.warning(
                        "Zerobus create_stream(%s) attempt %d hit 401 "
                        "(UC RAR rate limit) — retrying in %.1fs",
                        table, attempt, delay,
                    )
                    time.sleep(delay)
                    delay *= 1.7
                    continue
                raise
        raise RuntimeError(f"{table}: max retries exceeded")

    def reopen(self, label: str) -> None:
        """Replace ``self.<label>`` with a freshly-opened Zerobus stream.

        Called by ``_ingest`` when the in-flight stream has been closed by a
        server-initiated rotation. Best-effort closes the stale handle first so
        the SDK frees its resources; failures there are non-fatal because the
        stream is already broken.
        """
        bronze = self._LABEL_TO_BRONZE.get(label)
        if not bronze:
            raise ValueError(f"Unknown Zerobus stream label: {label}")
        try:
            old = getattr(self, label, None)
            if old is not None:
                old.close()
        except Exception:
            pass
        setattr(self, label, self._open(bronze))
        logger.info("Zerobus stream %s reopened", label)

    def flush_all(self) -> None:
        for label in self._LABEL_TO_BRONZE:
            try:
                getattr(self, label).flush()
            except Exception as e:
                logger.warning("flush(%s) failed: %s", label, str(e)[:120])

    def close_all(self) -> None:
        for label in self._LABEL_TO_BRONZE:
            try:
                getattr(self, label).close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Event emitters
# ---------------------------------------------------------------------------

def _emit_incident_start_event(
    streams: ZerobusStreams,
    shift_id: str,
    now: datetime,
    incident: ActiveIncident,
    lakebase_sink: Any = None,
) -> None:
    """Emit one event_log row per affected machine.

    Dual-writes: Zerobus → bronze (analytics) + Lakebase event_log_history
    (live UI). The Lakebase write is best-effort — sink swallows errors so
    Zerobus is never blocked.
    """
    now_us = _ts_us(now)
    for mid in incident.target_machines:
        # Zerobus payload includes the full bronze-schema fields (with the
        # outcome_* extensions); Lakebase's event_log_history schema is the
        # narrower subset that ai_serving readers care about. Build the
        # zerobus payload first, then derive the lakebase row from it.
        zb_payload = {
            "event_id": _iso_id("EVT"),
            "shift_id": shift_id,
            "machine_id": mid,
            "event_type": incident.scenario_id,
            "severity": incident.severity,
            "timestamp_start": now_us,
            "timestamp_end": None,
            "duration_minutes": 0.0,
            "root_cause": incident.scenario_id,
            "description": f"Incident started: {incident.scenario_id}",
            "corrective_action": "",
            "resolution_minutes": 0.0,
            "outcome_oee_impact": 0.0,
            "outcome_description": "",
        }
        _ingest(streams, zb_payload, label="event_log")
        if lakebase_sink is not None:
            lakebase_sink.write_event(zb_payload)


def _emit_incident_resolved_event(
    streams: ZerobusStreams,
    shift_id: str,
    now: datetime,
    incident: ActiveIncident,
    lakebase_sink: Any = None,
) -> None:
    """Emit one resolved event_log row per affected machine.

    See ``_emit_incident_start_event`` for the dual-write pattern.
    """
    now_us = _ts_us(now)
    for mid in incident.target_machines:
        zb_payload = {
            "event_id": _iso_id("EVT"),
            "shift_id": shift_id,
            "machine_id": mid,
            "event_type": "resolved",
            "severity": "info",
            "timestamp_start": now_us,
            "timestamp_end": now_us,
            "duration_minutes": 0.0,
            "root_cause": incident.scenario_id,
            "description": f"Incident resolved: {incident.scenario_id}",
            "corrective_action": "Operator action",
            "resolution_minutes": 0.0,
            "outcome_oee_impact": 0.0,
            "outcome_description": "Recovered",
        }
        _ingest(streams, zb_payload, label="event_log")
        if lakebase_sink is not None:
            lakebase_sink.write_event(zb_payload)


# ---------------------------------------------------------------------------
# Baseline noise
# ---------------------------------------------------------------------------

def _add_baseline_noise(state: LineState, rng: random.Random) -> None:
    """Add gentle speed drift + noise to running machines."""
    for ms in state.machines.values():
        if ms.status == "running":
            drift = rng.uniform(-1.5, 1.5)
            ms.speed_pct = max(70.0, min(105.0, ms.speed_pct + drift))


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def _apply_cli_catalog_schema() -> None:
    """Databricks Jobs can pass config as argv; mirror into env.

    This enables running on serverless workflows compute where cluster env-vars
    may not be available.
    """
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--catalog", default=None)
    p.add_argument("--schema", default=None)
    p.add_argument("--shift-id", default=None)
    p.add_argument("--tick-seconds", default=None)
    p.add_argument("--host", default=None)
    p.add_argument("--warehouse-id", default=None)
    p.add_argument("--zerobus-endpoint", default=None)
    p.add_argument("--workspace-url", default=None)
    p.add_argument("--client-id", default=None)
    p.add_argument("--client-secret", default=None)
    # ``--start-fresh`` is set ONLY by the operator-triggered Clean Restart
    # (``/sim/restart`` in the App). Continuous-trigger restarts after crashes
    # / log-volume rotations use the bundle's default parameters (no flag),
    # so warm restarts still rehydrate invisibly. When the flag is present
    # the producer skips all "resume from previous state" paths so the run
    # truly starts from zero — matches the operator's mental model when
    # they hit Clean Restart in the UI.
    p.add_argument("--start-fresh", action="store_true", default=False)
    # Tier-1 dual-write to Lakebase ai_serving.*_history (sub-second live UI).
    # Without these, the producer only writes to Zerobus → bronze and the live
    # UI inherits Zerobus' 5-30 s materialization SLA. With them, the same
    # rows go directly to Lakebase via psycopg2 in the same tick (~200 ms).
    # See src/streaming_sim/lakebase_sink.py for the architecture rationale.
    p.add_argument("--pg-host", default=None)
    p.add_argument("--pg-user", default=None)
    p.add_argument("--pg-database", default=None)
    p.add_argument("--lakebase-endpoint", default=None)
    p.add_argument(
        "--lakebase-direct-write",
        default=None,
        help="Set to 'false' to disable producer→Lakebase direct write (fallback to consumer-driven _history sink).",
    )
    ns, _unknown = p.parse_known_args()
    if ns.catalog:
        os.environ["UC_CATALOG"] = ns.catalog
    if ns.schema:
        os.environ["UC_SCHEMA"] = ns.schema
    if ns.shift_id:
        os.environ["SIM_SHIFT_ID"] = ns.shift_id
    if ns.tick_seconds:
        os.environ["SIM_TICK_SECONDS"] = ns.tick_seconds
    if ns.host:
        os.environ["DATABRICKS_HOST"] = ns.host
    if ns.warehouse_id:
        os.environ["DATABRICKS_WAREHOUSE_ID"] = ns.warehouse_id
    if ns.zerobus_endpoint:
        os.environ["ZEROBUS_SERVER_ENDPOINT"] = ns.zerobus_endpoint
    if ns.workspace_url:
        os.environ["DATABRICKS_WORKSPACE_URL"] = ns.workspace_url
    if ns.client_id:
        os.environ["DATABRICKS_CLIENT_ID"] = ns.client_id
    if ns.client_secret:
        os.environ["DATABRICKS_CLIENT_SECRET"] = ns.client_secret
    if ns.start_fresh:
        os.environ["SIM_START_FRESH"] = "true"
    if ns.pg_host:
        os.environ["PGHOST"] = ns.pg_host
    if ns.pg_user:
        os.environ["PGUSER"] = ns.pg_user
    if ns.pg_database:
        os.environ["PGDATABASE"] = ns.pg_database
    if ns.lakebase_endpoint:
        os.environ["LAKEBASE_ENDPOINT"] = ns.lakebase_endpoint
    if ns.lakebase_direct_write is not None:
        os.environ["SIM_LAKEBASE_DIRECT_WRITE"] = ns.lakebase_direct_write


def _resolve_databricks_client_secret() -> None:
    """Resolve ``DATABRICKS_CLIENT_SECRET`` if it's still a ``{{secrets/...}}``
    reference in env, replacing it with the actual secret value.

    Why this is needed: the bundle passes the secret as a literal CLI arg
    (``--client-secret '{{secrets/scope/key}}'``) because DAB doesn't
    interpolate these in ``parameters:``. ``_apply_cli_catalog_schema()``
    then writes the raw reference straight into ``DATABRICKS_CLIENT_SECRET``.

    Zerobus is fine because ``_env()`` auto-resolves the reference inside
    ``ZerobusStreams.__init__``. But ``WorkspaceClient()`` reads
    ``DATABRICKS_CLIENT_SECRET`` directly from os.environ, so it gets the
    literal string and OAuth fails with ``invalid_client: Client
    authentication failed``. The LakebaseSink in particular needs a
    healthy ``WorkspaceClient()`` to mint Lakebase OAuth tokens.

    This must run BEFORE any code path that constructs ``WorkspaceClient()``
    — currently that's LakebaseSink + the active-incident rehydration query
    (which already uses ``_env()`` so it's safe either way).

    Idempotent: if the value is already resolved (no ``{{secrets/`` prefix),
    we leave it alone.
    """
    raw = os.environ.get("DATABRICKS_CLIENT_SECRET", "")
    if not raw.startswith("{{secrets/"):
        return
    try:
        # _env handles the dbutils + SDK fallback paths.
        resolved = _env("DATABRICKS_CLIENT_SECRET")
        if resolved and resolved != raw:
            os.environ["DATABRICKS_CLIENT_SECRET"] = resolved
            logger.info(
                "DATABRICKS_CLIENT_SECRET resolved from secret reference (len=%d)",
                len(resolved),
            )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Could not resolve DATABRICKS_CLIENT_SECRET secret ref: %s — "
            "WorkspaceClient() will fail and LakebaseSink will be unavailable",
            str(e)[:200],
        )


def main() -> None:
    _apply_cli_catalog_schema()
    _resolve_databricks_client_secret()
    catalog = os.environ.get("UC_CATALOG", "main")
    schema = os.environ.get("UC_SCHEMA", "line_manager_streaming")
    shift_id = os.environ.get("SIM_SHIFT_ID", "shift_1")
    tick_s = float(os.environ.get("SIM_TICK_SECONDS", "1"))
    start_fresh = (os.environ.get("SIM_START_FRESH") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )

    # Scenario command polling — now Lakebase-backed.
    #
    # Two modes:
    #   * PGHOST + PGUSER BOTH set → command polling is REQUIRED. The class
    #     constructor is cheap (no I/O) so the only way it fails here is a
    #     missing env var; first SELECT in the tick loop opens the connection
    #     and is wrapped in the class's own retry/reconnect contract.
    #   * either env unset → baseline-only mode by design (workshop / smoke
    #     test contexts that don't need the incident workflow).
    #
    # Migration note (2026-06): this used to require ``DATABRICKS_HOST`` +
    # ``DATABRICKS_WAREHOUSE_ID`` and connected via ``databricks.sql`` to
    # poll the UC Delta ``scenario_commands`` table. Each tick paid a
    # SQL-warehouse round-trip (200-1000 ms steady, 4+ s on cold start),
    # which produced visible multi-second freezes in the dashboard SSE
    # stream. The new path reads ``ai_serving.scenario_commands`` directly
    # from Lakebase Postgres, so the per-tick poll is a sub-10 ms local
    # query with no shared-warehouse contention.
    commands: CommandStore | None = None
    pg_host = os.environ.get("PGHOST")
    pg_user = os.environ.get("PGUSER")
    if pg_host and pg_user:
        try:
            commands = CommandStore(shift_id=shift_id)
            logger.info(
                "CommandStore (Lakebase) configured (PGHOST=%s PGUSER=%s shift_id=%s); "
                "first SELECT will open the connection",
                pg_host, pg_user, shift_id,
            )
        except Exception as e:
            # Constructor only reads env vars, so this is unexpected; surface
            # loudly so the producer doesn't silently drop incidents.
            raise SystemExit(
                f"FATAL: CommandStore (Lakebase) construction failed: {e}. "
                f"Verify PGHOST / PGUSER / LAKEBASE_ENDPOINT env vars."
            ) from e
    else:
        logger.info(
            "CommandStore disabled (PGHOST=%s PGUSER=%s) — "
            "baseline-only mode; incidents triggered from the App will be ignored",
            bool(pg_host), bool(pg_user),
        )
    streams = ZerobusStreams(catalog, schema)

    # ── Tier-1 direct-write sink: producer → ai_serving.*_history ─────────
    # Decoupled from Zerobus so a Lakebase outage degrades only the live UI
    # to consumer-driven freshness; bronze + analytics stay healthy. Returns
    # None when not configured (workshop / smoke contexts) — the rest of the
    # producer just calls the (possibly-None) sink unconditionally below
    # via short-circuit guards.
    from src.streaming_sim.lakebase_sink import maybe_build_sink_from_env
    lakebase_sink = maybe_build_sink_from_env()
    if lakebase_sink is not None:
        logger.info("Tier-1 dual-write sink enabled (producer → ai_serving.*_history)")
    else:
        logger.info(
            "Tier-1 dual-write sink disabled — live UI will inherit Zerobus "
            "bronze materialization latency (~5-30 s)"
        )

    specs = _machine_specs()
    active_ids = set(_machine_ids())
    specs = [s for s in specs if s.id in active_ids]

    # Conveyor IDs are excluded from avg_speed_pct (their speed is throughput-/-
    # capacity = ~0.3 % which makes the line OEE artificially tiny).
    from src.data_generation.config import MACHINES as _MACHINES
    conveyor_ids = {m.id for m in _MACHINES if m.type == "conveyor"}

    state = build_line(specs)
    rng = random.Random(42)
    # why: fixed seed keeps scenario tests and demos reproducible.
    # Separate RNG for sensor-output jitter so it doesn't perturb the
    # deterministic cascade-state RNG (cascade tests depend on seed=42).
    sensor_jitter_rng = random.Random(1337)
    active_incidents: dict[str, ActiveIncident] = {}

    # ── Rehydrate active incidents from UC on startup ─────────────────────
    # The continuous job auto-restarts on crash. Without rehydration the new
    # process has an empty active_incidents dict, so any subsequent
    # resolve_requested rows can't find their target machines and the
    # recovery cascade silently no-ops — leaving cascaded machines stuck
    # forever from the operator's POV.
    #
    # ``--start-fresh`` (operator Clean Restart) skips this: the restart
    # endpoint already aborts every non-terminal scenario_command before
    # cycling the producer, so this query would normally return zero rows
    # anyway, but skipping is defence-in-depth against a races where an
    # admin re-fires an incident between abort and restart.
    #
    # This block used to reach for ``commands._cur`` — an attribute CommandStore
    # has never had — against ``{_fqn}.scenario_commands``, where ``_fqn`` is
    # already the fully-qualified table. Both raise, the bare except logs at
    # warning, and rehydration has therefore never once run: the symptom is an
    # incident whose command sits at 'active' while the machine it named runs
    # normally. It goes through the same shift-scoped SELECT as every other read.
    if commands is not None and not start_fresh:
        try:
            existing = commands.fetch_unresolved()
            for row in existing:
                cid = str(row["command_id"])
                sid = str(row.get("scenario_id") or "")
                target = str(row["target_machine_id"]) if row.get("target_machine_id") else None
                severity = str(row.get("severity") or "medium")
                affected = apply_incident(state, sid, target, severity)
                if affected:
                    active_incidents[cid] = ActiveIncident(
                        command_id=cid,
                        scenario_id=sid,
                        target_machines=affected,
                        severity=severity,
                    )
            if existing:
                logger.info(
                    "Rehydrated %d of %d active/resolving incidents after restart",
                    len(active_incidents), len(existing),
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("Rehydration of active incidents failed: %s", str(e)[:200])
    # ----------------------------------------------------------------------
    # Running tally of cases produced this shift. The end-of-line machine's
    # throughput (cases/min) integrated over each tick gives total cases.
    cumulative_cases: float = 0.0
    # Current fixed 8h shift window; ``cumulative_cases`` resets when this rolls
    # over (see the tick loop below) so the tally never exceeds one shift.
    current_window_start = _shift_window_start(_utc_now())
    # End-of-line machine = highest line_position (the one whose flow is the
    # actual line output rate).
    end_of_line_id = max(specs, key=lambda s: s.line_position).id

    # ── Rehydrate cumulative_cases from Lakebase on startup ──────────────
    # Same motivation as the inventory rehydration just below: this job
    # auto-restarts every ~2 h on the 30 MB stdout limit. Without seeding,
    # ``cumulative_cases`` resets to 0 each restart, producing a sawtooth
    # ``Cases`` tile that drops from e.g. 12,179 → 5 every two hours.
    #
    # Source: ``ai_serving.line_kpi_history`` (Lakebase) — NOT the bronze
    # ``line_kpi_bronze`` Delta table. Critical: the App's Clean Restart
    # wipes the Lakebase ``*_history`` rows for this shift via
    # ``_truncate_ai_serving_demo_state`` but does NOT wipe bronze. Reading
    # from Lakebase here means an empty result after Clean Restart → we
    # naturally fall through to the 0.0 init, no race with the producer
    # job's continuous-trigger auto-restart. Reading from bronze (the
    # earlier shape) inherited the previous shift's depleted state and
    # was the root cause of "Clean Restart doesn't reset cumulative cases /
    # inventory" reports.
    #
    # ``--start-fresh`` (operator Clean Restart) still skips for clarity
    # but the behaviour is identical to a non-flag warm start on an empty
    # Lakebase: the SELECT returns no rows.
    if lakebase_sink is not None and not start_fresh:
        try:
            # Scope rehydration to the CURRENT shift window: a warm restart
            # mid-shift resumes the tally, but a restart in a new window (no
            # rows since the boundary) correctly starts at 0 rather than
            # inheriting the previous shift's count.
            seed_cases = lakebase_sink.read_latest_cumulative_cases(
                shift_id, since_ts=current_window_start,
            )
            if seed_cases is not None:
                elapsed_min = (
                    _utc_now() - current_window_start
                ).total_seconds() / 60.0
                cumulative_cases = _plausible_seed_cases(seed_cases, elapsed_min)
                if abs(cumulative_cases - float(seed_cases)) > 0.5:
                    # Legacy lifetime tally (or other implausible value) — the
                    # clamp replaced it with an elapsed-time estimate so the
                    # Cases tile is believable NOW instead of at the next window
                    # boundary. Going forward the producer writes small per-shift
                    # values, so subsequent restarts rehydrate cleanly.
                    logger.warning(
                        "Discarded implausible rehydrated cumulative_cases=%.0f "
                        "(> %.0f ceiling) for shift_id=%s; seeded elapsed "
                        "estimate %.0f",
                        seed_cases, SHIFT_PLANNED_CASES * 1.5, shift_id,
                        cumulative_cases,
                    )
                else:
                    logger.info(
                        "Rehydrated cumulative_cases=%.1f from Lakebase for "
                        "shift_id=%s (window_start=%s)",
                        cumulative_cases, shift_id,
                        current_window_start.isoformat(),
                    )
        except Exception as e:  # noqa: BLE001
            logger.warning("cumulative_cases rehydration failed: %s", str(e)[:200])

    # Inventory state (shared tick logic in src/streaming_sim/inventory_engine.py).
    from src.streaming_sim.inventory_engine import (
        init_inventory,
        restock_for_machines,
        snapshot_inventory,
        tick_inventory,
    )
    inventory, material_by_machine = init_inventory()

    # ── Rehydrate inventory state from Lakebase on startup ───────────────
    # Source switched from ``inventory_status_bronze`` (Delta, NOT wiped by
    # Clean Restart) to ``ai_serving.inventory_status_history`` (Lakebase,
    # wiped by Clean Restart). Reason: the previous shape caused the new
    # producer run launched by Clean Restart to rehydrate from stale bronze
    # rows, so the UI showed "previous values, depleting" instead of a
    # fresh full-stock baseline. This change makes Clean Restart correct
    # by construction — the wipe is the rehydration source, period.
    #
    # Warm restarts (no Clean) keep the same behaviour they had before:
    # Lakebase has recent rows → producer resumes from there → no jump in
    # ``on_hand_qty`` across the restart boundary.
    if lakebase_sink is not None and not start_fresh:
        try:
            latest_rows = lakebase_sink.read_latest_inventory(shift_id)
            seeded = 0
            for row in latest_rows:
                mid = str(row.get("material_id") or "")
                inv = inventory.get(mid)
                if inv is None:
                    continue
                qty = row.get("on_hand_qty")
                if qty is None:
                    continue
                inv.on_hand_qty = float(qty)
                seeded += 1
            if seeded:
                logger.info(
                    "Rehydrated %d/%d materials from Lakebase after restart",
                    seeded, len(inventory),
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("Inventory rehydration failed: %s", str(e)[:200])

    # Sensor synthesizer — turns per-machine state into one row per defined
    # sensor per tick (~66 rows/tick across 19 machines). The SENSORS catalog
    # in src/data_generation/config.py is the source of truth.
    from src.streaming_sim.sensor_synthesizer import SensorSynthesizer
    sensors_synth = SensorSynthesizer(seed=42)
    logger.info("Sensor synthesizer ready: %d sensor definitions across all machines",
                sensors_synth.sensor_count)

    # Shift-to-date availability/performance. The producer is the only caller
    # that sees every tick of a shift, so it is the only one that can report
    # what ``*_to_now`` claims; see line_kpi_derived.ShiftKpiAccumulator.
    kpi_accumulator = ShiftKpiAccumulator(shift_id=shift_id)

    logger.info("Producer starting: catalog=%s.%s shift=%s tick=%ss machines=%d start_fresh=%s",
                catalog, schema, shift_id, tick_s, len(specs), start_fresh)

    try:
        while True:
            # Wall-clock anchored cadence: total cycle time = max(tick_s,
            # processing_time). Without this, total cycle = processing +
            # tick_s and the producer drifts to ~0.6 Hz when Zerobus +
            # Lakebase round-trips dominate (each adds 80-200 ms). The
            # SSE cursor walks at 1 Hz, so any time we drift below 1 Hz
            # the cursor outpaces fresh data and the live UI shows
            # "waiting for new value".
            loop_start = time.monotonic()
            now = _utc_now()
            now_us = _ts_us(now)

            # ── Shift-window rollover → reset the per-shift case tally ──
            win_start = _shift_window_start(now)
            if win_start != current_window_start:
                logger.info(
                    "Shift window rollover %s → %s; resetting cumulative_cases "
                    "from %.1f to 0",
                    current_window_start.isoformat(), win_start.isoformat(),
                    cumulative_cases,
                )
                cumulative_cases = 0.0
                # Same boundary, same reason: shift-to-date OEE must not carry
                # the previous shift's downtime into this one.
                kpi_accumulator.reset()
                current_window_start = win_start

            # ── Poll for new incidents ────────────────────────────────
            if commands is not None:
                for cmd in commands.fetch_pending():
                    cid = str(cmd["command_id"])
                    sid = str(cmd.get("scenario_id") or "")
                    target = str(cmd["target_machine_id"]) if cmd.get("target_machine_id") else None
                    severity = str(cmd.get("severity") or "medium")

                    affected = apply_incident(state, sid, target, severity)
                    if affected:
                        incident = ActiveIncident(
                            command_id=cid,
                            scenario_id=sid,
                            target_machines=affected,
                            severity=severity,
                        )
                        active_incidents[cid] = incident
                        _emit_incident_start_event(streams, shift_id, now, incident, lakebase_sink)
                        commands.set_status(cid, "active")
                        logger.info("Incident started: %s on %s", sid, affected)
                    else:
                        # apply_incident matched no machine: an unknown
                        # scenario_id, or a target that is not on this line.
                        # The row goes terminal either way, but silently doing
                        # so is how "I clicked Trigger and nothing happened"
                        # became unattributable — say which input was wrong.
                        logger.warning(
                            "Command %s applied to nothing (scenario=%r target=%r): "
                            "unknown scenario or a machine not on this line; "
                            "marking resolved",
                            cid, sid, target,
                        )
                        commands.set_status(cid, "resolved")

            # ── Poll for resolve requests ─────────────────────────────
            if commands is not None:
                for cmd in commands.fetch_resolve_requested():
                    cid = str(cmd["command_id"])
                    commands.set_status(cid, "resolving")
                    incident = incident_for_resolve(cmd, active_incidents, state)
                    if incident:
                        resolve_incident(state, incident.target_machines)
                        if incident.scenario_id == "material_shortage":
                            restock_for_machines(inventory, material_by_machine, incident.target_machines)
                        _emit_incident_resolved_event(streams, shift_id, now, incident, lakebase_sink)
                        logger.info("Incident resolved: %s", incident.scenario_id)
                    commands.set_status(cid, "resolved")

            # ── Baseline noise on healthy machines ────────────────────
            _add_baseline_noise(state, rng)

            # ── Re-apply active faults (sticky until resolved) ────────
            for incident in active_incidents.values():
                for mid in incident.target_machines:
                    apply_incident(state, incident.scenario_id, mid, incident.severity)

            # ── Cascade tick ──────────────────────────────────────────
            cascade_tick(specs, state, tick_seconds=tick_s)

            # ── Accumulate cases produced this tick ───────────────────
            # End-of-line throughput is in BOTTLES/min (the cascade tracks
            # bottle-equivalent flow end to end), so divide by BOTTLES_PER_CASE
            # to get cases/min before integrating over tick_s. Omitting the
            # divide overcounts ~24x (bottles counted as cases) — the cause of
            # the "31,106 cases vs 8,400 plan" tile bug. The tally stays a float
            # for sub-case precision across ticks; it's floored to a whole count
            # only at emit (see line_kpi_derived.compute_tier1_line_kpi_row).
            bottles_this_tick = state.machines[end_of_line_id].throughput * (tick_s / 60.0)
            cases_this_tick = bottles_this_tick / BOTTLES_PER_CASE
            cumulative_cases += max(0.0, cases_this_tick)

            # ── Emit machine state (with realistic ~±1.5% sensor jitter) ─
            rows = snapshot_to_dicts(state, shift_id, now_us, rng=sensor_jitter_rng)
            for row in rows:
                _ingest(streams, row, label="machine_state")

            # ── Emit line KPI (aggregated from the SAME jittered rows so
            # ── total_throughput / avg_speed_pct also wobble realistically) ─
            kpi = compute_line_kpi(
                state, shift_id, now_us,
                exclude_from_speed=conveyor_ids,
                cumulative_cases=cumulative_cases,
                accumulator=kpi_accumulator,
                machine_rows=rows,
            )
            _ingest(streams, kpi, label="line_kpi")

            # ── Tick + emit inventory ─────────────────────────────────
            shortage_machines = {
                m for inc in active_incidents.values()
                if inc.scenario_id == "material_shortage"
                for m in inc.target_machines
            }
            tick_inventory(
                inventory,
                machine_status_for={mid: ms.status for mid, ms in state.machines.items()},
                machine_speed_for={mid: ms.speed_pct for mid, ms in state.machines.items()},
                tick_seconds=tick_s,
                shortage_machines=shortage_machines,
            )
            # Compute snapshot ONCE: re-used by both the Zerobus loop below
            # and the Lakebase dual-write after the flush. (Cheap pure-Python
            # dict construction; snapshotting twice was the previous shape.)
            inv_snapshot = snapshot_inventory(inventory, shift_id)
            for inv_row in inv_snapshot:
                # Bronze schema: include event-time ts for downstream watermarking.
                payload = {**inv_row, "ts": now_us}
                _ingest(streams, payload, label="inventory_status")

            # ── Emit telemetry: every defined sensor per machine per tick ─
            # Replaces the legacy hand-picked 6-row emission. The SENSORS
            # catalog (~66 rows/tick) becomes the raw signal that the streaming
            # quality-gate job derives machine_state / line_kpi gold from.
            for sr in sensors_synth.emit_all(state.machines, tick_seconds=tick_s):
                _ingest(streams, {
                    "shift_id": shift_id,
                    "timestamp": now_us,
                    "machine_id": sr.machine_id,
                    "sensor_name": sr.sensor_name,
                    "value": sr.value,
                }, label="telemetry")

            # ── Emit QC during quality incidents ──────────────────────
            for incident in active_incidents.values():
                if incident.scenario_id in ("quality_deviation", "contamination"):
                    for mid in incident.target_machines:
                        ms = state.machines.get(mid)
                        if ms and ms.status == "degraded":
                            val = 330.0 + rng.uniform(-3.0, 6.0)
                            _ingest(streams, {
                                "check_id": _iso_id("QC"),
                                "shift_id": shift_id,
                                "machine_id": mid,
                                "timestamp": now_us,
                                "parameter": "fill_volume_ml",
                                "value": float(val),
                                "spec_min": 325.0,
                                "spec_max": 335.0,
                                "result": "fail" if val > 335.0 or val < 325.0 else "pass",
                            }, label="quality_checks")

            streams.flush_all()

            # ── Tier-1 direct-write to Lakebase ai_serving.*_history ─────
            # Single transaction for all three per-tick INSERTs (machine
            # state + KPI + inventory) — saves ~2 commit round-trips vs
            # three separate write_* calls (~160 ms / tick on the Lakebase
            # Autoscale endpoint). All failures are swallowed inside the
            # sink so the Zerobus path (analytics) is never blocked by a
            # Lakebase hiccup.
            if lakebase_sink is not None:
                lakebase_sink.write_tick(
                    machine_rows=rows,
                    kpi_row=kpi,
                    inventory_rows=inv_snapshot,
                    ts_us=now_us,
                )

            elapsed = time.monotonic() - loop_start
            if elapsed < tick_s:
                time.sleep(tick_s - elapsed)
            # else: tick took longer than the budget — proceed immediately
            # so total cycle stays as close to 1 Hz as possible.

    finally:
        streams.close_all()
        if commands is not None:
            commands.close()
        if lakebase_sink is not None:
            lakebase_sink.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    main()
