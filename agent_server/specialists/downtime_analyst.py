"""Downtime Analyst specialist factory.

Diagnoses unplanned stops: root cause from sensor precursors + event history,
upstream/downstream cascade impact, ranked recovery options, time-to-recovery
estimate. Tools: SQL, plan/constraints loader, calculator, similar-case
retriever, anomaly detector. Runs on Claude Sonnet 5 — see
``agent_server.model_roster`` for why each role gets the model it does.
"""
from databricks_langchain import ChatDatabricks
from langchain.agents import create_agent
from langchain_core.messages import SystemMessage

from agent_server.model_roster import resolve
from agent_server.prompts.loader import prompt_text
from agent_server.tools.anomaly_detector import detect_anomalies
from agent_server.tools.calculator import calculator
from agent_server.tools.governed_domain_query import (
    governed_domain_prompt,
    governed_domain_tools,
)
from agent_server.tools.plan_constraints_loader import load_plan_and_constraints
from agent_server.tools.similar_case_retriever import retrieve_similar_cases
from agent_server.tools.sql_query import sql_query


def create_downtime_analyst():
    """Build the ReAct agent registered as 'downtime_analyst' in `_get_specialists`.

    Resolves its endpoint at construction time; set ``MODEL_DOWNTIME_ANALYST``
    to A/B a different model without touching code.
    """
    llm = ChatDatabricks(endpoint=resolve("downtime_analyst"))
    tools = [
        sql_query, load_plan_and_constraints, calculator,
        retrieve_similar_cases, detect_anomalies,
        *governed_domain_tools("asset"),
    ]

    return create_agent(
        model=llm,
        tools=tools,
        system_prompt=SystemMessage(
            content=prompt_text("downtime_analyst_system") + governed_domain_prompt("asset"),
        ),
    )
