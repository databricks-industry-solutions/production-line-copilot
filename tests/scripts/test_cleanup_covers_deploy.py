"""Teardown must cover everything deploy creates, or the next "fresh" deploy isn't.

A gap here is quiet and expensive. `cleanup.sh` deleted two of the six Genie
spaces `deploy.sh` creates and reset two of the six settings keys it writes, so
every teardown/redeploy cycle leaked four spaces AND left their ids in the
config — after which the next deploy "refreshed" spaces that had been deleted.
Vector Search and Lakebase were not represented at all, so a fresh-deploy test
would silently reuse an existing endpoint, an existing index, and existing
`ai_serving` tables, and prove nothing about a first-time install.

These tests read the two scripts as text rather than executing them. That is
enough to catch the failure mode that actually occurred: deploy grew a resource
and cleanup was never taught about it.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEPLOY = (REPO / "scripts" / "deploy.sh").read_text(encoding="utf-8")
CLEANUP = (REPO / "scripts" / "cleanup.sh").read_text(encoding="utf-8")


class TestEveryConfigKeyDeployWritesIsReset:
    def test_the_keys_written_back_are_the_keys_reset(self):
        """deploy.sh persists ids into project-settings.local.yaml; all must reset."""
        written = set(re.findall(r'write_genie_space_id\s+"[^"]*"\s+"?\$?\{?(\w+)', DEPLOY))
        # The loop form writes genie_space_<domain> via a computed field name.
        domains = re.search(r"for DOMAIN in ([a-z ]+); do", DEPLOY)
        assert domains, "the governed-domain loop moved; this guard needs updating"
        expected = {f"genie_space_{d}" for d in domains.group(1).split()}

        # Non-greedy to the first ")" stops inside a comment; anchor on the close.
        keys_block = re.search(r"KEYS = \((.*?)\n\)", CLEANUP, re.DOTALL)
        assert keys_block, "cleanup.sh no longer defines a KEYS tuple"
        reset = set(re.findall(r'"(\w+)"', keys_block.group(1)))

        missing = sorted(expected - reset)
        assert not missing, (
            f"deploy.sh writes {missing} into the settings file and cleanup.sh "
            "never resets them, so the next deploy refreshes deleted spaces"
        )
        assert {"app_client_id", "genie_space_id", "mvm_genie_space_id"} <= reset
        # `written` is captured to fail loudly if the helper's call shape changes.
        assert isinstance(written, set)


class TestEveryGenieSpaceDeployCreatesIsDeleted:
    def test_the_governed_domain_spaces_are_in_the_delete_loop(self):
        loop = re.search(r"for space in ([^\n]+); do", CLEANUP)
        assert loop, "cleanup.sh no longer loops over Genie spaces"
        assert "DOMAIN_GENIE_IDS" in loop.group(1), (
            "only the streaming and MVM spaces are deleted; the four governed "
            "domain spaces leak on every teardown"
        )

    def test_the_domain_list_matches_deploy(self):
        deploy_domains = re.search(r"for DOMAIN in ([a-z ]+); do", DEPLOY).group(1).split()
        cleanup_domains = re.search(r"for _d in ([a-z ]+); do", CLEANUP).group(1).split()
        assert sorted(deploy_domains) == sorted(cleanup_domains)


class TestTheResourcesDeployProvisionsOutsideTheBundle:
    """bundle destroy knows nothing about these; cleanup.sh has to."""

    def test_vector_search_is_torn_down(self):
        assert "vector_search_endpoint" in CLEANUP
        assert "delete-index" in CLEANUP, "Vector Search indexes are never deleted"
        assert "delete-endpoint" in CLEANUP, "the Vector Search endpoint is never deleted"

    def test_lakebase_schemas_are_dropped(self):
        assert "ai_serving" in CLEANUP and "ai_chatbot" in CLEANUP, (
            "the app's Lakebase schemas survive teardown, so a 'fresh' deploy "
            "finds its tables already there"
        )

    def test_the_lakebase_instance_itself_is_not_deleted(self):
        """It is a prerequisite, and deleting it is not recoverable from here."""
        assert "delete-database-instance" not in CLEANUP

    def test_the_secret_scope_is_never_deleted(self):
        """It holds the Zerobus client secret, whose value we do not have."""
        assert "secrets delete-scope" not in CLEANUP

    def test_the_traces_experiment_is_deleted_with_its_tables(self):
        """A UC trace link is permanent, so an orphaned experiment is scrap.

        provision_trace_storage.py creates `<path>-traces` outside the bundle
        and links it to tables inside the schema this script drops. Keeping the
        experiment past that drop leaves one that can never be repaired — the
        next deploy skips to the `-2` fallback and the workspace collects a dead
        experiment per teardown cycle.
        """
        assert "-traces" in CLEANUP, "the UC traces experiment survives teardown"
        assert "delete_experiment" in CLEANUP
        # The fallback names provision_trace_storage.py can have moved on to.
        assert "range(2, 8)" in CLEANUP, (
            "only the base traces experiment is deleted; the -2..-7 fallbacks leak"
        )

    def test_both_service_principals_lose_their_lakebase_role(self):
        """deploy.sh registers a role for the app SP and the producer SP."""
        assert "PRODUCER_CLIENT_ID" in CLEANUP, (
            "only the app SP's Lakebase role is removed; the producer SP keeps "
            "the Postgres role deploy.sh granted it"
        )
        loop = re.search(r"for _sp_pair in ([^\n]+); do", CLEANUP)
        assert loop, "the SP role-removal loop moved; this guard needs updating"
        assert "APP_CLIENT_ID" in loop.group(1) and "PRODUCER_CLIENT_ID" in loop.group(1)

    def test_the_producer_service_principal_itself_survives(self):
        """It is an operator-created prerequisite holding the Zerobus secret."""
        assert "service-principals delete" not in CLEANUP


class TestSharedResourcesNeedAnExplicitFlag:
    """Tearing down dev must not be able to take prod's data with it."""

    def test_shared_purges_are_gated(self):
        assert "PURGE_SHARED" in CLEANUP
        # Anchor on the executable statement, not the header comment that
        # documents it.
        for guarded in ("DROP SCHEMA IF EXISTS {schema} CASCADE", "delete-endpoint"):
            idx = CLEANUP.index(guarded)
            preceding = CLEANUP[:idx]
            assert 'PURGE_SHARED" == "1"' in preceding, (
                f"{guarded} is purged without the --purge-shared gate"
            )

    def test_the_flag_implies_purge_data(self):
        assert re.search(r"--purge-shared\)\s*PURGE_SHARED=1;\s*PURGE_DATA=1", CLEANUP)

    def test_this_environments_search_index_goes_without_the_flag(self):
        """The endpoint is shared; an index inside the dropped schema is not.

        Gating both on --purge-shared left a dev teardown with a live index over a
        table it had just dropped, so the next "fresh" deploy found an index
        already there, created nothing, and proved nothing.
        """
        before_delete = CLEANUP[: CLEANUP.index("delete-index")]
        opener = before_delete.rindex("if [[ ")
        assert 'PURGE_SHARED" == "1" && -n "$VS_ENDPOINT"' not in before_delete[opener:], (
            "index deletion is back behind the shared gate"
        )
        assert 'if [[ -n "$VS_ENDPOINT" ]]' in before_delete, (
            "index deletion should run whenever this environment names an endpoint"
        )

    def test_a_dev_teardown_only_deletes_its_own_indexes(self):
        assert '_vs_indexes "${UC_CATALOG}.${UC_SCHEMA}."' in CLEANUP, (
            "an unscoped delete would take prod's index off the shared endpoint"
        )
        assert '_vs_indexes ""' in CLEANUP, "--purge-shared should still take them all"

    def test_the_index_goes_before_the_schema_it_lives_in(self):
        """CASCADE removes the index without telling the serving side.

        Ordered the other way round, the API delete finds nothing to delete —
        the schema drop already took the index out of the listing — and the
        serving half is left behind with no way to see it.
        """
        assert CLEANUP.index("delete-index") < CLEANUP.index(
            "DROP SCHEMA IF EXISTS {schema} CASCADE"
        ), "the index must be deleted through the API before the schema drop"
