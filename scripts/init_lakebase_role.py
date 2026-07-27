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
    stmts = [
        f'GRANT USAGE ON SCHEMA ai_serving TO "{sp_client_id}"',
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA ai_serving TO "{sp_client_id}"',
        f'ALTER DEFAULT PRIVILEGES IN SCHEMA ai_serving GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{sp_client_id}"',
        f'GRANT USAGE, CREATE ON SCHEMA public TO "{sp_client_id}"',
    ]
    for s in stmts:
        cur.execute(s)
        print(f"  ok: {s[:70]}{'…' if len(s) > 70 else ''}")
    cur.close()
    conn.close()


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
