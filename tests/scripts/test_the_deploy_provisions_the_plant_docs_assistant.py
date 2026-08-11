"""The deploy has to build the Knowledge Assistant, and the teardown has to remove it.

Read as text, like the other deploy guards — enough to catch the failure that
actually recurs in this repo: a step is added to ``deploy.sh`` and ``cleanup.sh``
is never taught about it, so the next "fresh" deploy finds the resource already
there and proves nothing.

The specific things pinned here:

  * The step is opt-in and says how to opt in. A silent skip reads as a broken
    feature to whoever set the corpus up but not the config key.
  * A failure warns instead of aborting. The assistant is one tool on three
    specialists; the rest of the deploy is the product.
  * The two values the provisioning script prints are written back, because the
    app reads them from settings and nothing else produces them.
  * Teardown deletes the assistant (it serves from an endpoint that bills) and
    resets both keys (or the next deploy syncs an assistant that is gone).
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEPLOY = (REPO / "scripts" / "deploy.sh").read_text(encoding="utf-8")
CLEANUP = (REPO / "scripts" / "cleanup.sh").read_text(encoding="utf-8")


class TestTheDeployStep:
    def test_the_provisioning_script_is_called(self):
        assert "scripts/provision_knowledge_assistant.py" in DEPLOY

    def test_it_is_gated_on_the_assistant_name_being_configured(self):
        assert "knowledge_assistant_name" in DEPLOY
        idx = DEPLOY.index("scripts/provision_knowledge_assistant.py")
        assert "KA_NAME" in DEPLOY[:idx], "the call is not guarded by the config key"

    def test_an_unconfigured_deploy_says_how_to_turn_it_on(self):
        block = DEPLOY[DEPLOY.index("provision_knowledge_assistant.py"):]
        assert "knowledge_assistant_name" in block
        assert "project-settings.local.yaml" in block

    def test_a_failure_warns_rather_than_aborting_the_deploy(self):
        block = DEPLOY[DEPLOY.index("scripts/provision_knowledge_assistant.py"):][:900]
        assert "WARN" in block
        assert "exit 1" not in block

    def test_both_values_are_written_back_into_the_settings_file(self):
        for key in ("knowledge_assistant_endpoint", "knowledge_assistant_id"):
            assert re.search(rf'write_genie_space_id\s+"[^"]*"\s+{key}', DEPLOY), (
                f"{key} is never persisted, so the app never learns it"
            )

    def test_the_app_is_rewired_after_a_first_provision(self):
        """The endpoint lands in settings after the app was already deployed."""
        block = DEPLOY[DEPLOY.index("scripts/provision_knowledge_assistant.py"):][:2600]
        assert "render_app_yaml.py" in block, (
            "the new endpoint never reaches the running app, so the tool stays inert "
            "until someone deploys again"
        )

    def test_the_rewire_is_decided_on_what_the_app_has_not_what_settings_say(self):
        """Provisioning writes the endpoint into settings itself.

        Comparing against settings therefore compares the new endpoint with a copy
        of itself the moment anyone provisions outside a full deploy, so the app
        never gets re-wired and query_plant_docs stays unconfigured in a deploy
        that reports success. The app template is what the running app was built
        from, so that is the side worth comparing.
        """
        idx = DEPLOY.index("scripts/provision_knowledge_assistant.py")
        guard = re.search(r'if \[\[ "\$KA_ENDPOINT_NEW" != "\$(\w+)" \]\]', DEPLOY[idx:])
        assert guard, "the re-wire is no longer guarded by an endpoint comparison"
        other = guard.group(1)
        assignment = re.search(rf"^{other}=(.*)$", DEPLOY, re.MULTILINE)
        assert assignment, f"{other} is compared but never assigned"
        assert "_profile_field" not in assignment.group(1), (
            "compared against settings, which provisioning has already updated"
        )
        assert "APP_SOURCE_YAML" in assignment.group(1)
        assert "DOCS_KA_ENDPOINT" in assignment.group(1)


class TestTheTeardown:
    def test_the_assistant_is_deleted(self):
        assert "delete-knowledge-assistant" in CLEANUP, (
            "the assistant survives teardown and keeps serving from a billing endpoint"
        )

    def test_both_keys_are_reset(self):
        keys_block = re.search(r"KEYS = \((.*?)\n\)", CLEANUP, re.DOTALL)
        assert keys_block, "cleanup.sh no longer defines a KEYS tuple"
        reset = set(re.findall(r'"(\w+)"', keys_block.group(1)))
        assert {"knowledge_assistant_endpoint", "knowledge_assistant_id"} <= reset
