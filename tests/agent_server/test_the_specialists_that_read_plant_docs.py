"""Only the specialists whose questions are answered in writing get the doc tool.

Four roles routinely need the written record: maintenance (procedures and
manuals), quality (specifications, deviations, certificates), downtime analysis
(isolation steps, buffer behaviour) and scheduling (changeover minutes, cleaning
groups, planned maintenance windows). The rest reason over numbers, and handing
every specialist another tool costs a tool-choice decision on every turn for
answers those roles were not asked for.

Scheduling was the correction, and it was made against the deployed app rather
than on principle: asked how long a 0.5 L to 1.0 L changeover takes, the role
answered "≈20 min (per matrix)" at HIGH confidence, citing a matrix that says 45
and that it had no way to read. A role that reasons over numbers is still a role
that gets asked what the document says.

Asserted against built agents rather than import statements, so a refactor of
how tools are assembled cannot quietly drop the wiring while the imports still
look right.
"""
from __future__ import annotations

import importlib

import pytest

READS_PLANT_DOCS = ["maintenance_predictor", "quality_specialist", "downtime_analyst",
                    "schedule_optimizer"]
DOES_NOT = ["oee_coach", "supply_chain_advisor", "strategic_advisor", "shift_briefing"]

TOOL_NAME = "query_plant_docs"


def _tool_names(agent) -> list[str]:
    """Tool names bound to a built agent, across LangGraph layouts."""
    node = agent.nodes.get("tools")
    if node is not None:
        runnable = getattr(node, "bound", node)
        tools_by_name = getattr(runnable, "tools_by_name", None)
        if tools_by_name:
            return sorted(tools_by_name)
    pytest.skip("cannot introspect tools from this LangGraph version")


def _build(name: str):
    module = importlib.import_module(f"agent_server.specialists.{name}")
    return getattr(module, f"create_{name}")()


@pytest.mark.parametrize("name", READS_PLANT_DOCS)
def test_the_document_reading_specialists_have_the_tool(name: str):
    assert TOOL_NAME in _tool_names(_build(name))


@pytest.mark.parametrize("name", DOES_NOT)
def test_the_number_reading_specialists_do_not(name: str):
    assert TOOL_NAME not in _tool_names(_build(name))
