"""No answer path caps its own completion.

Every specialist carried a literal ``max_tokens=2048``. Against a
non-reasoning model that is generous — the prompt caps an answer at ~250
words, roughly 350 tokens. Against a reasoning model it is not: gpt-5-mini,
the default for ``MODEL_SPECIALIST``, spends most of the completion budget on
reasoning tokens nobody sees, and they count.

The answer then stops mid-sentence, and it stops from the bottom. A
DEPALLETIZER diagnosis ended at "1. **Check head safety interlock**" with no
Estimated Recovery and no Confidence line — it looked complete enough to read
and was missing the parts anyone acts on.

Raising the number only moves the cliff. The endpoint applies no punitive
default of its own (an uncapped call returns ``finish_reason='stop'`` after
~2.8k tokens), and length is already bounded three ways that fail loudly
rather than silently: the prompt's word cap, ``SPECIALIST_RECURSION_LIMIT`` on
call count, and ``SPECIALIST_TIMEOUT_SECONDS`` on the wait.

The router keeps its cap. It returns a specialist name, so truncation there
either still matches or falls through to the default.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPECIALIST_DIR = ROOT / "agent_server" / "specialists"
SPECIALIST_FILES = sorted(p for p in SPECIALIST_DIR.glob("*.py") if p.name != "__init__.py")
AGENT = ROOT / "agent_server" / "agent.py"


def _chat_calls(path: Path) -> list[ast.Call]:
    tree = ast.parse(path.read_text())
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "ChatDatabricks"
    ]


def _max_tokens(call: ast.Call) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == "max_tokens":
            return kw.value
    return None


def _role(call: ast.Call) -> str:
    """The role string or name passed to resolve() in this call."""
    for kw in call.keywords:
        if kw.arg != "endpoint" or not isinstance(kw.value, ast.Call):
            continue
        for arg in kw.value.args:
            if isinstance(arg, ast.Constant):
                return str(arg.value)
            if isinstance(arg, ast.Name):
                return arg.id
    return "?"


class TestSpecialists:
    @pytest.mark.parametrize("path", SPECIALIST_FILES, ids=lambda p: p.stem)
    def test_a_specialist_builds_a_model(self, path):
        assert _chat_calls(path), f"{path.name} constructs no ChatDatabricks"

    @pytest.mark.parametrize("path", SPECIALIST_FILES, ids=lambda p: p.stem)
    def test_a_specialist_does_not_cap_its_completion(self, path):
        for call in _chat_calls(path):
            arg = _max_tokens(call)
            assert arg is None, (
                f"{path.name} caps max_tokens; a reasoning model spends that budget "
                "before it finishes the answer, and the answer truncates from the bottom"
            )

    def test_no_specialist_differs_from_the_others(self):
        """Nothing here would justify a per-specialist number anyway.

        All nine render the same five-section, 250-word contract. The variable
        is how much the model reasons before writing it, and that is a property
        of the model, which the roster already assigns per role. The eight
        literal 2048s were copied, not chosen, and the strategic advisor's 3000
        rested on a docstring its own prompt contradicts.
        """
        budgets = {p.stem: [_max_tokens(c) for c in _chat_calls(p)] for p in SPECIALIST_FILES}
        assert all(all(b is None for b in v) for v in budgets.values()), budgets


class TestTheAgentPaths:
    def test_the_router_still_caps_itself(self):
        """The one place a cap is right, so removing them all would be wrong."""
        router = [c for c in _chat_calls(AGENT) if "ROUTER" in _role(c)]
        assert router, "no router model found"
        for call in router:
            arg = _max_tokens(call)
            assert isinstance(arg, ast.Constant) and arg.value <= 100

    def test_the_answer_paths_do_not_cap_themselves(self):
        # The general no-tool path and the synthesis pass both render the same
        # 250-word contract as the specialists and truncated the same way.
        general = [c for c in _chat_calls(AGENT) if "GENERAL" in _role(c)]
        assert general, "no general/synthesis model found"
        for call in general:
            assert _max_tokens(call) is None

    def test_the_bounds_that_replaced_the_cap_are_still_there(self):
        src = AGENT.read_text()
        assert "SPECIALIST_RECURSION_LIMIT = int(" in src
        assert "SPECIALIST_TIMEOUT_SECONDS = int(" in src

    def test_the_word_cap_still_bounds_the_answer(self):
        from agent_server.prompts._shared import RESPONSE_STRUCTURE

        assert "250 words" in RESPONSE_STRUCTURE
