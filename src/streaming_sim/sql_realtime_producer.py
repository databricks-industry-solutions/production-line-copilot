"""Realtime simulator that writes directly to Delta tables via SQL Warehouse.

Use this when Zerobus credentials / endpoint are not available, but you still want
the app to show continuously updating "realtime" values in UC tables.

It writes to:
- {catalog}.{schema}.machine_state_bronze
- {catalog}.{schema}.event_log_bronze
- {catalog}.{schema}.quality_checks_bronze
- {catalog}.{schema}.telemetry_bronze
and polls {catalog}.{schema}.scenario_commands for disturbances.
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

import json
import os
import random
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from databricks.sdk import WorkspaceClient

from src.data_generation.config import MACHINES


def _env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, default)
    if v is None or v == "":
        raise ValueError(f"Missing required env var: {name}")
    return v


def _machine_ids() -> list[str]:
    raw = os.environ.get("SIM_MACHINES", "").strip()
    if raw:
        return [m.strip() for m in raw.split(",") if m.strip()]
    return [m.id for m in MACHINES]


def _ordered_machines(machine_ids: list[str]) -> list[str]:
    """Use known line order when possible; otherwise keep given order."""
    known = [m.id for m in MACHINES if m.id in set(machine_ids)]
    if known:
        # Append unknown machines at end.
        unknown = [m for m in machine_ids if m not in set(known)]
        return known + unknown
    return machine_ids


def _baseline(machine_id: str) -> dict[str, Any]:
    nominal = next((m.nominal_speed for m in MACHINES if m.id == machine_id), None)
    tput = float(nominal) if nominal is not None else 35.0
    return {
        "machine_id": machine_id,
        "status": "running",
        "throughput_per_min": tput,
        "speed_pct": 100.0,
        "alarm_count": 0,
    }


def _base_speed_pct(now: datetime, machine_id: str, rng: random.Random) -> float:
    # Gentle drift + noise
    base = 97.0 + 3.0 * rng.random()
    jitter = rng.uniform(-1.0, 1.0)
    return max(0.0, min(100.0, base + jitter))


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

    def add(self, s: ActiveScenario) -> None:
        self.active.append(s)

    def prune(self, now: datetime) -> None:
        self.active = [s for s in self.active if s.end_time > now]

    def apply(self, now: datetime, machine_id: str, base: dict[str, Any]) -> dict[str, Any]:
        out = dict(base)
        for s in self.active:
            if not (s.start_time <= now <= s.end_time):
                continue
            if s.target_machine_id and s.target_machine_id != machine_id:
                continue
            sid = s.scenario_id
            sev = s.severity
            if sid == "filler_down" and machine_id == "FILLER":
                out["status"] = "stopped"
                out["throughput_per_min"] = 0.0
                out["speed_pct"] = 0.0
                out["alarm_count"] = int(out.get("alarm_count", 0)) + (3 if sev in ("high", "critical") else 1)
            if sid == "quality_deviation" and machine_id == "FILLER":
                out["status"] = "degraded"
                out["throughput_per_min"] = float(out.get("throughput_per_min", 0.0)) * 0.8
                out["alarm_count"] = int(out.get("alarm_count", 0)) + 1
            if sid == "upstream_delay" and machine_id in ("DEPALLETIZER", "RINSE", "FILLER_FEED"):
                out["status"] = "degraded"
                out["throughput_per_min"] = float(out.get("throughput_per_min", 0.0)) * 0.7
        return out


def _apply_simple_cascades(ordered: list[str], state: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out = {k: dict(v) for k, v in state.items()}
    # If downstream is stopped, upstream gets blocked/degraded. If upstream is stopped, downstream gets starved.
    for i, mid in enumerate(ordered):
        if i + 1 < len(ordered):
            nxt = ordered[i + 1]
            if out.get(nxt, {}).get("status") == "stopped" and out[mid].get("status") == "running":
                out[mid]["status"] = "degraded"
                out[mid]["throughput_per_min"] = float(out[mid].get("throughput_per_min", 0.0)) * 0.5
                out[mid]["speed_pct"] = float(out[mid].get("speed_pct", 0.0)) * 0.6
        if i - 1 >= 0:
            prev = ordered[i - 1]
            if out.get(prev, {}).get("status") == "stopped" and out[mid].get("status") == "running":
                out[mid]["status"] = "starved"
                out[mid]["throughput_per_min"] = float(out[mid].get("throughput_per_min", 0.0)) * 0.2
                out[mid]["speed_pct"] = float(out[mid].get("speed_pct", 0.0)) * 0.4
    return out


class CommandStore:
    def __init__(self, host: str, warehouse_id: str, catalog: str, schema: str) -> None:
        from databricks import sql as dbsql

        token = os.environ.get("DATABRICKS_TOKEN", "") or _oauth_token()
        self._conn = dbsql.connect(
            server_hostname=host.replace("https://", ""),
            http_path=f"/sql/1.0/warehouses/{warehouse_id}",
            access_token=token,
            catalog=catalog,
            schema=schema,
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
            SELECT command_id, created_at, created_by, shift_id, scenario_id, start_time, duration_seconds,
                   severity, target_machine_id, params_json
            FROM {tbl}
            WHERE (status IS NULL OR status = 'pending')
            ORDER BY created_at ASC
            LIMIT {int(limit)}
            """
        )
        cols = [c[0] for c in self._cur.description]
        return [dict(zip(cols, r)) for r in self._cur.fetchall()]

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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ts_lit(ts: datetime | None) -> str:
    if ts is None:
        return "NULL"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    ts2 = ts.astimezone(timezone.utc).replace(tzinfo=None)
    return f"TIMESTAMP '{ts2.strftime('%Y-%m-%d %H:%M:%S')}'"


def _str_lit(v: Any) -> str:
    if v is None:
        return "NULL"
    s = str(v).replace("'", "''")
    return f"'{s}'"


def _num_lit(v: Any) -> str:
    if v is None:
        return "NULL"
    try:
        return str(float(v))
    except Exception:
        return "NULL"


def _int_lit(v: Any) -> str:
    if v is None:
        return "NULL"
    try:
        return str(int(v))
    except Exception:
        return "NULL"


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def _oauth_token() -> str:
    w = WorkspaceClient()
    try:
        headers = w.config.authenticate()
        if isinstance(headers, dict):
            return headers.get("Authorization", "").removeprefix("Bearer ")
    except TypeError:
        pass
    headers_mut: dict[str, str] = {}
    w.config.authenticate(headers_mut)
    return headers_mut.get("Authorization", "").removeprefix("Bearer ")


def main() -> None:
    from databricks import sql as dbsql

    catalog = os.environ.get("UC_CATALOG", "main")
    schema = os.environ.get("UC_SCHEMA", "line_manager_ai_kit_preview")
    host = _env("DATABRICKS_HOST")
    warehouse_id = _env("DATABRICKS_WAREHOUSE_ID")

    token = os.environ.get("DATABRICKS_TOKEN", "") or _oauth_token()
    if not token:
        raise RuntimeError("Could not obtain OAuth token for SQL Warehouse")

    conn = dbsql.connect(
        server_hostname=host.replace("https://", ""),
        http_path=f"/sql/1.0/warehouses/{warehouse_id}",
        access_token=token,
        catalog=catalog,
        schema=schema,
    )
    cur = conn.cursor()

    shift_id = os.environ.get("SIM_SHIFT_ID", "shift_1")
    tick_s = float(os.environ.get("SIM_TICK_SECONDS", "2"))
    rng = random.Random(42)

    command_store = CommandStore(host, warehouse_id, catalog, schema)
    engine = ScenarioEngine()
    machines = _machine_ids()
    ordered = _ordered_machines(machines)
    producer_id = f"sql_realtime_producer:{uuid.uuid4()}"

    tbl_state = f"{catalog}.{schema}.machine_state_bronze"
    tbl_events = f"{catalog}.{schema}.event_log_bronze"
    tbl_qc = f"{catalog}.{schema}.quality_checks_bronze"
    tbl_telem = f"{catalog}.{schema}.telemetry_bronze"

    open_events: dict[str, dict[str, Any]] = {}

    try:
        while True:
            now = _utc_now()
            engine.prune(now)

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

                s = ActiveScenario(
                    scenario_id=str(cmd.get("scenario_id") or ""),
                    start_time=start,
                    end_time=end,
                    severity=str(cmd.get("severity") or "medium"),
                    target_machine_id=(str(cmd["target_machine_id"]) if cmd.get("target_machine_id") else None),
                    params=params,
                )
                engine.add(s)
                command_store.mark_processed(str(cmd["command_id"]), processed_by=producer_id)

                # Emit a start event row.
                sid = s.scenario_id
                target_mid = s.target_machine_id or ("FILLER" if sid in ("filler_down", "quality_deviation") else ordered[0])
                event_id = _id("EVT")
                open_events[event_id] = {"scenario_id": sid, "start": s.start_time, "machine_id": target_mid, "severity": s.severity}

                cur.execute(
                    f"""INSERT INTO {tbl_events} VALUES (
                      {_str_lit(event_id)},
                      {_str_lit(shift_id)},
                      {_str_lit(target_mid)},
                      {_str_lit('major_stop' if sid == 'filler_down' else 'quality_hold' if sid == 'quality_deviation' else 'upstream_delay')},
                      {_str_lit('critical' if sid == 'filler_down' else 'major')},
                      {_ts_lit(s.start_time)},
                      NULL,
                      {_num_lit(0.0)},
                      {_str_lit('mechanical' if sid == 'filler_down' else 'quality' if sid == 'quality_deviation' else 'supply')},
                      {_str_lit(f'Scenario triggered: {sid}')},
                      {_str_lit('')},
                      {_num_lit(0.0)},
                      {_num_lit(0.0)},
                      {_str_lit('')}
                    )"""
                )

            # Close events whose scenario has ended.
            for eid, meta in list(open_events.items()):
                # If scenario no longer active, close.
                sid = meta.get("scenario_id")
                if not any(s.scenario_id == sid for s in engine.active):
                    start_ts: datetime = meta["start"]
                    end_ts: datetime = now
                    dur_min = max(0.0, (end_ts - start_ts).total_seconds() / 60.0)
                    cur.execute(
                        f"""INSERT INTO {tbl_events} VALUES (
                          {_str_lit(eid)},
                          {_str_lit(shift_id)},
                          {_str_lit(meta.get('machine_id'))},
                          {_str_lit('resolved')},
                          {_str_lit('info')},
                          {_ts_lit(start_ts)},
                          {_ts_lit(end_ts)},
                          {_num_lit(dur_min)},
                          {_str_lit(str(sid))},
                          {_str_lit(f'Scenario resolved: {sid}')},
                          {_str_lit('Operator action')},
                          {_num_lit(dur_min)},
                          {_num_lit(-0.02 if sid == 'filler_down' else -0.01)},
                          {_str_lit('Recovered')}
                        )"""
                    )
                    open_events.pop(eid, None)

            # Build a tick for machine state.
            ts_min = now.replace(second=0, microsecond=0)
            base_state: dict[str, dict[str, Any]] = {}
            for mid in ordered:
                base = _baseline(mid)
                base["speed_pct"] = _base_speed_pct(now, mid, rng)
                base_state[mid] = engine.apply(now, mid, base)
            derived = _apply_simple_cascades(ordered=ordered, state=base_state)

            # Insert all machine ticks in one statement.
            values = []
            for mid in ordered:
                rec = derived[mid]
                values.append(
                    "("
                    + ",".join(
                        [
                            _str_lit(shift_id),
                            _str_lit(mid),
                            _ts_lit(now),
                            _ts_lit(ts_min),
                            _str_lit(rec.get("status")),
                            _num_lit(rec.get("throughput_per_min")),
                            _num_lit(rec.get("speed_pct")),
                            _int_lit(rec.get("alarm_count")),
                        ]
                    )
                    + ")"
                )
            cur.execute(f"INSERT INTO {tbl_state} VALUES {', '.join(values)}")

            # Telemetry + QC (lightweight)
            for mid in ("FILLER", "LABELLER", "CASE_PACKER"):
                rec = derived.get(mid)
                if not rec:
                    continue
                cur.execute(
                    f"""INSERT INTO {tbl_telem} VALUES
                    ({_str_lit(shift_id)}, {_ts_lit(now)}, {_str_lit(mid)}, {_str_lit('throughput_per_min')}, {_num_lit(rec.get('throughput_per_min'))}),
                    ({_str_lit(shift_id)}, {_ts_lit(now)}, {_str_lit(mid)}, {_str_lit('speed_pct')}, {_num_lit(rec.get('speed_pct'))})
                    """
                )

            filler = derived.get("FILLER")
            if filler and filler.get("status") == "degraded":
                val = 330.0 + rng.uniform(-3.0, 6.0)
                cur.execute(
                    f"""INSERT INTO {tbl_qc} VALUES (
                      {_str_lit(_id('QC'))},
                      {_str_lit(shift_id)},
                      {_str_lit('FILLER')},
                      {_ts_lit(now)},
                      {_str_lit('fill_volume_ml')},
                      {_num_lit(val)},
                      {_num_lit(325.0)},
                      {_num_lit(335.0)},
                      {_str_lit('fail' if val > 335.0 or val < 325.0 else 'pass')}
                    )"""
                )

            conn.commit()
            time.sleep(tick_s)
    finally:
        try:
            cur.close()
            conn.close()
        finally:
            command_store.close()


if __name__ == "__main__":
    main()

