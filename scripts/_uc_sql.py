"""Minimal cursor-like adapter over the Databricks SDK Statement Execution API.

Why this exists
---------------
The setup job runs ``init_realtime_uc.py`` / ``bootstrap_reference_data.py`` as
serverless ``spark_python_task``s. Those scripts used to open a
``databricks-sql-connector`` connection, but on serverless job compute that
connector's ``connect()`` imports ``pyarrow`` from the base image, which crashes
against the environment's NumPy 2.x (ABI mismatch) and the task hangs
indefinitely.

The SDK Statement Execution API needs no ``pyarrow`` and reuses the job's
ambient auth, so it runs reliably on serverless compute. ``StmtCursor`` exposes
just the tiny subset of the DB-API cursor the setup scripts use
(``execute`` + ``fetchone`` + ``close``), so those scripts change by one line.
"""
from __future__ import annotations

import os
import time
from typing import Any

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState

_TERMINAL = {
    StatementState.SUCCEEDED,
    StatementState.FAILED,
    StatementState.CANCELED,
    StatementState.CLOSED,
}


def _maybe_int(value: Any) -> Any:
    """SDK returns cells as strings; coerce plain integers (e.g. COUNT(*))."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


class StmtCursor:
    """DB-API-ish cursor backed by ``statement_execution`` on a SQL warehouse."""

    def __init__(self, warehouse_id: str, host: str | None = None) -> None:
        if host and not os.environ.get("DATABRICKS_HOST"):
            os.environ["DATABRICKS_HOST"] = host
        # Ambient auth: on a Databricks job the runtime injects host + token;
        # locally the SDK picks up env vars / the active CLI profile.
        self._w = WorkspaceClient()
        self._warehouse_id = warehouse_id
        self._last = None

    def execute(self, sql: str) -> "StmtCursor":
        resp = self._w.statement_execution.execute_statement(
            warehouse_id=self._warehouse_id,
            statement=sql,
            wait_timeout="30s",
        )
        while resp.status and resp.status.state not in _TERMINAL:
            time.sleep(1.0)
            resp = self._w.statement_execution.get_statement(resp.statement_id)

        state = resp.status.state if resp.status else None
        if state != StatementState.SUCCEEDED:
            err = resp.status.error if resp.status else None
            msg = getattr(err, "message", None) or err or "unknown error"
            raise RuntimeError(f"SQL failed ({state}): {msg}\n  stmt: {sql.strip()[:200]}")
        self._last = resp
        return self

    def fetchone(self) -> list[Any] | None:
        r = self._last
        if not r or not r.result or not r.result.data_array:
            return None
        return [_maybe_int(v) for v in r.result.data_array[0]]

    def fetchall(self) -> list[list[Any]]:
        r = self._last
        if not r or not r.result or not r.result.data_array:
            return []
        return [[_maybe_int(v) for v in row] for row in r.result.data_array]

    def close(self) -> None:  # symmetry with the connector cursor
        self._last = None
