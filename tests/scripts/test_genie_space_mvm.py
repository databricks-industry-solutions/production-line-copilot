"""Tests for the governed MVM Genie space config (scripts/genie_space_config_mvm.py).

The governed space exposes the Manufacturing MVM silver model (asset.*,
production.*, quality.*, inventory.*) to Genie. The critical invariant is
HONESTY: the space must describe ONLY columns the silver mapper actually
populates — never the hundreds of enterprise columns that stay NULL in this
demo. These tests pin that contract by cross-checking the Genie column configs
against ``agent_server.serving.silver_mapper.MAPPINGS`` so the two can never
drift.

All tests load the module's ``serialized_space``/``tables`` directly — no
network, no Databricks CLI — so they run in any environment.
"""
from __future__ import annotations

import importlib
import json

import pytest


@pytest.fixture(scope="module")
def mod():
    return importlib.import_module("scripts.genie_space_config_mvm")


@pytest.fixture(scope="module")
def space(mod) -> dict:
    ss = getattr(mod, "serialized_space")
    assert isinstance(ss, dict)
    return ss


@pytest.fixture(scope="module")
def populated_columns() -> dict[tuple[str, str], set[str]]:
    """(schema, table) -> the set of columns the silver mapper populates.

    Union of batch ``columns`` and streaming ``stream_columns`` so a column
    written on EITHER path counts as populated (and is therefore fair game to
    describe in the Genie space)."""
    sm = importlib.import_module("agent_server.serving.silver_mapper")
    out: dict[tuple[str, str], set[str]] = {}
    for m in sm.MAPPINGS:
        cols = set(m.columns) | set(m.stream_columns or {})
        out[(m.schema, m.table)] = cols
    return out


def test_twelve_tables_registered(space):
    tables = space["data_sources"]["tables"]
    assert len(tables) == 12, "governed space must register all 12 MVM entities"


def test_identifiers_use_template_and_valid_schemas(space):
    """Every identifier is __CAT__.<schema>.<table> with a real MVM schema."""
    valid_schemas = {"asset", "production", "quality", "inventory"}
    for t in space["data_sources"]["tables"]:
        ident = t["identifier"]
        assert ident.startswith("__CAT__."), ident
        _, schema, table = ident.split(".")
        assert schema in valid_schemas, ident
        assert table


def test_described_columns_are_a_subset_of_populated(space, populated_columns):
    """HONESTY GATE: no Genie column may reference an unpopulated MVM column.

    If this fails, the Genie space is advertising a column the silver mapper
    never writes — Genie would query NULLs and fabricate empty answers. Either
    populate the column in silver_mapper or drop it from the Genie config.
    """
    for t in space["data_sources"]["tables"]:
        _, schema, table = t["identifier"].split(".")
        described = {c["column_name"] for c in t["column_configs"]}
        populated = populated_columns.get((schema, table))
        assert populated is not None, f"{schema}.{table} has no silver_mapper mapping"
        extra = described - populated
        assert not extra, (
            f"{schema}.{table} Genie config describes unpopulated columns "
            f"{sorted(extra)} — either populate them in silver_mapper or remove "
            f"them from genie_space_config_mvm.py"
        )


def test_incident_facts_flagged_empty_at_steady_state(space):
    """failure_record, inspection_result and ncr must warn they can be empty."""
    want = {"asset.failure_record", "quality.inspection_result", "quality.ncr"}
    for t in space["data_sources"]["tables"]:
        _, schema, table = t["identifier"].split(".")
        if f"{schema}.{table}" in want:
            desc = " ".join(t["description"]).lower()
            assert "empty" in desc, f"{schema}.{table} must flag it can be empty"


def test_production_line_oee_not_multiplied(space):
    """OEE column must warn against the historic *100 inflation bug."""
    pl = next(t for t in space["data_sources"]["tables"]
              if t["identifier"].endswith(".production.production_line"))
    oee = next(c for c in pl["column_configs"]
               if c["column_name"] == "actual_oee_percentage")
    joined = " ".join(oee["description"]).lower()
    assert "do not multiply by 100" in joined


def test_single_text_instruction_block_documents_join_graph(space):
    """Genie allows one text_instructions block; it must document the join keys."""
    blocks = space["instructions"]["text_instructions"]
    assert len(blocks) == 1
    joined = "".join(blocks[0]["content"]).lower()
    assert "xxhash64" in joined
    assert "equipment_register_id" in joined
    assert "do not" in joined and "100" in joined  # OEE caveat


def test_sample_questions_and_example_sqls_present(space):
    assert len(space["config"]["sample_questions"]) >= 8
    examples = space["instructions"]["example_question_sqls"]
    assert len(examples) >= 6
    for e in examples:
        # Example SQL must target the governed catalog template, not a live table.
        body = "".join(e["sql"])
        assert "__CAT__." in body, e["id"]


def test_no_benchmarks_key_conflict(space):
    """Payload must round-trip through JSON exactly (catches stray refs/keys)."""
    assert json.loads(json.dumps(space)) == space


def test_catalog_substitution_removes_all_placeholders(mod, tmp_path, monkeypatch):
    """A dry-run render must substitute every __CAT__ token."""
    import sys
    argv = [
        "genie_space_config_mvm.py", "--dry-run",
        "--catalog", "my_cat", "--warehouse-id", "wid", "--profile", "prof",
    ]
    monkeypatch.setattr(sys, "argv", argv)
    mod.main()
    from pathlib import Path
    blob = Path("/tmp/genie_curated_mvm.json").read_text()
    payload = json.loads(blob)
    assert "__CAT__" not in payload["serialized_space"]
    assert "my_cat.asset.equipment_register" in payload["serialized_space"]
