#!/usr/bin/env python3
"""Give an environment a Unity Catalog-backed MLflow experiment for traces.

Why this exists: a classic experiment stores span payloads as an artifact
uploaded to ``<region>.storage.cloud.databricks.com``, and the Databricks Apps
runtime gets ``Connection refused`` from that host. Trace metadata lands, every
span is dropped, and because export is async and non-fatal nothing on the
surface says so — the MLflow UI just shows traces with no spans in them. A
UC-backed experiment writes spans to Delta through the workspace host the app
already reaches for SQL and model serving, so the export path stops depending
on an egress route the app does not have.

Two constraints shape everything here, and both are one-way doors:

1. A UC trace destination can only be attached to an experiment that contains
   **no traces**. The bundle's experiment has been collecting them since the
   app first ran, so it can never be converted — hence a separate, purpose-made
   experiment, linked before anything writes to it, whose id is written into
   ``app.<env>.local.yaml``. That is also why the env var is set by id rather
   than ``valueFrom: experiment``.
2. A link is **permanent**. ``mlflow.tracing.unset_experiment_trace_location``
   exists but the backend refuses it outright, so an experiment linked wrongly
   is scrap and the only repair is a new experiment.

Because of (2) this uses ``mlflow.set_experiment(trace_location=UnityCatalog(...))``
rather than ``mlflow.tracing.set_experiment_trace_location``. The latter reports
success and writes the storage-table tags, but does not register the telemetry
destination that export actually routes through: spans are then silently
discarded, and the experiment cannot be repaired. ``_destination_id`` is checked
after linking for exactly that reason, and a link without one is reported as a
failure rather than written into the app config.

Idempotent: the link is recorded in experiment tags, so a second run reads the
tags and reports rather than re-linking. Safe to run on every deploy.

Usage:
  python3 scripts/provision_trace_storage.py dev  --profile <p> [--app-sp <id>]
  python3 scripts/provision_trace_storage.py prod --profile <p> --dry-run

Without ``--app-sp`` the experiment and tables are created but the app's
service principal is not granted access, and the app will not be able to write
traces. deploy.sh passes it once the app SP is known.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from refresh_app_yaml_ids import _patch_yaml, _pick_app_yaml  # noqa: E402

ENV_TO_APP_YAML = {
    "dev": ROOT / "app.dev.yaml",
    "prod": ROOT / "app.yaml",
}

# The tag MLflow writes when an experiment is linked. Reading it is what makes
# this script idempotent without depending on the wording of an error message.
LINK_TAG = "mlflow.experiment.databricksTraceDestinationPath"

# The telemetry destination is what span export actually routes through. Its
# absence is the difference between a link that works and one that reports
# success while discarding every span, so it is checked rather than assumed.
DESTINATION_TAG = "mlflow.experiment.databricksTelemetryDestinationId"

# Tags naming the Delta tables the traces are written to. The app's service
# principal needs write access to these, and their names are not fully
# predictable from the schema alone, so they are read back rather than guessed.
TABLE_TAGS = (
    "mlflow.experiment.databricksTraceSpanStorageTable",
    "mlflow.experiment.databricksTraceLogStorageTable",
    "mlflow.experiment.databricksTraceAnnotationStorageTable",
)

# Suffix distinguishing the traces experiment from the bundle's. Kept adjacent
# to the bundle path so the two sort together in the experiments list.
TRACES_SUFFIX = "-traces"

# Names the trace tables so they read as ours in a schema that also holds the
# app's own tables, rather than as MLflow's generic defaults.
TABLE_PREFIX = "prodline_traces"


def _candidates(base: str):
    """The trace experiment name, then fallbacks.

    A link is permanent, so an experiment that linked badly can never be
    repaired or reused. Rather than stall the deploy on a name that is now
    scrap, move to the next one.
    """
    yield base
    for n in range(2, 8):
        yield f"{base}-{n}"


def _profile(settings: Path, env: str) -> dict:
    """The project-settings profile whose bundle_target matches ``env``."""
    import yaml

    target = f"line_manager_{env}"
    data = yaml.safe_load(settings.read_text(encoding="utf-8")) or {}
    for prof in (data.get("profiles") or {}).values():
        if str(prof.get("bundle_target", "")).strip() == target:
            return prof
    raise SystemExit(f"No profile in {settings} with bundle_target: {target}")


def _experiment_name(profile: dict) -> str:
    base = str(profile.get("mlflow_experiment_path", "")).strip()
    if not base or base == "SET_ME":
        raise SystemExit("profile has no usable mlflow_experiment_path")
    return f"{base}{TRACES_SUFFIX}"


def _grant(warehouse_id: str, statement: str) -> None:
    """Run one GRANT, reporting failures without aborting the deploy."""
    from _uc_sql import StmtCursor  # local helper over the Statement Execution API

    cur = StmtCursor(warehouse_id)
    try:
        cur.execute(statement)
        print(f"  ok: {statement}")
    except Exception as exc:  # noqa: BLE001 - a missing grant is visible in the app
        if "TABLE_DOES_NOT_EXIST" in str(exc):
            # MLflow creates some of the trace tables lazily on first write, so
            # right after linking they are named in the tags but do not exist.
            # deploy.sh runs this every time, so the next deploy grants it.
            print(f"  skipped (created on first use): {statement.split(' ON TABLE ')[-1].split(' TO ')[0]}")
        else:
            print(f"  WARN ({type(exc).__name__}): {statement} -> {exc}")
    finally:
        cur.close()


def provision(env: str, settings: Path, app_sp: str = "", dry_run: bool = False) -> int:
    import mlflow
    from mlflow.entities import UnityCatalog

    profile = _profile(settings, env)
    catalog = str(profile.get("uc_catalog", "")).strip()
    schema = str(profile.get("uc_schema", "")).strip()
    warehouse = str(profile.get("sql_warehouse_id", "")).strip()
    if not catalog or catalog == "SET_ME" or not schema:
        raise SystemExit(f"profile has no usable uc_catalog/uc_schema ({catalog!r}/{schema!r})")

    name = _experiment_name(profile)
    print(f"Trace experiment: {name}")
    print(f"Trace storage:    {catalog}.{schema}")

    if dry_run:
        print("Dry run — nothing created.")
        return 0

    def link(candidate: str) -> tuple[str, dict]:
        """Create the experiment already attached to UC, and verify it took."""
        exp = mlflow.set_experiment(
            experiment_name=candidate,
            trace_location=UnityCatalog(
                catalog_name=catalog,
                schema_name=schema,
                table_prefix=TABLE_PREFIX,
            ),
        )
        fresh = dict(mlflow.get_experiment(exp.experiment_id).tags or {})
        if not fresh.get(DESTINATION_TAG):
            raise SystemExit(
                f"Linked {candidate} ({exp.experiment_id}) but it has no "
                f"telemetry destination, so every span would be discarded. "
                "The link cannot be undone; delete the experiment and retry."
            )
        print(f"  linked {candidate} ({exp.experiment_id}) -> {catalog}.{schema}.{TABLE_PREFIX}_*")
        return exp.experiment_id, fresh

    experiment_id, tags = "", {}
    for candidate in _candidates(name):
        existing = mlflow.get_experiment_by_name(candidate)
        if existing is None:
            experiment_id, tags = link(candidate)
            break

        found = dict(existing.tags or {})
        if found.get(LINK_TAG) and found.get(DESTINATION_TAG):
            experiment_id, tags = existing.experiment_id, found
            print(f"  reusing {candidate} ({experiment_id}), already linked")
            break
        if found.get(LINK_TAG):
            # Linked but with no destination: spans go nowhere and it cannot be
            # unlinked, so it is scrap. Move to the next name rather than
            # pointing the app at an experiment that silently drops everything.
            print(f"  {candidate} is linked with no destination — unusable, trying the next name")
            continue
        experiment_id, tags = link(candidate)
        break
    else:
        raise SystemExit(f"No usable experiment name near {name} — clean up the scrap ones by hand.")

    if app_sp:
        tables = [tags[t] for t in TABLE_TAGS if tags.get(t)]
        print(f"  granting {app_sp} on {len(tables)} trace table(s)")
        for table in tables:
            _grant(warehouse, f"GRANT SELECT, MODIFY ON TABLE {table} TO `{app_sp}`")
        _grant_experiment(experiment_id, app_sp)
    else:
        print("  no --app-sp given; skipping grants (the app cannot write traces yet)")

    app_yaml = _pick_app_yaml(ENV_TO_APP_YAML[env])
    changed = _patch_yaml(app_yaml, {"MLFLOW_EXPERIMENT_ID": str(experiment_id)})
    print(f"  {app_yaml.name}: MLFLOW_EXPERIMENT_ID {'updated' if changed else 'already correct'}")
    return 0


def _grant_experiment(experiment_id: str, app_sp: str) -> None:
    """Give the app's service principal CAN_EDIT on the traces experiment.

    Without this the app authenticates fine and every export is rejected, which
    again shows up only as traces with no spans.
    """
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service.ml import (
        ExperimentAccessControlRequest,
        ExperimentPermissionLevel,
    )

    try:
        # update_permissions is additive, unlike set_permissions which replaces
        # the whole ACL and would drop whoever else can read the experiment.
        WorkspaceClient().experiments.update_permissions(
            experiment_id,
            access_control_list=[
                ExperimentAccessControlRequest(
                    service_principal_name=app_sp,
                    permission_level=ExperimentPermissionLevel.CAN_EDIT,
                )
            ],
        )
        print(f"  ok: CAN_EDIT on experiment {experiment_id}")
    except Exception as exc:  # noqa: BLE001
        print(f"  WARN: could not grant CAN_EDIT on the experiment: {exc}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("env", choices=["dev", "prod"])
    ap.add_argument(
        "--settings",
        default=str(ROOT / "config" / "project-settings.local.yaml"),
        help="project-settings file (defaults to the gitignored .local.yaml)",
    )
    ap.add_argument("--app-sp", default="", help="app service principal id to grant")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    settings = Path(args.settings)
    if not settings.is_file():
        settings = ROOT / "config" / "project-settings.yaml"

    try:
        return provision(args.env, settings, app_sp=args.app_sp, dry_run=args.dry_run)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - never fail a deploy over observability
        print(f"ERROR: trace storage provisioning failed: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
