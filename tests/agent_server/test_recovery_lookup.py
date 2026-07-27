"""Unit tests for the recover_machine command lookup helper.

The /sim/recover_machine endpoint resolves any active incident on a given
machine, even when the operator only knows the machine_id (not the
underlying command_id). The lookup logic is factored out so it can be
tested without booting the FastAPI app or any DBSQL client.
"""
from __future__ import annotations

from agent_server.sim.recovery import commands_targeting_machine


class TestSingleTargetMatching:
    def test_exact_match(self):
        cmds = [{"command_id": "c1", "target_machine_id": "FILLER"}]
        result = commands_targeting_machine(cmds, "FILLER")
        assert [c["command_id"] for c in result] == ["c1"]

    def test_no_match(self):
        cmds = [{"command_id": "c1", "target_machine_id": "FILLER"}]
        assert commands_targeting_machine(cmds, "CAPPER") == []

    def test_substring_does_not_match(self):
        # 'FILL' must not match 'FILLER' — token comparison, not substring.
        cmds = [{"command_id": "c1", "target_machine_id": "FILLER"}]
        assert commands_targeting_machine(cmds, "FILL") == []


class TestMultiTargetMatching:
    def test_comma_joined_targets(self):
        # Engine joins cascade-affected machines with ", ".
        cmds = [{"command_id": "c1", "target_machine_id": "FILLER, CAPPER, LABELLER"}]
        assert [c["command_id"] for c in commands_targeting_machine(cmds, "CAPPER")] == ["c1"]
        assert [c["command_id"] for c in commands_targeting_machine(cmds, "LABELLER")] == ["c1"]

    def test_no_separator_padding_required(self):
        cmds = [{"command_id": "c1", "target_machine_id": "FILLER,CAPPER"}]
        assert [c["command_id"] for c in commands_targeting_machine(cmds, "CAPPER")] == ["c1"]


class TestEdgeCases:
    def test_empty_machine_id(self):
        cmds = [{"command_id": "c1", "target_machine_id": "FILLER"}]
        assert commands_targeting_machine(cmds, "") == []

    def test_whitespace_machine_id(self):
        cmds = [{"command_id": "c1", "target_machine_id": "FILLER"}]
        assert commands_targeting_machine(cmds, "   ") == []

    def test_none_target(self):
        cmds = [{"command_id": "c1", "target_machine_id": None}]
        assert commands_targeting_machine(cmds, "FILLER") == []

    def test_empty_target(self):
        cmds = [{"command_id": "c1", "target_machine_id": ""}]
        assert commands_targeting_machine(cmds, "FILLER") == []

    def test_preserves_order(self):
        cmds = [
            {"command_id": "c1", "target_machine_id": "FILLER"},
            {"command_id": "c2", "target_machine_id": "CAPPER"},
            {"command_id": "c3", "target_machine_id": "FILLER, LABELLER"},
        ]
        result = [c["command_id"] for c in commands_targeting_machine(cmds, "FILLER")]
        assert result == ["c1", "c3"]
