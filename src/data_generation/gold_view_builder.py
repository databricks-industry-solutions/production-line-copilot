"""Gold view builder — aggregates raw telemetry into machine_state and line_kpi."""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from .config import (
    ALARM_COUNT_DEGRADED_THRESHOLD,
    MACHINES,
    PRIMARY_SENSOR_MAPPING,
    SCENARIOS,
    SENSORS,
    SPEED_ZERO_THRESHOLD,
)
from .telemetry_generator import generate_shift_telemetry

# =============================================================================
# LOOKUPS
# =============================================================================

# Machine -> out_count sensor name (None for conveyors which have no out_count)
OUT_COUNT_SENSORS: dict[str, str | None] = {
    "DEPALLETIZER": "depal_out_count",
    "CONVEYOR_1": None,
    "UNCASER": "uncaser_out_count",
    "BOTTLE_WASHER": "washer_out_count",
    "EMPTY_INSPECTOR": "insp_out_count",
    "CONVEYOR_2": None,
    "FILLER": "filler_out_count",
    "CAPPER": "capper_out_count",
    "PASTEURIZER": "past_out_count",
    "LABELLER": "label_out_count",
    "CODER": "coder_out_count",
    "CONVEYOR_3": None,
    "CASE_PACKER": "packer_out_count",
    "SHRINK_WRAPPER": "shrink_out_count",
    "CASE_SEALER": "sealer_out_count",
    "CHECKWEIGHER": "chkwt_out_count",
    "CONVEYOR_4": None,
    "PALLETIZER": "pallet_out_count",
    "STRETCH_WRAPPER": "stretch_out_count",
}

# Machine -> (speed_sensor_name, nominal_speed) for has_speed_sensor
SPEED_SENSORS: dict[str, tuple[str, float] | None] = {}
for m in MACHINES:
    if m.has_speed_sensor and m.nominal_speed is not None:
        s = next((x for x in SENSORS if x.machine_id == m.id and x.sensor_type == "speed"), None)
        if s:
            SPEED_SENSORS[m.id] = (s.sensor_name, m.nominal_speed)
    elif m.has_speed_sensor:
        s = next((x for x in SENSORS if x.machine_id == m.id and x.sensor_type == "speed"), None)
        if s:
            nom = s.nominal_value or 420.0
            SPEED_SENSORS[m.id] = (s.sensor_name, nom)
    else:
        SPEED_SENSORS[m.id] = None

# Quality sensors: (machine_id, sensor_name, baseline_per_min, is_cumulative)
QUALITY_SENSORS: list[tuple[str, str, float, bool]] = []
for s in SENSORS:
    if s.sensor_type != "quality":
        continue
    if s.unit == "EA/min":
        QUALITY_SENSORS.append((s.machine_id, s.sensor_name, 2.0, False))
    else:
        QUALITY_SENSORS.append((s.machine_id, s.sensor_name, 1.0, True))

MACHINE_BY_POSITION: dict[int, str] = {m.line_position: m.id for m in MACHINES}

# Shift -> ideal_cases_per_min: CASE_PACKER nominal rate is 17.5 cases/min for all SKUs.
# Performance = cumulative_cases / (available_minutes * ideal_cpm); packer is the bottleneck.
IDEAL_CASES_PER_MIN: dict[str, float] = {
    "shift_1": 17.5,
    "shift_2": 17.5,
    "shift_3": 17.5,
    "shift_4": 17.5,
    "shift_5": 17.5,
    "shift_6": 17.5,
    "shift_7": 17.5,
}


# =============================================================================
# MACHINE_STATE_1M
# =============================================================================


def _floor_to_minute(ts_str: str) -> str:
    """Floor ISO timestamp to minute."""
    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    floored = dt.replace(second=0, microsecond=0)
    return floored.strftime("%Y-%m-%dT%H:%M:%S")


def _derive_status(
    machine_id: str,
    has_speed: bool,
    speed_val: float | None,
    counter_delta: int,
    alarm_count: int,
    upstream_stopped: bool,
    is_changeover_minute: bool,
) -> str:
    """Derive status from speed/counter/alarm/upstream/changeover."""
    threshold = ALARM_COUNT_DEGRADED_THRESHOLD
    is_producing = False
    if has_speed and speed_val is not None:
        is_producing = speed_val > SPEED_ZERO_THRESHOLD
    if not is_producing:
        is_producing = counter_delta > 0

    if is_changeover_minute:
        return "idle"
    if is_producing and alarm_count <= threshold:
        return "running"
    if is_producing and alarm_count > threshold:
        return "degraded"
    # Not producing
    if upstream_stopped:
        return "starved"
    return "stopped"


def build_machine_state(shift_telemetry: pd.DataFrame, shift_id: str) -> pd.DataFrame:
    """Derive machine_state from raw telemetry for one shift."""
    df = shift_telemetry.copy()
    df["ts"] = df["timestamp"].apply(_floor_to_minute)

    # Pivot to wide: (ts, machine_id) x sensor values
    records: list[dict] = []

    for machine_id in [m.id for m in MACHINES]:
        machine_df = df[df["machine_id"] == machine_id].copy()
        if machine_df.empty:
            continue

        out_sensor = OUT_COUNT_SENSORS.get(machine_id)
        speed_info = SPEED_SENSORS.get(machine_id)
        primary = PRIMARY_SENSOR_MAPPING.get(machine_id)

        # Group by minute
        grp = machine_df.groupby("ts")

        # Counter delta (out_count)
        if out_sensor:
            out_df = machine_df[machine_df["sensor_name"] == out_sensor]
            if not out_df.empty:
                out_agg = out_df.groupby("ts")["value"].agg(
                    first="first", last="last"
                )
                counter_delta = (out_agg["last"] - out_agg["first"]).fillna(0).astype(int)
                throughput = counter_delta.astype(float)
            else:
                counter_delta = pd.Series(dtype=int)
                throughput = pd.Series(dtype=float)
        else:
            counter_delta = pd.Series(dtype=int)
            throughput = pd.Series(dtype=float)

        # Speed mean and speed_pct (clamped to 0–100%)
        if speed_info:
            speed_sensor, nominal = speed_info
            speed_df = machine_df[machine_df["sensor_name"] == speed_sensor]
            if not speed_df.empty:
                speed_mean = speed_df.groupby("ts")["value"].mean()
                speed_pct = (speed_mean / nominal * 100).clip(upper=100.0) if nominal else None
            else:
                speed_mean = pd.Series(dtype=float)
                speed_pct = pd.Series(dtype=float)
        else:
            speed_mean = pd.Series(dtype=float)
            speed_pct = pd.Series(dtype=float)

        # Alarm count: quality sensors exceeding 3× baseline per minute
        alarm_counts = pd.Series(0.0, index=grp.indices.keys() if hasattr(grp, "indices") else [])
        for mid, sname, baseline, is_cumulative in QUALITY_SENSORS:
            if mid != machine_id:
                continue
            q_df = machine_df[machine_df["sensor_name"] == sname]
            if q_df.empty:
                continue
            alarm_threshold = baseline * ALARM_COUNT_DEGRADED_THRESHOLD
            if is_cumulative:
                agg = q_df.groupby("ts")["value"].agg(first="first", last="last")
                delta = agg["last"] - agg["first"]
                exceeded = (delta > alarm_threshold).astype(int)
            else:
                total = q_df.groupby("ts")["value"].sum()
                exceeded = (total > alarm_threshold).astype(int)
            alarm_counts = alarm_counts.add(exceeded, fill_value=0)

        # Primary sensors
        ps1_vals = pd.Series(dtype=float)
        ps2_vals = pd.Series(dtype=float)
        ml_vals = pd.Series(dtype=float)
        if primary and primary.primary_sensor_1:
            p1 = machine_df[machine_df["sensor_name"] == primary.primary_sensor_1]
            if not p1.empty:
                ps1_vals = p1.groupby("ts")["value"].mean()
        if primary and primary.primary_sensor_2:
            p2 = machine_df[machine_df["sensor_name"] == primary.primary_sensor_2]
            if not p2.empty:
                ps2_vals = p2.groupby("ts")["value"].mean()
        if primary and primary.material_level_pct:
            ml = machine_df[machine_df["sensor_name"] == primary.material_level_pct]
            if not ml.empty:
                ml_vals = ml.groupby("ts")["value"].mean()

        # Build index from all ts values
        all_minutes = machine_df["ts"].unique()
        idx = pd.Index(all_minutes, name="ts")

        for ts_min in idx:
            cd = int(counter_delta.get(ts_min, 0))
            tp = float(throughput.get(ts_min, 0.0))
            sp = speed_pct.get(ts_min) if speed_info else None
            ac = int(alarm_counts.get(ts_min, 0))
            records.append({
                "ts": ts_min,
                "shift_id": shift_id,
                "machine_id": machine_id,
                "throughput_per_min": tp,
                "speed_pct": sp if pd.notna(sp) else None,
                "counter_delta": cd,
                "primary_sensor_1": ps1_vals.get(ts_min) if not ps1_vals.empty else None,
                "primary_sensor_2": ps2_vals.get(ts_min) if not ps2_vals.empty else None,
                "material_level_pct": ml_vals.get(ts_min) if not ml_vals.empty else None,
                "alarm_count": ac,
            })

    ms = pd.DataFrame(records)
    if ms.empty:
        return pd.DataFrame(columns=[
            "ts", "shift_id", "machine_id", "status",
            "throughput_per_min", "speed_pct", "counter_delta",
            "primary_sensor_1", "primary_sensor_2", "material_level_pct", "alarm_count",
        ])

    # Derive status: need upstream info
    machine_pos = {m.id: m.line_position for m in MACHINES}
    ms["line_position"] = ms["machine_id"].map(machine_pos)
    ms["has_speed"] = ms["machine_id"].map(lambda x: SPEED_SENSORS.get(x) is not None)

    # Upstream stopped: for each (ts_min, machine), check if machine at position-1 is stopped
    statuses: list[str] = []
    for _, row in ms.iterrows():
        pos = row["line_position"]
        ts_min = row["ts"]
        upstream_id = MACHINE_BY_POSITION.get(pos - 1)
        upstream_stopped = False
        if upstream_id:
            up_rows = ms[(ms["ts"] == ts_min) & (ms["machine_id"] == upstream_id)]
            if not up_rows.empty:
                up = up_rows.iloc[0]
                up_speed = up.get("speed_pct")
                up_cd = up.get("counter_delta", 0)
                up_has_speed = up.get("has_speed", False)
                up_producing = (up_has_speed and up_speed is not None and up_speed > 0.01) or (up_cd > 0)
                upstream_stopped = not up_producing

        is_changeover = _is_changeover_minute(shift_id, ts_min)
        st = _derive_status(
            row["machine_id"],
            row["has_speed"],
            row.get("speed_pct"),
            int(row.get("counter_delta", 0)),
            int(row.get("alarm_count", 0)),
            upstream_stopped,
            is_changeover,
        )
        statuses.append(st)

    ms["status"] = statuses
    ms.drop(columns=["line_position", "has_speed"], inplace=True)

    # Reorder columns
    cols = [
        "ts", "shift_id", "machine_id", "status",
        "throughput_per_min", "speed_pct", "counter_delta",
        "primary_sensor_1", "primary_sensor_2", "material_level_pct", "alarm_count",
    ]
    return ms[[c for c in cols if c in ms.columns]]


def _is_changeover_minute(shift_id: str, ts_min: str) -> bool:
    """Check if minute is within a known changeover window (S4: 06:00-06:45)."""
    if shift_id != "shift_4":
        return False
    try:
        dt = datetime.fromisoformat(ts_min)
        # S4 shift starts 06:00, changeover 06:00-06:45
        start = dt.replace(hour=6, minute=0, second=0)
        end = dt.replace(hour=6, minute=45, second=0)
        return start <= dt <= end
    except Exception:
        return False


# =============================================================================
# LINE_KPI_1M
# =============================================================================


def build_line_kpi(
    machine_state: pd.DataFrame,
    shift_id: str,
    shift_telemetry: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Derive line_kpi from machine_state for one shift."""
    if machine_state.empty:
        return pd.DataFrame()

    ideal_cpm = IDEAL_CASES_PER_MIN.get(shift_id, 17.5)

    # Status counts per minute
    status_counts = machine_state.groupby("ts")["status"].value_counts().unstack(fill_value=0)
    for col in ["running", "stopped", "degraded", "starved", "idle"]:
        if col not in status_counts.columns:
            status_counts[col] = 0

    # CASE_PACKER throughput (bottleneck)
    packer = machine_state[machine_state["machine_id"] == "CASE_PACKER"]
    packer_throughput = packer.groupby("ts")["throughput_per_min"].sum()

    # Minutes where CASE_PACKER is stopped/starved/idle
    packer_status = packer.groupby("ts")["status"].first()
    non_producing = packer_status.isin(["stopped", "starved", "idle"])

    # Cumulative cases
    total_throughput = packer_throughput.reindex(status_counts.index, fill_value=0)
    cumulative_cases = total_throughput.cumsum().astype(int)

    # Cumulative rejects from CHECKWEIGHER chkwt_reject_count (delta per minute, then cumsum)
    cumulative_rejects = 0
    if shift_telemetry is not None:
        chkwt = shift_telemetry[(shift_telemetry["machine_id"] == "CHECKWEIGHER") & (shift_telemetry["sensor_name"] == "chkwt_reject_count")]
        if not chkwt.empty:
            chkwt = chkwt.copy()
            chkwt["ts"] = chkwt["timestamp"].apply(_floor_to_minute)
            agg = chkwt.groupby("ts")["value"].agg(first="first", last="last")
            reject_delta = (agg["last"] - agg["first"]).fillna(0).reindex(status_counts.index, fill_value=0)
            cumulative_rejects = reject_delta.cumsum().values
        else:
            cumulative_rejects = np.zeros(len(status_counts))
    else:
        cumulative_rejects = np.zeros(len(status_counts))

    # Build kpi df
    kpi = pd.DataFrame({
        "ts": status_counts.index,
        "shift_id": shift_id,
        "machines_running": status_counts["running"].values,
        "machines_stopped": status_counts["stopped"].values,
        "machines_degraded": status_counts["degraded"].values,
        "machines_starved": status_counts["starved"].values,
        "total_throughput": total_throughput.values,
        "cumulative_cases": cumulative_cases.values,
    })

    minutes_elapsed = np.arange(1, len(kpi) + 1, dtype=float)
    minutes_all_stopped = non_producing.reindex(kpi["ts"]).fillna(False).astype(int).cumsum()
    availability_to_now = (minutes_elapsed - minutes_all_stopped.values) / minutes_elapsed * 100

    available_minutes = minutes_elapsed - minutes_all_stopped.values
    performance_to_now = cumulative_cases.values / (available_minutes * ideal_cpm + 1e-9) * 100
    performance_to_now = np.minimum(performance_to_now, 100)

    quality_to_now = (cumulative_cases.values - cumulative_rejects) / (cumulative_cases.values + 1e-9) * 100
    quality_to_now = np.minimum(quality_to_now, 100)

    availability_to_now = np.clip(availability_to_now, 0, 100)
    oee_to_now = availability_to_now * performance_to_now * quality_to_now / 10000
    oee_to_now = np.clip(oee_to_now, 0, 100)

    kpi["availability_to_now"] = availability_to_now
    kpi["performance_to_now"] = performance_to_now
    kpi["quality_to_now"] = quality_to_now
    kpi["oee_to_now"] = oee_to_now

    return kpi


# =============================================================================
# GENERATE GOLD VIEWS
# =============================================================================


def generate_gold_views() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Generate both gold views for all 7 shifts."""
    machine_state_dfs: list[pd.DataFrame] = []
    line_kpi_dfs: list[pd.DataFrame] = []

    for scenario in SCENARIOS:
        shift_id = scenario.shift_id
        telemetry = generate_shift_telemetry(shift_id)
        machine_state = build_machine_state(telemetry, shift_id)
        line_kpi = build_line_kpi(machine_state, shift_id, shift_telemetry=telemetry)
        machine_state_dfs.append(machine_state)
        line_kpi_dfs.append(line_kpi)

    machine_state = pd.concat(machine_state_dfs, ignore_index=True)
    line_kpi = pd.concat(line_kpi_dfs, ignore_index=True)
    return machine_state, line_kpi


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    # Generate gold views for S1 and S5 only (for speed)
    print("=== Quick test: S1 and S5 ===\n")

    telemetry_s1 = generate_shift_telemetry("shift_1")
    telemetry_s5 = generate_shift_telemetry("shift_5")

    machine_state_s1 = build_machine_state(telemetry_s1, "shift_1")
    machine_state_s5 = build_machine_state(telemetry_s5, "shift_5")

    line_kpi_s1 = build_line_kpi(machine_state_s1, "shift_1", shift_telemetry=telemetry_s1)
    line_kpi_s5 = build_line_kpi(machine_state_s5, "shift_5", shift_telemetry=telemetry_s5)

    print("S1 status distribution:")
    print(machine_state_s1["status"].value_counts().to_string())
    print()

    print("S5 status distribution:")
    print(machine_state_s5["status"].value_counts().to_string())
    print()

    print("S5 — CAPPER status at T=18:50, 19:00, 19:10:")
    for ts_min in ["2025-01-02T18:50:00", "2025-01-02T19:00:00", "2025-01-02T19:10:00"]:
        row = machine_state_s5[
            (machine_state_s5["machine_id"] == "CAPPER") & (machine_state_s5["ts"] == ts_min)
        ]
        if not row.empty:
            print(f"  {ts_min}: {row['status'].iloc[0]}")
        else:
            print(f"  {ts_min}: (no row)")
