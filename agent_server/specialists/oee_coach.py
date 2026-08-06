"""OEE Coach factory.

Breaks down OEE into A × P × Q, Pareto-ranks loss contributors, suggests
prioritized improvements. Runs on GPT-5.4 because the loss-tree reasoning is
multi-step and a dropped factor silently corrupts the answer — see
``agent_server.model_roster`` for why each role gets the model it does.
Carries both `oee_loss_analysis` (Pareto across shifts) and
`production_forecast` (Monte Carlo end-of-shift projection) tools that the
lighter specialists don't need.
"""
from databricks_langchain import ChatDatabricks
from langchain.agents import create_agent
from langchain_core.messages import SystemMessage

from agent_server.model_roster import resolve
from agent_server.optimizers.oee_loss_prioritizer import oee_loss_analysis
from agent_server.optimizers.production_forecaster import production_forecast
from agent_server.prompts.loader import prompt_text
from agent_server.tools.calculator import calculator
from agent_server.tools.genie_query import genie_query
from agent_server.tools.governed_domain_query import (
    governed_domain_prompt,
    governed_domain_tools,
)
from agent_server.tools.plan_constraints_loader import load_plan_and_constraints
from agent_server.tools.sql_query import sql_query


def create_oee_coach():
    """Build the ReAct agent registered as 'oee_coach'."""
    llm = ChatDatabricks(endpoint=resolve("oee_coach"))
    tools = [
        sql_query, genie_query, calculator, load_plan_and_constraints,
        oee_loss_analysis, production_forecast,
        *governed_domain_tools("production"),
    ]

    return create_agent(
        model=llm,
        tools=tools,
        system_prompt=SystemMessage(
            content=prompt_text("oee_coach_system") + governed_domain_prompt("production"),
        ),
    )
