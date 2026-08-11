"""Plant document tool — asks the Agent Bricks Knowledge Assistant, with citations.

This is the document half of retrieval. ``similar_case_retriever`` searches
resolved *events* ("how did we fix this last time"); this searches the
*documents* ("what does the procedure say"). They are different corpora and they
must not stand in for each other, which is why a failure here returns an error
rather than degrading to anything else: the event library answers in fluent,
plausible prose, so a silent substitution would look like a correct answer to a
question about an SOP.

Opt-in. ``DOCS_KA_ENDPOINT`` names the agent serving endpoint that
``scripts/provision_knowledge_assistant.py`` creates. Unset means the assistant
was never provisioned, and the tool says exactly that instead of returning an
empty answer a specialist could narrate over.

Two shapes of request and response are handled on purpose. An Agent Bricks
endpoint is an agent endpoint, and agent endpoints take either the Responses
form (``input``) or the chat form (``messages``) depending on how the agent was
authored; the reply is correspondingly either ``output[].content[].text`` or
``choices[0].message.content``. Rather than pin a shape we cannot verify from
here, the request tries ``input`` first and retries once with ``messages`` if the
endpoint rejects the schema, and the parser reads whichever reply shape arrives.

Auth follows ``genie_query``: fresh headers per call from the app service
principal, never a cached token, because the OAuth token rotates and a stale one
401s inside a tool call.
"""

import logging
import os

import requests
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

_ENDPOINT = os.environ.get("DOCS_KA_ENDPOINT", "")
_ASSISTANT_ID = os.environ.get("DOCS_KA_ID", "")
_TIMEOUT_SECONDS = 90
_MIN_QUESTION_CHARS = 5

# Status codes an endpoint returns when it dislikes the request *schema* (as
# opposed to the request being unauthorised or the endpoint being down). Only
# these are worth retrying in the other shape.
_SCHEMA_REJECTED = (400, 404, 422)


def docs_assistant_configured() -> bool:
    """Whether a Knowledge Assistant endpoint is wired up for this deployment."""
    return bool((_ENDPOINT or "").strip())


def assistant_id() -> str:
    """The knowledge assistant resource id, for provenance panels. May be empty."""
    return (_ASSISTANT_ID or "").strip()


def _make_headers() -> tuple[dict, str]:
    """Fresh auth headers and host for one call (see module docstring)."""
    host = os.environ.get("DATABRICKS_HOST", "").rstrip("/")

    try:
        from agent_server.tools.workspace_client import app_workspace_client
        w = app_workspace_client()
        host = w.config.host.rstrip("/")
        raw = w.config.authenticate()
        if isinstance(raw, dict):
            raw.setdefault("Content-Type", "application/json")
            return raw, host
        return {"Authorization": f"Bearer {raw}", "Content-Type": "application/json"}, host
    except Exception:
        logger.debug("OAuth auth for the docs assistant failed, trying DATABRICKS_TOKEN",
                     exc_info=True)

    token = os.environ.get("DATABRICKS_TOKEN", "")
    if host and token:
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, host

    raise RuntimeError("Cannot authenticate to Databricks for the plant docs assistant")


def _post(question: str) -> dict:
    """Invoke the assistant endpoint, trying both documented request shapes."""
    headers, host = _make_headers()
    url = f"{host}/serving-endpoints/{_ENDPOINT.strip()}/invocations"
    turn = {"role": "user", "content": question}

    resp = requests.post(url, headers=headers, json={"input": [turn]}, timeout=_TIMEOUT_SECONDS)
    if resp.status_code in _SCHEMA_REJECTED:
        logger.debug("docs assistant rejected the Responses shape (HTTP %s), retrying as chat",
                     resp.status_code)
        resp = requests.post(url, headers=headers, json={"messages": [turn]},
                             timeout=_TIMEOUT_SECONDS)
    resp.raise_for_status()
    return resp.json()


def extract_answer(payload: dict) -> str:
    """Answer text from either agent reply shape. Empty string when there is none."""
    parts: list[str] = []

    for item in payload.get("output") or []:
        content = item.get("content")
        if isinstance(content, str):
            parts.append(content)
            continue
        for part in content or []:
            text = part.get("text")
            if text:
                parts.append(text)

    if not parts:
        for choice in payload.get("choices") or []:
            content = (choice.get("message") or {}).get("content")
            if isinstance(content, str) and content:
                parts.append(content)

    return "\n".join(p.strip() for p in parts if p and p.strip()).strip()


def _citation_rows(payload: dict) -> list[dict]:
    """Every citation-ish record in the reply, from wherever the shape puts them."""
    rows: list[dict] = list(payload.get("citations") or [])
    for item in payload.get("output") or []:
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            rows.extend(part.get("annotations") or [])
    return [r for r in rows if isinstance(r, dict)]


def _document_name(row: dict, uri: str) -> str:
    """The file name an operator would recognise.

    ``title`` is what the assistant sends. Falling back to the last path segment
    means stripping the fragment first: the citation URL carries a
    ``#:~:text=...`` deep link to the quoted sentence, so a naive split returns
    the whole quote glued to the file name.
    """
    title = (row.get("title") or "").strip()
    if title:
        return title
    return uri.split("#")[0].split("?")[0].rstrip("/").split("/")[-1]


def extract_citations(payload: dict) -> list[dict]:
    """Sources behind the answer as ``{doc, uri, page?}``, de-duplicated in order.

    Field names are the ones a live assistant sends, which are not the ones the
    agent documentation suggests: an annotation is
    ``{"type": "url_citation", "url": ..., "title": "sop_filler_changeover.md"}``.
    The documented ``doc_uri`` / ``uri`` / ``source`` spellings are still read
    because this endpoint is in beta and they cost nothing to keep, but ``url``
    is the one that fires in practice. Reading only the documented names dropped
    every citation while still returning a confident answer — the failure mode
    this tool exists to prevent.

    De-duplicated by *document*, not by url. A live answer annotates each cited
    sentence separately and every annotation carries its own ``#:~:text=``
    fragment, so the same document arrives up to seven times with seven distinct
    urls. A provenance list that names one specification three times reads as
    three sources.
    """
    seen: list[dict] = []
    documents: set[str] = set()
    for row in _citation_rows(payload):
        uri = (row.get("url") or row.get("doc_uri") or row.get("uri")
               or row.get("source") or "")
        if not uri:
            continue
        doc = _document_name(row, str(uri))
        if doc in documents:
            continue
        cite = {"doc": doc, "uri": uri}
        page = row.get("page") or row.get("page_number")
        if page is not None:
            cite["page"] = page
        documents.add(doc)
        seen.append(cite)
    return seen


def answered_from_documents(payload: dict) -> bool:
    """Whether this answer actually cites a document.

    Deliberately stricter than the endpoint's own ``custom_outputs.sources_used``
    flag, which was measured to mean "the sources were searched" rather than "the
    answer came from them": a question the corpus does not cover comes back with
    ``sources_used`` true, no citations, and a correct refusal. Treating that as
    grounded would hand a specialist a refusal it might narrate as procedure.

    So the test is whether anything was cited. When the endpoint says it did not
    consult its sources at all, that settles it without looking further.
    """
    if (payload.get("custom_outputs") or {}).get("sources_used") is False:
        return False
    return bool(extract_citations(payload))


def run_plant_docs_query(question: str) -> dict:
    """Core call. Returns ``{answer, citations, retrieval}`` or ``{error}``."""
    if not docs_assistant_configured():
        return {"error": "Plant document assistant not configured (DOCS_KA_ENDPOINT not set). "
                         "Say so rather than answering from memory; the documents were not read."}

    if not question or len(question.strip()) < _MIN_QUESTION_CHARS:
        return {"error": "Please provide a meaningful question (at least 5 characters)"}

    try:
        payload = _post(question.strip())
    except Exception as e:
        logger.exception("Plant docs assistant call failed: %s", str(e)[:200])
        return {"error": f"Plant document assistant failed: {type(e).__name__}: {str(e)[:200]}"}

    answer = extract_answer(payload)
    if not answer:
        return {"error": "The plant document assistant returned no answer. The documents may "
                         "still be indexing, or they may not cover this question."}

    return {
        "answer": answer,
        "citations": extract_citations(payload),
        "grounded": answered_from_documents(payload),
        "retrieval": "knowledge_assistant",
    }


@tool
def query_plant_docs(question: str) -> dict:
    """Answer a question from the plant's engineering documents, with citations.

    Searches the document corpus — standard operating procedures, the line
    process narrative, deviation reports, certificates of analysis, maintenance
    manuals — through the Databricks Knowledge Assistant. Use this for questions
    whose answer is written down rather than measured:
    - "What is the isolation sequence before opening the CAPPER guard?"
    - "What fill target do we set for the 1.0 L format?"
    - "What did the March labeller deviation conclude?"
    - "Does this closure lot need a torque change?"

    For live telemetry use sql_query or the governed domain tools. For how a past
    incident was resolved use retrieve_similar_cases — that searches events, not
    documents, and the two are not interchangeable.

    Args:
        question: A clear question about plant procedure, specification or history.

    Returns:
        A dict with 'answer', 'citations' (the documents it cited), 'grounded'
        and 'retrieval', or 'error' when the assistant is unavailable. On an
        error, say the documents could not be read — do not answer from memory.
        When 'grounded' is False the assistant answered without reading any
        document: report that the documents do not cover the question rather
        than passing the answer on as procedure.
    """
    return run_plant_docs_query(question)
