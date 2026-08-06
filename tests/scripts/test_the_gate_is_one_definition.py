"""CI and the local command have to check the same things.

Two copies of "what passing means" drift, and the drift is always discovered the
expensive way: a change that is green locally and red in CI, or worse, the other
way round. So the workflow calls the same script a developer runs, and these
tests hold that arrangement in place.

There is a second reason it matters here. Hosted runners are disabled on this
GitHub Enterprise organisation, so no workflow on this repository has ever
completed a run — the jobs fail with "hosted runners are disabled" before
executing a step. Where that is true the script is the only gate there is, so it
has to be complete on its own rather than a convenience wrapper around part of
the checks.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"


@pytest.fixture(scope="module")
def script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _run_steps(workflow: dict) -> list[str]:
    return [
        str(step.get("run", ""))
        for job in workflow["jobs"].values()
        for step in job["steps"]
    ]


class TestTheGateIsRunnable:
    def test_the_script_exists_and_is_executable(self):
        assert SCRIPT.is_file()
        assert SCRIPT.stat().st_mode & 0o111, "check.sh must be executable to be a gate"

    def test_it_declares_a_shell(self, script):
        assert script.startswith("#!/usr/bin/env bash")


class TestItChecksEverythingItClaimsTo:
    @pytest.mark.parametrize("gate", ["ruff", "pytest", "typecheck", "lint", "vitest"])
    def test_each_gate_is_invoked(self, script, gate):
        assert gate in script, f"check.sh does not run {gate}"

    def test_it_reports_failure_rather_than_stopping_at_the_first(self, script):
        # Running the rest after one failure is what makes a single run useful:
        # you fix everything at once instead of rediscovering the next gate.
        assert "FAILED+=(" in script
        assert "exit 1" in script

    def test_it_does_not_chain_the_smoke_run(self, script):
        # `npm test` pulls in Playwright, which needs a warehouse and Lakebase.
        # Comments are allowed to name it; commands are not.
        commands = "\n".join(
            line for line in script.splitlines() if not line.lstrip().startswith("#")
        )
        assert "npm test" not in commands
        assert "vitest run" in commands


class TestCiCallsTheSameScript:
    def test_both_jobs_delegate_to_it(self, workflow):
        runs = " ".join(_run_steps(workflow))
        assert "./scripts/check.sh python" in runs
        assert "./scripts/check.sh client" in runs

    def test_ci_does_not_re_implement_the_gates(self, workflow):
        """A second copy in the workflow is the drift this is meant to prevent."""
        runs = _run_steps(workflow)
        checks = [r for r in runs if "check.sh" not in r]
        for forbidden in ("pytest", "ruff check", "vitest", "npm run lint", "npm run typecheck"):
            assert not any(forbidden in r for r in checks), (
                f"{forbidden} is invoked directly in the workflow as well as in check.sh"
            )

    def test_it_runs_on_pull_requests(self, workflow):
        # PyYAML reads the bare `on:` key as the boolean True.
        triggers = workflow.get("on") or workflow.get(True)
        assert "pull_request" in triggers

    def test_neither_job_needs_a_secret_or_a_workspace(self, workflow):
        raw = WORKFLOW.read_text(encoding="utf-8")
        assert "secrets." not in raw, "a gate that needs a secret cannot run on a fork"
        assert "DATABRICKS_HOST" not in raw


def test_contributors_are_told_about_the_script():
    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "./scripts/check.sh" in contributing
    # And warned that a green CI is not guaranteed to exist for them.
    assert "hosted runners" in contributing
