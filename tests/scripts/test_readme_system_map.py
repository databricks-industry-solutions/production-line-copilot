"""The README's System Map image must still describe this app.

A screenshot is the one artefact that rots without saying so: the app grows a
specialist or a governed domain, the picture on the front page quietly keeps
describing last quarter's architecture, and nothing fails.

What can be checked is the data the picture was drawn from.
``scripts/dev/capture_system_map.mjs`` renders the real ``SystemMap`` component
against the fixtures in ``scripts/dev/system_map_preview/``, so those fixtures
are the image's content in machine-readable form. Pinning them to the app's own
wiring — ``DOMAIN_ENV`` and ``_DOMAIN_OWNERS``, the same values the live
endpoint serves — means adding a domain or moving a specialist fails here, and
the fix is to re-run the capture script.

The pixels are not compared. This asserts the picture was drawn from the truth,
not that nobody has restyled a border.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_server.start_server import _DOMAIN_OWNERS
from agent_server.tools.governed_domain_query import DOMAIN_ENV

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
FIXTURES = ROOT / "scripts" / "dev" / "system_map_preview"
IMAGES = ("docs/media/system_map.png", "docs/media/system_map_dark.png")


def _topology() -> dict:
    return json.loads((FIXTURES / "topology.json").read_text())


def _domains() -> dict[str, dict]:
    return {d["domain"]: d for d in _topology()["domains"]}


# --------------------------------------------------------------------------- #
# the image is actually wired into the README
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("image", IMAGES)
def test_the_readme_references_the_image(image):
    assert image in README.read_text(), (
        f"{image} is not referenced from README.md. Both are needed: the app "
        "follows prefers-color-scheme and GitHub serves whichever matches."
    )


@pytest.mark.parametrize("image", IMAGES)
def test_the_image_exists_and_is_not_a_stub(image):
    path = ROOT / image
    assert path.exists(), (
        f"{image} is referenced by README.md but missing. Regenerate with "
        "`node scripts/dev/capture_system_map.mjs`."
    )
    assert path.stat().st_size > 20_000, (
        f"{image} is {path.stat().st_size} bytes, which is too small to be the "
        "rendered map — the capture probably shot an empty or errored page."
    )


# --------------------------------------------------------------------------- #
# the fixtures the image was drawn from still match the app
# --------------------------------------------------------------------------- #
def test_the_fixture_covers_every_governed_domain():
    assert set(_domains()) == set(DOMAIN_ENV), (
        "the captured topology does not describe the same governed domains the "
        "app serves. Re-run scripts/dev/capture_system_map.mjs against a "
        "current deployment."
    )


@pytest.mark.parametrize("domain", sorted(DOMAIN_ENV))
def test_the_captured_domain_was_provisioned(domain):
    """A screenshot of a half-provisioned workspace undersells the app."""
    assert _domains()[domain]["configured"], (
        f"the {domain} domain reads as unprovisioned in the captured topology, "
        "so the README image draws it as SQL-only. Recapture against a "
        "deployment where all four domains exist."
    )


@pytest.mark.parametrize("domain", sorted(_DOMAIN_OWNERS))
def test_each_domain_is_captured_with_its_real_owners(domain):
    assert sorted(_domains()[domain]["specialists"]) == sorted(_DOMAIN_OWNERS[domain]), (
        f"the {domain} domain is owned by {sorted(_DOMAIN_OWNERS[domain])} but "
        f"the image was drawn showing {sorted(_domains()[domain]['specialists'])}."
    )


def test_the_line_ops_space_is_in_the_picture():
    assert _topology()["streaming_space"], (
        "the captured topology has no space over the live streaming tables, so "
        "the 'line ops genie' chips are missing from the README image"
    )


def test_every_role_has_a_model_in_the_picture():
    """Empty model tags would make the map look half-configured."""
    roles = json.loads((FIXTURES / "models.json").read_text()).get("roles", [])
    assert roles, "the captured model roster is empty"
    missing = [r["role"] for r in roles if not r.get("display")]
    assert not missing, f"these roles were captured without a model name: {missing}"
