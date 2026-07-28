"""Tests for `_restart_producer_job_best_effort`.

The "Clean Restart leaves inventory at previous depleted values" bug had
two layers:

  1. The continuous-trigger producer job lets Databricks auto-spawn a
     replacement run with BUNDLE defaults the moment ``cancel_run``
     registers, so the ``run_now(python_params=[..., "--start-fresh"])``
     override often loses the race and the flag never reaches the producer.

  2. Producer rehydration historically read from ``inventory_status_bronze``
     (a Delta table Clean Restart cannot wipe) — so a new run resumed from
     the previous shift's depleted state.

The architectural fix lives in the producer: rehydration now reads from
``ai_serving.*_history`` (Lakebase), which Clean Restart wipes BEFORE this
function runs. Empty Lakebase ⇒ empty rehydration ⇒ fresh ``init_inventory()``,
regardless of whether ``--start-fresh`` actually landed.

These tests focus on what THIS function still owns: assembling and firing
the cancel-and-restart sequence, including the best-effort ``python_params``
override. The rehydration-source guarantees live in
``tests/streaming_sim/test_lakebase_sink_read.py``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _make_mock_workspace_client(*, run_now_raises: bool = False) -> MagicMock:
    w = MagicMock()
    spt = MagicMock()
    spt.parameters = ["--catalog", "demo", "--schema", "stream"]
    task = MagicMock()
    task.spark_python_task = spt
    job_def = MagicMock()
    job_def.settings.tasks = [task]
    w.jobs.get.return_value = job_def

    active_run = MagicMock()
    active_run.run_id = 999
    w.jobs.list_runs.return_value = [active_run]

    if run_now_raises:
        w.jobs.run_now.side_effect = RuntimeError("queue full")
    else:
        run = MagicMock()
        run.run_id = 1001
        w.jobs.run_now.return_value = run

    return w


def test_clean_restart_cancels_active_runs_and_appends_start_fresh(monkeypatch):
    """start_fresh=True attempts to concatenate --start-fresh onto baseline params."""
    from agent_server import start_server as ss

    monkeypatch.setenv("OPS_PRODUCER_JOB_ID", "12345")
    mock_w = _make_mock_workspace_client()
    # ``_restart_producer_job_best_effort`` builds its WorkspaceClient via
    # ``agent_server.tools.workspace_client.app_workspace_client`` (pins to
    # the app SP so queries don't inherit browsing-user OBO). Patch there.
    with patch("agent_server.tools.workspace_client.app_workspace_client", return_value=mock_w):
        out = ss._restart_producer_job_best_effort(start_fresh=True)

    assert out.get("start_fresh") is True, out
    assert out.get("cancelled_runs") == [999], out
    pp = mock_w.jobs.run_now.call_args.kwargs.get("python_params")
    assert pp == ["--catalog", "demo", "--schema", "stream", "--start-fresh"], pp


def test_warm_restart_does_not_pass_python_params(monkeypatch):
    """start_fresh=False is a plain warm restart — no override."""
    from agent_server import start_server as ss

    monkeypatch.setenv("OPS_PRODUCER_JOB_ID", "12345")
    mock_w = _make_mock_workspace_client()
    with patch("agent_server.tools.workspace_client.app_workspace_client", return_value=mock_w):
        out = ss._restart_producer_job_best_effort(start_fresh=False)

    assert "python_params" not in mock_w.jobs.run_now.call_args.kwargs
    assert out.get("start_fresh") is False, out


def test_run_now_failure_surfaces_warning_not_500(monkeypatch):
    """run_now race / permission failure must NOT crash /sim/restart.

    The continuous trigger may still pick up the cancel and spawn a fresh
    run, so we surface a 'warning' instead of an error so the operator
    sees the partial success.
    """
    from agent_server import start_server as ss

    monkeypatch.setenv("OPS_PRODUCER_JOB_ID", "12345")
    mock_w = _make_mock_workspace_client(run_now_raises=True)
    with patch("agent_server.tools.workspace_client.app_workspace_client", return_value=mock_w):
        out = ss._restart_producer_job_best_effort(start_fresh=True)

    assert "warning" in out, out
    assert out.get("cancelled_runs") == [999], out


def test_skips_when_job_id_not_set(monkeypatch):
    """No OPS_PRODUCER_JOB_ID env var → fast skip, never touch the SDK."""
    from agent_server import start_server as ss

    monkeypatch.delenv("OPS_PRODUCER_JOB_ID", raising=False)
    out = ss._restart_producer_job_best_effort(start_fresh=True)
    assert "skipped" in out
