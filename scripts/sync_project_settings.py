#!/usr/bin/env python3
"""Regenerate config/bundle-targets.yaml from config/project-settings.yaml.

Each profile sets ``zerobus_ingest_enabled``:
  - false → bundle target gets UC setup job only (workshop / no Zerobus).
  - true  → setup job + Zerobus streaming producer + continuous streaming
            quality-gate job (streaming_quality_sync).

Usage:
  python scripts/sync_project_settings.py
  python scripts/sync_project_settings.py --check   # verify files are in sync (CI)

Edits only config/bundle-targets.yaml (and optionally prints app env hints).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Public-template / operator-local config split.
#
# - config/project-settings.yaml ships in git with SET_ME placeholders.
# - config/project-settings.local.yaml is gitignored and carries the real
#   workspace identifiers, SP UUIDs, Lakebase hosts, etc.
#
# Prefer the .local.yaml when present so deployers' real values never need
# to land in git. Fall back to the committed file when no .local.yaml exists
# (e.g. CI ``--check`` runs against the public template) — in that mode the
# generated bundle-targets.yaml carries SET_ME placeholders too, and any
# deploy step will fail fast on the missing values.
_SETTINGS_LOCAL = ROOT / "config" / "project-settings.local.yaml"
_SETTINGS_DEFAULT = ROOT / "config" / "project-settings.yaml"
SETTINGS_PATH = _SETTINGS_LOCAL if _SETTINGS_LOCAL.is_file() else _SETTINGS_DEFAULT
TARGETS_PATH = ROOT / "config" / "bundle-targets.yaml"
DASHBOARD_PATH = ROOT / "dashboards" / "data_quality.lvdash.json"

UC_CATALOG_TOKEN = "{{UC_CATALOG}}"
UC_SCHEMA_TOKEN = "{{UC_SCHEMA}}"

# Public-release templating: project-settings.yaml ships with `${DEPLOYER_EMAIL}`
# in fields that are deployer-specific (lakebase_syncer_pg_user, a personal
# /Users/<email>/ MLflow path, etc.). We resolve it here so
# the generated bundle-targets.yaml carries a concrete email — bundle vars
# don't expand `${DEPLOYER_EMAIL}` themselves, and the downstream Lakebase /
# MLflow APIs need a real user.
DEPLOYER_EMAIL_TOKEN = "${DEPLOYER_EMAIL}"


def _resolve_deployer_email(explicit: str | None) -> str | None:
    """Resolve the deployer email used to substitute `${DEPLOYER_EMAIL}`.

    Only resolves when ``explicit`` is provided. We deliberately do NOT
    fall back to the DEPLOYER_EMAIL env var or `databricks current-user me`
    from this helper, so direct invocations and CI ``--check`` runs are
    deterministic — the committed ``bundle-targets.yaml`` should always carry
    the literal placeholder. ``deploy.sh`` is the one place that's expected
    to call us with ``--deployer-email``; that path produces a substituted
    bundle-targets.yaml on a backed-up working copy that the trap restores
    on exit, so the committed file stays clean.
    """
    if not explicit:
        return None
    return explicit.strip() or None


def _substitute_deployer_email(value: str, deployer_email: str | None) -> str:
    """Replace ``${DEPLOYER_EMAIL}`` in a single string value."""
    if DEPLOYER_EMAIL_TOKEN not in value:
        return value
    if not deployer_email:
        # Leave the placeholder so the generated file makes the missing
        # context obvious; downstream tooling will fail with a clear
        # ``${DEPLOYER_EMAIL}`` literal in an email field.
        return value
    return value.replace(DEPLOYER_EMAIL_TOKEN, deployer_email)


def _render_dashboard(target: str, catalog: str, schema: str) -> bool:
    """Render ``dashboards/data_quality.lvdash.json`` for the given bundle target.

    The committed file ships with ``{{UC_CATALOG}}`` / ``{{UC_SCHEMA}}``
    placeholders so the same source-of-truth works across all targets. At
    deploy time we substitute the target's actual UC catalog + schema in
    place, ``databricks bundle deploy`` uploads the rendered file, then
    ``scripts/deploy.sh``'s EXIT trap restores the committed (templated)
    copy. Returns True when at least one substitution happened.
    """
    if not DASHBOARD_PATH.is_file():
        return False
    if not catalog or not schema:
        raise SystemExit(
            f"--target={target!r}: missing uc_catalog or uc_schema in "
            f"project-settings.yaml; cannot render {DASHBOARD_PATH.name}"
        )
    text = DASHBOARD_PATH.read_text(encoding="utf-8")
    if UC_CATALOG_TOKEN not in text and UC_SCHEMA_TOKEN not in text:
        return False
    rendered = text.replace(UC_CATALOG_TOKEN, catalog).replace(UC_SCHEMA_TOKEN, schema)
    if rendered == text:
        return False
    DASHBOARD_PATH.write_text(rendered, encoding="utf-8")
    return True


def _find_profile_for_target(data: dict, target: str) -> dict | None:
    for prof in (data.get("profiles") or {}).values():
        if isinstance(prof, dict) and prof.get("bundle_target") == target:
            return prof
    return None


def _load_yaml() -> dict:
    try:
        import yaml  # type: ignore
    except ImportError as e:
        print("Install PyYAML: pip install pyyaml", file=sys.stderr)
        raise SystemExit(1) from e
    if not SETTINGS_PATH.is_file():
        raise SystemExit(f"Missing {SETTINGS_PATH}")
    with SETTINGS_PATH.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or "profiles" not in data:
        raise SystemExit("project-settings.yaml: expected top-level 'profiles'")
    return data


def _emit_targets(data: dict, deployer_email: str | None = None) -> str:
    profiles: dict = data["profiles"]
    lines: list[str] = []
    lines.append("# AUTO-GENERATED by scripts/sync_project_settings.py — do not hand-edit.")
    lines.append("# Source of truth: config/project-settings.yaml")
    lines.append("")
    lines.append("targets:")

    for name, prof in profiles.items():
        if not isinstance(prof, dict):
            continue
        bt = prof.get("bundle_target")
        if not bt:
            continue
        host = prof.get("workspace_host", "").strip()
        if not host:
            raise SystemExit(f"Profile {name!r}: missing workspace_host")

        lines.append(f"  {bt}:")
        lines.append("    workspace:")
        lines.append(f"      host: {host}")
        lines.append("    variables:")
        # Bundle uses var.catalog / var.schema (UC live target); project file uses uc_* for clarity.
        var_map = [
            ("sql_warehouse_id", "sql_warehouse_id"),
            ("app_name", "app_name"),
            ("catalog", "uc_catalog"),
            ("schema", "uc_schema"),
            ("src_catalog", "src_catalog"),
            ("src_schema", "src_schema"),
            # Industry Data Model (MVM) silver catalog. Defaults to the live
            # ``uc_catalog`` below when unset (see fallback before the loop).
            ("mvm_catalog", "mvm_catalog"),
            ("lakebase_catalog", "lakebase_catalog"),
            ("lakebase_schema", "lakebase_schema"),
            ("genie_space_id", "genie_space_id"),
            ("zerobus_server_endpoint", "zerobus_server_endpoint"),
            ("producer_client_id", "producer_client_id"),
            ("producer_client_secret_ref", "producer_client_secret_ref"),
            ("app_client_id", "app_client_id"),
            # Lakebase Syncer wiring (gold → ai_serving Postgres).
            ("lakebase_pg_host", "lakebase_pg_host"),
            ("lakebase_pg_user", "lakebase_pg_user"),
            ("lakebase_syncer_pg_user", "lakebase_syncer_pg_user"),
            ("lakebase_pg_database", "lakebase_pg_database"),
            ("lakebase_endpoint", "lakebase_endpoint"),
            ("lakebase_checkpoint_volume", "lakebase_checkpoint_volume"),
            ("mlflow_experiment_path", "mlflow_experiment_path"),
            # Shift ID written by the producer + read by the App.
            # Per-environment to keep dev's rows out of prod's
            # ``WHERE shift_id = ...`` SSE cursor scan, since the
            # Lakebase ai_serving namespace is shared.
            ("sim_shift_id", "sim_shift_id"),
            # Optional "RemoveAfter" cleanup tag (YYYYMMDD). Empty string ⇒
            # no meaningful tag value emitted on jobs/apps. Set this only if
            # your workspace enforces a retention policy that auto-deletes
            # untagged resources; otherwise leave it empty.
            ("remove_after", "remove_after"),
        ]
        # mvm_catalog defaults to the live UC catalog. Installing a brand-new
        # catalog needs metastore CREATE CATALOG (denied on many workspaces,
        # e.g. FEVM serverless); reusing an owned catalog is the portable
        # default. The MVM model's domain schemas (asset/production/quality/…)
        # don't collide with the streaming schema.
        if not str(prof.get("mvm_catalog") or "").strip():
            prof = {**prof, "mvm_catalog": prof.get("uc_catalog", "")}
        for bundle_key, prof_key in var_map:
            val = prof.get(prof_key, "")
            if val is None:
                val = ""
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                lines.append(f"      {bundle_key}: {val}")
            else:
                sval = str(val)
                # Empty lakebase_catalog becomes null in bundle Terraform job parameters; use sentinel.
                if bundle_key == "lakebase_catalog" and not sval.strip():
                    sval = "__SKIP__"
                # Public-release templating: source-of-truth ships with
                # ``${DEPLOYER_EMAIL}`` in deployer-specific fields; resolve to
                # the active CLI user (or whatever the caller passed) so the
                # generated bundle vars carry a concrete email.
                sval = _substitute_deployer_email(sval, deployer_email)
                esc = sval.replace('"', '\\"')
                lines.append(f'      {bundle_key}: "{esc}"')

        zb = bool(prof.get("zerobus_ingest_enabled"))
        if bt in ("line_manager_prod", "line_manager_dev") and not zb:
            raise SystemExit(
                f"Profile {name!r}: bundle_target {bt} requires zerobus_ingest_enabled: true"
            )

        # Resource-name suffix lets dev + prod targets co-exist in the same
        # workspace without colliding on Databricks Job display names. Empty
        # string for prod-style targets (keeps existing names stable).
        suffix = str(prof.get("resource_name_suffix") or "").strip()

        # App SP client id is threaded into job permissions blocks so the
        # CAN_MANAGE_RUN grant is declarative + idempotent on every bundle
        # deploy. SET_ME / empty profiles emit no permissions block.
        app_sp = str(prof.get("app_client_id") or "").strip()

        if zb:
            lines.extend(
                _resources_setup_plus_zerobus_streaming(
                    suffix,
                    app_sp,
                    prof.get("src_catalog", ""),
                    prof.get("src_schema", ""),
                    dqx_enabled=bool(prof.get("dqx_enabled")),
                    mvm_silver_enabled=bool(prof.get("mvm_silver_enabled")),
                )
            )

        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _remove_after_tag_line(indent: str = "            ") -> str:
    """Emit the optional RemoveAfter tag line, conditional on ``var.remove_after``.

    Bundle vars don't have YAML conditionals, so we encode the conditional in
    the value itself: when ``var.remove_after`` is empty, the tag is still
    emitted but with an empty string value — harmless. When set to a YYYYMMDD
    value, it lands on the resource so a workspace retention sweeper (if any)
    can respect it.

    Indent matches whatever ``tags:`` block this line is inserted into (jobs
    use 12-space indent, the experiment uses 6).
    """
    return f'{indent}RemoveAfter: "${{var.remove_after}}"'


def _app_sp_permissions_block(app_sp: str) -> list[str]:
    """Emit a job-level ``permissions:`` block granting the App SP CAN_MANAGE_RUN.

    The App's ``/sim/restart`` and ``/ops/control/action`` endpoints call
    ``jobs.cancel_run`` + ``jobs.run_now`` as the App service principal.
    Without ``CAN_MANAGE_RUN`` those calls fail with
    ``User <sp> does not have Manage Run or Owner or Admin permissions on job ...``,
    Clean Restart leaves the producer running, and the inventory wipe
    gets immediately re-written from the producer's in-memory depleted
    state. Declaring the grant here keeps it idempotent and survives
    job recreation (e.g. when DAB renames a resource).

    Empty / SET_ME ``app_sp`` → returns [], so workshop-style profiles
    that haven't provisioned an App SP yet still deploy.
    """
    sp = (app_sp or "").strip()
    if not sp or sp == "SET_ME":
        return []
    return [
        "          permissions:",
        f"            - service_principal_name: \"{sp}\"",
        "              level: CAN_MANAGE_RUN",
    ]


def _data_quality_dashboard_permissions_block(app_sp: str) -> list[str]:
    """Emit a per-target ``dashboards:`` override granting the App SP CAN_READ.

    The Data Quality dashboard is defined in ``databricks.yml`` with
    ``embed_credentials: true`` (published with the deploy identity's creds so
    the app's embedded iframe renders widgets). The App backend also RESOLVES
    the dashboard by id/name AS the App SP (``/ops/dashboard-info``), which
    needs CAN_READ. Declaring the grant here makes ``databricks bundle deploy``
    self-sufficient for the embed — no post-deploy grant_app_jobs_view.sh
    required.

    Empty / SET_ME ``app_sp`` (first-ever deploy, before the App SP exists) →
    returns [], so the initial rollout still deploys; deploy.sh captures the
    real SP and re-deploys, at which point the grant applies.
    """
    sp = (app_sp or "").strip()
    if not sp or sp == "SET_ME":
        return []
    return [
        "      dashboards:",
        "        line_manager_data_quality:",
        "          permissions:",
        f"            - service_principal_name: \"{sp}\"",
        "              level: CAN_READ",
    ]


def _effective_src(value: object) -> str | None:
    """Return a usable src catalog/schema, or None when unset / placeholder."""
    s = str(value or "").strip()
    if not s or s == "SET_ME":
        return None
    return s


def _bootstrap_reference_src_param_lines(src_catalog: object, src_schema: object) -> list[str]:
    """Emit --src-catalog/--src-schema only when the profile sets real source UC."""
    if _effective_src(src_catalog) and _effective_src(src_schema):
        return [
            '                  - "--src-catalog"',
            '                  - "${var.src_catalog}"',
            '                  - "--src-schema"',
            '                  - "${var.src_schema}"',
        ]
    return []


def _solution_accelerator_job(
    suffix: str = "",
    app_sp: str = "",
    src_catalog: object = "",
    src_schema: object = "",
) -> list[str]:
    """Job block only (indented under ``jobs:``)."""
    bootstrap_src_params = _bootstrap_reference_src_param_lines(src_catalog, src_schema)
    return [
        "        solution_accelerator_setup:",
        f"          name: line-manager-solution-accelerator-setup{suffix}",
        *_app_sp_permissions_block(app_sp),
        "          tags:",
        "            project: line-manager-streaming",
        "            resource_group: production-line-copilot",
        "            cost_scope: demo-app",
        _remove_after_tag_line(),
        "          tasks:",
        "            - task_key: init_uc",
        "              environment_key: default",
        "              spark_python_task:",
        "                python_file: ../scripts/init_realtime_uc.py",
        "                parameters:",
        "                  - \"--catalog\"",
        "                  - \"${var.catalog}\"",
        "                  - \"--schema\"",
        "                  - \"${var.schema}\"",
        "                  - \"--host\"",
        "                  - \"${workspace.host}\"",
        "                  - \"--warehouse-id\"",
        "                  - \"${var.sql_warehouse_id}\"",
        "                  - \"--producer-client-id\"",
        "                  - \"${var.producer_client_id}\"",
        "                  - \"--app-client-id\"",
        "                  - \"${var.app_client_id}\"",
        "            - task_key: bootstrap_reference",
        "              depends_on:",
        "                - task_key: init_uc",
        "              environment_key: default",
        "              spark_python_task:",
        "                python_file: ../scripts/bootstrap_reference_data.py",
        "                parameters:",
        *bootstrap_src_params,
        "                  - \"--catalog\"",
        "                  - \"${var.catalog}\"",
        "                  - \"--schema\"",
        "                  - \"${var.schema}\"",
        "                  - \"--host\"",
        "                  - \"${workspace.host}\"",
        "                  - \"--warehouse-id\"",
        "                  - \"${var.sql_warehouse_id}\"",
        "            - task_key: setup_synced_tables",
        "              depends_on:",
        "                - task_key: init_uc",
        "              environment_key: default",
        "              spark_python_task:",
        "                python_file: ../scripts/setup_synced_tables.py",
        "                parameters:",
        "                  - \"--catalog\"",
        "                  - \"${var.catalog}\"",
        "                  - \"--schema\"",
        "                  - \"${var.schema}\"",
        "                  - \"--lakebase-catalog\"",
        "                  - \"${var.lakebase_catalog}\"",
        "                  - \"--lakebase-schema\"",
        "                  - \"${var.lakebase_schema}\"",
        # ── Industry Data Model (MVM) install + initial silver populate ──────
        # One-time (idempotent) provisioning of the governed silver layer the
        # SQL / Genie agents + DQX referential check read. Pulls the model SQL
        # live from the upstream repo and applies it via the SDK Statement
        # Execution API (serverless-safe). Runs independently of init_uc; the
        # populate step depends on BOTH the model (install) and the reference
        # data (bootstrap_reference). The full install (metric views) can take
        # tens of minutes — acceptable in one-time setup, never on app deploy.
        "            - task_key: install_industry_data_model",
        "              environment_key: default",
        "              spark_python_task:",
        "                python_file: ../scripts/install_industry_data_model.py",
        "                parameters:",
        "                  - \"--catalog\"",
        "                  - \"${var.mvm_catalog}\"",
        "                  - \"--warehouse-id\"",
        "                  - \"${var.sql_warehouse_id}\"",
        "                  - \"--host\"",
        "                  - \"${workspace.host}\"",
        # The governed schemas are created here, so the app SP is granted read
        # here too — init_uc runs earlier, when they don't exist yet.
        "                  - \"--app-client-id\"",
        "                  - \"${var.app_client_id}\"",
        "            - task_key: populate_silver_model",
        "              depends_on:",
        "                - task_key: install_industry_data_model",
        "                - task_key: bootstrap_reference",
        "              environment_key: default",
        "              spark_python_task:",
        "                python_file: ../agent_server/serving/silver_mapper.py",
        "                parameters:",
        "                  - \"--mvm-catalog\"",
        "                  - \"${var.mvm_catalog}\"",
        "                  - \"--src-catalog\"",
        "                  - \"${var.catalog}\"",
        "                  - \"--src-schema\"",
        "                  - \"${var.schema}\"",
        "                  - \"--warehouse-id\"",
        "                  - \"${var.sql_warehouse_id}\"",
        "                  - \"--host\"",
        "                  - \"${workspace.host}\"",
        "          environments:",
        "            - environment_key: default",
        "              spec:",
        "                environment_version: \"2\"",
        "                dependencies:",
        # SQL runs via the SDK Statement Execution API (scripts/_uc_sql.py) — no
        # databricks-sql-connector, which hangs on serverless job compute when its
        # pyarrow import hits the environment's NumPy 2.x ABI.
        "                  - \"databricks-sdk>=0.89.0\"",
        "                  - \"pandas>=2.0.0\"",
    ]


def _streaming_producer_job(suffix: str = "", app_sp: str = "") -> list[str]:
    return [
        "        streaming_producer:",
        f"          name: line-manager-streaming-producer{suffix}",
        *_app_sp_permissions_block(app_sp),
        "          # Continuous mode — restarts on failure. Producer is the sole writer to",
        "          # bronze Delta for the Zerobus streaming path.",
        "          continuous:",
        "            pause_status: UNPAUSED",
        "          tasks:",
        "            - task_key: producer",
        "              spark_python_task:",
        "                python_file: ../src/streaming_sim/producer.py",
        "                parameters:",
        "                  - \"--catalog\"",
        "                  - \"${var.catalog}\"",
        "                  - \"--schema\"",
        "                  - \"${var.schema}\"",
        # Per-environment shift_id: dev sets ``shift_1_dev`` so its
        # rows can't appear in prod's SSE cursor scan (Lakebase
        # ai_serving.* is shared across envs).
        "                  - \"--shift-id\"",
        "                  - \"${var.sim_shift_id}\"",
        "                  - \"--workspace-url\"",
        "                  - \"${workspace.host}\"",
        "                  # --host + --warehouse-id enable UC scenario_commands polling",
        "                  # so /sim/incident from the App actually reaches the producer.",
        "                  # Without these the CommandStore stays disabled and incidents",
        "                  # are silently ignored.",
        "                  - \"--host\"",
        "                  - \"${workspace.host}\"",
        "                  - \"--warehouse-id\"",
        "                  - \"${var.sql_warehouse_id}\"",
        "                  - \"--zerobus-endpoint\"",
        "                  - \"${var.zerobus_server_endpoint}\"",
        "                  - \"--client-id\"",
        "                  - \"${var.producer_client_id}\"",
        "                  - \"--client-secret\"",
        "                  - \"${var.producer_client_secret_ref}\"",
        "                  # Tier-1 dual-write to Lakebase ai_serving.*_history.",
        "                  # Producer writes the same per-tick rows directly via",
        "                  # psycopg2 so the live UI sees them in ~200 ms instead",
        "                  # of waiting for Zerobus' 5-30 s bronze materialization",
        "                  # SLA followed by streaming_quality_sync's micro-batch.",
        "                  #",
        "                  # PGUSER is the producer SP itself: WorkspaceClient()",
        "                  # inside the task picks up DATABRICKS_CLIENT_ID/SECRET",
        "                  # (passed for Zerobus auth) and mints OAuth tokens for",
        "                  # THAT identity, so PGUSER must match. The producer SP",
        "                  # is registered in Lakebase + granted ai_serving.* by",
        "                  # scripts/init_lakebase_role.py during deploy.sh.",
        "                  - \"--pg-host\"",
        "                  - \"${var.lakebase_pg_host}\"",
        "                  - \"--pg-user\"",
        "                  - \"${var.producer_client_id}\"",
        "                  - \"--pg-database\"",
        "                  - \"${var.lakebase_pg_database}\"",
        "                  - \"--lakebase-endpoint\"",
        "                  - \"${var.lakebase_endpoint}\"",
        "              environment_key: default",
        "          tags:",
        "            project: line-manager-streaming",
        "            resource_group: production-line-copilot",
        "            cost_scope: demo-app",
        _remove_after_tag_line(),
        "          environments:",
        "            - environment_key: default",
        "              spec:",
        "                environment_version: \"2\"",
        "                dependencies:",
        "                  - \"databricks-sdk>=0.89.0\"",
        "                  - \"databricks-sql-connector>=3.0.0\"",
        "                  - \"databricks-zerobus-ingest-sdk>=1.0.0\"",
        "                  # Required for the LakebaseSink direct-write path",
        "                  # (Tier-1). Without psycopg2 on the producer's serverless",
        "                  # task env, the sink falls back to disabled mode and the",
        "                  # live UI inherits Zerobus materialization latency.",
        "                  - \"psycopg2-binary>=2.9\"",
    ]


def _streaming_quality_sync_job(
    suffix: str = "", app_sp: str = "", dqx_enabled: bool = False,
    mvm_silver_enabled: bool = False,
) -> list[str]:
    """Streaming Quality + Side-Outputs — classic single-node compute.

    Tier-2 analytics consumer. Reads bronze Delta tables (Zerobus-fed),
    applies QC predicates inline, and writes EVERY valid row per
    micro-batch to:
      1. ``gold_history_<table>``        (Delta append, governed lakehouse for
                                          agent SQL + Genie + reconciliation)
      2. ``ai_serving.<table>_latest``   (Lakebase upsert, driver-side, ≤36 rows;
                                          current-state cache, cold-start fallback)
      3. ``<schema>.quality_rejects``    (Delta append for the DQ dashboard)

    What this job NO LONGER writes: ``ai_serving.<table>_history``. The
    producer dual-writes those rows directly via psycopg2 (Tier-1 live UI
    path) so the live ribbon doesn't inherit Zerobus' 5-30 s bronze
    materialization SLA. See ``src/streaming_sim/lakebase_sink.py``.

    Spark retries the WHOLE micro-batch on any failure; every sink is
    idempotent (Delta is checkpointed; Postgres uses ``DO UPDATE`` on
    latest).

    Compute: **classic single-node m6gd.2xlarge** (8 vCPU, 32 GiB).
    We tried serverless task envs to avoid cluster cost + cold start,
    but two issues made the latency unworkable:

      * Serverless rejects ``Trigger.processingTime``::

          [INFINITE_STREAMING_TRIGGER_NOT_SUPPORTED] Trigger type
          ProcessingTime is not supported for this cluster type.

      * The ``Trigger.AvailableNow`` workaround paid ~12 s of
        Spark-Connect query-plan + worker spin-up overhead per drain;
        across 4 streams that pushed end-to-end latency to ~50 s,
        well above the 4-5 s target.

    Classic compute keeps all 4 streams hot in a single JVM with
    ``processingTime=1 s``. Since this job no longer writes the volume
    _history sink (producer dual-writes that directly), the per-batch
    work is much smaller (gold_history append + ≤36-row _latest upsert
    + sparse rejects). The 8-core ``m6gd.2xlarge`` is now sufficient
    for both dev and prod; the previous dev bump to ``m6gd.8xlarge``
    is no longer required (kept as a per-env knob in case future load
    increases).

    NOTE: We deliberately do NOT use Real-Time Mode here. RTM only
    benefits Kafka / Kinesis / Pulsar sources, not Delta — see
    ``client/src/components/AboutTab.tsx`` for the architectural framing.
    """
    return [
        "        # ── Streaming Quality + Dual-Write (serverless, AvailableNow loop) ─",
        "        # Single hot-path writer: reads bronze, applies QC, writes to",
        "        # gold_history Delta + ai_serving.* Lakebase + quality_rejects.",
        "        # Runs on serverless task envs with --trigger-mode availableNow:",
        "        # each cycle drains every bronze stream once and sleeps. Live-UI",
        "        # latency is NOT gated by this job (producer dual-writes ai_serving",
        "        # directly to Lakebase via psycopg2). This path feeds the analytics",
        "        # surfaces (Genie SQL over gold_history, DQ dashboard over quality_rejects,",
        "        # sensor-vs-MES reconciliation) at ~60s cadence.",
        "        streaming_quality_sync:",
        f"          name: line-manager-streaming-quality-sync{suffix}",
        *_app_sp_permissions_block(app_sp),
        "          continuous:",
        "            pause_status: UNPAUSED",
        "          tasks:",
        "            - task_key: stream",
        "              environment_key: default",
        "              spark_python_task:",
        "                python_file: ../pipelines/streaming_quality_sync.py",
        "                parameters:",
        "                  - \"--catalog\"",
        "                  - \"${var.catalog}\"",
        "                  - \"--schema\"",
        "                  - \"${var.schema}\"",
        "                  - \"--checkpoint-volume\"",
        "                  - \"${var.lakebase_checkpoint_volume}\"",
        # Lakebase connection params — env vars don't propagate to the
        # executor Python workers (foreachPartition runs in a fresh
        # process per stage), so we pass them as CLI flags. The script's
        # _apply_cli_env() promotes them to PGHOST / PGUSER / PGDATABASE /
        # LAKEBASE_ENDPOINT on the driver before any code runs; the
        # foreachPartition closure captures them explicitly for the
        # executor side.
        "                  - \"--pg-host\"",
        "                  - \"${var.lakebase_pg_host}\"",
        "                  - \"--pg-user\"",
        "                  - \"${var.lakebase_syncer_pg_user}\"",
        "                  - \"--pg-database\"",
        "                  - \"${var.lakebase_pg_database}\"",
        "                  - \"--lakebase-endpoint\"",
        "                  - \"${var.lakebase_endpoint}\"",
        # Serverless-required: Trigger.processingTime is rejected on
        # serverless task envs ("INFINITE_STREAMING_TRIGGER_NOT_SUPPORTED"),
        # so we use AvailableNow + sleep loop. See the streaming_quality_sync.py
        # docstring for the latency trade-off rationale.
        "                  - \"--trigger-mode\"",
        "                  - \"availableNow\"",
        # DQX augmentation: --mvm-catalog is always passed (harmless when DQX is
        # off); --dqx-enabled "1" is emitted only for targets that opt in via
        # ``dqx_enabled: true`` in project-settings. dqx_augment reads these off
        # the env vars streaming_quality_sync._apply_cli_env promotes them to.
        "                  - \"--mvm-catalog\"",
        "                  - \"${var.mvm_catalog}\"",
        *([
            "                  - \"--dqx-enabled\"",
            "                  - \"1\"",
        ] if dqx_enabled else []),
        # Streaming silver (design B): when on, each micro-batch is MERGE-upserted
        # into the governed MVM fact tables from inside foreachBatch — the model
        # rides this same Zerobus→bronze path at seconds latency, replacing the
        # retired 30-min silver_model_refresh job. Emitted only for opted-in
        # targets; deploys dark otherwise. Adds a 5th stream (quality_checks →
        # quality.inspection_result) when enabled.
        *([
            "                  - \"--mvm-silver\"",
            "                  - \"1\"",
        ] if mvm_silver_enabled else []),
        "          tags:",
        "            project: line-manager-streaming",
        "            resource_group: production-line-copilot",
        "            cost_scope: demo-app",
        _remove_after_tag_line(),
        "          environments:",
        "            - environment_key: default",
        "              spec:",
        "                environment_version: \"2\"",
        # psycopg2-binary is REQUIRED on the driver: the *_latest upsert connects
        # to Lakebase via _connect_with_token inside foreachBatch. databricks-sdk
        # is needed for the OAuth token mint.
        "                dependencies:",
        "                  - \"databricks-sdk>=0.89.0\"",
        "                  - \"psycopg2-binary>=2.9\"",
        # databricks-labs-dqx provides DQEngine (freshness/range/referential
        # checks). Imported lazily + fail-safe in dqx_augment, so a missing lib
        # only DISABLES DQX silently — it must be present for DQX_ENABLED=1 to do
        # anything. Cheap to always ship (only invoked when DQX_ENABLED=1).
        # Pin >=0.15: the DQEngine ctor arg order and the freshness check name
        # (is_data_fresh) both changed vs 0.10, and dqx_augment targets 0.15.
        "                  - \"databricks-labs-dqx>=0.15\"",
    ]


def _history_ttl_job(suffix: str = "") -> list[str]:
    """Daily TTL cleanup for ``ai_serving.*_history`` tables.

    Runs once a day on serverless. DELETE old rows + VACUUM ANALYZE so
    Postgres reclaims tuples and refreshes statistics. 7-day retention is
    plenty for the App's 20 s playback buffer + ad-hoc lookback queries.
    """
    return [
        "        # ── Daily Lakebase history TTL (serverless, ~1 min/day) ──────",
        "        lakebase_history_ttl:",
        f"          name: line-manager-lakebase-history-ttl{suffix}",
        "          schedule:",
        "            quartz_cron_expression: \"0 0 3 * * ?\"",  # 03:00 UTC daily
        "            timezone_id: \"UTC\"",
        "            pause_status: UNPAUSED",
        "          tasks:",
        "            - task_key: prune_history",
        "              environment_key: default",
        "              spark_python_task:",
        "                python_file: ../scripts/prune_lakebase_history.py",
        "                parameters:",
        "                  - \"--pg-host\"",
        "                  - \"${var.lakebase_pg_host}\"",
        "                  - \"--pg-user\"",
        "                  - \"${var.lakebase_syncer_pg_user}\"",
        "                  - \"--pg-database\"",
        "                  - \"${var.lakebase_pg_database}\"",
        "                  - \"--lakebase-endpoint\"",
        "                  - \"${var.lakebase_endpoint}\"",
        "                  - \"--retention-days\"",
        "                  - \"7\"",
        "          tags:",
        "            project: line-manager-streaming",
        "            resource_group: production-line-copilot",
        "            cost_scope: demo-app",
        _remove_after_tag_line(),
        "          environments:",
        "            - environment_key: default",
        "              spec:",
        "                environment_version: \"2\"",
        "                dependencies:",
        "                  - \"databricks-sdk>=0.89.0\"",
        "                  - \"psycopg2-binary>=2.9\"",
    ]


def _resources_setup_plus_zerobus_streaming(
    suffix: str = "",
    app_sp: str = "",
    src_catalog: object = "",
    src_schema: object = "",
    dqx_enabled: bool = False,
    mvm_silver_enabled: bool = False,
) -> list[str]:
    """Full resource set for Zerobus-enabled targets.

    ``suffix`` is appended to every resource display name (jobs)
    so multiple targets sharing the same workspace (e.g. dev + prod in the
    same workspace) don't collide on resource names. Convention: empty for prod
    (keeps existing names stable across the dev/prod split rollout) and
    ``-dev`` for the dev target.

    ``app_sp`` (Databricks App service principal client id) is threaded into
    every Job resource that the App's ``/sim/restart`` or ``/ops/control/action``
    endpoints call ``run_now`` / ``cancel_run`` on (producer, streaming sync,
    setup). They emit a ``permissions: [CAN_MANAGE_RUN]`` block so the grant
    is declarative + idempotent on every ``databricks bundle deploy``. Without
    this grant Clean Restart can wipe Lakebase but fails to cancel the
    producer, which then re-writes the wiped data from its in-memory state —
    the "inventory shows previous values, depleting after Clean Restart" bug.
    """
    return [
        "    resources:",
        "      jobs:",
        *_solution_accelerator_job(suffix, app_sp, src_catalog, src_schema),
        *_streaming_producer_job(suffix, app_sp),
        *_streaming_quality_sync_job(
            suffix, app_sp, dqx_enabled=dqx_enabled,
            mvm_silver_enabled=mvm_silver_enabled,
        ),
        *_history_ttl_job(suffix),
        # NOTE: the MVM silver is populated by the streaming_quality_sync job's
        # per-micro-batch MERGE (design B, --mvm-silver), NOT a separate refresh
        # job. The old 30-min ``silver_model_refresh`` job was retired; a stale
        # copy is dropped from the workspace on the next ``databricks bundle
        # deploy``. Dimensions are seeded once by the setup job's populate step.
        # NOTE: the streaming_quality_sync job does the dual-write (Lakebase +
        # Delta) and owns quality validation. There is no separate syncer or
        # pipeline resource; a stale ``lakebase_syncer`` is dropped from the
        # workspace on the next ``databricks bundle deploy``.
        # Per-target Data Quality dashboard CAN_READ grant for the App SP so the
        # embedded dashboard resolves + renders after a plain bundle deploy.
        *_data_quality_dashboard_permissions_block(app_sp),
    ]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--check", action="store_true", help="Exit 1 if bundle-targets.yaml is out of date")
    p.add_argument(
        "--deployer-email",
        default=None,
        help=(
            "Email to substitute for ${DEPLOYER_EMAIL} placeholders. "
            "Falls back to the DEPLOYER_EMAIL env var, then `databricks "
            "current-user me` from the active CLI profile."
        ),
    )
    p.add_argument(
        "--target",
        default=None,
        help=(
            "Optional bundle target (e.g. line_manager_dev). When provided, "
            "render dashboards/data_quality.lvdash.json with the target's "
            "uc_catalog/uc_schema so widgets resolve against the right UC "
            "tables. deploy.sh backs up the committed (templated) copy "
            "before this step and restores it on EXIT."
        ),
    )
    args = p.parse_args()

    data = _load_yaml()
    deployer_email = _resolve_deployer_email(args.deployer_email)
    out = _emit_targets(data, deployer_email=deployer_email)

    if args.check:
        existing = TARGETS_PATH.read_text(encoding="utf-8") if TARGETS_PATH.is_file() else ""
        if existing != out:
            print("config/bundle-targets.yaml is out of sync with config/project-settings.yaml", file=sys.stderr)
            print("Run: python scripts/sync_project_settings.py", file=sys.stderr)
            raise SystemExit(1)
        print("OK: bundle targets match project-settings.yaml")
        return

    TARGETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    TARGETS_PATH.write_text(out, encoding="utf-8")
    print(f"Wrote {TARGETS_PATH}")
    print("Hint: set app UC_CATALOG / UC_SCHEMA to match the profile you deploy (see config/README.md)")

    if args.target:
        prof = _find_profile_for_target(data, args.target)
        if prof is None:
            raise SystemExit(f"--target={args.target!r}: no matching profile in {SETTINGS_PATH}")
        catalog = (prof.get("uc_catalog") or "").strip()
        schema = (prof.get("uc_schema") or "").strip()
        if _render_dashboard(args.target, catalog, schema):
            print(
                f"Rendered {DASHBOARD_PATH.name} for target {args.target} "
                f"(catalog={catalog}, schema={schema})"
            )
        else:
            print(
                f"Dashboard {DASHBOARD_PATH.name} unchanged "
                f"(no placeholders found or already rendered for {args.target})"
            )


if __name__ == "__main__":
    main()
