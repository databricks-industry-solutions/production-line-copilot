"""Create Synced Tables to replicate Gold-ready Delta tables into Lakebase.

Uses Continuous scheduling for near-real-time sync.  Requires CDF on all
source tables (already set by init_realtime_uc.py).

Prerequisite: Lakebase Autoscaling project must exist and be accessible.

Usage:
  DATABRICKS_HOST=... \
  LAKEBASE_CATALOG=<lakebase_catalog> \
  UC_CATALOG=main UC_SCHEMA=line_manager_streaming \
  python scripts/setup_synced_tables.py
"""

from __future__ import annotations

import argparse
import os
import sys


def _env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, default)
    if v is None or v == "":
        raise ValueError(f"Missing required env var: {name}")
    return v


# machine_state, line_kpi and event_log used to be synced here. They were
# dropped because all three named empty Delta tables that nothing wrote, so the
# sync provisioned (and billed for) a pipeline that mirrored zero rows into
# Postgres — and nothing read the mirror: the live UI reads `ai_serving.*`,
# which the quality gate upserts directly. See realtime_store.py.
SYNCED_TABLES = [
    {"name": "inventory_status", "pk": ["shift_id", "material_id"]},
    {"name": "scenario_commands", "pk": ["command_id"]},
]


def _apply_cli() -> None:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--catalog", default=None)
    p.add_argument("--schema", default=None)
    p.add_argument("--lakebase-catalog", default=None)
    p.add_argument("--lakebase-schema", default=None)
    ns, _unknown = p.parse_known_args()
    if ns.catalog:
        os.environ["UC_CATALOG"] = ns.catalog
    if ns.schema:
        os.environ["UC_SCHEMA"] = ns.schema
    if ns.lakebase_catalog:
        os.environ["LAKEBASE_CATALOG"] = ns.lakebase_catalog
    if ns.lakebase_schema:
        os.environ["LAKEBASE_SCHEMA"] = ns.lakebase_schema


def main() -> None:
    _apply_cli()
    lb_catalog = os.environ.get("LAKEBASE_CATALOG", "").strip()
    if not lb_catalog or lb_catalog == "__SKIP__":
        print("Skipping synced tables: lakebase_catalog is empty or __SKIP__ (configure in project-settings.yaml).")
        return

    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service.database import (
        NewPipelineSpec,
        SyncedDatabaseTable,
        SyncedTableSchedulingPolicy,
        SyncedTableSpec,
    )

    uc_catalog = os.environ.get("UC_CATALOG", "main")
    uc_schema = os.environ.get("UC_SCHEMA", "line_manager_streaming")
    lb_catalog = _env("LAKEBASE_CATALOG")
    lb_schema = os.environ.get("LAKEBASE_SCHEMA", "public")

    w = WorkspaceClient()

    for tbl in SYNCED_TABLES:
        source = f"{uc_catalog}.{uc_schema}.{tbl['name']}"
        target = f"{lb_catalog}.{lb_schema}.{tbl['name']}"

        print(f"Creating synced table: {source} → {target} ...", end=" ")
        try:
            w.database.create_synced_database_table(
                SyncedDatabaseTable(
                    name=target,
                    spec=SyncedTableSpec(
                        source_table_full_name=source,
                        primary_key_columns=tbl["pk"],
                        scheduling_policy=SyncedTableSchedulingPolicy.CONTINUOUS,
                        new_pipeline_spec=NewPipelineSpec(
                            storage_catalog=lb_catalog,
                            storage_schema="staging",
                        ),
                    ),
                )
            )
            print("OK")
        except Exception as e:
            if "already exists" in str(e).lower():
                print("SKIP (already exists)")
            else:
                print(f"ERROR: {e}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        raise
