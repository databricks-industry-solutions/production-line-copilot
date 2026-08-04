"""Keep vendor-specific fields on a tool call alive across a turn.

Running specialists on six vendors surfaced a gap in the LangChain layer, not
in the models. When an assistant message carrying tool calls is sent back to
the provider — which is every turn after the first in a ReAct loop — the
Databricks integration does not forward the message it received. It *rebuilds*
each tool call from a fixed four-field shape::

    {"type": "function", "id": ..., "function": {"name": ..., "arguments": ...}}

(``_get_tool_calls_from_ai_message`` in ``databricks_langchain.chat_models``).

That is lossless for OpenAI and Anthropic, whose tool calls carry nothing else.
It is not lossless for Google. Gemini attaches a ``thoughtSignature`` to each
function call and *requires* it back on the next turn; without it the request
is rejected::

    Function call is missing a thought_signature in functionCall parts.
    This is required for tools to work correctly.

The field is not lost on the way in — the integration stores the untouched
provider payload in ``additional_kwargs["tool_calls"]``. It is only dropped on
the way out. So the repair is to copy back whatever the rebuild left behind,
matched by tool-call id. Verified against the live workspace: without this,
``databricks-gemini-3-5-flash`` and ``-3-1-flash-lite`` fail on turn two of any
tool loop; with it, both complete multi-tool ReAct runs correctly.

This is a monkeypatch of a private function, so it is written to disappear
quietly rather than break anything:

  * If the function is missing or has been renamed, nothing is patched and a
    warning is logged — an upgrade cannot crash the app on import.
  * Applying it twice is a no-op.
  * Vendors whose tool calls carry no extra fields see byte-identical output,
    because the copy-back is empty for them.
  * ``tests/agent_server/test_vendor_compat.py`` asserts the upstream shape
    this depends on, so a release that changes or fixes it fails the suite
    loudly instead of silently altering behaviour.

Set ``LLM_VENDOR_COMPAT=0`` to skip it.

Deliberately NOT patched: the same module stringifies structured content with
``json.dumps`` (``_convert_dict_to_message``), which is why reasoning models
appear to return JSON. That one is handled by reading rather than writing — see
``agent_server/llm_output.py`` — because output parsers downstream genuinely do
expect a string, and changing the type under them is a far larger blast radius
than adding a key to a dict.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# The keys the upstream rebuild already produces. Anything else on the original
# provider payload is what we need to carry across.
_REBUILT_KEYS = frozenset({"id", "type", "function"})

_applied = False


def enabled() -> bool:
    return os.environ.get("LLM_VENDOR_COMPAT", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def apply() -> bool:
    """Install the patch. Returns True if it is now active.

    Idempotent and never raises: this runs at import time, so a failure here
    must degrade to "Gemini stays broken", not "the app won't start".
    """
    global _applied
    if _applied:
        return True
    if not enabled():
        logger.info("vendor_compat: disabled via LLM_VENDOR_COMPAT")
        return False

    try:
        from databricks_langchain import chat_models
    except Exception as exc:  # noqa: BLE001
        logger.warning("vendor_compat: databricks_langchain unavailable (%s)", exc)
        return False

    original = getattr(chat_models, "_get_tool_calls_from_ai_message", None)
    if original is None:
        logger.warning(
            "vendor_compat: _get_tool_calls_from_ai_message not found in "
            "databricks_langchain.chat_models — skipping. Gemini tool loops will "
            "fail on the second turn until this shim is updated."
        )
        return False

    def _preserve_vendor_fields(message):
        calls = original(message)
        try:
            raw_by_id = {
                raw.get("id"): raw
                for raw in (getattr(message, "additional_kwargs", {}) or {}).get("tool_calls", [])
                if isinstance(raw, dict)
            }
            if not raw_by_id:
                return calls
            for call in calls:
                raw = raw_by_id.get(call.get("id"))
                if not raw:
                    continue
                for key, value in raw.items():
                    # Never overwrite what the rebuild produced — LangChain may
                    # have normalised the arguments, and its version wins.
                    if key not in _REBUILT_KEYS and key not in call:
                        call[key] = value
        except Exception:  # noqa: BLE001
            logger.exception("vendor_compat: could not preserve tool-call fields")
        return calls

    _preserve_vendor_fields.__wrapped__ = original  # ty:ignore[unresolved-attribute]
    chat_models._get_tool_calls_from_ai_message = _preserve_vendor_fields
    _applied = True
    logger.info("vendor_compat: tool-call field preservation active")
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

        patched = getattr(chat_models, "_get_tool_calls_from_ai_message", None)
        original = getattr(patched, "__wrapped__", None)
        if original is not None:
            chat_models._get_tool_calls_from_ai_message = original
    except Exception:  # noqa: BLE001
        logger.exception("vendor_compat: reset failed")
    _applied = False
