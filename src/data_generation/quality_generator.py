"""Generate quality_checks table (200-300 rows).

Quality checks every ~15-20 minutes per shift, covering key process parameters.
Scenario-specific patterns for pass/fail rates.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from .config import SCENARIOS

# Shift start datetimes (same as telemetry)
SHIFT_START_DATETIMES: dict[str, datetime] = {
    "shift_1": datetime(2025, 1, 1, 8, 0, 0),
    "shift_2": datetime(2025, 1, 1, 14, 0, 0),
    "shift_3": datetime(2025, 1, 1, 22, 0, 0),
    "shift_4": datetime(2025, 1, 2, 6, 0, 0),
    "shift_5": datetime(2025, 1, 2, 14, 0, 0),
    "shift_6": datetime(2025, 1, 2, 22, 0, 0),
    "shift_7": datetime(2025, 1, 3, 6, 0, 0),
}

# Quality check definitions: (machine_id, parameter, spec_min, spec_max, nominal)
QUALITY_CHECKS_DEF: list[tuple[str, str, float, float, float]] = [
    ("FILLER", "fill_volume", 325.0, 335.0, 330.0),
    ("CAPPER", "cap_torque", 1.5, 2.5, 2.0),
    ("LABELLER", "label_alignment", 0.2, 2.0, 0.5),
    ("CHECKWEIGHER", "case_weight", 7800.0, 8100.0, 7920.0),
]


def _should_fail(
    shift_id: str,
    machine_id: str,
    parameter: str,
    check_time: datetime,
    rng: np.random.Generator,
) -> bool:
    """Determine if this check should fail based on scenario-specific patterns."""
    shift_start = SHIFT_START_DATETIMES[shift_id]

    # S1, S3: Nearly all pass (>95% pass rate)
    if shift_id in ("shift_1", "shift_3"):
        return rng.random() < 0.03  # ~3% fail

    # S2: Elevated label_alignment failures around 16:00-16:15
    if shift_id == "shift_2" and machine_id == "LABELLER" and parameter == "label_alignment":
        t16_00 = shift_start.replace(hour=16, minute=0, second=0)
        t16_15 = shift_start.replace(hour=16, minute=15, second=0)
        if t16_00 <= check_time <= t16_15:
            return rng.random() < 0.6  # ~60% fail in window
        return rng.random() < 0.03

    # S4: Some fill_volume failures after changeover (06:45-07:05)
    if shift_id == "shift_4" and machine_id == "FILLER" and parameter == "fill_volume":
        t06_45 = shift_start.replace(hour=6, minute=45, second=0)
        t07_05 = shift_start.replace(hour=7, minute=5, second=0)
        if t06_45 <= check_time <= t07_05:
            return rng.random() < 0.5  # ~50% fail in window
        return rng.random() < 0.03

    # S5: cap_torque failures near 18:00-19:00 (as hopper depletes)
    if shift_id == "shift_5" and machine_id == "CAPPER" and parameter == "cap_torque":
        t18_00 = shift_start.replace(hour=18, minute=0, second=0)
        t19_00 = shift_start.replace(hour=19, minute=0, second=0)
        if t18_00 <= check_time <= t19_00:
            return rng.random() < 0.55  # ~55% fail in window
        return rng.random() < 0.03

    # S6: fill_volume failures (00:30-01:00) + label_alignment failures (00:40-01:00)
    if shift_id == "shift_6":
        # shift_6: 22:00-06:00, so 00:30 is 2h30 into shift
        t00_30 = shift_start + timedelta(hours=2, minutes=30)
        t00_40 = shift_start + timedelta(hours=2, minutes=40)
        t01_00 = shift_start + timedelta(hours=3, minutes=0)
        if machine_id == "FILLER" and parameter == "fill_volume":
            if t00_30 <= check_time <= t01_00:
                return rng.random() < 0.5
        if machine_id == "LABELLER" and parameter == "label_alignment":
            if t00_40 <= check_time <= t01_00:
                return rng.random() < 0.5
        return rng.random() < 0.03

    # S7: Some fill_volume variations during upstream delays
    if shift_id == "shift_7" and machine_id == "FILLER" and parameter == "fill_volume":
        t06_15 = shift_start.replace(hour=6, minute=15, second=0)
        t06_35 = shift_start.replace(hour=6, minute=35, second=0)
        t10_00 = shift_start.replace(hour=10, minute=0, second=0)
        t10_25 = shift_start.replace(hour=10, minute=25, second=0)
        if (t06_15 <= check_time <= t06_35) or (t10_00 <= check_time <= t10_25):
            return rng.random() < 0.3  # ~30% fail during delays
        return rng.random() < 0.03

    return rng.random() < 0.03


def _generate_value(
    spec_min: float,
    spec_max: float,
    nominal: float,
    should_fail: bool,
    rng: np.random.Generator,
) -> float:
    """Generate a value within or outside spec based on should_fail."""
    if should_fail:
        if rng.random() < 0.5:
            return max(0.0, spec_min - rng.uniform(0.5, 3.0))
        return spec_max + rng.uniform(0.5, 3.0)
    # Pass: value within spec
    return nominal + rng.uniform(-(nominal - spec_min) * 0.8, (spec_max - nominal) * 0.8)


def _scenario_forced_checks(shift_id: str) -> list[tuple[datetime, int]]:
    """Return (check_time, quality_def_idx) pairs for scenario-critical windows.

    These guarantee quality checks occur during the narrow failure windows so
    that scenario patterns are reliably visible regardless of random scheduling.
    """
    start = SHIFT_START_DATETIMES[shift_id]
    forced: list[tuple[datetime, int]] = []

    if shift_id == "shift_2":
        for m in (0, 5, 10):
            forced.append((start.replace(hour=16, minute=m), 2))  # LABELLER

    elif shift_id == "shift_4":
        for m in (48, 55):
            forced.append((start.replace(hour=6, minute=m), 0))  # FILLER
        forced.append((start.replace(hour=7, minute=2), 0))  # FILLER

    elif shift_id == "shift_5":
        for m in (10, 30, 50):
            forced.append((start.replace(hour=18, minute=m), 1))  # CAPPER

    elif shift_id == "shift_6":
        for m in (35, 45, 55):
            forced.append((start + timedelta(hours=2, minutes=m), 0))  # FILLER
        for m in (42, 52):
            forced.append((start + timedelta(hours=2, minutes=m), 2))  # LABELLER

    elif shift_id == "shift_7":
        for m in (20, 30):
            forced.append((start.replace(hour=6, minute=m), 0))  # FILLER
        forced.append((start.replace(hour=10, minute=10), 0))

    return forced


def generate_quality_checks() -> pd.DataFrame:
    """Generate quality_checks table (200-300 rows)."""
    rows: list[dict[str, Any]] = []
    check_counter = 1
    rng = np.random.default_rng(42)

    for scenario in SCENARIOS:
        shift_id = scenario.shift_id
        shift_start = SHIFT_START_DATETIMES[shift_id]
        shift_end = shift_start + timedelta(hours=8)

        n_checks = rng.integers(28, 37)
        interval_min = 480 / n_checks
        schedule: list[tuple[datetime, int]] = [
            (shift_start + timedelta(minutes=i * interval_min), i % len(QUALITY_CHECKS_DEF))
            for i in range(n_checks)
        ]
        schedule.extend(_scenario_forced_checks(shift_id))
        schedule.sort(key=lambda t: t[0])

        for check_time, idx in schedule:
            if check_time >= shift_end:
                continue
            machine_id, parameter, spec_min, spec_max, nominal = QUALITY_CHECKS_DEF[idx]

            should_fail_flag = _should_fail(
                shift_id, machine_id, parameter, check_time, rng
            )
            value = _generate_value(
                spec_min, spec_max, nominal, should_fail_flag, rng
            )
            result = "fail" if (value < spec_min or value > spec_max) else "pass"

            rows.append({
                "check_id": f"QC{check_counter:03d}",
                "shift_id": shift_id,
                "machine_id": machine_id,
                "timestamp": check_time.strftime("%Y-%m-%dT%H:%M:%S"),
                "parameter": parameter,
                "value": round(value, 2),
                "spec_min": spec_min,
                "spec_max": spec_max,
                "result": result,
            })
            check_counter += 1

    return pd.DataFrame(rows)


if __name__ == "__main__":
    df = generate_quality_checks()
    print(f"quality_checks: {len(df)} rows")
    print(f"  pass: {(df['result'] == 'pass').sum()}")
    print(f"  fail: {(df['result'] == 'fail').sum()}")
    print(df.head(10).to_string())
