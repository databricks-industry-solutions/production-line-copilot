"""The capability inventory has to be right about what a fresh clone gets.

This is the answer to "did I deploy the whole product or a subset?", and it is
also the shipped promise: the four optional capabilities are ON in the committed
settings, so someone who clones this repo and deploys gets semantic retrieval,
the plant-document assistant, the governed model with its four Genie spaces and
the DQX checks — not a demo missing four things they never heard of.

Two failures worth pinning:

  * A capability quietly reverting to off in the shipped template. It reads as a
    one-word diff and it removes a feature from every fresh deploy.
  * MINIMAL reaching past the two capabilities that bill. Its whole purpose is
    "the product without the meter running"; if it also switched off the free
    ones, the cheap deploy would be the crippled deploy and nobody would use it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SHIPPED = ROOT / "config/project-settings.yaml"

sys.path.insert(0, str(ROOT / "scripts"))
import capabilities as cap  # noqa: E402


@pytest.fixture(params=["dev", "prod"])
def env(request):
    return request.param


def rows(env: str, minimal: bool = False, path: Path = SHIPPED) -> dict[str, dict]:
    return {r["id"]: r for r in cap.report(env, path, minimal)}


class TestWhatAFreshCloneGets:
    def test_every_optional_capability_ships_on(self, env):
        off = [r["id"] for r in rows(env).values() if not r["enabled"]]
        assert not off, (
            f"{off} ship off, so a fresh deploy of {env} silently omits them. If that "
            "is deliberate, the deploy has to offer them — see the skip messages."
        )

    def test_the_two_that_bill_are_named_as_such(self, env):
        billing = {r["id"] for r in rows(env).values() if r["bills"]}
        assert billing == {"vector_search", "knowledge_assistant"}

    def test_the_live_genie_space_reads_as_on_before_it_exists(self, env):
        """Its key holds an id the deploy captures, so it is SET_ME until then."""
        space = rows(env)["genie_space"]
        assert space["enabled"] and space["always"]
        assert space["value"] == "SET_ME", "the shipped marker changed; check the deploy gate"

    def test_each_capability_says_what_happens_without_it(self, env):
        for row in rows(env).values():
            assert row["without"].strip(), f"{row['id']} does not say what off means"


class TestMinimal:
    def test_it_skips_exactly_the_two_that_bill(self, env):
        skipped = {r["id"] for r in rows(env, minimal=True).values() if r["skipped_by_minimal"]}
        assert skipped == {"vector_search", "knowledge_assistant"}

    def test_the_free_capabilities_still_deploy(self, env):
        minimal = rows(env, minimal=True)
        for capability in ("dqx", "governed_model", "zerobus", "genie_space"):
            assert minimal[capability]["enabled"], (
                f"MINIMAL turned off {capability}, which costs nothing — a cheap deploy "
                "should not also be a lesser one"
            )

    def test_a_skipped_capability_still_reports_that_it_is_configured(self, env):
        """"Off because you asked" and "off because unset" need different fixes."""
        row = rows(env, minimal=True)["knowledge_assistant"]
        assert row["configured"] and not row["enabled"]


class TestReadingTheSettings:
    def test_an_unset_capability_is_reported_off(self, tmp_path):
        doc = yaml.safe_load(SHIPPED.read_text())
        doc["profiles"]["line_manager_dev"]["vector_search_endpoint"] = ""
        doc["profiles"]["line_manager_dev"]["dqx_enabled"] = False
        path = tmp_path / "settings.yaml"
        path.write_text(yaml.safe_dump(doc))
        got = rows("dev", path=path)
        assert not got["vector_search"]["enabled"]
        assert not got["dqx"]["enabled"]

    def test_a_placeholder_is_not_a_value(self, tmp_path):
        doc = yaml.safe_load(SHIPPED.read_text())
        doc["profiles"]["line_manager_dev"]["knowledge_assistant_name"] = "SET_ME"
        path = tmp_path / "settings.yaml"
        path.write_text(yaml.safe_dump(doc))
        assert not rows("dev", path=path)["knowledge_assistant"]["enabled"]

    def test_the_local_override_is_preferred_when_present(self, tmp_path, monkeypatch):
        """The deploy reads .local.yaml, so an inventory of the template would lie."""
        monkeypatch.setattr(cap, "ROOT", tmp_path)
        (tmp_path / "config").mkdir()
        (tmp_path / "config/project-settings.yaml").write_text("profiles: {}")
        assert cap.settings_path().name == "project-settings.yaml"
        (tmp_path / "config/project-settings.local.yaml").write_text("profiles: {}")
        assert cap.settings_path().name == "project-settings.local.yaml"


class TestTheCommandItself:
    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts/capabilities.py"), *args,
             "--settings", str(SHIPPED)],
            capture_output=True, text=True, cwd=ROOT,
        )

    def test_it_prints_the_inventory_and_exits_zero(self):
        done = self._run("dev")
        assert done.returncode == 0, done.stderr
        assert "Knowledge Assistant" in done.stdout
        assert "AI Search" in done.stdout or "Semantic retrieval" in done.stdout

    def test_a_billing_capability_is_flagged_with_its_way_out(self):
        out = self._run("dev").stdout
        assert "BILLS HOURLY" in out
        assert "MINIMAL=1" in out and "cleanup.sh" in out

    def test_the_slow_step_states_the_wait_and_that_it_does_not_block(self):
        out = self._run("dev").stdout
        assert "15-40 min" in out
        assert "does not wait" in out
        assert "VS_WAIT=1" in out

    def test_json_is_machine_readable(self):
        done = self._run("dev", "--json")
        payload = json.loads(done.stdout)
        assert payload["env"] == "dev" and payload["minimal"] is False
        assert {c["id"] for c in payload["capabilities"]} >= {
            "vector_search", "knowledge_assistant", "governed_model", "dqx"
        }

    def test_an_unknown_env_is_refused(self):
        assert self._run("staging").returncode != 0
