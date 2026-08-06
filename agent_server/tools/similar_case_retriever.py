"""Similar-case retriever — finds past events resembling the one described.

Specialists call this to ground their recommendations in past resolutions
("how did we recover the last time the FILLER seized?").

Two retrieval paths. Vector Search runs when ``VS_ENDPOINT`` names a
provisioned endpoint; otherwise a keyword match over ``gold_history_event_log``
does. Which one answered is reported rather than assumed: every returned row
carries a ``retrieval`` field of ``vector_search`` or ``keyword``, and
``match_score`` is 0 when nothing in the text matched.

Vector Search is opt-in (an endpoint bills hourly, and the deploy that creates
one takes 15-40 minutes), so the keyword path is not a degraded mode — at tens
of events per shift, token overlap is a fair substitute for embeddings. It is
the default and it is expected to run.

Both paths have been wrong in ways that looked fine. The keyword fallback once
ignored the description entirely and returned the machine's most recent events,
so whatever you asked about you got the same rows back, labelled as similar
cases. And the semantic path never executed at all until its two silent
failures were fixed — the client could not authenticate with the credentials a
Databricks App actually has, and the response parser read column names from a
key the server does not return. Both raised, both were caught, and both fell
through to keyword search, which returns plausible results. That is why the
``retrieval`` field exists.
"""

import logging
import os
import re

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

_SAFE_ID = re.compile(r"^[a-zA-Z0-9_-]+$")

# Built by scripts/provision_vector_search.py over `event_case_library`, which
# exists because the outcome columns this tool reads are dropped by the quality
# gate before they reach gold. Keep both in step: a column named here that the
# index does not sync fails the whole search.
_DEFAULT_INDEX = "event_case_index"
_INDEX_COLUMNS = [
    "event_id", "shift_id", "machine_id", "event_type", "severity",
    "description", "root_cause", "corrective_action", "duration_minutes",
    "resolution_minutes", "outcome_oee_impact", "outcome_description",
]

# Words that match everything and therefore rank nothing.
_STOPWORDS = frozenset("""
a an and are as at be been but by for from had has have how in into is it its of
on or that the this to was were what when where which who why with why did does
machine line shift issue problem again last time
""".split())


def _keywords(description: str, limit: int = 6) -> list[str]:
    """Significant lowercase tokens from ``description``, longest first.

    Longest-first because specific words ("carousel", "misfeed") discriminate
    between events and short ones do not.
    """
    tokens = {
        t for t in re.findall(r"[a-z0-9_]+", description.lower())
        if len(t) > 3 and t not in _STOPWORDS
    }
    return sorted(tokens, key=len, reverse=True)[:limit]


@tool
def retrieve_similar_cases(
    description: str,
    machine_id: str | None = None,
    event_type: str | None = None,
    top_k: int = 3,
) -> list[dict]:
    """Find past events resembling the one described, with how they were resolved.

    Matches semantically when a Vector Search index is provisioned, and by
    keyword overlap on the event narrative otherwise. Every returned row says
    which it was in a `retrieval` field, alongside `match_score` — check them
    before describing a result as a close match, because keyword overlap can
    rank an unrelated event highly on one shared word.

    Returns up to top_k events with outcome fields (corrective_action,
    resolution_minutes, outcome_oee_impact)."""
    endpoint = os.environ.get("VS_ENDPOINT", "").strip()
    if not endpoint:
        # No index configured, so do not pay for a round-trip that is certain to
        # fail — and do not record a vector_search_call that never happened.
        return _keyword_search(description, machine_id, event_type, top_k)

    try:
        from databricks.vector_search.client import VectorSearchClient

        try:
            from agent_server.start_server import _live_record  # lazy
            _live_record("vector_search_calls")
        except Exception:
            logger.debug("Failed to record live vector_search_calls metric", exc_info=True)

        vsc = _vector_search_client(VectorSearchClient)
        index_name = os.environ.get("VS_INDEX", "").strip() or (
            f"{os.environ.get('UC_CATALOG', 'main')}."
            f"{os.environ.get('UC_SCHEMA', 'line_manager_ai_kit')}.{_DEFAULT_INDEX}"
        )

        filters = {}
        if machine_id:
            filters["machine_id"] = machine_id
        if event_type:
            filters["event_type"] = event_type

        # HYBRID, not pure ANN. Databricks' own guidance is to reach for hybrid
        # "where domain-specific keywords are important", and that is this corpus
        # exactly: an operator writes "capper misfeed" or "caustic temp", and the
        # exact token carries more signal than the sentence embedding around it.
        # It costs roughly twice the resources of ANN, which is irrelevant at 27
        # vectors on a standard endpoint answering a handful of queries a shift.
        results = vsc.get_index(
            endpoint_name=endpoint,
            index_name=index_name,
        ).similarity_search(
            query_text=description,
            columns=_INDEX_COLUMNS,
            filters=filters if filters else None,
            num_results=top_k,
            query_type="HYBRID",
        )

        return _parse_results(results)

    except Exception as e:
        # Loud, because the fallback is silent by design and an endpoint bills
        # by the hour whether or not anything successfully queries it. A
        # provisioned index that never gets used looks identical, from the
        # outside, to one that works.
        logger.warning("Vector Search unavailable, falling back to keyword match: %s", e)
        return _keyword_search(description, machine_id, event_type, top_k)


def _parse_results(results: dict | None) -> list[dict]:
    """Turn a similarity_search response into rows.

    The column names live under ``manifest.columns``; ``result`` carries only
    ``row_count`` and ``data_array``. Reading them from ``result`` raised
    KeyError on every response, which the caller's except caught and turned
    into a keyword-search fallback — so this path returned results that looked
    entirely reasonable and were never semantic. Between that and the auth bug
    above, Vector Search had never once run in the deployed app.
    """
    if not results:
        return []
    columns = (results.get("manifest") or {}).get("columns")
    if not columns:
        # Older client versions put them here. Cheap to keep, and the cost of
        # getting this wrong is silence rather than an error.
        columns = (results.get("result") or {}).get("columns")
    if not columns:
        raise ValueError(
            f"no column manifest in the Vector Search response (keys: {sorted(results)})"
        )

    names = [c["name"] if isinstance(c, dict) else str(c) for c in columns]
    events = []
    for row in (results.get("result") or {}).get("data_array") or []:
        event = dict(zip(names, row))
        # The index returns relevance as `score`; the keyword fallback calls it
        # `match_score`. Callers should not have to know which path answered.
        if "score" in event and "match_score" not in event:
            event["match_score"] = event.pop("score")
        event["retrieval"] = "vector_search"
        events.append(event)
    return events


def _vector_search_client(factory):
    """Build a VectorSearchClient with credentials the Apps runtime actually has.

    ``VectorSearchClient()`` with no arguments resolves credentials through
    MLflow's ``get_databricks_host_creds``, which wants ``DATABRICKS_TOKEN`` — a
    personal access token. A Databricks App is never given one; it gets OAuth
    client credentials. So the bare constructor raised
    ``InvalidInputException`` on every call inside the deployed app, the
    exception was caught, and the tool quietly answered from keyword search
    instead. The endpoint stayed online and billed the whole time, and because
    the fallback returns perfectly good results, nothing looked wrong.

    Passing the app SP explicitly is the same identity every other client here
    uses; see agent_server/tools/workspace_client.py.
    """
    client_id = (os.environ.get("DATABRICKS_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get("DATABRICKS_CLIENT_SECRET") or "").strip()
    host = (os.environ.get("DATABRICKS_HOST") or "").strip()
    if host and not host.startswith("http"):
        host = f"https://{host}"

    if client_id and client_secret and host:
        return factory(
            workspace_url=host,
            service_principal_client_id=client_id,
            service_principal_client_secret=client_secret,
        )

    token = (os.environ.get("DATABRICKS_TOKEN") or "").strip()
    if token and host:
        # Passed explicitly rather than left to the library: its fallback runs
        # MLflow's credential resolution, which prefers a ~/.databrickscfg
        # profile and hands back a host with no token when that profile uses
        # CLI OAuth — so DATABRICKS_TOKEN in the environment gets ignored.
        return factory(workspace_url=host, personal_access_token=token)

    # Notebook, where the library reads credentials from the execution context.
    return factory()


def _keyword_search(
    description: str,
    machine_id: str | None,
    event_type: str | None,
    top_k: int,
) -> list[dict]:
    """Rank resolved past events by how many of ``description``'s terms they contain.

    Only events that were actually closed out are eligible. Without that filter
    the search returned the current shift's own open incidents — for a machine
    asked about mid-fault, its own siblings, and sometimes the very event being
    diagnosed — each carrying an empty corrective_action, resolution_minutes of
    0 and an empty outcome_description. The analyst reported "historical case
    match is same-shift keyword only, no outcome data" and dropped to LOW
    confidence, correctly. Retrieving the incident you are diagnosing as its own
    precedent is worse than retrieving nothing.

    Narrowest useful match first: same machine and same failure mode. Falling
    back to the same failure mode on other machines matters because the seeded
    history does not cover every machine — DEPALLETIZER had no resolved case of
    any kind — and a breakdown resolved on the CAPPER is a real precedent, as
    long as the answer says whose it was. The scope is returned so the model can
    attribute it rather than imply the case came off this machine.
    """
    for scope, restrict_machine in (("same_machine", True), ("same_event_type", False)):
        if restrict_machine and not (machine_id and _SAFE_ID.match(machine_id)):
            continue
        rows = _run_keyword_query(
            description,
            machine_id if restrict_machine else None,
            event_type,
            top_k,
            scope,
        )
        if rows and rows[0].get("error"):
            return rows
        if rows:
            return rows
    return []


def _run_keyword_query(
    description: str,
    machine_id: str | None,
    event_type: str | None,
    top_k: int,
    scope: str,
) -> list[dict]:
    from agent_server.tools.sql_query import sql_query

    where = [
        "e.timestamp_end IS NOT NULL",
        "b.outcome_description IS NOT NULL AND b.outcome_description <> ''",
    ]
    if machine_id and _SAFE_ID.match(machine_id):
        where.append(f"e.machine_id = '{machine_id}'")
    if event_type and _SAFE_ID.match(event_type):
        where.append(f"e.event_type = '{event_type}'")
    clause = " AND ".join(where)
    safe_k = min(max(int(top_k), 1), 10)

    terms = _keywords(description or "")
    if terms:
        # Searched across the narrative columns together: an operator describing
        # "cap misfeed" may find it recorded as the root cause rather than in
        # the free-text description.
        haystack = ("lower(concat_ws(' ', e.description, e.root_cause, "
                    "e.corrective_action, e.event_type))")
        score = " + ".join(
            f"CASE WHEN {haystack} LIKE '%{t}%' THEN 1 ELSE 0 END" for t in terms
        )
    else:
        score = "0"

    # The outcome columns are joined back from bronze because the quality gate
    # does not carry them into gold. "What fixed it, how long it took and what
    # it cost" is the entire reason to retrieve a past case; without them this
    # returns a description of something that once went wrong and no resolution.
    rows = sql_query.invoke(
        f"SELECT e.event_id, e.shift_id, e.machine_id, e.event_type, e.severity, "
        f"e.description, e.root_cause, e.corrective_action, e.duration_minutes, "
        f"b.resolution_minutes, b.outcome_oee_impact, b.outcome_description, "
        f"({score}) AS match_score "
        f"FROM gold_history_event_log e "
        f"JOIN event_log_bronze b ON b.event_id = e.event_id "
        f"WHERE {clause} "
        f"ORDER BY match_score DESC, e.timestamp_start DESC LIMIT {safe_k}"
    )

    # A failed query comes back as `[{"error": ...}]`, and returning that
    # unchanged handed the model one "similar case" with no root_cause, no
    # corrective_action and no resolution time — a case-shaped hole it would
    # then summarise. Say the search failed instead.
    failures = [r for r in rows if isinstance(r, dict) and r.get("error")]
    if failures:
        return [{"error": f"Similar-case search failed: {failures[0]['error']}", "retrieval": "keyword"}]

    for row in rows:
        if isinstance(row, dict):
            row["retrieval"] = "keyword"
            row["match_scope"] = scope
    return rows
