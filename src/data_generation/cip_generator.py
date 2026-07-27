"""Generate cip_schedule table (~14-21 CIP events across 7 shifts).

CIP (Clean-In-Place) events occur on FILLER, BOTTLE_WASHER, and PASTEURIZER.
Typical durations: filler CIP 90-120 min, washer CIP 60 min, rinse 20 min.
"""

import random
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from .config import SCENARIOS

CIP_MACHINES = ["FILLER", "BOTTLE_WASHER", "PASTEURIZER"]

CIP_TYPES = {
    "FILLER": [
        {"cip_type": "full", "chemical": "Caustic + Acid", "duration_range": (90, 120)},
        {"cip_type": "rinse", "chemical": "Water only", "duration_range": (15, 25)},
    ],
    "BOTTLE_WASHER": [
        {"cip_type": "full", "chemical": "Caustic soda", "duration_range": (50, 70)},
        {"cip_type": "rinse", "chemical": "Water only", "duration_range": (15, 20)},
    ],
    "PASTEURIZER": [
        {"cip_type": "full", "chemical": "Acid descaler", "duration_range": (60, 90)},
        {"cip_type": "rinse", "chemical": "Water only", "duration_range": (10, 20)},
    ],
}

SHIFT_BASES = {
    "shift_1": datetime(2025, 1, 1, 8, 0),
    "shift_2": datetime(2025, 1, 1, 14, 0),
    "shift_3": datetime(2025, 1, 1, 22, 0),
    "shift_4": datetime(2025, 1, 2, 6, 0),
    "shift_5": datetime(2025, 1, 2, 14, 0),
    "shift_6": datetime(2025, 1, 2, 22, 0),
    "shift_7": datetime(2025, 1, 3, 6, 0),
}


def generate_cip_schedule() -> pd.DataFrame:
    """Generate CIP schedule with 2-3 events per shift."""
    random.seed(42)
    rows: list[dict[str, Any]] = []
    cip_id = 1

    for scenario in SCENARIOS:
        sid = scenario.shift_id
        base = SHIFT_BASES[sid]

        for machine_id in CIP_MACHINES:
            if random.random() < 0.6:
                continue

            cip_info = random.choice(CIP_TYPES[machine_id])
            dur_min, dur_max = cip_info["duration_range"]
            duration = random.randint(dur_min, dur_max)

            offset_minutes = random.randint(30, 360)
            planned_start = base + timedelta(minutes=offset_minutes)
            planned_end = planned_start + timedelta(minutes=duration)

            delay = random.choice([0, 0, 0, 5, 10, 15, -5])
            actual_start = planned_start + timedelta(minutes=delay)
            overrun = random.choice([0, 0, 0, 5, 10, 15, 20])
            actual_end = actual_start + timedelta(minutes=duration + overrun)

            if overrun > 10:
                status = "overrun"
            elif delay > 0:
                status = "completed"
            else:
                status = "completed"

            if scenario.scenario_type == "baseline" and random.random() < 0.3:
                status = "planned"
                actual_start = None
                actual_end = None

            rows.append({
                "cip_id": f"CIP-{cip_id:03d}",
                "shift_id": sid,
                "machine_id": machine_id,
                "planned_start": planned_start.isoformat(),
                "planned_end": planned_end.isoformat(),
                "actual_start": actual_start.isoformat() if actual_start else None,
                "actual_end": actual_end.isoformat() if actual_end else None,
                "cip_type": cip_info["cip_type"],
                "chemical_used": cip_info["chemical"],
                "duration_minutes": duration + overrun if actual_end else duration,
                "status": status,
            })
            cip_id += 1

    return pd.DataFrame(rows)


if __name__ == "__main__":
    df = generate_cip_schedule()
    print(f"cip_schedule: {len(df)} rows")
    print(df[["shift_id", "machine_id", "cip_type", "status", "duration_minutes"]].to_string())
