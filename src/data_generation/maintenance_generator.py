"""Generate maintenance_schedule table (~14-28 work orders across 7 shifts).

Includes planned PM (preventive maintenance), corrective maintenance linked to
major events, and emergency repairs. Technician assignments from crew pool.
"""

import random
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from .config import SCENARIOS

SHIFT_BASES = {
    "shift_1": datetime(2025, 1, 1, 8, 0),
    "shift_2": datetime(2025, 1, 1, 14, 0),
    "shift_3": datetime(2025, 1, 1, 22, 0),
    "shift_4": datetime(2025, 1, 2, 6, 0),
    "shift_5": datetime(2025, 1, 2, 14, 0),
    "shift_6": datetime(2025, 1, 2, 22, 0),
    "shift_7": datetime(2025, 1, 3, 6, 0),
}

PM_SCHEDULE = [
    {"machine_id": "FILLER", "description": "Valve inspection and O-ring replacement", "duration": 45},
    {"machine_id": "LABELLER", "description": "Glue nozzle cleaning and calibration", "duration": 30},
    {"machine_id": "PASTEURIZER", "description": "Temperature probe calibration", "duration": 20},
    {"machine_id": "CAPPER", "description": "Torque head adjustment and lubrication", "duration": 25},
    {"machine_id": "CASE_PACKER", "description": "Magazine alignment and belt tension check", "duration": 20},
    {"machine_id": "BOTTLE_WASHER", "description": "Spray nozzle descaling", "duration": 35},
]

CORRECTIVE_EVENTS = {
    "shift_2": [{"machine_id": "LABELLER", "description": "Glue system repair after blockage", "duration": 40, "type": "corrective"}],
    "shift_4": [{"machine_id": "FILLER", "description": "Bowl level sensor replacement", "duration": 25, "type": "corrective"}],
    "shift_5": [{"machine_id": "CAPPER", "description": "Emergency cap hopper jam clearance", "duration": 15, "type": "emergency"}],
    "shift_6": [{"machine_id": "FILLER", "description": "Fill volume calibration after quality hold", "duration": 30, "type": "corrective"}],
    "shift_7": [{"machine_id": "FILLER", "description": "Bowl supply line inspection", "duration": 20, "type": "corrective"}],
}

TECHNICIANS = ["Klaus Wagner", "Stefan Hoffmann"]


def generate_maintenance_schedule() -> pd.DataFrame:
    """Generate maintenance_schedule with PM and corrective/emergency work orders."""
    random.seed(44)
    rows: list[dict[str, Any]] = []
    wo_id = 1

    for scenario in SCENARIOS:
        sid = scenario.shift_id
        base = SHIFT_BASES[sid]

        for pm in random.sample(PM_SCHEDULE, k=min(2, len(PM_SCHEDULE))):
            offset = random.randint(60, 360)
            planned_start = base + timedelta(minutes=offset)
            planned_end = planned_start + timedelta(minutes=pm["duration"])

            completed = random.random() < 0.7
            if completed:
                delay = random.choice([0, 0, 5, 10])
                actual_start = planned_start + timedelta(minutes=delay)
                overrun = random.choice([0, 0, 5])
                actual_end = actual_start + timedelta(minutes=pm["duration"] + overrun)
                status = "completed"
            else:
                actual_start = None
                actual_end = None
                status = "planned"

            rows.append({
                "work_order_id": f"WO-{wo_id:04d}",
                "shift_id": sid,
                "machine_id": pm["machine_id"],
                "maintenance_type": "PM",
                "planned_start": planned_start.isoformat(),
                "planned_end": planned_end.isoformat(),
                "actual_start": actual_start.isoformat() if actual_start else None,
                "actual_end": actual_end.isoformat() if actual_end else None,
                "technician": random.choice(TECHNICIANS),
                "status": status,
                "description": pm["description"],
            })
            wo_id += 1

        for event in CORRECTIVE_EVENTS.get(sid, []):
            offset = random.randint(120, 400)
            actual_start = base + timedelta(minutes=offset)
            actual_end = actual_start + timedelta(minutes=event["duration"])

            rows.append({
                "work_order_id": f"WO-{wo_id:04d}",
                "shift_id": sid,
                "machine_id": event["machine_id"],
                "maintenance_type": event["type"],
                "planned_start": None,
                "planned_end": None,
                "actual_start": actual_start.isoformat(),
                "actual_end": actual_end.isoformat(),
                "technician": random.choice(TECHNICIANS),
                "status": "completed",
                "description": event["description"],
            })
            wo_id += 1

    return pd.DataFrame(rows)


if __name__ == "__main__":
    df = generate_maintenance_schedule()
    print(f"maintenance_schedule: {len(df)} rows")
    print(df[["shift_id", "machine_id", "maintenance_type", "status", "description"]].to_string())
