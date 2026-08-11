"""A deploy this long has to prove it can authenticate before it starts.

``databricks current-user me`` proves the CLI can authenticate. It does not
prove ``bundle deploy`` can, because that spawns Terraform, which authenticates
in its own process — and on macOS the two really do differ: a U2M OAuth token
cached in the keyring is readable by the signed ``databricks`` binary and not by
the provider Terraform spawns.

Both halves of this have happened here. A deploy passed its own auth check, ran
the 19-minute setup job, created the AI Search index and built the Knowledge
Assistant, and then died on the last bundle pass with "the refresh token is
invalid" — because the OAuth session is shared, something else refreshed it, and
the copy the deploy held rotated out from under it. What that pass does is tell
the app which assistant endpoint to query. So the run ended with an agent
endpoint that was up and billing, an app that never queried it, and a summary
that would have called it "serving".

So: probe the path Terraform uses, before anything is touched; explain an auth
failure instead of printing one Terraform stanza per resource; and never report
the assistant as serving unless the app was actually given it.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEPLOY = (REPO / "scripts" / "deploy.sh").read_text(encoding="utf-8")

# The header comments describe the deploy's own steps, so ordering has to be
# checked against the code and not against the prose about the code.
CODE = "\n".join(line for line in DEPLOY.splitlines() if not line.lstrip().startswith("#"))


class TestThePreflight:
    def test_it_probes_the_path_terraform_uses(self):
        """``bundle plan`` runs the same provider; ``current-user me`` does not."""
        assert "bundle plan --target" in CODE

    def test_it_runs_before_anything_is_deployed(self):
        plan = CODE.index("bundle plan --target")
        for later in ("run_bundle_deploy()", "apps deploy", "RUN_SETUP=1"):
            assert plan < CODE.index(later), (
                f"the auth probe must come before {later!r} — the point is to fail in "
                "seconds rather than after the setup job"
            )

    def test_only_an_auth_failure_is_fatal(self):
        """A plan can fail for reasons a deploy then fixes.

        This is a probe for one blind spot, not a new gate, so the non-auth
        branch has to keep going.
        """
        block = DEPLOY[DEPLOY.index('if databricks bundle plan') :][:1200]
        assert "_auth_failure_in" in block
        assert "continuing" in block

    def test_it_names_both_causes_and_their_fixes(self):
        explain = DEPLOY[DEPLOY.index("_explain_auth_failure()") :][:1800]
        assert "databricks auth login --profile" in explain, "re-login on the default cache"
        assert "PAT" in explain or "client_secret" in explain, (
            "a credential that does not rotate, for a deploy that outlives a session"
        )

    def test_it_does_not_recommend_the_storage_switch_as_the_fix(self):
        """It cannot be the fix: the provider never receives that variable.

        Terraform is handed host, account, workspace and profile — nothing else —
        so it reads the default cache whatever the CLI was told. Switching
        storage and logging in there actively breaks the deploy, by invalidating
        the refresh token in the copy the provider is using. Recommending it cost
        an hour here, twice.
        """
        explain = DEPLOY[DEPLOY.index("_explain_auth_failure()") :][:1800]
        assert "Do NOT set DATABRICKS_AUTH_STORAGE" in explain


class TestAFailureMidDeploy:
    def test_the_bundle_deploy_explains_an_auth_failure(self):
        helper = DEPLOY[DEPLOY.index("run_bundle_deploy()") :][:3000]
        assert "_auth_failure_in" in helper and "_explain_auth_failure" in helper

    def test_it_says_that_re_running_resumes(self):
        """Every step is idempotent, and nobody reads that from six stanzas."""
        helper = DEPLOY[DEPLOY.index("run_bundle_deploy()") :][:3000]
        assert "re-run" in helper.lower()
        assert "resumes" in helper.lower() or "rebuilds nothing" in helper.lower()


class TestTheAssistantIsNotReportedAsServingUnlessItIs:
    def test_the_summary_checks_the_app_was_given_the_endpoint(self):
        block = DEPLOY[DEPLOY.index("KA_WIRED=1") :][:2200]
        assert "$APP_SOURCE_YAML" in block, (
            "the claim should be read back off the rendered template, not assumed "
            "from having written it"
        )

    def test_a_failed_wiring_says_so(self):
        block = DEPLOY[DEPLOY.index("KA_WIRED=1") :][:2200]
        assert "BUILT on" in block and "bills meanwhile" in block, (
            "an assistant that is up but unqueried costs money and looks like success"
        )

    def test_the_wiring_result_is_not_discarded(self):
        block = DEPLOY[DEPLOY.index("KA_WIRED=1") :][:2200]
        assert "if ! run_bundle_deploy; then KA_WIRED=0" in block, (
            "the redeploy's exit status is what tells us whether the app got it"
        )


class TestBuiltIsNotTheSameAsAnswering:
    """Wiring the endpoint into the app was necessary and turned out not to be
    sufficient. A cold deploy wired an assistant whose ingest had failed on the
    org's embedding quota, and the summary called it serving: the endpoint was up,
    the app had it, and every document question came back ungrounded. The corpus
    also takes the best part of an hour to index on a first build, so "up" and
    "answering from the documents" are hours apart even when nothing goes wrong.
    """

    def test_the_summary_reads_the_state_the_script_reports(self):
        assert "knowledge_assistant_state=" in DEPLOY, (
            "only the provisioning script reads the build state; the summary has to "
            "be told, not guess from the endpoint name"
        )

    def test_an_indexing_assistant_is_not_called_answering(self):
        block = DEPLOY[DEPLOY.index("KA_WIRED=1") :][:2600]
        assert 'KA_STATE" == "indexing"' in block
        assert "still indexing" in block and "ungrounded" in block, (
            "someone reading the summary will go and ask it a question"
        )

    def test_a_provisioning_failure_is_not_reported_as_building(self):
        """Waiting fixes one of those and not the other."""
        block = DEPLOY[DEPLOY.index("KA_WIRED=1") :][:2600]
        assert "KA_FAILED == 1" in block and "provisioning FAILED" in block
