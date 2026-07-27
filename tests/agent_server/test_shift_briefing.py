"""Unit tests for the Shift Briefing specialist wiring.

These tests verify the static wiring (registration, model map, context trimming,
HITL actions, classifier valid-set, prompt routing rules) without touching the
LLM or network. They give us fast feedback that the new specialist is plugged
in correctly before we deploy to line_manager_dev.

Two tiers:
  * source-text checks (always run — no third-party deps)
  * importable-module checks (skipped when langchain stack isn't installed
    locally; CI/deploy environments have it)
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO = Path(__file__).resolve().parents[2]


# ── Source-text wiring (no heavy imports) ────────────────────────────


def _read(relative: str) -> str:
    return (_REPO / relative).read_text(encoding="utf-8")


def test_prompt_module_exports_constant():
    """Prompt is pure strings — safe to import without langchain."""
    from agent_server.prompts.shift_briefing import SHIFT_BRIEFING_SYSTEM

    assert isinstance(SHIFT_BRIEFING_SYSTEM, str)
    for heading in ("## Situation", "## Key Risks", "## Priority Actions",
                    "## Outlook", "## Confidence"):
        assert heading in SHIFT_BRIEFING_SYSTEM, f"missing required heading: {heading}"
    lowered = SHIFT_BRIEFING_SYSTEM.lower()
    assert "pre-shift" in lowered
    assert "post-shift" in lowered


def test_orchestrator_prompt_lists_shift_briefing():
    """The classifier LLM prompt must advertise shift_briefing as a routable target."""
    from agent_server.prompts.orchestrator import ORCHESTRATOR_SYSTEM

    assert "shift_briefing" in ORCHESTRATOR_SYSTEM
    lowered = ORCHESTRATOR_SYSTEM.lower()
    for trigger in ("pre-shift", "post-shift", "handover", "handoff", "carryover"):
        assert trigger in lowered, f"missing trigger word in orchestrator prompt: {trigger}"


def test_specialist_module_uses_lean_toolbox():
    """Source-level check that the specialist file pulls the agreed five tools."""
    src = _read("agent_server/specialists/shift_briefing.py")
    for tool_import in (
        "from agent_server.tools.sql_query import sql_query",
        "from agent_server.tools.genie_query import genie_query",
        "from agent_server.tools.calculator import calculator",
        "from agent_server.tools.plan_constraints_loader import load_plan_and_constraints",
        "from agent_server.tools.similar_case_retriever import retrieve_similar_cases",
    ):
        assert tool_import in src, f"missing tool import: {tool_import}"
    # Briefings are summarisation work — must NOT import heavy optimisers.
    # We grep for the `import` line specifically so a docstring that names a
    # forbidden tool to explain *why* it's excluded doesn't false-positive.
    for forbidden in (
        "schedule_recovery", "production_forecast", "oee_loss_analysis",
        "multi_shift_plan", "stochastic_schedule_recovery", "quality_risk_assessment",
        "detect_anomalies",
    ):
        assert f"import {forbidden}" not in src, (
            f"shift_briefing must not import {forbidden} — briefings summarise, "
            "they don't optimise/diagnose"
        )
    # Mini endpoint — not the heavyweight model. Allow either the literal default
    # or env-var indirection (MODEL_SPECIALIST), but reject any direct reference
    # to the full gpt-5 / advanced endpoint.
    assert '"databricks-gpt-5-mini"' in src or 'MODEL_SPECIALIST' in src
    assert 'MODEL_ADVANCED' not in src
    assert '"databricks-gpt-5"' not in src


def test_agent_module_registers_shift_briefing():
    """Source-level check that agent.py wires shift_briefing everywhere it must.

    We grep the source rather than importing because importing agent.py pulls
    the full langchain stack — fine in CI, not always available locally.
    """
    src = _read("agent_server/agent.py")

    assert "from agent_server.specialists.shift_briefing import create_shift_briefing" in src
    assert '"shift_briefing": create_shift_briefing()' in src
    # Mini model for briefings (summarisation).
    assert '"shift_briefing": os.environ.get("MODEL_SPECIALIST"' in src
    # Classifier must accept the new specialist name.
    assert '"shift_briefing"' in src
    # Must extend _trim_context to give briefings the handover fields.
    assert 'specialist_name == "shift_briefing"' in src
    # HITL actions section.
    assert '"shift_briefing": [' in src


# ── Importable-module checks (require langchain) ─────────────────────


def _require_langchain():
    """Skip the test cleanly if the LangChain stack isn't installed locally."""
    pytest.importorskip("langchain_core")
    pytest.importorskip("langgraph")


def test_specialist_factory_creates_react_agent():
    """create_shift_briefing must wire the prompt + the lean tool list onto a ReAct agent."""
    _require_langchain()

    import agent_server.specialists.shift_briefing as briefing_mod
    from agent_server.prompts.shift_briefing import SHIFT_BRIEFING_SYSTEM

    with patch.object(briefing_mod, "ChatDatabricks") as mock_llm, \
         patch.object(briefing_mod, "create_agent") as mock_factory:
        mock_factory.return_value = MagicMock(name="agent")
        agent = briefing_mod.create_shift_briefing()

    assert agent is mock_factory.return_value
    mock_llm.assert_called_once()
    _, llm_kwargs = mock_llm.call_args
    assert llm_kwargs["endpoint"] == "databricks-gpt-5-mini"

    _, factory_kwargs = mock_factory.call_args
    tools = factory_kwargs["tools"]
    tool_names = {getattr(t, "name", None) or getattr(t, "__name__", None) for t in tools}
    assert tool_names == {
        "sql_query", "genie_query", "calculator",
        "load_plan_and_constraints", "retrieve_similar_cases",
    }
    prompt_msg = factory_kwargs["system_prompt"]
    assert prompt_msg.content == SHIFT_BRIEFING_SYSTEM


def test_specialist_registered_in_get_specialists():
    """_get_specialists must include shift_briefing alongside the existing ones."""
    _require_langchain()

    import agent_server.agent as agent_mod

    fake_specialists = {
        "downtime_analyst": MagicMock(),
        "quality_specialist": MagicMock(),
        "supply_chain_advisor": MagicMock(),
        "oee_coach": MagicMock(),
        "schedule_optimizer": MagicMock(),
        "maintenance_predictor": MagicMock(),
        "strategic_advisor": MagicMock(),
        "shift_briefing": MagicMock(),
    }
    name_map = {
        "create_downtime_analyst": "downtime_analyst",
        "create_quality_specialist": "quality_specialist",
        "create_supply_chain_advisor": "supply_chain_advisor",
        "create_oee_coach": "oee_coach",
        "create_schedule_optimizer": "schedule_optimizer",
        "create_maintenance_predictor": "maintenance_predictor",
        "create_strategic_advisor": "strategic_advisor",
        "create_shift_briefing": "shift_briefing",
    }
    patches = [patch.object(agent_mod, fn_name, return_value=fake_specialists[key])
               for fn_name, key in name_map.items()]
    for p in patches:
        p.start()
    try:
        agent_mod._specialists = None
        specialists = agent_mod._get_specialists()
    finally:
        for p in patches:
            p.stop()
        agent_mod._specialists = None

    assert "shift_briefing" in specialists
    assert specialists["shift_briefing"] is fake_specialists["shift_briefing"]


def test_model_map_assigns_mini_model():
    """Briefings are summarisation work — the mini endpoint is the right default."""
    _require_langchain()
    from agent_server.agent import MODEL_MAP

    assert "shift_briefing" in MODEL_MAP
    assert "mini" in MODEL_MAP["shift_briefing"]


def test_trim_context_for_shift_briefing_includes_handover_fields():
    """Shift briefing needs crew, maintenance, CIP, inventory, recommendations."""
    _require_langchain()
    from agent_server.agent import _trim_context

    full_context = {
        "scenario": {"shift_id": "shift_1", "scenario_name": "Normal"},
        "current_kpi": {"oee_to_now": 67.2},
        "production_plan": {"planned_cases": 8400},
        "active_events": [{"machine_id": "FILLER", "event_type": "minor_stop"} for _ in range(15)],
        "line_state": [{"machine_id": f"M{i}", "status": "running"} for i in range(20)],
        "inventory_snapshot": [{"item_id": f"item_{i}", "current_qty": 100} for i in range(30)],
        "crew": [{"name": f"Op {i}", "role": "operator"} for i in range(15)],
        "maintenance": [{"machine_id": f"M{i}", "scheduled_for": "today"} for i in range(15)],
        "cip_schedule": [{"unit_id": f"CIP{i}"} for i in range(15)],
        "constraints": [{"id": f"c{i}"} for i in range(10)],
        "recommendations": [{"rec_id": f"rec_{i}"} for i in range(10)],
        "quality_checks": [{"check_id": f"qc_{i}"} for i in range(10)],
        "active_incidents": [{"scenario_id": "x", "target_machine_id": "FILLER",
                              "severity": "high", "status": "active"}],
    }
    slim = _trim_context(full_context, "shift_briefing")

    assert isinstance(slim.get("line_state"), list)
    assert len(slim["line_state"]) <= 12
    assert slim["line_state"], "shift_briefing must see the actual line state, not a count summary"

    assert len(slim.get("inventory_snapshot", [])) <= 20
    assert len(slim.get("crew", [])) <= 10
    assert len(slim.get("maintenance", [])) <= 10
    assert len(slim.get("cip_schedule", [])) <= 10
    assert len(slim.get("constraints", [])) <= 5
    assert len(slim.get("recommendations", [])) <= 6
    assert len(slim.get("quality_checks", [])) <= 5
    assert slim.get("active_incidents")


def test_extract_actions_for_shift_briefing_returns_handover_actions():
    _require_langchain()
    from agent_server.agent import _extract_actions

    actions = _extract_actions("shift_briefing", {"active_incidents": []})

    assert actions, "shift_briefing must have HITL actions wired"
    titles = {a["title"] for a in actions}
    assert "Open Handover Note" in titles
    assert "Confirm Crew Coverage" in titles
    assert "Send Briefing to Next Shift" in titles
    assert "File Carryover Items" in titles
    assert all(a["status"] == "pending" for a in actions)
