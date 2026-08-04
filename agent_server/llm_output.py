"""Flatten an LLM message's content to plain text, whatever shape the vendor sent.

Running specialists on models from six vendors means the app can no longer
assume ``AIMessage.content`` is a string of prose. Reasoning models — GPT-OSS
and some Qwen builds among the endpoints we ship against — return a *list of
typed blocks* instead::

    [{"type": "reasoning", "summary": [...]}, {"type": "text", "text": "..."}]

and ``ChatDatabricks`` hands that to us JSON-serialised into the ``content``
string, so the usual ``isinstance(content, str)`` guard does not catch it. Left
alone it reaches the chat window verbatim: the user sees the model's private
scratchpad wrapped in JSON instead of an answer, and the intent router — which
called ``.strip()`` on it — routed every question to "general".

So every read of message content goes through :func:`message_text`. Models that
return a plain string are unaffected; the parsing below only engages for
content that is unambiguously a block list.
"""
from __future__ import annotations

import json
from typing import Any

# Blocks holding the model's private deliberation. Vendors disagree on the
# name, and none of it belongs in front of a line manager.
_HIDDEN_BLOCK_TYPES = frozenset({"reasoning", "thinking", "redacted_thinking"})


def message_text(message: Any) -> str:
    """Return the human-readable text of an LLM message (or raw content value).

    Accepts a LangChain message, a bare content value, or ``None``. Reasoning
    blocks are dropped. Returns ``""`` when there is no displayable text, which
    callers must treat as "no answer" rather than passing it to the user — an
    empty string is the honest result when a model emitted nothing but private
    reasoning, and it lets the existing no-response fallbacks fire.
    """
    return _content_text(getattr(message, "content", message))


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        blocks = _parse_serialised_blocks(content)
        # Not a serialised block list — an ordinary answer, return it untouched.
        return content if blocks is None else _blocks_text(blocks)
    if isinstance(content, dict):
        return _block_text(content)
    if isinstance(content, (list, tuple)):
        return _blocks_text(content)
    return str(content)


def _parse_serialised_blocks(text: str) -> list | None:
    """Return the block list encoded in ``text``, or None if it isn't one.

    The guard is deliberately narrow: a JSON array whose every element is a
    mapping and where at least one carries a ``type`` key. Prose that merely
    happens to open with ``[{`` fails to parse or fails the shape test and is
    returned to the caller unchanged.
    """
    stripped = text.lstrip()
    if not stripped.startswith("[{"):
        return None
    try:
        parsed = json.loads(stripped)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, list) or not parsed:
        return None
    if not all(isinstance(block, dict) for block in parsed):
        return None
    if not any("type" in block for block in parsed):
        return None
    return parsed


def _blocks_text(blocks: Any) -> str:
    parts = [_block_text(block) for block in blocks]
    return "".join(part for part in parts if part).strip()


def _block_text(block: Any) -> str:
    if isinstance(block, str):
        return block
    if not isinstance(block, dict):
        return str(block)
    if block.get("type") in _HIDDEN_BLOCK_TYPES:
        return ""
    text = block.get("text")
    if isinstance(text, str):
        return text
    # Anthropic-style nesting: a block whose payload is itself a block list.
    nested = block.get("content")
    if isinstance(nested, (list, tuple)):
        return _blocks_text(nested)
    return ""
