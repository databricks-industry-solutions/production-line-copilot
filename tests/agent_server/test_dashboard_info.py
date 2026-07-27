"""Tests for `_resolve_data_quality_dashboard` lookup + cache logic."""

from __future__ import annotations

from typing import Iterable
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_dashboard_cache():
    from agent_server import start_server as ss

    ss._DASHBOARD_INFO_CACHE.clear()
    yield
    ss._DASHBOARD_INFO_CACHE.clear()


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch):
    """Start with no dashboard env vars set."""
    for var in (
        "OPS_DATA_QUALITY_DASHBOARD_ID",
        "OPS_DATA_QUALITY_DASHBOARD_NAME",
        "DATABRICKS_HOST",
        "DATABRICKS_APP_NAME",
    ):
        monkeypatch.delenv(var, raising=False)
    yield monkeypatch


def _mk_dashboard(name: str, dashboard_id: str) -> MagicMock:
    d = MagicMock()
    d.display_name = name
    d.dashboard_id = dashboard_id
    return d


def test_resolve_by_explicit_id_skips_list(env: pytest.MonkeyPatch) -> None:
    from agent_server import start_server as ss

    env.setenv("OPS_DATA_QUALITY_DASHBOARD_ID", "abc-123")
    env.setenv("DATABRICKS_HOST", "example.cloud.databricks.com")

    w = MagicMock()
    w.lakeview.get.return_value = _mk_dashboard("Some Other Name", "abc-123")

    with patch.object(ss, "_ops_workspace", return_value=w):
        info = ss._resolve_data_quality_dashboard()

    assert info["id"] == "abc-123"
    assert info["embed_url"] == "https://example.cloud.databricks.com/embed/dashboardsv3/abc-123"
    w.lakeview.get.assert_called_once_with(dashboard_id="abc-123")
    w.lakeview.list.assert_not_called()


def test_resolve_by_name_default_uses_app_name(env: pytest.MonkeyPatch) -> None:
    from agent_server import start_server as ss

    env.setenv("DATABRICKS_APP_NAME", "production-line-copilot-prod")
    env.setenv("DATABRICKS_HOST", "https://example.cloud.databricks.com/")

    expected_name = "production-line-copilot-prod — Data Quality"
    dashboards: Iterable[MagicMock] = [
        _mk_dashboard("Other dashboard", "xxx"),
        _mk_dashboard(expected_name, "dq-id-99"),
        _mk_dashboard("Yet another", "yyy"),
    ]

    w = MagicMock()
    w.lakeview.list.return_value = iter(dashboards)

    with patch.object(ss, "_ops_workspace", return_value=w):
        info = ss._resolve_data_quality_dashboard()

    assert info["id"] == "dq-id-99"
    assert info["display_name"] == expected_name
    assert info["host"] == "https://example.cloud.databricks.com"
    assert info["embed_url"] == "https://example.cloud.databricks.com/embed/dashboardsv3/dq-id-99"


def test_resolve_caches_after_first_lookup(env: pytest.MonkeyPatch) -> None:
    from agent_server import start_server as ss

    env.setenv("OPS_DATA_QUALITY_DASHBOARD_NAME", "Custom Name")
    env.setenv("DATABRICKS_HOST", "host.cloud")

    w = MagicMock()
    w.lakeview.list.return_value = iter([_mk_dashboard("Custom Name", "cached-id")])

    with patch.object(ss, "_ops_workspace", return_value=w):
        first = ss._resolve_data_quality_dashboard()
        second = ss._resolve_data_quality_dashboard()

    assert first is second or first == second
    # Only one workspace call because of cache
    assert w.lakeview.list.call_count == 1


def test_resolve_raises_when_not_found(env: pytest.MonkeyPatch) -> None:
    from agent_server import start_server as ss

    env.setenv("OPS_DATA_QUALITY_DASHBOARD_NAME", "Missing")

    w = MagicMock()
    w.lakeview.list.return_value = iter([_mk_dashboard("Other", "x")])

    with patch.object(ss, "_ops_workspace", return_value=w):
        with pytest.raises(RuntimeError, match="not found"):
            ss._resolve_data_quality_dashboard()
