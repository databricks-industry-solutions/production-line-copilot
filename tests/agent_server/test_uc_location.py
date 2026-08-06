from __future__ import annotations


def test_get_uc_location_falls_back_on_placeholders(monkeypatch):
    monkeypatch.setenv("UC_CATALOG", "${var.catalog}")
    monkeypatch.setenv("UC_SCHEMA", "${var.schema}")

    from agent_server.uc import DEFAULT_UC_SCHEMA, get_uc_location

    loc = get_uc_location()
    assert loc.catalog == "main"
    # Named, not spelled out: this used to pin "line_manager_ai_kit_preview",
    # which kept a fallback pointing at a schema the app no longer installs into.
    assert loc.schema == DEFAULT_UC_SCHEMA
    assert loc.table("x") == f"main.{DEFAULT_UC_SCHEMA}.x"


def test_get_uc_location_uses_real_values(monkeypatch):
    monkeypatch.setenv("UC_CATALOG", "main")
    monkeypatch.setenv("UC_SCHEMA", "line_manager_ai_kit_preview")

    from agent_server.uc import get_uc_location

    loc = get_uc_location()
    # ``machine_state_bronze`` is a real UC table the producer writes to
    # via Zerobus; it stays the canonical example for this resolver test
    # now that ``scenario_commands`` has moved to Lakebase.
    assert loc.table("machine_state_bronze") == "main.line_manager_ai_kit_preview.machine_state_bronze"

