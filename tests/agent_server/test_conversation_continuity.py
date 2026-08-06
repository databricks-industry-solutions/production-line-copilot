"""Multi-turn memory and the keep-alive on the assistant's SSE stream.

Both mechanisms here were fully built server-side and inert in production, and
neither had a test — which is the same fact twice. `invoke_stream` accepted a
``conversation_id``, created one when absent, stored every message and returned
the id on the answer event; the client never sent it back, so every turn opened
a new conversation and the history overlay was never populated. And the drain
loop that yields tool events yields nothing at all for a question that calls no
tools, leaving the connection silent for up to the specialist timeout.

The tests drive `invoke_stream` directly with the surrounding pipeline stubbed,
because the failures are in the generator's control flow rather than in any
model output.
"""
from __future__ import annotations

import json
import re
import time

import pytest

import agent_server.agent as agent
from agent_server.memory.conversation_store import ConversationStore


# --------------------------------------------------------------------------- #
# History window
# --------------------------------------------------------------------------- #
class TestTheHistoryWindowFollowsTheConversation:
    """`ORDER BY created_at LIMIT 6` returns the OLDEST six, not the newest.

    Correct for the first three turns, which is long enough to pass a casual
    check; from the fourth turn the window froze on the opening exchange and
    the model was primed with staler and staler context as the conversation
    went on.
    """

    def test_the_query_asks_for_the_most_recent_messages(self, monkeypatch):
        seen = {}

        def _fake_query(sql, params):
            seen["sql"] = sql
            return [
                {"role": "user", "content": "newest"},
                {"role": "assistant", "content": "older"},
            ]

        monkeypatch.setattr("agent_server.memory.conversation_store.query", _fake_query)
        ConversationStore().load_history("c1", limit=6)
        assert re.search(r"ORDER BY created_at DESC", seen["sql"]), (
            "an ascending window pins history to the first messages ever sent"
        )

    def test_rows_come_back_oldest_first_because_callers_drop_the_last_one(self, monkeypatch):
        """Callers treat the final element as the turn just saved."""
        monkeypatch.setattr(
            "agent_server.memory.conversation_store.query",
            lambda sql, params: [
                {"role": "user", "content": "turn 3"},
                {"role": "assistant", "content": "turn 2"},
                {"role": "user", "content": "turn 1"},
            ],
        )
        out = ConversationStore().load_history("c1", limit=3)
        assert [m["content"] for m in out] == ["turn 1", "turn 2", "turn 3"]


# --------------------------------------------------------------------------- #
# invoke_stream
# --------------------------------------------------------------------------- #
class _FakeStore:
    """Records what the generator asks of it, and answers plausibly."""

    def __init__(self):
        self.created = 0
        self.messages: dict[str, list[dict]] = {}

    def create_conversation(self, shift_id):
        self.created += 1
        cid = f"conv-{self.created}"
        self.messages.setdefault(cid, [])
        return cid

    def save_message(self, conversation_id, role, content, metadata=None):
        self.messages.setdefault(conversation_id, []).append(
            {"role": role, "content": content}
        )

    def load_history(self, conversation_id, limit=50):
        return self.messages.get(conversation_id, [])[-limit:]


@pytest.fixture
def stubbed(monkeypatch):
    """Neutralise everything around the generator's control flow."""
    store = _FakeStore()
    monkeypatch.setattr(agent, "_conversation_store", store)
    monkeypatch.setattr(agent, "build_context", lambda *a, **k: {"scenario": {}})
    monkeypatch.setattr(agent, "_overlay_live_state", lambda ctx, shift_id=None: ctx)
    monkeypatch.setattr(agent, "_quick_snapshot", lambda ctx, query="": "snapshot text")
    monkeypatch.setattr(agent, "_route_by_active_incident", lambda ctx: None)
    monkeypatch.setattr(agent, "_classify_intent", lambda q, ctx: "general")
    monkeypatch.setattr(agent, "_extract_actions", lambda name, ctx: [])
    monkeypatch.setattr(agent, "resolve", lambda name: "endpoint")
    monkeypatch.setattr(agent, "describe", lambda ep: ("Model", "Vendor"))
    return store


def _events(chunks):
    """Parse SSE frames, ignoring comments and the terminator."""
    out = []
    for c in chunks:
        if not c.startswith("data: "):
            continue
        payload = c[6:].strip()
        if payload == "[DONE]":
            continue
        out.append(json.loads(payload))
    return out


class TestASecondTurnCanSeeTheFirst:
    def test_an_absent_id_opens_one_conversation_and_returns_it(self, stubbed, monkeypatch):
        monkeypatch.setattr(
            agent, "_run_specialist_streaming",
            lambda name, q, ctx, queue_: queue_.put(("done", "an answer")),
        )
        events = _events(list(agent.invoke_stream("first question")))
        answer = next(e for e in events if e["type"] == "answer")
        assert answer["conversation_id"] == "conv-1"
        assert stubbed.created == 1

    def test_passing_the_id_back_primes_the_specialist_with_what_was_said(
        self, stubbed, monkeypatch
    ):
        seen_context = {}

        def _capture(name, q, ctx, queue_):
            seen_context.update(ctx)
            queue_.put(("done", "an answer"))

        monkeypatch.setattr(agent, "_run_specialist_streaming", _capture)

        first = _events(list(agent.invoke_stream("which machine is worst?")))
        cid = next(e for e in first if e["type"] == "answer")["conversation_id"]
        assert "conversation_history" not in seen_context, "nothing was said before turn one"

        seen_context.clear()
        list(agent.invoke_stream("what about the next one?", conversation_id=cid))

        history = seen_context.get("conversation_history")
        assert history, (
            "the follow-up ran with no prior context — this is what made every "
            "turn stateless while the store quietly recorded all of them"
        )
        assert [m["content"] for m in history] == [
            "which machine is worst?",
            "an answer",
        ]
        assert stubbed.created == 1, "reusing an id must not open a second conversation"

    def test_the_turn_just_asked_is_not_fed_back_as_context(self, stubbed, monkeypatch):
        """The user's current question is the query, not history."""
        seen_context = {}

        def _capture(name, q, ctx, queue_):
            seen_context.update(ctx)
            queue_.put(("done", "answer"))

        monkeypatch.setattr(agent, "_run_specialist_streaming", _capture)
        first = _events(list(agent.invoke_stream("q1")))
        cid = next(e for e in first if e["type"] == "answer")["conversation_id"]
        seen_context.clear()
        list(agent.invoke_stream("q2", conversation_id=cid))
        contents = [m["content"] for m in seen_context.get("conversation_history", [])]
        assert "q2" not in contents


class TestTheAnswerArrivesAsItIsWritten:
    """The answer used to be accumulated in the worker and released in one go.

    What streamed was the tool trace; the text itself appeared in a single
    jump once the whole run finished. These cover the frames that carry it and
    the two ways the draft can be wrong if the worker forwards everything it
    sees on the messages stream.
    """

    def test_tokens_reach_the_client_before_the_answer_does(self, stubbed, monkeypatch):
        def _writes(name, q, ctx, queue_):
            for piece in ("The ", "filler ", "stopped."):
                queue_.put(("token", piece))
            queue_.put(("done", "The filler stopped."))

        monkeypatch.setattr(agent, "_run_specialist_streaming", _writes)
        events = _events(list(agent.invoke_stream("what happened?")))
        types = [e["type"] for e in events]

        assert types.count("token") == 3
        assert types.index("token") < types.index("answer")
        assert "".join(e["content"] for e in events if e["type"] == "token") == "The filler stopped."

    def test_a_preamble_before_a_tool_call_is_dropped_not_prepended(
        self, stubbed, monkeypatch
    ):
        """ReAct models think aloud, then call a tool, then answer.

        The thinking is superseded in `final_content`, so a draft that kept it
        would show the user a sentence that is not part of their answer, with
        the real answer glued onto the end.
        """
        def _thinks_then_answers(name, q, ctx, queue_):
            queue_.put(("token", "Let me check the telemetry."))
            queue_.put(("token_reset", None))
            queue_.put(("token", "Bearing failure on the filler."))
            queue_.put(("done", "Bearing failure on the filler."))

        monkeypatch.setattr(agent, "_run_specialist_streaming", _thinks_then_answers)
        events = _events(list(agent.invoke_stream("why did it stop?")))
        assert [e["type"] for e in events if e["type"].startswith("token")] == [
            "token", "token_reset", "token",
        ]

    def test_the_answer_event_remains_authoritative(self, stubbed, monkeypatch):
        """Synthesis rewrites the text, so the draft is never the final word."""
        def _writes(name, q, ctx, queue_):
            queue_.put(("token", "raw specialist prose"))
            queue_.put(("done", "raw specialist prose"))

        monkeypatch.setattr(agent, "_run_specialist_streaming", _writes)
        monkeypatch.setattr(agent, "_should_skip_synthesis", lambda n, r: False)
        monkeypatch.setattr(
            agent, "_synthesize_response", lambda n, r, c: "## Root Cause\nstructured"
        )
        events = _events(list(agent.invoke_stream("q")))
        answer = next(e for e in events if e["type"] == "answer")
        assert answer["content"] == "## Root Cause\nstructured"


class TestOnlyTheModelsProseIsStreamed:
    """The messages stream carries the tools node too.

    An unfiltered handler types raw tool JSON into the user's answer, and the
    argument fragments of a tool call arrive on it one piece at a time as well.
    Verified against a live endpoint: a ToolMessage for the tools node and
    AIMessageChunks carrying `tool_call_chunks` both appear there.
    """

    def _chunks_forwarded(self, monkeypatch, stream_items):
        """Run the worker's filter over a canned stream, collect ("token", …)."""
        import queue as _queue

        from langchain_core.messages import AIMessageChunk, ToolMessage

        forwarded = []

        class _FakeAgent:
            def stream(self, _inputs, _config, stream_mode=None):
                assert stream_mode == ["updates", "messages"], (
                    "both modes are required: updates builds the tool trace, "
                    "messages carries the text"
                )
                return iter(stream_items)

        monkeypatch.setattr(
            agent, "_get_specialists", lambda: {"downtime_analyst": _FakeAgent()}
        )
        monkeypatch.setattr(agent, "_trim_context", lambda ctx, name: ctx)
        monkeypatch.setattr(agent, "_record_answer", lambda span, text: None)
        monkeypatch.setattr(agent, "current_trace_url", lambda: None)

        q: _queue.Queue = _queue.Queue()
        agent._run_specialist_streaming("downtime_analyst", "why?", {"scenario": {}}, q)
        while not q.empty():
            kind, data = q.get_nowait()
            if kind == "token":
                forwarded.append(data)
        return forwarded, AIMessageChunk, ToolMessage

    def test_tool_output_is_not_typed_into_the_answer(self, monkeypatch):
        from langchain_core.messages import AIMessageChunk, ToolMessage

        items = [
            ("messages", (ToolMessage(content='{"oee": 0.62}', tool_call_id="1"), {"langgraph_node": "tools"})),
            ("messages", (AIMessageChunk(content="OEE is 62%."), {"langgraph_node": "agent"})),
        ]
        forwarded, _, _ = self._chunks_forwarded(monkeypatch, items)
        assert forwarded == ["OEE is 62%."], f"tool payload leaked into the draft: {forwarded}"

    def test_tool_call_arguments_are_not_typed_into_the_answer(self, monkeypatch):
        """Arguments stream in fragments and are not prose."""
        from langchain_core.messages import AIMessageChunk

        arg_fragment = AIMessageChunk(
            content="",
            tool_call_chunks=[{"name": "sql_query", "args": '{"q":', "id": "1", "index": 0}],
        )
        items = [
            ("messages", (arg_fragment, {"langgraph_node": "agent"})),
            ("messages", (AIMessageChunk(content="Done."), {"langgraph_node": "agent"})),
        ]
        forwarded, _, _ = self._chunks_forwarded(monkeypatch, items)
        assert forwarded == ["Done."]


class TestTheStreamDoesNotGoSilent:
    def test_a_question_that_calls_no_tools_still_sends_bytes(self, stubbed, monkeypatch):
        """The failure mode is a proxy dropping an idle connection.

        No tool events means nothing between the status frame and the answer.
        The drain loop already wakes once a second; it just had nothing to say.
        """
        def _slow(name, q, ctx, queue_):
            time.sleep(2.2)
            queue_.put(("done", "an answer"))

        monkeypatch.setattr(agent, "_run_specialist_streaming", _slow)
        chunks = list(agent.invoke_stream("a question needing no tools"))

        assert [c for c in chunks if c.startswith(":")], (
            "no keep-alive frames during a silent specialist run"
        )
        # Still a well-formed stream: the comments must not disturb the events.
        types = [e["type"] for e in _events(chunks)]
        assert types[0] == "snapshot" and "answer" in types
        assert chunks[-1] == "data: [DONE]\n\n"

    def test_the_keepalive_is_a_comment_the_client_will_skip(self, stubbed, monkeypatch):
        """The browser reader ignores any line not starting with 'data: '."""
        def _slow(name, q, ctx, queue_):
            time.sleep(1.2)
            queue_.put(("done", "answer"))

        monkeypatch.setattr(agent, "_run_specialist_streaming", _slow)
        pings = [c for c in agent.invoke_stream("q") if c.startswith(":")]
        assert pings and all(c.endswith("\n\n") for c in pings)
        assert all(not c.startswith("data:") for c in pings)
