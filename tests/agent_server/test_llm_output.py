"""Content normalisation across vendors.

Running specialists on six vendors' models means ``AIMessage.content`` is no
longer reliably a string of prose. The cases below are the shapes actually
observed against the Databricks serving endpoints this app ships against —
particularly GPT-OSS and Qwen, which return a JSON-serialised block list where
the model's private reasoning sits in front of the real answer.
"""
import json

import pytest

from agent_server.llm_output import message_text


class _Msg:
    """Stand-in for a LangChain message — only ``.content`` is read."""

    def __init__(self, content):
        self.content = content


class TestPlainStrings:
    def test_ordinary_prose_passes_through_untouched(self):
        assert message_text(_Msg("The filler stopped at 14:02.")) == "The filler stopped at 14:02."

    def test_bare_content_value_is_accepted(self):
        # Callers pass either a message or a raw content value.
        assert message_text("downtime_analyst") == "downtime_analyst"

    def test_none_becomes_empty_string(self):
        assert message_text(None) == ""
        assert message_text(_Msg(None)) == ""

    def test_prose_that_merely_starts_like_json_is_not_mangled(self):
        # The block-list guard must not eat legitimate answers. Markdown lists
        # and JSON examples in an answer are both realistic here.
        prose = '[{"machine": "FILLER"}] is the row the query returned.'
        assert message_text(_Msg(prose)) == prose


class TestSerialisedBlockLists:
    """The GPT-OSS / Qwen shape: content is a *string* holding a block array."""

    def test_reasoning_is_dropped_and_the_answer_survives(self):
        content = json.dumps(
            [
                {"type": "reasoning", "summary": [{"type": "summary_text", "text": "42*312=13104, so..."}]},
                {"type": "text", "text": "FILLER lost 13,104 cases."},
            ]
        )
        # Without this the user reads the model's scratchpad wrapped in JSON.
        assert message_text(_Msg(content)) == "FILLER lost 13,104 cases."

    def test_reasoning_only_yields_empty_so_callers_can_fall_back(self):
        content = json.dumps(
            [{"type": "reasoning", "summary": [{"type": "summary_text", "text": "thinking..."}]}]
        )
        # Empty is the honest answer: there is no prose to show. Returning the
        # raw blocks instead would leak reasoning into the chat.
        assert message_text(_Msg(content)) == ""

    def test_multiple_text_blocks_are_joined(self):
        content = json.dumps(
            [{"type": "text", "text": "Part one. "}, {"type": "text", "text": "Part two."}]
        )
        assert message_text(_Msg(content)) == "Part one. Part two."

    @pytest.mark.parametrize(
        "content",
        [
            "[{not json",
            json.dumps([1, 2, 3]),
            json.dumps([{"no": "type key"}]),
            json.dumps([]),
        ],
    )
    def test_near_misses_are_returned_verbatim(self, content):
        # Anything that isn't unambiguously a block list is left alone rather
        # than risking silent mutilation of a real answer.
        assert message_text(_Msg(content)) == content


class TestNativeBlockLists:
    """Some integrations hand back a real list rather than a serialised one."""

    def test_list_of_blocks_is_flattened(self):
        content = [
            {"type": "thinking", "thinking": "hidden"},
            {"type": "text", "text": "Visible answer."},
        ]
        assert message_text(_Msg(content)) == "Visible answer."

    def test_nested_content_blocks_are_followed(self):
        content = [{"type": "tool_result", "content": [{"type": "text", "text": "nested"}]}]
        assert message_text(_Msg(content)) == "nested"

    def test_plain_strings_inside_a_list_are_kept(self):
        assert message_text(_Msg(["one ", "two"])) == "one two"


class TestTheRouterCrashThisPrevents:
    def test_block_content_no_longer_raises_on_strip(self):
        # _classify_intent does message_text(response).strip().lower(); when it
        # called .strip() on a list it raised, was swallowed by the bare except,
        # and every question silently routed to "general".
        content = [{"type": "text", "text": " Downtime Analyst "}]
        assert message_text(_Msg(content)).strip().lower().replace(" ", "_") == "downtime_analyst"
