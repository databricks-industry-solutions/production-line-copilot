"""Genie Space tool — natural language data querying via Databricks Genie Conversation API.

Uses the REST API directly (rather than the SDK's Genie client) so the tool
works on any installed SDK version that ships the App container — the v2 SDK
Genie client surface has shifted across releases, REST is stable.

The tool authenticates using the Databricks SDK's config (auto-configured in
the Apps runtime via `DATABRICKS_CLIENT_ID` / app SP OAuth) and falls back to
`DATABRICKS_TOKEN` for local dev.

Caching note: ``_make_headers`` is called for every Genie request so OAuth
tokens are always fresh. We deliberately do NOT cache the headers/host because
the OAuth token returned by ``w.config.authenticate()`` rotates and a stale
cached token would 401 silently inside the conversation poll loop.
"""

import logging
import os
import re
import time

import requests
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

_SPACE_ID = os.environ.get("GENIE_SPACE_ID", "")
_MAX_POLL_SECONDS = 60
_POLL_INTERVAL = 2.0

# Heuristic table extractor for provenance: pull identifiers after FROM/JOIN.
# Good enough to show "which tables did Genie use" in the UI; not a SQL parser.
_TABLE_RE = re.compile(r"\b(?:FROM|JOIN)\s+([`\"\w.]+)", re.IGNORECASE)


def extract_tables(sql: str | None) -> list[str]:
    """Return the unique table identifiers referenced in a SQL string.

    Skips subquery openers ("(") and de-duplicates while preserving order so
    the provenance list reads in query order.
    """
    if not sql:
        return []
    seen: list[str] = []
    for match in _TABLE_RE.finditer(sql):
        name = match.group(1).strip("`\"")
        if name and not name.startswith("(") and name not in seen:
            seen.append(name)
    return seen


def _make_headers() -> tuple[dict, str]:
    """Build fresh auth headers for each request (handles token refresh)."""
    host = os.environ.get("DATABRICKS_HOST", "").rstrip("/")

    try:
        # why: pin to app SP so the Genie call uses the app's grant on the
        # space, not the browsing user's OBO identity (which may not have
        # access to the Genie space or its underlying tables).
        from agent_server.tools.workspace_client import app_workspace_client
        w = app_workspace_client()
        host = w.config.host.rstrip("/")
        raw = w.config.authenticate()
        if isinstance(raw, dict):
            raw.setdefault("Content-Type", "application/json")
            return raw, host
        return {"Authorization": f"Bearer {raw}", "Content-Type": "application/json"}, host
    except Exception:
        logger.debug("OAuth auth for Genie failed, falling back to DATABRICKS_TOKEN", exc_info=True)

    token = os.environ.get("DATABRICKS_TOKEN", "")
    if host and token:
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, host

    raise RuntimeError("Cannot authenticate to Databricks for Genie API")


def _poll_message(headers: dict, host: str, space_id: str, conv_id: str, msg_id: str) -> dict:
    """Poll until the Genie message reaches a terminal state."""
    url = f"{host}/api/2.0/genie/spaces/{space_id}/conversations/{conv_id}/messages/{msg_id}"
    deadline = time.time() + _MAX_POLL_SECONDS

    while time.time() < deadline:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status", "")
        if status in ("COMPLETED", "FAILED", "CANCELLED"):
            return data
        time.sleep(_POLL_INTERVAL)

    return {"status": "TIMEOUT"}


def run_genie_query(question: str, space_id: str | None = None) -> dict:
    """Core Genie call. Returns a dict with 'answer', 'sql', 'tables', 'data'
    (or 'error'). Shared by the ``genie_query`` LangChain tool (used inside
    specialists) and the governed per-domain tools below.

    ``space_id`` overrides the default streaming space, which is how the
    governed per-domain tools in ``governed_domain_query`` point the same
    machinery at their own space.
    """
    space = (space_id or _SPACE_ID or "").strip()
    if not space:
        return {"error": "Genie Space not configured (GENIE_SPACE_ID not set)"}

    if not question or len(question.strip()) < 5:
        return {"error": "Please provide a meaningful question (at least 5 characters)"}

    try:
        from agent_server.start_server import _live_record  # lazy: avoid circular at import
        _live_record("genie_queries")
    except Exception:
        logger.debug("Failed to record live genie_queries metric", exc_info=True)

    try:
        headers, host = _make_headers()
        base = f"{host}/api/2.0/genie/spaces/{space}"

        # Genie Conversation API: ``start-conversation`` creates the conversation
        # AND submits the first message in a single call. (The older two-step
        # POST /conversations + POST /conversations/{id}/messages flow returns
        # "No API found" on the current API.)
        start_resp = requests.post(
            f"{base}/start-conversation",
            headers=headers,
            json={"content": question.strip()},
            timeout=30,
        )
        start_resp.raise_for_status()
        start = start_resp.json()
        conv_id = start["conversation_id"]
        msg_id = start["message"]["message_id"]

        data = _poll_message(headers, host, space, conv_id, msg_id)

        result: dict = {"answer": "", "sql": None, "data": []}

        status = data.get("status", "")
        if status == "FAILED":
            return {"error": f"Genie query failed: {data.get('error', 'unknown error')}"}
        if status == "TIMEOUT":
            return {"error": "Genie did not respond within 60 seconds. Try a simpler question."}

        for att in data.get("attachments", []):
            text_content = att.get("text", {}).get("content", "")
            if text_content:
                result["answer"] += text_content + "\n"

            query_info = att.get("query", {})
            if query_info:
                result["sql"] = query_info.get("query")

                # Genie auto-executes the generated SQL; read the cached result
                # via GET .../attachments/{attachment_id}/query-result rather
                # than re-POSTing execute-query. The id field is
                # ``attachment_id`` (not ``id``).
                att_id = att.get("attachment_id")
                if att_id and result["sql"]:
                    try:
                        qr_url = (
                            f"{base}/conversations/{conv_id}/messages/{msg_id}"
                            f"/attachments/{att_id}/query-result"
                        )
                        exec_resp = requests.get(qr_url, headers=headers, timeout=30)
                        if exec_resp.ok:
                            stmt = exec_resp.json().get("statement_response", {})
                            columns = stmt.get("manifest", {}).get("schema", {}).get("columns", [])
                            col_names = [c.get("name", f"col_{i}") for i, c in enumerate(columns)]
                            rows = stmt.get("result", {}).get("data_array", [])
                            for row in (rows or [])[:20]:
                                result["data"].append(dict(zip(col_names, row)))
                    except Exception as exc:
                        logger.warning("Failed to fetch Genie query results: %s", exc)

        if not result["answer"]:
            if result["data"]:
                result["answer"] = f"Query returned {len(result['data'])} rows."
            else:
                result["answer"] = "Genie processed the query but returned no text summary."

        result["answer"] = result["answer"].strip()
        # Provenance: which tables did the generated SQL touch?
        result["tables"] = extract_tables(result.get("sql"))
        return result

    except requests.HTTPError as e:
        logger.exception("Genie HTTP error: %s", e.response.text[:300] if e.response else str(e))
        return {"error": f"Genie API error: {e.response.status_code if e.response else 'unknown'}"}
    except Exception as e:
        logger.exception("Genie query failed: %s", str(e)[:200])
        return {"error": f"Genie query failed: {type(e).__name__}: {str(e)[:200]}"}


@tool
def genie_query(question: str) -> dict:
    """Ask the Databricks Genie data analyst a natural language question about production line data.

    Genie translates the question into SQL against curated, governed datasets and returns
    structured results. Best for analytical / trend / comparison questions such as:
    - "What is the OEE trend over the last 5 shifts?"
    - "Which machine has the most downtime this week?"
    - "Compare quality reject rates across shifts"

    For simple, direct lookups where you know the exact SQL, prefer sql_query instead.

    Args:
        question: A clear natural language question about production data.

    Returns:
        A dict with 'answer' (text summary), optionally 'sql' (generated query),
        'tables' (referenced tables), and 'data' (result rows, max 20).
    """
    return run_genie_query(question)
