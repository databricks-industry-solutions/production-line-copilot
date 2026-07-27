"""Quality Specialist factory.

Detects quality deviations (fill drift, NOK spikes, contamination), decides
when a hold is warranted, recommends corrective action (parameter tweak,
line stop, rework). Adds the `quality_risk_assessment` Bayesian causal tool
on top of the standard diagnostic toolbox so it can quantify failure
probability given current conditions.
"""
import os

from databricks_langchain import ChatDatabricks
from langchain.agents import create_agent
from langchain_core.messages import SystemMessage

from agent_server.optimizers.quality_causal_model import quality_risk_assessment
from agent_server.prompts.quality_specialist import QUALITY_SPECIALIST_SYSTEM
from agent_server.tools.anomaly_detector import detect_anomalies
from agent_server.tools.calculator import calculator
from agent_server.tools.plan_constraints_loader import load_plan_and_constraints
from agent_server.tools.similar_case_retriever import retrieve_similar_cases
from agent_server.tools.sql_query import sql_query


def create_quality_specialist():
    """Build the ReAct agent registered as 'quality_specialist'."""
    llm = ChatDatabricks(
        endpoint=os.environ.get("MODEL_SPECIALIST", "databricks-gpt-5-mini"),
        max_tokens=2048,
    )
    tools = [
        sql_query, load_plan_and_constraints, calculator,
        retrieve_similar_cases, detect_anomalies, quality_risk_assessment,
    ]

    return create_agent(
        model=llm,
        tools=tools,
        system_prompt=SystemMessage(content=QUALITY_SPECIALIST_SYSTEM),
    )
