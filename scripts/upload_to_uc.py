"""Upload generated Parquet data to Unity Catalog Delta tables.

Usage:
    python scripts/upload_to_uc.py [--catalog CATALOG] [--schema SCHEMA]

Requires:
    - databricks-sql-connector
    - pandas
    - DATABRICKS_HOST and DATABRICKS_TOKEN environment variables
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "generated"

TABLES = [
    "machine_sensors",
    "machine_bom",
    "scenario_index",
    "kpi_recommendations",
    "scenario_moments",
    "line_constraints",
    "production_schedule",
    "machine_telemetry",
    "event_log",
    "quality_checks",
    "inventory_status",
    "consumption_log",
    "oee_gold",
    "machine_state",
    "line_kpi",
    "cip_schedule",
    "shift_crew",
    "maintenance_schedule",
]


def upload_table(cursor, catalog: str, schema: str, table_name: str, df: pd.DataFrame) -> None:
    """Upload a DataFrame to a Unity Catalog table using CTAS from VALUES."""
    full_name = f"{catalog}.{schema}.{table_name}"
    print(f"  Uploading {table_name} ({len(df)} rows)...")

    cursor.execute(f"DROP TABLE IF EXISTS {full_name}")

    if len(df) == 0:
        print(f"  Skipping {table_name} — empty DataFrame")
        return

    cols = df.columns.tolist()
    col_defs = []
    for col in cols:
        dtype = df[col].dtype
        if pd.api.types.is_integer_dtype(dtype):
            col_defs.append(f"`{col}` BIGINT")
        elif pd.api.types.is_float_dtype(dtype):
            col_defs.append(f"`{col}` DOUBLE")
        elif pd.api.types.is_bool_dtype(dtype):
            col_defs.append(f"`{col}` BOOLEAN")
        else:
            col_defs.append(f"`{col}` STRING")

    create_sql = f"CREATE TABLE {full_name} ({', '.join(col_defs)})"
    cursor.execute(create_sql)

    chunk_size = 500
    for start in range(0, len(df), chunk_size):
        chunk = df.iloc[start : start + chunk_size]
        rows = []
        for _, row in chunk.iterrows():
            vals = []
            for col in cols:
                v = row[col]
                if pd.isna(v):
                    vals.append("NULL")
                elif isinstance(v, str):
                    escaped = v.replace("'", "''")
                    vals.append(f"'{escaped}'")
                elif isinstance(v, bool):
                    vals.append("TRUE" if v else "FALSE")
                else:
                    vals.append(str(v))
            rows.append(f"({', '.join(vals)})")

        insert_sql = f"INSERT INTO {full_name} VALUES {', '.join(rows)}"
        cursor.execute(insert_sql)

    print(f"  ✓ {table_name}: {len(df)} rows uploaded")


def main():
    parser = argparse.ArgumentParser(description="Upload generated data to Unity Catalog")
    parser.add_argument("--catalog", default=os.environ.get("UC_CATALOG", "main"))
    parser.add_argument("--schema", default=os.environ.get("UC_SCHEMA", "line_manager_ai_kit"))
    args = parser.parse_args()

    if not DATA_DIR.exists():
        print(f"Error: Data directory not found: {DATA_DIR}")
        print("Run the data generation pipeline first: python -m src.data_generation.main")
        sys.exit(1)

    from databricks import sql as dbsql

    host = os.environ.get("DATABRICKS_HOST", "")
    warehouse_id = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
    if not host or not warehouse_id:
        print("Error: DATABRICKS_HOST and DATABRICKS_WAREHOUSE_ID must be set")
        sys.exit(1)

    print(f"Connecting to {host}, warehouse {warehouse_id}...")
    connection = dbsql.connect(
        server_hostname=host.replace("https://", ""),
        http_path=f"/sql/1.0/warehouses/{warehouse_id}",
    )
    cursor = connection.cursor()

    cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {args.catalog}.{args.schema}")

    total_rows = 0
    for table_name in TABLES:
        parquet_path = DATA_DIR / f"{table_name}.parquet"
        if not parquet_path.exists():
            print(f"  ⚠ {table_name}.parquet not found, skipping")
            continue

        df = pd.read_parquet(parquet_path)
        upload_table(cursor, args.catalog, args.schema, table_name, df)
        total_rows += len(df)

    cursor.close()
    connection.close()

    print(f"\nDone! Uploaded {total_rows:,} total rows to {args.catalog}.{args.schema}")


if __name__ == "__main__":
    main()
