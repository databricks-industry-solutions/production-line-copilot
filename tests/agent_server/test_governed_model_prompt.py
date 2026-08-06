"""Tests for the governed-industry-model awareness note (ID4 grounding).

The note lives once in ``agent_server.prompts._shared.GOVERNED_MODEL_NOTE`` and
is concatenated into the specialists whose domain has a governed MVM
counterpart. These tests pin:

  * the note exists and names the governed schemas/tables,
  * the note preserves the honesty caveats (curated subset, incident facts
    empty, prefer live tables for real-time),
  * exactly the intended specialists carry it (and the ones that shouldn't,
    don't — so we don't silently push every specialist at the governed model).
"""
from __future__ import annotations

from agent_server.prompts import _shared


def test_note_names_governed_schemas_and_tables():
    note = _shared.GOVERNED_MODEL_NOTE
    for token in ("asset.", "production.", "quality.", "inventory."):
        assert token in note, token
    for tbl in ("equipment_register", "pm_schedule", "failure_record",
                "production_line", "inspection_result", "ncr", "stock_balance"):
        assert tbl in note, tbl


def test_note_keeps_honesty_caveats():
    note = _shared.GOVERNED_MODEL_NOTE.lower()
    # Only a subset of columns is populated.
    assert "null" in note or "subset" in note
    # Incident facts are empty at steady state.
    assert "empty" in note
    # Real-time still prefers the live streaming tables.
    assert "real-time" in note and "live" in note
    # Never fabricate.
    assert "fabricate" in note


def test_expected_specialists_carry_the_note():
    from agent_server.prompts.downtime_analyst import DOWNTIME_ANALYST_SYSTEM
    from agent_server.prompts.maintenance_predictor import MAINTENANCE_PREDICTOR_SYSTEM
    from agent_server.prompts.oee_coach import OEE_COACH_SYSTEM
    from agent_server.prompts.quality_specialist import QUALITY_SPECIALIST_SYSTEM
    from agent_server.prompts.supply_chain_advisor import SUPPLY_CHAIN_SYSTEM

    for name, prompt in (
        ("downtime_analyst", DOWNTIME_ANALYST_SYSTEM),
        ("quality_specialist", QUALITY_SPECIALIST_SYSTEM),
        ("maintenance_predictor", MAINTENANCE_PREDICTOR_SYSTEM),
        ("supply_chain_advisor", SUPPLY_CHAIN_SYSTEM),
        ("oee_coach", OEE_COACH_SYSTEM),
    ):
        assert "GOVERNED INDUSTRY MODEL" in prompt, f"{name} missing governed note"


def test_schedule_optimizer_does_not_carry_the_note():
    """schedule_optimizer is plan/constraint-driven, not a UC-data specialist —
    it should NOT be pushed at the governed model."""
    from agent_server.prompts.schedule_optimizer import SCHEDULE_OPTIMIZER_SYSTEM
    assert "GOVERNED INDUSTRY MODEL" not in SCHEDULE_OPTIMIZER_SYSTEM
