"""Downtime Analyst specialist factory.

Diagnoses unplanned stops: root cause from sensor precursors + event history,
upstream/downstream cascade impact, ranked recovery options, time-to-recovery
estimate. Tools: SQL, plan/constraints loader, calculator, similar-case
retriever, anomaly detector. Endpoint is the mini tier — downtime RCA is
narrow enough that the larger reasoner doesn't beat it on the eval set.
"""
import os

from databricks_langchain import ChatDatabricks
from langchain.agents import create_agent
from langchain_core.messages import SystemMessage

from agent_server.prompts.downtime_analyst import DOWNTIME_ANALYST_SYSTEM
from agent_server.tools.anomaly_detector import detect_anomalies
from agent_server.tools.calculator import calculator
from agent_server.tools.plan_constraints_loader import load_plan_and_constraints
from agent_server.tools.similar_case_retriever import retrieve_similar_cases
from agent_server.tools.sql_query import sql_query


def create_downtime_analyst():
    """Build the ReAct agent registered as 'downtime_analyst' in `_get_specialists`.

    Reads MODEL_SPECIALIST at construction time; defaults to the mini endpoint.
    Override via env var to A/B test bigger models without code changes.
    """
    llm = ChatDatabricks(
        endpoint=os.environ.get("MODEL_SPECIALIST", "databricks-gpt-5-mini"),
        max_tokens=2048,
    )
    tools = [sql_query, load_plan_and_constraints, calculator, retrieve_similar_cases, detect_anomalies]

    return create_agent(
        model=llm,
        tools=tools,
        system_prompt=SystemMessage(content=DOWNTIME_ANALYST_SYSTEM),
    )
