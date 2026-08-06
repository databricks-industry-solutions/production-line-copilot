"""A deploy must land on the code it just deployed.

``bundle deploy`` replaces the workspace files a job runs from, but a run
already in flight keeps executing the code it started with, and the producer and
quality-sync jobs are ``continuous`` — they never terminate on their own. Every
deploy after the first therefore left them running whatever build was current
when they last started.

This was not caught by anything until a producer fix was deployed to both
environments and changed nothing: both were still running the previous day's
build, thirteen hours old. A fix that deploys cleanly and has no effect sends
you looking for a second bug that does not exist.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.restart_continuous_jobs import ACTIVE, CONTINUOUS_JOBS, restart

DEPLOY = Path(__file__).resolve().parents[2] / "scripts" / "deploy.sh"


class _State:
    def __init__(self, value):
        self.life_cycle_state = type("L", (), {"value": value})()


class _Run:
    def __init__(self, run_id, value):
        self.run_id = run_id
        self.state = _State(value)


class _Job:
    def __init__(self, job_id, name):
        self.job_id = job_id
        self.settings = type("S", (), {"name": name})()


class _Jobs:
    def __init__(self, jobs, runs):
        self._jobs = jobs
        self._runs = runs
        self.cancelled: list[int] = []

    def list(self):
        return list(self._jobs)

    def list_runs(self, job_id, limit=5):
        return list(self._runs.get(job_id, []))

    def cancel_run(self, run_id):
        self.cancelled.append(run_id)


@pytest.fixture
def workspace(monkeypatch):
    holder = {}

    def build(jobs, runs):
        api = _Jobs(jobs, runs)
        holder["api"] = api
        client = type("W", (), {"jobs": api})()
        import databricks.sdk

        monkeypatch.setattr(databricks.sdk, "WorkspaceClient", lambda **_k: client)
        return api

    return build


class TestItRestartsWhatTheDeployReplaced:
    def test_a_running_producer_is_cancelled(self, workspace):
        job = _Job(1, "line-manager-streaming-producer")
        api = workspace([job], {1: [_Run(99, "RUNNING")]})
        assert restart("p") == 0
        assert api.cancelled == [99]

    def test_both_continuous_jobs_are_covered(self, workspace):
        jobs = [_Job(1, "line-manager-streaming-producer"),
                _Job(2, "line-manager-streaming-quality-sync")]
        api = workspace(jobs, {1: [_Run(11, "RUNNING")], 2: [_Run(22, "RUNNING")]})
        restart("p")
        assert sorted(api.cancelled) == [11, 22]

    def test_the_dev_suffix_targets_the_dev_jobs(self, workspace):
        jobs = [_Job(1, "line-manager-streaming-producer"),
                _Job(2, "line-manager-streaming-producer-dev")]
        api = workspace(jobs, {1: [_Run(11, "RUNNING")], 2: [_Run(22, "RUNNING")]})
        restart("p", suffix="-dev")
        assert api.cancelled == [22], "a dev deploy must not restart prod's producer"

    def test_prod_does_not_match_the_dev_job(self, workspace):
        jobs = [_Job(1, "line-manager-streaming-producer"),
                _Job(2, "line-manager-streaming-producer-dev")]
        api = workspace(jobs, {1: [_Run(11, "RUNNING")], 2: [_Run(22, "RUNNING")]})
        restart("p")
        assert api.cancelled == [11]

    def test_a_terminated_run_is_left_alone(self, workspace):
        job = _Job(1, "line-manager-streaming-producer")
        api = workspace([job], {1: [_Run(99, "TERMINATED")]})
        assert restart("p") == 0
        assert api.cancelled == []

    def test_a_missing_job_is_not_a_failure(self, workspace):
        # Fresh deploys create these unpaused; workshop targets may omit them.
        workspace([], {})
        assert restart("p") == 0

    def test_a_cancel_failure_is_reported(self, workspace):
        job = _Job(1, "line-manager-streaming-producer")
        api = workspace([job], {1: [_Run(99, "RUNNING")]})

        def boom(run_id):
            raise RuntimeError("permission denied")

        api.cancel_run = boom
        assert restart("p") == 1

    def test_pending_counts_as_active(self):
        # A run queued behind cluster acquisition still starts on the old code.
        assert {"PENDING", "RUNNING", "QUEUED"} <= ACTIVE


class TestTheCommandLine:
    """Exercised through argv, because calling ``restart()`` directly is what
    let the ``--suffix -dev`` parsing bug reach a deploy."""

    def _parse(self, argv):
        import sys

        import scripts.restart_continuous_jobs as mod

        captured = {}
        orig_argv, orig_restart = sys.argv, mod.restart
        sys.argv = ["restart_continuous_jobs.py", *argv]
        mod.restart = lambda profile, suffix="": captured.update(
            profile=profile, suffix=suffix
        ) or 0
        try:
            mod.main()
        finally:
            sys.argv, mod.restart = orig_argv, orig_restart
        return captured

    def test_a_dash_prefixed_suffix_survives_parsing(self):
        assert self._parse(["--profile", "p", "--suffix=-dev"])["suffix"] == "-dev"

    def test_an_empty_suffix_is_prod(self):
        assert self._parse(["--profile", "p", "--suffix="])["suffix"] == ""

    def test_it_runs_with_no_arguments_at_all(self):
        assert self._parse([])["suffix"] == ""


class TestTheDeployCallsIt:
    def test_deploy_invokes_the_restart(self):
        assert "scripts/restart_continuous_jobs.py" in DEPLOY.read_text()

    def test_it_passes_the_dev_suffix(self):
        text = DEPLOY.read_text()
        assert 'JOB_SUFFIX="-dev"' in text
        # `--suffix -dev` fails: argparse reads a leading dash as another
        # option, so the flag is left with no argument and the whole restart
        # step aborts. A dev deploy did exactly that and reported only a
        # warning, leaving the producer on the old build — the very failure
        # this step exists to prevent. The `=` form is the fix.
        assert '--suffix="$JOB_SUFFIX"' in text
        assert '--suffix "$JOB_SUFFIX"' not in text

    def test_it_runs_after_the_bundle_deploy(self):
        text = DEPLOY.read_text()
        assert text.index("bundle deploy") < text.index("restart_continuous_jobs.py")

    def test_a_restart_failure_does_not_abort_the_deploy(self):
        # The app is already live by this point; failing here would be worse.
        text = DEPLOY.read_text()
        after = text[text.index("restart_continuous_jobs.py"):]
        assert "WARN" in after[:400]

    def test_it_uses_the_project_interpreter(self):
        # Bare python3 lacks the SDK; that mistake has been made here before.
        text = DEPLOY.read_text()
        call = text[text.index("restart_continuous_jobs.py") - 200:text.index("restart_continuous_jobs.py")]
        assert "PROJECT_PY" in call

    def test_the_job_names_match_what_the_bundle_creates(self):
        sync = (Path(__file__).resolve().parents[2] / "scripts" / "sync_project_settings.py").read_text()
        for name in CONTINUOUS_JOBS:
            assert f"name: {name}" in sync
