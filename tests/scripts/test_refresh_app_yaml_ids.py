"""Tests for scripts/refresh_app_yaml_ids.py — writes fresh bundle resource IDs
(job / dashboard) into ``app.<env>.yaml`` after each ``bundle deploy``.

The comment/order-preserving line patcher (`_patch_yaml`) and the summary
extraction (`_extract_id`) are the load-bearing pieces; a bug there leaves the
App pointing at stale job IDs (the exact "Data Quality tab 404" class of bug).
"""
from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import scripts.refresh_app_yaml_ids as refresh  # noqa: E402


# --------------------------------------------------------------------------- #
# _extract_id
# --------------------------------------------------------------------------- #
def test_extract_id_int_coerced_to_str():
    summary = {"resources": {"jobs": {"streaming_producer": {"id": 12345}}}}
    assert refresh._extract_id(summary, "jobs", "streaming_producer") == "12345"


def test_extract_id_guid_string():
    summary = {"resources": {"dashboards": {"dq": {"id": "abc-guid"}}}}
    assert refresh._extract_id(summary, "dashboards", "dq") == "abc-guid"


@pytest.mark.parametrize(
    "summary",
    [
        {},
        {"resources": {}},
        {"resources": {"jobs": {}}},
        {"resources": {"jobs": {"k": {}}}},
        {"resources": {"jobs": {"k": {"id": ""}}}},
        {"resources": {"jobs": {"k": {"id": None}}}},
    ],
)
def test_extract_id_missing_returns_none(summary):
    assert refresh._extract_id(summary, "jobs", "k") is None


# --------------------------------------------------------------------------- #
# _pick_app_yaml
# --------------------------------------------------------------------------- #
def test_pick_app_yaml_prefers_local_sibling(tmp_path):
    default = tmp_path / "app.dev.yaml"
    default.write_text("x", encoding="utf-8")
    local = tmp_path / "app.dev.local.yaml"
    local.write_text("y", encoding="utf-8")
    assert refresh._pick_app_yaml(default) == local


def test_pick_app_yaml_falls_back_to_default(tmp_path):
    default = tmp_path / "app.dev.yaml"
    default.write_text("x", encoding="utf-8")
    assert refresh._pick_app_yaml(default) == default


# --------------------------------------------------------------------------- #
# _patch_yaml
# --------------------------------------------------------------------------- #
_YAML = """\
env:
  - name: OPS_PRODUCER_JOB_ID
    value: '111'
  # keep this comment
  - name: OTHER_VAR
    value: 'keep'
  - name: OPS_TTL_JOB_ID
    value: '222'
"""


def test_patch_yaml_replaces_known_and_preserves_rest(tmp_path):
    path = tmp_path / "app.dev.yaml"
    path.write_text(_YAML, encoding="utf-8")
    n = refresh._patch_yaml(path, {"OPS_PRODUCER_JOB_ID": "999", "OPS_TTL_JOB_ID": "888"})
    out = path.read_text(encoding="utf-8")
    assert n == 2
    assert "value: '999'" in out
    assert "value: '888'" in out
    assert "# keep this comment" in out
    assert "value: 'keep'" in out  # unknown var untouched


def test_patch_yaml_idempotent_when_value_unchanged(tmp_path):
    path = tmp_path / "app.dev.yaml"
    path.write_text(_YAML, encoding="utf-8")
    n = refresh._patch_yaml(path, {"OPS_PRODUCER_JOB_ID": "111"})
    assert n == 0
    assert path.read_text(encoding="utf-8") == _YAML


def test_patch_yaml_ignores_unknown_env_names(tmp_path):
    path = tmp_path / "app.dev.yaml"
    path.write_text(_YAML, encoding="utf-8")
    n = refresh._patch_yaml(path, {"NOT_IN_FILE": "x"})
    assert n == 0


def test_patch_yaml_preserves_trailing_newline(tmp_path):
    path = tmp_path / "app.dev.yaml"
    path.write_text(_YAML, encoding="utf-8")
    refresh._patch_yaml(path, {"OPS_PRODUCER_JOB_ID": "999"})
    assert path.read_text(encoding="utf-8").endswith("\n")


# --------------------------------------------------------------------------- #
# _bundle_summary
# --------------------------------------------------------------------------- #
def test_bundle_summary_parses_json(monkeypatch):
    def fake_run(cmd, **kw):
        return types.SimpleNamespace(returncode=0, stdout='{"resources": {}}', stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert refresh._bundle_summary("line_manager_dev", "prof") == {"resources": {}}


def test_bundle_summary_nonzero_raises(monkeypatch):
    def fake_run(cmd, **kw):
        return types.SimpleNamespace(returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(SystemExit):
        refresh._bundle_summary("line_manager_dev", None)


# --------------------------------------------------------------------------- #
# main()
# --------------------------------------------------------------------------- #
def test_main_patches_ids_and_warns_on_missing(monkeypatch, tmp_path, capsys):
    app_yaml = tmp_path / "app.dev.yaml"
    app_yaml.write_text(
        "env:\n"
        "  - name: OPS_PRODUCER_JOB_ID\n    value: 'old'\n"
        "  - name: OPS_DATA_QUALITY_DASHBOARD_ID\n    value: 'old'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(refresh, "ROOT", tmp_path)
    monkeypatch.setattr(refresh, "ENV_TARGETS", {"dev": ("line_manager_dev", app_yaml)})
    # Only two of the five resources resolve → the rest emit a WARN.
    summary = {
        "resources": {
            "jobs": {"streaming_producer": {"id": 500}},
            "dashboards": {"line_manager_data_quality": {"id": "dq-guid"}},
        }
    }
    monkeypatch.setattr(refresh, "_bundle_summary", lambda *a, **k: summary)
    monkeypatch.setattr(sys, "argv", ["refresh", "dev"])

    refresh.main()

    out = app_yaml.read_text(encoding="utf-8")
    assert "value: '500'" in out
    assert "value: 'dq-guid'" in out
    captured = capsys.readouterr()
    assert "missing resources" in captured.err


def test_main_raises_when_nothing_resolves(monkeypatch, tmp_path):
    app_yaml = tmp_path / "app.dev.yaml"
    app_yaml.write_text("env: []\n", encoding="utf-8")
    monkeypatch.setattr(refresh, "ROOT", tmp_path)
    monkeypatch.setattr(refresh, "ENV_TARGETS", {"dev": ("line_manager_dev", app_yaml)})
    monkeypatch.setattr(refresh, "_bundle_summary", lambda *a, **k: {"resources": {}})
    monkeypatch.setattr(sys, "argv", ["refresh", "dev"])
    with pytest.raises(SystemExit):
        refresh.main()
