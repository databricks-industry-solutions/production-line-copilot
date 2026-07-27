"""Incident definitions — maps scenario_id to concrete effects on machines.

Each incident type is a pure function: given current LineState and incident
params, it applies faults via the cascade engine's apply_fault().  The cascade
engine then propagates the effects across the line.

Incident lifecycle:
  PENDING → ACTIVE (apply_incident) → RESOLVE_REQUESTED → RESOLVING (resolve_incident) → RESOLVED
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.streaming_sim.cascade import LineState, apply_fault, clear_fault


@dataclass(frozen=True)
class IncidentDef:
    """Static definition of an incident type."""
    scenario_id: str
    name: str
    specialist: str
    description: str
    default_targets: tuple[str, ...]
    default_severity: str = "medium"


# ── Registry ─────────────────────────────────────────────────────────────────

INCIDENTS: dict[str, IncidentDef] = {}


def _register(d: IncidentDef) -> IncidentDef:
    INCIDENTS[d.scenario_id] = d
    return d


# ── Downtime Analyst ─────────────────────────────────────────────────────────

_register(IncidentDef(
    scenario_id="machine_breakdown",
    name="Machine Breakdown",
    specialist="downtime_analyst",
    description="Complete machine failure — stops immediately, high alarm count",
    default_targets=("FILLER",),
))

_register(IncidentDef(
    scenario_id="sensor_failure",
    name="Sensor Failure",
    specialist="downtime_analyst",
    description="Critical sensor offline — machine degrades then stops",
    default_targets=("LABELLER",),
))

# ── OEE Coach ────────────────────────────────────────────────────────────────

_register(IncidentDef(
    scenario_id="speed_loss",
    name="Speed Loss",
    specialist="oee_coach",
    description="Gradual speed degradation on bottleneck machine",
    default_targets=("FILLER",),
    default_severity="low",
))

_register(IncidentDef(
    scenario_id="minor_stops",
    name="Minor Stops",
    specialist="oee_coach",
    description="Intermittent short stops — machine cycles between running and stopped",
    default_targets=("CASE_PACKER",),
    default_severity="low",
))

# ── Quality Specialist ───────────────────────────────────────────────────────

_register(IncidentDef(
    scenario_id="quality_deviation",
    name="Quality Deviation",
    specialist="quality_specialist",
    description="Fill volume drifting out of spec — machine degrades, QC failures rise",
    default_targets=("FILLER",),
))

_register(IncidentDef(
    scenario_id="contamination",
    name="Contamination Alert",
    specialist="quality_specialist",
    description="Foreign material detected — inspector rejects spike, line slows",
    default_targets=("EMPTY_INSPECTOR",),
    default_severity="high",
))

# ── Supply Chain ──────────────────────────────────────────────────────────────

_register(IncidentDef(
    scenario_id="material_shortage",
    name="Material Shortage",
    specialist="supply_chain",
    description="Material level critically low — machine slows then stops",
    default_targets=("CAPPER",),
))

_register(IncidentDef(
    scenario_id="supply_delay",
    name="Supply Delay",
    specialist="supply_chain",
    description="Upstream supply interrupted — depalletizer starves the line",
    default_targets=("DEPALLETIZER",),
))

# ── Maintenance ───────────────────────────────────────────────────────────────

_register(IncidentDef(
    scenario_id="maintenance_overdue",
    name="Maintenance Overdue",
    specialist="maintenance",
    description="Preventive maintenance window missed — vibration rising, degraded",
    default_targets=("PASTEURIZER",),
    default_severity="low",
))

_register(IncidentDef(
    scenario_id="vibration_anomaly",
    name="Vibration Anomaly",
    specialist="maintenance",
    description="Bearing vibration spike — machine degrades, risk of breakdown",
    default_targets=("FILLER",),
))

# ── Safety ────────────────────────────────────────────────────────────────────

_register(IncidentDef(
    scenario_id="safety_incident",
    name="Safety Incident",
    specialist="safety",
    description="Safety interlock triggered — machine and neighbors stop immediately",
    default_targets=("PALLETIZER",),
    default_severity="critical",
))

_register(IncidentDef(
    scenario_id="zone_lockout",
    name="Zone Lockout",
    specialist="safety",
    description="Maintenance lockout in a zone — affected machines stop",
    default_targets=("SHRINK_WRAPPER",),
    default_severity="high",
))

# ── Production Scheduler ─────────────────────────────────────────────────────

_register(IncidentDef(
    scenario_id="changeover_delay",
    name="Changeover Delay",
    specialist="production_scheduler",
    description="SKU changeover taking longer than planned — line stopped",
    default_targets=("FILLER", "LABELLER"),
))

_register(IncidentDef(
    scenario_id="rush_order",
    name="Rush Order",
    specialist="production_scheduler",
    description="Priority order inserted — speed increase, risk of quality issues",
    default_targets=("FILLER",),
    default_severity="low",
))


# ── Severity → effect scaling ────────────────────────────────────────────────

_SEVERITY_SCALE: dict[str, dict[str, Any]] = {
    "low":      {"speed_reduction": 10.0, "extra_alarms": 1},
    "medium":   {"speed_reduction": 30.0, "extra_alarms": 3},
    "high":     {"speed_reduction": 60.0, "extra_alarms": 5},
    "critical": {"speed_reduction": 100.0, "extra_alarms": 8},
}


def _scale(severity: str) -> dict[str, Any]:
    return _SEVERITY_SCALE.get(severity, _SEVERITY_SCALE["medium"])


# ── Apply / resolve ──────────────────────────────────────────────────────────

def apply_incident(
    state: LineState,
    scenario_id: str,
    target_machine_id: str | None = None,
    severity: str = "medium",
    reapply: bool = False,
) -> list[str]:
    """Apply an incident to the line state.  Returns list of affected machine IDs.

    When *reapply* is True (sticky re-application each tick), alarm_count is set
    to the expected value rather than accumulated, preventing balloon growth.
    """
    defn = INCIDENTS.get(scenario_id)
    if defn is None:
        return []

    targets = [target_machine_id] if target_machine_id else list(defn.default_targets)
    scale = _scale(severity)
    alarms = scale["extra_alarms"]
    affected: list[str] = []

    for mid in targets:
        if mid not in state.machines:
            continue

        if scenario_id in ("machine_breakdown", "supply_delay", "changeover_delay"):
            apply_fault(state, mid, "stopped", speed_pct=0.0,
                        extra_alarms=0 if reapply else alarms)
            if reapply:
                state.machines[mid].alarm_count = alarms

        elif scenario_id == "sensor_failure":
            apply_fault(state, mid, "degraded",
                        speed_pct=100.0 - scale["speed_reduction"],
                        extra_alarms=0 if reapply else alarms)
            if reapply:
                state.machines[mid].alarm_count = alarms

        elif scenario_id in ("speed_loss", "maintenance_overdue", "vibration_anomaly"):
            apply_fault(state, mid, "degraded",
                        speed_pct=100.0 - scale["speed_reduction"],
                        extra_alarms=0 if reapply else alarms)
            if reapply:
                state.machines[mid].alarm_count = alarms

        elif scenario_id == "minor_stops":
            apply_fault(state, mid, "stopped", speed_pct=0.0,
                        extra_alarms=0 if reapply else alarms)
            if reapply:
                state.machines[mid].alarm_count = alarms

        elif scenario_id in ("quality_deviation", "contamination"):
            apply_fault(state, mid, "degraded",
                        speed_pct=100.0 - scale["speed_reduction"],
                        extra_alarms=0 if reapply else alarms)
            if reapply:
                state.machines[mid].alarm_count = alarms

        elif scenario_id == "material_shortage":
            apply_fault(state, mid, "degraded",
                        speed_pct=max(0.0, 100.0 - scale["speed_reduction"] * 1.5),
                        extra_alarms=0 if reapply else alarms)
            if reapply:
                state.machines[mid].alarm_count = alarms

        elif scenario_id in ("safety_incident", "zone_lockout"):
            apply_fault(state, mid, "stopped", speed_pct=0.0,
                        extra_alarms=0 if reapply else alarms)
            if reapply:
                state.machines[mid].alarm_count = alarms

        elif scenario_id == "rush_order":
            # Use "degraded" status so cascade_tick preserves the speed_pct
            # (cascade resets "running" machines to 100% every tick).
            apply_fault(state, mid, "degraded", speed_pct=min(110.0, 100.0 + 10.0),
                        extra_alarms=0 if reapply else 1)
            if reapply:
                ms = state.machines[mid]
                ms.alarm_count = max(ms.alarm_count, 1)

        affected.append(mid)

    return affected


def resolve_incident(
    state: LineState,
    target_machine_ids: list[str],
) -> None:
    """Clear faults on the given machines — cascade engine will handle recovery."""
    for mid in target_machine_ids:
        clear_fault(state, mid)
