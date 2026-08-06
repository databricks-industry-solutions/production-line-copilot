"""Quality Specialist factory.

Detects quality deviations (fill drift, NOK spikes, contamination), decides
when a hold is warranted, recommends corrective action (parameter tweak,
line stop, rework). Adds the `quality_risk_assessment` Bayesian causal tool
on top of the standard diagnostic toolbox so it can quantify failure
probability given current conditions. Runs on GLM-5.2 — see
``agent_server.model_roster`` for why each role gets the model it does.
"""
from databricks_langchain import ChatDatabricks
from langchain.agents import create_agent
from langchain_core.messages import SystemMessage

from agent_server.model_roster import resolve
from agent_server.optimizers.quality_causal_model import quality_risk_assessment
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


def create_quality_specialist():
    """Build the ReAct agent registered as 'quality_specialist'."""
    llm = ChatDatabricks(endpoint=resolve("quality_specialist"))
    tools = [
        sql_query, load_plan_and_constraints, calculator,
        retrieve_similar_cases, detect_anomalies, quality_risk_assessment,
        *governed_domain_tools("quality"),
    ]

    return create_agent(
        model=llm,
        tools=tools,
        system_prompt=SystemMessage(
            content=prompt_text("quality_specialist_system")
            + governed_domain_prompt("quality"),
        ),
    )
