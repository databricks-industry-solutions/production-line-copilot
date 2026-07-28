"""Real-time production line simulator + Zerobus ingest producer.

This runs as a long-lived process (recommended as a Databricks Job on classic compute).
It:
- Polls `${UC_CATALOG}.${UC_SCHEMA}.scenario_commands` for new scenario triggers
- Generates synthetic per-machine state records continuously
- Ingests records into a Bronze Delta table via Zerobus Ingest (JSON mode for v1)

Environment variables (required):
- ZEROBUS_SERVER_ENDPOINT: e.g. https://<workspace_id>.zerobus.<region>.cloud.databricks.com
- DATABRICKS_WORKSPACE_URL: e.g. https://<your-workspace>.cloud.databricks.com
- DATABRICKS_CLIENT_ID / DATABRICKS_CLIENT_SECRET: service principal for ingest
- UC_CATALOG / UC_SCHEMA
- DATABRICKS_HOST / DATABRICKS_WAREHOUSE_ID: for polling + marking scenario commands (SQL)

Optional:
- SIM_SHIFT_ID (default: shift_1)
- SIM_TICK_SECONDS (default: 2)
- SIM_MACHINES (comma-separated machine ids; default: all from src.data_generation.config.MACHINES)
"""

from __future__ import annotations

import pathlib
import sys


def _ensure_src_on_path() -> None:
    """Ensure bundle root (parent of `src/`) is on `sys.path`.

    See `producer.py` for the serverless `__file__` caveat.
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
import json
import os
import random
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from zerobus.sdk.shared import RecordType, StreamConfigurationOptions, TableProperties
from zerobus.sdk.sync import ZerobusSdk

from src.data_generation.config import MACHINES
from src.streaming_sim.line_kpi_derived import compute_tier1_line_kpi_row


def _env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, default)
    if v is None or v == "":
        raise ValueError(f"Missing required env var: {name}")
    return v


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)

def _iso_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


@dataclass
class ActiveScenario:
    scenario_id: str
    start_time: datetime
    end_time: datetime
    severity: str
    target_machine_id: str | None
    params: dict[str, Any]


class ScenarioEngine:
    def __init__(self) -> None:
        self.active: list[ActiveScenario] = []
        self._open_events: dict[str, dict[str, Any]] = {}

    def add(self, s: ActiveScenario) -> None:
        self.active.append(s)

    def prune(self, now: datetime) -> None:
        self.active = [s for s in self.active if s.end_time > now]

    def scenarios_starting(self, now: datetime, window_s: int = 3) -> list[ActiveScenario]:
        """Return scenarios whose start_time is within [now-window_s, now+window_s]."""
        out = []
        for s in self.active:
            if abs((s.start_time - now).total_seconds()) <= window_s:
                out.append(s)
        return out

    def scenarios_ending(self, now: datetime, window_s: int = 3) -> list[ActiveScenario]:
        out = []
        for s in self.active:
            if abs((s.end_time - now).total_seconds()) <= window_s:
                out.append(s)
        return out

    def apply(self, now: datetime, machine_id: str, base: dict[str, Any]) -> dict[str, Any]:
        out = dict(base)
        for s in self.active:
            if not (s.start_time <= now <= s.end_time):
                continue
            if s.target_machine_id and s.target_machine_id != machine_id:
                continue

            sid = s.scenario_id
            sev = (s.severity or "medium").lower()

            if sid == "filler_down" and machine_id == "FILLER":
                out["status"] = "stopped"
                out["throughput_per_min"] = 0.0
                out["speed_pct"] = 0.0
                out["alarm_count"] = int(out.get("alarm_count", 0)) + (3 if sev in ("high", "critical") else 1)

            elif sid == "upstream_delay" and machine_id in ("DEPALLETIZER", "CONVEYOR_1", "UNCASER"):
                out["status"] = "stopped"
                out["throughput_per_min"] = 0.0
                out["speed_pct"] = 0.0

            elif sid == "quality_deviation" and machine_id == "FILLER":
                # Keep running but introduce disturbance and alarms.
                out["status"] = "degraded"
                out["speed_pct"] = max(0.0, float(out.get("speed_pct", 100.0)) - (10.0 if sev in ("high", "critical") else 5.0))
                out["alarm_count"] = int(out.get("alarm_count", 0)) + 2

        return out


def _ordered_machines(machine_ids: list[str]) -> list[str]:
    pos = {m.id: m.line_position for m in MACHINES}
    return sorted(machine_ids, key=lambda mid: pos.get(mid, 999))


def _machine_capacity_per_min(machine_id: str) -> float:
    """Approximate capacity in items/min for flow calculations."""
    m = next((m for m in MACHINES if m.id == machine_id), None)
    if not m:
        return 200.0
    if m.nominal_speed is None:
        # Cycle-time machines treated as non-bottlenecks for demo
        return 300.0
    # For conveyors nominal is m/min; keep them non-bottleneck.
    if m.type == "conveyor":
        return 9999.0
    # For case packer nominal is cases/min; scale to items/min (24 bottles per case).
    if m.id == "CASE_PACKER":
        return float(m.nominal_speed) * 24.0
    return float(m.nominal_speed)


def _base_speed_pct(now: datetime, mid: str, rng: random.Random) -> float:
    # Gentle drift + noise, bounded.
    drift = 2.0 * (1.0 if (now.minute % 10) < 5 else -1.0)
    noise = rng.uniform(-1.5, 1.5)
    return max(70.0, min(105.0, 100.0 + drift + noise))


def _default_buffers(machine_ids: list[str]) -> dict[tuple[str, str], float]:
    """Buffer between adjacent machines (items)."""
    ordered = _ordered_machines(machine_ids)
    buffers: dict[tuple[str, str], float] = {}
    for a, b in zip(ordered, ordered[1:]):
        # Small WIP buffers; conveyors slightly larger.
        cap = 600.0 if "CONVEYOR" in a or "CONVEYOR" in b else 300.0
        buffers[(a, b)] = cap * 0.4
    return buffers


def _apply_cascades(
    *,
    ordered: list[str],
    base_state: dict[str, dict[str, Any]],
    buffers: dict[tuple[str, str], float],
    tick_minutes: float,
) -> dict[str, dict[str, Any]]:
    """Compute flow + derived statuses (running/starved) from local states + buffers."""
    out = {k: dict(v) for k, v in base_state.items()}

    # First pass: capacities (0 if stopped), convert speed_pct into capacity scaling.
    cap: dict[str, float] = {}
    for mid in ordered:
        st = out[mid].get("status", "running")
        if st == "stopped":
            cap[mid] = 0.0
            continue
        nominal = _machine_capacity_per_min(mid)
        sp = float(out[mid].get("speed_pct", 100.0) or 0.0) / 100.0
        cap[mid] = max(0.0, nominal * sp)

    # Forward simulate flows.
    # Flow at machine i limited by its cap, upstream supply (buffer), and downstream acceptance.
    flow: dict[str, float] = {mid: 0.0 for mid in ordered}

    for i, mid in enumerate(ordered):
        if i == 0:
            supply = cap[mid]  # source unlimited; first machine self-limits
        else:
            prev = ordered[i - 1]
            supply = min(cap[mid], flow[prev])  # items arriving from upstream

        # Downstream constraint: if next is stopped, we can still run until buffer is full.
        if i + 1 < len(ordered):
            nxt = ordered[i + 1]
            buf_key = (mid, nxt)
            buf = buffers.get(buf_key, 0.0)
            # If next has 0 cap (stopped), allow producing into buffer up to remaining space.
            if cap[nxt] == 0.0:
                remaining = max(0.0, 600.0 - buf)  # treat 600 as hard max for safety
                f = min(supply, remaining / max(tick_minutes, 1e-6))
                buffers[buf_key] = min(600.0, buf + f * tick_minutes)
                flow[mid] = f
            else:
                # Next can accept up to its cap; buffer used to smooth starvation.
                accept = cap[nxt]
                # If upstream supply less than accept, drain buffer.
                if supply < accept and buf > 0:
                    pull = min(accept - supply, buf / max(tick_minutes, 1e-6))
                    buffers[buf_key] = max(0.0, buf - pull * tick_minutes)
                    flow[mid] = supply + pull
                else:
                    # If supply exceeds accept, fill buffer.
                    push = max(0.0, supply - accept)
                    buffers[buf_key] = min(600.0, buf + push * tick_minutes)
                    flow[mid] = min(supply, accept)
        else:
            flow[mid] = supply

    # Second pass: derive starvation and set throughput.
    for i, mid in enumerate(ordered):
        out[mid]["throughput_per_min"] = float(flow[mid])
        if out[mid].get("status") == "stopped":
            continue
        if i > 0 and flow[ordered[i - 1]] <= 1e-6 and cap[mid] > 0:
            # upstream not delivering -> starved
            out[mid]["status"] = "starved"
            out[mid]["speed_pct"] = max(0.0, float(out[mid].get("speed_pct", 100.0)) * 0.1)
        # If this machine is producing but downstream is stopped and buffer is near full, mark degraded (blocked).
        if i + 1 < len(ordered):
            nxt = ordered[i + 1]
            buf_key = (mid, nxt)
            buf = buffers.get(buf_key, 0.0)
            if cap[nxt] == 0.0 and buf >= 540.0 and out[mid].get("status") == "running":
                out[mid]["status"] = "degraded"
                out[mid]["speed_pct"] = max(0.0, float(out[mid].get("speed_pct", 100.0)) * 0.5)

    return out


class CommandStore:
    """Poll scenario commands and mark them processed via SQL Warehouse."""

    def __init__(self, host: str, warehouse_id: str, catalog: str, schema: str) -> None:
        from databricks import sql as dbsql

        self._dbsql = dbsql
        self._conn = dbsql.connect(
            server_hostname=host.replace("https://", ""),
            http_path=f"/sql/1.0/warehouses/{warehouse_id}",
        )
        self._cur = self._conn.cursor()
        self._catalog = catalog
        self._schema = schema

    def close(self) -> None:
        self._cur.close()
        self._conn.close()

    def fetch_pending(self, limit: int = 50) -> list[dict[str, Any]]:
        tbl = f"{self._catalog}.{self._schema}.scenario_commands"
        self._cur.execute(
            f"""
            SELECT
              command_id,
              created_at,
              created_by,
              shift_id,
              scenario_id,
              start_time,
              duration_seconds,
              severity,
              target_machine_id,
              params_json
            FROM {tbl}
            WHERE (status IS NULL OR status = 'pending')
            ORDER BY created_at ASC
            LIMIT {int(limit)}
            """
        )
        cols = [c[0] for c in self._cur.description]
        rows = []
        for r in self._cur.fetchall():
            rows.append(dict(zip(cols, r)))
        return rows

    def mark_processed(self, command_id: str, processed_by: str) -> None:
        tbl = f"{self._catalog}.{self._schema}.scenario_commands"
        self._cur.execute(
            f"""
            UPDATE {tbl}
            SET status = 'processed',
                processed_at = current_timestamp(),
                processed_by = %(pb)s
            WHERE command_id = %(cid)s
            """,
            {"cid": command_id, "pb": processed_by},
        )


def _machine_ids() -> list[str]:
    raw = os.environ.get("SIM_MACHINES", "").strip()
    if raw:
        return [m.strip() for m in raw.split(",") if m.strip()]
    return [m.id for m in MACHINES]


def _baseline(machine_id: str) -> dict[str, Any]:
    # Simple baseline model: most machines running near nominal; conveyors are rarely bottlenecks.
    nominal = next((m.nominal_speed for m in MACHINES if m.id == machine_id), None)
    speed_pct = 100.0
    status = "running"
    alarms = 0

    # Convert nominal into throughput_per_min loosely (demo semantics).
    if nominal is None:
        tput = 35.0
    else:
        tput = float(nominal)

    return {
        "machine_id": machine_id,
        "status": status,
        "throughput_per_min": tput,
        "speed_pct": speed_pct,
        "alarm_count": alarms,
    }


def _apply_cli_catalog_schema() -> None:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--catalog", default=None)
    p.add_argument("--schema", default=None)
    ns, _unknown = p.parse_known_args()
    if ns.catalog:
        os.environ["UC_CATALOG"] = ns.catalog
    if ns.schema:
        os.environ["UC_SCHEMA"] = ns.schema


def _aggregate_line_kpi(
    shift_id: str, ts_min: datetime, derived: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Match ``line_kpi_bronze`` / Gold schema for the quality-gate (Tier-1 OEE columns included)."""
    running = stopped = degraded = starved = 0
    total_tput = 0.0
    speed_sum = 0.0
    speed_count = 0
    for mid, rec in derived.items():
        total_tput += float(rec.get("throughput_per_min") or 0.0)
        st = str(rec.get("status") or "running")
        if st == "running":
            running += 1
        elif st == "stopped":
            stopped += 1
        elif st == "degraded":
            degraded += 1
        elif st in ("starved", "blocked"):
            starved += 1
        if "CONVEYOR" not in mid:
            speed_sum += float(rec.get("speed_pct") or 0.0)
            speed_count += 1
    avg_sp = round(speed_sum / max(speed_count, 1), 1)
    return compute_tier1_line_kpi_row(
        shift_id=shift_id,
        ts=ts_min,
        machines_running=int(running),
        machines_stopped=int(stopped),
        machines_degraded=int(degraded),
        machines_starved=int(starved),
        total_throughput=round(total_tput, 2),
        avg_speed_pct=avg_sp,
        cumulative_cases=0.0,
    )


def main() -> None:
    _apply_cli_catalog_schema()
    catalog = os.environ.get("UC_CATALOG", "main")
    schema = os.environ.get("UC_SCHEMA", "line_manager_ai_kit")

    # Command polling
    host = _env("DATABRICKS_HOST")
    warehouse_id = _env("DATABRICKS_WAREHOUSE_ID")
    command_store = CommandStore(host, warehouse_id, catalog, schema)

    # Zerobus stream
    server_endpoint = _env("ZEROBUS_SERVER_ENDPOINT")
    workspace_url = _env("DATABRICKS_WORKSPACE_URL")
    client_id = _env("DATABRICKS_CLIENT_ID")
    client_secret = _env("DATABRICKS_CLIENT_SECRET")

    state_table = f"{catalog}.{schema}.machine_state_bronze"
    events_table = f"{catalog}.{schema}.event_log_bronze"
    qc_table = f"{catalog}.{schema}.quality_checks_bronze"
    telem_table = f"{catalog}.{schema}.telemetry_bronze"
    kpi_table = f"{catalog}.{schema}.line_kpi_bronze"

    sdk = ZerobusSdk(server_endpoint, workspace_url)
    options = StreamConfigurationOptions(record_type=RecordType.JSON)
    state_stream = sdk.create_stream(client_id, client_secret, TableProperties(state_table), options)
    events_stream = sdk.create_stream(client_id, client_secret, TableProperties(events_table), options)
    qc_stream = sdk.create_stream(client_id, client_secret, TableProperties(qc_table), options)
    telem_stream = sdk.create_stream(client_id, client_secret, TableProperties(telem_table), options)
    kpi_stream = sdk.create_stream(client_id, client_secret, TableProperties(kpi_table), options)

    shift_id = os.environ.get("SIM_SHIFT_ID", "shift_1")
    tick_s = float(os.environ.get("SIM_TICK_SECONDS", "2"))
    producer_id = f"realtime_producer:{uuid.uuid4()}"

    engine = ScenarioEngine()
    machines = _machine_ids()
    ordered = _ordered_machines(machines)
    buffers = _default_buffers(machines)
    rng = random.Random(42)

    try:
        while True:
            now = _utc_now()
            engine.prune(now)

            # Apply any new commands (at-least-once; mark processed after accepted)
            for cmd in command_store.fetch_pending():
                params = {}
                if cmd.get("params_json"):
                    try:
                        params = json.loads(cmd["params_json"])
                    except Exception:
                        params = {}

                start = cmd.get("start_time") or now
                if isinstance(start, str):
                    start = datetime.fromisoformat(start.replace("Z", "+00:00"))

                dur = int(cmd.get("duration_seconds") or 300)
                end = start + timedelta(seconds=dur)

                engine.add(
                    ActiveScenario(
                        scenario_id=str(cmd.get("scenario_id") or ""),
                        start_time=start,
                        end_time=end,
                        severity=str(cmd.get("severity") or "medium"),
                        target_machine_id=(str(cmd["target_machine_id"]) if cmd.get("target_machine_id") else None),
                        params=params,
                    )
                )

                command_store.mark_processed(str(cmd["command_id"]), processed_by=producer_id)

            # Emit one record per machine per tick
            ts_min = now.replace(second=0, microsecond=0)
            tick_minutes = tick_s / 60.0

            base_state: dict[str, dict[str, Any]] = {}
            for mid in ordered:
                base = _baseline(mid)
                # realistic baseline speed + small noise
                base["speed_pct"] = _base_speed_pct(now, mid, rng)
                # apply direct scenario effects (e.g., FILLER stopped/degraded)
                base_state[mid] = engine.apply(now, mid, base)

            # Apply line cascades to derive starvation/blocking-like behavior and consistent flow.
            derived = _apply_cascades(ordered=ordered, base_state=base_state, buffers=buffers, tick_minutes=tick_minutes)

            # Scenario-driven events (start/end markers) + QC + telemetry disturbances.
            for s in engine.scenarios_starting(now):
                sid = s.scenario_id
                target_mid = s.target_machine_id or ("FILLER" if sid in ("filler_down", "quality_deviation") else ordered[0])
                event_id = _iso_id("EVT")
                engine._open_events[event_id] = {"scenario_id": sid, "start": s.start_time, "machine_id": target_mid, "severity": s.severity}

                events_stream.ingest_record_offset(
                    {
                        "event_id": event_id,
                        "shift_id": shift_id,
                        "machine_id": target_mid,
                        "event_type": "major_stop" if sid == "filler_down" else "quality_hold" if sid == "quality_deviation" else "upstream_delay",
                        "severity": "critical" if sid == "filler_down" else "major" if sid == "quality_deviation" else "major",
                        "timestamp_start": s.start_time,
                        "timestamp_end": None,
                        "duration_minutes": 0.0,
                        "root_cause": "mechanical" if sid == "filler_down" else "quality" if sid == "quality_deviation" else "supply",
                        "description": f"Scenario triggered: {sid}",
                        "corrective_action": "",
                        "resolution_minutes": 0.0,
                        "outcome_oee_impact": 0.0,
                        "outcome_description": "",
                    }
                )

            for s in engine.scenarios_ending(now):
                # Close any open events for this scenario (best-effort).
                for eid, meta in list(engine._open_events.items()):
                    if meta.get("scenario_id") != s.scenario_id:
                        continue
                    if abs((meta.get("start") - s.start_time).total_seconds()) > 10:
                        continue
                    start_ts: datetime = meta["start"]
                    end_ts: datetime = s.end_time
                    dur_min = max(0.0, (end_ts - start_ts).total_seconds() / 60.0)
                    events_stream.ingest_record_offset(
                        {
                            "event_id": eid,
                            "shift_id": shift_id,
                            "machine_id": meta.get("machine_id"),
                            "event_type": "resolved",
                            "severity": "info",
                            "timestamp_start": start_ts,
                            "timestamp_end": end_ts,
                            "duration_minutes": dur_min,
                            "root_cause": meta.get("scenario_id"),
                            "description": f"Scenario resolved: {meta.get('scenario_id')}",
                            "corrective_action": "Operator action",
                            "resolution_minutes": dur_min,
                            "outcome_oee_impact": -0.02 if meta.get("scenario_id") == "filler_down" else -0.01,
                            "outcome_description": "Recovered",
                        }
                    )
                    engine._open_events.pop(eid, None)

            for mid in ordered:
                rec = derived[mid]
                rec.update(
                    {
                    "shift_id": shift_id,
                    "ts": ts_min,
                    }
                )
                state_stream.ingest_record_offset(rec)

                # Lightweight telemetry: a few key sensors per tick for trend panels.
                if mid in ("FILLER", "LABELLER", "CASE_PACKER"):
                    base_val = rec.get("throughput_per_min", 0.0) or 0.0
                    telem_stream.ingest_record_offset(
                        {"shift_id": shift_id, "timestamp": now, "machine_id": mid, "sensor_name": "throughput_per_min", "value": float(base_val)}
                    )
                    telem_stream.ingest_record_offset(
                        {"shift_id": shift_id, "timestamp": now, "machine_id": mid, "sensor_name": "speed_pct", "value": float(rec.get("speed_pct", 0.0) or 0.0)}
                    )

                # Quality checks during quality deviation scenario.
                if mid == "FILLER" and rec.get("status") == "degraded":
                    val = 330.0 + rng.uniform(-3.0, 6.0)
                    qc_stream.ingest_record_offset(
                        {
                            "check_id": _iso_id("QC"),
                            "shift_id": shift_id,
                            "machine_id": mid,
                            "timestamp": now,
                            "parameter": "fill_volume_ml",
                            "value": float(val),
                            "spec_min": 325.0,
                            "spec_max": 335.0,
                            "result": "fail" if val > 335.0 or val < 325.0 else "pass",
                        }
                    )

            kpi_stream.ingest_record_offset(_aggregate_line_kpi(shift_id, ts_min, derived))

            state_stream.flush()
            events_stream.flush()
            qc_stream.flush()
            telem_stream.flush()
            kpi_stream.flush()
            time.sleep(tick_s)
    finally:
        try:
            state_stream.close()
            events_stream.close()
            qc_stream.close()
            telem_stream.close()
            kpi_stream.close()
        finally:
            command_store.close()


if __name__ == "__main__":
    main()

