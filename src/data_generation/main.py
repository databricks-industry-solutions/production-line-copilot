"""Data generation pipeline orchestrator.

Runs all generators in order:
  reference -> schedule -> telemetry -> events -> quality -> inventory -> consumption -> OEE -> gold views.

Saves each dataset to data/generated/ as parquet (or CSV for small tables).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pandas as pd


def run_pipeline(output_dir: str | None = None) -> dict[str, pd.DataFrame]:
    """Execute the full data generation pipeline.

    Returns a dict of table_name -> DataFrame for all generated datasets.
    If *output_dir* is provided, each DataFrame is saved as parquet.
    """
    from .cip_generator import generate_cip_schedule
    from .consumption_generator import generate_consumption_log
    from .crew_generator import generate_shift_crew
    from .event_generator import generate_event_log
    from .gold_view_builder import generate_gold_views
    from .inventory_generator import generate_inventory_status
    from .maintenance_generator import generate_maintenance_schedule
    from .oee_calculator import generate_oee_gold
    from .quality_generator import generate_quality_checks
    from .reference_data import generate_all_reference_data
    from .schedule_generator import generate_production_schedule
    from .telemetry_generator import generate_all_telemetry

    datasets: dict[str, pd.DataFrame] = {}

    t0 = time.time()
    print("=" * 60)
    print("DATA GENERATION PIPELINE")
    print("=" * 60)

    # 1. Reference data
    _step("Reference data")
    ref = generate_all_reference_data()
    for name, df in ref.items():
        datasets[name] = df
        print(f"  {name}: {len(df):,} rows")

    # 2. Production schedule
    _step("Production schedule")
    datasets["production_schedule"] = generate_production_schedule()
    print(f"  production_schedule: {len(datasets['production_schedule']):,} rows")

    # 3. Telemetry (~6.6M rows — largest dataset)
    _step("Telemetry")
    datasets["machine_telemetry"] = generate_all_telemetry()

    # 4. Events
    _step("Event log")
    datasets["event_log"] = generate_event_log()
    print(f"  event_log: {len(datasets['event_log']):,} rows")

    # 5. Quality checks
    _step("Quality checks")
    datasets["quality_checks"] = generate_quality_checks()
    print(f"  quality_checks: {len(datasets['quality_checks']):,} rows")

    # 6. Inventory status
    _step("Inventory status")
    datasets["inventory_status"] = generate_inventory_status()
    print(f"  inventory_status: {len(datasets['inventory_status']):,} rows")

    # 7. Consumption log
    _step("Consumption log")
    datasets["consumption_log"] = generate_consumption_log()
    print(f"  consumption_log: {len(datasets['consumption_log']):,} rows")

    # 8. OEE gold
    _step("OEE gold")
    datasets["oee_gold"] = generate_oee_gold()
    print(f"  oee_gold: {len(datasets['oee_gold']):,} rows")

    # 9. Gold views (machine_state + line_kpi)
    _step("Gold views")
    machine_state, line_kpi = generate_gold_views()
    datasets["machine_state"] = machine_state
    datasets["line_kpi"] = line_kpi
    print(f"  machine_state: {len(machine_state):,} rows")
    print(f"  line_kpi: {len(line_kpi):,} rows")

    # 10. CIP schedule
    _step("CIP schedule")
    datasets["cip_schedule"] = generate_cip_schedule()
    print(f"  cip_schedule: {len(datasets['cip_schedule']):,} rows")

    # 11. Shift crew
    _step("Shift crew")
    datasets["shift_crew"] = generate_shift_crew()
    print(f"  shift_crew: {len(datasets['shift_crew']):,} rows")

    # 12. Maintenance schedule
    _step("Maintenance schedule")
    datasets["maintenance_schedule"] = generate_maintenance_schedule()
    print(f"  maintenance_schedule: {len(datasets['maintenance_schedule']):,} rows")

    elapsed = time.time() - t0
    total_rows = sum(len(df) for df in datasets.values())
    print(f"\n{'=' * 60}")
    print(f"COMPLETE: {len(datasets)} tables, {total_rows:,} total rows in {elapsed:.1f}s")
    print("=" * 60)

    if output_dir:
        _save_datasets(datasets, output_dir)

    return datasets


def _step(name: str) -> None:
    print(f"\n--- {name} ---")


def _save_datasets(datasets: dict[str, pd.DataFrame], output_dir: str) -> None:
    """Save all datasets to parquet files."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    print(f"\nSaving to {out}/")
    for name, df in datasets.items():
        path = out / f"{name}.parquet"
        df.to_parquet(path, index=False)
        size_mb = os.path.getsize(path) / (1024 * 1024)
        print(f"  {name}.parquet ({size_mb:.1f} MB, {len(df):,} rows)")


if __name__ == "__main__":
    run_pipeline(output_dir="data/generated")
