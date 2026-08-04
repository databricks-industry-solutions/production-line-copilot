"""Stop the SQL connector's log filter from killing MLflow trace export.

Two libraries, each reasonable alone, combine into a defect that silently
disabled tracing for this app. Both attach a redaction filter to a logger
neither of them owns — ``urllib3.connectionpool``, shared by every HTTP client
in the process — and they run in the order they were installed.

MLflow's goes first. When it finds credentials in a URL it rewrites the message
and clears the arguments it just consumed, which is correct and legal::

    # mlflow/utils/logging_utils.py — SensitiveQueryParamFilter
    if redacted != message:
        record.msg = redacted
        record.args = None

The connector's runs next, and assumes ``args`` is always iterable::

    # databricks/sql/__init__.py — RedactUrlQueryParamsFilter
    record.args = tuple(
        (self.redact(arg) if isinstance(arg, str) else arg) for arg in record.args
    )

``TypeError: 'NoneType' object is not iterable``. A filter raises inside
``Logger.handle``, so the exception surfaces in whoever made the log call.

The loop closes on MLflow. Span data is uploaded to blob storage through
``requests``; a transient failure makes ``urllib3`` log a retry warning
containing the presigned URL; MLflow's filter redacts the credentials in it and
nulls the args; the connector's filter raises; the ``TypeError`` propagates out
of ``session.request`` and kills the upload. MLflow catches it and emits one
line::

    WARNING mlflow.tracing.export.mlflow_v3: Failed to send trace to MLflow
    backend: 'NoneType' object is not iterable

So it takes MLflow's own protection to trigger the bug that destroys MLflow's
own upload — and only on the retry path, which is why every local probe
exported cleanly while the deployed app never landed a single trace. The trace
metadata is written before the spans, so the app produced valid-looking trace
ids for traces that cannot be loaded, and every "Full trace in MLflow" link
404'd.

We repair the connector's filter rather than inserting one of our own, because
position cannot be relied on: an earlier attempt put a guard at the head of the
chain and it did nothing, since MLflow nulls the args downstream of it. Wrapping
the method fixes every instance wherever it sits.

Still broken upstream as of ``databricks-sql-connector`` 4.2.5; introduced by
databricks/databricks-sql-python#341. ``tests/agent_server/test_sql_logging_compat.py``
holds both halves of the interaction, so the release that fixes either one fails
the suite and this file can go.

Set ``SQL_LOGGING_COMPAT=0`` to skip it.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# The logger both libraries hijack, and the reason a SQL driver's bug reaches an
# MLflow upload.
TARGET_LOGGER = "urllib3.connectionpool"

_applied = False


def enabled() -> bool:
    return os.environ.get("SQL_LOGGING_COMPAT", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def apply() -> bool:
    """Repair the connector's filter. Returns True if it is now safe.

    Idempotent and never raises: this runs at import time, so failing here must
    cost trace export, not startup.
    """
    global _applied
    if _applied:
        return True
    if not enabled():
        logger.info("sql_logging_compat: disabled via SQL_LOGGING_COMPAT")
        return False

    try:
        # Imported here rather than lazily because the filter is installed as a
        # side effect of this import, and there is nothing to patch until it
        # has happened. Costs ~50ms, and the app imports it at startup anyway.
        from databricks import sql as databricks_sql
    except Exception as exc:  # noqa: BLE001
        logger.warning("sql_logging_compat: databricks.sql unavailable (%s)", exc)
        return False

    filter_cls = getattr(databricks_sql, "RedactUrlQueryParamsFilter", None)
    if filter_cls is None:
        logger.warning(
            "sql_logging_compat: RedactUrlQueryParamsFilter not found in databricks.sql "
            "— skipping. If the connector has stopped filtering urllib3, this module "
            "can be deleted; if it was renamed, MLflow trace export is broken again."
        )
        return False

    original = filter_cls.filter
    if getattr(original, "__wrapped__", None) is not None:
        _applied = True
        return True

    def tolerate_argless_records(self, record: logging.LogRecord) -> bool:
        # () is what an argless record carries anyway and formats identically,
        # so this only ever widens what the original accepts.
        if record.args is None:
            record.args = ()
        return original(self, record)

    tolerate_argless_records.__wrapped__ = original  # ty:ignore[unresolved-attribute]
    filter_cls.filter = tolerate_argless_records
    _applied = True
    logger.info("sql_logging_compat: connector log filter made argless-safe")
    return True


def is_applied() -> bool:
    return _applied


def reset() -> None:
    """Undo the repair. For tests."""
    global _applied
    if not _applied:
        return
    try:
        from databricks import sql as databricks_sql

        filter_cls = getattr(databricks_sql, "RedactUrlQueryParamsFilter", None)
        original = getattr(getattr(filter_cls, "filter", None), "__wrapped__", None)
        if original is not None:
            filter_cls.filter = original
    except Exception:  # noqa: BLE001
        logger.exception("sql_logging_compat: reset failed")
    _applied = False
