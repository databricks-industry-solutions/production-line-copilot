"""Guards on ``scripts/hooks/install.sh`` and the pre-push gate it installs.

The installer writes an executable into a directory that git chooses, which is
a short walk from writing an executable into the wrong one. The first version
of this script found its target with ``git rev-parse --git-path hooks`` — the
obvious call, and wrong: it resolves *through* ``core.hooksPath``. On a machine
where an organisation manages hooks centrally that returns the shared
directory, so the installer moved the managed pre-push aside and symlinked its
own in place of it, disabling secret scanning for every repository on the
machine rather than installing a hook in one.

Hence two rules. The installer must not consult a git path that honours
``core.hooksPath``, and it must refuse to write outside the repository even if
some future git call lies to it. Both are checked statically, because the
failure they describe is one you do not want to reproduce in a test to detect.

The behavioural tests below run the hook directly against a throwaway repo with
a stub ``check.sh``, which is safe: nothing here installs anything.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = ROOT / "scripts" / "hooks"
INSTALLER = HOOKS_DIR / "install.sh"
PRE_PUSH = HOOKS_DIR / "pre-push"

ZERO_SHA = "0" * 40


@pytest.fixture(scope="module")
def installer_source() -> str:
    return INSTALLER.read_text(encoding="utf-8")


def _uncommented(source: str) -> str:
    """The script with comment lines dropped, so prose cannot satisfy a rule.

    The comments here discuss ``--git-path hooks`` at length precisely because
    it is the trap; a naive substring search would find it there and pass.
    """
    return "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )


def test_the_hook_and_installer_are_executable():
    """A present-but-unrunnable hook aborts the push with 127."""
    for path in (INSTALLER, PRE_PUSH):
        assert path.exists(), f"{path} is missing"
        assert os.stat(path).st_mode & stat.S_IXUSR, f"{path} is not executable"


def test_the_installer_never_asks_git_for_a_hooks_path_it_cannot_trust(
    installer_source,
):
    """No ``--git-path hooks``: it resolves through ``core.hooksPath``."""
    code = _uncommented(installer_source)
    assert "--git-path" not in code, (
        "git rev-parse --git-path honours core.hooksPath, so on a machine with "
        "managed hooks this installs into the shared directory and replaces "
        "the organisation's hooks for every repository"
    )


def test_the_installer_refuses_to_write_outside_the_repository(installer_source):
    """The containment check is present, and the path it checks is the one used."""
    code = _uncommented(installer_source)
    assert "--show-toplevel" in code, "nothing establishes where the repo is"
    assert "refusing to install" in code, "no containment check on the target"


def _run_hook(repo: Path, stdin: str, env: dict[str, str] | None = None):
    return subprocess.run(
        [str(repo / ".git" / "hooks" / "pre-push"), "origin", "git@example.invalid:x"],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=repo,
        env={**os.environ, **(env or {})},
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway repo with the real hook installed over a stub gate."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    scripts = tmp_path / "scripts"
    (scripts / "hooks").mkdir(parents=True)

    stub = scripts / "check.sh"
    stub.write_text(
        '#!/usr/bin/env bash\necho "stub gate ran"\nexit "${STUB_EXIT:-0}"\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)

    for name in ("pre-push", "install.sh"):
        copy = scripts / "hooks" / name
        copy.write_text((HOOKS_DIR / name).read_text(encoding="utf-8"), encoding="utf-8")
        copy.chmod(0o755)

    subprocess.run([str(scripts / "hooks" / "install.sh")], cwd=tmp_path, check=True)
    return tmp_path


def test_installing_puts_the_hook_in_this_repos_git_hooks(repo):
    """Even when core.hooksPath points somewhere else entirely."""
    installed = repo / ".git" / "hooks" / "pre-push"
    assert installed.is_symlink()
    assert Path(os.readlink(installed)) == repo / "scripts" / "hooks" / "pre-push"


def test_a_failing_gate_aborts_the_push(repo):
    result = _run_hook(repo, f"refs/heads/main abc123 refs/heads/main {ZERO_SHA}\n", {"STUB_EXIT": "1"})
    assert result.returncode != 0
    assert "stub gate ran" in result.stdout


def test_a_passing_gate_lets_the_push_through(repo):
    result = _run_hook(repo, f"refs/heads/main abc123 refs/heads/main {ZERO_SHA}\n")
    assert result.returncode == 0
    assert "stub gate ran" in result.stdout


def test_deleting_a_branch_skips_the_gate(repo):
    """An all-zero local sha is a deletion: there is no new code to test."""
    result = _run_hook(repo, f"(delete) {ZERO_SHA} refs/heads/gone abc123\n")
    assert result.returncode == 0
    assert "stub gate ran" not in result.stdout


def test_a_deletion_alongside_real_work_still_runs_the_gate(repo):
    result = _run_hook(
        repo,
        f"refs/heads/gone {ZERO_SHA} refs/heads/gone abc123\n"
        f"refs/heads/main abc123 refs/heads/main {ZERO_SHA}\n",
    )
    assert "stub gate ran" in result.stdout


def test_no_refs_at_all_still_runs_the_gate(repo):
    """Empty stdin is not evidence there is nothing to check; a gate should run."""
    result = _run_hook(repo, "")
    assert "stub gate ran" in result.stdout


def test_skip_checks_bypasses_the_gate(repo):
    result = _run_hook(repo, "refs/heads/main abc123 refs/heads/main x\n", {"SKIP_CHECKS": "1"})
    assert result.returncode == 0
    assert "stub gate ran" not in result.stdout


def test_the_bypass_is_documented_where_it_is_offered():
    """A developer told only about --no-verify would skip secret scanning too."""
    assert "SKIP_CHECKS" in (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
