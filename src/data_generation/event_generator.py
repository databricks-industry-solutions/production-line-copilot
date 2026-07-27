"""Event log generation — 30–50 rows aligned to telemetry scenario patterns.

Generates the event_log table with events matching what telemetry_generator produces.
Uses config.SCENARIOS, config.MACHINES for structure.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from .config import MACHINES

# Shift start datetimes (must match telemetry_generator)
SHIFT_START_DATETIMES: dict[str, datetime] = {
    "shift_1": datetime(2025, 1, 1, 8, 0, 0),
    "shift_2": datetime(2025, 1, 1, 14, 0, 0),
    "shift_3": datetime(2025, 1, 1, 22, 0, 0),
    "shift_4": datetime(2025, 1, 2, 6, 0, 0),
    "shift_5": datetime(2025, 1, 2, 14, 0, 0),
    "shift_6": datetime(2025, 1, 2, 22, 0, 0),
    "shift_7": datetime(2025, 1, 3, 6, 0, 0),
}

# Machines with speed sensors (for random micro-stop selection)
_MACHINES_WITH_SPEED = [m.id for m in MACHINES if m.has_speed_sensor]


def _ts(base: datetime, hour: int, minute: int, second: int = 0) -> str:
    """Return ISO timestamp from base date with given time."""
    t = base.replace(hour=hour, minute=minute, second=second)
    return t.strftime("%Y-%m-%dT%H:%M:%S")


def _ts_delta(base: datetime, hours: int = 0, minutes: int = 0, seconds: int = 0) -> str:
    """Return ISO timestamp from base + delta."""
    t = base + timedelta(hours=hours, minutes=minutes, seconds=seconds)
    return t.strftime("%Y-%m-%dT%H:%M:%S")


def _random_machine(seed: str = "") -> str:
    """Deterministic 'random' machine for micro-stops (reproducible)."""
    import hashlib
    h = int(hashlib.sha256(f"event_random_machine_{seed}".encode()).hexdigest()[:8], 16)
    return _MACHINES_WITH_SPEED[h % len(_MACHINES_WITH_SPEED)]


def _event(
    event_id: str,
    shift_id: str,
    machine_id: str,
    event_type: str,
    severity: str,
    timestamp_start: str,
    timestamp_end: str | None,
    duration_minutes: float,
    root_cause: str,
    description: str,
    corrective_action: str,
    resolution_minutes: float,
    outcome_oee_impact: float,
    outcome_description: str,
    similar_event_ids: str,
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "shift_id": shift_id,
        "machine_id": machine_id,
        "event_type": event_type,
        "severity": severity,
        "timestamp_start": timestamp_start,
        "timestamp_end": timestamp_end,
        "duration_minutes": duration_minutes,
        "root_cause": root_cause,
        "description": description,
        "corrective_action": corrective_action,
        "resolution_minutes": resolution_minutes,
        "outcome_oee_impact": outcome_oee_impact,
        "outcome_description": outcome_description,
        "similar_event_ids": similar_event_ids,
    }


def generate_event_log() -> pd.DataFrame:
    """Generate event_log table (30–50 rows) aligned to telemetry scenario patterns."""
    events: list[dict[str, Any]] = []

    # -------------------------------------------------------------------------
    # S1 — Baseline Smooth (3 events)
    # -------------------------------------------------------------------------
    s1 = SHIFT_START_DATETIMES["shift_1"]
    m1, m2, m3 = _random_machine("E001"), _random_machine("E002"), _random_machine("E003")
    events.append(_event(
        "E001", "shift_1", m1, "micro_stop", "minor",
        _ts_delta(s1, minutes=90),  # ~09:30
        _ts_delta(s1, minutes=90, seconds=25),
        25 / 60, "mechanical",
        "Brief speed drop — minor mechanical hesitation",
        "", 0, 0, "",
        "",
    ))
    events.append(_event(
        "E002", "shift_1", m2, "micro_stop", "minor",
        _ts_delta(s1, hours=3, minutes=45),  # ~11:45
        _ts_delta(s1, hours=3, minutes=45, seconds=20),
        20 / 60, "mechanical",
        "Short stop — sensor reset",
        "", 0, 0, "",
        "",
    ))
    events.append(_event(
        "E003", "shift_1", m3, "micro_stop", "minor",
        _ts_delta(s1, hours=6, minutes=0),  # ~14:00
        _ts_delta(s1, hours=6, minutes=0, seconds=30),
        30 / 60, "mechanical",
        "Micro-stop — belt slip recovery",
        "", 0, 0, "",
        "",
    ))

    # -------------------------------------------------------------------------
    # S2 — Downtime Triage (6 events)
    # -------------------------------------------------------------------------
    s2 = SHIFT_START_DATETIMES["shift_2"]
    events.append(_event(
        "E004", "shift_2", "LABELLER", "near_miss", "minor",
        _ts(s2, 15, 50), _ts(s2, 15, 50, 25),  # 15:50:25
        25 / 60, "mechanical",
        "Label vision NOK rising — precursor to adhesive failure",
        "Operator monitored", 25 / 60, 0, "",
        "",
    ))
    events.append(_event(
        "E005", "shift_2", "LABELLER", "near_miss", "minor",
        _ts(s2, 16, 5), _ts(s2, 16, 5, 20),
        20 / 60, "mechanical",
        "Short micro-stop — label misalignment",
        "Operator adjusted", 20 / 60, 0, "",
        "",
    ))
    events.append(_event(
        "E006", "shift_2", "LABELLER", "major_stop", "critical",
        _ts(s2, 16, 15), _ts(s2, 16, 23),
        8, "mechanical",
        "Label misalignment — adhesive failure",
        "Switched label roll, reduced speed to 380 BPM",
        8, -0.08, "Recovery at 380 BPM",
        "E031",
    ))
    events.append(_event(
        "E007", "shift_2", "LABELLER", "speed_reduction", "major",
        _ts(s2, 16, 23), None,
        0, "mechanical",
        "Recovery at 380 BPM (not full 420)",
        "Continued monitoring", 0, -0.02, "",
        "",
    ))

    # -------------------------------------------------------------------------
    # S3 — Normal Operation (2 events)
    # -------------------------------------------------------------------------
    s3 = SHIFT_START_DATETIMES["shift_3"]
    m8, m9 = _random_machine("E008"), _random_machine("E009")
    events.append(_event(
        "E008", "shift_3", m8, "micro_stop", "minor",
        _ts_delta(s3, hours=2, minutes=30), _ts_delta(s3, hours=2, minutes=30, seconds=20),
        20 / 60, "mechanical",
        "Brief stop — routine",
        "", 0, 0, "",
        "",
    ))
    events.append(_event(
        "E009", "shift_3", m9, "micro_stop", "minor",
        _ts_delta(s3, hours=4, minutes=0), _ts_delta(s3, hours=4, minutes=0, seconds=25),
        25 / 60, "mechanical",
        "Micro-stop — minor",
        "", 0, 0, "",
        "",
    ))

    # -------------------------------------------------------------------------
    # S4 — Schedule Recovery (4 events)
    # -------------------------------------------------------------------------
    s4 = SHIFT_START_DATETIMES["shift_4"]
    events.append(_event(
        "E010", "shift_4", "FILLER", "changeover", "info",
        _ts(s4, 6, 0), _ts(s4, 6, 45),
        45, "planned",
        "SKU changeover PILSNER→WHEAT",
        "Planned changeover completed", 45, 0, "Changeover complete",
        "",
    ))
    events.append(_event(
        "E011", "shift_4", "FILLER", "major_stop", "major",
        _ts(s4, 6, 50), _ts(s4, 7, 5),
        15, "mechanical",
        "Post-changeover fault — fill valve misalignment",
        "Recalibrated fill valves",
        15, -0.12, "Full recovery",
        "",
    ))
    events.append(_event(
        "E012", "shift_4", "FILLER", "speed_reduction", "major",
        _ts(s4, 7, 5), None,
        0, "mechanical",
        "Recovery at reduced speed post-fault",
        "Gradual ramp-up", 0, -0.02, "",
        "",
    ))
    events.append(_event(
        "E013", "shift_4", _random_machine("E013"), "micro_stop", "minor",
        _ts_delta(s4, hours=4, minutes=0), _ts_delta(s4, hours=4, minutes=0, seconds=25),
        25 / 60, "mechanical",
        "Micro-stop — minor",
        "", 0, 0, "",
        "",
    ))

    # -------------------------------------------------------------------------
    # S5 — Material Shortage MVP (6 events)
    # -------------------------------------------------------------------------
    s5 = SHIFT_START_DATETIMES["shift_5"]
    events.append(_event(
        "E014", "shift_5", "CAPPER", "near_miss", "minor",
        _ts(s5, 17, 0), _ts(s5, 17, 0, 25),
        25 / 60, "material",
        "Cap hopper level low (40%) — torque fluctuation",
        "Operator alerted", 25 / 60, 0, "",
        "",
    ))
    events.append(_event(
        "E015", "shift_5", "CAPPER", "near_miss", "major",
        _ts(s5, 18, 30), _ts(s5, 18, 30, 20),
        20 / 60, "material",
        "Cap hopper level critical (20%) — alarm threshold",
        "Emergency cap order placed", 20 / 60, 0, "",
        "",
    ))
    events.append(_event(
        "E016", "shift_5", "CAPPER", "major_stop", "critical",
        _ts(s5, 19, 0), _ts(s5, 19, 35),
        35, "material",
        "Cap hopper depleted — CAPPER stopped",
        "Emergency cap delivery ordered, manual cap loading",
        35, -0.18, "Recovered after manual cap loading",
        "",
    ))
    events.append(_event(
        "E017", "shift_5", "FILLER", "upstream_delay", "major",
        _ts(s5, 19, 0), None,
        0, "external",
        "FILLER slowing at 19:00 due to downstream backup",
        "Reduced speed to 70%", 0, -0.05, "",
        "",
    ))
    events.append(_event(
        "E018", "shift_5", "CASE_PACKER", "major_stop", "critical",
        _ts(s5, 19, 1), None,
        34, "material",
        "Starvation from CAPPER — no bottles to pack",
        "Waiting for CAPPER recovery", 34, -0.10, "",
        "",
    ))
    events.append(_event(
        "E019", "shift_5", _random_machine("E019"), "micro_stop", "minor",
        _ts_delta(s5, hours=1, minutes=30), _ts_delta(s5, hours=1, minutes=30, seconds=20),
        20 / 60, "mechanical",
        "Micro-stop — minor",
        "", 0, 0, "",
        "",
    ))

    # -------------------------------------------------------------------------
    # S6 — Quality Deviation (5 events)
    # -------------------------------------------------------------------------
    s6 = SHIFT_START_DATETIMES["shift_6"]
    events.append(_event(
        "E020", "shift_6", "FILLER", "near_miss", "minor",
        _ts_delta(s6, hours=2, minutes=30), _ts_delta(s6, hours=2, minutes=30, seconds=10),
        10 / 60, "process",
        "Fill volume drift detected",
        "Operator monitored", 10 / 60, 0, "",
        "",
    ))
    events.append(_event(
        "E021", "shift_6", "LABELLER", "near_miss", "minor",
        _ts_delta(s6, hours=2, minutes=40), _ts_delta(s6, hours=2, minutes=40, seconds=20),
        20 / 60, "quality",
        "LABELLER vision NOK rising",
        "Operator monitored", 20 / 60, 0, "",
        "",
    ))
    events.append(_event(
        "E022", "shift_6", "FILLER", "quality_hold", "critical",
        _ts_delta(s6, hours=3, minutes=0), _ts_delta(s6, hours=3, minutes=25),
        25, "quality",
        "Quality hold — fill volume out of spec + elevated label NOK",
        "Recalibrated filler, switched label roll, held 200 cases for inspection",
        25, -0.15, "Line released after inspection",
        "",
    ))
    events.append(_event(
        "E023", "shift_6", "FILLER", "speed_reduction", "info",
        _ts_delta(s6, hours=3, minutes=25), None,
        0, "quality",
        "Recovery at normal parameters",
        "Gradual ramp-up", 0, 0, "",
        "",
    ))
    events.append(_event(
        "E024", "shift_6", _random_machine("E024"), "micro_stop", "minor",
        _ts_delta(s6, hours=5, minutes=0), _ts_delta(s6, hours=5, minutes=0, seconds=25),
        25 / 60, "mechanical",
        "Micro-stop — minor",
        "", 0, 0, "",
        "",
    ))

    # -------------------------------------------------------------------------
    # S7 — Upstream Delay (6 events)
    # -------------------------------------------------------------------------
    s7 = SHIFT_START_DATETIMES["shift_7"]
    events.append(_event(
        "E025", "shift_7", "FILLER", "upstream_delay", "major",
        _ts(s7, 6, 15), _ts(s7, 6, 35),
        20, "external",
        "FILLER bowl_level dropping",
        "Upstream supply issue", 20, -0.05, "",
        "",
    ))
    events.append(_event(
        "E026", "shift_7", "FILLER", "speed_reduction", "major",
        _ts(s7, 6, 25), _ts(s7, 6, 35),
        10, "external",
        "FILLER reduced to 300 BPM",
        "Bowl level recovery", 10, -0.03, "",
        "",
    ))
    events.append(_event(
        "E027", "shift_7", "FILLER", "upstream_delay", "info",
        _ts(s7, 6, 35), _ts(s7, 6, 35),
        0, "external",
        "FILLER recovery — bowl refilled",
        "Normal operation resumed", 0, 0, "",
        "",
    ))
    events.append(_event(
        "E028", "shift_7", "FILLER", "upstream_delay", "major",
        _ts(s7, 10, 0), _ts(s7, 10, 25),
        25, "external",
        "FILLER bowl second drop",
        "Upstream supply failure", 25, -0.08, "",
        "",
    ))
    events.append(_event(
        "E029", "shift_7", "FILLER", "major_stop", "critical",
        _ts(s7, 10, 10), _ts(s7, 10, 25),
        15, "external",
        "Bowl starvation — upstream supply failure",
        "Emergency supply, bowl refill", 15, -0.10, "",
        "",
    ))
    events.append(_event(
        "E030", "shift_7", "FILLER", "upstream_delay", "info",
        _ts(s7, 10, 25), _ts(s7, 10, 25),
        0, "external",
        "FILLER recovery",
        "Normal operation resumed at 410 BPM", 0, 0, "",
        "",
    ))

    # -------------------------------------------------------------------------
    # Cross-shift reference event (E031 — for S2 E006 similar_event_ids)
    # -------------------------------------------------------------------------
    events.append(_event(
        "E031", "shift_6", "LABELLER", "major_stop", "critical",
        _ts_delta(s6, hours=3, minutes=0), _ts_delta(s6, hours=3, minutes=25),
        25, "quality",
        "Label quality hold (part of E022) — label roll switch",
        "Switched label roll, recalibrated",
        25, -0.05, "Cross-reference for S2 E006 similar case",
        "E006",
    ))

    # -------------------------------------------------------------------------
    # Safety incidents (E032-E034, ~1-2 per week)
    # -------------------------------------------------------------------------
    events.append(_event(
        "E032", "shift_2", "CASE_PACKER", "safety_near_miss", "major",
        _ts_delta(s2, hours=4, minutes=10), _ts_delta(s2, hours=4, minutes=10),
        0, "human_error",
        "Near miss — operator hand near packer infeed during jam clearance",
        "Lockout-tagout refresher training scheduled, guard interlock verified",
        0, 0, "No injury, procedure review initiated",
        "",
    ))
    events.append(_event(
        "E033", "shift_5", "STRETCH_WRAPPER", "safety_first_aid", "minor",
        _ts_delta(s5, hours=5, minutes=30), _ts_delta(s5, hours=5, minutes=45),
        15, "mechanical",
        "First aid — minor cut from stretch film edge during roll change",
        "First aid applied, operator resumed duties. Glove specification upgraded.",
        15, 0, "Minor injury, operator returned to work",
        "",
    ))
    events.append(_event(
        "E034", "shift_7", "CONVEYOR_3", "safety_near_miss", "info",
        _ts_delta(s7, hours=2, minutes=20), _ts_delta(s7, hours=2, minutes=20),
        0, "environmental",
        "Near miss — wet floor near conveyor 3 from pasteurizer condensate",
        "Spill cleaned, drip tray adjusted, area cordoned during fix",
        0, 0, "No injury, housekeeping issue resolved",
        "",
    ))

    return pd.DataFrame(events)


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    df = generate_event_log()
    print("=== Event Log Summary ===\n")
    print(f"Total events: {len(df)}\n")
    for shift_id in sorted(df["shift_id"].unique()):
        subset = df[df["shift_id"] == shift_id]
        print(f"{shift_id} ({len(subset)} events):")
        for _, row in subset.iterrows():
            print(f"  {row['event_id']}: {row['event_type']} | {row['machine_id']} | {row['severity']} | {row['description'][:50]}...")
        print()
