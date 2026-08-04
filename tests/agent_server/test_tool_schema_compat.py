"""Tests for the open-dict tool-schema shim.

Three jobs here.

The first is the usual one: prove the strip does what it claims and leaves the
schemas that were already valid untouched.

The second is the upstream contract. The shim only earns its place while
pydantic still renders a bare ``dict`` as ``additionalProperties: true`` and
while ``databricks_langchain.chat_models`` still looks ``convert_to_openai_tool``
up in its own namespace. Both are asserted directly, so a release that changes
either fails loudly instead of leaving a dead patch and two dark vendors.

The third is a drift guard over the tools we actually ship. The original bug was
invisible from the outside — Qwen and Llama returned "encountered an error" and
the app fell through to the general handler — so the guard is here rather than
in a smoke test.
"""
from __future__ import annotations

import importlib
import pkgutil

import pytest
from langchain_core.tools import BaseTool, tool
from langchain_core.utils.function_calling import convert_to_openai_tool

from agent_server import tool_schema_compat

# Endpoints measured to reject the key, for the error message on the drift guard.
REJECTING_ENDPOINTS = ("databricks-qwen35-122b-a10b", "databricks-llama-4-maverick")


@pytest.fixture(autouse=True)
def _clean_patch_state():
    """Every test starts unpatched and leaves the process as it found it."""
    was_applied = tool_schema_compat.is_applied()
    tool_schema_compat.reset()
    yield
    tool_schema_compat.reset()
    if was_applied:
        tool_schema_compat.apply()


@tool
def _takes_an_open_dict(operation: str, params: dict) -> dict:
    """A tool shaped like calculator."""
    return {}


def _all_shipped_tools() -> list[BaseTool]:
    """Every tool object in the packages a specialist can draw from."""
    found: dict[str, BaseTool] = {}
    for package in ("agent_server.tools", "agent_server.optimizers"):
        pkg = importlib.import_module(package)
        for info in pkgutil.iter_modules(pkg.__path__):
            module = importlib.import_module(f"{package}.{info.name}")
            for value in vars(module).values():
                if isinstance(value, BaseTool):
                    found[value.name] = value
    return list(found.values())


def _open_dict_paths(node, path: str = "") -> list[str]:
    """Every location in a schema where ``additionalProperties`` is literal true."""
    hits: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "additionalProperties" and value is True:
                hits.append(f"{path}.{key}")
            hits.extend(_open_dict_paths(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            hits.extend(_open_dict_paths(value, f"{path}[{index}]"))
    return hits


# --------------------------------------------------------------------------- #
# upstream contract — these failing means the shim needs revisiting
# --------------------------------------------------------------------------- #
def test_upstream_still_renders_a_bare_dict_as_an_open_object():
    """The bug this shim exists for. If upstream stops, delete the shim."""
    schema = convert_to_openai_tool(_takes_an_open_dict)
    assert _open_dict_paths(schema["function"]["parameters"]) == [
        ".properties.params.additionalProperties"
    ], (
        "pydantic no longer renders a bare dict as additionalProperties: true. "
        "agent_server/tool_schema_compat.py may be redundant — check against "
        f"{REJECTING_ENDPOINTS[0]} and remove it with this test."
    )


def test_upstream_still_has_the_seam_we_patch():
    from databricks_langchain import chat_models

    assert callable(getattr(chat_models, "convert_to_openai_tool", None)), (
        "databricks_langchain.chat_models no longer resolves "
        "convert_to_openai_tool in its own namespace. The shim in "
        "agent_server/tool_schema_compat.py is now a no-op and roles on "
        f"{', '.join(REJECTING_ENDPOINTS)} will fail — find the new seam."
    )


# --------------------------------------------------------------------------- #
# the strip itself
# --------------------------------------------------------------------------- #
def test_a_literal_true_is_removed():
    stripped = tool_schema_compat.strip_open_additional_properties(
        {"type": "object", "additionalProperties": True}
    )
    assert stripped == {"type": "object"}


def test_false_is_a_real_constraint_and_survives():
    schema = {"type": "object", "additionalProperties": False}
    assert tool_schema_compat.strip_open_additional_properties(schema) == schema


def test_a_schema_valued_additional_properties_survives():
    """``additionalProperties: {...}`` describes the permitted shape, so it stays."""
    schema = {"type": "object", "additionalProperties": {"type": "string"}}
    assert tool_schema_compat.strip_open_additional_properties(schema) == schema


def test_it_reaches_inside_arrays():
    """estimate_rul's offender is nested under ``items``, not at the top level."""
    stripped = tool_schema_compat.strip_open_additional_properties(
        {
            "properties": {
                "sensor_readings": {
                    "type": "array",
                    "items": {"type": "object", "additionalProperties": True},
                }
            }
        }
    )
    assert _open_dict_paths(stripped) == []
    assert stripped["properties"]["sensor_readings"]["items"] == {"type": "object"}


def test_the_input_is_not_mutated():
    schema = {"type": "object", "additionalProperties": True}
    tool_schema_compat.strip_open_additional_properties(schema)
    assert schema == {"type": "object", "additionalProperties": True}


def test_a_schema_with_nothing_to_strip_is_unchanged():
    schema = convert_to_openai_tool(_takes_an_open_dict)
    del schema["function"]["parameters"]["properties"]["params"]["additionalProperties"]
    assert tool_schema_compat.strip_open_additional_properties(schema) == schema


# --------------------------------------------------------------------------- #
# the patch
# --------------------------------------------------------------------------- #
def test_binding_a_tool_no_longer_emits_an_open_dict():
    from databricks_langchain import chat_models

    assert tool_schema_compat.apply() is True
    schema = chat_models.convert_to_openai_tool(_takes_an_open_dict)
    assert _open_dict_paths(schema) == []
    assert "params" in schema["function"]["parameters"]["properties"], (
        "the argument itself must survive — only the keyword is dropped"
    )


def test_applying_twice_does_not_double_wrap():
    from databricks_langchain import chat_models

    tool_schema_compat.apply()
    tool_schema_compat.apply()
    assert _open_dict_paths(chat_models.convert_to_openai_tool(_takes_an_open_dict)) == []
    tool_schema_compat.reset()
    assert _open_dict_paths(chat_models.convert_to_openai_tool(_takes_an_open_dict)) != []


def test_kill_switch(monkeypatch):
    from databricks_langchain import chat_models

    monkeypatch.setenv("LLM_TOOL_SCHEMA_COMPAT", "0")
    assert tool_schema_compat.apply() is False
    assert _open_dict_paths(chat_models.convert_to_openai_tool(_takes_an_open_dict)) != []


def test_a_missing_seam_is_survivable(monkeypatch):
    """An upgrade that moves the seam must warn, not crash the import."""
    from databricks_langchain import chat_models

    monkeypatch.delattr(chat_models, "convert_to_openai_tool")
    assert tool_schema_compat.apply() is False


def test_the_package_import_applies_it():
    """agent_server/__init__.py is the only place this is wired up."""
    import agent_server

    tool_schema_compat.reset()
    importlib.reload(agent_server)
    assert tool_schema_compat.is_applied() is True


# --------------------------------------------------------------------------- #
# drift guard over the tools we ship
# --------------------------------------------------------------------------- #
def test_no_shipped_tool_reaches_a_model_with_an_open_dict():
    from databricks_langchain import chat_models

    tool_schema_compat.apply()
    offenders = {
        t.name: paths
        for t in _all_shipped_tools()
        if (paths := _open_dict_paths(chat_models.convert_to_openai_tool(t)))
    }
    assert not offenders, (
        f"these tools still bind an open dict: {offenders}. "
        f"{' and '.join(REJECTING_ENDPOINTS)} reject the request outright, so "
        "every specialist on those endpoints fails before the model runs."
    )


def test_the_guard_would_actually_catch_something():
    """Without the patch the guard must fail, or it is proving nothing."""
    offenders = [
        t.name for t in _all_shipped_tools() if _open_dict_paths(convert_to_openai_tool(t))
    ]
    assert offenders, (
        "no shipped tool renders an open dict any more. If that is deliberate, "
        "agent_server/tool_schema_compat.py is redundant — remove it."
    )
