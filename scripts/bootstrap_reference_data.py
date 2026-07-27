"""Bootstrap reference / dimension tables into the streaming catalog.

Default mode generates the 10 reference tables in-process via
``src.data_generation`` and uploads with CREATE TABLE + chunked INSERT.

When ``SRC_CATALOG`` and ``SRC_SCHEMA`` (or ``--src-catalog`` / ``--src-schema``)
are explicitly set to real values, copies from the source schema instead
(legacy bootstrap for workspaces that already have ``main.line_manager_ai_kit``).

One-time bootstrap — safe to re-run.

Tables bootstrapped:
  machine_sensors, machine_bom, scenario_index, scenario_moments,
  kpi_recommendations, line_constraints, production_schedule,
  shift_crew, cip_schedule, maintenance_schedule

Usage:
  DATABRICKS_HOST=... DATABRICKS_WAREHOUSE_ID=... \
  python scripts/bootstrap_reference_data.py
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REFERENCE_TABLES = [
    "machine_sensors",
    "machine_bom",
    "scenario_index",
    "scenario_moments",
    "kpi_recommendations",
    "line_constraints",
    "production_schedule",
    "shift_crew",
    "cip_schedule",
    "maintenance_schedule",
]

_SKIP_SENTINELS = frozenset({"", "SET_ME", "__SKIP__"})


def _env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, default)
    if v is None or v == "":
        raise ValueError(f"Missing required env var: {name}")
    return v


def _ensure_repo_on_path() -> None:
    """Make ``src.data_generation`` and ``upload_to_uc`` importable when run as a script.

    Serverless ``spark_python_task`` runs this file via ``exec()`` with no
    ``__file__`` global, so fall back to ``sys.argv[0]`` to locate the repo root.
    """
    here = Path(os.path.abspath(globals().get("__file__") or sys.argv[0]))
    repo = here.resolve().parent.parent
    scripts = here.resolve().parent
    for p in (repo, scripts):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))


def _is_explicit_src(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip() not in _SKIP_SENTINELS


def _should_copy_from_src(src_catalog: str | None, src_schema: str | None) -> bool:
    return _is_explicit_src(src_catalog) and _is_explicit_src(src_schema)


def _apply_cli() -> None:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--src-catalog", default=None)
    p.add_argument("--src-schema", default=None)
    p.add_argument("--catalog", default=None)
    p.add_argument("--schema", default=None)
    p.add_argument("--host", default=None)
    p.add_argument("--warehouse-id", default=None)
    ns, _unknown = p.parse_known_args()
    if ns.src_catalog is not None:
        os.environ["SRC_CATALOG"] = ns.src_catalog
    if ns.src_schema is not None:
        os.environ["SRC_SCHEMA"] = ns.src_schema
    if ns.catalog:
        os.environ["UC_CATALOG"] = ns.catalog
    if ns.schema:
        os.environ["UC_SCHEMA"] = ns.schema
    if ns.host:
        os.environ["DATABRICKS_HOST"] = ns.host
    if ns.warehouse_id:
        os.environ["DATABRICKS_WAREHOUSE_ID"] = ns.warehouse_id


def _generate_reference_tables() -> dict:
    _ensure_repo_on_path()
    from src.data_generation.cip_generator import generate_cip_schedule
    from src.data_generation.crew_generator import generate_shift_crew
    from src.data_generation.maintenance_generator import generate_maintenance_schedule
    from src.data_generation.reference_data import generate_all_reference_data
    from src.data_generation.schedule_generator import generate_production_schedule

    tables = dict(generate_all_reference_data())
    tables["production_schedule"] = generate_production_schedule()
    tables["shift_crew"] = generate_shift_crew()
    tables["cip_schedule"] = generate_cip_schedule()
    tables["maintenance_schedule"] = generate_maintenance_schedule()
    return tables


def _copy_reference_tables(cur, src: str, dst: str) -> None:
    for table in REFERENCE_TABLES:
        cur.execute(
            f"CREATE OR REPLACE TABLE {dst}.{table} AS SELECT * FROM {src}.{table}"
        )
        cur.execute(f"SELECT COUNT(*) FROM {dst}.{table}")
        count = cur.fetchone()[0]
        print(f"OK: {dst}.{table} ({count} rows)")


def _upload_reference_tables(cur, dst_catalog: str, dst_schema: str, tables: dict) -> None:
    from upload_to_uc import upload_table

    for table in REFERENCE_TABLES:
        df = tables[table]
        upload_table(cur, dst_catalog, dst_schema, table, df)
        print(f"OK: {dst_catalog}.{dst_schema}.{table} ({len(df)} rows)")


def _populate_inline_tables(cur, dst: str) -> None:
    # Populate machine_registry from MACHINES config
    cur.execute(f"DELETE FROM {dst}.machine_registry")
    for m_data in [
        ("DEPALLETIZER", "Depalletizer", "depalletizer", 1, 441.0, "wet_end", "KHS"),
        ("CONVEYOR_1", "Conveyor 1", "conveyor", 2, 630.0, "wet_end", "Intralox"),
        ("UNCASER", "Uncaser", "uncaser", 3, 441.0, "wet_end", "KHS"),
        ("BOTTLE_WASHER", "Bottle Washer", "washer", 4, 441.0, "wet_end", "KHS"),
        ("EMPTY_INSPECTOR", "Empty Bottle Inspector", "inspector", 5, 441.0, "wet_end", "HEUFT"),
        ("CONVEYOR_2", "Conveyor 2", "conveyor", 6, 630.0, "wet_end", "Intralox"),
        ("FILLER", "Filler", "filler", 7, 420.0, "filling", "KHS"),
        ("CAPPER", "Capper", "capper", 8, 420.0, "filling", "KHS"),
        ("PASTEURIZER", "Pasteurizer", "pasteurizer", 9, 441.0, "thermal", "KHS"),
        ("LABELLER", "Labeller", "labeller", 10, 420.0, "labelling", "Krones"),
        ("CODER", "Coder", "coder", 11, 441.0, "labelling", "Markem-Imaje"),
        ("CONVEYOR_3", "Conveyor 3", "conveyor", 12, 630.0, "packaging", "Intralox"),
        ("CASE_PACKER", "Case Packer", "packer", 13, 420.0, "packaging", "OCME"),
        ("SHRINK_WRAPPER", "Shrink Wrapper", "shrink", 14, 441.0, "packaging", "OCME"),
        ("CASE_SEALER", "Case Sealer", "sealer", 15, 441.0, "packaging", "3M-Matic"),
        ("CHECKWEIGHER", "Checkweigher", "checkweigher", 16, 441.0, "packaging", "Mettler-Toledo"),
        ("CONVEYOR_4", "Conveyor 4", "conveyor", 17, 630.0, "palletizing", "Intralox"),
        ("PALLETIZER", "Palletizer", "palletizer", 18, 441.0, "palletizing", "KUKA"),
        ("STRETCH_WRAPPER", "Stretch Wrapper", "stretch", 19, 441.0, "palletizing", "Robopac"),
    ]:
        cur.execute(
            f"INSERT INTO {dst}.machine_registry VALUES ('{m_data[0]}', '{m_data[1]}', '{m_data[2]}', {m_data[3]}, {m_data[4]}, '{m_data[5]}', '{m_data[6]}')"
        )
    cur.execute(f"SELECT COUNT(*) FROM {dst}.machine_registry")
    print(f"OK: {dst}.machine_registry ({cur.fetchone()[0]} rows)")

    # Populate alert_thresholds
    cur.execute(f"DELETE FROM {dst}.alert_thresholds")
    for t_data in [
        ("FILLER", "speed_pct", 85.0, 105.0, 70.0, 110.0),
        ("CAPPER", "speed_pct", 85.0, 105.0, 70.0, 110.0),
        ("LABELLER", "speed_pct", 85.0, 105.0, 70.0, 110.0),
        ("CASE_PACKER", "speed_pct", 85.0, 105.0, 70.0, 110.0),
        ("FILLER", "alarm_count", None, 3.0, None, 5.0),
        ("CAPPER", "alarm_count", None, 3.0, None, 5.0),
        ("LABELLER", "alarm_count", None, 3.0, None, 5.0),
    ]:
        vals = ", ".join("NULL" if v is None else f"'{v}'" if isinstance(v, str) else str(v) for v in t_data)
        cur.execute(f"INSERT INTO {dst}.alert_thresholds VALUES ({vals})")
    cur.execute(f"SELECT COUNT(*) FROM {dst}.alert_thresholds")
    print(f"OK: {dst}.alert_thresholds ({cur.fetchone()[0]} rows)")

    # Populate maintenance_windows with defaults
    cur.execute(f"DELETE FROM {dst}.maintenance_windows")
    for mw in [
        ("FILLER", "preventive", 720, "2025-01-01T00:00:00", "2025-02-01T00:00:00"),
        ("CAPPER", "preventive", 720, "2025-01-01T00:00:00", "2025-02-01T00:00:00"),
        ("LABELLER", "preventive", 500, "2025-01-01T00:00:00", "2025-01-22T00:00:00"),
        ("PASTEURIZER", "preventive", 1000, "2025-01-01T00:00:00", "2025-02-12T00:00:00"),
        ("CASE_PACKER", "preventive", 500, "2025-01-01T00:00:00", "2025-01-22T00:00:00"),
    ]:
        cur.execute(
            f"INSERT INTO {dst}.maintenance_windows VALUES ('{mw[0]}', '{mw[1]}', {mw[2]}, '{mw[3]}', '{mw[4]}')"
        )
    cur.execute(f"SELECT COUNT(*) FROM {dst}.maintenance_windows")
    print(f"OK: {dst}.maintenance_windows ({cur.fetchone()[0]} rows)")


def main() -> None:
    _apply_cli()
    src_catalog = os.environ.get("SRC_CATALOG")
    src_schema = os.environ.get("SRC_SCHEMA")
    dst_catalog = os.environ.get("UC_CATALOG", "main")
    dst_schema = os.environ.get("UC_SCHEMA", "line_manager_streaming")

    host = _env("DATABRICKS_HOST")
    warehouse_id = _env("DATABRICKS_WAREHOUSE_ID")

    # Serverless-safe SQL exec (SDK Statement Execution API — no pyarrow).
    _ensure_repo_on_path()
    from _uc_sql import StmtCursor

    cur = StmtCursor(warehouse_id, host=host)

    dst = f"{dst_catalog}.{dst_schema}"

    try:
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {dst}")

        if _should_copy_from_src(src_catalog, src_schema):
            src = f"{src_catalog}.{src_schema}"
            print(f"Copying reference tables from {src} -> {dst}")
            _copy_reference_tables(cur, src, dst)
        else:
            print(f"Generating reference tables in-process -> {dst}")
            tables = _generate_reference_tables()
            _upload_reference_tables(cur, dst_catalog, dst_schema, tables)

        _populate_inline_tables(cur, dst)

    finally:
        cur.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        raise
