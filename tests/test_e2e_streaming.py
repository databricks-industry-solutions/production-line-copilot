"""E2E integration test for the streaming pipeline.

Tests the full loop locally (no Databricks required):
  1. Build a line -> run healthy ticks -> verify output
  2. Trigger an incident -> verify cascade effects
  3. Resolve the incident -> verify recovery
  4. Verify agent routing picks the right specialist

Agent routing tests are skipped if mlflow is not installed locally.
"""

from datetime import datetime, timezone

import pytest

from src.streaming_sim.cascade import (
    MachineSpec,
    apply_fault,
    build_line,
    cascade_tick,
    clear_fault,
    compute_line_kpi,
    snapshot_to_dicts,
)
from src.streaming_sim.incidents import (
    INCIDENTS,
    apply_incident,
    resolve_incident,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _five_machine_specs():
    return [
        MachineSpec(id="DEPAL", line_position=1, nominal_capacity=450.0),
        MachineSpec(id="WASHER", line_position=2, nominal_capacity=420.0),
        MachineSpec(id="FILLER", line_position=3, nominal_capacity=400.0),
        MachineSpec(id="LABELER", line_position=4, nominal_capacity=430.0),
        MachineSpec(id="PACKER", line_position=5, nominal_capacity=380.0),
    ]


def _five_machine_line():
    specs = _five_machine_specs()
    return specs, build_line(specs, buffer_seconds=10.0)


def _tick(specs, state, n=1, dt=1.0):
    """Run n ticks, returning state for convenience."""
    for _ in range(n):
        cascade_tick(specs, state, tick_seconds=dt)
    return state


# ── Step 1: Healthy line ──────────────────────────────────────────────────


class TestE2EHealthyLine:
    def test_healthy_line_all_running(self):
        specs, state = _five_machine_line()
        _tick(specs, state, 5)

        rows = snapshot_to_dicts(state, shift_id="e2e_shift", timestamp=_now())
        assert len(rows) == 5
        assert {r["status"] for r in rows} == {"running"}

    def test_healthy_kpi(self):
        specs, state = _five_machine_line()
        _tick(specs, state, 3)
        kpi = compute_line_kpi(state, shift_id="e2e_shift", timestamp=_now())
        assert kpi["machines_stopped"] == 0
        assert kpi["machines_running"] == 5
        assert kpi["total_throughput"] > 0

    def test_snapshot_column_names(self):
        specs, state = _five_machine_line()
        _tick(specs, state, 1)
        rows = snapshot_to_dicts(state, shift_id="s1", timestamp=_now())
        expected = {"shift_id", "machine_id", "ts", "status",
                    "throughput_per_min", "speed_pct", "alarm_count"}
        assert set(rows[0].keys()) == expected

    def test_kpi_column_names(self):
        specs, state = _five_machine_line()
        _tick(specs, state, 1)
        kpi = compute_line_kpi(state, shift_id="s1", timestamp=_now())
        assert "ts" in kpi
        assert "shift_id" in kpi


# ── Step 2: Incident cascade ─────────────────────────────────────────────


class TestE2EIncidentCascade:
    @pytest.mark.parametrize("scenario_id", [
        "machine_breakdown",
        "speed_loss",
        "quality_deviation",
        "vibration_anomaly",
        "changeover_delay",
    ])
    def test_incident_causes_fault(self, scenario_id):
        specs, state = _five_machine_line()
        _tick(specs, state, 3)

        apply_incident(state, scenario_id, "FILLER", severity="high")
        _tick(specs, state, 5)

        ms = state.machines["FILLER"]
        assert ms.status in ("stopped", "degraded")

    def test_cascade_downstream_starves(self):
        specs, state = _five_machine_line()
        _tick(specs, state, 5)

        apply_fault(state, "FILLER", "stopped", extra_alarms=3)
        _tick(specs, state, 100)

        downstream_starved = [
            mid for mid in ("LABELER", "PACKER")
            if state.machines[mid].status in ("starved", "blocked")
        ]
        assert len(downstream_starved) > 0, "Downstream should starve"

    def test_kpi_reflects_incident(self):
        specs, state = _five_machine_line()
        _tick(specs, state, 3)
        kpi_before = compute_line_kpi(state, shift_id="s", timestamp=_now())

        apply_fault(state, "FILLER", "stopped", extra_alarms=2)
        _tick(specs, state, 10)
        kpi_after = compute_line_kpi(state, shift_id="s", timestamp=_now())

        assert kpi_after["machines_stopped"] + kpi_after["machines_starved"] > 0
        assert kpi_after["total_throughput"] < kpi_before["total_throughput"]


# ── Step 3: Resolve and recovery ──────────────────────────────────────────


class TestE2EResolveRecovery:
    def test_resolve_then_recover(self):
        specs, state = _five_machine_line()
        _tick(specs, state, 3)

        apply_incident(state, "machine_breakdown", "FILLER", severity="high")
        _tick(specs, state, 10)
        assert state.machines["FILLER"].status in ("stopped", "degraded")

        resolve_incident(state, ["FILLER"])
        _tick(specs, state, 100)

        assert state.machines["FILLER"].status == "running"
        assert state.machines["FILLER"].speed_pct == 100.0

    def test_kpi_recovers_after_resolve(self):
        specs, state = _five_machine_line()
        _tick(specs, state, 5)
        kpi_healthy = compute_line_kpi(state, shift_id="s", timestamp=_now())

        apply_fault(state, "WASHER", "stopped", extra_alarms=3)
        _tick(specs, state, 20)

        clear_fault(state, "WASHER")
        _tick(specs, state, 100)

        kpi_recovered = compute_line_kpi(state, shift_id="s", timestamp=_now())
        assert kpi_recovered["machines_stopped"] == 0
        assert kpi_recovered["total_throughput"] >= kpi_healthy["total_throughput"] * 0.9


# ── Step 4: Agent routing ─────────────────────────────────────────────────


try:
    import mlflow  # noqa: F401
    _has_mlflow = True
except ImportError:
    _has_mlflow = False


@pytest.mark.skipif(not _has_mlflow, reason="mlflow not installed locally")
class TestE2EAgentRouting:
    def test_routing_map_covers_all_incidents(self):
        from agent_server.agent import INCIDENT_TO_SPECIALIST
        for scenario_id in INCIDENTS:
            assert scenario_id in INCIDENT_TO_SPECIALIST, (
                f"Incident '{scenario_id}' missing from routing map"
            )

    def test_route_by_active_incident(self):
        from agent_server.agent import INCIDENT_TO_SPECIALIST, _route_by_active_incident
        for scenario_id, expected in INCIDENT_TO_SPECIALIST.items():
            ctx = {"active_incidents": [{"scenario_id": scenario_id}]}
            assert _route_by_active_incident(ctx) == expected

    def test_no_active_incident_returns_none(self):
        from agent_server.agent import _route_by_active_incident
        assert _route_by_active_incident({"active_incidents": []}) is None
        assert _route_by_active_incident({}) is None


# ── Step 5: Full lifecycle ────────────────────────────────────────────────


class TestE2EFullLoop:
    def test_complete_lifecycle(self):
        specs, state = _five_machine_line()
        ids = list(state.machines.keys())

        # Phase 1: Healthy
        _tick(specs, state, 5)
        for mid in ids:
            assert state.machines[mid].status == "running"

        # Phase 2: Trigger critical breakdown on FILLER
        apply_incident(state, "machine_breakdown", "FILLER", severity="critical")

        # Phase 3: Cascade
        _tick(specs, state, 50)
        affected = [mid for mid in ids if state.machines[mid].status != "running"]
        assert "FILLER" in affected
        assert len(affected) > 1, "Cascade should affect more than the target"
        kpi_incident = compute_line_kpi(state, shift_id="s", timestamp=_now())

        # Phase 4: Resolve
        resolve_incident(state, ["FILLER"])

        # Phase 5: Recovery
        _tick(specs, state, 100)
        still_affected = [mid for mid in ids if state.machines[mid].status != "running"]
        assert len(still_affected) == 0, f"Still affected: {still_affected}"

        kpi_recovered = compute_line_kpi(state, shift_id="s", timestamp=_now())
        assert kpi_recovered["total_throughput"] > kpi_incident["total_throughput"]
