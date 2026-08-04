"""``uv.lock`` must agree with ``pyproject.toml`` about our own version.

The lock records the project as one of its packages, version included, so a
release bump desynchronises it. Nothing local notices: every test here runs
`uv run --frozen`, which uses the lock as-is. The Databricks Apps build runs
`uv sync --locked`, which does not, and fails with::

    The lockfile at `uv.lock` needs to be updated, but `--locked` was provided.
    [ERROR] Error installing packages.

That is six minutes into a production deploy, after the source has already been
uploaded — which is exactly what happened bumping 0.2.1 to this release.

Re-locking has its own trap worth recording here. This machine's
``~/.config/uv/uv.toml`` sets an internal proxy as the default index, and a
plain ``uv lock`` rewrites every URL in the lock to point at a host the Apps
build cannot reach. Relock with the public index pinned explicitly::

    uv lock --default-index https://pypi.org/simple/
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"
LOCK = ROOT / "uv.lock"


def _project() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]


def _locked_version(name: str) -> str | None:
    """The version `uv.lock` records for our own editable package."""
    block = re.search(
        rf'^\[\[package\]\]\nname = "{re.escape(name)}"\nversion = "([^"]+)"',
        LOCK.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    return block.group(1) if block else None


def test_the_lock_records_this_projects_version():
    project = _project()
    locked = _locked_version(project["name"])
    assert locked is not None, (
        f"uv.lock no longer contains a package block for {project['name']!r}. "
        "If the package was renamed, update this test; if the lock was regenerated "
        "without the editable root, the Apps build will fail to install it."
    )
    assert locked == project["version"], (
        f"pyproject says {project['version']}, uv.lock says {locked}. The Apps build runs "
        "`uv sync --locked` and will fail six minutes into the deploy. Fix with:\n"
        "    uv lock --default-index https://pypi.org/simple/"
    )


def test_the_lock_resolves_against_public_pypi():
    """No internal index hosts, whatever the developer's uv config says.

    A lock written against the internal proxy passes every local gate and then
    cannot install in Databricks Apps, which has no route to that host.
    """
    hosts = set(re.findall(r"https?://([a-zA-Z0-9._-]+)/", LOCK.read_text(encoding="utf-8")))
    internal = {h for h in hosts if h.endswith("dev.databricks.com") or "artifactory" in h or "nexus" in h}
    assert not internal, (
        f"uv.lock references non-public index host(s): {sorted(internal)}. Relock with "
        "`uv lock --default-index https://pypi.org/simple/`."
    )
