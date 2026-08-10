"""Static guards on how ``scripts/deploy.sh`` hands a workspace to Python.

``deploy.sh`` takes its workspace from ``DATABRICKS_PROFILE`` and passes it to
the Databricks CLI as ``--profile``. The Python steps do not read that variable:
the SDK reads ``DATABRICKS_CONFIG_PROFILE``, and a script that builds a bare
``WorkspaceClient()`` with neither runs the SDK's own default chain instead —
landing in whatever workspace ``~/.databrickscfg`` calls DEFAULT.

Two of the nine Python steps had neither, found by deploying to dev with a
non-default profile. ``provision_trace_storage.py`` was one of them, which is
the bad one to lose: it links Unity Catalog trace storage to an experiment and
grants read on it, and a UC link is permanent. Against an expired default
profile it fails loudly, which is the lucky case. Against a *valid* default
profile for the wrong workspace it would succeed there instead — provisioning
and granting in a workspace nobody asked about, while the deploy reports
success.

The rule is structural rather than a list of known-bad scripts, so a Python
step added later cannot quietly join them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SH = ROOT / "scripts" / "deploy.sh"


@pytest.fixture(scope="module")
def lines() -> list[str]:
    return DEPLOY_SH.read_text(encoding="utf-8").splitlines()


def _logical_command(lines: list[str], index: int) -> str:
    """The whole backslash-continued command containing ``lines[index]``.

    Both directions matter: the env prefix usually sits on an earlier line
    (``FOO=$(BAR=1 \\`` then the interpreter on the next), while the script
    name and its flags trail off onto later ones.
    """
    start = index
    while start > 0 and lines[start - 1].rstrip().endswith("\\"):
        start -= 1
    end = index
    while end < len(lines) - 1 and lines[end].rstrip().endswith("\\"):
        end += 1
    return "\n".join(lines[start : end + 1])


def _python_steps(lines: list[str]) -> list[tuple[int, str, str]]:
    """(line number, script name, full command) for every Python invocation."""
    steps = []
    for i, line in enumerate(lines):
        if "PROJECT_PY[@]" not in line or line.strip().startswith("#"):
            continue
        command = _logical_command(lines, i)
        match = re.search(r"scripts/(\S+\.py)", command)
        steps.append((i + 1, match.group(1) if match else "(inline -c)", command))
    return steps


def test_the_script_still_has_python_steps_to_check(lines):
    """Guard the guard: a parser that silently matches nothing proves nothing."""
    assert len(_python_steps(lines)) >= 8


def test_every_python_step_is_told_which_workspace(lines):
    """Each Python step gets --profile or DATABRICKS_CONFIG_PROFILE.

    Either is fine — some scripts parse the flag themselves, the rest rely on
    the SDK reading the environment. Neither is the bug.
    """
    orphans = [
        (number, script)
        for number, script, command in _python_steps(lines)
        if "--profile" not in command and "DATABRICKS_CONFIG_PROFILE" not in command
    ]
    assert not orphans, (
        "these Python steps would run against the SDK's default workspace "
        f"rather than the deploy's: {orphans}"
    )


def test_trace_provisioning_is_pointed_at_databricks_mlflow(lines):
    """The trace provisioning step sets MLFLOW_TRACKING_URI.

    Unset, mlflow defaults to a local ``./mlruns`` store, so the script creates
    its experiment on disk and the UC link fails with "trace_location is only
    supported with a Databricks tracking URI" — leaving an mlruns directory in
    the deploy tree. This hid behind the missing profile above: auth failed
    first, so the tracking URI never got the chance to be wrong.
    """
    steps = [
        command
        for _, script, command in _python_steps(lines)
        if script == "provision_trace_storage.py"
    ]
    assert steps, "provision_trace_storage.py is no longer invoked from deploy.sh"
    for command in steps:
        assert "MLFLOW_TRACKING_URI=databricks" in command
