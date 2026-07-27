"""Generate shift_crew table (~56-84 rows = 7 shifts x 8-12 crew).

Tracks operators, mechanics, QA, and supervisors per shift.
Includes skill matrix and occasional absences for realism.
"""

import random
from typing import Any

import pandas as pd

from .config import SCENARIOS

CREW_POOL = [
    {"operator_id": "OP001", "name": "Jan Müller", "role": "operator", "skills": ["FILLER", "CAPPER", "LABELLER"], "skill_level": 5},
    {"operator_id": "OP002", "name": "Anna Schmidt", "role": "operator", "skills": ["DEPALLETIZER", "UNCASER", "CONVEYOR_1"], "skill_level": 4},
    {"operator_id": "OP003", "name": "Peter Weber", "role": "operator", "skills": ["CASE_PACKER", "PALLETIZER", "STRETCH_WRAPPER"], "skill_level": 4},
    {"operator_id": "OP004", "name": "Lisa Fischer", "role": "operator", "skills": ["BOTTLE_WASHER", "EMPTY_INSPECTOR"], "skill_level": 3},
    {"operator_id": "OP005", "name": "Tom Braun", "role": "operator", "skills": ["FILLER", "PASTEURIZER"], "skill_level": 3},
    {"operator_id": "OP006", "name": "Maria Koch", "role": "operator", "skills": ["LABELLER", "CODER", "SHRINK_WRAPPER"], "skill_level": 4},
    {"operator_id": "OP007", "name": "Max Bauer", "role": "operator", "skills": ["PALLETIZER", "CASE_PACKER", "CHECKWEIGHER"], "skill_level": 2},
    {"operator_id": "OP008", "name": "Sarah Richter", "role": "operator", "skills": ["FILLER", "CAPPER"], "skill_level": 5},
    {"operator_id": "MEC01", "name": "Klaus Wagner", "role": "mechanic", "skills": ["FILLER", "CAPPER", "LABELLER", "PASTEURIZER"], "skill_level": 5},
    {"operator_id": "MEC02", "name": "Stefan Hoffmann", "role": "mechanic", "skills": ["CASE_PACKER", "PALLETIZER", "STRETCH_WRAPPER", "DEPALLETIZER"], "skill_level": 4},
    {"operator_id": "QA001", "name": "Elena Schäfer", "role": "qa", "skills": ["FILLER", "CHECKWEIGHER", "PASTEURIZER", "LABELLER"], "skill_level": 4},
    {"operator_id": "SUP01", "name": "Frank Zimmerman", "role": "supervisor", "skills": [], "skill_level": 5},
]

MACHINE_ASSIGNMENTS = {
    "OP001": "FILLER",
    "OP002": "DEPALLETIZER",
    "OP003": "CASE_PACKER",
    "OP004": "BOTTLE_WASHER",
    "OP005": "PASTEURIZER",
    "OP006": "LABELLER",
    "OP007": "PALLETIZER",
    "OP008": "CAPPER",
    "MEC01": None,
    "MEC02": None,
    "QA001": None,
    "SUP01": None,
}

ABSENCE_SHIFTS = {"shift_2": "OP005", "shift_6": "OP007"}
LATE_SHIFTS = {"shift_4": "OP002"}


def generate_shift_crew() -> pd.DataFrame:
    """Generate shift_crew with 10-12 crew per shift, absences on specific shifts."""
    random.seed(43)
    rows: list[dict[str, Any]] = []

    for scenario in SCENARIOS:
        sid = scenario.shift_id

        for person in CREW_POOL:
            pid = person["operator_id"]

            if pid == ABSENCE_SHIFTS.get(sid):
                status = "absent"
            elif pid == LATE_SHIFTS.get(sid):
                status = "late"
            else:
                status = "present"

            rows.append({
                "shift_id": sid,
                "operator_id": pid,
                "operator_name": person["name"],
                "role": person["role"],
                "assigned_machine": MACHINE_ASSIGNMENTS.get(pid),
                "skill_level": person["skill_level"],
                "skills": ",".join(person["skills"]),
                "status": status,
            })

    return pd.DataFrame(rows)


if __name__ == "__main__":
    df = generate_shift_crew()
    print(f"shift_crew: {len(df)} rows")
    absent = df[df["status"] != "present"]
    print("\nAbsences/late:")
    print(absent[["shift_id", "operator_name", "role", "status"]].to_string())
