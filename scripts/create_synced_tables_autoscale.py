"""Create synced tables that mirror quality-gate-validated Gold Delta → Lakebase Autoscaling Postgres.

Use case: serve the App from a single quality-checked path:

  Producer → ``*_bronze`` → streaming quality-gate → ``*_gold`` Delta
                                                                  ↓ Synced Table (Continuous)
                                                                  Lakebase Autoscaling Postgres
                                                                  ↓
                                                                  App SSE reads

Unlike ``setup_synced_tables.py``, this script targets the **Lakebase Autoscaling**
backend (``database_instance_name`` on the parent object) and creates the synced UC
table inside an existing **regular** UC catalog — no metastore-admin-owned
``DATABASE_INSTANCE`` catalog required.

Prerequisites:
- The streaming quality-gate job has run and populated the gold tables with
  ``delta.enableChangeDataFeed = true`` enabled (Continuous mode requires CDF).
- The Lakebase Autoscaling project + branch + database exists and the running user
  has access (project owner suffices).

Usage:
  python scripts/create_synced_tables_autoscale.py \
      --uc-catalog <your_catalog> \
      --uc-schema line_manager_streaming \
      --database-instance <your_lakebase_project> \
      --logical-database databricks_postgres \
      [--target-suffix _synced] [--target-schema line_manager_streaming]

Re-running the script is idempotent: tables that already exist are skipped.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Iterable

# Source-table → primary-key contract for sync.
# - PK MUST be unique on the source table; non-unique PKs cause Continuous mode to
#   silently drop rows or refuse to start.
SYNCED_TABLES: list[dict[str, object]] = [
    {"name": "machine_state",  "pk": ["shift_id", "machine_id", "ts"]},
    {"name": "event_log",      "pk": ["event_id"]},
    {"name": "quality_checks", "pk": ["check_id"]},
    {"name": "telemetry",      "pk": ["shift_id", "machine_id", "sensor_name", "timestamp"]},
    {"name": "line_kpi",       "pk": ["shift_id", "ts"]},
]


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--uc-catalog", default=os.environ.get("UC_CATALOG"))
    p.add_argument("--uc-schema", default=os.environ.get("UC_SCHEMA", "line_manager_streaming"))
    p.add_argument("--database-instance", default=os.environ.get("LAKEBASE_DATABASE_INSTANCE"),
                   help="Lakebase Autoscaling project name (set in project-settings.yaml).")
    p.add_argument("--logical-database", default=os.environ.get("LAKEBASE_LOGICAL_DATABASE", "databricks_postgres"))
    p.add_argument("--target-schema", default=os.environ.get("LAKEBASE_TARGET_SCHEMA"),
                   help="UC schema where synced tables register (defaults to --uc-schema).")
    p.add_argument("--target-suffix", default=os.environ.get("LAKEBASE_TARGET_SUFFIX", "_synced"),
                   help="Suffix appended to source table name when creating UC entry. Set to '' to reuse exact name.")
    p.add_argument("--scheduling", default=os.environ.get("LAKEBASE_SCHEDULING", "CONTINUOUS"),
                   choices=["SNAPSHOT", "TRIGGERED", "CONTINUOUS"])
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def _required(value: str | None, name: str) -> str:
    if not value:
        raise SystemExit(f"Missing required argument: --{name.replace('_','-')} or env equivalent")
    return value.strip()


def main(argv: Iterable[str] | None = None) -> int:
    args = _parse_args()
    uc_catalog = _required(args.uc_catalog, "uc_catalog")
    uc_schema = _required(args.uc_schema, "uc_schema")
    db_instance = _required(args.database_instance, "database_instance")
    logical_db = _required(args.logical_database, "logical_database")
    target_schema = (args.target_schema or uc_schema).strip()
    suffix = args.target_suffix or ""
    scheduling = args.scheduling

    # Lazy import keeps unit tests fast.
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service.database import (
        NewPipelineSpec,
        SyncedDatabaseTable,
        SyncedTableSchedulingPolicy,
        SyncedTableSpec,
    )

    sched_enum = {
        "SNAPSHOT": SyncedTableSchedulingPolicy.SNAPSHOT,
        "TRIGGERED": SyncedTableSchedulingPolicy.TRIGGERED,
        "CONTINUOUS": SyncedTableSchedulingPolicy.CONTINUOUS,
    }[scheduling]

    w = WorkspaceClient()
    print(
        f"Creating {len(SYNCED_TABLES)} synced tables in {uc_catalog}.{target_schema} "
        f"backed by Lakebase Autoscaling project '{db_instance}' / db '{logical_db}' "
        f"(scheduling={scheduling}, dry_run={args.dry_run})"
    )

    failures = 0
    for tbl in SYNCED_TABLES:
        name = str(tbl["name"])
        pk = list(tbl["pk"])  # type: ignore[arg-type]
        source = f"{uc_catalog}.{uc_schema}.{name}"
        target_name = f"{name}{suffix}"
        target = f"{uc_catalog}.{target_schema}.{target_name}"

        print(f"  {source} → {target}  pk={pk}", end="  ")

        if args.dry_run:
            print("DRY-RUN")
            continue

        try:
            w.database.create_synced_database_table(
                SyncedDatabaseTable(
                    name=target,
                    database_instance_name=db_instance,
                    logical_database_name=logical_db,
                    spec=SyncedTableSpec(
                        source_table_full_name=source,
                        primary_key_columns=pk,
                        scheduling_policy=sched_enum,
                        create_database_objects_if_missing=True,
                        new_pipeline_spec=NewPipelineSpec(
                            storage_catalog=uc_catalog,
                            storage_schema=target_schema,
                        ),
                    ),
                )
            )
            print("OK")
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "already exists" in msg.lower():
                print("SKIP (already exists)")
                continue
            print(f"ERROR: {msg[:200]}")
            failures += 1

    print(f"Done. failures={failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
