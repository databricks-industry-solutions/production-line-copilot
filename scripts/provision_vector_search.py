#!/usr/bin/env python3
"""Build the Vector Search endpoint and index that ``retrieve_similar_cases`` needs.

Until this existed the semantic path was unreachable. The tool asked for an
index named ``event_log_vs_index`` on an endpoint named ``vs_endpoint``; neither
had ever been created, nothing in the repository created them, and no config set
``VS_ENDPOINT``. Every call constructed a client, failed, and degraded to keyword
matching — while the cost panel billed for the endpoint anyway.

Three things had to be solved together, not just "create an index":

1. **The source needs the outcome columns.** The tool reads
   ``resolution_minutes``, ``outcome_oee_impact`` and ``outcome_description``.
   Those are written to ``event_log_bronze`` but the quality gate drops them on
   the way to ``gold_history_event_log``. An index over gold — the obvious
   choice — fails on the column list. So this builds a purpose-made retrieval
   table joining the gated event to its outcome fields.

2. **A Delta Sync index embeds one column.** Relevance comes from the
   description, the root cause and the corrective action together; an operator
   describing "cap misfeed" often matches text recorded as a root cause. The
   retrieval table therefore carries a ``case_text`` column with those fields
   concatenated, and that is what gets embedded.

3. **Sync needs Change Data Feed**, and the table is written by MERGE rather
   than CREATE OR REPLACE so the feed stays coherent — a replace rewrites every
   file and the index resyncs the entire corpus each time.

Idempotent by construction: the endpoint, the table and the index are each
created only when absent, and the MERGE upserts. Safe on every deploy.

Usage:
  python3 scripts/provision_vector_search.py --catalog C --schema S \\
      --warehouse-id W [--profile P] [--dry-run] [--wait]
"""
from __future__ import annotations

import argparse
import os
import sys
import time

from databricks.sdk import WorkspaceClient
from databricks.sdk.service import vectorsearch as vs
from databricks.sdk.service.sql import StatementState

DEFAULT_ENDPOINT = "prodline-copilot-vs"
DEFAULT_INDEX = "event_case_index"
DEFAULT_EMBEDDING = "databricks-gte-large-en"
SOURCE_TABLE = "event_case_library"
ENDPOINT_SETTLE_SECONDS = 180

# Held to the columns the tool actually reads, plus the embedded text. Syncing
# more would cost storage per vector for fields nothing displays.
SYNCED_COLUMNS = [
    "event_id", "shift_id", "machine_id", "event_type", "severity",
    "description", "root_cause", "corrective_action", "duration_minutes",
    "resolution_minutes", "outcome_oee_impact", "outcome_description",
    "case_text",
]


class Warehouse:
    def __init__(self, client: WorkspaceClient, warehouse_id: str):
        self._w, self._id = client, warehouse_id

    def run(self, sql: str) -> list[list]:
        r = self._w.statement_execution.execute_statement(
            warehouse_id=self._id, statement=sql, wait_timeout="50s"
        )
        while r.status and r.status.state in (StatementState.PENDING, StatementState.RUNNING):
            time.sleep(2)
            r = self._w.statement_execution.get_statement(r.statement_id)
        if r.status and r.status.state != StatementState.SUCCEEDED:
            raise RuntimeError(f"{sql[:90]}... -> {r.status.error}")
        return (r.result.data_array or []) if r.result else []

    def table_exists(self, fqn: str) -> bool:
        catalog, schema, name = fqn.split(".")
        rows = self.run(
            "SELECT 1 FROM system.information_schema.tables "
            f"WHERE table_catalog = '{catalog}' AND table_schema = '{schema}' "
            f"AND table_name = '{name}' LIMIT 1"
        )
        return bool(rows)


def build_retrieval_table(wh: Warehouse, fqn: str, dry_run: bool) -> int:
    """Create (if absent) and refresh the curated case library. Returns row count."""
    table = f"{fqn}.{SOURCE_TABLE}"
    ddl = f"""
        CREATE TABLE IF NOT EXISTS {table} (
          event_id STRING NOT NULL,
          shift_id STRING,
          machine_id STRING,
          event_type STRING,
          severity STRING,
          description STRING,
          root_cause STRING,
          corrective_action STRING,
          duration_minutes DOUBLE,
          resolution_minutes DOUBLE,
          outcome_oee_impact DOUBLE,
          outcome_description STRING,
          case_text STRING,
          updated_at TIMESTAMP,
          CONSTRAINT event_case_library_pk PRIMARY KEY (event_id)
        )
        TBLPROPERTIES (delta.enableChangeDataFeed = true)
    """
    # concat_ws skips nulls, so a case missing its corrective action still
    # embeds the text it does have rather than collapsing to NULL.
    merge = f"""
        MERGE INTO {table} AS t
        USING (
          SELECT
            e.event_id, e.shift_id, e.machine_id, e.event_type, e.severity,
            e.description, e.root_cause, e.corrective_action,
            CAST(e.duration_minutes AS DOUBLE) AS duration_minutes,
            CAST(b.resolution_minutes AS DOUBLE) AS resolution_minutes,
            CAST(b.outcome_oee_impact AS DOUBLE) AS outcome_oee_impact,
            b.outcome_description,
            concat_ws('. ',
              e.description, e.root_cause, e.corrective_action,
              concat('event type ', e.event_type),
              concat('machine ', e.machine_id)
            ) AS case_text,
            current_timestamp() AS updated_at
          FROM {fqn}.gold_history_event_log e
          LEFT JOIN {fqn}.event_log_bronze b ON b.event_id = e.event_id
          WHERE e.event_id IS NOT NULL AND e.description IS NOT NULL
        ) AS s
        ON t.event_id = s.event_id
        WHEN MATCHED AND t.case_text <> s.case_text THEN UPDATE SET *
        WHEN NOT MATCHED THEN INSERT *
    """
    if dry_run:
        print(f"  [dry-run] would create and refresh {table}")
        return -1
    wh.run(ddl)

    # gold_history_event_log is created by the streaming quality-sync job, which
    # starts alongside this deploy step. On a fresh workspace the deploy usually
    # wins that race, and a MERGE against the missing table used to abort the
    # whole provisioning run — costing the endpoint and index too, which are the
    # slow part. The case library is enrichment; the index is the deliverable.
    if not wh.table_exists(f"{fqn}.gold_history_event_log"):
        print(
            "  gold_history_event_log does not exist yet — the streaming "
            "quality-sync job creates it on its first batch.\n"
            "  Building the index over the cases already present; re-run this "
            "script (or deploy) once the stream has caught up to fold in the "
            "real event history."
        )
    else:
        wh.run(merge)
    return int(wh.run(f"SELECT count(*) FROM {table}")[0][0])


def ensure_endpoint(w: WorkspaceClient, name: str, dry_run: bool, wait: bool) -> bool:
    """True when the endpoint is ONLINE and usable."""
    try:
        ep = w.vector_search_endpoints.get_endpoint(name)
        state = ep.endpoint_status.state if ep.endpoint_status else None
        print(f"  endpoint {name}: exists, state={state}")
        return str(state).endswith("ONLINE")
    except Exception:
        pass

    if dry_run:
        print(f"  [dry-run] would create endpoint {name}")
        return False

    print(f"  creating endpoint {name} (this takes several minutes)")
    w.vector_search_endpoints.create_endpoint(name=name, endpoint_type=vs.EndpointType.STANDARD)
    if not wait:
        print("  not waiting — re-run with --wait once it is online to create the index")
        return False

    deadline = time.time() + 40 * 60
    while time.time() < deadline:
        ep = w.vector_search_endpoints.get_endpoint(name)
        state = str(ep.endpoint_status.state if ep.endpoint_status else "")
        if state.endswith("ONLINE"):
            # A freshly created endpoint reports ONLINE before it can accept an
            # index. Create one into that window and it sticks permanently at
            # "pending endpoint provisioning" with a sync pipeline that is never
            # created — and the index cannot then be deleted, only dropped with
            # the endpoint. The settle is cheap insurance against a one-way door.
            print(f"  endpoint {name} reports ONLINE — settling for {ENDPOINT_SETTLE_SECONDS}s")
            time.sleep(ENDPOINT_SETTLE_SECONDS)
            return True
        if "OFFLINE" in state or "FAILED" in state:
            print(f"  endpoint {name} entered {state}")
            return False
        time.sleep(30)
    print("  timed out waiting for the endpoint")
    return False


def ensure_index(w: WorkspaceClient, endpoint: str, index_fqn: str, source_fqn: str,
                 embedding: str, dry_run: bool) -> bool:
    try:
        idx = w.vector_search_indexes.get_index(index_fqn)
        print(f"  index {index_fqn}: exists, ready={idx.status.ready if idx.status else '?'}")
        return True
    except Exception:
        pass

    if dry_run:
        print(f"  [dry-run] would create index {index_fqn} over {source_fqn}")
        return False

    print(f"  creating index {index_fqn} over {source_fqn}")
    w.vector_search_indexes.create_index(
        name=index_fqn,
        endpoint_name=endpoint,
        primary_key="event_id",
        index_type=vs.VectorIndexType.DELTA_SYNC,
        delta_sync_index_spec=vs.DeltaSyncVectorIndexSpecRequest(
            source_table=source_fqn,
            # TRIGGERED, not CONTINUOUS: the corpus is resolved historical cases,
            # which do not need second-level freshness, and CONTINUOUS keeps a
            # pipeline running (and billing) around the clock to achieve it.
            pipeline_type=vs.PipelineType.TRIGGERED,
            columns_to_sync=SYNCED_COLUMNS,
            embedding_source_columns=[
                vs.EmbeddingSourceColumn(
                    name="case_text", embedding_model_endpoint_name=embedding
                )
            ],
        ),
    )
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--catalog", default=os.environ.get("UC_CATALOG"))
    ap.add_argument("--schema", default=os.environ.get("UC_SCHEMA"))
    ap.add_argument("--warehouse-id", default=os.environ.get("DATABRICKS_WAREHOUSE_ID"))
    ap.add_argument("--endpoint", default=os.environ.get("VS_ENDPOINT") or DEFAULT_ENDPOINT)
    ap.add_argument("--index-name", default=DEFAULT_INDEX)
    ap.add_argument("--embedding-model", default=DEFAULT_EMBEDDING)
    ap.add_argument("--profile", default=os.environ.get("DATABRICKS_CONFIG_PROFILE"))
    ap.add_argument("--wait", action="store_true", help="block until the endpoint is ONLINE")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    missing = [n for n in ("catalog", "schema", "warehouse_id") if not getattr(args, n)]
    if missing:
        print(f"error: missing required argument(s): {', '.join(missing)}")
        return 2

    w = WorkspaceClient(profile=args.profile) if args.profile else WorkspaceClient()
    fqn = f"{args.catalog}.{args.schema}"
    index_fqn = f"{fqn}.{args.index_name}"
    source_fqn = f"{fqn}.{SOURCE_TABLE}"

    print(f"Vector Search provisioning for {fqn}")
    rows = build_retrieval_table(Warehouse(w, args.warehouse_id), fqn, args.dry_run)
    if rows == 0:
        print("  the case library is empty — an index over it would return nothing.")
        print("  populate gold_history_event_log first, then re-run.")
        return 1
    if rows > 0:
        print(f"  {source_fqn}: {rows} cases")

    online = ensure_endpoint(w, args.endpoint, args.dry_run, args.wait)
    if not online:
        print("\nEndpoint is not online yet; the index has not been created.")
        print(f"Re-run with --wait (or once it reports ONLINE) to finish:\n"
              f"  VS_ENDPOINT={args.endpoint} python3 {sys.argv[0]} "
              f"--catalog {args.catalog} --schema {args.schema} "
              f"--warehouse-id {args.warehouse_id} --wait")
        return 0 if args.dry_run else 1

    ensure_index(w, args.endpoint, index_fqn, source_fqn, args.embedding_model, args.dry_run)
    print(f"\nSet VS_ENDPOINT={args.endpoint} in the app config for the tool to use it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
