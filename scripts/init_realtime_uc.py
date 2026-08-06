"""Initialize Unity Catalog objects for the streaming line-manager demo.

Creates (idempotent):
  - Schema   (default: main.line_manager_streaming)
  - Bronze landing tables (ZeroBus ingest targets): machine_state_bronze,
    event_log_bronze, quality_checks_bronze, telemetry_bronze, line_kpi_bronze
  - Live tables: machine_state, event_log, quality_checks, telemetry,
                 line_kpi, scenario_commands
  - HITL tables: decision_log, draft_artifacts
  - Reference tables (Tier 2 OT enrichment): machine_registry, alert_thresholds,
    maintenance_windows, inventory_status

All live tables are CDF-enabled so Synced Tables (Continuous) can replicate
them to Lakebase.

Usage:
  UC_CATALOG=main UC_SCHEMA=line_manager_streaming \
  DATABRICKS_HOST=... DATABRICKS_WAREHOUSE_ID=... \
  python scripts/init_realtime_uc.py
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


_CDF = "TBLPROPERTIES (delta.enableChangeDataFeed = true)"

_TABLES: dict[str, str] = {
    # ── Bronze (ZeroBus ingest → streaming quality-gate reads) ────────────
    "machine_state_bronze": f"""
        CREATE TABLE IF NOT EXISTS {{fqn}}.machine_state_bronze (
          shift_id           STRING,
          machine_id         STRING,
          ts                 TIMESTAMP,
          status             STRING,
          throughput_per_min  DOUBLE,
          speed_pct          DOUBLE,
          alarm_count        BIGINT
        ) USING DELTA {_CDF}
    """,
    "event_log_bronze": f"""
        CREATE TABLE IF NOT EXISTS {{fqn}}.event_log_bronze (
          event_id            STRING,
          shift_id            STRING,
          machine_id          STRING,
          event_type          STRING,
          severity            STRING,
          timestamp_start     TIMESTAMP,
          timestamp_end       TIMESTAMP,
          duration_minutes    DOUBLE,
          root_cause          STRING,
          description         STRING,
          corrective_action   STRING,
          resolution_minutes  DOUBLE,
          outcome_oee_impact  DOUBLE,
          outcome_description STRING
        ) USING DELTA {_CDF}
    """,
    "quality_checks_bronze": f"""
        CREATE TABLE IF NOT EXISTS {{fqn}}.quality_checks_bronze (
          check_id   STRING,
          shift_id   STRING,
          machine_id STRING,
          timestamp  TIMESTAMP,
          parameter  STRING,
          value      DOUBLE,
          spec_min   DOUBLE,
          spec_max   DOUBLE,
          result     STRING
        ) USING DELTA {_CDF}
    """,
    "telemetry_bronze": f"""
        CREATE TABLE IF NOT EXISTS {{fqn}}.telemetry_bronze (
          shift_id    STRING,
          timestamp   TIMESTAMP,
          machine_id  STRING,
          sensor_name STRING,
          value       DOUBLE
        ) USING DELTA {_CDF}
    """,
    "line_kpi_bronze": f"""
        CREATE TABLE IF NOT EXISTS {{fqn}}.line_kpi_bronze (
          shift_id                 STRING,
          ts                       TIMESTAMP,
          machines_running         INT,
          machines_stopped         INT,
          machines_degraded        INT,
          machines_starved         INT,
          total_throughput         DOUBLE,
          avg_speed_pct            DOUBLE,
          availability_to_now      DOUBLE,
          performance_to_now     DOUBLE,
          quality_to_now         DOUBLE,
          oee_to_now              DOUBLE,
          cumulative_cases        DOUBLE
        ) USING DELTA {_CDF}
    """,
    # Inventory snapshots — ticked per-shift by producer.py via the shared
    # src/streaming_sim/inventory_engine.py module. Field set must match
    # snapshot_inventory(...) + producer's payload (`ts`).
    "inventory_status_bronze": f"""
        CREATE TABLE IF NOT EXISTS {{fqn}}.inventory_status_bronze (
          shift_id                  STRING,
          material_id               STRING,
          on_hand_qty               DOUBLE,
          safety_stock              INT,
          reorder_point             INT,
          status                    STRING,
          consumption_rate_per_hour DOUBLE,
          feeds_machine_id          STRING,
          ts                        TIMESTAMP
        ) USING DELTA {_CDF}
    """,
    # ── Deliberately absent: machine_state, line_kpi, event_log, telemetry,
    #    quality_checks ────────────────────────────────────────────────────
    #
    # These five used to be created here as empty shells, left over from when
    # the producer wrote them directly. The pipeline now writes `*_bronze` and
    # the quality gate publishes `gold_history_*`; nothing has written the
    # unsuffixed names in a long time, and nothing reads their Lakebase mirrors
    # either (see realtime_store.py).
    #
    # They are not merely useless, they are actively harmful. Their names are
    # the ones an LLM guesses first, and a query against an empty table returns
    # zero rows instead of an error — so a specialist writes a confident answer
    # over nothing and no one sees a failure. Deleting the DDL converts that
    # silent wrong answer into TABLE_OR_VIEW_NOT_FOUND, which the model reports
    # and a human can act on. tests/agent_server/test_no_dead_tables.py keeps
    # them from creeping back into queries, prompts or the Genie config.
    #
    # ── Command channel (UI → producer, bidirectional lifecycle) ─────────
    "scenario_commands": f"""
        CREATE TABLE IF NOT EXISTS {{fqn}}.scenario_commands (
          command_id        STRING,
          created_at        TIMESTAMP,
          created_by        STRING,
          shift_id          STRING,
          scenario_id       STRING,
          target_machine_id STRING,
          severity          STRING,
          params            STRING,
          status            STRING,
          resolved_at       TIMESTAMP,
          resolved_by       STRING,
          resolution_notes  STRING
        ) USING DELTA {_CDF}
    """,
    # NOTE: the decision_log / draft_artifacts tables were removed. The decision
    # log is client-side (client/src/lib/decisionLogStorage.ts, localStorage), so
    # the UC tables and their writer module had no reader and no writer.
    # ── Reference (Tier 2 OT enrichment) ────────────────────────────────
    "machine_registry": f"""
        CREATE TABLE IF NOT EXISTS {{fqn}}.machine_registry (
          machine_id       STRING NOT NULL,
          machine_name     STRING,
          machine_type     STRING,
          line_position    INT,
          nominal_capacity DOUBLE,
          zone             STRING,
          manufacturer     STRING
        ) USING DELTA {_CDF}
    """,
    "alert_thresholds": f"""
        CREATE TABLE IF NOT EXISTS {{fqn}}.alert_thresholds (
          machine_id   STRING NOT NULL,
          sensor_name  STRING NOT NULL,
          warn_low     DOUBLE,
          warn_high    DOUBLE,
          critical_low DOUBLE,
          critical_high DOUBLE
        ) USING DELTA {_CDF}
    """,
    "maintenance_windows": f"""
        CREATE TABLE IF NOT EXISTS {{fqn}}.maintenance_windows (
          machine_id     STRING NOT NULL,
          pm_type        STRING,
          interval_hours INT,
          last_pm        TIMESTAMP,
          next_pm        TIMESTAMP
        ) USING DELTA {_CDF}
    """,
    # ``inventory_status`` (current snapshot) is exposed as a UC VIEW over
    # ``inventory_status_bronze`` — latest row per (shift_id, material_id). The
    # streaming quality-gate job (pipelines/streaming_quality_sync.py) writes the
    # live inventory state to Lakebase (ai_serving.inventory_status) for the UI,
    # NOT to UC — so a VIEW here does not collide with any writer. It gives Genie
    # and any warehouse consumer a clean current-inventory table to query.
    "inventory_status": """
        CREATE OR REPLACE VIEW {fqn}.inventory_status AS
        SELECT shift_id, material_id, on_hand_qty, safety_stock, reorder_point,
               status, consumption_rate_per_hour, feeds_machine_id, ts
        FROM (
          SELECT *,
                 ROW_NUMBER() OVER (
                   PARTITION BY shift_id, material_id ORDER BY ts DESC
                 ) AS _rn
          FROM {fqn}.inventory_status_bronze
        )
        WHERE _rn = 1
    """,
    # ── DQX audit sink (Data Quality eXtended warnings) ───────────────────
    # Stable, normalized audit table written by ``pipelines.dqx_augment`` when
    # DQX is enabled (``DQX_ENABLED=1``). Created empty here (idempotent) so the
    # Data Quality dashboard's DQX widgets always resolve, even before DQX is
    # turned on. One row per (flagged bronze row × failed check).
    "dqx_warnings": f"""
        CREATE TABLE IF NOT EXISTS {{fqn}}.dqx_warnings (
          bronze_table    STRING,
          flagged_at      TIMESTAMP,
          check_name      STRING,
          check_function  STRING,
          message         STRING
        ) USING DELTA {_CDF}
    """,
}


_BRONZE_TABLES = (
    "machine_state_bronze",
    "event_log_bronze",
    "quality_checks_bronze",
    "telemetry_bronze",
    "line_kpi_bronze",
    "inventory_status_bronze",
)


def _apply_cli() -> None:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--catalog", default=None)
    p.add_argument("--schema", default=None)
    p.add_argument("--host", default=None)
    p.add_argument("--warehouse-id", default=None)
    p.add_argument(
        "--producer-client-id",
        default=None,
        help="Zerobus producer service principal — receives MODIFY+SELECT on bronze tables.",
    )
    p.add_argument(
        "--app-client-id",
        default=None,
        help="App service principal — needs USE CATALOG/SCHEMA + SELECT on UC tables (scenario_commands, scenario_index, scenario_moments).",
    )
    ns, _unknown = p.parse_known_args()
    if ns.catalog:
        os.environ["UC_CATALOG"] = ns.catalog
    if ns.schema:
        os.environ["UC_SCHEMA"] = ns.schema
    if ns.host:
        os.environ["DATABRICKS_HOST"] = ns.host
    if ns.warehouse_id:
        os.environ["DATABRICKS_WAREHOUSE_ID"] = ns.warehouse_id
    if ns.producer_client_id:
        os.environ["PRODUCER_CLIENT_ID"] = ns.producer_client_id
    if ns.app_client_id:
        os.environ["APP_CLIENT_ID"] = ns.app_client_id


def main() -> None:
    _apply_cli()
    catalog = os.environ.get("UC_CATALOG", "main")
    schema = os.environ.get("UC_SCHEMA", "line_manager_streaming")
    fqn = f"{catalog}.{schema}"

    host = _env("DATABRICKS_HOST")
    warehouse_id = _env("DATABRICKS_WAREHOUSE_ID")

    # Serverless-safe SQL exec (SDK Statement Execution API — no pyarrow).
    # Serverless ``spark_python_task`` runs this file via ``exec()`` with no
    # ``__file__`` global, so fall back to ``sys.argv[0]`` to locate scripts/.
    _script_dir = os.path.dirname(os.path.abspath(globals().get("__file__") or sys.argv[0]))
    if _script_dir not in sys.path:
        sys.path.insert(0, _script_dir)
    from _uc_sql import StmtCursor

    cur = StmtCursor(warehouse_id, host=host)

    try:
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {fqn}")
        print(f"OK: schema {fqn}")

        # Spark Structured Streaming checkpoints live in this volume
        # (see ``streaming_quality_sync.JOB_CHECKPOINT_ROOT`` and the
        # ``--checkpoint-volume`` CLI flag). Without it the streams fail
        # to start with ``[UC_VOLUME_NOT_FOUND] Volume … _checkpoints
        # does not exist`` — silent on a fresh schema until the first
        # streaming run lights it up. Idempotent.
        cur.execute(f"CREATE VOLUME IF NOT EXISTS {fqn}._checkpoints")
        print(f"OK: volume {fqn}._checkpoints")

        for name, ddl in _TABLES.items():
            cur.execute(ddl.format(fqn=fqn))
            print(f"OK: {fqn}.{name}")

        # Producer SP needs MODIFY (write) + SELECT (read-back) on every bronze
        # table for Zerobus stream init to succeed. Without this, stream creation
        # fails with: "Specified UC token is in invalid format: 401
        # invalid_authorization_details / User is not authorized…".
        producer_sp = os.environ.get("PRODUCER_CLIENT_ID", "").strip()
        if producer_sp:
            cur.execute(f"GRANT USE CATALOG ON CATALOG {catalog} TO `{producer_sp}`")
            print(f"OK: grant USE CATALOG on {catalog} -> {producer_sp[:8]}…")
            cur.execute(f"GRANT USE SCHEMA ON SCHEMA {fqn} TO `{producer_sp}`")
            print(f"OK: grant USE SCHEMA on {fqn} -> {producer_sp[:8]}…")
            for name in _BRONZE_TABLES:
                cur.execute(
                    f"GRANT MODIFY, SELECT ON TABLE {fqn}.{name} TO `{producer_sp}`"
                )
                print(f"OK: grant MODIFY,SELECT on {fqn}.{name} -> {producer_sp[:8]}…")
            # Producer SP also needs SELECT/MODIFY on scenario_commands so it
            # can poll pending incidents from the App and flip them to active /
            # resolving / resolved. Without this, /sim/incident from the UI
            # writes a row into UC but the producer's CommandStore returns 0
            # rows on every poll → incidents are silently invisible.
            cur.execute(
                f"GRANT SELECT, MODIFY ON TABLE {fqn}.scenario_commands TO `{producer_sp}`"
            )
            print(f"OK: grant SELECT,MODIFY on {fqn}.scenario_commands -> {producer_sp[:8]}…")
        else:
            print(
                "WARN: --producer-client-id not provided; skipping bronze grants. "
                "Set var.producer_client_id in the bundle so Zerobus stream init "
                "succeeds for all bronze tables."
            )

        # App SP needs USE CATALOG/SCHEMA + SELECT on scenario_commands,
        # scenario_index, scenario_moments + MODIFY on scenario_commands so
        # /sim/incident, /sim/resolve, and the chat scenarios endpoint can
        # write to and read from UC. Without this the app falls back silently
        # and incidents triggered from the UI never reach the producer.
        app_sp = os.environ.get("APP_CLIENT_ID", "").strip()
        if app_sp:
            cur.execute(f"GRANT USE CATALOG ON CATALOG {catalog} TO `{app_sp}`")
            cur.execute(f"GRANT USE SCHEMA ON SCHEMA {fqn} TO `{app_sp}`")
            # Schema-wide SELECT so the app SP can read every reference/gold
            # table + the inventory_status view — needed by context_builder AND
            # by Genie (which runs its SQL as the app SP). Covers current and
            # future objects, so new tables don't silently return empty.
            cur.execute(f"GRANT SELECT ON SCHEMA {fqn} TO `{app_sp}`")
            cur.execute(f"GRANT SELECT, MODIFY ON TABLE {fqn}.scenario_commands TO `{app_sp}`")
            print(f"OK: grant USE CATALOG + SELECT ON SCHEMA + scenario_commands MODIFY -> app SP {app_sp[:8]}…")

            # NOTE: the governed MVM schemas (asset/production/quality/inventory)
            # are granted by install_industry_data_model.py, not here — this task
            # runs before the model exists, so the grants would have nothing to
            # apply to.
        else:
            print(
                "WARN: --app-client-id not provided; skipping app SP grants. "
                "Set var.app_client_id in the bundle so /sim/incident & /sim/resolve "
                "can write to UC scenario_commands."
            )
    finally:
        cur.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        raise
