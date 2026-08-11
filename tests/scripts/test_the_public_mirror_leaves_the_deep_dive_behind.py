"""What is internal stays internal, without anyone having to remember.

The public repository is a chain of squashed commits, one per release, each a
full copy of this tree. Copying everything is the default, so a file is public
unless something stops it — and the thing stopping it used to be the person
doing the release deleting files by hand. That works until the release you do in
a hurry, and the failure is unrecoverable: you cannot unpublish a git history
someone has already cloned.

``scripts/public-exclude.txt`` names what must not ship and
``scripts/mirror_public.sh`` enforces it. These tests cover the parts that fail
silently: a typo in the list (which publishes the file while looking correct), a
script that stopped reading the list, and a script that pushes before a human
has looked at what it built.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXCLUDE = ROOT / "scripts/public-exclude.txt"
MIRROR = ROOT / "scripts/mirror_public.sh"


def _excluded() -> list[str]:
    lines = EXCLUDE.read_text().splitlines()
    return [line for line in lines if line.strip() and not line.lstrip().startswith("#")]


def test_the_exclusion_list_exists_and_is_not_empty():
    assert EXCLUDE.exists(), f"{EXCLUDE} is missing — the mirror would publish everything"
    assert _excluded(), "the exclusion list has no paths; either it rotted or it was emptied"


def test_every_excluded_path_is_really_there():
    # A typo excludes nothing and reports success, which is the worst possible
    # outcome: the release looks clean and the file is published anyway. If you
    # are deleting an internal asset for good, delete its line here too.
    missing = [path for path in _excluded() if not (ROOT / path).exists()]
    assert not missing, (
        f"listed but not present: {missing}. A path that does not exist excludes nothing — "
        "fix the spelling, or remove the line if the asset is gone."
    )


def test_every_excluded_path_is_tracked_by_git():
    # Only tracked files can reach the mirror, so an untracked path in the list
    # is dead weight that hides whether the real exclusion still works.
    for path in _excluded():
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", path],
            cwd=ROOT,
            capture_output=True,
        )
        assert tracked.returncode == 0, f"{path} is not tracked; it cannot reach the mirror anyway"


def test_the_deep_dive_is_on_the_list():
    # The reason the list exists. Named explicitly so that deleting the entry
    # has to be deliberate rather than a side effect of tidying the file.
    listed = " ".join(_excluded())
    for expected in (
        "film/src/deep_dive.jsx",
        "docs/media/prodline_copilot_deep_dive.html",
        "docs/media/prodline_copilot_deep_dive.mp4",
        "docs/media/prodline_copilot_deep_dive_vo",
    ):
        assert expected in listed, f"{expected} is no longer withheld from the public mirror"


def test_the_mirror_script_is_runnable():
    assert MIRROR.exists()
    assert MIRROR.stat().st_mode & 0o111, f"{MIRROR} is not executable"


def test_the_mirror_script_reads_the_list():
    # Rather than carrying its own copy of the paths, which would drift.
    body = MIRROR.read_text()
    assert "public-exclude.txt" in body
    for path in _excluded():
        assert path not in body, (
            f"{path} is hardcoded in the mirror script as well as the list; "
            "two copies of this will disagree eventually"
        )


def test_the_mirror_does_not_push_unless_asked():
    body = MIRROR.read_text()
    assert "--push" in body
    # Every push has to sit behind the flag. Finding one before the PUSH check
    # would mean the script publishes as a side effect of verifying.
    guard = body.index("if [[ $DRY_RUN -eq 1 || $PUSH -eq 0 ]]")
    assert "git push" not in body[:guard], (
        "the script pushes before deciding whether it was asked to"
    )


def test_the_mirror_refuses_to_run_on_an_empty_list():
    body = MIRROR.read_text()
    assert "lists no paths" in body, (
        "an empty or unreadable exclusion list must abort, not publish everything"
    )
