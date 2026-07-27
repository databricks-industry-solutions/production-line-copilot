"""Generate static/reference tables as Pandas DataFrames from config constants.

These tables are written to Unity Catalog by main.py. Used for machine_sensors,
machine_bom, scenario_index, kpi_recommendations, scenario_moments, line_constraints.
"""

from __future__ import annotations

import pandas as pd

from .config import (
    LINE_CONSTRAINTS,
    MACHINES,
    MATERIALS,
    PRIMARY_SENSOR_MAPPING,
    SCENARIOS,
    SENSORS,
)

# Shift date mapping for ISO timestamps (design §2.3)
_SHIFT_START_DATES: dict[str, str] = {
    "shift_1": "2025-01-01",
    "shift_2": "2025-01-01",
    "shift_3": "2025-01-01",
    "shift_4": "2025-01-02",
    "shift_5": "2025-01-02",
    "shift_6": "2025-01-02",
    "shift_7": "2025-01-03",
}
_SHIFT_END_DATES: dict[str, str] = {
    "shift_1": "2025-01-01",
    "shift_2": "2025-01-01",
    "shift_3": "2025-01-02",  # spans midnight
    "shift_4": "2025-01-02",
    "shift_5": "2025-01-02",
    "shift_6": "2025-01-03",  # spans midnight
    "shift_7": "2025-01-03",
}


def _machine_line_position(machine_id: str) -> int:
    """Lookup line_position from MACHINES by machine_id."""
    for m in MACHINES:
        if m.id == machine_id:
            return m.line_position
    return 0


def generate_machine_sensors() -> pd.DataFrame:
    """Generate machine_sensors table (66 rows) from SENSORS and PRIMARY_SENSOR_MAPPING."""
    rows: list[dict[str, object]] = []
    for sensor in SENSORS:
        mapping = PRIMARY_SENSOR_MAPPING.get(sensor.machine_id)
        primary_1 = mapping.primary_sensor_1 if mapping else None
        primary_2 = mapping.primary_sensor_2 if mapping else None
        rows.append({
            "machine_id": sensor.machine_id,
            "sensor_name": sensor.sensor_name,
            "sensor_type": sensor.sensor_type,
            "unit": sensor.unit,
            "nominal_value": sensor.nominal_value,
            "alarm_low": sensor.alarm_low,
            "alarm_high": sensor.alarm_high,
            "line_position": _machine_line_position(sensor.machine_id),
            "is_primary_1": sensor.sensor_name == primary_1 if primary_1 else False,
            "is_primary_2": sensor.sensor_name == primary_2 if primary_2 else False,
        })
    return pd.DataFrame(rows)


def generate_machine_bom() -> pd.DataFrame:
    """Generate machine_bom table (~34 rows) from MATERIALS."""
    rows = [
        {
            "machine_id": m.machine_id,
            "material_id": m.material_id,
            "material_name": m.material_name,
            "unit": m.unit,
            "consumption_rate_per_hour": m.consumption_rate_per_hour,
            "waste_factor_pct": m.waste_factor_pct,
        }
        for m in MATERIALS
    ]
    return pd.DataFrame(rows)


def generate_scenario_index() -> pd.DataFrame:
    """Generate scenario_index table (7 rows) from SCENARIOS."""
    rows: list[dict[str, object]] = []
    for s in SCENARIOS:
        start_date = _SHIFT_START_DATES.get(s.shift_id, "2025-01-01")
        end_date = _SHIFT_END_DATES.get(s.shift_id, "2025-01-01")
        shift_start_iso = f"{start_date}T{s.shift_start}:00"
        shift_end_iso = f"{end_date}T{s.shift_end}:00"
        rows.append({
            "shift_id": s.shift_id,
            "scenario_name": s.scenario_name,
            "scenario_type": s.scenario_type,
            "description": s.description,
            "key_machines": ",".join(s.key_machines) if s.key_machines else "",
            "shift_start": shift_start_iso,
            "shift_end": shift_end_iso,
            "default_moment": s.default_moment,
        })
    return pd.DataFrame(rows)


def generate_kpi_recommendations() -> pd.DataFrame:
    """Generate kpi_recommendations table (12 rows). Static coaching recommendations for Strategic Advisor."""
    rows = [
        {
            "recommendation_id": "R001",
            "category": "cost",
            "title": "Reduce changeover time",
            "description": "Implement SMED methodology for SKU changeovers. Standardize tooling and pre-stage materials to cut changeover from 45 to 25 minutes.",
            "priority": "high",
            "estimated_impact": "+3% OEE",
        },
        {
            "recommendation_id": "R002",
            "category": "maintenance",
            "title": "Predictive label roll monitoring",
            "description": "Add trend alerts when label_roll_remaining drops below 30% with declining rate. Prevents unexpected LABELLER stops.",
            "priority": "high",
            "estimated_impact": "Avoid 22-min stops",
        },
        {
            "recommendation_id": "R003",
            "category": "quality",
            "title": "Automated fill volume SPC",
            "description": "Implement statistical process control on filler_fill_volume_avg with automated alerts when Cpk degrades.",
            "priority": "medium",
            "estimated_impact": "Reduce quality holds",
        },
        {
            "recommendation_id": "R004",
            "category": "maintenance",
            "title": "Cap hopper level alerting",
            "description": "Set proactive alerts when capper_cap_hopper_level falls below 25% with consumption rate trending.",
            "priority": "high",
            "estimated_impact": "Prevent S5-type shortages",
        },
        {
            "recommendation_id": "R005",
            "category": "scheduling",
            "title": "Dynamic speed optimization",
            "description": "Adjust line speed based on downstream accumulation pressure and material availability to maximize throughput.",
            "priority": "medium",
            "estimated_impact": "+1-2% performance",
        },
        {
            "recommendation_id": "R006",
            "category": "energy",
            "title": "Pasteurizer zone optimization",
            "description": "Optimize pasteurizer zone temperatures and PU value to reduce steam consumption while maintaining quality.",
            "priority": "low",
            "estimated_impact": "€2K/month",
        },
        {
            "recommendation_id": "R007",
            "category": "waste",
            "title": "Carton reject root cause analysis",
            "description": "Track packer_reject_case_count by SKU and carton type. Identify patterns and address top causes.",
            "priority": "medium",
            "estimated_impact": "1% waste reduction",
        },
        {
            "recommendation_id": "R008",
            "category": "safety",
            "title": "Conveyor accumulation pressure monitoring",
            "description": "Alert when conv2/conv3/conv4_accumulation_pressure exceeds 70% to prevent backup and jams.",
            "priority": "high",
            "estimated_impact": "Prevent jams",
        },
        {
            "recommendation_id": "R009",
            "category": "maintenance",
            "title": "Glue temperature drift detection",
            "description": "Monitor label_glue_temp for gradual drift outside 135–155°C range. Early detection reduces label defects.",
            "priority": "medium",
            "estimated_impact": "Reduce label defects",
        },
        {
            "recommendation_id": "R010",
            "category": "scheduling",
            "title": "Cross-shift knowledge handoff",
            "description": "Document key events and corrective actions in shift handover. Use similar-case retrieval for new incidents.",
            "priority": "high",
            "estimated_impact": "Reduce repeat incidents",
        },
        {
            "recommendation_id": "R011",
            "category": "quality",
            "title": "Checkweigher trend monitoring",
            "description": "Track chkwt_weight_avg and chkwt_reject_count trends. Alert on drift before quality hold threshold.",
            "priority": "medium",
            "estimated_impact": "Earlier quality deviation detection",
        },
        {
            "recommendation_id": "R012",
            "category": "cost",
            "title": "Material consumption optimization",
            "description": "Compare consumption_rate_per_hour vs actual usage by SKU. Identify over-consumption and waste.",
            "priority": "low",
            "estimated_impact": "€3K/month",
        },
    ]
    return pd.DataFrame(rows)


def generate_scenario_moments() -> pd.DataFrame:
    """Generate scenario_moments table (~25 rows). Pre-computed bookmarks for timeline slider (design §2.3)."""
    rows = [
        # shift_1: default (10:00), mid-shift check (12:00)
        {"shift_id": "shift_1", "moment_id": "default", "label": "Mid-shift", "timestamp": "2025-01-01T10:00:00", "description": "Stable baseline operation", "highlight_machines": ""},
        {"shift_id": "shift_1", "moment_id": "mid_shift_check", "label": "Mid-shift check", "timestamp": "2025-01-01T12:00:00", "description": "Routine check point", "highlight_machines": ""},
        # shift_2: default (16:15 LABELLER stop), precursor (16:05), recovery (16:23)
        {"shift_id": "shift_2", "moment_id": "default", "label": "LABELLER stop", "timestamp": "2025-01-01T16:15:00", "description": "LABELLER stop event", "highlight_machines": "LABELLER"},
        {"shift_id": "shift_2", "moment_id": "precursor", "label": "10 min before", "timestamp": "2025-01-01T16:05:00", "description": "Precursor signals visible", "highlight_machines": "LABELLER"},
        {"shift_id": "shift_2", "moment_id": "recovery", "label": "Recovery", "timestamp": "2025-01-01T16:23:00", "description": "8 min after stop, recovery", "highlight_machines": "LABELLER"},
        # shift_3: default (02:00), adjustment (00:30)
        {"shift_id": "shift_3", "moment_id": "default", "label": "Mid-shift", "timestamp": "2025-01-02T02:00:00", "description": "Steady state operation", "highlight_machines": ""},
        {"shift_id": "shift_3", "moment_id": "adjustment", "label": "Adjustment", "timestamp": "2025-01-02T00:30:00", "description": "Minor adjustment point", "highlight_machines": ""},
        # shift_4: default (06:45), changeover_start (06:00), speed_ramp (07:00)
        {"shift_id": "shift_4", "moment_id": "default", "label": "Changeover end + fault", "timestamp": "2025-01-02T06:45:00", "description": "Changeover end and fault", "highlight_machines": "FILLER"},
        {"shift_id": "shift_4", "moment_id": "changeover_start", "label": "Changeover start", "timestamp": "2025-01-02T06:00:00", "description": "Changeover begins", "highlight_machines": "FILLER"},
        {"shift_id": "shift_4", "moment_id": "speed_ramp", "label": "Speed ramp", "timestamp": "2025-01-02T07:00:00", "description": "Line speed ramp-up", "highlight_machines": "FILLER"},
        # shift_5: default (19:00 CAPPER stop), near_miss (17:00), hopper_declining (18:30)
        {"shift_id": "shift_5", "moment_id": "default", "label": "CAPPER stop", "timestamp": "2025-01-02T19:00:00", "description": "CAPPER stop due to cap shortage", "highlight_machines": "CAPPER"},
        {"shift_id": "shift_5", "moment_id": "near_miss", "label": "2h before", "timestamp": "2025-01-02T17:00:00", "description": "Near-miss, hopper still adequate", "highlight_machines": "CAPPER"},
        {"shift_id": "shift_5", "moment_id": "hopper_declining", "label": "30m before", "timestamp": "2025-01-02T18:30:00", "description": "Hopper level declining", "highlight_machines": "CAPPER"},
        # shift_6: default (01:00 quality hold), nok_rising (00:40), corrective (01:25)
        {"shift_id": "shift_6", "moment_id": "default", "label": "Quality hold", "timestamp": "2025-01-03T01:00:00", "description": "Quality hold event", "highlight_machines": "FILLER,LABELLER"},
        {"shift_id": "shift_6", "moment_id": "nok_rising", "label": "20m before", "timestamp": "2025-01-03T00:40:00", "description": "NOK rate rising", "highlight_machines": "FILLER,LABELLER"},
        {"shift_id": "shift_6", "moment_id": "corrective", "label": "Corrective", "timestamp": "2025-01-03T01:25:00", "description": "Corrective action taken", "highlight_machines": "FILLER,LABELLER"},
        # shift_7: default (06:15 1st delay), second_delay (10:00), recovery (10:25)
        {"shift_id": "shift_7", "moment_id": "default", "label": "1st delay", "timestamp": "2025-01-03T06:15:00", "description": "First FILLER bowl starvation", "highlight_machines": "FILLER"},
        {"shift_id": "shift_7", "moment_id": "second_delay", "label": "Second delay", "timestamp": "2025-01-03T10:00:00", "description": "Second upstream delay", "highlight_machines": "FILLER"},
        {"shift_id": "shift_7", "moment_id": "recovery", "label": "Recovery", "timestamp": "2025-01-03T10:25:00", "description": "Recovery from delays", "highlight_machines": "FILLER"},
    ]
    return pd.DataFrame(rows)


def generate_line_constraints() -> pd.DataFrame:
    """Generate line_constraints table (~20 rows) from LINE_CONSTRAINTS."""
    rows = [
        {
            "constraint_id": c.constraint_id,
            "category": c.category,
            "parameter": c.parameter,
            "value": c.value,
            "unit": c.unit,
            "description": c.description,
            "applies_to": c.applies_to,
        }
        for c in LINE_CONSTRAINTS
    ]
    return pd.DataFrame(rows)


def generate_all_reference_data() -> dict[str, pd.DataFrame]:
    """Orchestrator: generate all 6 reference tables and return dict keyed by table name."""
    return {
        "machine_sensors": generate_machine_sensors(),
        "machine_bom": generate_machine_bom(),
        "scenario_index": generate_scenario_index(),
        "kpi_recommendations": generate_kpi_recommendations(),
        "scenario_moments": generate_scenario_moments(),
        "line_constraints": generate_line_constraints(),
    }


if __name__ == "__main__":
    data = generate_all_reference_data()
    for name, df in data.items():
        print(f"{name}: {len(df)} rows, columns={list(df.columns)}")
