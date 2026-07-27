"""Supply Chain Advisor factory.

Predicts material depletion, recommends mitigation (reorder / substitute /
slow down), assesses inventory risk against the production plan. The leanest
toolbox of any specialist — supply-chain calls are mostly inventory math on
the context bundle, so heavy optimisers aren't needed.
"""
import os

from databricks_langchain import ChatDatabricks
from langchain.agents import create_agent
from langchain_core.messages import SystemMessage

from agent_server.prompts.supply_chain_advisor import SUPPLY_CHAIN_SYSTEM
from agent_server.tools.calculator import calculator
from agent_server.tools.plan_constraints_loader import load_plan_and_constraints
from agent_server.tools.similar_case_retriever import retrieve_similar_cases
from agent_server.tools.sql_query import sql_query


def create_supply_chain_advisor():
    """Build the ReAct agent registered as 'supply_chain_advisor'."""
    llm = ChatDatabricks(
        endpoint=os.environ.get("MODEL_SPECIALIST", "databricks-gpt-5-mini"),
        max_tokens=2048,
    )
    tools = [sql_query, load_plan_and_constraints, calculator, retrieve_similar_cases]

    return create_agent(
        model=llm,
        tools=tools,
        system_prompt=SystemMessage(content=SUPPLY_CHAIN_SYSTEM),
    )
