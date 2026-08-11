"""A specialist that runs out of time should not throw away what it wrote.

The downtime analyst spends about 34s in tools, then roughly 40s composing, and
only then starts emitting prose. So the first token lands somewhere around 79s
and a cut lands partway through an answer the reader can already see on screen.
The old handler replaced all of it with "analysis timed out", which is the one
outcome worse than either finishing or failing early: the reader waited the full
budget and then watched the answer disappear.

These tests drive the streaming path with a fake agent whose stream blocks, so
the timeout fires deterministically without waiting on a real model.
"""

from __future__ import annotations

import queue
import threading
import time

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from agent_server import agent as agent_mod


class _FakeAgent:
    """Streams the tokens it is given, then blocks until released.

    Blocking rather than sleeping a fixed time keeps the test independent of how
    long the timeout is set to: the run is still in flight when the wait expires,
    which is exactly the state a real cut catches the model in.
    """

    def __init__(self, tokens: list[str], release: threading.Event):
        self._tokens = tokens
        self._release = release

    def stream(self, _inputs, _config, stream_mode=None):
        for tok in self._tokens:
            yield "messages", (AIMessageChunk(content=tok),)
        # Held open past the caller's patience.
        self._release.wait(timeout=30)


@pytest.fixture
def short_timeout(monkeypatch):
    monkeypatch.setattr(agent_mod, "SPECIALIST_TIMEOUT_SECONDS", 1)


@pytest.fixture
def no_context(monkeypatch):
    monkeypatch.setattr(agent_mod, "_trim_context", lambda context, name: context)


@pytest.fixture
def one_specialist(monkeypatch):
    def _install(tokens: list[str]):
        release = threading.Event()
        monkeypatch.setattr(
            agent_mod,
            "_get_specialists",
            lambda: {"downtime_analyst": _FakeAgent(tokens, release)},
        )
        return release

    return _install


class TestWhatTheReaderKeeps:
    def test_prose_already_streamed_survives_the_cut(
        self, short_timeout, no_context, one_specialist
    ):
        release = one_specialist(["## Root Cause\n", "- FILLER stalled at 15:16\n"])
        q: queue.Queue = queue.Queue()
        try:
            result = agent_mod._run_specialist_streaming(
                "downtime_analyst", "why did the filler stop?", {}, q
            )
        finally:
            release.set()

        assert "## Root Cause" in result
        assert "FILLER stalled at 15:16" in result

    def test_the_reader_is_told_it_is_incomplete(
        self, short_timeout, no_context, one_specialist
    ):
        release = one_specialist(["## Root Cause\n", "- FILLER stalled\n"])
        q: queue.Queue = queue.Queue()
        try:
            result = agent_mod._run_specialist_streaming(
                "downtime_analyst", "why?", {}, q
            )
        finally:
            release.set()

        # Presenting a truncated diagnosis as a whole one would be worse than
        # the error it replaces.
        assert "cut short" in result.lower()

    def test_a_cut_with_nothing_written_yet_still_explains_itself(
        self, short_timeout, no_context, one_specialist
    ):
        release = one_specialist([])
        q: queue.Queue = queue.Queue()
        try:
            result = agent_mod._run_specialist_streaming(
                "downtime_analyst", "why?", {}, q
            )
        finally:
            release.set()

        assert "timed out" in result.lower()
        assert "downtime analyst" in result.lower()

    def test_the_done_sentinel_carries_the_partial_answer(
        self, short_timeout, no_context, one_specialist
    ):
        release = one_specialist(["## Root Cause\n", "- FILLER\n"])
        q: queue.Queue = queue.Queue()
        try:
            agent_mod._run_specialist_streaming("downtime_analyst", "why?", {}, q)
        finally:
            release.set()

        events = []
        while not q.empty():
            events.append(q.get_nowait())
        done = [payload for kind, payload in events if kind == "done"]
        assert done, "the caller drains until 'done'; without it the SSE stream hangs"
        assert "Root Cause" in done[0]


class TestTheBudgetCoversTheWork:
    def test_the_default_clears_the_measured_range(self):
        # Measured end to end on both environments for the same question: 88s on
        # dev, 93s on prod. A budget inside that spread decides coin flips.
        assert agent_mod.SPECIALIST_TIMEOUT_SECONDS >= 120

    def test_it_still_bounds_the_wait(self):
        # Not unbounded either — a question that never lands has to give up
        # while the reader is still there.
        assert agent_mod.SPECIALIST_TIMEOUT_SECONDS <= 300


class TestThinkingAloudIsStillDiscarded:
    def test_a_preamble_before_a_tool_call_does_not_reach_the_reader(
        self, short_timeout, no_context, monkeypatch
    ):
        """Tokens emitted before a tool call are the model reasoning aloud.

        They are reset in the UI when the tool result arrives, and the partial
        answer has to reset with them — otherwise a cut would hand back the
        preamble as though it were the findings.
        """
        from langchain_core.messages import AIMessage, ToolMessage

        release = threading.Event()

        class _PreambleThenTool:
            def stream(self, _inputs, _config, stream_mode=None):
                yield "messages", (AIMessageChunk(content="Let me look that up. "),)
                yield "updates", {
                    "tools": {
                        "messages": [
                            ToolMessage(content="{}", tool_call_id="1", name="query_production_db")
                        ]
                    }
                }
                yield "messages", (AIMessageChunk(content="## Root Cause\n"),)
                release.wait(timeout=30)

            def invoke(self, *a, **k):  # pragma: no cover - not used
                return AIMessage(content="")

        monkeypatch.setattr(
            agent_mod, "_get_specialists", lambda: {"downtime_analyst": _PreambleThenTool()}
        )
        q: queue.Queue = queue.Queue()
        try:
            result = agent_mod._run_specialist_streaming("downtime_analyst", "why?", {}, q)
        finally:
            release.set()

        assert "Let me look that up" not in result
        assert "## Root Cause" in result


def test_the_worker_is_released_rather_than_left_running(
    short_timeout, no_context, one_specialist
):
    """The cancellation flag has to be set, or an abandoned run holds a slot."""
    release = one_specialist(["partial"])
    q: queue.Queue = queue.Queue()
    start = time.monotonic()
    try:
        agent_mod._run_specialist_streaming("downtime_analyst", "why?", {}, q)
    finally:
        release.set()
    # The caller returns on the timeout rather than waiting for the agent.
    assert time.monotonic() - start < 10


class _FakeSyncAgent:
    """Yields whole graph states, blocking partway through.

    `stream_mode="values"` emits the full state after each super-step, so this
    stands in for the shape `_run_specialist` drives. The block holds the run
    open past the caller's patience, which is the state a real cut catches a
    specialist in. What happens *after* the release is the point.
    """

    def __init__(self, release: threading.Event):
        self._release = release
        self.ran_to_completion = False

    def stream(self, _inputs, _config, stream_mode=None):
        assert stream_mode == "values", (
            "the sync path needs whole states, not per-node updates, or the "
            "last emission is not what invoke() would have returned"
        )
        yield {"messages": [AIMessage(content="first")]}
        self._release.wait(timeout=30)
        yield {"messages": [AIMessage(content="second")]}
        yield {"messages": [AIMessage(content="third")]}
        self.ran_to_completion = True


class TestTheSyncPathLetsGoOfItsWorker:
    """`future.result(timeout=…)` frees the caller, never the thread.

    The pool holds six workers. A timed-out run that keeps going holds one of
    them for as long as its model and tools take, so a handful of slow questions
    starved the pool for everyone — while the people who asked them had already
    been told the analysis timed out.
    """

    def test_a_timed_out_run_stops_instead_of_finishing_for_nobody(
        self, short_timeout, no_context, monkeypatch
    ):
        release = threading.Event()
        fake = _FakeSyncAgent(release)
        monkeypatch.setattr(agent_mod, "_get_specialists", lambda: {"downtime_analyst": fake})

        answer = agent_mod._run_specialist("downtime_analyst", "why?", {})
        assert "timed out" in answer

        # Let the blocked super-step finish. A cancelled run takes the next
        # seam and stops; an uncancelled one runs the remaining states.
        release.set()
        deadline = time.monotonic() + 10
        while not release.is_set() and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(0.5)

        assert not fake.ran_to_completion, (
            "the worker ran the whole ReAct loop after the caller gave up, "
            "holding a pool slot for an answer nobody is waiting for"
        )

    def test_a_run_that_finishes_in_time_still_returns_its_last_state(
        self, no_context, monkeypatch
    ):
        """The cancel seam replaced `invoke` with `stream`; same answer either way."""

        class _Finishes:
            def stream(self, _inputs, _config, stream_mode=None):
                yield {"messages": [AIMessage(content="working")]}
                yield {"messages": [AIMessage(content="the actual answer")]}

        monkeypatch.setattr(agent_mod, "_get_specialists", lambda: {"downtime_analyst": _Finishes()})
        assert agent_mod._run_specialist("downtime_analyst", "why?", {}) == "the actual answer"
