"""Guards for the destroy → redeploy cycle.

Every failure these pin is silent: the deploy still exits 0, and the damage only
shows up later as an app with no permissions, a duplicated Genie space, or a
streaming job crash-looping against checkpoints for tables that no longer exist.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CLEANUP = (ROOT / "scripts" / "cleanup.sh").read_text(encoding="utf-8")
DEPLOY = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")


class TestCleanupResetsDeployManagedState:
    @pytest.mark.parametrize(
        "key",
        ["app_client_id", "lakebase_pg_user", "genie_space_id", "mvm_genie_space_id"],
    )
    def test_mutated_config_keys_are_reset(self, key):
        """deploy.sh writes these back into the local settings file.

        Left populated after a destroy, the next deploy grafts every grant onto
        a service principal that no longer exists — and reports success.
        """
        block = CLEANUP.split("KEYS = (")[1].split(")")[0]
        assert f'"{key}"' in block

    def test_genie_spaces_are_deleted_before_ids_are_reset(self):
        """Order matters: deploy.sh creates a new space whenever the stored id is
        SET_ME, so resetting without deleting leaks a space every cycle."""
        delete_at = CLEANUP.index("/api/2.0/genie/spaces/")
        reset_at = CLEANUP.index("KEYS = (")
        assert delete_at < reset_at

    def test_uc_purge_drops_the_schema_rather_than_its_tables(self):
        """Streaming checkpoints live in a UC volume INSIDE the schema.

        Dropping tables individually leaves the volume behind, and a checkpoint
        pins its source table's id — so the next deploy's streaming job fails on
        startup against tables that no longer exist.

        Scoped to the Unity Catalog block: the Lakebase purge legitimately drops
        a table (AppKit's cache lives in `public`, which is not ours to drop).
        """
        assert "DROP SCHEMA IF EXISTS" in CLEANUP
        assert "CASCADE" in CLEANUP

        uc_block = CLEANUP.split("from _uc_sql import StmtCursor")[1].split("PY")[0]
        assert "DROP SCHEMA IF EXISTS" in uc_block, "the UC purge block moved"
        assert "DROP TABLE" not in uc_block, (
            "the UC purge drops tables individually, leaving the checkpoint "
            "volume behind for the next deploy's streaming job to trip over"
        )

    def test_uc_purge_is_opt_in(self):
        """Data loss must never be the default for a teardown command."""
        assert "--purge-data" in CLEANUP
        assert 'PURGE_DATA=0' in CLEANUP

    @pytest.mark.parametrize(
        "key", ["sql_warehouse_id", "uc_catalog", "uc_schema", "mvm_catalog"]
    )
    def test_purge_reads_keys_that_actually_exist_in_the_settings_file(self, key):
        """A misspelled key reads as empty and the purge SILENTLY skips.

        `warehouse_id` vs `sql_warehouse_id` did exactly that, so this pins the
        names against the real template rather than against the script's own
        spelling of them.
        """
        import yaml

        template = yaml.safe_load(
            (ROOT / "config" / "project-settings.yaml").read_text(encoding="utf-8")
        )
        known = {k for prof in template["profiles"].values() for k in (prof or {})}
        assert key in known, f"{key} is not a real settings key"
        assert f"_field {key}" in CLEANUP

    def test_governed_schemas_are_purged_too(self):
        for schema in ("asset", "production", "quality", "inventory"):
            assert schema in CLEANUP

    def test_a_shared_governed_catalog_is_never_purged(self):
        """asset/production/quality/inventory carry no environment suffix.

        dev and prod both point at the same mvm_catalog, so one physical
        governed model serves both. Tearing down prod must not drop the schemas
        dev is actively merging into — that is silent, cross-environment data
        loss from a command whose target says "prod".
        """
        assert "MVM_PURGE_CATALOG" in CLEANUP
        assert "MVM_SHARED_WITH" in CLEANUP
        # The purge must receive the guarded variable, never the raw setting.
        purge = CLEANUP.split("Dropping ")[1]
        assert '"$MVM_PURGE_CATALOG"' in purge
        assert '"$MVM_CATALOG"' not in purge

    def test_the_share_check_compares_against_every_other_profile(self):
        """Run the real guard against the real template rather than mocking it."""
        import subprocess
        import textwrap

        import yaml

        settings = ROOT / "config" / "project-settings.yaml"
        doc = yaml.safe_load(settings.read_text(encoding="utf-8"))
        targets = [
            (p or {}).get("bundle_target") for p in (doc["profiles"] or {}).values()
        ]
        # Extract the guard's python body straight out of the script so the test
        # can't drift from what actually runs.
        body = CLEANUP.split("MVM_SHARED_WITH=$(python3 - \"$SETTINGS\" \"$TARGET\" <<'PY'\n")[1]
        body = body.split("\nPY\n")[0]
        for target in targets:
            out = subprocess.run(
                ["python3", "-c", textwrap.dedent(body), str(settings), str(target)],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            mine = next(
                p for p in doc["profiles"].values()
                if (p or {}).get("bundle_target") == target
            )
            expected = {
                str((p or {}).get("bundle_target"))
                for p in doc["profiles"].values()
                if (p or {}).get("bundle_target") != target
                and str((p or {}).get("mvm_catalog") or "").strip()
                == str((mine or {}).get("mvm_catalog") or "").strip()
                and str((mine or {}).get("mvm_catalog") or "").strip() not in ("", "SET_ME")
            }
            assert {s for s in out.split(",") if s} == expected, target

    def test_uc_purge_uses_an_interpreter_that_has_the_sdk(self):
        """System python3 usually lacks databricks-sdk, and the purge failure is
        only a WARN — so the schemas silently survive a --purge-data teardown."""
        assert ".venv/bin/python" in CLEANUP
        purge = CLEANUP.split("Dropping ")[1]
        assert '"${SDK_PY[@]}"' in purge

    def test_bundle_targets_are_regenerated_with_the_deployer_email(self):
        """Without this, `bundle destroy` fails before deleting anything.

        project-settings.yaml ships a literal `${DEPLOYER_EMAIL}` so the repo is
        forkable. Terraform reads an unsubstituted `${...}` as a resource
        reference and aborts with "Invalid reference", so a teardown that
        regenerates the targets must substitute exactly as deploy.sh does.
        """
        regen = CLEANUP.split("sync_project_settings.py")[1].split("\n\n")[0]
        assert "--deployer-email" in regen
        assert "current-user me" in CLEANUP

    def test_an_unreachable_seed_catalog_fails_before_the_setup_job(self):
        """src_catalog is optional; when set, the setup job COPIES from it.

        A stale value cost 17 minutes on prod: init_uc and the industry-model
        install both succeeded, then bootstrap_reference died with
        INSUFFICIENT_PERMISSIONS on a catalog this workspace cannot see. The
        check has to run before `bundle deploy`, not inside the job.
        """
        deploy = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")
        assert "Seed-catalog check" in deploy
        assert "databricks catalogs get" in deploy
        # Must be gated on src_catalog being non-empty — an empty value means
        # "generate in-workspace" and is the recommended default.
        guard = deploy.split("Seed-catalog check")[0]
        assert 'if [[ -n "$SRC_CATALOG"' in guard
        # And it must precede the bundle deploy it exists to protect.
        assert deploy.index("Seed-catalog check") < deploy.index("run_bundle_deploy()")

    def test_template_restore_cannot_perpetuate_a_previous_leak(self):
        """Restoring from a backup is only as good as the file backed up.

        A deploy that dies before its trap runs leaves a RENDERED template on
        disk; the next deploy backs that up and faithfully restores the
        pollution, so one missed trap pins the committed dashboard to a single
        catalog forever. Observed twice. When the file was clean against HEAD at
        start, git is the authoritative copy.
        """
        deploy = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")
        assert "_was_clean_at_start" in deploy
        assert "git -C \"$ROOT_DIR\" checkout --" in deploy
        for var in ("APP_YAML_WAS_CLEAN", "DASHBOARD_WAS_CLEAN"):
            assert var in deploy
        # And the trap must still shout if a rendered template survives.
        assert "left rendered template(s) modified" in deploy

    def test_the_public_template_generates_reference_data_by_default(self):
        """A fork must never inherit a seed catalog only we can read."""
        import yaml

        doc = yaml.safe_load(
            (ROOT / "config" / "project-settings.yaml").read_text(encoding="utf-8")
        )
        for name, prof in (doc["profiles"] or {}).items():
            assert not str((prof or {}).get("src_catalog") or "").strip(), name
            assert not str((prof or {}).get("src_schema") or "").strip(), name

    def test_teardown_does_not_render_the_dashboard_template(self):
        """`--target` renders data_quality.lvdash.json in place, replacing the
        {{UC_CATALOG}} placeholders. Only deploy.sh has the trap that restores
        them, so a teardown passing --target permanently pins the committed
        template to the catalog of the environment that was just destroyed.
        """
        regen = CLEANUP.split("sync_project_settings.py")[1].split("\n\n")[0]
        assert "--target" not in regen


class TestDeployRecoversFromAStaleServicePrincipal:
    def test_app_sp_is_resolved_every_run_not_only_when_unset(self):
        """The capture used to be gated on the stored id being empty/SET_ME, so a
        recreated app was never noticed."""
        assert "databricks apps get" in DEPLOY
        # The gate that caused the bug: capture conditional on an unset value.
        assert not re.search(
            r'if \[\[ -z "\$CURRENT_APP_SP" \|\| "\$CURRENT_APP_SP" == "SET_ME" \]\]; then\s*\n\s*echo "=== Capturing',
            DEPLOY,
        )

    def test_a_changed_app_sp_triggers_a_rewrite_and_redeploy(self):
        assert '"$CAPTURED_SP" != "$CURRENT_APP_SP"' in DEPLOY
        after = DEPLOY.split('"$CAPTURED_SP" != "$CURRENT_APP_SP"')[1]
        assert "write_app_client_id" in after
        assert "NEEDS_REDEPLOY=1" in after

    def test_an_unreadable_app_sp_falls_back_instead_of_dropping_grants(self):
        assert "falling back to stored" in DEPLOY


class TestGovernedModelGrants:
    def test_grants_run_where_the_schemas_are_created(self):
        """init_uc runs before the model exists, so the grant cannot live there."""
        installer = (ROOT / "scripts" / "install_industry_data_model.py").read_text()
        assert "def grant_app_sp_read" in installer
        assert "GRANT SELECT ON SCHEMA" in installer

        init_uc = (ROOT / "scripts" / "init_realtime_uc.py").read_text()
        assert "install_industry_data_model.py" in init_uc, "cross-reference comment"

    def test_only_populated_schemas_are_granted(self):
        """The model ships 13 schemas; the app reads the 4 the mapper fills."""
        from scripts.install_industry_data_model import POPULATED_SCHEMAS

        assert set(POPULATED_SCHEMAS) == {"asset", "production", "quality", "inventory"}

    def test_the_setup_job_passes_the_app_sp_to_the_installer(self):
        from scripts.sync_project_settings import _solution_accelerator_job

        yaml_lines = "\n".join(_solution_accelerator_job())
        installer = yaml_lines.split("install_industry_data_model.py")[1]
        installer = installer.split("task_key:")[0]
        assert "--app-client-id" in installer

    def test_grant_failure_does_not_fail_the_install(self):
        """The model is still usable by humans; a grant problem is a warning."""
        from scripts.install_industry_data_model import grant_app_sp_read

        def _boom(_sql):
            raise RuntimeError("PERMISSION_DENIED")

        grant_app_sp_read(_boom, "cat", "sp-id")  # must not raise

    def test_missing_app_sp_is_skipped_quietly(self):
        from scripts.install_industry_data_model import grant_app_sp_read

        calls = []
        grant_app_sp_read(lambda s: calls.append(s), "cat", "")
        assert calls == []


class TestSourcePinning:
    def test_industry_model_ddl_is_pinned_to_a_commit(self):
        """Tracking `main` means two users installing on different days get
        different schemas, and an upstream rename breaks the mapper silently."""
        from scripts.install_industry_data_model import DEFAULT_SOURCE_REF

        assert DEFAULT_SOURCE_REF != "main"
        assert re.fullmatch(r"[0-9a-f]{40}", DEFAULT_SOURCE_REF), DEFAULT_SOURCE_REF
