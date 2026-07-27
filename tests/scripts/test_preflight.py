"""Tests for scripts/preflight.py — the read-only prerequisite checker run
before ``./scripts/deploy.sh``.

Preflight is the first thing a forker runs; a false PASS/FAIL wastes their time
or lets a broken deploy through. These tests mock the workspace SDK + local
tooling so they stay hermetic.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import scripts.preflight as pf  # noqa: E402
from scripts.preflight import Status  # noqa: E402


# --------------------------------------------------------------------------- #
# small pure helpers
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", [None, "", "  ", "SET_ME", "<host>"])
def test_is_placeholder_true(value):
    assert pf._is_placeholder(value) is True


@pytest.mark.parametrize("value", ["real", "main", 5])
def test_is_placeholder_false(value):
    assert pf._is_placeholder(value) is False


def test_parse_secret_ref_valid():
    assert pf._parse_secret_ref("{{secrets/scope/key}}") == ("scope", "key")


@pytest.mark.parametrize("ref", ["", "not-a-ref", "{{secrets/only}}"])
def test_parse_secret_ref_invalid(ref):
    assert pf._parse_secret_ref(ref) == (None, None)


def test_normalize_host():
    assert pf._normalize_host("HTTPS://Foo.Bar/") == "https://foo.bar"


def test_find_profile_for_target():
    data = {
        "profiles": {
            "a": {"bundle_target": "line_manager_prod"},
            "b": {"bundle_target": "line_manager_dev"},
        }
    }
    name, prof = pf._find_profile_for_target(data, "line_manager_dev")
    assert name == "b"
    assert prof["bundle_target"] == "line_manager_dev"


def test_find_profile_for_target_absent():
    assert pf._find_profile_for_target({"profiles": {}}, "x") == (None, None)


# --------------------------------------------------------------------------- #
# _discover_serving_endpoints
# --------------------------------------------------------------------------- #
def test_discover_endpoints_from_profile_overrides():
    prof = {
        "model_specialist_endpoint": "spec",
        "model_advanced_endpoint": "adv",
        "model_orchestrator_endpoint": "SET_ME",  # skipped
    }
    assert pf._discover_serving_endpoints(prof) == ["adv", "spec"]


def test_discover_endpoints_fallback_when_agent_py_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(pf, "AGENT_PY", tmp_path / "nope.py")
    assert pf._discover_serving_endpoints(None) == [
        "databricks-gpt-5-mini",
        "databricks-gpt-5",
    ]


def test_discover_endpoints_parses_agent_py(monkeypatch, tmp_path):
    agent = tmp_path / "agent.py"
    agent.write_text(
        'x = os.environ.get("MODEL_SPECIALIST", "databricks-gpt-5-mini")\n'
        'y = "databricks-gpt-5"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(pf, "AGENT_PY", agent)
    assert pf._discover_serving_endpoints(None) == [
        "databricks-gpt-5",
        "databricks-gpt-5-mini",
    ]


# --------------------------------------------------------------------------- #
# check_required_config
# --------------------------------------------------------------------------- #
def test_required_config_no_profile_fails():
    results = pf.check_required_config(None, None)
    assert results[0].status is Status.FAIL


def test_required_config_placeholder_fails():
    prof = {k: "SET_ME" for k in pf.REQUIRED_CONFIG_KEYS}
    results = pf.check_required_config("dev", prof)
    assert results[0].status is Status.FAIL
    assert "placeholder" in results[0].message


def test_required_config_all_filled_passes():
    prof = {k: f"val-{k}" for k in pf.REQUIRED_CONFIG_KEYS}
    results = pf.check_required_config("dev", prof)
    assert results[0].status is Status.PASS


# --------------------------------------------------------------------------- #
# check_local_tooling  (mock which + _run_cmd; keep hermetic)
# --------------------------------------------------------------------------- #
def _node_result(results):
    return next(r for r in results if r.name == "node")


def test_local_tooling_node_ok(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(pf, "_run_cmd", lambda argv: (0, "v22.3.0"))
    assert _node_result(pf.check_local_tooling()).status is Status.PASS


def test_local_tooling_node_too_old(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(pf, "_run_cmd", lambda argv: (0, "v18.0.0"))
    assert _node_result(pf.check_local_tooling()).status is Status.FAIL


def test_local_tooling_node_unparseable(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(pf, "_run_cmd", lambda argv: (0, "vBANANA"))
    assert _node_result(pf.check_local_tooling()).status is Status.WARN


def test_local_tooling_missing_cli(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(pf, "_run_cmd", lambda argv: (0, "v22.0.0"))
    results = {r.name: r for r in pf.check_local_tooling()}
    assert results["databricks CLI"].status is Status.FAIL
    assert results["uv"].status is Status.FAIL


# --------------------------------------------------------------------------- #
# run_api_checks  (fake WorkspaceClient)
# --------------------------------------------------------------------------- #
def _fake_workspace_client(**overrides):
    import types

    defaults = dict(
        current_user=types.SimpleNamespace(
            me=lambda: types.SimpleNamespace(user_name="me@example.com")
        ),
        config=types.SimpleNamespace(host="https://ws.cloud.databricks.com"),
        warehouses=types.SimpleNamespace(
            get=lambda wid: types.SimpleNamespace(state="RUNNING")
        ),
        catalogs=types.SimpleNamespace(get=lambda c: None),
        serving_endpoints=types.SimpleNamespace(get=lambda e: None),
        secrets=types.SimpleNamespace(
            list_scopes=lambda: [types.SimpleNamespace(name="line-manager")]
        ),
    )
    defaults.update(overrides)
    return types.SimpleNamespace(**defaults)


_PROF = {
    "workspace_host": "https://ws.cloud.databricks.com",
    "sql_warehouse_id": "wh123",
    "uc_catalog": "mycat",
    "producer_client_secret_ref": "{{secrets/line-manager/zerobus-client-secret}}",
    "genie_space_id": "sp123",
}


def _install_client(monkeypatch, w):
    import databricks.sdk as sdk

    monkeypatch.setattr(sdk, "WorkspaceClient", lambda **kw: w)


def test_api_checks_all_pass(monkeypatch):
    _install_client(monkeypatch, _fake_workspace_client())
    results, sdk_ok = pf.run_api_checks("prof", _PROF, ["databricks-gpt-5"])
    assert sdk_ok is True
    assert not any(r.status is Status.FAIL for r in results)
    names = {r.name for r in results}
    assert "CLI auth" in names and "SQL warehouse" in names and "UC catalog" in names


def test_api_checks_warehouse_not_found(monkeypatch):
    from databricks.sdk.errors import NotFound

    def _raise(_wid):
        raise NotFound("nope")

    import types

    w = _fake_workspace_client(warehouses=types.SimpleNamespace(get=_raise))
    _install_client(monkeypatch, w)
    results, _ = pf.run_api_checks("prof", _PROF, [])
    wh = next(r for r in results if r.name == "SQL warehouse")
    assert wh.status is Status.FAIL


def test_api_checks_endpoint_not_found(monkeypatch):
    from databricks.sdk.errors import NotFound

    def _raise(_e):
        raise NotFound("nope")

    import types

    w = _fake_workspace_client(serving_endpoints=types.SimpleNamespace(get=_raise))
    _install_client(monkeypatch, w)
    results, _ = pf.run_api_checks("prof", _PROF, ["missing-endpoint"])
    ep = next(r for r in results if "serving endpoint" in r.name)
    assert ep.status is Status.FAIL


def test_api_checks_client_construction_fails(monkeypatch):
    import databricks.sdk as sdk

    def _boom(**kw):
        raise RuntimeError("no auth")

    monkeypatch.setattr(sdk, "WorkspaceClient", _boom)
    results, sdk_ok = pf.run_api_checks("prof", _PROF, [])
    assert sdk_ok is True
    assert results[0].name == "WorkspaceClient"
    assert results[0].status is Status.FAIL
