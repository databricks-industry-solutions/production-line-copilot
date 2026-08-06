#!/usr/bin/env python3
"""Optional prerequisite provisioner (privileged users only).

``deploy.sh`` auto-creates everything *inside* your catalog/schema (tables,
reference data, the App + its SP, jobs, dashboard, Genie space). It deliberately
does NOT create the *platform* resources it depends on, because in most
enterprises those are governed by a platform team, cost money, or need admin
rights the deployer doesn't have.

If you DO have the rights and want a turn-key setup, this script creates the
creatable ones and writes the resulting IDs back into
``config/project-settings.local.yaml`` so ``deploy.sh`` can consume them:

  * SQL warehouse                     (serverless, small, auto-stop)
  * Producer service principal        + OAuth secret + Databricks secret scope
  * Lakebase (Autoscale) project      + branch + read-write endpoint   [--with-lakebase]
  * Zerobus endpoint URL              (derived only — see note below)

Zerobus is NOT created here. It's an *ingest entitlement* (account / preview-level)
that you can't script "enable Zerobus" for. The endpoint URL, however, is derivable
from workspace-id + region, so this script derives and records that for you.

It does NOT create the UC catalog (usually governed) — set ``uc_catalog`` yourself.

SAFETY
------
* Defaults to a **dry run** — prints what it would do and changes nothing.
  Pass ``--apply`` to actually create resources.
* Every step is **idempotent**: existing resources (matched by name) are reused,
  never duplicated.
* ``--with-lakebase`` provisions **billable** compute — read the printed warning.
* Never invents values: everything is read from / written to your
  project-settings profile for the chosen target.

Usage:
  python3 scripts/provision_prereqs.py dev                      # dry run
  python3 scripts/provision_prereqs.py dev --apply              # create (no Lakebase)
  python3 scripts/provision_prereqs.py dev --apply --with-lakebase
  python3 scripts/provision_prereqs.py prod --apply --profile myprofile --region us-east-1
"""
from __future__ import annotations

import argparse
import os
import re
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

ENV_TO_BUNDLE_TARGET = {"dev": "line_manager_dev", "prod": "line_manager_prod"}


# --------------------------------------------------------------------------- #
# project-settings helpers
# --------------------------------------------------------------------------- #
def _settings_path() -> Path:
    local = ROOT / "config" / "project-settings.local.yaml"
    return local if local.is_file() else ROOT / "config" / "project-settings.yaml"


def _load_profile(settings_path: Path, bundle_target: str) -> tuple[str, dict]:
    data = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
    for name, prof in (data.get("profiles") or {}).items():
        if (prof or {}).get("bundle_target") == bundle_target:
            return name, (prof or {})
    raise SystemExit(f"No profile with bundle_target={bundle_target!r} in {settings_path}")


def _ensure_local_settings() -> Path:
    """Never write real infra IDs into the committed template — mirror deploy.sh
    and create/prefer the gitignored .local.yaml."""
    local = ROOT / "config" / "project-settings.local.yaml"
    if not local.is_file():
        template = ROOT / "config" / "project-settings.yaml"
        local.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"  · created {local.relative_to(ROOT)} from template")
    return local


def _write_profile_fields(local_path: Path, bundle_target: str, fields: dict[str, str]) -> None:
    """Comment-preserving, profile-scoped ``key: "value"`` setter.

    Line-based (like deploy.sh's inline patchers) so we don't reflow the YAML or
    drop comments. Only rewrites keys that already exist inside the matching
    profile block.
    """
    lines = local_path.read_text(encoding="utf-8").split("\n")
    doc = yaml.safe_load("\n".join(lines)) or {}
    pname = None
    for name, prof in (doc.get("profiles") or {}).items():
        if (prof or {}).get("bundle_target") == bundle_target:
            pname = name
            break
    if not pname:
        raise SystemExit(f"No profile with bundle_target={bundle_target!r} in {local_path}")

    remaining = dict(fields)
    out: list[str] = []
    in_block = False
    block_indent = None
    for line in lines:
        header = re.match(r"^(\s+)([A-Za-z0-9_.-]+):\s*$", line)
        if header and header.group(2) == pname:
            in_block = True
            block_indent = len(header.group(1))
            out.append(line)
            continue
        if in_block:
            cur = re.match(r"^(\s*)\S", line)
            cur_indent = len(cur.group(1)) if cur else 999
            if line.strip() and cur_indent <= block_indent:
                in_block = False
            else:
                key_match = re.match(r"^(\s+)([A-Za-z0-9_.-]+):\s*.*$", line)
                if key_match and key_match.group(2) in remaining:
                    key = key_match.group(2)
                    out.append(f'{key_match.group(1)}{key}: "{remaining.pop(key)}"')
                    continue
        out.append(line)

    local_path.write_text("\n".join(out), encoding="utf-8")
    if remaining:
        print(f"  ! WARN: keys not found in profile {pname!r} (not written): "
              f"{', '.join(sorted(remaining))}")


def _parse_secret_ref(ref: str) -> tuple[str, str]:
    m = re.fullmatch(r"\{\{secrets/([^/]+)/([^}]+)\}\}", (ref or "").strip())
    if not m:
        return "line-manager", "zerobus-client-secret"
    return m.group(1), m.group(2)


def _is_set(value: object) -> bool:
    text = str(value or "").strip()
    return bool(text) and text != "SET_ME" and not (text.startswith("<") and text.endswith(">"))


# --------------------------------------------------------------------------- #
# provisioning steps (each returns dict of project-settings fields to write)
# --------------------------------------------------------------------------- #
def provision_warehouse(w, prof: dict, apply: bool) -> dict[str, str]:
    from databricks.sdk.service.sql import CreateWarehouseRequestWarehouseType

    if _is_set(prof.get("sql_warehouse_id")):
        print(f"  · warehouse: already set ({prof['sql_warehouse_id']}) — skipping")
        return {}

    name = "line-manager-serverless"
    for wh in w.warehouses.list():
        if wh.name == name:
            print(f"  · warehouse: reusing existing {name!r} ({wh.id})")
            return {"sql_warehouse_id": wh.id}

    if not apply:
        print(f"  · [dry-run] would create serverless SQL warehouse {name!r} (Small, auto-stop 10m)")
        return {}

    print(f"  · creating serverless SQL warehouse {name!r} …")
    resp = w.warehouses.create(
        name=name,
        cluster_size="Small",
        min_num_clusters=1,
        max_num_clusters=1,
        auto_stop_mins=10,
        enable_serverless_compute=True,
        warehouse_type=CreateWarehouseRequestWarehouseType.PRO,
    ).result()
    print(f"    → warehouse id {resp.id}")
    return {"sql_warehouse_id": resp.id}


def provision_service_principal(w, prof: dict, apply: bool) -> dict[str, str]:
    scope, key = _parse_secret_ref(prof.get("producer_client_secret_ref", ""))
    out: dict[str, str] = {}

    # 1) service principal (match by display name for idempotency)
    display = "line-manager-producer"
    client_id = prof.get("producer_client_id")
    sp_numeric_id = None
    if _is_set(client_id):
        print(f"  · producer SP: already set ({client_id}) — reusing")
        for sp in w.service_principals.list():
            if sp.application_id == client_id:
                sp_numeric_id = sp.id
                break
    else:
        for sp in w.service_principals.list():
            if sp.display_name == display:
                client_id, sp_numeric_id = sp.application_id, sp.id
                print(f"  · producer SP: reusing existing {display!r} ({client_id})")
                break
        if not sp_numeric_id:
            if not apply:
                print(f"  · [dry-run] would create service principal {display!r}")
            else:
                print(f"  · creating service principal {display!r} …")
                sp = w.service_principals.create(display_name=display)
                client_id, sp_numeric_id = sp.application_id, sp.id
                print(f"    → application_id {client_id}")
    if client_id and _is_set(client_id):
        out["producer_client_id"] = client_id

    # 2) secret scope
    existing_scopes = {s.name for s in w.secrets.list_scopes()}
    if scope in existing_scopes:
        print(f"  · secret scope {scope!r}: exists")
    elif not apply:
        print(f"  · [dry-run] would create secret scope {scope!r}")
    else:
        print(f"  · creating secret scope {scope!r} …")
        w.secrets.create_scope(scope=scope)

    # 3) OAuth secret for the SP → store under scope/key
    if not sp_numeric_id:
        print("  ! producer SP id unknown (dry-run or not created) — skipping OAuth secret")
        return out
    if not apply:
        print(f"  · [dry-run] would mint an OAuth secret for the SP and store it at "
              f"secrets/{scope}/{key}")
        return out

    print("  · minting OAuth secret for the producer SP …")
    secret = w.service_principal_secrets_proxy.create(service_principal_id=str(sp_numeric_id))
    w.secrets.put_secret(scope=scope, key=key, string_value=secret.secret)
    print(f"    → stored client secret at secrets/{scope}/{key} (value not printed)")
    return out


DEFAULT_LAKEBASE_PROJECT = "prodline-copilot"


def _lakebase_path_parts(prof: dict) -> tuple[str, str, str, bool]:
    """Project / branch / endpoint to provision, and whether the settings chose them.

    ``lakebase_endpoint`` is where this project already keeps the Lakebase path —
    ``deploy.sh`` parses the project out of it with awk to register SP roles — so
    it is also where an operator states which project they want, well before
    ``lakebase_pg_host`` can be known (the host only exists once the project
    does). This used to hardcode ``prodline-copilot`` and ignore that field
    entirely, which meant two people running the documented
    ``provision_prereqs.py <target> --apply --with-lakebase`` in one workspace
    raced for a single fixed name, and anyone who had written their own project
    into the settings watched it be disregarded.

    Note the placeholder check has to run on the parsed segment, not the whole
    path: the shipped template reads ``projects/<your-lakebase-project>/...``,
    which passes ``_is_set`` as a string because it does not itself start with
    ``<``.
    """
    raw = str(prof.get("lakebase_endpoint") or "").strip().strip("/")
    parts = raw.split("/")
    parsed: dict[str, str] = {}
    if len(parts) >= 6 and parts[0] == "projects":
        parsed = {"project": parts[1], "branch": parts[3], "endpoint": parts[5]}

    chosen = _is_set(parsed.get("project"))
    return (
        parsed["project"] if chosen else DEFAULT_LAKEBASE_PROJECT,
        parsed.get("branch") if chosen and _is_set(parsed.get("branch")) else "production",
        parsed.get("endpoint") if chosen and _is_set(parsed.get("endpoint")) else "primary",
        chosen,
    )


def provision_lakebase(w, prof: dict, apply: bool) -> dict[str, str]:
    from databricks.sdk.service import postgres as pg

    if _is_set(prof.get("lakebase_pg_host")) and _is_set(prof.get("lakebase_endpoint")):
        print(f"  · lakebase: already set ({prof['lakebase_pg_host']}) — skipping")
        return {}

    print("  ! Lakebase Autoscale provisions BILLABLE compute. It stays online and "
          "bills hourly until you delete the project.")

    project_id, branch_id, endpoint_id, from_settings = _lakebase_path_parts(prof)
    endpoint_path = f"projects/{project_id}/branches/{branch_id}/endpoints/{endpoint_id}"

    if from_settings:
        print(f"  · lakebase project {project_id!r} (from lakebase_endpoint)")
    else:
        print(f"  ! lakebase_endpoint names no project, so defaulting to {project_id!r}. "
              "Anyone else running this in the same workspace lands on that same "
              "name — set lakebase_endpoint to "
              "projects/<your-project>/branches/production/endpoints/primary first "
              "if you want your own.")

    if not apply:
        print(f"  · [dry-run] would create Lakebase project {project_id!r} "
              f"(auto-provisions branch {branch_id!r} + read-write endpoint {endpoint_id!r})")
        print(f"  · [dry-run] endpoint path would be {endpoint_path}")
        return {}

    # Creating a project auto-provisions the default `production` branch AND a
    # read-write `primary` endpoint — so we only create the project, then read
    # the auto-created endpoint's host. (Verified against the live API.)
    project_name = f"projects/{project_id}"
    try:
        w.postgres.get_project(name=project_name)
        print(f"  · lakebase project {project_id!r}: exists")
    except Exception:
        print(f"  · creating lakebase project {project_id!r} …")
        w.postgres.create_project(
            project=pg.Project(spec=pg.ProjectSpec(display_name="ProdLine CoPilot", pg_version=16)),
            project_id=project_id,
        ).wait()

    # Poll the auto-created endpoint until its host is published (~seconds).
    ep_name = f"{project_name}/branches/{branch_id}/endpoints/{endpoint_id}"
    host = None
    for _ in range(24):  # up to ~2 min
        try:
            endpoint = w.postgres.get_endpoint(name=ep_name)
            if endpoint.status and endpoint.status.hosts and endpoint.status.hosts.host:
                host = endpoint.status.hosts.host
                break
        except Exception:
            pass
        time.sleep(5)
    out = {"lakebase_endpoint": endpoint_path}
    if host:
        out["lakebase_pg_host"] = host
        print(f"    → pg host {host}")
    else:
        print("  ! endpoint host not yet available — re-run to capture lakebase_pg_host, "
              "or read it from Compute → Database instances.")
    return out


def derive_zerobus_url(w, prof: dict, region: str | None, created_lakebase: dict) -> dict[str, str]:
    if _is_set(prof.get("zerobus_server_endpoint")):
        print(f"  · zerobus: already set ({prof['zerobus_server_endpoint']}) — skipping")
        return {}

    # region resolution: explicit flag > parse from a lakebase host we know
    resolved = region
    if not resolved:
        for host in (created_lakebase.get("lakebase_pg_host"), prof.get("lakebase_pg_host")):
            m = re.search(r"\.database\.([a-z]{2}-[a-z]+-\d)\.cloud\.databricks\.com", str(host or ""))
            if m:
                resolved = m.group(1)
                break
    if not resolved:
        print("  ! zerobus: could not derive region — pass --region <aws-region> "
              "(Zerobus itself is an account-level entitlement, not created here).")
        return {}

    try:
        workspace_id = w.get_workspace_id()
    except Exception as exc:  # noqa: BLE001
        print(f"  ! zerobus: could not read workspace id ({exc}); skipping.")
        return {}

    url = f"https://{workspace_id}.zerobus.{resolved}.cloud.databricks.com"
    print(f"  · zerobus endpoint (derived): {url}")
    print("    Note: Zerobus is an ingest entitlement (account / preview-level) — it")
    print("    can't be created/enabled by script; only the URL is derived. Confirm")
    print("    Zerobus Ingest is enabled for this workspace/account with your admin.")
    return {"zerobus_server_endpoint": url}


# --------------------------------------------------------------------------- #
def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("env", choices=["dev", "prod"])
    p.add_argument("--apply", action="store_true",
                   help="Actually create resources (default: dry run).")
    p.add_argument("--with-lakebase", action="store_true",
                   help="Also provision the (billable) Lakebase Autoscale project.")
    p.add_argument("--region", default=None,
                   help="AWS region for the Zerobus URL (else derived from a Lakebase host).")
    p.add_argument("--profile", default=None,
                   help="Databricks CLI profile (default: $DATABRICKS_PROFILE or DEFAULT).")
    args = p.parse_args()

    bundle_target = ENV_TO_BUNDLE_TARGET[args.env]
    settings_path = _settings_path()
    profile_name, prof = _load_profile(settings_path, bundle_target)

    profile = args.profile or os.environ.get("DATABRICKS_PROFILE", "DEFAULT")

    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"=== provision_prereqs: env={args.env} target={bundle_target} "
          f"profile={profile} [{mode}] ===")
    print(f"    settings: {settings_path.relative_to(ROOT)}  (profile block: {profile_name})")
    if not args.apply:
        print("    Nothing will be created. Re-run with --apply to provision.")
    print()

    from databricks.sdk import WorkspaceClient
    w = WorkspaceClient(profile=profile)

    collected: dict[str, str] = {}
    print("[1/4] SQL warehouse")
    collected.update(provision_warehouse(w, prof, args.apply))
    print("[2/4] Producer service principal + secret")
    collected.update(provision_service_principal(w, prof, args.apply))
    print("[3/4] Lakebase (Autoscale)")
    lb = {}
    if args.with_lakebase:
        lb = provision_lakebase(w, prof, args.apply)
        collected.update(lb)
    else:
        print("  · skipped (pass --with-lakebase to provision; it is billable)")
    print("[4/4] Zerobus endpoint URL")
    collected.update(derive_zerobus_url(w, prof, args.region, lb))

    print()
    if not collected:
        print("Nothing to write back (all set already or dry run).")
        return
    if not args.apply:
        print("Dry run — would write these into project-settings.local.yaml:")
        for k, v in collected.items():
            shown = v if "secret" not in k else "***"
            print(f"  {k} = {shown}")
        return

    local = _ensure_local_settings()
    _write_profile_fields(local, bundle_target, collected)
    print(f"Wrote {len(collected)} field(s) into {local.relative_to(ROOT)} "
          f"(profile {profile_name!r}).")
    print("Next: python3 scripts/preflight.py "
          f"{args.env} --profile {profile}  →  ./scripts/deploy.sh {args.env}")


if __name__ == "__main__":
    main()
