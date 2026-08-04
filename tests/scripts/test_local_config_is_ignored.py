"""Every operator-specific config file must be gitignored.

``render_app_yaml.py`` writes ``app.<env>.local.yaml`` for whatever environment
it is handed, and those files carry real workspace identifiers: Lakebase hosts,
service principal UUIDs, resolved deployer emails. ``.gitignore`` used to list
the environments by hand, which held for ``app.dev.local.yaml`` and missed
``app.prod.local.yaml`` — leaving the production copy as the only one a
``git add -A`` would pick up.

The repository is published to a public solution-accelerator mirror, so a
committed local config is disclosed, not merely misplaced. This test walks the
environments the renderer actually supports rather than a second hand-written
list, so adding an environment cannot reintroduce the gap.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.render_app_yaml import DEFAULT_APP_YAML, ENV_TO_BUNDLE_TARGET  # noqa: E402

# Written by the deploy path from operator-supplied values; none may be tracked.
GENERATED_LOCAL_CONFIGS = [
    "app.local.yaml",
    "config/project-settings.local.yaml",
    "config/bundle-targets.yaml",
]


def _is_ignored(relpath: str) -> bool:
    """True when git would refuse to stage `relpath`."""
    return (
        subprocess.run(
            ["git", "check-ignore", "-q", "--no-index", relpath],
            cwd=ROOT,
            check=False,
        ).returncode
        == 0
    )


@pytest.mark.parametrize("env", sorted(set(ENV_TO_BUNDLE_TARGET) | set(DEFAULT_APP_YAML)))
def test_per_env_local_app_yaml_is_ignored(env: str) -> None:
    relpath = f"app.{env}.local.yaml"
    assert _is_ignored(relpath), (
        f"{relpath} is not gitignored. render_app_yaml.py writes this file with real "
        f"workspace identifiers, and this repo is mirrored publicly. Prefer the "
        f"app.*.local.yaml glob over enumerating environments."
    )


@pytest.mark.parametrize("relpath", GENERATED_LOCAL_CONFIGS)
def test_generated_local_configs_are_ignored(relpath: str) -> None:
    assert _is_ignored(relpath), f"{relpath} carries operator values and must be gitignored"


def test_the_committed_templates_are_still_tracked() -> None:
    """The glob must not swallow the placeholder templates operators copy from."""
    for template in ("app.yaml", "app.dev.yaml"):
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", template],
            cwd=ROOT,
            check=False,
            capture_output=True,
        )
        assert tracked.returncode == 0, (
            f"{template} is no longer tracked — an over-broad ignore rule would strip the "
            f"SET_ME templates the README tells operators to copy."
        )
