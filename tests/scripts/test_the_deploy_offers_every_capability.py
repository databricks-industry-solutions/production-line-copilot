"""A deploy has to say what it built, what it skipped, and what that costs.

Read as text like the other deploy guards, plus one real invocation of the
read-only path. What is pinned here is the contract a fresh clone relies on:

  * ``--capabilities`` answers "what would this build?" without building it, and
    without touching a file on the way — it used to be that the only way to find
    out was to run the deploy and read the log.
  * The two capabilities that bill can be skipped in one switch (MINIMAL=1), and
    the skip says what the app does instead. A cost you cannot avoid in one step
    is a cost people avoid by not deploying at all.
  * The slow one does not block. Creating an AI Search endpoint takes 15-40
    minutes; the deploy starts it and lets the next run build the index, so the
    first deploy of a fresh workspace finishes in minutes. VS_WAIT=1 is there for
    the run that has to come out of it complete.
  * "Still provisioning" is not reported as a failure. It is the expected outcome
    of not waiting, and a deploy that calls it a failure trains people to ignore
    its failures.
  * The governed model announces itself when off. It was the one capability with
    no message on either branch, so a fresh deploy never mentioned the four
    domain Genie spaces at all.
  * Every branch records an outcome, and the outcomes are printed together at the
    end. Individually they scroll past hundreds of lines before anyone asks.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEPLOY = (REPO / "scripts" / "deploy.sh").read_text(encoding="utf-8")

# The header comment names most of the deploy's own steps, so ordering checks
# have to look at the code and not at the documentation of the code.
CODE = "\n".join(
    line for line in DEPLOY.splitlines() if not line.lstrip().startswith("#")
)


def _block(anchor: str, length: int = 1800) -> str:
    return DEPLOY[DEPLOY.index(anchor):][:length]


class TestTheReadOnlyInventory:
    def test_the_flag_is_documented_in_usage(self):
        assert "--capabilities" in DEPLOY
        usage = DEPLOY[: DEPLOY.index("SKIP_APP=0")]
        assert "MINIMAL=1" in usage and "VS_WAIT=1" in usage

    def test_it_exits_before_anything_can_be_touched(self):
        """Asking what a deploy would do is not asking for a deploy."""
        exit_at = CODE.index("exec python3 scripts/capabilities.py")
        for later in (
            'if [[ -f "$ROOT_DIR/uv.lock" ]]',      # rewrites uv.lock in place
            "python3 scripts/sync_project_settings.py",
            "run_bundle_deploy() {",
        ):
            assert CODE.index(later) > exit_at, (
                f"{later!r} runs before the --capabilities exit, so a read-only "
                "question has side effects"
            )

    def test_running_it_prints_the_inventory_and_changes_nothing(self):
        before = {
            path: path.stat().st_mtime
            for path in (REPO / "uv.lock", REPO / "app.dev.yaml",
                         REPO / "config/project-settings.yaml")
            if path.exists()
        }
        done = subprocess.run(
            ["bash", str(REPO / "scripts/deploy.sh"), "dev", "--capabilities"],
            capture_output=True, text=True, cwd=REPO,
        )
        assert done.returncode == 0, done.stderr
        assert "Capabilities for dev" in done.stdout
        assert "Knowledge Assistant" in done.stdout
        assert {p: p.stat().st_mtime for p in before} == before, "it modified a file"

    def test_the_plan_is_printed_before_the_work(self):
        plan_at = DEPLOY.index("capabilities.py")
        assert plan_at < DEPLOY.index("run_bundle_deploy()")


class TestMinimal:
    def test_it_skips_the_search_endpoint_and_says_what_replaces_it(self):
        block = _block('if [[ "${MINIMAL:-0}" == "1" && -n "$VS_ENDPOINT_NAME" ]]', 700)
        assert "keyword overlap" in block
        assert "provision_vector_search.py" not in block

    def test_it_skips_the_assistant_and_says_what_it_costs_you(self):
        block = _block('if [[ "${MINIMAL:-0}" == "1" && -n "$KA_NAME"', 700)
        assert "documents were not read" in block
        assert "provision_knowledge_assistant.py" not in block

    def test_it_does_not_gate_the_capabilities_that_cost_nothing(self):
        """DQX and the governed model are free; a cheap deploy keeps them."""
        governed = _block("MVM_SILVER=$(python3", 2000)
        assert "MINIMAL" not in governed


class TestTheSlowStepDoesNotBlock:
    def test_wait_is_opt_in_rather_than_hardcoded(self):
        call = _block("scripts/provision_vector_search.py \\", 400)
        assert "--wait" not in call, "the deploy blocks for 15-40 minutes again"
        assert "VS_WAIT_FLAG" in call

    def test_the_flag_is_set_from_the_environment_switch(self):
        block = _block('VS_WAIT_FLAG=""', 200)
        assert 'VS_WAIT:-0' in block and "--wait" in block

    def test_the_deploy_says_the_index_arrives_on_the_next_run(self):
        block = _block('echo "    NOTE: creating the endpoint takes', 400)
        assert "NEXT deploy" in block
        assert "VS_WAIT=1" in block

    def test_still_provisioning_is_not_reported_as_a_failure(self):
        block = _block('if [[ "$VS_RC" == "0" ]]', 900)
        assert '"$VS_RC" == "3"' in block, "exit 3 is not distinguished from a failure"
        third = block[block.index('"$VS_RC" == "3"'):]
        assert "nothing is wrong" in third
        assert "WARN" not in third[: third.index("else")]

    def test_the_provisioner_still_uses_that_exit_code(self):
        """The two halves of this contract live in different files."""
        script = (REPO / "scripts/provision_vector_search.py").read_text()
        assert "return 0 if args.dry_run else 3" in script


class TestTheGovernedModel:
    def test_being_off_is_announced_with_what_is_lost(self):
        block = _block('elif [[ "$MVM_SILVER" != "true" ]]', 900)
        assert "mvm_silver_enabled" in block
        assert "sql_query" in block, "it does not say what the specialists do instead"
        assert "project-settings.local.yaml" in block, "it does not say how to turn it on"

    def test_an_empty_catalog_falls_back_to_the_live_one(self):
        """It ships empty, and the settings comment promises this fallback."""
        block = DEPLOY[DEPLOY.index("MVM_CAT=$(_profile_field mvm_catalog)"):][:600]
        assert "_profile_field uc_catalog" in block, (
            "silver on with a blank mvm_catalog creates no domain spaces"
        )


class TestTheSummary:
    def test_every_capability_branch_records_an_outcome(self):
        # 3 states each (on / off / misconfigured) for search and the assistant,
        # plus the two MINIMAL skips, plus the governed model's three.
        assert DEPLOY.count("note_capability ") >= 11

    def test_the_outcomes_are_printed_together_at_the_end(self):
        block = _block("=== Capabilities ===", 500)
        assert "CAPABILITY_OUTCOMES[@]" in block
        assert "--capabilities" in block, "the summary does not point at the full inventory"

    def test_the_summary_survives_a_deploy_that_recorded_nothing(self):
        """bash 3.2 aborts on an empty array under set -u."""
        block = DEPLOY[DEPLOY.index("=== Capabilities ===") - 300:]
        assert "${#CAPABILITY_OUTCOMES[@]} > 0" in block

    def test_the_helper_is_defined_before_the_first_capability_uses_it(self):
        assert DEPLOY.index("note_capability() {") < DEPLOY.index("note_capability \"")


class TestItRunsUnderTheShellItShipsWith:
    def test_the_script_parses(self):
        done = subprocess.run(["bash", "-n", str(REPO / "scripts/deploy.sh")],
                              capture_output=True, text=True)
        assert done.returncode == 0, done.stderr

    def test_the_inventory_script_parses_too(self):
        done = subprocess.run([sys.executable, "-m", "py_compile",
                               str(REPO / "scripts/capabilities.py")],
                              capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
