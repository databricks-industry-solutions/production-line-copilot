"""Shift Briefing specialist factory.

Produces pre-shift huddle / post-shift handover readouts. Replaces the
manual whiteboard meeting — line manager reads the output on a tablet (or
mobile via Genie) in <60s and acts on it.

Lean toolbox by design: briefings are summarisation work, not investigation,
so heavyweight optimisers (schedule_recovery, multi_shift_plan, oee_loss_analysis,
production_forecast, stochastic_schedule_recovery, quality_risk_assessment,
detect_anomalies) are intentionally EXCLUDED — `tests/agent_server/test_shift_briefing.py`
enforces this. Runs on Claude Haiku 4.5: this is the largest prompt in the
app and it is read at every shift change, so format discipline and latency
matter more than depth — see ``agent_server.model_roster`` for why each role
gets the model it does.
"""
from databricks_langchain import ChatDatabricks
from langchain.agents import create_agent
from langchain_core.messages import SystemMessage

from agent_server.model_roster import resolve
from agent_server.prompts.shift_briefing import SHIFT_BRIEFING_SYSTEM
from agent_server.tools.calculator import calculator
from agent_server.tools.genie_query import genie_query
from agent_server.tools.plan_constraints_loader import load_plan_and_constraints
from agent_server.tools.similar_case_retriever import retrieve_similar_cases
from agent_server.tools.sql_query import sql_query


def create_shift_briefing():
    """Build the ReAct agent registered as 'shift_briefing'."""
    llm = ChatDatabricks(endpoint=resolve("shift_briefing"), max_tokens=2048)
    tools = [
        sql_query,
        genie_query,
        calculator,
        load_plan_and_constraints,
        retrieve_similar_cases,
    ]

    return create_agent(
        model=llm,
        tools=tools,
        system_prompt=SystemMessage(content=SHIFT_BRIEFING_SYSTEM),
    )
