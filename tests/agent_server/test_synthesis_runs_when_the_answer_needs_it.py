"""The second pass should run exactly when the reader would notice it missing.

`_synthesize_response` is what turns a specialist's prose into the `## Root
Cause` / `## Impact` headings ChatPanel folds into cards. Skipping it is a cost
decision, so the gate has to be about whether the answer is already in that
shape — not about how long it happens to be.

Two heuristics used to stand in for that and both failed in the same direction,
skipping the pass on answers that needed it:

* anything over 800 characters was assumed "likely already in the right shape",
  when a long ReAct answer with no headings is precisely the one worth folding;
* any answer containing the words "timed out" anywhere was treated as an
  apology, so a real downtime analysis of a PLC that timed out skipped its own
  formatting.
"""

from __future__ import annotations

import re
from pathlib import Path

from agent_server.agent import _UI_SECTIONS, _should_skip_synthesis

CHAT_PANEL = Path(__file__).resolve().parents[2] / "client/src/components/ChatPanel.tsx"


def test_the_section_list_matches_the_one_the_ui_parses():
    """The gate asks "can the UI already fold this?", so it needs the UI's list.

    Two copies of the same list in two languages is the drift this file's own
    subject matter is about: add a section to ChatPanel and forget this one, and
    answers using it silently stop being recognised as already-formatted.
    """
    block = re.search(
        r"const ALL_KNOWN_SECTIONS = \[(.*?)\];", CHAT_PANEL.read_text(), re.S
    )
    assert block, "ALL_KNOWN_SECTIONS is no longer declared in ChatPanel.tsx"
    ui_sections = tuple(re.findall(r"'([^']+)'", block.group(1)))
    assert ui_sections == _UI_SECTIONS, (
        "agent._UI_SECTIONS and ChatPanel's ALL_KNOWN_SECTIONS disagree:\n"
        f"  python: {_UI_SECTIONS}\n  ui:     {ui_sections}"
    )


class TestAnswersThatStillNeedFormatting:
    def test_a_long_answer_with_no_headings_is_not_assumed_to_be_formatted(self):
        wall_of_text = "The pasteurizer lost temperature control. " * 40
        assert len(wall_of_text) > 800
        assert not _should_skip_synthesis("downtime_analyst", wall_of_text)

    def test_an_answer_that_merely_mentions_a_timeout_is_still_formatted(self):
        # The words appear because they describe the plant, not the agent.
        answer = (
            "The PLC on the filler timed out at 14:32 and the line stopped for "
            "eleven minutes. Throughput for the shift is down by 4,200 cases."
        )
        assert not _should_skip_synthesis("downtime_analyst", answer)

    def test_an_answer_reporting_an_equipment_error_is_still_formatted(self):
        answer = (
            "The labeller encountered an error code E-47 twice during the shift. "
            "Both clears were manual and each cost about six minutes."
        )
        assert not _should_skip_synthesis("downtime_analyst", answer)

    def test_a_single_stray_heading_is_not_the_template(self):
        assert not _should_skip_synthesis("oee_coach", "## Impact\nOEE fell to 61%.")


class TestAnswersThatDoNotNeedIt:
    def test_an_answer_already_in_the_template_is_left_alone(self):
        answer = (
            "## Root Cause\nSeal wear on filler 2.\n\n"
            "## Impact\n4,200 cases.\n\n"
            "## Confidence\n**HIGH** — three corroborating signals."
        )
        assert _should_skip_synthesis("downtime_analyst", answer)

    def test_the_general_path_is_already_a_direct_chat_call(self):
        assert _should_skip_synthesis("general", "Anything at all.")

    def test_the_sync_timeout_sentinel_is_not_reformatted(self):
        # Built by _run_specialist's FuturesTimeout handler.
        sentinel = (
            "The downtime analyst analysis timed out. This usually means the "
            "query required too many tool calls. Please try a more specific question."
        )
        assert _should_skip_synthesis("downtime_analyst", sentinel)

    def test_the_streaming_timeout_sentinel_is_not_reformatted(self):
        # Worded differently from the sync one; both have to be caught.
        sentinel = (
            "The downtime analyst analysis timed out before it reached an answer. "
            "Ask about one machine or one time window to narrow it."
        )
        assert _should_skip_synthesis("downtime_analyst", sentinel)

    def test_the_error_sentinel_is_not_reformatted(self):
        sentinel = (
            "The downtime analyst encountered an error: TimeoutError. "
            "No analysis was produced. Please try rephrasing your question."
        )
        assert _should_skip_synthesis("downtime_analyst", sentinel)

    def test_an_enormous_unstructured_answer_is_shown_whole(self):
        # Past the ceiling the pass truncates rather than restructures.
        assert _should_skip_synthesis("downtime_analyst", "word " * 2000)
