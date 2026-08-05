"""A span that records the question and not the answer is not evidence.

Both specialist spans called ``set_inputs`` and never ``set_outputs``, so every
``specialist.*`` trace in dev and prod rendered an empty "Model output" in the
MLflow UI. The query was stored, the answer was not. Nothing downstream — no
judge, no eval set, no manual review of a bad reply — can work from that, and
the gap is invisible unless you open a trace and look.

The parsing tests below pin the behaviour; the AST test at the bottom is the
one that stops the defect coming back, because it fails for a span added later
that forgets its outputs.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent_server.agent import (
    NO_ANSWER,
    _confidence_level,
    _final_answer_text,
    _record_answer,
)

AGENT_SRC = pathlib.Path(__file__).resolve().parents[2] / "agent_server" / "agent.py"


class TestConfidenceLevel:
    """The rating every answer template is required to end with."""

    @pytest.mark.parametrize("level", ["HIGH", "MEDIUM", "LOW"])
    def test_it_reads_the_rating_off_a_well_formed_answer(self, level: str) -> None:
        answer = f"## Situation\n- Line 3 down\n\n## Confidence\n**{level}** — sensor data is complete"
        assert _confidence_level(answer) == level

    def test_a_missing_section_is_not_the_same_as_low(self) -> None:
        """Absent means the model skipped a required section.

        That is a formatting failure to investigate, not a hedge to act on, so
        it must not be silently bucketed with the genuinely uncertain answers.
        """
        assert _confidence_level("## Situation\n- Line 3 down") is None

    def test_it_tolerates_formatting_the_model_got_slightly_wrong(self) -> None:
        # Unbolded and lowercased still carries the rating; this parses model
        # output, so being strict here just loses data.
        assert _confidence_level("## Confidence\nlow — limited history") == "LOW"

    def test_the_word_alone_is_not_a_rating(self) -> None:
        """Only the heading counts.

        "high" shows up constantly in this domain — high scrap, high OEE — and
        matching it loose would tag confident-looking answers at random.
        """
        assert _confidence_level("Scrap is high on line 3 and downtime is low") is None

    def test_empty_input_is_handled(self) -> None:
        assert _confidence_level("") is None


class TestFinalAnswerText:
    def test_it_takes_the_last_message_carrying_content(self) -> None:
        result = {"messages": [HumanMessage(content="q"), AIMessage(content="first"),
                               AIMessage(content="final answer")]}
        assert _final_answer_text(result) == "final answer"

    def test_a_reasoning_only_message_is_not_an_answer(self) -> None:
        """Reasoning blocks render as empty text and must not blank the reply.

        Two of the ten roster models emit them, so taking the newest message
        unconditionally would return "" for those vendors only.
        """
        result = {"messages": [AIMessage(content="the real answer"),
                               AIMessage(content=[{"type": "reasoning", "text": "hmm"}])]}
        assert _final_answer_text(result) == "the real answer"

    def test_a_run_that_produced_nothing_says_so(self) -> None:
        assert _final_answer_text({"messages": [HumanMessage(content="q")]}) == NO_ANSWER


class _FakeSpan:
    def __init__(self) -> None:
        self.outputs: dict | None = None

    def set_outputs(self, value: dict) -> None:
        self.outputs = value


class TestRecordAnswer:
    def test_the_answer_and_its_rating_both_land_on_the_span(self) -> None:
        span = _FakeSpan()
        _record_answer(span, "## Confidence\n**MEDIUM** — partial sensor coverage")
        assert span.outputs["response"].startswith("## Confidence")
        assert span.outputs["confidence"] == "MEDIUM"

    def test_an_unrated_answer_is_still_recorded(self) -> None:
        """The response body matters more than the tag.

        Dropping the output because the rating could not be parsed would
        recreate the original defect for exactly the malformed answers most
        worth reading.
        """
        span = _FakeSpan()
        _record_answer(span, "plain text, no headings")
        assert span.outputs == {"response": "plain text, no headings", "confidence": None}

    def test_tagging_failure_does_not_reach_the_caller(self, monkeypatch) -> None:
        """No active trace, or a tracing backend that is down, must not break a reply."""
        import agent_server.agent as agent_mod

        def boom(**_kwargs):
            raise RuntimeError("no active trace")

        monkeypatch.setattr(agent_mod.mlflow, "update_current_trace", boom)
        span = _FakeSpan()
        _record_answer(span, "## Confidence\n**LOW** — stale data")
        assert span.outputs["confidence"] == "LOW"


def _span_blocks() -> list[ast.With]:
    """Every ``with mlflow.start_span(...)`` block in agent.py."""
    tree = ast.parse(AGENT_SRC.read_text())
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.With)
        and any("mlflow.start_span" in ast.unparse(item.context_expr) for item in node.items)
    ]


def test_the_scan_finds_the_span_blocks_at_all() -> None:
    """A guard whose matcher stops matching guards nothing."""
    assert len(_span_blocks()) >= 2


@pytest.mark.parametrize("block", _span_blocks(), ids=lambda b: f"line_{b.lineno}")
def test_every_manual_span_records_its_output(block: ast.With) -> None:
    """`set_inputs` without `set_outputs` is the original bug.

    The `@mlflow.trace` decorator captures a return value on its own, so only
    hand-rolled spans can get this wrong — and both of ours did.
    """
    body = ast.unparse(block)
    assert "set_inputs" in body, "a span worth opening records what went in"
    assert "set_outputs" in body or "_record_answer" in body, (
        f"the mlflow.start_span block at line {block.lineno} sets inputs but never outputs. "
        "Its trace will show the question with an empty answer, which is what made every "
        "specialist trace useless for judging answer quality."
    )


def test_the_synthesis_pass_is_traced() -> None:
    """It produces the text the manager actually reads.

    Untraced, a trace could show the specialist's draft but never the answer
    that reached the screen — including the `## Confidence` line, which the
    synthesis template is what mandates.
    """
    tree = ast.parse(AGENT_SRC.read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_synthesize_response")
    assert any("mlflow.trace" in ast.unparse(d) for d in fn.decorator_list)
