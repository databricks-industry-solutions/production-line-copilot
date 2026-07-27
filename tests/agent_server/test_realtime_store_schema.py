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
