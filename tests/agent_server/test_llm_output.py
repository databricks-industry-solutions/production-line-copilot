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


class TestTheStreamedAnthropicShape:
    """A complete JSON array with the answer concatenated onto the end of it.

    Streaming accumulates ``AIMessageChunk`` content by string concatenation. On
    Anthropic the first chunk carries the reasoning block already serialised, so
    every prose chunk after it is appended *outside* the closing bracket. The
    result parses as "JSON followed by extra data", which ``json.loads`` refuses
    outright — so the guard returned None and the whole thing, base64 signature
    and all, was rendered into the chat window as the answer.

    Captured from a live prod stream: a 7,420-character payload of which 6,643
    were an opaque reasoning signature.
    """

    def _payload(self, answer: str) -> str:
        blocks = json.dumps(
            [{
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": ""}],
                "signature": "EqsmCnIIEBABGAIqQES6iRyL54PAPVIk0ze8qv1wQtXhXECS2AKg",
            }]
        )
        return blocks + answer

    def test_the_trailing_answer_is_recovered(self):
        answer = "## Root Cause\n- **BFEV0024 (FILLER)**: ongoing `minor_stop`."
        assert message_text(_Msg(self._payload(answer))) == answer

    def test_the_reasoning_signature_does_not_reach_the_user(self):
        out = message_text(_Msg(self._payload("## Root Cause\n- Starved infeed.")))
        assert "signature" not in out
        assert "EqsmCnIIEBABGAIqQES6iRyL54PAPVIk0ze8qv1wQtXhXECS2AKg" not in out
        assert not out.lstrip().startswith("[{")

    def test_a_text_block_and_a_trailing_tail_are_both_kept(self):
        """Where the array itself carries prose, neither half may be dropped."""
        content = json.dumps(
            [
                {"type": "reasoning", "summary": []},
                {"type": "text", "text": "## Root Cause"},
            ]
        ) + "\n- Starved infeed."
        assert message_text(_Msg(content)) == "## Root Cause\n- Starved infeed."

    def test_a_pure_block_list_is_unaffected(self):
        """raw_decode must not change the behaviour it already had."""
        content = json.dumps(
            [
                {"type": "reasoning", "summary": [{"type": "summary_text", "text": "x"}]},
                {"type": "text", "text": "FILLER lost 13,104 cases."},
            ]
        )
        assert message_text(_Msg(content)) == "FILLER lost 13,104 cases."

    def test_prose_after_a_non_block_array_is_still_left_alone(self):
        # The array parses, but carries no `type` key, so it is not a block list
        # and the whole string must survive untouched.
        prose = '[{"machine": "FILLER"}] is the row the query returned.'
        assert message_text(_Msg(prose)) == prose


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


class TestTheNestedBoldTheChatWindowShowed:
    """Two prompt rules collide: bullets lead with a bolded key value, and every
    metric is bolded inline. Models apply the second inside the first and emit
    ``**OEE **74.2%****``. Markdown has no nested bold, so the line manager sees
    the asterisks. Captured verbatim from a production answer.
    """

    def test_the_lead_in_collapses_to_one_bold_span(self):
        assert message_text(_Msg("- **OEE **74.2%****: below target.")) == (
            "- **OEE 74.2%**: below target."
        )

    def test_no_stray_asterisk_runs_survive(self):
        out = message_text(_Msg("- **Open stop on **FILLER****: event **BFEV0024** open."))
        assert "****" not in out
        assert out == "- **Open stop on FILLER**: event **BFEV0024** open."

    def test_adjacent_bold_on_one_line_is_not_merged(self):
        # The repair must not reach across two legitimately separate spans.
        line = "- **Availability**: **36.1** min CIP, **28.9** min hold."
        assert message_text(_Msg(line)) == line

    def test_well_formed_bold_is_untouched(self):
        line = "- **Performance loss**: lowest component at **85.6%**."
        assert message_text(_Msg(line)) == line

    def test_more_than_one_nested_span_per_answer(self):
        out = message_text(_Msg("- **A **1****: x\n- **B **2****: y"))
        assert out == "- **A 1**: x\n- **B 2**: y"

    def test_it_is_idempotent(self):
        once = message_text(_Msg("- **Cases **5,855****: behind plan."))
        assert message_text(_Msg(once)) == once

    def test_italics_and_plain_text_are_left_alone(self):
        assert message_text(_Msg("*emphasis* and plain")) == "*emphasis* and plain"

    def test_it_applies_to_the_streamed_anthropic_shape(self):
        # The block-list-plus-prose form has to be unwrapped before the repair
        # can see the markdown at all.
        raw = '[{"type": "thinking", "thinking": "private"}]- **OEE **74.2%****: low.'
        assert message_text(_Msg(raw)) == "- **OEE 74.2%**: low."
