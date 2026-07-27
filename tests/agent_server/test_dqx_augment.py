"""Sanity tests for the DQX augmentation layer.

Only checks configuration / metadata — the actual DQEngine call requires a
Spark session and the DQX extra installed, so it's covered separately by an
integration test on the streaming job cluster (not here).
"""
from __future__ import annotations

import pytest


def test_yaml_loads_and_is_non_empty():
    """Missing / malformed YAML would silently no-op the whole DQX layer in
    production. Explicit assertion here catches accidental deletions."""
    pytest.importorskip("yaml")
    from pipelines.dqx_augment import CHECKS_YAML, load_checks

    assert CHECKS_YAML.exists(), f"dqx_checks.yaml missing at {CHECKS_YAML}"
    checks = load_checks()
    assert len(checks) >= 3, f"expected ≥3 DQX checks, got {len(checks)}"


def test_check_naming_convention():
    """Every check name must start with ``<table>__`` so the DQ dashboard
    can group by table prefix. Enforced here rather than at load time so a
    single typo doesn't take the streaming job down."""
    pytest.importorskip("yaml")
    from pipelines.dqx_augment import load_checks

    known_tables = {"machine_state", "line_kpi", "event_log", "inventory_status"}
    for c in load_checks():
        name = c.get("name", "")
        prefix = name.split("__", 1)[0]
        assert prefix in known_tables, (
            f"DQX check {name!r} uses unknown table prefix {prefix!r}"
        )


def test_filter_by_table_name():
    pytest.importorskip("yaml")
    from pipelines.dqx_augment import load_checks

    ms_checks = load_checks("machine_state")
    assert ms_checks, "machine_state should have at least one DQX check"
    assert all(c["name"].startswith("machine_state__") for c in ms_checks)

    # Non-existent table returns empty list, not an error.
    assert load_checks("no_such_table") == []


def test_is_enabled_defaults_off(monkeypatch):
    """DQX must default OFF so a bare deploy doesn't accidentally start
    writing to a not-yet-created ``dqx_warnings`` table."""
    monkeypatch.delenv("DQX_ENABLED", raising=False)
    from pipelines.dqx_augment import is_enabled
    assert is_enabled() is False


def test_is_enabled_reads_env(monkeypatch):
    monkeypatch.setenv("DQX_ENABLED", "1")
    from pipelines.dqx_augment import is_enabled
    assert is_enabled() is True


def test_apply_dqx_warnings_no_op_when_dqx_missing(monkeypatch):
    """The augment function must be safe to call before DQX is installed —
    otherwise a partial upgrade would take the streaming job down."""
    pytest.importorskip("yaml")

    # Simulate DQX not being importable by pointing sys.modules at None.
    import sys
    monkeypatch.setitem(sys.modules, "databricks.labs.dqx.engine", None)

    from pipelines.dqx_augment import apply_dqx_warnings

    # Spark + DataFrame are unused when the DQX import fails at the top, so
    # passing None here is intentional and demonstrates the fail-safe.
    result = apply_dqx_warnings(spark=None, batch_df=None,
                                table_name="machine_state", dqx_sink="ignored")
    assert result == 0
