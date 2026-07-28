"""Tests for incident definitions and apply/resolve logic."""

from __future__ import annotations

import pytest

from src.streaming_sim.cascade import (
    MachineSpec,
    build_line,
    cascade_tick,
)
from src.streaming_sim.incidents import (
    INCIDENTS,
    apply_incident,
    resolve_incident,
)


def _line_specs() -> list[MachineSpec]:
    """Simplified 5-machine line matching real machine IDs used in incidents."""
    return [
        MachineSpec("DEPALLETIZER", 1, 100.0),
        MachineSpec("FILLER", 2, 420.0),
        MachineSpec("LABELLER", 3, 420.0),
        MachineSpec("CASE_PACKER", 4, 100.0),
        MachineSpec("PALLETIZER", 5, 100.0),
    ]


def _fresh_line():
    specs = _line_specs()
    state = build_line(specs)
    return specs, state


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_all_specialists_have_incidents(self):
        specialists = {d.specialist for d in INCIDENTS.values()}
        expected = {
            "downtime_analyst", "oee_coach", "quality_specialist",
            "supply_chain", "maintenance", "safety", "production_scheduler",
        }
        assert specialists == expected

    def test_at_least_two_per_specialist(self):
        from collections import Counter
        counts = Counter(d.specialist for d in INCIDENTS.values())
        for specialist, count in counts.items():
            assert count >= 2, f"{specialist} has only {count} incident(s)"

    def test_all_scenario_ids_unique(self):
        assert len(INCIDENTS) == len(set(INCIDENTS.keys()))


# ---------------------------------------------------------------------------
# Apply — stop incidents
# ---------------------------------------------------------------------------

class TestApplyStop:
    @pytest.mark.parametrize("scenario_id", [
        "machine_breakdown", "supply_delay", "changeover_delay",
        "safety_incident", "zone_lockout", "minor_stops",
    ])
    def test_stop_incidents_set_stopped(self, scenario_id):
        specs, state = _fresh_line()
        defn = INCIDENTS[scenario_id]
        target = defn.default_targets[0]
        if target not in state.machines:
            pytest.skip(f"{target} not in test line")
        affected = apply_incident(state, scenario_id)
        assert len(affected) > 0
        for mid in affected:
            assert state.machines[mid].status == "stopped"
            assert state.machines[mid].throughput == 0.0


# ---------------------------------------------------------------------------
# Apply — degrade incidents
# ---------------------------------------------------------------------------

class TestApplyDegrade:
    @pytest.mark.parametrize("scenario_id", [
        "speed_loss", "quality_deviation", "contamination",
        "sensor_failure", "maintenance_overdue", "vibration_anomaly",
        "material_shortage",
    ])
    def test_degrade_incidents_set_degraded(self, scenario_id):
        specs, state = _fresh_line()
        defn = INCIDENTS[scenario_id]
        target = defn.default_targets[0]
        if target not in state.machines:
            pytest.skip(f"{target} not in test line")
        affected = apply_incident(state, scenario_id)
        assert len(affected) > 0
        for mid in affected:
            assert state.machines[mid].status == "degraded"
            assert state.machines[mid].speed_pct < 100.0


# ---------------------------------------------------------------------------
# Apply — severity scaling
# ---------------------------------------------------------------------------

class TestSeverity:
    def test_higher_severity_more_alarms(self):
        _, state_low = _fresh_line()
        apply_incident(state_low, "machine_breakdown", "FILLER", severity="low")
        alarms_low = state_low.machines["FILLER"].alarm_count

        _, state_crit = _fresh_line()
        apply_incident(state_crit, "machine_breakdown", "FILLER", severity="critical")
        alarms_crit = state_crit.machines["FILLER"].alarm_count

        assert alarms_crit > alarms_low

    def test_critical_severity_full_stop(self):
        _, state = _fresh_line()
        apply_incident(state, "quality_deviation", "FILLER", severity="critical")
        assert state.machines["FILLER"].speed_pct == 0.0


# ---------------------------------------------------------------------------
# Apply — rush order (special case)
# ---------------------------------------------------------------------------

class TestRushOrder:
    def test_rush_order_increases_speed(self):
        _, state = _fresh_line()
        apply_incident(state, "rush_order", "FILLER")
        assert state.machines["FILLER"].speed_pct > 100.0

    def test_rush_order_caps_at_110(self):
        _, state = _fresh_line()
        apply_incident(state, "rush_order", "FILLER")
        apply_incident(state, "rush_order", "FILLER")
        apply_incident(state, "rush_order", "FILLER")
        assert state.machines["FILLER"].speed_pct <= 110.0


# ---------------------------------------------------------------------------
# Resolve
# ---------------------------------------------------------------------------

class TestResolve:
    def test_resolve_clears_fault(self):
        specs, state = _fresh_line()
        apply_incident(state, "machine_breakdown", "FILLER")
        assert state.machines["FILLER"].status == "stopped"
        resolve_incident(state, ["FILLER"])
        assert state.machines["FILLER"].status == "running"
        assert state.machines["FILLER"].speed_pct == 100.0

    def test_resolve_then_cascade_recovers(self):
        specs, state = _fresh_line()
        apply_incident(state, "machine_breakdown", "FILLER")
        for _ in range(50):
            cascade_tick(specs, state, tick_seconds=1.0)
        resolve_incident(state, ["FILLER"])
        for _ in range(100):
            cascade_tick(specs, state, tick_seconds=1.0)
        for ms in state.machines.values():
            assert ms.status == "running"
            assert ms.throughput > 0


# ---------------------------------------------------------------------------
# Unknown scenario
# ---------------------------------------------------------------------------

class TestUnknown:
    def test_unknown_scenario_no_effect(self):
        _, state = _fresh_line()
        affected = apply_incident(state, "nonexistent_scenario")
        assert affected == []

    def test_unknown_target_no_effect(self):
        _, state = _fresh_line()
        affected = apply_incident(state, "machine_breakdown", "NONEXISTENT")
        assert affected == []
