"""Tests for the Industry Data Model (MVM) + DQX wiring in bundle-targets.yaml.

v0.3.0 wired the Manufacturing MVM + DQX into ``scripts/sync_project_settings.py``.
Design B (streaming silver) then folded silver population into the streaming job:

  1. The setup job installs the Manufacturing MVM + does an initial silver
     populate (``install_industry_data_model`` → ``populate_silver_model``).
     Dimensions are seeded here once.
  2. The ``streaming_quality_sync`` job keeps the FACT tables fresh by
     MERGE-upserting each micro-batch (``--mvm-silver 1``), replacing the retired
     30-min ``silver_model_refresh`` batch job.
  3. That same job opts into DQX (freshness / range / referential checks) only
     for targets with ``dqx_enabled: true``.

These tests pin that contract so a refactor can't silently drop a task, flip a
toggle for the wrong target, or resurrect the retired batch refresh job.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import scripts.sync_project_settings as sync  # noqa: E402
from scripts.sync_project_settings import (  # noqa: E402
    _resources_setup_plus_zerobus_streaming,
    _solution_accelerator_job,
    _streaming_quality_sync_job,
)


def test_setup_job_installs_and_populates_mvm():
    yaml = "\n".join(_solution_accelerator_job(suffix="-dev", app_sp="sp"))
    # Install task pulls the model + applies via the SDK statement API.
    assert "task_key: install_industry_data_model" in yaml
    assert "python_file: ../scripts/install_industry_data_model.py" in yaml
    # Populate task runs the silver mapper AFTER install + reference data.
    assert "task_key: populate_silver_model" in yaml
    assert "python_file: ../agent_server/serving/silver_mapper.py" in yaml
    assert "- task_key: install_industry_data_model" in yaml  # depends_on
    assert "- task_key: bootstrap_reference" in yaml
    # Both drive the MVM catalog var.
    assert yaml.count("${var.mvm_catalog}") >= 2


def test_retired_batch_refresh_job_is_gone():
    """Design B replaced the 30-min batch refresh with per-micro-batch MERGE in
    the streaming job. The helper must no longer exist and the retired resource
    must not appear anywhere in the generated bundle."""
    assert not hasattr(sync, "_silver_model_refresh_job")
    yaml = "\n".join(
        _resources_setup_plus_zerobus_streaming(
            suffix="-dev", app_sp="sp", mvm_silver_enabled=True,
        )
    )
    assert "silver_model_refresh:" not in yaml
    assert "line-manager-silver-model-refresh" not in yaml


def test_streaming_sync_enables_mvm_silver_only_when_opted_in():
    on = "\n".join(
        _streaming_quality_sync_job(suffix="-dev", app_sp="sp", mvm_silver_enabled=True)
    )
    off = "\n".join(
        _streaming_quality_sync_job(suffix="", app_sp="sp", mvm_silver_enabled=False)
    )
    assert "--mvm-silver" in on
    assert "--mvm-silver" not in off


def test_streaming_sync_mvm_silver_defaults_off():
    """Default (no kwarg) must be dark so the model rides the streaming path
    only on targets that explicitly opt in."""
    yaml = "\n".join(_streaming_quality_sync_job(suffix="", app_sp="sp"))
    assert "--mvm-silver" not in yaml


def test_streaming_sync_enables_dqx_only_when_opted_in():
    on = "\n".join(_streaming_quality_sync_job(suffix="-dev", app_sp="sp", dqx_enabled=True))
    off = "\n".join(_streaming_quality_sync_job(suffix="", app_sp="sp", dqx_enabled=False))
    # MVM catalog is always passed (harmless when DQX off).
    assert "--mvm-catalog" in on
    assert "--mvm-catalog" in off
    # --dqx-enabled 1 only when opted in.
    assert "--dqx-enabled" in on
    assert "--dqx-enabled" not in off


def test_streaming_sync_dqx_defaults_off():
    """Default (no kwarg) must be dark so targets deploy DQX off unless asked."""
    yaml = "\n".join(_streaming_quality_sync_job(suffix="", app_sp="sp"))
    assert "--dqx-enabled" not in yaml


def test_streaming_sync_ships_dqx_library():
    """databricks-labs-dqx must be in the job env — it's imported lazily +
    fail-safe in dqx_augment, so a missing lib silently DISABLES DQX even when
    DQX_ENABLED=1. Ship it on every target (only invoked when enabled)."""
    yaml = "\n".join(_streaming_quality_sync_job(suffix="", app_sp="sp"))
    assert "databricks-labs-dqx" in yaml
    # Pin must be >=0.15: the 0.10 DQEngine ctor + freshness check names differ
    # and dqx_augment targets the 0.15 API.
    assert "databricks-labs-dqx>=0.15" in yaml
