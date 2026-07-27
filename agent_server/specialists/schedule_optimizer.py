"""Schedule Optimizer factory.

Calculates feasible recovery plans (speed bump + overtime) when the shift
is behind plan. Wraps three optimisation engines:
  schedule_recovery              deterministic MILP
  production_forecast            Monte Carlo projection
  stochastic_schedule_recovery   two-stage stochastic MILP with MC scenarios

The mini endpoint is enough — the heavy lifting happens inside the tools.
"""
import os

from databricks_langchain import ChatDatabricks
from langchain.agents import create_agent
from langchain_core.messages import SystemMessage

from agent_server.optimizers.production_forecaster import production_forecast
from agent_server.optimizers.schedule_recovery import schedule_recovery
from agent_server.optimizers.stochastic_schedule import stochastic_schedule_recovery
from agent_server.prompts.schedule_optimizer import SCHEDULE_OPTIMIZER_SYSTEM
from agent_server.tools.calculator import calculator
from agent_server.tools.plan_constraints_loader import load_plan_and_constraints
from agent_server.tools.sql_query import sql_query


def create_schedule_optimizer():
    """Build the ReAct agent registered as 'schedule_optimizer'."""
    llm = ChatDatabricks(
        endpoint=os.environ.get("MODEL_SPECIALIST", "databricks-gpt-5-mini"),
        max_tokens=2048,
    )
    tools = [
        sql_query, load_plan_and_constraints, calculator,
        schedule_recovery, production_forecast, stochastic_schedule_recovery,
    ]

    return create_agent(
        model=llm,
        tools=tools,
        system_prompt=SystemMessage(content=SCHEDULE_OPTIMIZER_SYSTEM),
    )
