"""Tests for scripts/preflight.py — the read-only prerequisite checker run
before ``./scripts/deploy.sh``.

Preflight is the first thing a forker runs; a false PASS/FAIL wastes their time
or lets a broken deploy through. These tests mock the workspace SDK + local
tooling so they stay hermetic.
"""
from __future__ import annotations

import shutil
import sys
import types
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
SHIPPED_TIERS = {"databricks-gpt-5", "databricks-gpt-5-mini"}


def test_discover_endpoints_from_profile_overrides():
    prof = {
        "model_specialist_endpoint": "spec",
        "model_advanced_endpoint": "adv",
        "model_orchestrator_endpoint": "SET_ME",  # skipped
    }
    assert {"spec", "adv"} <= set(pf._discover_serving_endpoints(prof))


def test_discover_endpoints_falls_back_to_the_shipped_tiers():
    assert pf._discover_serving_endpoints(None) == sorted(SHIPPED_TIERS)


def test_a_partly_configured_profile_still_checks_the_runtime_default():
    """Configuring one tier must not switch off checking of the others.

    This returned the configured names *instead of* the defaults, so a profile
    naming only the specialist tier left `databricks-gpt-5` unverified — which
    is what the advanced roles actually resolve to at runtime. Preflight passed
    clean and every advanced role then failed on its first model call.
    """
    required = set(pf._discover_serving_endpoints({"model_specialist_endpoint": "spec"}))
    assert "spec" in required
    assert "databricks-gpt-5" in required, (
        "the un-configured advanced tier's runtime default is not being checked"
    )


def test_the_defaults_checked_are_the_ones_the_app_would_use():
    # Restating the constant here is how preflight ends up verifying an endpoint
    # the runtime stopped using, so ask the roster instead.
    from agent_server import model_roster

    required = set(pf._discover_serving_endpoints(None))
    for tier in ("specialist", "advanced"):
        assert model_roster._tier_endpoint(tier) in required


def test_a_placeholder_endpoint_is_not_a_requirement():
    prof = {"model_specialist_endpoint": "<fill-me>", "model_advanced_endpoint": "SET_ME"}
    assert pf._discover_serving_endpoints(prof) == sorted(SHIPPED_TIERS)


def test_only_the_tiers_are_hard_requirements():
    """Per-role models must not be validated as required.

    They are optional by design — a role whose preferred model is missing falls
    back to its tier. Listing them here would fail preflight on any workspace
    that doesn't serve all six vendors, which is most of them.
    """
    required = pf._discover_serving_endpoints({"model_specialist_endpoint": "spec"})
    roster = {ep for ep, _ in pf._roster_endpoints()}
    assert set(required) == {"spec"} | SHIPPED_TIERS
    assert not roster & (set(required) - SHIPPED_TIERS)


def test_roster_endpoints_are_discovered_with_their_roles():
    roster = pf._roster_endpoints()
    assert roster, "roster should be importable from preflight"
    roles = {role for _, role in roster}
    assert "downtime_analyst" in roles
    assert "intent_router" in roles
    # Every entry names a real endpoint, or the WARN message would be nonsense.
    assert all(ep and ep.startswith("databricks-") for ep, _ in roster)


# --------------------------------------------------------------------------- #
# report_model_coverage — the `--models` provider check
# --------------------------------------------------------------------------- #
def _coverage_output(monkeypatch, capsys, served, prof=None):
    """Run the coverage report against a fake workspace endpoint listing."""

    class _FakeEndpoint:
        def __init__(self, name):
            self.name = name

    class _FakeServing:
        @staticmethod
        def list():
            return [_FakeEndpoint(n) for n in served]

    class _FakeWorkspace:
        def __init__(self, *_args, **_kwargs):
            self.serving_endpoints = _FakeServing()

    fake_sdk = types.ModuleType("databricks.sdk")
    fake_sdk.WorkspaceClient = _FakeWorkspace
    monkeypatch.setitem(sys.modules, "databricks.sdk", fake_sdk)

    pf.report_model_coverage("someprofile", prof, ["tier-a", "tier-b"])
    return capsys.readouterr().out


def test_coverage_reports_every_role_as_served(monkeypatch, capsys):
    served = {ep for ep, _ in pf._roster_endpoints()}
    out = _coverage_output(monkeypatch, capsys, served)
    for _, role in pf._roster_endpoints():
        assert role in out
    assert "MISSING" not in out
    assert "All 10 roles served" in out


def test_coverage_names_the_roles_that_will_fall_back(monkeypatch, capsys):
    """The point of the check: say which vendors this workspace is missing."""
    roster = pf._roster_endpoints()
    dropped_ep, dropped_role = roster[0]
    served = {ep for ep, _ in roster} - {dropped_ep}

    out = _coverage_output(monkeypatch, capsys, served)
    assert "MISSING" in out
    assert dropped_role in out
    assert "will fall back to the tier endpoints" in out


def test_coverage_marks_config_overrides_as_such(monkeypatch, capsys):
    """An operator pinning a role in settings should see that reflected."""
    roster = pf._roster_endpoints()
    _, role = roster[0]
    served = {ep for ep, _ in roster} | {"my-own-model"}

    out = _coverage_output(
        monkeypatch, capsys, served, prof={"model_roles": {role: "my-own-model"}}
    )
    line = next(ln for ln in out.splitlines() if ln.strip().startswith(role))
    assert "my-own-model" in line
    assert "config" in line


def test_coverage_never_raises_when_the_workspace_is_unreachable(monkeypatch, capsys):
    """It runs on every deploy, so an auth blip must not break the deploy."""

    class _Boom:
        def __init__(self, *_a, **_k):
            raise RuntimeError("no creds")

    fake_sdk = types.ModuleType("databricks.sdk")
    fake_sdk.WorkspaceClient = _Boom
    monkeypatch.setitem(sys.modules, "databricks.sdk", fake_sdk)

    pf.report_model_coverage("someprofile", None, ["tier-a"])
    out = capsys.readouterr().out
    assert "Coverage unknown" in out
    assert "unknown" in out


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


# --------------------------------------------------------------------------- #
# governed domain spaces
#
# The seven domain specialists degrade to sql_query when their space is absent,
# and the app says nothing about it — so preflight is the only place an operator
# learns before deploying. What makes this subtle is that "none wired" is
# correct in one environment and a broken deploy in another, and the two must
# not print the same thing.
# --------------------------------------------------------------------------- #
_DOMAINS = ("asset", "quality", "inventory", "production")


def _prof_with_domains(*wired, mvm=True):
    prof = dict(_PROF, mvm_silver_enabled=mvm)
    for i, d in enumerate(wired):
        prof[f"genie_space_{d}"] = f"space{i}"
    return prof


def _domain_check(monkeypatch, prof):
    _install_client(monkeypatch, _fake_workspace_client())
    results, _ = pf.run_api_checks("prof", prof, [])
    return next(r for r in results if r.name == "governed domain spaces")


def test_all_four_domains_wired_is_ok(monkeypatch):
    check = _domain_check(monkeypatch, _prof_with_domains(*_DOMAINS))
    assert check.status is Status.PASS
    assert "all 4" in check.message


def test_governed_model_off_and_no_domains_is_not_a_warning(monkeypatch):
    """An environment that never opted in is correct, not broken."""
    check = _domain_check(monkeypatch, _prof_with_domains(mvm=False))
    assert check.status is Status.PASS
    assert "mvm_silver_enabled is off" in check.message


def test_governed_model_on_but_nothing_provisioned_warns(monkeypatch):
    """The loudest case, and the one the first version of this check missed.

    `if wired:` skipped the empty list, so an environment where provisioning had
    never run reported exactly what a fully wired one did: nothing.
    """
    check = _domain_check(monkeypatch, _prof_with_domains(mvm=True))
    assert check.status is Status.WARN
    assert "0/4" in check.message
    for domain in _DOMAINS:
        assert domain in check.message


def test_partial_provisioning_names_what_is_missing(monkeypatch):
    check = _domain_check(monkeypatch, _prof_with_domains("asset", "quality"))
    assert check.status is Status.WARN
    assert "2/4" in check.message
    assert "inventory" in check.message and "production" in check.message
    assert "asset" not in check.message.split("missing:")[1]


def test_a_placeholder_space_id_does_not_count_as_wired(monkeypatch):
    """SET_ME is what deploy writes to request recapture, not a real space."""
    prof = dict(_PROF, mvm_silver_enabled=True)
    for d in _DOMAINS:
        prof[f"genie_space_{d}"] = "SET_ME"
    check = _domain_check(monkeypatch, prof)
    assert check.status is Status.WARN
    assert "0/4" in check.message


def test_the_domain_check_always_reports(monkeypatch):
    """Every combination says something. Silence was the original defect."""
    for mvm in (True, False):
        for count in range(len(_DOMAINS) + 1):
            prof = _prof_with_domains(*_DOMAINS[:count], mvm=mvm)
            _install_client(monkeypatch, _fake_workspace_client())
            results, _ = pf.run_api_checks("prof", prof, [])
            assert any(r.name == "governed domain spaces" for r in results), (
                f"no governed domain line for mvm={mvm} wired={count}"
            )


def test_api_checks_client_construction_fails(monkeypatch):
    import databricks.sdk as sdk

    def _boom(**kw):
        raise RuntimeError("no auth")

    monkeypatch.setattr(sdk, "WorkspaceClient", _boom)
    results, sdk_ok = pf.run_api_checks("prof", _PROF, [])
    assert sdk_ok is True
    assert results[0].name == "WorkspaceClient"
    assert results[0].status is Status.FAIL
