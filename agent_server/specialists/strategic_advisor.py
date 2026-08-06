"""Strategic Advisor factory.

Multi-shift trend analysis, improvement roadmaps, capex/opex framing,
benchmarking. Runs on Claude Opus 5 — see ``agent_server.model_roster`` for why
each role gets the model it does.

This used to claim a larger max_tokens budget than the tactical specialists,
on the grounds that strategy answers argue rather than report. Its prompt says
otherwise: it renders the same ``RESPONSE_STRUCTURE`` as every other
specialist, five sections capped at 250 words. The budget is shared because
the output contract is shared.
"""
from databricks_langchain import ChatDatabricks
from langchain.agents import create_agent
from langchain_core.messages import SystemMessage

from agent_server.model_roster import resolve
from agent_server.optimizers.multi_shift_planner import multi_shift_plan
from agent_server.prompts.loader import prompt_text
from agent_server.tools.calculator import calculator
from agent_server.tools.genie_query import genie_query
from agent_server.tools.governed_domain_query import (
    governed_domain_prompt,
    governed_domain_tools,
)
from agent_server.tools.plan_constraints_loader import load_plan_and_constraints
from agent_server.tools.similar_case_retriever import retrieve_similar_cases
from agent_server.tools.sql_query import sql_query


def create_strategic_advisor():
    """Build the ReAct agent registered as 'strategic_advisor'."""
    llm = ChatDatabricks(endpoint=resolve("strategic_advisor"))
    tools = [
        sql_query, genie_query, calculator, load_plan_and_constraints,
        retrieve_similar_cases, multi_shift_plan,
        *governed_domain_tools("production"),
    ]

    return create_agent(
        model=llm,
        tools=tools,
        system_prompt=SystemMessage(
            content=prompt_text("strategic_advisor_system")
            + governed_domain_prompt("production"),
        ),
    )
