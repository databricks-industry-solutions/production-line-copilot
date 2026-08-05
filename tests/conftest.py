"""Keep the unit suite off the network.

Several code paths under test are decorated with ``@mlflow.trace`` or open a
span directly. With tracing live, running them builds real traces and MLflow's
background exporter then tries to ship them to a tracking backend that no test
has configured, which fails::

    WARNING mlflow.tracing.export.mlflow_v3: Failed to send trace to MLflow
    backend: INVALID_PARAMETER_VALUE: `trace_info.mlflow_experiment.experiment_id`
    is missing.

Harmless in isolation but wrong in three ways: it puts a network call in a unit
test, the export is asynchronous so the warning lands on whichever unrelated
test happens to be running when the queue flushes, and a machine that *can*
reach a backend would quietly write junk traces to it.

Tracing behaviour itself is covered without needing it switched on —
``test_span_outputs.py`` asserts against a fake span and against the source
tree, so nothing here loses coverage.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session", autouse=True)
def _no_trace_export() -> None:
    """Disable MLflow tracing for the whole session."""
    try:
        import mlflow.tracing

        mlflow.tracing.disable()
    except Exception:  # noqa: BLE001 - a missing/renamed API must not fail the suite
        pass
