"""Guards for Lakebase synced-table read path (identifier validation)."""

import pytest

from agent_server.serving import lakebase_sync_config as lsc
from agent_server.serving.lakebase_sync_config import is_missing_synced_relation, pg_ident_quoted


def test_pg_ident_quoted_accepts_public() -> None:
    assert pg_ident_quoted("public") == "public"


@pytest.mark.parametrize("bad", ["", "9abc", "a-b", "a;b", "schema.table"])
def test_pg_ident_quoted_rejects_invalid(bad: str) -> None:
    with pytest.raises(ValueError):
        pg_ident_quoted(bad)


class _Exc42P01(Exception):
    pgcode = "42P01"


def test_is_missing_synced_relation_pgcode() -> None:
    assert is_missing_synced_relation(_Exc42P01()) is True


def test_is_missing_synced_relation_message() -> None:
    assert is_missing_synced_relation(
        Exception('relation "public.machine_state" does not exist')
    ) is True


def test_is_missing_synced_relation_other() -> None:
    assert not is_missing_synced_relation(ValueError("other"))


def test_is_missing_synced_relation_follows_cause() -> None:
    inner = _Exc42P01()
    outer = RuntimeError("wrapper")
    outer.__cause__ = inner
    assert is_missing_synced_relation(outer) is True


def test_sse_use_ai_serving_only_overrides_synced_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    # SSE_USE_AI_SERVING_ONLY=true overrides any LAKEBASE_SYNCED_DATA_SCHEMA value.
    monkeypatch.setenv("UC_CATALOG", "main")
    monkeypatch.setenv("LAKEBASE_SYNCED_DATA_SCHEMA", "public")
    monkeypatch.setenv("SSE_USE_AI_SERVING_ONLY", "true")
    assert lsc.lakebase_synced_data_schema() is None
    monkeypatch.delenv("SSE_USE_AI_SERVING_ONLY", raising=False)
    assert lsc.lakebase_synced_data_schema() == "public"


def test_bootstrap_preview_name_forces_ai_serving(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SSE_USE_AI_SERVING_ONLY", raising=False)
    monkeypatch.delenv("SSE_USE_AI_SERVING_ONLY_FORCE_OFF", raising=False)
    monkeypatch.setenv("LAKEBASE_SYNCED_DATA_SCHEMA", "public")
    monkeypatch.setenv("DATABRICKS_APP_NAME", "my-app-preview")
    try:
        assert lsc.lakebase_synced_data_schema() is None
    finally:
        # lakebase_synced_data_schema() mutates os.environ; avoid leaking into other tests.
        monkeypatch.delenv("SSE_USE_AI_SERVING_ONLY", raising=False)


def test_bootstrap_uc_catalog_in_auto_list_forces_ai_serving(monkeypatch: pytest.MonkeyPatch) -> None:
    """Catalogs listed in SSE_USE_AI_SERVING_ONLY_AUTO_CATALOGS flip the env on."""
    monkeypatch.delenv("SSE_USE_AI_SERVING_ONLY", raising=False)
    monkeypatch.delenv("SSE_USE_AI_SERVING_ONLY_FORCE_OFF", raising=False)
    monkeypatch.delenv("DATABRICKS_APP_NAME", raising=False)
    monkeypatch.delenv("APP_NAME", raising=False)
    monkeypatch.setenv("LAKEBASE_SYNCED_DATA_SCHEMA", "public")
    monkeypatch.setenv("UC_CATALOG", "some_catalog")
    monkeypatch.setenv("SSE_USE_AI_SERVING_ONLY_AUTO_CATALOGS", "other_catalog, some_catalog ")
    try:
        assert lsc.lakebase_synced_data_schema() is None
    finally:
        monkeypatch.delenv("SSE_USE_AI_SERVING_ONLY", raising=False)


def test_bootstrap_uc_catalog_not_in_auto_list_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """Catalogs not in the auto-list (and no preview app name) leave the schema visible."""
    monkeypatch.delenv("SSE_USE_AI_SERVING_ONLY", raising=False)
    monkeypatch.delenv("SSE_USE_AI_SERVING_ONLY_FORCE_OFF", raising=False)
    monkeypatch.delenv("DATABRICKS_APP_NAME", raising=False)
    monkeypatch.delenv("APP_NAME", raising=False)
    monkeypatch.delenv("SSE_USE_AI_SERVING_ONLY_AUTO_CATALOGS", raising=False)
    monkeypatch.setenv("LAKEBASE_SYNCED_DATA_SCHEMA", "public")
    monkeypatch.setenv("UC_CATALOG", "main")
    try:
        assert lsc.lakebase_synced_data_schema() == "public"
    finally:
        monkeypatch.delenv("SSE_USE_AI_SERVING_ONLY", raising=False)


class TestKpiRowNormalisation:
    """``_normalize_line_kpi_row`` fills the columns gold ``line_kpi`` lacks.

    It used ``setdefault``, which cannot see the case that actually occurs:
    both readers ``SELECT *``, so a nullable column comes back as a present key
    holding ``None`` and ``setdefault`` leaves it there. Callers then call
    ``float()`` on it and the request dies.
    """

    def test_a_column_that_is_absent_becomes_zero(self):
        from agent_server.serving.realtime_store import _normalize_line_kpi_row

        out = _normalize_line_kpi_row({"shift_id": "s1"})
        assert out["availability_to_now"] == 0
        assert out["cumulative_cases"] == 0

    def test_a_column_that_is_present_but_null_also_becomes_zero(self):
        from agent_server.serving.realtime_store import _normalize_line_kpi_row

        out = _normalize_line_kpi_row({"shift_id": "s1", "oee_to_now": None})
        assert out["oee_to_now"] == 0

    def test_a_real_reading_is_left_alone(self):
        from agent_server.serving.realtime_store import _normalize_line_kpi_row

        out = _normalize_line_kpi_row({"oee_to_now": 74.6, "cumulative_cases": 0})
        assert out["oee_to_now"] == 74.6
        assert out["cumulative_cases"] == 0

    def test_every_filled_column_survives_float(self):
        # The contract the callers actually rely on.
        from agent_server.serving.realtime_store import _normalize_line_kpi_row

        out = _normalize_line_kpi_row(dict.fromkeys(
            ["cumulative_cases", "availability_to_now", "performance_to_now",
             "quality_to_now", "oee_to_now"], None))
        assert all(isinstance(float(v), float) for v in out.values())

    def test_no_row_stays_no_row(self):
        from agent_server.serving.realtime_store import _normalize_line_kpi_row

        assert _normalize_line_kpi_row(None) is None
        assert _normalize_line_kpi_row({}) is None
