"""Daily TTL job — prune old rows from ``ai_serving.*_history`` tables.

The streaming quality sync job appends ~3 M rows/day across the four history
tables. Without retention these grow unbounded. We prune anything older than
``--retention-days`` (default 7) and run ``VACUUM ANALYZE`` so Postgres
reclaims tuples and refreshes statistics.

Runs as a Databricks Job on serverless compute via cron at 03:00 UTC.

Usage::

    python scripts/prune_lakebase_history.py \\
        --pg-host <host> --pg-user <sp-uuid> --pg-database databricks_postgres \\
        --lakebase-endpoint projects/<p>/branches/<b>/endpoints/primary \\
        --retention-days 7
"""
from __future__ import annotations

import argparse
import logging
import os
import pathlib
import sys


def _ensure_repo_on_path() -> None:
    """Make ``agent_server.memory`` importable when running on a Databricks Job."""
    try:
        here = pathlib.Path(__file__).resolve()
        candidates = [here.parent] + list(here.parents)
    except NameError:
        candidates = [pathlib.Path.cwd().resolve()] + list(pathlib.Path.cwd().resolve().parents)
    for p in candidates:
        if (p / "agent_server").is_dir() and (p / "src").is_dir():
            if str(p) not in sys.path:
                sys.path.insert(0, str(p))
            return


_ensure_repo_on_path()

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    stream=sys.stdout,
    force=True,
)


HISTORY_TABLES = {
    # Each entry maps table → ts column. event_log_history uses
    # timestamp_start (it doesn't have a ``ts`` column).
    "ai_serving.machine_state_history": "ts",
    "ai_serving.line_kpi_history": "ts",
    "ai_serving.inventory_status_history": "ts",
    "ai_serving.event_log_history": "timestamp_start",
}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pg-host", required=True)
    p.add_argument("--pg-user", required=True)
    p.add_argument("--pg-database", default="databricks_postgres")
    p.add_argument("--pg-port", default="5432")
    p.add_argument("--pg-sslmode", default="require")
    p.add_argument("--lakebase-endpoint", required=True)
    p.add_argument("--retention-days", type=int, default=7)
    args = p.parse_args()

    # Mirror to env so the OAuth helper below picks up the endpoint.
    for k, v in {
        "PGHOST": args.pg_host,
        "PGUSER": args.pg_user,
        "PGDATABASE": args.pg_database,
        "PGPORT": args.pg_port,
        "PGSSLMODE": args.pg_sslmode,
        "LAKEBASE_ENDPOINT": args.lakebase_endpoint,
    }.items():
        os.environ[k] = v

    import psycopg2

    from agent_server.memory.pg_pool import _get_pg_password, _get_pg_user

    user = _get_pg_user()
    password = _get_pg_password()
    logger.info(
        "Pruning Lakebase history: host=%s db=%s user=%s retention_days=%s",
        args.pg_host, args.pg_database, user, args.retention_days,
    )

    conn = psycopg2.connect(
        host=args.pg_host, port=int(args.pg_port), user=user, password=password,
        dbname=args.pg_database, sslmode=args.pg_sslmode, connect_timeout=10,
    )
    conn.autocommit = True

    total_deleted = 0
    try:
        for table, ts_col in HISTORY_TABLES.items():
            with conn.cursor() as cur:
                # Delete in batches of 100 K to avoid one giant transaction
                # holding row locks. The DELETE uses the (shift_id, ts) /
                # (shift_id, timestamp_start) index for fast range scan.
                deleted_for_table = 0
                while True:
                    cur.execute(
                        f"""
                        WITH old AS (
                            SELECT ctid FROM {table}
                            WHERE {ts_col} < (now() - INTERVAL '{args.retention_days} days')
                            LIMIT 100000
                        )
                        DELETE FROM {table} WHERE ctid IN (SELECT ctid FROM old)
                        """
                    )
                    n = cur.rowcount
                    deleted_for_table += n
                    if n == 0:
                        break
                logger.info("[%s] deleted=%d", table, deleted_for_table)
                total_deleted += deleted_for_table
                # VACUUM (no FULL — non-blocking) reclaims dead tuples + ANALYZE
                # refreshes planner stats so the cursor query stays fast.
                try:
                    cur.execute(f"VACUUM ANALYZE {table}")
                    logger.info("[%s] VACUUM ANALYZE done", table)
                except Exception as e:  # noqa: BLE001
                    logger.warning("[%s] VACUUM failed (non-fatal): %s", table, str(e)[:200])
    finally:
        conn.close()

    logger.info("Pruning complete: total_deleted=%d", total_deleted)


if __name__ == "__main__":
    main()
