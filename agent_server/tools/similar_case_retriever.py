"""Similar-case retriever — RAG over historical events via Vector Search.

Specialists call this to ground their recommendations in past resolutions
("how did we recover the last time the FILLER seized?"). When the Vector
Search index is unavailable (cold endpoint, missing index, network), we fall
back to a SQL LIKE-style filter on ``event_log`` so the agent always returns
*something* useful instead of failing — degraded recall is preferable to
"sorry I don't know" in the line manager UX.
"""

import logging
import os
import re

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

_SAFE_ID = re.compile(r"^[a-zA-Z0-9_-]+$")


@tool
def retrieve_similar_cases(
    description: str,
    machine_id: str | None = None,
    event_type: str | None = None,
    top_k: int = 3,
) -> list[dict]:
    """Retrieve similar historical events from the Vector Search index.
    Uses the event description to find semantically similar past events.
    Returns top_k similar events with outcome fields."""
    try:
        from databricks.vector_search.client import VectorSearchClient

        try:
            from agent_server.start_server import _live_record  # lazy
            _live_record("vector_search_calls")
        except Exception:
            logger.debug("Failed to record live vector_search_calls metric", exc_info=True)

        vsc = VectorSearchClient()
        index_name = (
            f"{os.environ.get('UC_CATALOG', 'main')}."
            f"{os.environ.get('UC_SCHEMA', 'line_manager_ai_kit')}.event_log_vs_index"
        )

        filters = {}
        if machine_id:
            filters["machine_id"] = machine_id
        if event_type:
            filters["event_type"] = event_type

        results = vsc.get_index(
            endpoint_name=os.environ.get("VS_ENDPOINT", "vs_endpoint"),
            index_name=index_name,
        ).similarity_search(
            query_text=description,
            columns=[
                "event_id", "shift_id", "machine_id", "description",
                "corrective_action", "resolution_minutes", "outcome_oee_impact",
                "outcome_description",
            ],
            filters=filters if filters else None,
            num_results=top_k,
        )

        events = []
        if results and "result" in results:
            for row in results["result"]["data_array"]:
                cols = results["result"]["columns"]
                event = dict(zip([c["name"] for c in cols], row))
                events.append(event)
        return events

    except Exception as e:
        logger.warning("Vector Search unavailable, falling back to SQL: %s", e)
        from agent_server.tools.sql_query import sql_query

        where = ["1=1"]
        if machine_id and _SAFE_ID.match(machine_id):
            where.append(f"machine_id = '{machine_id}'")
        if event_type and _SAFE_ID.match(event_type):
            where.append(f"event_type = '{event_type}'")
        clause = " AND ".join(where)
        safe_k = min(max(int(top_k), 1), 10)
        return sql_query.invoke(
            f"SELECT event_id, shift_id, machine_id, description, corrective_action, "
            f"resolution_minutes, outcome_oee_impact, outcome_description "
            f"FROM event_log WHERE {clause} ORDER BY shift_id DESC LIMIT {safe_k}"
        )
