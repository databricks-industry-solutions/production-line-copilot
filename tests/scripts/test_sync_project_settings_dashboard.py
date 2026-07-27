"""Tests for the per-target dashboard rendering in sync_project_settings.py.

Why this exists: the data-quality dashboard JSON is one file shared across
every bundle target, so the committed source-of-truth ships with
``{{UC_CATALOG}}`` / ``{{UC_SCHEMA}}`` placeholders. ``deploy.sh`` runs
``sync_project_settings.py --target <bundle_target>`` to render those in
place before ``databricks bundle deploy`` uploads them; an EXIT trap then
restores the committed templated copy. These tests pin that contract.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


@pytest.fixture
def fresh_module(tmp_path, monkeypatch):
    """Reload ``sync_project_settings`` with ROOT pointed at a tmp tree.

    The module computes ROOT/DASHBOARD_PATH at import time, so we have to
    materialize a fake repo, monkeypatch the constants, and exercise the
    public ``_render_dashboard`` against the tmp file. This is closer to
    integration than unit but keeps the test self-contained.
    """
    mod = importlib.import_module("scripts.sync_project_settings")
    importlib.reload(mod)

    dash_dir = tmp_path / "dashboards"
    dash_dir.mkdir()
    dash_path = dash_dir / "data_quality.lvdash.json"
    monkeypatch.setattr(mod, "ROOT", tmp_path, raising=False)
    monkeypatch.setattr(mod, "DASHBOARD_PATH", dash_path, raising=False)
    return mod, dash_path


def _template_body() -> str:
    return json.dumps(
        {
            "datasets": [
                {
                    "name": "k",
                    "queryLines": [
                        "SELECT 1 FROM {{UC_CATALOG}}.{{UC_SCHEMA}}.machine_state_bronze"
                    ],
                }
            ]
        }
    )


def test_render_dashboard_substitutes_catalog_and_schema(fresh_module):
    mod, dash_path = fresh_module
    dash_path.write_text(_template_body())

    changed = mod._render_dashboard("line_manager_dev", "cat_a", "sch_b")

    assert changed is True
    out = dash_path.read_text()
    assert "{{UC_CATALOG}}" not in out
    assert "{{UC_SCHEMA}}" not in out
    assert "cat_a.sch_b.machine_state_bronze" in out


def test_render_dashboard_noop_when_no_placeholders(fresh_module):
    mod, dash_path = fresh_module
    dash_path.write_text(
        json.dumps({"datasets": [{"name": "k", "queryLines": ["SELECT 1"]}]})
    )
    before = dash_path.read_text()

    changed = mod._render_dashboard("any", "cat", "sch")

    assert changed is False
    assert dash_path.read_text() == before


def test_render_dashboard_raises_when_target_missing_uc_namespace(fresh_module):
    mod, dash_path = fresh_module
    dash_path.write_text(_template_body())

    with pytest.raises(SystemExit, match="missing uc_catalog or uc_schema"):
        mod._render_dashboard("line_manager_dev", "", "sch")
    with pytest.raises(SystemExit, match="missing uc_catalog or uc_schema"):
        mod._render_dashboard("line_manager_dev", "cat", "")


def test_find_profile_for_target_resolves_by_bundle_target(fresh_module):
    mod, _ = fresh_module
    data = {
        "profiles": {
            "prod_profile": {"bundle_target": "line_manager_prod", "uc_catalog": "c1"},
            "dev_profile": {"bundle_target": "line_manager_dev", "uc_catalog": "c2"},
        }
    }
    assert mod._find_profile_for_target(data, "line_manager_dev")["uc_catalog"] == "c2"
    assert mod._find_profile_for_target(data, "missing") is None
