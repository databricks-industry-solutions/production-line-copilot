"""Static guards on the redeploy paths in scripts/deploy.sh.

`render_app_yaml.py` writes ``app.<env>.local.yaml``, but the bundle uploads
``app.yaml`` — so a re-render only reaches the running app if
``do_app_yaml_swap`` runs between the render and the bundle deploy.

Skipping the swap fails silently in the worst way: the deploy reports success,
the app restarts, and it comes up with the env var still unset. That happened
once with the governed domain space ids, so the ordering is pinned here rather
than left to reviewer memory. Shell is hard to unit-test; reading the source is
worth more than nothing.
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


def _code_lines(lines: list[str]) -> list[tuple[int, str]]:
    """Line numbers and text, skipping comments and blanks."""
    out = []
    for i, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if stripped and not stripped.startswith("#"):
            out.append((i, stripped))
    return out


def test_every_rerender_swaps_before_deploying(lines):
    """A render followed by a bundle deploy must have a swap in between."""
    code = _code_lines(lines)
    renders = [n for n, text in code if "render_app_yaml.py" in text]
    assert renders, "no render_app_yaml.py calls found — has deploy.sh moved?"

    for render_line in renders:
        # The next bundle deploy after this render, if any.
        following = [n for n, text in code if n > render_line and "run_bundle_deploy" in text]
        if not following:
            continue
        deploy_line = following[0]
        swaps = [
            n
            for n, text in code
            if render_line < n < deploy_line and "do_app_yaml_swap" in text
        ]
        assert swaps, (
            f"render_app_yaml.py at line {render_line} is followed by "
            f"run_bundle_deploy at line {deploy_line} with no do_app_yaml_swap "
            "between them — the redeploy would ship the pre-render app.yaml"
        )


def test_domain_genie_block_provisions_all_four_domains(lines):
    """All four governed domains must be in the provisioning loop."""
    text = "\n".join(lines)
    match = re.search(r"for DOMAIN in ([a-z ]+); do", text)
    assert match, "the per-domain Genie provisioning loop is missing"
    assert match.group(1).split() == ["asset", "quality", "inventory", "production"]


def test_domain_spaces_are_granted_to_the_app_sp(lines):
    """A space the app SP cannot run is a space the specialist cannot query."""
    text = "\n".join(lines)
    block = text.split("for DOMAIN in", 1)[1]
    assert "permissions/genie/$DOMAIN_ID" in block
    assert "CAN_RUN" in block.split("permissions/genie/$DOMAIN_ID")[0][-400:]


def test_newly_created_domain_spaces_trigger_a_redeploy(lines):
    """Ids captured after the app deployed only reach it via a second deploy."""
    text = "\n".join(lines)
    assert "DOMAIN_SPACES_CREATED=1" in text, "creation must flag that a redeploy is needed"
    block = text.split('if [[ "$DOMAIN_SPACES_CREATED" == "1" ]]; then', 1)
    assert len(block) == 2, "no redeploy guarded on DOMAIN_SPACES_CREATED"
    assert "databricks apps deploy" in block[1]
