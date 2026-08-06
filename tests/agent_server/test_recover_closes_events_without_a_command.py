"""Recover has to work on events with no producer command behind them.

Two kinds of event have never had one. The backfill seeds its last event open
on purpose, so the shift briefing has carryover to report, and a producer that
restarts mid-incident can orphan one. Neither has a ``scenario_commands`` row,
so the only thing Recover knew how to do — mark a command resolve_requested and
let the producer emit the resolve marker — did nothing for either. The event
stayed on the operator board until an eight-hour age-out hid it, and no control
in the UI could clear it.

``event_log_recent`` is append-only, mirroring the Zerobus bronze ingest it
shadows, so closing an event means appending a later ``event_type='resolved'``
row for the same machine. That is what the producer does, and what the app now
does itself when nothing is holding the event open.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from agent_server.serving import realtime_store


class _Cursor:
    def __init__(self, open_events: int):
        self._open_events = open_events
        self.statements: list[tuple[str, tuple]] = []
        self._last = ""

    def execute(self, sql, args=()):
        self._last = " ".join(sql.split())
        self.statements.append((self._last, args))

    def fetchone(self):
        return [self._open_events]

    @property
    def inserts(self):
        return [s for s in self.statements if s[0].upper().startswith("INSERT")]


@pytest.fixture
def cursor(monkeypatch):
    def build(open_events: int = 1, available: bool = True):
        cur = _Cursor(open_events)

        @contextmanager
        def fake_transaction():
            yield cur if available else None

        monkeypatch.setattr(realtime_store, "transaction", fake_transaction)
        return cur
    return build


class TestClosingAMachinesOpenEvents:
    def test_it_reports_how_many_it_closed(self, cursor):
        cursor(open_events=2)
        assert realtime_store.mark_machine_events_resolved("shift_1", "LABELLER") == 2

    def test_it_appends_a_resolve_marker(self, cursor):
        cur = cursor(open_events=1)
        realtime_store.mark_machine_events_resolved("shift_1", "LABELLER")
        assert len(cur.inserts) == 1
        sql, args = cur.inserts[0]
        assert "ai_serving.event_log_recent" in sql
        assert "'resolved'" in sql
        assert "shift_1" in args and "LABELLER" in args

    def test_the_marker_is_closed_the_moment_it_is_written(self, cursor):
        # An open resolve marker would itself need resolving.
        cur = cursor(open_events=1)
        realtime_store.mark_machine_events_resolved("shift_1", "LABELLER")
        sql, _args = cur.inserts[0]
        assert "now(), now()" in sql

    def test_it_records_who_asked(self, cursor):
        cur = cursor(open_events=1)
        realtime_store.mark_machine_events_resolved("shift_1", "LABELLER", resolved_by="ops@corp")
        assert any("ops@corp" in str(a) for a in cur.inserts[0][1])

    def test_it_only_looks_at_this_shift(self, cursor):
        # Every environment shares one Lakebase database, so an unscoped close
        # reaches into the other environment's board.
        cur = cursor(open_events=1)
        realtime_store.mark_machine_events_resolved("shift_1_dev", "LABELLER")
        select = cur.statements[0]
        assert "shift_id = %s" in select[0]
        assert select[1] == ("shift_1_dev", "LABELLER")

    def test_it_ignores_resolve_markers_when_counting(self, cursor):
        cur = cursor(open_events=1)
        realtime_store.mark_machine_events_resolved("shift_1", "LABELLER")
        assert "event_type <> 'resolved'" in cur.statements[0][0]


class TestWhenThereIsNothingToClose:
    def test_it_writes_nothing(self, cursor):
        cur = cursor(open_events=0)
        assert realtime_store.mark_machine_events_resolved("shift_1", "LABELLER") == 0
        assert cur.inserts == []

    def test_a_second_click_does_not_litter_the_feed(self, cursor):
        # The first click closes the event; the second finds nothing open.
        cur = cursor(open_events=0)
        realtime_store.mark_machine_events_resolved("shift_1", "LABELLER")
        realtime_store.mark_machine_events_resolved("shift_1", "LABELLER")
        assert cur.inserts == []

    def test_lakebase_being_down_is_not_a_silent_success(self, cursor):
        cursor(available=False)
        assert realtime_store.mark_machine_events_resolved("shift_1", "LABELLER") == 0


class TestTheEndpointPrefersTheProducer:
    """When a command *is* behind the event, the producer owns the marker: it
    has to clear the sticky fault first, and a marker emitted here would close
    the event before the machine actually recovered."""

    def test_the_endpoint_only_closes_events_when_no_command_matched(self):
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[2] / "agent_server" / "start_server.py"
        ).read_text()
        recover = source[source.index('@app.post("/sim/recover_machine")'):]
        recover = recover[: recover.index('@app.get("/sim/commands")')]
        assert "if all_resolved:" in recover
        early_return = recover.index("if all_resolved:")
        close_call = recover.index("mark_machine_events_resolved")
        assert close_call > early_return, (
            "the event close must sit after the early return for command-backed "
            "incidents, or a live fault gets closed before the producer clears it"
        )
