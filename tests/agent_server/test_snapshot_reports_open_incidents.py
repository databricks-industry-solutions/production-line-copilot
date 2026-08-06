"""A question about one machine must not be answered with another's history.

Asked to diagnose a stopped FILLER, the snapshot reported three open
DEPALLETIZER breakdowns and no FILLER at all, while the line state — read from
a different source — correctly said the only issue was FILLER.

Three faults stacked up. The event log is append-only, so closure lives in a
later ``event_type='resolved'`` row rather than in ``timestamp_end``; testing
the latter counts every incident the shift ever had as open. The same machine
appeared once per repeat fault, so one flapping unit filled all three slots.
And ordering was by recency alone, with no notion of which machine was asked
about. The specialists were fed the same unreconciled list under the name
``active_events``.

The rows below are the ones that were actually in prod.
"""
from __future__ import annotations

import pytest

from agent_server.agent import _event_detail, _quick_snapshot, _snapshot_alerts, _trim_context
from agent_server.events import active_events, is_event_active, to_epoch

NOW = "2026-08-06T12:49:00Z"


def ev(machine, etype, severity, start, end=None, description="Incident started: machine_breakdown"):
    return {
        "machine_id": machine, "event_type": etype, "severity": severity,
        "timestamp_start": start, "timestamp_end": end, "description": description,
    }


@pytest.fixture
def prod_rows():
    return [
        ev("FILLER", "machine_breakdown", "critical", "2026-08-06T12:48:01.682Z"),
        ev("DEPALLETIZER", "resolved", "info", "2026-08-06T11:16:56.425Z",
           end="2026-08-06T11:16:56.425Z", description="Incident resolved: machine_breakdown"),
        ev("DEPALLETIZER", "machine_breakdown", "high", "2026-08-06T10:01:51.498Z"),
        ev("DEPALLETIZER", "machine_breakdown", "high", "2026-08-06T09:45:37.878Z"),
        ev("DEPALLETIZER", "machine_breakdown", "medium", "2026-08-06T09:38:32.602Z"),
        ev("LABELLER", "minor_stop", "medium", "2026-08-06T09:21:37.000Z",
           description="Label web break during reel changeover"),
    ]


@pytest.fixture
def context(prod_rows):
    return {
        "scenario": {"shift_id": "shift_1", "scenario_name": "Live Real-Time (Lakebase)",
                     "current_timestamp": NOW, "remaining_minutes": 240},
        "current_kpi": {"oee_to_now": 74.4, "availability_to_now": 88.0,
                        "performance_to_now": 85.8, "quality_to_now": 98.5,
                        "cumulative_cases": 3015},
        "production_plan": {"planned_cases": 8400},
        "active_events": prod_rows,
        "line_state": [{"machine_id": "FILLER", "status": "stopped"}]
                      + [{"machine_id": f"M{i}", "status": "running"} for i in range(17)],
    }


class TestAResolveMarkerClosesTheIncident:
    def test_a_marker_closes_everything_open_on_that_machine(self, prod_rows):
        # One marker at 11:16 against three openers at 10:01, 09:45 and 09:38.
        open_now = active_events(prod_rows, to_epoch(NOW))
        assert [e["machine_id"] for e in open_now] == ["FILLER", "LABELLER"]

    def test_the_marker_itself_is_not_an_incident(self, prod_rows):
        assert all(e["event_type"] != "resolved" for e in active_events(prod_rows, to_epoch(NOW)))

    def test_a_marker_older_than_the_opener_closes_nothing(self):
        rows = [
            ev("FILLER", "resolved", "info", "2026-08-06T09:00:00Z", end="2026-08-06T09:00:00Z"),
            ev("FILLER", "machine_breakdown", "high", "2026-08-06T12:00:00Z"),
        ]
        assert len(active_events(rows, to_epoch(NOW))) == 1

    def test_timestamp_end_still_closes_an_event_that_has_one(self):
        rows = [ev("CAPPER", "micro_stop", "low", "2026-08-06T09:01:00Z", end="2026-08-06T09:05:00Z")]
        assert active_events(rows, to_epoch(NOW)) == []

    def test_an_event_with_no_marker_ages_out_after_a_shift(self):
        rows = [ev("LABELLER", "minor_stop", "medium", "2026-08-05T20:00:00Z")]
        assert active_events(rows, to_epoch(NOW)) == []

    def test_an_unreadable_start_is_never_aged_out(self):
        # 0.0 means "could not parse", and ageing those out would hide live rows.
        assert is_event_active(ev("X", "minor_stop", "low", "not-a-date"), to_epoch(NOW), {})


class TestTimestampsArriveInMoreThanOneShape:
    def test_lakebase_datetimes_and_uc_strings_agree(self):
        from datetime import datetime, timezone
        as_string = to_epoch("2026-08-06T12:48:01Z")
        as_dt = to_epoch(datetime(2026, 8, 6, 12, 48, 1, tzinfo=timezone.utc))
        assert as_string == as_dt

    def test_a_naive_datetime_is_read_as_utc(self):
        from datetime import datetime
        assert to_epoch(datetime(2026, 8, 6, 12, 48, 1)) == to_epoch("2026-08-06T12:48:01Z")

    def test_nothing_readable_is_zero(self):
        for value in (None, "", "garbage", object()):
            assert to_epoch(value) == 0.0


class TestTheAlertsMatchTheQuestion:
    def test_the_machine_asked_about_comes_first(self, prod_rows, context):
        alerts = _snapshot_alerts(prod_rows, context, "Diagnose FILLER: status is stopped")
        assert alerts[0]["machine_id"] == "FILLER"

    def test_a_resolved_machine_is_not_reported_at_all(self, prod_rows, context):
        alerts = _snapshot_alerts(prod_rows, context, "Diagnose FILLER")
        assert "DEPALLETIZER" not in {a["machine_id"] for a in alerts}

    def test_one_line_per_machine(self, context):
        rows = [ev("DEPALLETIZER", "machine_breakdown", "high", f"2026-08-06T1{i}:00:00Z")
                for i in range(4)]
        alerts = _snapshot_alerts(rows, context, "how is the line?")
        assert len(alerts) == 1

    def test_the_worst_severity_represents_the_machine(self, context):
        rows = [
            ev("DEPALLETIZER", "machine_breakdown", "medium", "2026-08-06T12:40:00Z"),
            ev("DEPALLETIZER", "machine_breakdown", "critical", "2026-08-06T12:30:00Z"),
        ]
        assert _snapshot_alerts(rows, context, "")[0]["severity"] == "critical"

    def test_severity_orders_the_rest(self, context):
        rows = [
            ev("A", "minor_stop", "low", "2026-08-06T12:45:00Z"),
            ev("B", "machine_breakdown", "critical", "2026-08-06T12:40:00Z"),
        ]
        assert [a["machine_id"] for a in _snapshot_alerts(rows, context, "")] == ["B", "A"]

    def test_it_still_shows_at_most_three(self, context):
        rows = [ev(f"M{i}", "minor_stop", "medium", "2026-08-06T12:40:00Z") for i in range(8)]
        assert len(_snapshot_alerts(rows, context, "")) == 3

    def test_a_question_naming_no_machine_is_fine(self, prod_rows, context):
        alerts = _snapshot_alerts(prod_rows, context, "how are we doing?")
        assert [a["machine_id"] for a in alerts] == ["FILLER", "LABELLER"]

    def test_a_machine_named_inside_another_word_does_not_match(self, context):
        rows = [ev("CAPPER", "minor_stop", "low", "2026-08-06T12:40:00Z"),
                ev("FILLER", "machine_breakdown", "critical", "2026-08-06T12:30:00Z")]
        # "uncapper" must not select CAPPER over a critical FILLER.
        assert _snapshot_alerts(rows, context, "check the uncapper")[0]["machine_id"] == "FILLER"


class TestTheDescriptionEarnsItsLine:
    def test_a_description_that_restates_the_type_is_dropped(self):
        assert _event_detail(ev("F", "machine_breakdown", "high", NOW)) == ""

    def test_real_prose_survives(self):
        e = ev("LABELLER", "minor_stop", "medium", NOW, description="Label web break during reel changeover")
        assert _event_detail(e) == "Label web break during reel changeover"

    def test_an_empty_description_is_dropped(self):
        assert _event_detail(ev("F", "minor_stop", "low", NOW, description="")) == ""

    def test_a_resolved_description_that_restates_the_type_is_dropped(self):
        e = ev("F", "machine_breakdown", "high", NOW, description="Incident resolved: machine_breakdown")
        assert _event_detail(e) == ""

    def test_it_is_capped(self):
        e = ev("F", "minor_stop", "low", NOW, description="x" * 200)
        assert len(_event_detail(e)) == 80


class TestTheWholeSnapshot:
    def test_the_alerts_agree_with_the_line_state(self, context):
        out = _quick_snapshot(context, "Diagnose FILLER: status is stopped")
        assert "1 issue(s)**: FILLER" in out
        assert "DEPALLETIZER" not in out

    def test_it_no_longer_repeats_the_event_type(self, context):
        out = _quick_snapshot(context, "Diagnose FILLER")
        assert "Incident started:" not in out

    def test_it_names_the_machine_that_is_down(self, context):
        assert "**FILLER** — machine_breakdown (critical)" in _quick_snapshot(context, "Diagnose FILLER")

    def test_a_quiet_line_says_so(self, context):
        context["active_events"] = []
        context["line_state"] = [{"machine_id": f"M{i}", "status": "running"} for i in range(18)]
        assert "All 18 machines running" in _quick_snapshot(context, "how are we doing?")


class TestTheSpecialistsSeeTheSameTruth:
    def test_trimmed_context_carries_only_open_events(self, context):
        slim = _trim_context(context, "downtime_analyst")
        assert [e["machine_id"] for e in slim["active_events"]] == ["FILLER", "LABELLER"]

    def test_every_specialist_gets_the_reconciled_list(self, context):
        for name in ("downtime_analyst", "quality_specialist", "shift_briefing",
                     "oee_coach", "maintenance_predictor", "general"):
            slim = _trim_context(context, name)
            assert "DEPALLETIZER" not in {e["machine_id"] for e in slim["active_events"]}, name

    def test_the_briefing_prompt_no_longer_teaches_the_wrong_rule(self):
        from agent_server.prompts import shift_briefing

        text = shift_briefing.SYSTEM_PROMPT if hasattr(shift_briefing, "SYSTEM_PROMPT") else ""
        source = __import__("pathlib").Path(shift_briefing.__file__).read_text()
        assert "active_events with no timestamp_end" not in source + text
