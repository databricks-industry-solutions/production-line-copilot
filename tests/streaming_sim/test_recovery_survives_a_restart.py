"""A resolve request must close its event even if the producer restarted.

Observed in dev: CASE_PACKER showed ``machine_breakdown`` as active in the
incident feed with no Recover button, hours after the machine had gone back to
running. Its command row was terminal — ``status='resolved'``, ``resolved_at``
13 minutes after it started — but no ``event_type='resolved'`` row ever
appeared in ``event_log_recent``, so the dashboard, which pairs an event closed
by that marker, showed it open forever. Recover only offers itself for machines
with a live command, and this one had none left, so nothing in the UI could
clear it.

The producer holds active incidents in a dict that a restart empties, and
rehydration reloads only 'active' and 'resolving'. A command that reached
'resolve_requested' across a restart therefore matched nothing in memory and
fell straight through to the terminal UPDATE, skipping the marker.
"""
from __future__ import annotations

import logging

import pytest

from src.streaming_sim.cascade import build_line
from src.streaming_sim.incidents import (
    apply_incident,
    incident_target_machines,
    resolve_incident,
)
from src.streaming_sim.producer import (
    ActiveIncident,
    _machine_specs,
    incident_for_resolve,
)


@pytest.fixture
def state():
    """The real line, so "is this machine on the line" means what it means in
    production rather than what a hand-written fixture happens to list."""
    return build_line(_machine_specs())


def _command(**over):
    cmd = {
        "command_id": "cmd-1",
        "scenario_id": "machine_breakdown",
        "target_machine_id": "CASE_PACKER",
        "severity": "medium",
    }
    cmd.update(over)
    return cmd


class TestTheNormalPath:
    def test_an_incident_still_in_memory_is_used(self, state):
        held = ActiveIncident("cmd-1", "machine_breakdown", ["CASE_PACKER"], "medium")
        active = {"cmd-1": held}
        assert incident_for_resolve(_command(), active, state) is held

    def test_it_is_taken_out_of_the_book(self, state):
        active = {"cmd-1": ActiveIncident("cmd-1", "machine_breakdown", ["CASE_PACKER"], "medium")}
        incident_for_resolve(_command(), active, state)
        assert active == {}, "a resolved incident must not stay resolvable"

    def test_a_second_resolve_of_the_same_command_does_not_replay_the_first(self, state):
        active = {"cmd-1": ActiveIncident("cmd-1", "machine_breakdown", ["CASE_PACKER"], "medium")}
        first = incident_for_resolve(_command(), active, state)
        second = incident_for_resolve(_command(), active, state)
        # Rebuilt rather than the same object, but it must still name the
        # machine so a duplicated request cannot leave the event open.
        assert second is not first
        assert second.target_machines == ["CASE_PACKER"]


class TestAfterARestart:
    """``active_incidents`` is empty — the case that produced the stuck event."""

    def test_the_incident_is_rebuilt_from_the_command_row(self, state):
        rebuilt = incident_for_resolve(_command(), {}, state)
        assert rebuilt is not None
        assert rebuilt.command_id == "cmd-1"
        assert rebuilt.scenario_id == "machine_breakdown"
        assert rebuilt.target_machines == ["CASE_PACKER"]

    def test_the_severity_comes_from_the_row(self, state):
        assert incident_for_resolve(_command(severity="high"), {}, state).severity == "high"

    def test_a_missing_severity_does_not_lose_the_incident(self, state):
        assert incident_for_resolve(_command(severity=None), {}, state).severity == "medium"

    def test_a_cascade_across_machines_is_recovered_whole(self, state):
        cmd = _command(target_machine_id="CASE_PACKER,CASE_SEALER")
        assert incident_for_resolve(cmd, {}, state).target_machines == [
            "CASE_PACKER", "CASE_SEALER",
        ]

    def test_a_line_wide_scenario_falls_back_to_its_default_targets(self, state):
        cmd = _command(scenario_id="machine_breakdown", target_machine_id=None)
        rebuilt = incident_for_resolve(cmd, {}, state)
        assert rebuilt is not None and rebuilt.target_machines

    def test_the_rebuilt_incident_actually_clears_the_fault(self, state):
        apply_incident(state, "machine_breakdown", "CASE_PACKER", "medium")
        assert state.machines["CASE_PACKER"].status != "running"
        rebuilt = incident_for_resolve(_command(), {}, state)
        resolve_incident(state, rebuilt.target_machines)
        assert state.machines["CASE_PACKER"].status == "running"

    def test_it_says_why_in_the_log(self, state, caplog):
        with caplog.at_level(logging.INFO, logger="src.streaming_sim.producer"):
            incident_for_resolve(_command(), {}, state)
        assert "restarted" in caplog.text


class TestWhenTheRowNamesNothingWeCanActfOn:
    def test_an_unknown_scenario_yields_no_incident(self, state):
        assert incident_for_resolve(_command(scenario_id="not_a_scenario"), {}, state) is None

    def test_a_machine_from_another_line_yields_no_incident(self, state):
        assert incident_for_resolve(_command(target_machine_id="NOT_A_MACHINE"), {}, state) is None

    def test_that_case_is_logged_loudly(self, state, caplog):
        with caplog.at_level(logging.WARNING, logger="src.streaming_sim.producer"):
            incident_for_resolve(_command(target_machine_id="NOT_A_MACHINE"), {}, state)
        assert "leaves the original event open" in caplog.text

    def test_a_partly_unknown_cascade_keeps_the_machines_we_do_have(self, state):
        cmd = _command(target_machine_id="CASE_PACKER,NOT_A_MACHINE")
        assert incident_for_resolve(cmd, {}, state).target_machines == ["CASE_PACKER"]


class TestTargetsAreDerivedWithoutTouchingState:
    """``apply_incident`` worked the target list out only while mutating state,
    so recovery could not reuse it without re-applying the fault it is trying
    to clear."""

    def test_a_named_machine_is_the_target(self):
        assert incident_target_machines("machine_breakdown", "FILLER") == ["FILLER"]

    def test_a_comma_joined_cascade_is_split(self):
        assert incident_target_machines("machine_breakdown", "FILLER, CAPPER") == [
            "FILLER", "CAPPER",
        ]

    def test_an_unnamed_target_uses_the_scenario_default(self):
        assert incident_target_machines("machine_breakdown") == incident_target_machines(
            "machine_breakdown", None,
        )

    def test_an_unknown_scenario_names_nothing(self):
        assert incident_target_machines("not_a_scenario", "FILLER") == []

    def test_it_agrees_with_what_apply_incident_affects(self, state):
        affected = apply_incident(state, "machine_breakdown", "FILLER", "medium")
        assert incident_target_machines("machine_breakdown", "FILLER") == affected

    def test_it_leaves_the_line_alone(self, state):
        before = {m: state.machines[m].status for m in state.machines}
        incident_target_machines("machine_breakdown", "FILLER")
        assert {m: state.machines[m].status for m in state.machines} == before


class TestATriggerThatAppliesToNothingIsNotSilent:
    """The other half of the same silence: when ``apply_incident`` matches no
    machine the row goes terminal anyway, which is how "I clicked Trigger and
    nothing happened" became unattributable."""

    def test_the_producer_logs_which_input_was_wrong(self):
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[2] / "src" / "streaming_sim" / "producer.py"
        ).read_text()
        assert "applied to nothing" in source
