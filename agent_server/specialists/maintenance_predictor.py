"""Maintenance Predictor factory.

Reads sensor trends + alarm history, picks at-risk machines, recommends PM
windows that minimise production disruption. Adds `estimate_rul` (Remaining
Useful Life estimator) on top of the standard diagnostic tools so it can
quantify time-to-failure rather than just say "high risk".
"""
import os

from databricks_langchain import ChatDatabricks
from langchain.agents import create_agent
from langchain_core.messages import SystemMessage

from agent_server.optimizers.rul_estimator import estimate_rul
from agent_server.prompts.maintenance_predictor import MAINTENANCE_PREDICTOR_SYSTEM
from agent_server.tools.anomaly_detector import detect_anomalies
from agent_server.tools.calculator import calculator
from agent_server.tools.plan_constraints_loader import load_plan_and_constraints
from agent_server.tools.similar_case_retriever import retrieve_similar_cases
from agent_server.tools.sql_query import sql_query


def create_maintenance_predictor():
    """Build the ReAct agent registered as 'maintenance_predictor'."""
    llm = ChatDatabricks(
        endpoint=os.environ.get("MODEL_SPECIALIST", "databricks-gpt-5-mini"),
        max_tokens=2048,
    )
    tools = [
        sql_query, load_plan_and_constraints, calculator,
        retrieve_similar_cases, detect_anomalies, estimate_rul,
    ]

    return create_agent(
        model=llm,
        tools=tools,
        system_prompt=SystemMessage(content=MAINTENANCE_PREDICTOR_SYSTEM),
    )
