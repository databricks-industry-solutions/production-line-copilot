"""Supply Chain Advisor factory.

Predicts material depletion, recommends mitigation (reorder / substitute /
slow down), assesses inventory risk against the production plan. The leanest
toolbox of any specialist — supply-chain calls are mostly inventory math on
the context bundle, so heavy optimisers aren't needed. Runs on Llama 4
Maverick — see ``agent_server.model_roster`` for why each role gets the
model it does.
"""
from databricks_langchain import ChatDatabricks
from langchain.agents import create_agent
from langchain_core.messages import SystemMessage

from agent_server.model_roster import resolve
from agent_server.prompts.loader import prompt_text
from agent_server.tools.calculator import calculator
from agent_server.tools.governed_domain_query import (
    governed_domain_prompt,
    governed_domain_tools,
)
from agent_server.tools.plan_constraints_loader import load_plan_and_constraints
from agent_server.tools.similar_case_retriever import retrieve_similar_cases
from agent_server.tools.sql_query import sql_query


def create_supply_chain_advisor():
    """Build the ReAct agent registered as 'supply_chain_advisor'."""
    llm = ChatDatabricks(endpoint=resolve("supply_chain_advisor"))
    tools = [
        sql_query, load_plan_and_constraints, calculator, retrieve_similar_cases,
        *governed_domain_tools("inventory"),
    ]

    return create_agent(
        model=llm,
        tools=tools,
        system_prompt=SystemMessage(
            content=prompt_text("supply_chain_advisor_system")
            + governed_domain_prompt("inventory"),
        ),
    )
