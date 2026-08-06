"""Deep links from an answer to the MLflow trace that produced it.

The chat already explains itself in prose — which specialist ran, which tools
fired, what SQL they issued. This is the layer under that: the actual spans,
token counts, prompts and latencies, in the MLflow UI. It answers "prove it"
rather than "explain it", so it lives in the collapsed expander next to the
evidence trail and not on the message itself.

Why a link and not the spans inline: reading a trace back in-process means
``mlflow.get_trace(id, flush=True)``, which blocks on the async export queue —
roughly a second, on every answer, to show a truncated copy of what the MLflow
UI renders in full. The id costs nothing and is available the moment the run
ends.
"""

from __future__ import annotations

import logging
import os
from urllib.parse import quote

import mlflow

logger = logging.getLogger(__name__)

# Databricks routes a single trace at /ml/experiments/<exp>/traces/<trace>.
_TRACE_PATH = "{host}/ml/experiments/{experiment_id}/traces/{trace_id}"

# Where to land when there is an experiment but no usable trace id. A valid page
# one click from the trace beats a deep link that 404s.
_TRACES_TAB = "{host}/ml/experiments/{experiment_id}/traces"

# A Unity Catalog-backed trace is identified by its storage location rather than
# a bare id: `trace:/<catalog>.<schema>.<prefix>/<hex>`. The UI route takes one
# path segment, so interpolating that whole string splits it across three and
# the router matches nothing — the page renders its own 404 while the server
# still answers 200, which is why this could not be caught by checking status
# codes.
#
# Dropping the location and linking the trailing hex stops the 404 but does not
# open the trace. The workspace redirects /traces/<id> to the tab with
# ?selectedEvaluationId=<id>, and the tab's list is keyed on the *full* id —
# search_traces returns 'trace:/<catalog>.<schema>.<prefix>/<hex>' verbatim — so
# a bare hex selects nothing and the user lands on the unfiltered overview.
# A query parameter can carry the slashes that a path segment cannot, once
# percent-encoded, so UC traces link straight at the selection.
_UC_TRACE_PREFIX = "trace:/"

# Where the redirect above lands, built directly so the selection actually matches.
_SELECTED_TRACE = "{host}/ml/experiments/{experiment_id}/traces?selectedEvaluationId={trace_id}"

# Values the deploy templates ship before a real id is substituted in. Treated
# as unset, because a link built from "SET_ME" is worse than no link: it looks
# clickable and lands on a 404.
_PLACEHOLDERS = {"", "SET_ME", "None", "null"}


def _clean(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if value in _PLACEHOLDERS or (value.startswith("<") and value.endswith(">")):
        return ""
    return value


def workspace_host() -> str:
    """``DATABRICKS_HOST`` as an absolute origin, or "" if it isn't usable."""
    host = _clean("DATABRICKS_HOST")
    if not host:
        return ""
    if not host.startswith("http"):
        host = f"https://{host}"
    return host.rstrip("/")


def _has_trace(trace_id: str) -> bool:
    """Whether ``trace_id`` names an actual trace rather than just a location.

    A UC id truncated to its location (nothing after the final slash) is not
    something the UI can select, so the caller falls back to the tab.
    """
    if not trace_id.startswith(_UC_TRACE_PREFIX):
        return bool(trace_id.strip())
    return bool(trace_id.rsplit("/", 1)[-1].strip())


def trace_url(trace_id: str | None) -> str | None:
    """Build the workspace URL for ``trace_id``, or None if we can't.

    Missing host or experiment is normal rather than exceptional — the app runs
    locally without either — so this returns None and the UI simply omits the
    link.
    """
    if not trace_id:
        return None
    host = workspace_host()
    # Safe rather than lucky: MLflow resolves the active experiment from this
    # variable and rewrites it whenever set_experiment() moves, so it always
    # names the experiment the trace actually landed in. A link is only wrong
    # if a caller changes experiments without going through MLflow.
    experiment_id = _clean("MLFLOW_EXPERIMENT_ID")
    if not host or not experiment_id:
        return None
    if not _has_trace(trace_id):
        return _TRACES_TAB.format(host=host, experiment_id=experiment_id)
    if trace_id.startswith(_UC_TRACE_PREFIX):
        return _SELECTED_TRACE.format(
            host=host,
            experiment_id=experiment_id,
            trace_id=quote(trace_id, safe=""),
        )
    return _TRACE_PATH.format(host=host, experiment_id=experiment_id, trace_id=trace_id.strip())


def current_trace_url() -> str | None:
    """URL for the trace this thread just finished, or None.

    ``thread_local=True`` is load-bearing. The default is process-wide, and
    specialists run on a pool of worker threads, so under any concurrency at all
    the default hands one caller a link to another caller's trace — including
    that caller's question, which is visible in the trace preview. Call this on
    the thread that ran the agent; a trace opened in a worker is invisible from
    the thread that submitted the work.
    """
    try:
        return trace_url(mlflow.get_last_active_trace_id(thread_local=True))
    except Exception:
        # Never fail an answer over a link. A missing tracing backend, or an
        # MLflow release that moves this call again, costs the link and nothing
        # else — test_trace_link.py fails loudly if the call itself disappears.
        logger.debug("Could not resolve the current MLflow trace", exc_info=True)
        return None
