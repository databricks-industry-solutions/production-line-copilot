"""Strategic Advisor factory.

Multi-shift trend analysis, improvement roadmaps, capex/opex framing,
benchmarking. Uses `MODEL_ADVANCED` (full GPT-5) and a slightly larger
max_tokens budget than tactical specialists because strategy answers need
to weave more numbers + recommendations into a longer narrative.
"""
import os

from databricks_langchain import ChatDatabricks
from langchain.agents import create_agent
from langchain_core.messages import SystemMessage

from agent_server.optimizers.multi_shift_planner import multi_shift_plan
from agent_server.prompts.strategic_advisor import STRATEGIC_ADVISOR_SYSTEM
from agent_server.tools.calculator import calculator
from agent_server.tools.genie_query import genie_query
from agent_server.tools.plan_constraints_loader import load_plan_and_constraints
from agent_server.tools.similar_case_retriever import retrieve_similar_cases
from agent_server.tools.sql_query import sql_query


def create_strategic_advisor():
    """Build the ReAct agent registered as 'strategic_advisor'."""
    llm = ChatDatabricks(
        endpoint=os.environ.get("MODEL_ADVANCED", "databricks-gpt-5"),
        max_tokens=3000,
    )
    tools = [
        sql_query, genie_query, calculator, load_plan_and_constraints,
        retrieve_similar_cases, multi_shift_plan,
    ]

    return create_agent(
        model=llm,
        tools=tools,
        system_prompt=SystemMessage(content=STRATEGIC_ADVISOR_SYSTEM),
    )
