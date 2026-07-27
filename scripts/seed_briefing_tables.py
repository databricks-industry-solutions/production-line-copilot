"""Seed the three shift-handover reference tables in a streaming UC schema.

Tables seeded:
  * shift_crew          (operator rosters, 12 per shift)
  * production_schedule (planned cases, SKU, ideal_rate_bpm, time window)
  * cip_schedule        (clean-in-place windows during the shift)

These tables exist in the legacy seed schema ``main.line_manager_ai_kit``,
but they may be missing from the live streaming schema
(``<your_catalog>.line_manager_streaming``). If the live
workspace is on a different UC metastore than the seed workspace, the
``bootstrap_reference_data.py`` CTAS path cannot reach the source — this
seeder embeds the rows directly from ``scripts/_briefing_fixtures.json``
and writes them via the target workspace's SQL warehouse.

The seeder also writes per-environment "mirror" rows so each app's
``sim_shift_id`` resolves to real briefing data, e.g. ``shift_1_dev``
mirroring ``shift_1`` for the dev bundle target.

It is idempotent (``CREATE OR REPLACE TABLE``).

Usage::

  databricks --profile <profile> ... (sets DATABRICKS_HOST + WAREHOUSE)
  python scripts/seed_briefing_tables.py \\
      --profile <profile> \\
      --warehouse-id <warehouse_id> \\
      --catalog <your_catalog> \\
      --schema line_manager_streaming

  # Optional: also seed the dev schema in the same workspace
  python scripts/seed_briefing_tables.py \\
      --profile <profile> \\
      --warehouse-id <warehouse_id> \\
      --catalog <your_catalog> \\
      --schema line_manager_streaming_dev
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "scripts" / "_briefing_fixtures.json"

# Per-environment mirrors. Each mirror copies all rows for the source shift_id
# under a new shift_id so the per-env producer's sim_shift_id finds them.
MIRRORS = {
    "shift_1": ["shift_1_dev", "shift_1_west"],
}

# Column → SQL type. We pin these explicitly so CREATE TABLE produces a schema
# identical to the legacy reference tables (DESCRIBE-verified before extraction).
TABLE_SCHEMAS = {
    "shift_crew": [
        ("shift_id", "STRING"),
        ("operator_id", "STRING"),
        ("operator_name", "STRING"),
        ("role", "STRING"),
        ("assigned_machine", "STRING"),
        ("skill_level", "BIGINT"),
        ("skills", "STRING"),
        ("status", "STRING"),
    ],
    "production_schedule": [
        ("shift_id", "STRING"),
        ("date", "STRING"),
        ("shift_number", "BIGINT"),
        ("start_time", "STRING"),
        ("end_time", "STRING"),
        ("sku", "STRING"),
        ("sku_name", "STRING"),
        ("planned_cases", "BIGINT"),
        ("actual_cases", "BIGINT"),
        ("ideal_rate_bpm", "BIGINT"),
        ("status", "STRING"),
        ("batch_id", "STRING"),
    ],
    "cip_schedule": [
        ("cip_id", "STRING"),
        ("shift_id", "STRING"),
        ("machine_id", "STRING"),
        ("planned_start", "STRING"),
        ("planned_end", "STRING"),
        ("actual_start", "STRING"),
        ("actual_end", "STRING"),
        ("cip_type", "STRING"),
        ("chemical_used", "STRING"),
        ("duration_minutes", "BIGINT"),
        ("status", "STRING"),
    ],
}


def _sql_literal(value, col_type: str) -> str:
    if value is None:
        return "NULL"
    if col_type == "BIGINT":
        return str(int(value))
    # STRING: escape single quotes by doubling them.
    return "'" + str(value).replace("'", "''") + "'"


def _values_clause(row: dict, columns: list[tuple[str, str]]) -> str:
    return "(" + ", ".join(_sql_literal(row.get(name), tp) for name, tp in columns) + ")"


def _expand_mirrors(rows: list[dict]) -> list[dict]:
    """For each row whose shift_id is a mirror source, emit copies under the
    mirror shift_ids in addition to the original row."""
    expanded = list(rows)
    for r in rows:
        src = r.get("shift_id")
        for dst_shift in MIRRORS.get(src, []):
            mirror = dict(r)
            mirror["shift_id"] = dst_shift
            # IDs that embed the shift_id need a suffix so we don't violate
            # any implicit uniqueness assumptions readers might have.
            if "operator_id" in mirror and mirror["operator_id"]:
                mirror["operator_id"] = f"{mirror['operator_id']}_{dst_shift.split('_', 1)[1]}"
            if "cip_id" in mirror and mirror["cip_id"]:
                mirror["cip_id"] = f"{mirror['cip_id']}_{dst_shift.split('_', 1)[1]}"
            if "batch_id" in mirror and mirror["batch_id"]:
                mirror["batch_id"] = f"{mirror['batch_id']}_{dst_shift.split('_', 1)[1]}"
            expanded.append(mirror)
    return expanded


def _exec_sql(profile: str, warehouse_id: str, stmt: str) -> dict:
    """Run a single SQL statement via the Databricks SQL Statements API.

    Returns the response dict. Raises on non-SUCCEEDED state.
    """
    result = subprocess.run(
        [
            "databricks", "--profile", profile, "api", "post",
            "/api/2.0/sql/statements",
            "--json", json.dumps({
                "statement": stmt,
                "warehouse_id": warehouse_id,
                "wait_timeout": "50s",
            }),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"CLI error: {result.stderr.strip() or result.stdout.strip()}")
    try:
        d = json.loads(result.stdout)
    except Exception as exc:
        raise RuntimeError(f"non-JSON response: {result.stdout[:300]}") from exc
    state = d.get("status", {}).get("state")
    if state != "SUCCEEDED":
        msg = d.get("status", {}).get("error", {}).get("message", state)
        raise RuntimeError(f"SQL {state}: {msg[:300]}")
    return d


def _seed_table(profile: str, warehouse_id: str, fq: str, columns: list[tuple[str, str]],
                rows: list[dict]) -> int:
    col_decl = ", ".join(f"{name} {tp}" for name, tp in columns)
    _exec_sql(profile, warehouse_id, f"CREATE OR REPLACE TABLE {fq} ({col_decl}) USING DELTA")
    if not rows:
        return 0
    # Chunk the INSERTs so we don't exceed any backend statement-size limits.
    chunk = 50
    # why: 50 rows keeps SQL Statements API payloads below practical size limits.
    for i in range(0, len(rows), chunk):
        values = ", ".join(_values_clause(r, columns) for r in rows[i:i + chunk])
        _exec_sql(profile, warehouse_id, f"INSERT INTO {fq} VALUES {values}")
    return len(rows)


def main() -> None:
    p = argparse.ArgumentParser(description="Seed briefing reference tables (crew, plan, CIP)")
    p.add_argument("--profile", required=True, help="Databricks CLI profile")
    p.add_argument("--warehouse-id", required=True, help="SQL warehouse id to run statements through")
    p.add_argument("--catalog", required=True, help="Target UC catalog")
    p.add_argument("--schema", required=True, help="Target UC schema (created if missing)")
    p.add_argument("--fixtures", default=str(FIXTURES), help="Path to the fixtures JSON")
    p.add_argument("--no-mirrors", action="store_true",
                   help="Skip writing shift_1_dev/shift_1_west mirror rows")
    p.add_argument("--grant-select-to", action="append", default=[],
                   help=("Databricks principal (SP id, user email or group) to GRANT SELECT "
                         "on every seeded table. Repeatable. Without this, the app SP "
                         "can't read the rows and the briefing falls back to 'Not on file'."))
    args = p.parse_args()

    fixtures = json.loads(Path(args.fixtures).read_text(encoding="utf-8"))
    print(f"Loaded fixtures from {args.fixtures}")

    print(f"\nTarget: {args.profile} → {args.catalog}.{args.schema}")
    _exec_sql(args.profile, args.warehouse_id, f"CREATE SCHEMA IF NOT EXISTS {args.catalog}.{args.schema}")

    for tbl, columns in TABLE_SCHEMAS.items():
        rows = fixtures.get(tbl, [])
        if not args.no_mirrors:
            rows = _expand_mirrors(rows)
        fq = f"{args.catalog}.{args.schema}.{tbl}"
        n = _seed_table(args.profile, args.warehouse_id, fq, columns, rows)
        print(f"  {fq:80s}  {n} rows")

        for principal in args.grant_select_to:
            _exec_sql(
                args.profile,
                args.warehouse_id,
                f"GRANT SELECT ON TABLE {fq} TO `{principal}`",
            )
            print(f"    GRANT SELECT TO `{principal}`")

    print("\nDone.")


if __name__ == "__main__":
    main()
