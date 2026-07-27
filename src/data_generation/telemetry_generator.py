"""Core telemetry data generation engine — ~6.6M rows across 7 shifts.

Generates machine_telemetry table: timestamp, shift_id, machine_id, sensor_name, value.
Uses config.SENSORS, config.MACHINES, config.SCENARIOS for structure.
Scenario modifiers create the telemetry 'stories' for control tower visualization.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable

import numpy as np
import pandas as pd

from .config import MACHINES, SCENARIOS, SENSORS, MachineDef, SensorDef

# =============================================================================
# 1. CONSTANTS
# =============================================================================

TIMESTAMPS_PER_SHIFT = 14_400  # 8 hours × 3600 / 2 sec
INTERVAL_SEC = 2
SHIFT_HOURS = 8

# Shift start datetimes (user-specified)
SHIFT_START_DATETIMES: dict[str, datetime] = {
    "shift_1": datetime(2025, 1, 1, 8, 0, 0),
    "shift_2": datetime(2025, 1, 1, 14, 0, 0),
    "shift_3": datetime(2025, 1, 1, 22, 0, 0),
    "shift_4": datetime(2025, 1, 2, 6, 0, 0),
    "shift_5": datetime(2025, 1, 2, 14, 0, 0),
    "shift_6": datetime(2025, 1, 2, 22, 0, 0),
    "shift_7": datetime(2025, 1, 3, 6, 0, 0),
}


# =============================================================================
# 2. LOOKUP HELPERS
# =============================================================================

def _machine_by_id(machine_id: str) -> MachineDef | None:
    for m in MACHINES:
        if m.id == machine_id:
            return m
    return None


def _machine_line_position(machine_id: str) -> int:
    m = _machine_by_id(machine_id)
    return m.line_position if m else 0


def _nominal_speed_for_machine(machine_id: str) -> float:
    """Get nominal speed (BPM or m/min) for counter rate calculation."""
    m = _machine_by_id(machine_id)
    if m and m.nominal_speed is not None:
        return m.nominal_speed
    # Find speed sensor on machine
    for s in SENSORS:
        if s.machine_id == machine_id and s.sensor_type == "speed":
            return s.nominal_value or 420.0
    return 420.0  # default line speed


def _line_position_delta(from_machine: str, to_machine: str) -> int:
    return _machine_line_position(to_machine) - _machine_line_position(from_machine)


# =============================================================================
# 3. BASELINE VALUE GENERATORS (vectorized)
# =============================================================================

def _generate_counter_baseline(
    rng: np.random.Generator,
    sensor: SensorDef,
    timestamps: np.ndarray,
    is_machine_running: np.ndarray,
    shift_id: str,
) -> np.ndarray:
    """Counter: cumulative, monotonically increasing. Rate drops to 0 when stopped."""
    n = len(timestamps)
    seed = hash(shift_id + sensor.sensor_name) % (2**32)
    local_rng = np.random.default_rng(seed)

    nominal = _nominal_speed_for_machine(sensor.machine_id)
    # 420 BPM = 7 items per 2-sec; 25 m/min = different; adjust by nominal
    rate_per_2sec = nominal / 60 * 2 if nominal else 7  # items per 2-sec interval
    rate_per_2sec *= 1 + local_rng.uniform(-0.05, 0.05)

    rates = np.full(n, rate_per_2sec)
    rates = rates * is_machine_running
    rates = rates * (1 + local_rng.uniform(-0.05, 0.05, n))
    rates = np.maximum(rates, 0)
    counts = np.cumsum(rates)
    return np.round(counts).astype(float)


def _generate_speed_baseline(
    rng: np.random.Generator,
    sensor: SensorDef,
    timestamps: np.ndarray,
    is_machine_running: np.ndarray,
    shift_id: str,
) -> np.ndarray:
    """Speed: continuous around nominal. Warm-up ramp, drops to 0 when stopped."""
    n = len(timestamps)
    seed = hash(shift_id + sensor.sensor_name) % (2**32)
    local_rng = np.random.default_rng(seed)

    nominal = sensor.nominal_value or 420.0
    sigma = 0.02 * nominal
    noise = local_rng.normal(0, sigma, n)

    # 5-min warm-up: 150 timestamps = 300 sec
    warmup = 150
    ramp = np.ones(n)
    ramp[:warmup] = 0.8 + 0.2 * np.arange(warmup) / warmup

    base = nominal * ramp + noise
    return base * is_machine_running


def _generate_process_baseline(
    rng: np.random.Generator,
    sensor: SensorDef,
    timestamps: np.ndarray,
    is_machine_running: np.ndarray,
    shift_id: str,
) -> np.ndarray:
    """Process: continuous around nominal. ±2% sinusoidal drift, 2h period."""
    n = len(timestamps)
    seed = hash(shift_id + sensor.sensor_name) % (2**32)
    local_rng = np.random.default_rng(seed)

    nominal = sensor.nominal_value or 100.0
    sigma = 0.01 * nominal
    noise = local_rng.normal(0, sigma, n)

    # 2h period = 3600 timestamps
    period = 3600
    t = np.arange(n, dtype=float)
    drift = 0.02 * nominal * np.sin(2 * np.pi * t / period)

    base = nominal + noise + drift
    if sensor.alarm_low is not None and sensor.alarm_high is not None:
        base = np.clip(base, sensor.alarm_low, sensor.alarm_high)
    return base * is_machine_running + (1 - is_machine_running) * 0


def _generate_level_baseline(
    rng: np.random.Generator,
    sensor: SensorDef,
    timestamps: np.ndarray,
    is_machine_running: np.ndarray,
    shift_id: str,
) -> np.ndarray:
    """Level: declining from 95-100%. Refill at 30%. Flat when stopped."""
    n = len(timestamps)
    seed = hash(shift_id + sensor.sensor_name) % (2**32)
    local_rng = np.random.default_rng(seed)

    start = 0.95 + local_rng.uniform(0, 0.05)
    # -12% per hour ≈ -0.00033 per 2-sec interval
    decline_per_interval = 0.12 / 1800  # 1800 intervals per hour

    level = np.zeros(n)
    level[0] = start
    for i in range(1, n):
        if is_machine_running[i]:
            level[i] = level[i - 1] - decline_per_interval * (1 + local_rng.uniform(-0.2, 0.2))
        else:
            level[i] = level[i - 1]
        if level[i] < 0.30:
            level[i] = 0.90 + local_rng.uniform(0, 0.05)
        level[i] = np.clip(level[i], 0.01, 1.0)

    return level * 100  # as percentage




def _generate_quality_ea_min_baseline(
    rng: np.random.Generator,
    sensor: SensorDef,
    timestamps: np.ndarray,
    is_machine_running: np.ndarray,
    shift_id: str,
) -> np.ndarray:
    """Quality EA/min: Poisson baseline, sporadic spikes."""
    n = len(timestamps)
    seed = hash(shift_id + sensor.sensor_name) % (2**32)
    local_rng = np.random.default_rng(seed)

    lam = (sensor.nominal_value or 2) / 30  # per 2-sec (2 per min = 60 intervals)
    base = local_rng.poisson(lam, n).astype(float)

    # 1 spike per ~30 min = 900 intervals
    spike_interval = 900
    base_indices = np.arange(0, n, spike_interval)
    offsets = local_rng.integers(0, 100, size=len(base_indices))
    spike_indices = (base_indices + offsets).astype(int)
    spike_indices = spike_indices[spike_indices < n]
    base[spike_indices] *= local_rng.uniform(2, 3, len(spike_indices))

    return base * is_machine_running


def _generate_quality_ea_baseline(
    rng: np.random.Generator,
    sensor: SensorDef,
    timestamps: np.ndarray,
    is_machine_running: np.ndarray,
    shift_id: str,
) -> np.ndarray:
    """Quality EA (cumulative): slow rate 1-3 per minute."""
    n = len(timestamps)
    seed = hash(shift_id + sensor.sensor_name) % (2**32)
    local_rng = np.random.default_rng(seed)

    rate_per_2sec = (1 + local_rng.uniform(0, 2)) / 30  # 1-3 per min
    rates = np.full(n, rate_per_2sec) * is_machine_running
    rates = rates * (1 + local_rng.uniform(-0.3, 0.3, n))
    return np.cumsum(np.maximum(rates, 0))


def _generate_baseline_values(
    sensor: SensorDef,
    timestamps: np.ndarray,
    is_machine_running: np.ndarray,
    rng: np.random.Generator,
    shift_id: str,
) -> np.ndarray:
    """Dispatch to appropriate baseline generator by sensor type."""
    if sensor.sensor_type == "counter":
        return _generate_counter_baseline(rng, sensor, timestamps, is_machine_running, shift_id)
    if sensor.sensor_type == "speed":
        return _generate_speed_baseline(rng, sensor, timestamps, is_machine_running, shift_id)
    if sensor.sensor_type == "process":
        return _generate_process_baseline(rng, sensor, timestamps, is_machine_running, shift_id)
    if sensor.sensor_type == "level":
        return _generate_level_baseline(rng, sensor, timestamps, is_machine_running, shift_id)
    if sensor.sensor_type == "quality":
        if sensor.unit == "EA/min":
            return _generate_quality_ea_min_baseline(rng, sensor, timestamps, is_machine_running, shift_id)
        return _generate_quality_ea_baseline(rng, sensor, timestamps, is_machine_running, shift_id)
    return np.zeros(len(timestamps))


# =============================================================================
# 4. SCENARIO MODIFIER: APPLY TO VALUES ARRAY
# =============================================================================

def _time_to_index(shift_start: datetime, t: datetime) -> int:
    """Convert datetime to index (0..14399)."""
    delta = (t - shift_start).total_seconds()
    return int(delta / INTERVAL_SEC)


def _apply_micro_stop(
    values: np.ndarray,
    start_idx: int,
    duration_sec: int,
    shift_start: datetime,
) -> None:
    """Zero out values for duration_sec starting at start_idx (for speed sensors)."""
    n_steps = duration_sec // INTERVAL_SEC
    end_idx = min(start_idx + n_steps, len(values))
    values[start_idx:end_idx] = 0


def _apply_counter_flat(values: np.ndarray, start_idx: int, duration_sec: int) -> None:
    """Keep counter flat (no increment) during stop."""
    n_steps = duration_sec // INTERVAL_SEC
    end_idx = min(start_idx + n_steps, len(values))
    flat_val = values[start_idx - 1] if start_idx > 0 else 0
    values[start_idx:end_idx] = flat_val


def _apply_speed_ramp_down(values: np.ndarray, start_idx: int, n_steps: int = 5) -> None:
    """Ramp speed from current to 0 over n_steps."""
    for i in range(n_steps):
        idx = start_idx + i
        if idx >= len(values):
            break
        values[idx] = values[max(0, start_idx - 1)] * (1 - (i + 1) / n_steps)


def _apply_downstream_starvation(
    values: np.ndarray,
    start_idx: int,
    delay_steps: int,
    ramp_steps: int = 5,
) -> None:
    """Downstream machine: delay then ramp to 0."""
    actual_start = start_idx + delay_steps
    for i in range(ramp_steps):
        idx = actual_start + i
        if idx >= len(values):
            break
        values[idx] = values[max(0, actual_start - 1)] * (1 - (i + 1) / ramp_steps)
    for i in range(ramp_steps, len(values) - actual_start):
        idx = actual_start + i
        if idx >= len(values):
            break
        values[idx] = 0


# =============================================================================
# 5. SCENARIO MODIFIER FUNCTIONS (S1-S7)
# =============================================================================

ScenarioModifier = Callable[
    [str, datetime, np.ndarray, dict[tuple[str, str], np.ndarray], np.random.Generator], None
]


def _modifier_s1_baseline_smooth(
    shift_id: str,
    shift_start: datetime,
    timestamps: np.ndarray,
    sensor_values: dict[tuple[str, str], np.ndarray],
    rng: np.random.Generator,
) -> None:
    """S1: 1-3 random micro-stops (20-30 sec) on random machines."""
    n_micro_stops = rng.integers(1, 4)
    machines_with_speed = [m.id for m in MACHINES if m.has_speed_sensor]
    for _ in range(n_micro_stops):
        machine_id = rng.choice(machines_with_speed)
        start_min = int(rng.integers(60, 60 * 6))  # between 1h and 6h into shift
        duration = int(rng.integers(20, 31))
        start_idx = _time_to_index(shift_start, shift_start + timedelta(minutes=start_min))
        for (mid, sname), arr in sensor_values.items():
            if mid != machine_id:
                continue
            sensor = next((s for s in SENSORS if s.machine_id == mid and s.sensor_name == sname), None)
            if not sensor:
                continue
            if sensor.sensor_type == "speed":
                _apply_speed_ramp_down(arr, start_idx)
                _apply_micro_stop(arr, start_idx + 5, duration, shift_start)
            if sensor.sensor_type == "counter":
                _apply_counter_flat(arr, start_idx, duration)


def _modifier_s2_downtime_triage(
    shift_id: str,
    shift_start: datetime,
    timestamps: np.ndarray,
    sensor_values: dict[tuple[str, str], np.ndarray],
    rng: np.random.Generator,
) -> None:
    """S2: LABELLER stop at 16:15, precursor, recovery."""
    t16_05 = shift_start.replace(hour=16, minute=5, second=0)
    t16_10 = shift_start.replace(hour=16, minute=10, second=0)
    t16_15 = shift_start.replace(hour=16, minute=15, second=0)
    t16_23 = shift_start.replace(hour=16, minute=23, second=0)
    t17_00 = shift_start.replace(hour=17, minute=0, second=0)

    idx_16_05 = _time_to_index(shift_start, t16_05)
    idx_16_10 = _time_to_index(shift_start, t16_10)
    idx_16_15 = _time_to_index(shift_start, t16_15)
    idx_16_23 = _time_to_index(shift_start, t16_23)
    idx_17_00 = _time_to_index(shift_start, t17_00)

    for (mid, sname), arr in sensor_values.items():
        if mid == "LABELLER" and sname == "label_vision_nok_count":
            arr[idx_16_05:idx_16_15] = np.linspace(2 / 30, 12 / 30, idx_16_15 - idx_16_05)
        if mid == "LABELLER" and sname == "label_glue_temp":
            arr[idx_16_05:idx_16_15] = np.linspace(145, 140, idx_16_15 - idx_16_05)
        if mid == "LABELLER" and sname == "label_carousel_speed":
            _apply_micro_stop(arr, idx_16_10, 20, shift_start)
            _apply_micro_stop(arr, idx_16_10 + 10, 25, shift_start)
            arr[idx_16_15:idx_16_23] = 0
            arr[idx_16_23:idx_17_00] = 380  # reduced recovery
        if mid == "LABELLER" and sname in ("label_in_count", "label_out_count"):
            _apply_counter_flat(arr, idx_16_10, 20)
            _apply_counter_flat(arr, idx_16_10 + 10, 25)
            arr[idx_16_15:] = arr[idx_16_15 - 1]
        if mid == "CONVEYOR_3" and sname == "conv3_accumulation_pressure":
            arr[idx_16_15:idx_16_23] = 80
        if mid == "CODER" and sname == "coder_in_count":
            arr[idx_16_15 + 30 : idx_16_23] = arr[idx_16_15 + 29]
        if mid == "CASE_PACKER" and sname == "packer_speed":
            arr[idx_16_15 + 30 : idx_16_23] = 0

    _propagate_stop_downstream("LABELLER", idx_16_15, sensor_values, end_idx=idx_16_23)


def _modifier_s3_normal_operation(
    shift_id: str,
    shift_start: datetime,
    timestamps: np.ndarray,
    sensor_values: dict[tuple[str, str], np.ndarray],
    rng: np.random.Generator,
) -> None:
    """S3: Slight lower throughput, 1-2 micro-stops, label_roll_remaining drops then refill."""
    machines_with_speed = [m.id for m in MACHINES if m.has_speed_sensor]
    for _ in range(int(rng.integers(1, 3))):
        machine_id = rng.choice(machines_with_speed)
        start_min = int(rng.integers(120, 60 * 5))
        duration = int(rng.integers(20, 30))
        start_idx = _time_to_index(shift_start, shift_start + timedelta(minutes=start_min))
        for (mid, sname), arr in sensor_values.items():
            if mid != machine_id:
                continue
            sensor = next((s for s in SENSORS if s.machine_id == mid and s.sensor_name == sname), None)
            if sensor and sensor.sensor_type == "speed":
                _apply_micro_stop(arr, start_idx, duration, shift_start)
    for (mid, sname), arr in sensor_values.items():
        if mid == "LABELLER" and sname == "label_roll_remaining":
            t_mid = shift_start + timedelta(hours=3)
            idx = _time_to_index(shift_start, t_mid)
            arr[idx:] = np.maximum(arr[idx:], 20)  # ensure it drops to ~20% then refill (handled by baseline)


def _modifier_s4_schedule_recovery(
    shift_id: str,
    shift_start: datetime,
    timestamps: np.ndarray,
    sensor_values: dict[tuple[str, str], np.ndarray],
    rng: np.random.Generator,
) -> None:
    """S4: 06:00-06:45 idle, ramp 06:45, FILLER fault 06:50-07:05."""
    idx_06_45 = _time_to_index(shift_start, shift_start + timedelta(minutes=45))
    idx_06_50 = _time_to_index(shift_start, shift_start + timedelta(minutes=50))
    idx_07_05 = _time_to_index(shift_start, shift_start + timedelta(minutes=65))

    for (mid, sname), arr in sensor_values.items():
        sensor = next((s for s in SENSORS if s.machine_id == mid and s.sensor_name == sname), None)
        if not sensor:
            continue
        if sensor.sensor_type == "speed":
            arr[:idx_06_45] = 0
            if mid == "FILLER" and sname == "filler_carousel_speed":
                arr[idx_06_50:idx_07_05] = 200
        if sensor.sensor_type == "counter":
            offset = arr[idx_06_45 - 1] if idx_06_45 > 0 else 0
            arr[:idx_06_45] = 0
            arr[idx_06_45:] = np.maximum(arr[idx_06_45:] - offset, 0)
        if mid == "FILLER" and sname == "filler_fill_volume_avg":
            arr[idx_06_50:idx_07_05] = 327


def _modifier_s5_material_shortage(
    shift_id: str,
    shift_start: datetime,
    timestamps: np.ndarray,
    sensor_values: dict[tuple[str, str], np.ndarray],
    rng: np.random.Generator,
) -> None:
    """S5: CAPPER cap hopper low, stop at 19:00. MVP scenario."""
    t17 = shift_start.replace(hour=17, minute=0, second=0)
    t18_30 = shift_start.replace(hour=18, minute=30, second=0)
    t19 = shift_start.replace(hour=19, minute=0, second=0)

    idx_17 = _time_to_index(shift_start, t17)
    idx_18_30 = _time_to_index(shift_start, t18_30)
    idx_19 = _time_to_index(shift_start, t19)

    for (mid, sname), arr in sensor_values.items():
        if mid == "CAPPER" and sname == "capper_cap_hopper_level":
            # Override: decline from 95% to 40% at 17:00, 20% at 18:30, 5% at 19:00, 2% after
            arr[:idx_17] = np.linspace(95, 40, idx_17)
            arr[idx_17:idx_18_30] = np.linspace(40, 20, idx_18_30 - idx_17)
            arr[idx_18_30:idx_19] = np.linspace(20, 5, idx_19 - idx_18_30)
            arr[idx_19:] = 2
        if mid == "CAPPER" and sname == "capper_torque_avg":
            arr[idx_17:idx_19] = 2.0 + rng.uniform(-0.2, 0.2, idx_19 - idx_17)
        if mid == "CAPPER" and sname == "capper_carousel_speed":
            arr[idx_19:] = 0
        if mid == "CAPPER" and sname == "capper_in_count":
            arr[idx_19:] = arr[idx_19 - 1]
        if mid == "CAPPER" and sname == "capper_out_count":
            arr[idx_19:] = arr[idx_19 - 1]
        if mid == "FILLER" and sname == "filler_carousel_speed":
            arr[idx_19 + 15 :] = 420 * 0.7  # upstream pressure when CAPPER stops
        if mid == "CONVEYOR_3" and sname == "conv3_accumulation_pressure":
            arr[idx_19:] = 80
        if mid == "CASE_PACKER" and sname == "packer_speed":
            arr[idx_19 + 30 :] = 0

    _propagate_stop_downstream("CAPPER", idx_19, sensor_values)


def _modifier_s6_quality_deviation(
    shift_id: str,
    shift_start: datetime,
    timestamps: np.ndarray,
    sensor_values: dict[tuple[str, str], np.ndarray],
    rng: np.random.Generator,
) -> None:
    """S6: Quality hold at 01:00. Fill volume drift, vision NOK rise."""
    # shift_6: 22:00-06:00, so 00:30, 00:40, 01:00, 01:25 are next day
    t00_30 = shift_start + timedelta(hours=2, minutes=30)
    t00_40 = shift_start + timedelta(hours=2, minutes=40)
    t01_00 = shift_start + timedelta(hours=3, minutes=0)
    t01_25 = shift_start + timedelta(hours=3, minutes=25)

    idx_00_30 = _time_to_index(shift_start, t00_30)
    idx_00_40 = _time_to_index(shift_start, t00_40)
    idx_01_00 = _time_to_index(shift_start, t01_00)
    idx_01_25 = _time_to_index(shift_start, t01_25)

    for (mid, sname), arr in sensor_values.items():
        if mid == "FILLER" and sname == "filler_fill_volume_avg":
            arr[idx_00_30:idx_01_25] = np.linspace(330, 327, idx_01_25 - idx_00_30)
            arr[idx_01_25:] = 330
        if mid == "LABELLER" and sname == "label_vision_nok_count":
            arr[idx_00_40:idx_01_00] = np.linspace(2 / 30, 8 / 30, idx_01_00 - idx_00_40)
        if mid == "LABELLER" and sname == "label_carousel_speed":
            arr[idx_01_00:idx_01_25] = 0
        if mid == "FILLER" and sname == "filler_carousel_speed":
            arr[idx_01_00:idx_01_25] = 0
        for m in MACHINES:
            if mid == m.id:
                for s in SENSORS:
                    if s.machine_id == mid and s.sensor_name == sname and s.sensor_type == "speed":
                        arr[idx_01_00:idx_01_25] = 0
                break

    _propagate_stop_downstream("DEPALLETIZER", idx_01_00, sensor_values, end_idx=idx_01_25)


def _modifier_s7_upstream_delay(
    shift_id: str,
    shift_start: datetime,
    timestamps: np.ndarray,
    sensor_values: dict[tuple[str, str], np.ndarray],
    rng: np.random.Generator,
) -> None:
    """S7: FILLER bowl starvation — 06:15, 10:00 delays."""
    t06_15 = shift_start.replace(hour=6, minute=15, second=0)
    t06_25 = shift_start.replace(hour=6, minute=25, second=0)
    t06_35 = shift_start.replace(hour=6, minute=35, second=0)
    t10_00 = shift_start.replace(hour=10, minute=0, second=0)
    t10_10 = shift_start.replace(hour=10, minute=10, second=0)
    t10_25 = shift_start.replace(hour=10, minute=25, second=0)

    idx_06_15 = _time_to_index(shift_start, t06_15)
    idx_06_25 = _time_to_index(shift_start, t06_25)
    idx_06_35 = _time_to_index(shift_start, t06_35)
    idx_10_00 = _time_to_index(shift_start, t10_00)
    idx_10_10 = _time_to_index(shift_start, t10_10)
    idx_10_25 = _time_to_index(shift_start, t10_25)

    for (mid, sname), arr in sensor_values.items():
        if mid == "FILLER" and sname == "filler_bowl_level":
            arr[idx_06_15:idx_06_25] = np.linspace(75, 30, idx_06_25 - idx_06_15)
            arr[idx_06_25:idx_06_35] = np.linspace(30, 75, idx_06_35 - idx_06_25)
            arr[idx_10_00:idx_10_10] = np.linspace(75, 10, idx_10_10 - idx_10_00)
            arr[idx_10_10:idx_10_25] = np.linspace(10, 75, idx_10_25 - idx_10_10)
        if mid == "FILLER" and sname == "filler_carousel_speed":
            arr[idx_06_25:idx_06_35] = 300
            arr[idx_10_10:idx_10_25] = 0
            arr[idx_10_25:] = 410

    _propagate_stop_downstream("FILLER", idx_10_10, sensor_values, end_idx=idx_10_25)


def _propagate_stop_downstream(
    stopped_machine_id: str,
    stop_idx: int,
    sensor_values: dict[tuple[str, str], np.ndarray],
    end_idx: int | None = None,
) -> None:
    """Cascade a machine stop to ALL downstream machines (higher line_position).

    Delay ~16 sec per position gap.  Affects speed and counter sensors.
    If *end_idx* is given the stop is temporary (machines resume afterward).
    """
    stop_pos = _machine_line_position(stopped_machine_id)
    for m in MACHINES:
        if m.line_position <= stop_pos:
            continue
        delta = m.line_position - stop_pos
        delay_steps = delta * 8

        for s in SENSORS:
            if s.machine_id != m.id:
                continue
            key = (m.id, s.sensor_name)
            if key not in sensor_values:
                continue
            arr = sensor_values[key]
            actual_start = min(stop_idx + delay_steps, len(arr))
            actual_end = end_idx if end_idx is not None else len(arr)
            if actual_start >= actual_end:
                continue

            if s.sensor_type == "speed":
                _apply_downstream_starvation(arr, stop_idx, delay_steps, ramp_steps=5)
                if end_idx is not None:
                    prev = arr[max(0, stop_idx - 1)]
                    arr[actual_end:] = prev
            elif s.sensor_type == "counter":
                flat_val = arr[actual_start - 1] if actual_start > 0 else 0.0
                if end_idx is not None:
                    arr[actual_start:actual_end] = flat_val
                else:
                    arr[actual_start:] = flat_val


SCENARIO_MODIFIERS: dict[str, ScenarioModifier] = {
    "shift_1": _modifier_s1_baseline_smooth,
    "shift_2": _modifier_s2_downtime_triage,
    "shift_3": _modifier_s3_normal_operation,
    "shift_4": _modifier_s4_schedule_recovery,
    "shift_5": _modifier_s5_material_shortage,
    "shift_6": _modifier_s6_quality_deviation,
    "shift_7": _modifier_s7_upstream_delay,
}


# =============================================================================
# 6. MAIN GENERATION
# =============================================================================

def generate_shift_telemetry(shift_id: str) -> pd.DataFrame:
    """Generate telemetry for a single shift. Returns DataFrame with 950,400 rows."""
    scenario = next((s for s in SCENARIOS if s.shift_id == shift_id), None)
    if not scenario:
        raise ValueError(f"Unknown shift_id: {shift_id}")

    shift_start = SHIFT_START_DATETIMES[shift_id]

    # Timestamp array (2-second intervals)
    timestamps = np.array([
        shift_start + timedelta(seconds=i * INTERVAL_SEC)
        for i in range(TIMESTAMPS_PER_SHIFT)
    ])

    # Default: all machines running
    is_machine_running = {m.id: np.ones(TIMESTAMPS_PER_SHIFT, dtype=float) for m in MACHINES}

    shift_num = int(shift_id.split("_")[1])
    rng = np.random.default_rng(42 + shift_num)

    sensor_values: dict[tuple[str, str], np.ndarray] = {}

    for sensor in SENSORS:
        running = is_machine_running.get(sensor.machine_id, np.ones(TIMESTAMPS_PER_SHIFT))
        values = _generate_baseline_values(sensor, timestamps, running, rng, shift_id)
        sensor_values[(sensor.machine_id, sensor.sensor_name)] = values

    if shift_id in SCENARIO_MODIFIERS:
        SCENARIO_MODIFIERS[shift_id](shift_id, shift_start, timestamps, sensor_values, rng)

    rows: list[dict[str, object]] = []
    for sensor in SENSORS:
        key = (sensor.machine_id, sensor.sensor_name)
        arr = sensor_values[key]
        for i in range(TIMESTAMPS_PER_SHIFT):
            ts = shift_start + timedelta(seconds=i * INTERVAL_SEC)
            rows.append({
                "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S"),
                "shift_id": shift_id,
                "machine_id": sensor.machine_id,
                "sensor_name": sensor.sensor_name,
                "value": float(arr[i]),
            })

    return pd.DataFrame(rows)


def generate_all_telemetry() -> pd.DataFrame:
    """Generate telemetry for all 7 shifts. Returns concatenated DataFrame."""
    dfs: list[pd.DataFrame] = []
    for scenario in SCENARIOS:
        print(f"Generating telemetry for {scenario.shift_id} ({scenario.scenario_name})...")
        df = generate_shift_telemetry(scenario.shift_id)
        print(f"  → {len(df):,} rows")
        dfs.append(df)
    result = pd.concat(dfs, ignore_index=True)
    print(f"Total telemetry rows: {len(result):,}")
    return result


# =============================================================================
# 7. MAIN ENTRY
# =============================================================================

if __name__ == "__main__":
    # Quick test: S1 and S5 only
    print("=== Quick test: S1 and S5 ===\n")

    df1 = generate_shift_telemetry("shift_1")
    df5 = generate_shift_telemetry("shift_5")

    print(f"S1 total rows: {len(df1):,}")
    print(f"S5 total rows: {len(df5):,}")
    print(f"Combined: {len(df1) + len(df5):,}\n")

    # S5: CAPPER hopper_level at T=17:00, 18:30, 19:00, 19:30
    shift5_start = SHIFT_START_DATETIMES["shift_5"]
    times = ["17:00", "18:30", "19:00", "19:30"]
    print("S5 — CAPPER capper_cap_hopper_level:")
    for t_str in times:
        h, m = map(int, t_str.split(":"))
        t = shift5_start.replace(hour=h, minute=m, second=0)
        ts_str = t.strftime("%Y-%m-%dT%H:%M:%S")
        row = df5[(df5["machine_id"] == "CAPPER") & (df5["sensor_name"] == "capper_cap_hopper_level") & (df5["timestamp"] == ts_str)]
        if not row.empty:
            print(f"  T={t_str}: {row['value'].iloc[0]:.1f}%")
        else:
            # Check nearest
            matches = df5[(df5["machine_id"] == "CAPPER") & (df5["sensor_name"] == "capper_cap_hopper_level")]
            matches = matches[matches["timestamp"].str.startswith(t.strftime("%Y-%m-%d"))]
            if not matches.empty:
                first = matches.iloc[0]
                print(f"  T≈{t_str}: {first['value']:.1f}% (at {first['timestamp']})")

    # S1: all speed sensors at T=10:00
    print("\nS1 — Speed sensors at T=10:00:")
    t10 = SHIFT_START_DATETIMES["shift_1"].replace(hour=10, minute=0, second=0)
    ts_str = t10.strftime("%Y-%m-%dT%H:%M:%S")
    speed_rows = df1[(df1["sensor_name"].str.contains("speed", case=False)) & (df1["timestamp"] == ts_str)]
    for _, r in speed_rows.iterrows():
        print(f"  {r['machine_id']} {r['sensor_name']}: {r['value']:.1f}")
