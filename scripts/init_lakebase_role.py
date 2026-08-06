"""Register an app service principal as a Lakebase Postgres role and grant
the access the App needs (``ai_serving.*`` + ``public`` schema).

Lakebase Autoscale uses an internal "managed identity" registry on top of
``pg_roles``. A raw ``CREATE ROLE`` via SQL produces a Postgres role with
``auth_method=NO_LOGIN``, so OAuth tokens minted via
``w.postgres.generate_database_credential`` still get rejected with
``password authentication failed``. The supported path is
``w.postgres.create_role`` with ``identity_type=SERVICE_PRINCIPAL`` and
``auth_method=LAKEBASE_OAUTH_V1`` — that hooks the role into the project's
identity verification path so the OAuth token is accepted.

This script is idempotent:
  * If the role already exists with the right auth_method, it just re-applies
    the SQL grants on ``ai_serving`` + ``public``.
  * If the role exists but with the wrong auth_method (e.g. created via raw
    SQL), it deletes and re-creates via the API.

Usage:
  python scripts/init_lakebase_role.py \
      --profile <your-cli-profile> \
      --project <your-lakebase-project-id> \
      --branch production \
      --pg-host <your-lakebase-pg-host>.database.<region>.cloud.databricks.com \
      --pg-database databricks_postgres \
      --pg-admin-user <your-databricks-username@example.com> \
      --sp-client-id <your-app-service-principal-uuid>

Typically invoked by ``scripts/deploy.sh``, which resolves these values from
``config/project-settings.yaml`` and ``databricks current-user me``.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _run(cmd: list[str]) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd)}\nstderr: {r.stderr}")
    return r.stdout


def _list_lakebase_roles(profile: str, parent: str) -> list[dict]:
    out = _run(
        [
            "databricks",
            "api",
            "get",
            f"/api/2.0/postgres/{parent}/roles",
            "--profile",
            profile,
        ]
    )
    return json.loads(out).get("roles", [])


def _delete_lakebase_role(profile: str, role_name: str) -> None:
    _run(["databricks", "api", "delete", f"/api/2.0/postgres/{role_name}", "--profile", profile])


def _create_lakebase_sp_role(profile: str, parent: str, sp_client_id: str) -> None:
    """Create the Postgres role via the Lakebase create_role API.

    Uses the SDK because the public ``databricks api post`` shape rejects flat
    payloads (the Lakebase create endpoint requires a ``Role`` proto with a
    nested ``RoleSpec`` envelope that the CLI can't easily synthesize).
    """
    sdk_script = f"""
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.postgres import (
    Role, RoleRoleSpec, RoleAuthMethod, RoleIdentityType,
)

w = WorkspaceClient(profile={profile!r})
role = Role(
    parent={parent!r},
    spec=RoleRoleSpec(
        postgres_role={sp_client_id!r},
        identity_type=RoleIdentityType.SERVICE_PRINCIPAL,
        auth_method=RoleAuthMethod.LAKEBASE_OAUTH_V1,
    ),
)
op = w.postgres.create_role(parent={parent!r}, role=role)
print('create_role op:', op)
"""
    venv_py = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        ".venv",
        "bin",
        "python",
    )
    py = venv_py if os.path.exists(venv_py) else sys.executable
    r = subprocess.run([py, "-c", sdk_script], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"create_role failed: {r.stderr}")
    print(r.stdout.strip())


def _ensure_lakebase_role(profile: str, project: str, branch: str, sp_client_id: str) -> None:
    parent = f"projects/{project}/branches/{branch}"
    roles = _list_lakebase_roles(profile, parent)
    existing = next((r for r in roles if r.get("status", {}).get("postgres_role") == sp_client_id), None)
    if existing is None:
        print(f"  no Lakebase role for {sp_client_id} — creating via API")
        _create_lakebase_sp_role(profile, parent, sp_client_id)
        return

    auth = existing.get("status", {}).get("auth_method")
    if auth == "LAKEBASE_OAUTH_V1":
        print(f"  Lakebase role for {sp_client_id} already registered (auth_method={auth})")
        return

    print(f"  existing role has auth_method={auth} (expected LAKEBASE_OAUTH_V1) — recreating")
    _delete_lakebase_role(profile, existing["name"])
    time.sleep(5)  # allow propagation
    _create_lakebase_sp_role(profile, parent, sp_client_id)


def _grant_pg_access(
    pg_host: str,
    pg_database: str,
    pg_admin_user: str,
    profile: str,
    sp_client_id: str,
) -> None:
    """Grant SCHEMA + TABLE privileges so the App can read ai_serving and let
    AppKit's persistent-storage migrations run in the public schema."""
    import psycopg2

    tok_out = _run(["databricks", "auth", "token", "--profile", profile, "--output", "json"])
    tok = json.loads(tok_out)["access_token"]

    conn = psycopg2.connect(
        host=pg_host,
        port=5432,
        database=pg_database,
        user=pg_admin_user,
        password=tok,
        sslmode="require",
    )
    conn.autocommit = True
    cur = conn.cursor()

    # Fail fast instead of queueing behind a lock. This step runs ALTER TABLE
    # against ai_serving.scenario_commands, which needs ACCESS EXCLUSIVE, while
    # the app and both producers are reading the same table every second. A
    # single reader left idle-in-transaction is enough to park the ALTER
    # indefinitely — and because an exclusive request queues ahead of later
    # readers, every producer poll then blocks behind *it*. Observed on a dev
    # deploy: the ALTER waited 14 minutes and took both environments' incident
    # handling down with it while it waited.
    #
    # With a timeout the DDL raises, the deploy reports it, and nothing else is
    # held hostage. Retrying is cheap; the statements are all IF NOT EXISTS.
    cur.execute("SET lock_timeout = '5s'")

    # Create the schemas before granting on them. This step runs on the very
    # first deploy, before the app has ever connected — and the app is what
    # creates ai_serving and ai_chatbot. So `GRANT USAGE ON SCHEMA ai_serving`
    # used to raise "schema does not exist", abort the rest of the loop, and
    # leave the deploy printing a warning it then continued past. The app came
    # up later, created the schemas itself, and never got its grants.
    #
    # ai_chatbot matters as much as ai_serving: it holds conversations and
    # messages. It was missing from this list entirely, which is why chat
    # persistence needed scripts/grant_app_schemas_lakebase.sql run by hand.
    stmts = [
        "CREATE SCHEMA IF NOT EXISTS ai_serving",
        "CREATE SCHEMA IF NOT EXISTS ai_chatbot",
    ]
    for schema in ("ai_serving", "ai_chatbot"):
        stmts += [
            f'GRANT USAGE, CREATE ON SCHEMA {schema} TO "{sp_client_id}"',
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {schema} '
            f'TO "{sp_client_id}"',
            f'ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} '
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{sp_client_id}"',
        ]
    stmts.append(f'GRANT USAGE, CREATE ON SCHEMA public TO "{sp_client_id}"')

    failures = 0
    for s in stmts:
        # One bad grant should not cost the app every later one. Reported at
        # the end so a partial failure is loud rather than buried mid-scroll.
        try:
            cur.execute(s)
            print(f"  ok: {s[:70]}{'…' if len(s) > 70 else ''}")
        except Exception as exc:  # noqa: BLE001 - reported below
            failures += 1
            print(f"  FAILED: {s[:70]} -> {str(exc)[:120]}")
    if failures:
        raise RuntimeError(
            f"{failures} of {len(stmts)} Lakebase grants failed — the app will hit "
            "'permission denied for schema' at runtime. Re-run this script once the "
            "admin role can create/grant on ai_serving and ai_chatbot."
        )

    _create_app_tables(cur)

    cur.close()
    conn.close()


def _create_app_tables(cur) -> None:
    """Create the ai_serving / ai_chatbot tables, as the deployer.

    These used to be created lazily by the app on its first Postgres
    connection, which made them depend on someone opening the UI — and the
    streaming quality-sync job dual-writes into ai_serving.* from its very
    first micro-batch. On a fresh workspace the stream reliably lost that race
    and died on `relation "ai_serving.event_log_recent" does not exist`,
    which in turn meant no gold_history tables and a line UI showing zero
    machines.

    The app keeps its own lazy init as a fallback for anyone running against a
    Lakebase this script never touched. pg_pool._INIT_SQL stays the single
    definition of the schema; this just runs it earlier, as an identity that
    can also CREATE SCHEMA.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from agent_server.memory.pg_pool import _INIT_SQL, iter_statements

    print("Creating ai_serving + ai_chatbot tables")
    created = failed = 0
    for stmt in iter_statements(_INIT_SQL):
        try:
            cur.execute(stmt)
            created += 1
        except Exception as exc:  # noqa: BLE001 - reported in aggregate
            failed += 1
            print(f"  FAILED: {stmt.splitlines()[0][:70]} -> {str(exc).strip()[:140]}")
    print(f"  {created} statement(s) applied, {failed} failed")
    if failed:
        raise RuntimeError(
            f"{failed} Lakebase DDL statement(s) failed — the streaming job "
            "dual-writes into ai_serving and will fail on its first batch."
        )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--profile", default=os.environ.get("DATABRICKS_PROFILE", "DEFAULT"))
    p.add_argument("--project", required=True, help="Lakebase project id (set in project-settings.yaml)")
    p.add_argument("--branch", default="production")
    p.add_argument("--pg-host", required=True)
    p.add_argument("--pg-database", default="databricks_postgres")
    p.add_argument("--pg-admin-user", required=True, help="user email of the deploying admin (issues OAuth tokens for SQL)")
    p.add_argument("--sp-client-id", required=True, help="App service principal application id")
    ns = p.parse_args()

    print(f"=== Registering {ns.sp_client_id} as Lakebase role in {ns.project}/{ns.branch} ===")
    _ensure_lakebase_role(ns.profile, ns.project, ns.branch, ns.sp_client_id)

    print("\n=== Granting Postgres access (ai_serving + public) ===")
    _grant_pg_access(
        pg_host=ns.pg_host,
        pg_database=ns.pg_database,
        pg_admin_user=ns.pg_admin_user,
        profile=ns.profile,
        sp_client_id=ns.sp_client_id,
    )
    print("\n=== Done ===")


if __name__ == "__main__":
    main()
