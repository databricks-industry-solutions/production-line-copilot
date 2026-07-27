"""Tests for the App-SP CAN_MANAGE_RUN permissions emitted into bundle-targets.yaml.

The Clean Restart bug ("inventory shows previous values, depleting") was
caused by the App SP lacking ``CAN_MANAGE_RUN`` on the producer job. The
declarative fix lives in ``scripts/sync_project_settings.py``: every Job
resource the App calls ``run_now`` / ``cancel_run`` on now emits a
``permissions:`` block scoped to the App SP. These tests pin that contract
so a future refactor can't silently drop the block and re-introduce the
"can't cancel producer" failure mode.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.sync_project_settings import (  # noqa: E402
    _app_sp_permissions_block,
    _remove_after_tag_line,
    _solution_accelerator_job,
    _streaming_producer_job,
    _streaming_quality_sync_job,
)


def test_permissions_block_omitted_when_app_sp_unset():
    """Workshop-style profiles without an App SP must not emit a partial block."""
    assert _app_sp_permissions_block("") == []
    assert _app_sp_permissions_block("   ") == []
    assert _app_sp_permissions_block("SET_ME") == []


def test_permissions_block_grants_can_manage_run():
    """Configured SP → CAN_MANAGE_RUN (not CAN_VIEW, not CAN_MANAGE)."""
    lines = _app_sp_permissions_block("abc-123")
    yaml = "\n".join(lines)
    assert "permissions:" in yaml
    assert 'service_principal_name: "abc-123"' in yaml
    assert "level: CAN_MANAGE_RUN" in yaml
    # Negative — must not accidentally elevate to CAN_MANAGE / IS_OWNER.
    assert "IS_OWNER" not in yaml
    assert "level: CAN_MANAGE\n" not in yaml + "\n"


def test_producer_job_embeds_permissions_when_sp_provided():
    """Producer is the canonical job for Clean Restart — perm block is mandatory."""
    yaml = "\n".join(_streaming_producer_job(suffix="-dev", app_sp="sp-1"))
    assert "name: line-manager-streaming-producer-dev" in yaml
    assert 'service_principal_name: "sp-1"' in yaml
    assert "level: CAN_MANAGE_RUN" in yaml


def test_streaming_quality_sync_job_embeds_permissions_when_sp_provided():
    """Streaming sync is also invoked by /ops/control/action run_now."""
    yaml = "\n".join(_streaming_quality_sync_job(suffix="", app_sp="sp-2"))
    assert "name: line-manager-streaming-quality-sync" in yaml
    assert 'service_principal_name: "sp-2"' in yaml


def test_setup_job_embeds_permissions_when_sp_provided():
    """Setup job is run_now-triggered from the Admin / Cost & Obs tab."""
    yaml = "\n".join(_solution_accelerator_job(suffix="", app_sp="sp-3"))
    assert "name: line-manager-solution-accelerator-setup" in yaml
    assert 'service_principal_name: "sp-3"' in yaml


def test_jobs_omit_permissions_when_sp_missing():
    """Workshop / SET_ME case: no permissions block is emitted at all."""
    for job_lines in (
        _streaming_producer_job(suffix="", app_sp=""),
        _streaming_quality_sync_job(suffix="", app_sp="SET_ME"),
        _solution_accelerator_job(suffix="", app_sp=""),
    ):
        yaml = "\n".join(job_lines)
        assert "permissions:" not in yaml, yaml[:200]


def test_remove_after_tag_line_references_var():
    """The tag must point at ``${var.remove_after}`` so a single bundle var
    (per profile) sets the value across every emitted job. Hard-coding would
    require editing five places per profile.
    """
    line = _remove_after_tag_line()
    assert line.strip() == 'RemoveAfter: "${var.remove_after}"'


def test_all_jobs_carry_remove_after_tag():
    """Every job we emit must carry the RemoveAfter tag so workspaces that
    enforce a retention policy (auto-deleting untagged resources) can pick up
    the value from ``var.remove_after``. When that var is empty the tag is
    still emitted with an empty value, which is harmless.
    """
    for job_lines in (
        _solution_accelerator_job(suffix="", app_sp="sp"),
        _streaming_producer_job(suffix="", app_sp="sp"),
        _streaming_quality_sync_job(suffix="", app_sp="sp"),
    ):
        yaml = "\n".join(job_lines)
        assert 'RemoveAfter: "${var.remove_after}"' in yaml, yaml[:300]


def test_generated_bundle_targets_yaml_contains_permissions_for_streaming():
    """Integration: the committed bundle-targets.yaml must carry the grants
    for both streaming profiles (prod + dev). Catches a forgotten regen on the
    source project-settings.yaml.

    Reads the expected SP ids from ``config/project-settings.yaml`` so the
    test stays correct after a workspace migration (App SPs are auto-created
    per-app-name and rotate when the workspace changes).
    """
    import yaml as _yaml

    settings_path = ROOT / "config" / "project-settings.yaml"
    settings = _yaml.safe_load(settings_path.read_text(encoding="utf-8"))
    profiles = settings.get("profiles", {})
    fvm_sp = profiles.get("line_manager_prod", {}).get("app_client_id", "")
    fvd_sp = profiles.get("line_manager_dev", {}).get("app_client_id", "")
    # Skip if either profile is in the unconfigured "SET_ME" state — the
    # public template ships this way and forks haven't deployed yet.
    if fvm_sp in ("", "SET_ME") or fvd_sp in ("", "SET_ME"):
        import pytest
        pytest.skip("streaming profiles use SET_ME placeholders (pre-first-deploy)")

    targets_path = ROOT / "config" / "bundle-targets.yaml"
    text = targets_path.read_text(encoding="utf-8")

    # Each streaming profile target should have 3 permission blocks (producer,
    # streaming_quality_sync, solution_accelerator_setup).
    fvm_count = text.count(f'service_principal_name: "{fvm_sp}"')
    fvd_count = text.count(f'service_principal_name: "{fvd_sp}"')
    assert fvm_count >= 3, f"prod App SP {fvm_sp} grants missing in bundle-targets.yaml: {fvm_count}"
    assert fvd_count >= 3, f"dev App SP {fvd_sp} grants missing in bundle-targets.yaml: {fvd_count}"
