"""Deploy must leave the board and the assistant telling the same story.

The event log is append-only, so resolving an incident appends a 'resolved'
row and the original keeps timestamp_end NULL for good. Every incident of the
shift therefore still matches "timestamp_end IS NULL". On prod that rule
counted nine open where the board, which reconciles against resolve markers,
counted one — the assistant reported unresolved incidents nobody could see.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "close_open_incidents.py"
DEPLOY = ROOT / "scripts" / "deploy.sh"


@pytest.fixture(scope="module")
def source() -> str:
    return SCRIPT.read_text()


@pytest.fixture(scope="module")
def deploy() -> str:
    return DEPLOY.read_text()


class TestItClosesTheRightRows:
    def test_it_only_closes_what_a_marker_actually_resolved(self, source):
        """The marker must be at or after the incident, not merely on the
        same machine — an earlier resolve belongs to an earlier incident."""
        assert "r.resolved_at >= e.timestamp_start" in source

    def test_it_never_closes_a_marker_with_itself(self, source):
        assert "e.event_type <> 'resolved'" in source

    def test_it_takes_the_latest_marker(self, source):
        assert "max(timestamp_start) AS resolved_at" in source

    def test_it_matches_markers_within_a_shift(self, source):
        assert "r.shift_id = e.shift_id" in source
        assert "r.machine_id = e.machine_id" in source

    def test_it_records_how_long_the_incident_ran(self, source):
        assert "duration_minutes" in source
        assert "unix_timestamp" in source

    def test_carryover_is_scoped_to_other_shifts(self, source):
        """The shift being run now is live; closing its open incidents would
        erase faults that are genuinely happening."""
        assert "shift_id <> " in source

    def test_carryover_says_who_closed_it(self, source):
        assert "carryover" in source.lower()


class TestItAlsoClearsTheBoard:
    def test_it_writes_markers_to_the_serving_table(self, source):
        """The board reads Lakebase, so UC alone leaves the row on screen."""
        assert "ai_serving.event_log_recent" in source
        assert "INSERT INTO ai_serving.event_log_recent" in source

    def test_it_does_not_double_mark_an_already_resolved_machine(self, source):
        assert "NOT EXISTS" in source

    def test_a_missing_lakebase_does_not_stop_it(self, source):
        assert "Lakebase unreachable" in source or "Lakebase helpers unavailable" in source


class TestItIsSafeToRunOnEveryDeploy:
    def test_it_never_fails_the_deploy(self, source):
        """Housekeeping is not a reason to leave the app undeployed."""
        assert source.count("continuing") >= 2
        assert "return 0" in source

    def test_it_is_idempotent_by_construction(self, source):
        """A second run finds nothing because the first set timestamp_end."""
        assert "timestamp_end IS NULL" in source

    def test_it_refuses_to_guess_its_target(self, source):
        assert 'missing required argument' in source


class TestTheDeployRunsIt:
    def test_the_deploy_calls_it(self, deploy):
        assert "scripts/close_open_incidents.py" in deploy

    def test_it_runs_after_the_streaming_jobs_restart(self, deploy):
        """It UPDATEs a Delta source the quality-sync stream reads, so that
        stream must already be on the ignoreChanges build or it dies on the
        next micro-batch."""
        restart = deploy.index("restart_continuous_jobs.py")
        close = deploy.index("close_open_incidents.py")
        assert restart < close

    def test_it_runs_before_the_deploy_is_called_complete(self, deploy):
        assert deploy.index("close_open_incidents.py") < deploy.index("=== Deploy complete")

    def test_it_is_given_the_environment_it_is_pointed_at(self, deploy):
        block = deploy[deploy.index("close_open_incidents.py"):][:400]
        for flag in ("--catalog", "--schema", "--shift-id", "--warehouse-id"):
            assert flag in block, flag

    def test_it_uses_this_environment_shift_not_a_hardcoded_one(self, deploy):
        block = deploy[deploy.index("close_open_incidents.py"):][:400]
        assert "SIM_SHIFT" in block

    def test_a_failure_only_warns(self, deploy):
        block = deploy[deploy.index("close_open_incidents.py"):][:500]
        assert "WARN" in block

    def test_it_uses_the_project_interpreter(self, deploy):
        """It imports agent_server for the Lakebase pass, so it needs deps."""
        block = deploy[max(0, deploy.index("close_open_incidents.py") - 200):]
        assert "PROJECT_PY" in block[:400]


class TestTheGenieSpaceNoLongerTeachesTheWrongRule:
    @pytest.fixture(scope="class")
    def genie(self) -> str:
        return (ROOT / "scripts" / "genie_space_config_streaming.py").read_text()

    def test_no_query_treats_a_null_end_as_open(self, genie):
        """This is what made the assistant count the whole shift as open."""
        naive = re.findall(r"WHERE[^\"]*timestamp_end IS NULL(?! AND event_type)", genie)
        assert not naive, naive

    def test_the_queries_check_for_a_resolve_marker(self, genie):
        assert "r.event_type = 'resolved'" in genie

    def test_the_table_description_warns_it_is_append_only(self, genie):
        assert "APPEND-ONLY" in genie or "append-only" in genie
