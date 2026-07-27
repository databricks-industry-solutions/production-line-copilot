"""OEE Coach factory.

Breaks down OEE into A × P × Q, Pareto-ranks loss contributors, suggests
prioritized improvements. Uses `MODEL_ADVANCED` (full GPT-5 by default)
because the loss-tree reasoning is multi-step and benefits from a larger
context window than the mini tier. Carries both `oee_loss_analysis`
(Pareto across shifts) and `production_forecast` (Monte Carlo end-of-shift
projection) tools that the lighter specialists don't need.
"""
import os

from databricks_langchain import ChatDatabricks
from langchain.agents import create_agent
from langchain_core.messages import SystemMessage

from agent_server.optimizers.oee_loss_prioritizer import oee_loss_analysis
from agent_server.optimizers.production_forecaster import production_forecast
from agent_server.prompts.oee_coach import OEE_COACH_SYSTEM
from agent_server.tools.calculator import calculator
from agent_server.tools.genie_query import genie_query
from agent_server.tools.plan_constraints_loader import load_plan_and_constraints
from agent_server.tools.sql_query import sql_query


def create_oee_coach():
    """Build the ReAct agent registered as 'oee_coach'."""
    llm = ChatDatabricks(
        endpoint=os.environ.get("MODEL_ADVANCED", "databricks-gpt-5"),
        max_tokens=2048,
    )
    tools = [
        sql_query, genie_query, calculator, load_plan_and_constraints,
        oee_loss_analysis, production_forecast,
    ]

    return create_agent(
        model=llm,
        tools=tools,
        system_prompt=SystemMessage(content=OEE_COACH_SYSTEM),
    )
