"""Unit tests for ``agent_server.tools.workspace_client``.

Guards the fix for the "colleague sees PERMISSION_DENIED on /ops/cost-summary"
bug: any user_authorization scope (even read-only) makes the SDK auth chain
pick up the browsing user's OBO token, causing backend queries against
app-owned resources (SQL warehouse, jobs, Genie) to fail. The helper pins
to the app SP's OAuth-M2M creds injected by the Apps runtime.

We can't touch the real SDK ``WorkspaceClient(config=Config(...))`` path
because the SDK does eager token exchange on construction — that would
hang the test suite trying to reach the fake host. Instead we monkeypatch
``WorkspaceClient`` and ``Config`` inside the module under test and assert
the arguments our factory would have passed.
"""
from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _clear_databricks_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in (
        "DATABRICKS_HOST",
        "DATABRICKS_CLIENT_ID",
        "DATABRICKS_CLIENT_SECRET",
        "DATABRICKS_TOKEN",
        "DATABRICKS_CONFIG_PROFILE",
        "DATABRICKS_CONFIG_FILE",
    ):
        monkeypatch.delenv(k, raising=False)


class _FakeConfig:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class _FakeWorkspaceClient:
    def __init__(self, config: Any = None, **_ignored: Any) -> None:
        self.config = config


def _patch_sdk(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """Replace ``WorkspaceClient`` and ``Config`` in the module under test.

    Returns a ``calls`` dict so tests can inspect what args our factory
    passed. Two entries: ``config`` (list of kwarg dicts) and ``client``
    (list of positional/kwarg tuples).
    """
    import agent_server.tools.workspace_client as mod

    calls: dict[str, list[Any]] = {"config": [], "client": []}

    def fake_config(**kw: Any) -> _FakeConfig:
        calls["config"].append(kw)
        return _FakeConfig(**kw)

    def fake_client(*args: Any, **kw: Any) -> _FakeWorkspaceClient:
        calls["client"].append((args, kw))
        return _FakeWorkspaceClient(**kw)

    monkeypatch.setattr(mod, "Config", fake_config)
    monkeypatch.setattr(mod, "WorkspaceClient", fake_client)
    return calls


def test_uses_oauth_m2m_when_sp_creds_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """When DATABRICKS_CLIENT_ID + _SECRET + _HOST are set, force oauth-m2m."""
    calls = _patch_sdk(monkeypatch)
    monkeypatch.setenv("DATABRICKS_HOST", "https://example.cloud.databricks.com")
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "sp-uuid-1234")
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "sp-secret")

    from agent_server.tools.workspace_client import app_workspace_client
    app_workspace_client()

    assert len(calls["config"]) == 1
    cfg = calls["config"][0]
    assert cfg["auth_type"] == "oauth-m2m"
    assert cfg["client_id"] == "sp-uuid-1234"
    assert cfg["client_secret"] == "sp-secret"
    assert cfg["host"] == "https://example.cloud.databricks.com"


def test_falls_back_when_client_secret_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without DATABRICKS_CLIENT_SECRET we must NOT force oauth-m2m — the
    SDK default auth chain (PAT / user OAuth / profile) is used instead."""
    calls = _patch_sdk(monkeypatch)
    monkeypatch.setenv("DATABRICKS_HOST", "https://example.cloud.databricks.com")
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "sp-uuid-1234")

    from agent_server.tools.workspace_client import app_workspace_client
    app_workspace_client()

    assert calls["config"] == [], "Config() must not be built when SP creds are incomplete"
    assert len(calls["client"]) == 1
    _args, kw = calls["client"][0]
    assert "config" not in kw, "should fall back to default auth chain, not a custom Config"


def test_falls_back_when_host_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without DATABRICKS_HOST we must NOT force oauth-m2m."""
    calls = _patch_sdk(monkeypatch)
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "sp-uuid-1234")
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "sp-secret")

    from agent_server.tools.workspace_client import app_workspace_client
    app_workspace_client()

    assert calls["config"] == []


def test_falls_back_when_no_sp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty env → default auth chain (local dev with a profile / PAT)."""
    calls = _patch_sdk(monkeypatch)

    from agent_server.tools.workspace_client import app_workspace_client
    app_workspace_client()

    assert calls["config"] == []
    assert len(calls["client"]) == 1
