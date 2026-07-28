#!/usr/bin/env python3
"""Verify Lakebase JWT + Postgres connection + ai_serving tables (CLI smoke test).

Run from repo root (same env as the app), e.g.:

  export DATABRICKS_HOST=https://<your-workspace>.cloud.databricks.com
  export PGHOST=ep-....database....cloud.databricks.com
  export PGDATABASE=databricks_postgres
  export ENDPOINT_NAME=projects/.../branches/.../endpoints/...
  export LAKEBASE_PGUSER=<oauth-role-uuid>
  uv run python scripts/verify_lakebase.py

Exit 0 if connection works and expected tables exist; 1 on failure; 2 if connected but tables missing.
"""
from __future__ import annotations

import json
import os
import sys


def main() -> int:
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo not in sys.path:
        sys.path.insert(0, repo)

    try:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(repo, ".env"))
    except ImportError:
        pass

    import importlib.util

    pg_path = os.path.join(repo, "agent_server", "memory", "pg_pool.py")
    spec = importlib.util.spec_from_file_location("lakebase_pg_pool_verify", pg_path)
    if spec is None or spec.loader is None:
        print(json.dumps({"ok": False, "error": "could not load pg_pool.py"}, indent=2))
        return 1
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    get_connection = mod.get_connection
    _is_pg_configured = mod._is_pg_configured
    _lakebase_disabled = mod._lakebase_disabled

    out: dict[str, object] = {}
    if not _is_pg_configured() or _lakebase_disabled():
        out["ok"] = False
        out["error"] = "PGHOST unset or LAKEBASE_DISABLED=1"
        print(json.dumps(out, indent=2))
        return 1

    conn = get_connection()
    if not conn:
        out["ok"] = False
        out["error"] = "get_connection() returned None"
        print(json.dumps(out, indent=2))
        return 1

    expected = (
        "machine_state_latest",
        "line_kpi_latest",
        "event_log_recent",
    )
    tables: dict[str, object] = {}
    with conn.cursor() as cur:
        cur.execute("SELECT current_user AS role, current_database() AS db")
        out["session"] = dict(cur.fetchone() or {})
        for t in expected:
            fq = f"ai_serving.{t}"
            try:
                cur.execute(f"SELECT COUNT(*) AS n FROM {fq}")
                row = cur.fetchone()
                n = int(row["n"]) if row and row.get("n") is not None else 0
                tables[fq] = {"exists": True, "row_count": n}
            except Exception as e:
                tables[fq] = {"exists": False, "error": str(e)[:200]}

    out["tables"] = tables
    all_exist = all(
        isinstance(tables[f"ai_serving.{t}"], dict) and tables[f"ai_serving.{t}"].get("exists") for t in expected
    )
    out["ok"] = all_exist
    print(json.dumps(out, indent=2, default=str))
    return 0 if all_exist else 2


if __name__ == "__main__":
    raise SystemExit(main())
