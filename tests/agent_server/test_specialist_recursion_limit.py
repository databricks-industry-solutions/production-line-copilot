"""The ReAct step budget, and why it is one named number.

The streaming and non-streaming paths build the same agent and must give it the
same budget, but they are 90 lines apart and each used to carry its own literal
``10``. That is how they drift: raise one, and the other keeps failing on
exactly the questions the first one just fixed.

The value also has to mean something. LangGraph counts super-steps, so a ReAct
round costs two and the answer costs one more — a budget of ``2n + 1`` buys
``n`` rounds of tool use. At 10 the Quality Specialist had four, which was not
enough to query its governed domain, query live SQL, and still recover from a
query that named a column that doesn't exist; it failed roughly two runs in
three against the deployed app.
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path

from agent_server import agent

AGENT_SOURCE = Path(agent.__file__).read_text()


def _rounds(limit: int) -> int:
    """Tool-use rounds a step budget buys."""
    return (limit - 1) // 2


def test_both_paths_share_one_named_budget():
    """Neither call site may carry its own literal."""
    tree = ast.parse(AGENT_SOURCE)
    literals = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if (
                isinstance(key, ast.Constant)
                and key.value == "recursion_limit"
                and not (isinstance(value, ast.Name) and value.id == "SPECIALIST_RECURSION_LIMIT")
            ):
                literals.append(ast.unparse(value))
    assert not literals, (
        f"recursion_limit is set to {literals} somewhere instead of "
        "SPECIALIST_RECURSION_LIMIT. The streaming and non-streaming paths must "
        "share one budget or they will drift."
    )


def test_both_paths_actually_set_it():
    """A shared constant is worthless if a path forgets to pass it."""
    assert AGENT_SOURCE.count('"recursion_limit": SPECIALIST_RECURSION_LIMIT') == 2, (
        "expected exactly two call sites — agent.stream and agent.invoke — to "
        "set the recursion limit"
    )


def test_the_budget_leaves_room_to_recover_from_a_bad_query():
    """Three productive rounds plus a retry, with headroom."""
    assert _rounds(agent.SPECIALIST_RECURSION_LIMIT) >= 6, (
        f"a budget of {agent.SPECIALIST_RECURSION_LIMIT} buys only "
        f"{_rounds(agent.SPECIALIST_RECURSION_LIMIT)} tool rounds. The Quality "
        "Specialist needs its governed domain, live SQL, and a retry, and was "
        "failing two runs in three at four rounds."
    )


def test_wall_clock_is_the_real_ceiling():
    """The step budget is generous because something else bounds a runaway loop."""
    assert agent.SPECIALIST_TIMEOUT_SECONDS > 0, (
        "SPECIALIST_RECURSION_LIMIT is deliberately loose on the assumption that "
        "a wall-clock timeout stops a looping specialist. If that guard is gone, "
        "the step budget has to become the guard again."
    )


def test_the_budget_is_env_overridable(monkeypatch):
    monkeypatch.setenv("SPECIALIST_RECURSION_LIMIT", "30")
    reloaded = importlib.reload(agent)
    try:
        assert reloaded.SPECIALIST_RECURSION_LIMIT == 30
    finally:
        monkeypatch.delenv("SPECIALIST_RECURSION_LIMIT")
        importlib.reload(agent)
