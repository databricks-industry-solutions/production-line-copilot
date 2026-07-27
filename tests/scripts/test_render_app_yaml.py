"""Tests for scripts/render_app_yaml.py — the single-source-of-truth renderer
that derives ``app.<env>.yaml`` env values from a project-settings profile.

Runs on every deploy, so a regression here silently ships wrong UC/Lakebase/
model values to a fork. Covered here:

  * ``_is_placeholder`` classification
  * field → env mapping for a real profile
  * genie_space_id (optional) is skipped when it's a placeholder
  * MODEL_ORCHESTRATOR falls back to MODEL_SPECIALIST when unset, but honours
    an explicit override
  * "nothing to patch" when every source value is a placeholder
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import scripts.render_app_yaml as render  # noqa: E402

_APP_YAML = """\
env:
  - name: UC_CATALOG
    value: 'OLD'
  - name: UC_SCHEMA
    value: 'OLD'
  - name: PGHOST
    value: 'OLD'
  - name: GENIE_SPACE_ID
    value: 'OLD'
  - name: MODEL_SPECIALIST
    value: 'OLD'
  - name: MODEL_ADVANCED
    value: 'OLD'
  - name: MODEL_ORCHESTRATOR
    value: 'OLD'
"""


def _write_settings(path: Path, dev_profile: dict) -> None:
    import yaml

    path.write_text(
        yaml.safe_dump({"profiles": {"line_manager_dev": dev_profile}}),
        encoding="utf-8",
    )


def _run(monkeypatch, tmp_path: Path, dev_profile: dict) -> str:
    app_yaml = tmp_path / "app.dev.yaml"
    app_yaml.write_text(_APP_YAML, encoding="utf-8")
    settings = tmp_path / "settings.yaml"
    _write_settings(settings, dev_profile)

    monkeypatch.setattr(render, "ROOT", tmp_path)
    monkeypatch.setattr(
        render, "DEFAULT_APP_YAML", {"dev": app_yaml, "prod": tmp_path / "app.yaml"}
    )
    monkeypatch.setattr(sys, "argv", ["render", "dev", "--settings", str(settings)])
    render.main()
    return app_yaml.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# _is_placeholder
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", ["", "   ", "SET_ME", "<your-host>"])
def test_is_placeholder_true(value):
    assert render._is_placeholder(value) is True


@pytest.mark.parametrize("value", ["real", "main", "databricks-gpt-5", 42])
def test_is_placeholder_false(value):
    assert render._is_placeholder(value) is False


# --------------------------------------------------------------------------- #
# main()
# --------------------------------------------------------------------------- #
def test_fills_fields_and_skips_placeholder_genie(monkeypatch, tmp_path):
    out = _run(
        monkeypatch,
        tmp_path,
        {
            "bundle_target": "line_manager_dev",
            "uc_catalog": "mycat",
            "uc_schema": "mysch",
            "lakebase_pg_host": "myhost",
            "genie_space_id": "SET_ME",  # placeholder → must be skipped
            "model_specialist_endpoint": "spec-ep",
            "model_advanced_endpoint": "adv-ep",
            "model_orchestrator_endpoint": "",  # empty → fallback
        },
    )
    assert "value: 'mycat'" in out
    assert "value: 'mysch'" in out
    assert "value: 'myhost'" in out
    assert "value: 'spec-ep'" in out
    assert "value: 'adv-ep'" in out
    # genie stays at its original template value (never clobbered by SET_ME)
    genie_line = [ln for ln in out.splitlines() if "GENIE_SPACE_ID" in ln]
    idx = out.splitlines().index(genie_line[0])
    assert out.splitlines()[idx + 1].strip() == "value: 'OLD'"


def test_orchestrator_falls_back_to_specialist(monkeypatch, tmp_path):
    out = _run(
        monkeypatch,
        tmp_path,
        {
            "bundle_target": "line_manager_dev",
            "model_specialist_endpoint": "spec-ep",
            "model_orchestrator_endpoint": "SET_ME",  # placeholder → fallback
        },
    )
    # MODEL_ORCHESTRATOR mirrors the specialist endpoint
    lines = out.splitlines()
    idx = next(i for i, ln in enumerate(lines) if "MODEL_ORCHESTRATOR" in ln)
    assert lines[idx + 1].strip() == "value: 'spec-ep'"


def test_orchestrator_explicit_override_wins(monkeypatch, tmp_path):
    out = _run(
        monkeypatch,
        tmp_path,
        {
            "bundle_target": "line_manager_dev",
            "model_specialist_endpoint": "spec-ep",
            "model_orchestrator_endpoint": "orch-ep",
        },
    )
    lines = out.splitlines()
    idx = next(i for i, ln in enumerate(lines) if "MODEL_ORCHESTRATOR" in ln)
    assert lines[idx + 1].strip() == "value: 'orch-ep'"


def test_nothing_to_patch_when_all_placeholders(monkeypatch, tmp_path, capsys):
    out = _run(
        monkeypatch,
        tmp_path,
        {
            "bundle_target": "line_manager_dev",
            "uc_catalog": "SET_ME",
            "uc_schema": "",
            "genie_space_id": "<fill-me>",
        },
    )
    assert "value: 'OLD'" in out  # unchanged
    assert "value: 'mycat'" not in out
    assert "nothing to patch" in capsys.readouterr().out.lower()


def test_missing_profile_raises(monkeypatch, tmp_path):
    app_yaml = tmp_path / "app.dev.yaml"
    app_yaml.write_text(_APP_YAML, encoding="utf-8")
    settings = tmp_path / "settings.yaml"
    _write_settings(settings, {"bundle_target": "some_other_target"})
    monkeypatch.setattr(render, "ROOT", tmp_path)
    monkeypatch.setattr(
        render, "DEFAULT_APP_YAML", {"dev": app_yaml, "prod": tmp_path / "app.yaml"}
    )
    monkeypatch.setattr(sys, "argv", ["render", "dev", "--settings", str(settings)])
    with pytest.raises(SystemExit):
        render.main()
