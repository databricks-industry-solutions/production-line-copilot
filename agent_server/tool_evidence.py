"""What a specialist's tools actually did, so the chat can show its evidence.

The "How this decision was made" expander used to list tool *labels* only —
"Asking Genie data analyst" — which tells a line manager that something happened
but not what. The interesting part already exists and was being dropped at the
agent boundary: ``run_genie_query`` returns the SQL Genie generated and the
tables it touched, and ``sql_query`` is *handed* its SQL as a tool argument.
This module lifts both out of the LangGraph message stream.

We deliberately do not surface the model's own reasoning blocks here; those stay
dropped in :mod:`agent_server.llm_output`. Two reasons. Stated reasoning is
frequently unfaithful to what the model actually computed, so it manufactures
confidence rather than earning it, where a query the manager can re-run is
checkable. And only two of the ten roster models emit reasoning blocks at all,
so an evidence trail is the only confidence surface that reads the same
whichever vendor answered.

Row counts are reported honestly. Both tools cap what they return, so a count
that lands exactly on the cap is marked as capped rather than presented as the
true size of the result set.
"""

from __future__ import annotations

import json
from typing import Any

from agent_server.tools.genie_query import extract_tables

# run_genie_query keeps at most this many rows from the Genie query result.
GENIE_ROW_CAP = 20
# sql_query appends "LIMIT 500" when the model's query omits one.
SQL_ROW_CAP = 500

# Genie SQL is generated, not authored, and can run long. The trail is a
# provenance aid, not a SQL editor, and the whole payload rides the SSE stream.
_SQL_MAX_CHARS = 2000

_GOVERNED_PREFIX = "governed_"
_GOVERNED_SUFFIX = "_query"


def governed_domain(tool_name: str) -> str | None:
    """The domain a ``governed_<domain>_query`` tool covers, else None."""
    if not tool_name.startswith(_GOVERNED_PREFIX) or not tool_name.endswith(_GOVERNED_SUFFIX):
        return None
    domain = tool_name[len(_GOVERNED_PREFIX) : -len(_GOVERNED_SUFFIX)]
    return domain or None


def _is_genie_tool(tool_name: str) -> bool:
    return tool_name == "genie_query" or governed_domain(tool_name) is not None


def evidence_from_call(tool_name: str, args: Any) -> dict:
    """Provenance visible in the tool *call* — the SQL the model wrote itself.

    Only ``sql_query`` qualifies: its query is the argument. Genie is asked a
    question, so its SQL only exists once the tool has answered.
    """
    if tool_name != "sql_query" or not isinstance(args, dict):
        return {}
    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        return {}
    return {"sql": _clip(query.strip()), "tables": extract_tables(query)}


def evidence_from_result(tool_name: str, content: Any) -> dict:
    """Provenance visible in the tool *result* — Genie's SQL, tables, row count.

    ``content`` is whatever LangChain put on the ToolMessage: a JSON string for
    the dict and list returns these tools produce, but a bare list when the
    result was empty, since an empty sequence vacuously passes LangChain's
    content-block check and is kept as-is.
    """
    payload = _decode(content)

    if _is_genie_tool(tool_name):
        return _genie_evidence(tool_name, payload)
    if tool_name == "sql_query":
        return _sql_evidence(payload)
    return {}


def _genie_evidence(tool_name: str, payload: Any) -> dict:
    if not isinstance(payload, dict):
        return {}

    domain = governed_domain(tool_name)
    if payload.get("error"):
        evidence = {"error": str(payload["error"])[:200]}
        if domain:
            evidence["domain"] = domain
        return evidence

    evidence: dict = {}
    sql = payload.get("sql")
    if isinstance(sql, str) and sql.strip():
        evidence["sql"] = _clip(sql.strip())

    tables = payload.get("tables")
    if isinstance(tables, list) and tables:
        evidence["tables"] = [str(t) for t in tables]

    data = payload.get("data")
    if isinstance(data, list):
        evidence["rows"] = len(data)
        evidence["rows_capped"] = len(data) >= GENIE_ROW_CAP

    if domain:
        evidence["domain"] = domain
    return evidence


def _sql_evidence(payload: Any) -> dict:
    if not isinstance(payload, list):
        return {}
    # sql_query signals refusal and failure as a single-row error payload.
    if len(payload) == 1 and isinstance(payload[0], dict) and "error" in payload[0]:
        return {"error": str(payload[0]["error"])[:200]}
    return {"rows": len(payload), "rows_capped": len(payload) >= SQL_ROW_CAP}


def _decode(content: Any) -> Any:
    if isinstance(content, str):
        try:
            return json.loads(content)
        except (ValueError, TypeError):
            return None
    return content


def _clip(sql: str) -> str:
    if len(sql) <= _SQL_MAX_CHARS:
        return sql
    return sql[:_SQL_MAX_CHARS] + "\n-- … truncated"
