"""Map the streaming simulator's bronze/reference data into the Manufacturing
industry data model (MVM) — a *governed silver* layer for the SQL / Genie agents.

Where this fits
---------------
The realtime hot path stays as-is: the producer writes bronze + Lakebase
``ai_serving.*_latest`` and the UI reads those over SSE. This mapper lands the
same information into the governed, standards-aligned MVM tables (installed by
``notebooks/06_Install_Industry_Data_Model.py``) so Genie and the SQL tools can
answer questions against ~98 curated metric views instead of ad-hoc serving tables.

Two population modes share the mapping definitions below:

- **Batch seed** (``build_insert`` → ``INSERT OVERWRITE``, this module's ``main``):
  run once at setup to fully materialize every target from the bronze/reference
  tables via the SDK Statement Execution API (serverless-safe, no Spark session).
  Ideal for the slow-changing DIMENSIONS (equipment_register, location,
  material_master, schedule).
- **Streaming silver** (``build_merge`` → ``MERGE INTO``): the
  ``streaming_quality_sync`` job upserts each micro-batch into the FACT tables
  (failure_record, stock_balance, production_line, inspection_result) from inside
  foreachBatch, so the governed model rides the same Zerobus→bronze streaming path
  at seconds latency — replacing the retired 30-min batch refresh job.

Design
------
- Serverless-safe: the batch path executes plain SQL through the SDK Statement
  Execution API (no Spark session, no ``pyarrow``), matching
  ``scripts/init_realtime_uc.py``; the streaming path runs ``MERGE`` on the job's
  live Spark session.
- Schema-drift tolerant: each mapping declares ``{target_column: sql_expr}``; at
  runtime we introspect the target table and keep only the columns that actually
  exist, so a newer/older MVM version never breaks the write.
- Idempotent: ``INSERT OVERWRITE`` fully replaces each target from bronze;
  ``MERGE`` matches on a stable surrogate key so re-processing a micro-batch on
  Spark retry re-upserts the same rows. Surrogate keys are ``xxhash64(natural_key)``
  so foreign keys line up across tables (e.g. ``failure_record.equipment_register_id``
  == ``equipment_register.equipment_register_id`` for the same ``machine_id``).

Usage:
  MVM_CATALOG=line_manager_silver_mvm UC_CATALOG=main UC_SCHEMA=line_manager_streaming \
  DATABRICKS_HOST=... DATABRICKS_WAREHOUSE_ID=... \
  python -m agent_server.serving.silver_mapper
"""
from __future__ import annotations

import argparse
import os
import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Mapping:
    """One bronze/reference -> MVM table mapping.

    ``columns`` maps a *target* column name to a SQL expression evaluated over
    ``from_clause``. ``{src}`` in ``from_clause`` is substituted with the fully
    qualified source schema (``catalog.schema``). Only target columns that exist
    in the installed model are emitted.

    Two population modes share these definitions:

    * **Batch seed** (``build_insert`` -> ``INSERT OVERWRITE``) — used at setup
      to fully materialize a table from the source *bronze* tables. Ideal for
      the slow-changing dimensions (equipment_register, location, material_master,
      schedule) that are seeded once.
    * **Streaming silver** (``build_merge`` -> ``MERGE INTO``) — used inside the
      ``streaming_quality_sync`` foreachBatch to upsert each *micro-batch* into
      the governed model at streaming latency (seconds), so the fact tables
      (failure_record, stock_balance, production_line, inspection_result) stay
      fresh WITHOUT a separate 30-min batch path. A mapping is stream-enabled
      when it declares both ``merge_key`` and ``stream_from``.

    ``merge_key`` is the target column MERGE matches on (a stable surrogate key,
    so re-processing a micro-batch is idempotent). ``stream_from`` is the FROM
    clause evaluated over the per-batch temp view (``{batch}`` is substituted
    with the view name). ``stream_columns`` overrides ``columns`` for the
    streaming path when the batch-shaped source differs from the full-table one
    (e.g. production_line reads the batch's latest KPI row, not a table scan).
    """

    schema: str
    table: str
    columns: dict[str, str]
    from_clause: str = ""
    description: str = ""
    key: str = field(default="")
    merge_key: str = ""
    stream_from: str = ""
    stream_columns: dict[str, str] | None = None

    @property
    def is_streaming(self) -> bool:
        """True when this mapping can be upserted per micro-batch via MERGE."""
        return bool(self.merge_key and self.stream_from)

    def stream_cols(self) -> dict[str, str]:
        """Columns used by the streaming MERGE (falls back to batch columns)."""
        return self.stream_columns or self.columns


# Surrogate-key helper: stable BIGINT from a natural key (nulls -> '').
def _sk(expr: str) -> str:
    return f"xxhash64(COALESCE(CAST({expr} AS STRING), ''))"


# Columns the streaming MERGE writes on INSERT but never rewrites on UPDATE.
# See build_merge for why: creation facts must not be restamped by a replay, and
# lifecycle state must not be reset to its seed value by one.
INSERT_ONLY_COLUMNS = frozenset({
    "created_timestamp",
    "ncr_status",
    "ncr_type",
})


# The single physical line this demo models.
_LINE_CODE = "LINE_1"
_LINE_ID = f"xxhash64('{_LINE_CODE}')"

# Shift length in minutes, mirroring streaming_sim.producer.SHIFT_WINDOW_MINUTES.
# Needed to convert the simulator's per-minute capacity rates into the governed
# model's per-shift capacity columns. Duplicated rather than imported because
# this module runs on the SQL warehouse without the simulator on the path.
SHIFT_MINUTES = 480


MAPPINGS: list[Mapping] = [
    # ── asset.location (one per production zone) ─────────────────────────────
    Mapping(
        schema="asset",
        table="location",
        key="location",
        description="Production zones -> functional locations",
        from_clause="FROM (SELECT DISTINCT zone FROM {src}.machine_registry WHERE zone IS NOT NULL) z",
        columns={
            "location_id": _sk("z.zone"),
            "location_name": "z.zone",
            "location_type": "'production_zone'",
            "production_line_code": f"'{_LINE_CODE}'",
            "site_name": "'ProdLine Plant'",
            "location_status": "'active'",
            "created_timestamp": "current_timestamp()",
        },
    ),
    # ── asset.equipment_register (one per machine) ──────────────────────────
    Mapping(
        schema="asset",
        table="equipment_register",
        key="equipment_register",
        description="19-machine line -> equipment register",
        from_clause="FROM {src}.machine_registry mr",
        columns={
            "equipment_register_id": _sk("mr.machine_id"),
            "asset_number": "mr.machine_id",
            "equipment_name": "mr.machine_name",
            "equipment_class": "mr.machine_type",
            "asset_category": "'Production Equipment'",
            "manufacturer_name": "mr.manufacturer",
            "rated_capacity": "mr.nominal_capacity",
            "operational_status": "'active'",
            "criticality_ranking": "'High'",
            "location_id": _sk("mr.zone"),
            "work_center_id": _sk("mr.machine_id"),
            "record_created_timestamp": "current_timestamp()",
        },
    ),
    # ── production.production_line (single row, current OEE) ─────────────────
    Mapping(
        schema="production",
        table="production_line",
        key="production_line",
        description="The packaging line + current OEE from latest line_kpi",
        from_clause="",  # scalar single-row SELECT
        merge_key="production_line_id",
        # Streaming source: the batch's freshest line_kpi row. oee_to_now is
        # ALREADY a percentage (0-100) — do NOT multiply by 100 (see line_kpi_
        # derived / DQX oee range check). The batch-shaped source differs from
        # the full-table one, so it overrides ``columns`` via ``stream_columns``.
        stream_from=(
            "FROM (SELECT oee_to_now, ROW_NUMBER() OVER (ORDER BY ts DESC) rn "
            "FROM {batch}) k WHERE k.rn = 1"
        ),
        stream_columns={
            "production_line_id": _LINE_ID,
            "line_code": f"'{_LINE_CODE}'",
            "operational_status": "'running'",
            "actual_oee_percentage": "CAST(COALESCE(k.oee_to_now, 0) AS DECIMAL(18,2))",
        },
        columns={
            "production_line_id": _LINE_ID,
            "line_code": f"'{_LINE_CODE}'",
            "line_name": "'Production Line 1'",
            "line_type": "'packaging'",
            "operational_status": "'running'",
            # oee_to_now is already a 0-100 percentage; the previous ``* 100``
            # inflated it to 0-10000. Store it directly as the percentage.
            "actual_oee_percentage": (
                "CAST(COALESCE((SELECT oee_to_now FROM {src}.line_kpi_bronze "
                "ORDER BY ts DESC LIMIT 1), 0) AS DECIMAL(18,2))"
            ),
            "target_oee_percentage": "CAST(85.0 AS DECIMAL(18,2))",
            "number_of_stations": (
                "CAST((SELECT COUNT(*) FROM {src}.machine_registry) AS STRING)"
            ),
            # The line is measured end to end in bottle-equivalents/min (~300
            # healthy), not cases: a 420 BPM line is 17.5 cases/min. Labelling
            # it cases_per_min told anyone reading the governed model the line
            # ran 24x faster than it does.
            "throughput_unit_of_measure": "'bottles_per_min'",
            "created_timestamp": "current_timestamp()",
        },
    ),
    # ── production.schedule (from the reference production_schedule) ─────────
    Mapping(
        schema="production",
        table="schedule",
        key="schedule",
        description="Reference production schedule -> MVM schedule",
        from_clause="FROM {src}.production_schedule ps",
        columns={
            "schedule_id": _sk("concat(ps.shift_id, '|', ps.sku)"),
            "schedule_number": "ps.shift_id",
            "planned_quantity": "ps.planned_cases",
            "schedule_status": "ps.status",
            "unit_of_measure": "'cases'",
            "created_timestamp": "current_timestamp()",
        },
    ),
    # ── asset.failure_record (downtime events) ──────────────────────────────
    Mapping(
        schema="asset",
        table="failure_record",
        key="failure_record",
        description="Downtime event log -> failure records",
        from_clause="FROM {src}.event_log_bronze e",
        merge_key="failure_record_id",
        # event_id is unique per row, so a micro-batch never has two source rows
        # for one target key — MERGE is safe without dedup.
        stream_from="FROM {batch} e",
        columns={
            "failure_record_id": _sk("e.event_id"),
            "equipment_register_id": _sk("e.machine_id"),
            "production_line_id": _LINE_ID,
            "failure_number": "e.event_id",
            "failure_datetime": "e.timestamp_start",
            "downtime_start_datetime": "e.timestamp_start",
            "downtime_end_datetime": "e.timestamp_end",
            "downtime_duration_minutes": "e.duration_minutes",
            "failure_mode_description": "e.event_type",
            "failure_cause_description": "e.root_cause",
            "failure_narrative": "e.description",
            "remedy_description": "e.corrective_action",
            "severity_rating": "e.severity",
            "maintenance_type": "'corrective'",
            "record_status": "'closed'",
            "created_timestamp": "current_timestamp()",
        },
    ),
    # ── quality.inspection_result (quality checks) ──────────────────────────
    Mapping(
        schema="quality",
        table="inspection_result",
        key="inspection_result",
        description="Quality checks -> inspection results (variable + attribute)",
        from_clause="FROM {src}.quality_checks_bronze q",
        merge_key="inspection_result_id",
        # check_id is unique per row — safe to MERGE the micro-batch directly.
        stream_from="FROM {batch} q",
        columns={
            "inspection_result_id": _sk("q.check_id"),
            "equipment_register_id": _sk("q.machine_id"),
            "characteristic_type": "'variable'",
            "attribute_result": (
                "CASE lower(q.result) WHEN 'pass' THEN 'pass' "
                "WHEN 'fail' THEN 'fail' ELSE 'not_applicable' END"
            ),
            "measured_value": "q.value",
            "nominal_value": "(q.spec_min + q.spec_max) / 2",
            "lower_spec_limit": "q.spec_min",
            "upper_spec_limit": "q.spec_max",
            "is_out_of_spec": "lower(q.result) = 'fail'",
            "result_status": "q.result",
            "defect_description": "q.parameter",
            "inspection_timestamp": "q.timestamp",
            "inspection_date": "to_date(q.timestamp)",
            "created_timestamp": "current_timestamp()",
        },
    ),
    # ── inventory.material_master (from the machine BOM) ────────────────────
    Mapping(
        schema="inventory",
        table="material_master",
        key="material_master",
        description="Distinct BOM materials -> material master",
        from_clause=(
            "FROM (SELECT DISTINCT material_id, material_name, unit "
            "FROM {src}.machine_bom) m"
        ),
        columns={
            "material_master_id": _sk("m.material_id"),
            "material_number": "m.material_id",
            "material_description": "m.material_name",
            "base_unit_of_measure": "m.unit",
            "material_type": "'RAW'",
            "material_status": "'active'",
            "created_date": "current_date()",
        },
    ),
    # ── inventory.stock_balance (latest on-hand per material) ────────────────
    Mapping(
        schema="inventory",
        table="stock_balance",
        key="stock_balance",
        description="Latest inventory snapshot -> stock balance",
        from_clause=(
            "FROM (SELECT material_id, on_hand_qty, safety_stock, reorder_point, "
            "status, ts, ROW_NUMBER() OVER (PARTITION BY material_id "
            "ORDER BY ts DESC) rn FROM {src}.inventory_status_bronze) i WHERE i.rn = 1"
        ),
        merge_key="stock_balance_id",
        # A micro-batch can carry several snapshots per material; dedup to the
        # freshest per material_id first so MERGE never sees two source rows for
        # one target key (which would raise a multiple-match error).
        stream_from=(
            "FROM (SELECT material_id, on_hand_qty, safety_stock, reorder_point, "
            "status, ts, ROW_NUMBER() OVER (PARTITION BY material_id "
            "ORDER BY ts DESC) rn FROM {batch}) i WHERE i.rn = 1"
        ),
        columns={
            "stock_balance_id": _sk("i.material_id"),
            "material_master_id": _sk("i.material_id"),
            "quantity_on_hand": "i.on_hand_qty",
            "available_quantity": "i.on_hand_qty",
            "safety_stock_quantity": "i.safety_stock",
            "reorder_point": "i.reorder_point",
            "stock_status": "i.status",
            "unit_of_measure": "'unit'",
            "last_updated_timestamp": "i.ts",
        },
    ),
    # ── asset.pm_schedule (preventive-maintenance plan) ─────────────────────
    # Seeded once at setup from the maintenance_windows reference (one planned
    # PM per machine). Slow-changing, so no streaming path. FK equipment_
    # register_id aligns with equipment_register via _sk(machine_id).
    Mapping(
        schema="asset",
        table="pm_schedule",
        key="pm_schedule",
        description="Maintenance windows -> preventive-maintenance schedule",
        from_clause="FROM {src}.maintenance_windows mw",
        columns={
            "pm_schedule_id": _sk("mw.machine_id"),
            "equipment_register_id": _sk("mw.machine_id"),
            "schedule_name": "concat('PM - ', mw.machine_id)",
            "schedule_number": "mw.machine_id",
            "maintenance_type": "mw.pm_type",
            "trigger_type": "'time_based'",
            "frequency_unit": "'hours'",
            "frequency_value": "CAST(mw.interval_hours AS STRING)",
            "last_completion_date": "to_date(mw.last_pm)",
            "next_due_date": "to_date(mw.next_pm)",
            "schedule_status": "'active'",
            "created_timestamp": "current_timestamp()",
        },
    ),
    # ── production.work_center (one per machine/station) ─────────────────────
    # Seeded once at setup from machine_registry. work_center_id aligns with
    # equipment_register.work_center_id (both _sk(machine_id)) so the governed
    # asset↔work-center join holds.
    Mapping(
        schema="production",
        table="work_center",
        key="work_center",
        description="19-machine line -> production work centers",
        from_clause="FROM {src}.machine_registry mr",
        columns={
            "work_center_id": _sk("mr.machine_id"),
            "location_id": _sk("mr.zone"),
            "work_center_code": "mr.machine_id",
            "work_center_name": "mr.machine_name",
            "work_center_category": "mr.machine_type",
            "work_center_status": "'active'",
            "number_of_machines": "'1'",
            # nominal_capacity is a RATE (units/min — see cascade.MachineSpec),
            # and the target column is per SHIFT, so it must be scaled by the
            # shift length. Storing the raw rate here understated governed
            # capacity by 480x and silently skewed every utilisation metric view
            # built on work_center.
            "available_capacity_per_shift": (
                f"CAST(mr.nominal_capacity * {SHIFT_MINUTES} AS DECIMAL(18,2))"
            ),
            "created_timestamp": "current_timestamp()",
        },
    ),
    # ── production.run (one per scheduled shift) ─────────────────────────────
    # Seeded once at setup from the production_schedule reference. schedule_id
    # aligns with production.schedule via _sk(shift_id|sku) so run↔schedule joins.
    Mapping(
        schema="production",
        table="run",
        key="run",
        description="Production schedule -> production runs (planned vs actual)",
        from_clause="FROM {src}.production_schedule ps",
        columns={
            "run_id": _sk("concat(ps.shift_id, '|', ps.sku)"),
            "schedule_id": _sk("concat(ps.shift_id, '|', ps.sku)"),
            "run_number": "ps.shift_id",
            "run_type": "'production'",
            "run_status": "ps.status",
            "planned_quantity": "CAST(ps.planned_cases AS DECIMAL(18,2))",
            "actual_quantity": "CAST(ps.actual_cases AS DECIMAL(18,2))",
            "unit_of_measure": "'cases'",
            "created_timestamp": "current_timestamp()",
        },
    ),
    # ── quality.ncr (non-conformance reports) ───────────────────────────────
    # Streams from quality_checks (result='fail') via the SAME quality_checks
    # stream that feeds inspection_result. Empty at steady state (no false NCRs)
    # and self-populates during quality incidents — demonstrating governed
    # non-conformance capture on the live path. check_id is unique so MERGE is
    # safe without dedup.
    Mapping(
        schema="quality",
        table="ncr",
        key="ncr",
        description="Failed quality checks -> non-conformance reports",
        from_clause="FROM {src}.quality_checks_bronze q WHERE lower(q.result) = 'fail'",
        merge_key="ncr_id",
        stream_from="FROM {batch} q WHERE lower(q.result) = 'fail'",
        columns={
            "ncr_id": _sk("q.check_id"),
            "equipment_register_id": _sk("q.machine_id"),
            "production_line_id": _LINE_ID,
            "ncr_number": "q.check_id",
            "ncr_status": "'open'",
            "ncr_type": "'in_process'",
            "detection_source": "'in_line_inspection'",
            "defect_code": "q.parameter",
            "nonconformance_description": (
                "concat('Out-of-spec ', q.parameter, ' = ', CAST(q.value AS STRING))"
            ),
            "severity": "'major'",
            "detection_timestamp": "q.timestamp",
            "reported_timestamp": "q.timestamp",
            "created_timestamp": "current_timestamp()",
        },
    ),
]


# ── Pure SQL builders (unit-tested; no network) ─────────────────────────────
def introspect_sql(mvm_catalog: str, schema: str, table: str) -> str:
    """SQL to list a target table's columns via information_schema."""
    return (
        f"SELECT column_name FROM {mvm_catalog}.information_schema.columns "
        f"WHERE table_schema = '{schema}' AND table_name = '{table}'"
    )


def build_insert(m: Mapping, mvm_catalog: str, src_fqn: str,
                 existing_cols: set[str]) -> str | None:
    """Build an ``INSERT OVERWRITE`` for one mapping, restricted to columns that
    exist in the installed target table. Returns None if none of the mapping's
    columns are present (e.g. table missing / different model version)."""
    cols = [c for c in m.columns if c in existing_cols]
    if not cols:
        return None
    select_list = ",\n  ".join(
        f"{m.columns[c].format(src=src_fqn)} AS {c}" for c in cols
    )
    from_clause = m.from_clause.format(src=src_fqn)
    target = f"{mvm_catalog}.{m.schema}.{m.table}"
    stmt = (
        f"INSERT OVERWRITE {target} ({', '.join(cols)})\n"
        f"SELECT\n  {select_list}"
    )
    if from_clause:
        stmt += f"\n{from_clause}"
    return stmt


def build_merge(m: Mapping, mvm_catalog: str, batch_view: str,
                existing_cols: set[str]) -> str | None:
    """Build a ``MERGE INTO`` that upserts one micro-batch into an MVM fact table.

    Used by the streaming silver path (``streaming_quality_sync`` foreachBatch).
    ``batch_view`` is the name of a temp view over the current QC'd micro-batch;
    ``{batch}`` in ``stream_from`` is substituted with it. Only target columns
    present in ``existing_cols`` are written, and the ``merge_key`` MUST be one of
    them (else there's nothing to match on and we return None).

    MERGE (not INSERT OVERWRITE) because a micro-batch is a *slice* of the stream:
    we upsert the rows it carries and leave the rest of the table intact. The
    ``merge_key`` is a stable surrogate, so re-processing a batch on Spark retry
    is idempotent.
    """
    if not m.is_streaming:
        return None
    cols_def = m.stream_cols()
    cols = [c for c in cols_def if c in existing_cols]
    if m.merge_key not in cols:
        return None
    select_list = ",\n  ".join(
        f"{cols_def[c].format(batch=batch_view)} AS {c}" for c in cols
    )
    from_clause = m.stream_from.format(batch=batch_view)
    source = f"(\n  SELECT\n  {select_list}\n  {from_clause}\n) s"
    target = f"{mvm_catalog}.{m.schema}.{m.table}"
    # Insert-only columns are written once and never rewritten by a later match.
    # Two reasons this matters:
    #   * `created_timestamp` is a creation fact — re-merging a replayed batch
    #     must not restamp it to "now", which would also break the claim that a
    #     replay is a no-op (the rows would differ).
    #   * governed lifecycle state (e.g. `ncr_status`) is mapped from a literal
    #     seed value, so keeping it in UPDATE SET would silently re-open a
    #     non-conformance report that something downstream had closed.
    update_cols = [
        c for c in cols if c != m.merge_key and c not in INSERT_ONLY_COLUMNS
    ]
    set_clause = ",\n    ".join(f"t.{c} = s.{c}" for c in update_cols)
    insert_cols = ", ".join(cols)
    insert_vals = ", ".join(f"s.{c}" for c in cols)
    stmt = (
        f"MERGE INTO {target} t\n"
        f"USING {source}\n"
        f"ON t.{m.merge_key} = s.{m.merge_key}\n"
    )
    if update_cols:
        stmt += f"WHEN MATCHED THEN UPDATE SET\n    {set_clause}\n"
    stmt += (
        f"WHEN NOT MATCHED THEN INSERT ({insert_cols})\n"
        f"VALUES ({insert_vals})"
    )
    return stmt


# ── Runner (SDK Statement Execution API) ────────────────────────────────────
def _make_exec(host: str | None, warehouse_id: str):
    """Return (exec_one, query_all) bound to a warehouse via the SDK."""
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service.sql import StatementState

    if host and not os.environ.get("DATABRICKS_HOST"):
        os.environ["DATABRICKS_HOST"] = host
    w = WorkspaceClient()
    terminal = {StatementState.SUCCEEDED, StatementState.FAILED,
                StatementState.CANCELED, StatementState.CLOSED}

    def _run(sql: str):
        resp = w.statement_execution.execute_statement(
            warehouse_id=warehouse_id, statement=sql, wait_timeout="30s")
        while resp.status and resp.status.state not in terminal:
            time.sleep(1.0)
            resp = w.statement_execution.get_statement(resp.statement_id)
        state = resp.status.state if resp.status else None
        if state != StatementState.SUCCEEDED:
            err = resp.status.error if resp.status else None
            msg = getattr(err, "message", None) or err or "unknown error"
            raise RuntimeError(f"SQL failed ({state}): {msg}")
        return resp

    def exec_one(sql: str) -> None:
        _run(sql)

    def query_all(sql: str) -> list[list]:
        resp = _run(sql)
        if not resp.result or not resp.result.data_array:
            return []
        return [list(row) for row in resp.result.data_array]

    return exec_one, query_all


def refresh_silver_model(
    mvm_catalog: str,
    src_catalog: str,
    src_schema: str,
    warehouse_id: str,
    host: str | None = None,
    only: list[str] | None = None,
    exec_one=None,
    query_all=None,
) -> list[tuple[str, str]]:
    """Refresh the MVM silver tables from the simulator's bronze/reference data.

    ``exec_one`` / ``query_all`` can be injected for tests; otherwise a SDK-backed
    pair is created. Returns [(mapping_key, status)] where status is ``ok``,
    ``skipped (...)`` or ``FAILED: ...``.
    """
    src_fqn = f"{src_catalog}.{src_schema}"
    if exec_one is None or query_all is None:
        exec_one, query_all = _make_exec(host, warehouse_id)

    selected = [m for m in MAPPINGS if not only or m.key in only]
    results: list[tuple[str, str]] = []
    for m in selected:
        try:
            rows = query_all(introspect_sql(mvm_catalog, m.schema, m.table))
            existing = {str(r[0]) for r in rows}
            if not existing:
                results.append((m.key, f"skipped (target {m.schema}.{m.table} not found)"))
                print(f"SKIP {m.schema}.{m.table}: target not installed")
                continue
            stmt = build_insert(m, mvm_catalog, src_fqn, existing)
            if stmt is None:
                results.append((m.key, "skipped (no mappable columns)"))
                print(f"SKIP {m.schema}.{m.table}: no mappable columns")
                continue
            exec_one(stmt)
            results.append((m.key, "ok"))
            print(f"OK   {m.schema}.{m.table} <- {m.description}")
        except Exception as e:  # noqa: BLE001 — report per-mapping, keep going
            results.append((m.key, f"FAILED: {e}"))
            print(f"FAIL {m.schema}.{m.table}: {e}")
    return results


# ── CLI ─────────────────────────────────────────────────────────────────────
def _env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, default)
    if v is None or v == "":
        raise ValueError(f"Missing required env var: {name}")
    return v


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Populate the Manufacturing MVM silver model.")
    p.add_argument("--mvm-catalog", default=os.environ.get("MVM_CATALOG", "line_manager_silver_mvm"))
    p.add_argument("--src-catalog", default=os.environ.get("UC_CATALOG", "main"))
    p.add_argument("--src-schema", default=os.environ.get("UC_SCHEMA", "line_manager_streaming"))
    p.add_argument("--host", default=os.environ.get("DATABRICKS_HOST"))
    p.add_argument("--warehouse-id", default=os.environ.get("DATABRICKS_WAREHOUSE_ID"))
    p.add_argument("--only", nargs="*", default=None,
                   help="Subset of mapping keys to refresh (default: all).")
    args = p.parse_args(argv)

    host = args.host
    warehouse_id = args.warehouse_id or _env("DATABRICKS_WAREHOUSE_ID")

    print(f"Silver mapper: {args.src_catalog}.{args.src_schema} -> {args.mvm_catalog}")
    results = refresh_silver_model(
        mvm_catalog=args.mvm_catalog,
        src_catalog=args.src_catalog,
        src_schema=args.src_schema,
        warehouse_id=warehouse_id,
        host=host,
        only=args.only,
    )
    print("=" * 60)
    failed = [k for k, s in results if s.startswith("FAILED")]
    for key, status in results:
        print(f"  {key:22s} {status}")
    if failed:
        print(f"{len(failed)} mapping(s) FAILED")
        return 1
    print("Silver model refresh complete.")
    return 0


if __name__ == "__main__":
    # Only raise SystemExit on a NON-ZERO code. On serverless spark_python_task
    # (IPython-wrapped, environment_version 2) a raised ``SystemExit(0)`` is
    # surfaced as INTERNAL_ERROR / workload failure even though the mapping
    # succeeded — so a clean run must fall off the end without raising.
    _rc = main()
    if _rc:
        raise SystemExit(_rc)
