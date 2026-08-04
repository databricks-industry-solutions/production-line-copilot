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


def test_mvm_catalog_token_substituted_default(monkeypatch):
    """The ``${MVM_CATALOG}`` token in the referential check must be replaced
    at load time — a literal ``${MVM_CATALOG}`` would make DQX target a
    nonsensical table."""
    pytest.importorskip("yaml")
    monkeypatch.delenv("MVM_CATALOG", raising=False)
    from pipelines.dqx_augment import DEFAULT_MVM_CATALOG, load_checks

    fk = [c for c in load_checks() if c.get("name") == "machine_state__machine_id_referential"]
    assert fk, "referential FK check should be present"
    ref_table = fk[0]["check"]["arguments"]["ref_table"]
    assert "${MVM_CATALOG}" not in ref_table
    assert ref_table == f"{DEFAULT_MVM_CATALOG}.asset.equipment_register"


def test_mvm_catalog_token_substituted_from_env(monkeypatch):
    pytest.importorskip("yaml")
    monkeypatch.setenv("MVM_CATALOG", "my_mvm_cat")
    from pipelines.dqx_augment import load_checks

    fk = [c for c in load_checks() if c.get("name") == "machine_state__machine_id_referential"]
    assert fk[0]["check"]["arguments"]["ref_table"] == "my_mvm_cat.asset.equipment_register"


def test_freshness_check_uses_dqx_015_function(monkeypatch):
    """The freshness check must use ``is_data_fresh`` (DQX >=0.15), not the
    removed ``is_older_than_n_seconds`` (0.10) — the latter raises
    ``InvalidCheckError`` at apply time, which the fail-safe swallows so DQX
    silently stops flagging freshness."""
    pytest.importorskip("yaml")
    from pipelines.dqx_augment import load_checks

    fresh = [c for c in load_checks("machine_state")
             if c.get("name", "").startswith("machine_state__stale")]
    assert fresh, "freshness check should be present"
    fn = fresh[0]["check"]["function"]
    assert fn == "is_data_fresh", f"expected is_data_fresh, got {fn!r}"
    assert "max_age_minutes" in fresh[0]["check"]["arguments"]


def test_line_kpi_checks_reference_real_bronze_columns():
    """The line_kpi checks must reference columns that actually exist on
    line_kpi_bronze (total_throughput / oee_to_now). Wrong names make DQX emit
    'invalid check columns' meta-warnings that flood dqx_warnings instead of
    evaluating — a regression that was invisible until DQX actually ran."""
    pytest.importorskip("yaml")
    from pipelines.dqx_augment import load_checks

    by_name = {c["name"]: c for c in load_checks("line_kpi")}
    assert by_name["line_kpi__throughput_non_negative"]["check"]["arguments"]["column"] == "total_throughput"
    oee = by_name["line_kpi__oee_in_unit_range"]["check"]["arguments"]
    assert oee["column"] == "oee_to_now"
    # OEE is a percentage (0..100) in the producer schema, not a 0..1 fraction.
    assert oee["max_limit"] == 100


def test_mvm_catalog_arg_overrides_env(monkeypatch):
    """The explicit ``mvm_catalog`` arg must win over ``MVM_CATALOG`` env.

    Regression guard for the serverless bug: the streaming sink resolves the
    catalog on the driver and passes it in, because a server-side ``os.environ``
    read inside the ``foreachBatch`` closure sees neither the CLI-promoted env
    var nor the correct catalog."""
    pytest.importorskip("yaml")
    monkeypatch.setenv("MVM_CATALOG", "env_cat")
    from pipelines.dqx_augment import load_checks

    fk = [c for c in load_checks(mvm_catalog="arg_cat")
          if c.get("name") == "machine_state__machine_id_referential"]
    assert fk[0]["check"]["arguments"]["ref_table"] == "arg_cat.asset.equipment_register"


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
