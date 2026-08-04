"""Tests for the tool-call field preservation shim.

Two jobs here. The first is the usual one: prove the patch does what it claims
and stays harmless for vendors that don't need it.

The second matters more. This shim patches a *private* function in
``databricks_langchain``, so it is only correct as long as that function keeps
the shape it has today. The ``upstream contract`` tests below assert that shape
directly. If a future release renames the function, changes what it returns, or
fixes the underlying bug, those tests fail — which is the point. A silent
mismatch would mean either a dead patch (Gemini quietly breaks again) or a
double-applied one, and neither is visible from the outside.
"""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from agent_server import vendor_compat


@pytest.fixture(autouse=True)
def _clean_patch_state():
    """Every test starts unpatched and leaves the process as it found it."""
    was_applied = vendor_compat.is_applied()
    vendor_compat.reset()
    yield
    vendor_compat.reset()
    if was_applied:
        vendor_compat.apply()


def _gemini_style_message() -> AIMessage:
    """An assistant turn as Gemini actually returns it through ChatDatabricks.

    The signature value is truncated; the shape is what was observed live.
    """
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "get_downtime_minutes",
                "args": {"machine_id": "FILLER-01"},
                "id": "call_1",
                "type": "tool_call",
            }
        ],
        additional_kwargs={
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "get_downtime_minutes",
                        "arguments": '{"machine_id":"FILLER-01"}',
                    },
                    "thoughtSignature": "AY89a1/rlON5m1Il",
                }
            ]
        },
    )


def _openai_style_message() -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"name": "calculator", "args": {"expression": "2+2"}, "id": "call_9", "type": "tool_call"}
        ],
        additional_kwargs={
            "tool_calls": [
                {
                    "id": "call_9",
                    "type": "function",
                    "function": {"name": "calculator", "arguments": '{"expression":"2+2"}'},
                }
            ]
        },
    )


def _convert(message: AIMessage) -> list[dict]:
    from databricks_langchain import chat_models

    return chat_models._get_tool_calls_from_ai_message(message)


# --------------------------------------------------------------------------- #
# upstream contract — these failing means the shim needs revisiting
# --------------------------------------------------------------------------- #
def test_upstream_still_has_the_function_we_patch():
    from databricks_langchain import chat_models

    assert callable(getattr(chat_models, "_get_tool_calls_from_ai_message", None)), (
        "databricks_langchain no longer exposes _get_tool_calls_from_ai_message. "
        "The Gemini shim in agent_server/vendor_compat.py is now a no-op and "
        "Gemini-backed roles will fail on turn two — find the new seam."
    )


def test_upstream_still_drops_vendor_fields():
    """The bug this shim exists for. If upstream fixes it, delete the shim."""
    calls = _convert(_gemini_style_message())
    assert calls, "upstream returned no tool calls for a message that has one"
    assert "thoughtSignature" not in calls[0], (
        "databricks_langchain now preserves vendor tool-call fields. The shim in "
        "agent_server/vendor_compat.py is redundant — remove it and this test."
    )


def test_upstream_returns_the_four_field_shape_we_assume():
    calls = _convert(_openai_style_message())
    assert set(calls[0]) == {"id", "type", "function"}
    assert set(calls[0]["function"]) == {"name", "arguments"}


# --------------------------------------------------------------------------- #
# the shim itself
# --------------------------------------------------------------------------- #
def test_gemini_signature_survives_the_round_trip():
    assert vendor_compat.apply() is True
    calls = _convert(_gemini_style_message())
    assert calls[0]["thoughtSignature"] == "AY89a1/rlON5m1Il"


def test_other_vendors_are_byte_identical():
    """A vendor with no extra fields must see exactly what it saw before."""
    before = _convert(_openai_style_message())
    vendor_compat.apply()
    after = _convert(_openai_style_message())
    assert before == after


def test_the_rebuilt_arguments_win_over_the_raw_payload():
    """LangChain normalises arguments; the shim must not undo that.

    Only keys the rebuild did *not* produce get copied across.
    """
    vendor_compat.apply()
    msg = _gemini_style_message()
    msg.additional_kwargs["tool_calls"][0]["function"] = {"name": "hacked", "arguments": "{}"}
    calls = _convert(msg)
    assert calls[0]["function"]["name"] == "get_downtime_minutes"


def test_ids_are_matched_not_positional():
    """Two calls in one turn must each keep their own signature."""
    vendor_compat.apply()
    msg = AIMessage(
        content="",
        tool_calls=[
            {"name": "a", "args": {}, "id": "call_1", "type": "tool_call"},
            {"name": "b", "args": {}, "id": "call_2", "type": "tool_call"},
        ],
        additional_kwargs={
            "tool_calls": [
                {"id": "call_2", "type": "function", "function": {"name": "b", "arguments": "{}"}, "thoughtSignature": "SIG-2"},
                {"id": "call_1", "type": "function", "function": {"name": "a", "arguments": "{}"}, "thoughtSignature": "SIG-1"},
            ]
        },
    )
    by_id = {c["id"]: c for c in _convert(msg)}
    assert by_id["call_1"]["thoughtSignature"] == "SIG-1"
    assert by_id["call_2"]["thoughtSignature"] == "SIG-2"


def test_applying_twice_does_not_double_wrap():
    vendor_compat.apply()
    vendor_compat.apply()
    calls = _convert(_gemini_style_message())
    assert calls[0]["thoughtSignature"] == "AY89a1/rlON5m1Il"
    vendor_compat.reset()
    assert "thoughtSignature" not in _convert(_gemini_style_message())


def test_a_message_with_no_raw_payload_is_untouched():
    vendor_compat.apply()
    msg = AIMessage(
        content="",
        tool_calls=[{"name": "a", "args": {}, "id": "call_1", "type": "tool_call"}],
    )
    assert _convert(msg) == [
        {"type": "function", "id": "call_1", "function": {"name": "a", "arguments": "{}"}}
    ]


def test_kill_switch(monkeypatch):
    monkeypatch.setenv("LLM_VENDOR_COMPAT", "0")
    assert vendor_compat.apply() is False
    assert "thoughtSignature" not in _convert(_gemini_style_message())


def test_a_missing_upstream_function_is_survivable(monkeypatch):
    """An upgrade that renames the seam must warn, not crash the import."""
    from databricks_langchain import chat_models

    monkeypatch.delattr(chat_models, "_get_tool_calls_from_ai_message")
    assert vendor_compat.apply() is False


def test_the_package_import_applies_it():
    """agent_server/__init__.py is the only place this is wired up."""
    import importlib

    import agent_server

    vendor_compat.reset()
    importlib.reload(agent_server)
    assert vendor_compat.is_applied() is True
