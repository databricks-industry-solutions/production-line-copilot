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

One shape is not pure JSON and has to be handled anyway. Streaming accumulates
``AIMessageChunk`` content by string concatenation, so when the first chunk
carries the serialised block list and the ones after it carry prose, the result
is a complete JSON array with the answer *glued to the end of it*::

    [{"type": "reasoning", "summary": [...]}]## Root Cause\\n- **BFEV0024**: ...

``json.loads`` rejects that with "Extra data" and every reasoning block in it
went straight to the user — which is what Anthropic answers looked like in the
chat window, an opaque base64 signature followed by the real answer. Parsing
with ``raw_decode`` takes the array off the front and keeps the remainder as
text, which is exactly what both halves are.
"""
from __future__ import annotations

import json
import re
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
    return _collapse_nested_bold(_content_text(getattr(message, "content", message)))


# Two formatting rules meet badly: bullets lead with a bolded key value, and
# every metric is bolded inline. Models apply the second rule inside the first
# and emit `**OEE **74.2%****`. Markdown has no nested bold, so the reader gets
# the literal asterisks. The prompt now forbids it; this is the safety net,
# because a formatting instruction is a request and this is the chat window.
_NESTED_BOLD = re.compile(r"\*\*([^*\n]+?)\*\*([^*\n]+?)\*\*\*\*")


def _collapse_nested_bold(text: str) -> str:
    """Rewrite ``**A **B****`` as ``**A B**``, leaving well-formed bold alone.

    Applied repeatedly so a bullet carrying more than one nested span is fully
    cleaned. The pattern cannot match across ``*`` or a newline, so ordinary
    adjacent bold — ``**36.1** min CIP, **28.9** min`` — is never touched.
    """
    if "**" not in text:
        return text
    for _ in range(8):
        collapsed = _NESTED_BOLD.sub(r"**\1\2**", text)
        if collapsed == text:
            return collapsed
        text = collapsed
    return text


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        parsed = _parse_serialised_blocks(content)
        # Not a serialised block list — an ordinary answer, return it untouched.
        if parsed is None:
            return content
        blocks, trailing = parsed
        return "\n".join(part for part in (_blocks_text(blocks), trailing.strip()) if part)
    if isinstance(content, dict):
        return _block_text(content)
    if isinstance(content, (list, tuple)):
        return _blocks_text(content)
    return str(content)


def _parse_serialised_blocks(text: str) -> tuple[list, str] | None:
    """Return ``(blocks, trailing_text)``, or None if ``text`` isn't a block list.

    ``raw_decode`` rather than ``loads`` because the streamed form is a JSON
    array with prose concatenated onto the end of it (see the module docstring);
    anything after the array is returned as the second element. For a response
    that is purely a block list the remainder is empty and this behaves exactly
    as before.

    The guard stays deliberately narrow: a JSON array whose every element is a
    mapping and where at least one carries a ``type`` key. Prose that merely
    happens to open with ``[{`` fails to parse or fails the shape test and is
    returned to the caller unchanged.
    """
    stripped = text.lstrip()
    if not stripped.startswith("[{"):
        return None
    try:
        parsed, end = json.JSONDecoder().raw_decode(stripped)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, list) or not parsed:
        return None
    if not all(isinstance(block, dict) for block in parsed):
        return None
    if not any("type" in block for block in parsed):
        return None
    return parsed, stripped[end:]


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
