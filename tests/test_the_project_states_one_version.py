"""One release, one version number.

`package.json` said 1.0.0 while `pyproject.toml` said 0.3.6 and the newest tag
was v0.3.5 — three answers to "which version is this?", none of them wrong
enough to break anything, which is why it survived several releases. It still
misleads anyone reading the repository to find out what they have checked out,
so the numbers are pinned to each other here.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


@pytest.fixture(scope="module")
def pyproject_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]["version"]


@pytest.fixture(scope="module")
def package_version() -> str:
    return json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]


def test_python_and_node_agree(pyproject_version, package_version):
    assert package_version == pyproject_version, (
        "package.json and pyproject.toml disagree about the version; "
        "bump both or neither"
    )


@pytest.mark.parametrize("fixture", ["pyproject_version", "package_version"])
def test_the_version_is_semver(fixture, request):
    version = request.getfixturevalue(fixture)
    assert SEMVER.match(version), f"{version!r} is not MAJOR.MINOR.PATCH"


def test_the_changelog_documents_the_current_version(pyproject_version):
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{pyproject_version}]" in changelog, (
        f"CHANGELOG.md has no section for {pyproject_version}; "
        "the version was bumped without release notes"
    )


def test_the_changelog_keeps_an_unreleased_section():
    """Somewhere to write the next change down, so it does not go unrecorded."""
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [Unreleased]" in changelog


def test_every_released_version_links_to_its_tag():
    """A bracketed heading with no link definition renders as literal brackets.

    v0.3.7 shipped without its definition and nothing noticed, because the
    heading still reads correctly as text — the break is only visible to
    someone clicking it on GitHub.
    """
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    headings = set(re.findall(r"^## \[(\d+\.\d+\.\d+)\]", changelog, re.M))
    linked = set(re.findall(r"^\[(\d+\.\d+\.\d+)\]:\s*https?://", changelog, re.M))
    missing = sorted(headings - linked, key=lambda v: [int(p) for p in v.split(".")])
    assert not missing, (
        f"CHANGELOG.md has sections with no link definition: {missing}. "
        "Add a '[x.y.z]: <release url>' line at the foot of the file."
    )


# The release before the public mirror existed. From 0.3.5 the public repository
# stopped being a remote of this one and started receiving squashed commits
# tagged by scripts/mirror_public.sh, which suffixes them. Anything older was
# pushed directly and carries a bare tag.
FIRST_MIRRORED_RELEASE = (0, 3, 5)


def test_every_link_points_at_the_tag_the_mirror_actually_publishes():
    """A link to a tag that was never pushed is a 404, and it renders fine here.

    Five releases shipped pointing at `v0.3.5` through `v0.3.9`, none of which
    exist: the mirror publishes `v0.3.9-public`. The changelog is the one page a
    reader uses to find the code for a version, so every one of those was a dead
    end, and the failure is invisible from inside the repository — the markdown
    is well-formed and only GitHub knows the tag is missing.
    """
    mirror = (ROOT / "scripts" / "mirror_public.sh").read_text(encoding="utf-8")
    template = re.search(r'^TAG="([^"]+)"', mirror, re.M)
    assert template, "scripts/mirror_public.sh no longer assigns TAG; update this test."
    # v${VERSION}-public -> the suffix the published tag carries.
    suffix = template.group(1).replace("v${VERSION}", "")

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    links = re.findall(r"^\[(\d+\.\d+\.\d+)\]:\s*(\S+)$", changelog, re.M)
    assert links, "CHANGELOG.md has no release link definitions at all."

    wrong: list[str] = []
    for version, url in links:
        mirrored = tuple(int(p) for p in version.split(".")) >= FIRST_MIRRORED_RELEASE
        expected = f"v{version}{suffix}" if mirrored else f"v{version}"
        actual = url.rsplit("/", 1)[-1]
        if actual != expected:
            wrong.append(f"  [{version}] points at {actual!r}, expected {expected!r}")

    assert not wrong, (
        "CHANGELOG.md links to tags that were never published:\n"
        + "\n".join(wrong)
        + f"\n\nReleases from {'.'.join(str(p) for p in FIRST_MIRRORED_RELEASE)} on are "
        f"tagged {suffix!r} by scripts/mirror_public.sh."
    )
