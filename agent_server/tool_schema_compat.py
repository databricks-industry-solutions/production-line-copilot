"""Drop ``additionalProperties: true`` from tool schemas before they are bound.

Two of our tools take an open dictionary because the payload genuinely varies:
``calculator(operation, params)`` reads a different set of keys per operation,
and ``estimate_rul`` takes a list of sensor readings. Pydantic renders a bare
``dict`` as::

    {"type": "object", "additionalProperties": true}

Most serving endpoints ignore that key. Two of the ones this app runs on do not,
and reject the whole request before the model ever sees it::

    Invalid JSON schema - the "additionalProperties" keyword must be
    False or not specified

Measured against every endpoint in the roster: ``databricks-qwen35-122b-a10b``
(Maintenance Predictor) and ``databricks-llama-4-maverick`` (Supply Chain
Advisor) reject it; Anthropic, OpenAI, OpenAI open-weights and Z.ai accept it.
Since ``calculator`` is bound by almost every specialist, those two roles failed
on their first model call and fell through to the general handler — a whole
vendor dark, with nothing in the UI to say why.

The repair is a no-op in JSON Schema terms. An absent ``additionalProperties``
already means "additional properties allowed", so removing the explicit ``true``
says exactly what the schema said before. Only the literal ``true`` is removed:
``false`` is a real constraint, and a schema-valued ``additionalProperties``
describes the permitted shape. Both are left alone.

Applied by patching ``convert_to_openai_tool`` in the namespace of
``databricks_langchain.chat_models``, which is where ``bind_tools`` looks it up.
That covers every specialist without each one having to remember, and it covers
tools added later — the failure mode this replaces was silent.

Set ``LLM_TOOL_SCHEMA_COMPAT=0`` to skip it.
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_applied = False


def enabled() -> bool:
    return os.environ.get("LLM_TOOL_SCHEMA_COMPAT", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def strip_open_additional_properties(node: Any) -> Any:
    """Return ``node`` with every ``additionalProperties: true`` removed.

    Pure: the input is never mutated, because the caller may be handing us a
    schema dict it intends to keep using.
    """
    if isinstance(node, dict):
        return {
            key: strip_open_additional_properties(value)
            for key, value in node.items()
            if not (key == "additionalProperties" and value is True)
        }
    if isinstance(node, list):
        return [strip_open_additional_properties(item) for item in node]
    return node


def apply() -> bool:
    """Install the patch. Returns True if it is now active.

    Idempotent and never raises: this runs at import time, so a failure here
    must degrade to "two vendors stay broken", not "the app won't start".
    """
    global _applied
    if _applied:
        return True
    if not enabled():
        logger.info("tool_schema_compat: disabled via LLM_TOOL_SCHEMA_COMPAT")
        return False

    try:
        from databricks_langchain import chat_models
    except Exception as exc:  # noqa: BLE001
        logger.warning("tool_schema_compat: databricks_langchain unavailable (%s)", exc)
        return False

    original = getattr(chat_models, "convert_to_openai_tool", None)
    if original is None:
        logger.warning(
            "tool_schema_compat: convert_to_openai_tool not found in "
            "databricks_langchain.chat_models — skipping. Roles on Qwen and "
            "Llama will fail on their first model call until this is updated."
        )
        return False

    def _convert_without_open_dicts(tool, *args, **kwargs):
        return strip_open_additional_properties(original(tool, *args, **kwargs))

    _convert_without_open_dicts.__wrapped__ = original  # ty:ignore[unresolved-attribute]
    chat_models.convert_to_openai_tool = _convert_without_open_dicts
    _applied = True
    logger.info("tool_schema_compat: open-dict schema stripping active")
    return True


def is_applied() -> bool:
    return _applied


def reset() -> None:
    """Undo the patch. For tests."""
    global _applied
    if not _applied:
        return
    try:
        from databricks_langchain import chat_models

        patched = getattr(chat_models, "convert_to_openai_tool", None)
        original = getattr(patched, "__wrapped__", None)
        if original is not None:
            chat_models.convert_to_openai_tool = original
    except Exception:  # noqa: BLE001
        logger.exception("tool_schema_compat: reset failed")
    _applied = False
