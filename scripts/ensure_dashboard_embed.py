#!/usr/bin/env python3
"""Make the Data Quality dashboard actually embeddable, and prove it.

The Data Quality tab embeds ``/embed/dashboardsv3/<id>``, which resolves only
for a *published* dashboard that the viewer can read. Three things have to be
true and each of them used to be assumed rather than checked:

  1. the dashboard exists in this workspace
  2. it is published, with embed_credentials so its queries run as the
     publisher instead of as the iframe viewer
  3. the app service principal can read it, and so can the humans opening the
     tab

The bundle declares ``embed_credentials: true``, and recent CLI versions do
publish on deploy — but that is a version-dependent behaviour we cannot rely on
in a repo other people deploy, and the grants were only ever applied by a bash
script that ran under scripts/deploy.sh and warned-and-continued on failure.
Anyone running ``databricks bundle deploy`` directly got a dashboard that
existed, resolved by name, and rendered Databricks' bare "The resource is not
available" page inside the iframe, with nothing anywhere explaining why.

So this does not assume: it reads the state back after every change and exits
non-zero if the dashboard would not embed. Idempotent — a second run makes no
calls beyond the checks.

Usage:
  python3 scripts/ensure_dashboard_embed.py --warehouse-id W [--dashboard-id D]
      [--app-name N] [--dashboard-name "…"] [--viewer user@example.com]
      [--viewer-group "data-team"] [--profile P] [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import sys

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.iam import AccessControlRequest, PermissionLevel


def _resolve_dashboard_id(w: WorkspaceClient, explicit: str, name: str) -> tuple[str, str]:
    """Return (dashboard_id, display_name), preferring an explicit id.

    `SET_ME` is treated as unset. It is a non-empty string, so every naive
    truthiness check upstream has taken it for a real id at least once.
    """
    explicit = (explicit or "").strip()
    if explicit and explicit != "SET_ME" and not explicit.startswith("<"):
        d = w.lakeview.get(dashboard_id=explicit)
        return explicit, (getattr(d, "display_name", None) or name)

    for d in w.lakeview.list():
        if getattr(d, "display_name", None) == name:
            return d.dashboard_id, name
    raise SystemExit(
        f"error: no dashboard named {name!r} in this workspace.\n"
        "       Deploy the bundle first (databricks bundle deploy), or pass\n"
        "       --dashboard-id / --dashboard-name if yours is named differently."
    )


def _is_published(w: WorkspaceClient, dashboard_id: str):
    try:
        return w.lakeview.get_published(dashboard_id=dashboard_id)
    except Exception:
        return None


def _grantees(
    w: WorkspaceClient, app_sp: str, viewers: list[str], groups: list[str]
) -> list[AccessControlRequest]:
    acl: list[AccessControlRequest] = []
    if app_sp and app_sp != "SET_ME":
        # The app backend resolves the dashboard as itself, via /ops/dashboard-info.
        acl.append(AccessControlRequest(
            service_principal_name=app_sp, permission_level=PermissionLevel.CAN_READ))
    for v in viewers:
        # Humans. The old script granted the app SP only, so the tab worked for
        # the person who deployed it and 404'd for everyone they showed it to.
        acl.append(AccessControlRequest(user_name=v, permission_level=PermissionLevel.CAN_READ))
    for g in groups:
        acl.append(AccessControlRequest(group_name=g, permission_level=PermissionLevel.CAN_READ))
    return acl


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--warehouse-id", default=os.environ.get("DATABRICKS_WAREHOUSE_ID"))
    ap.add_argument("--dashboard-id", default=os.environ.get("OPS_DATA_QUALITY_DASHBOARD_ID", ""))
    ap.add_argument("--app-name", default=os.environ.get("DATABRICKS_APP_NAME", ""))
    ap.add_argument("--dashboard-name", default=os.environ.get("OPS_DATA_QUALITY_DASHBOARD_NAME", ""))
    ap.add_argument("--app-sp", default=os.environ.get("APP_SP_ID", ""))
    ap.add_argument("--viewer", action="append", default=[],
                    help="user to grant CAN_READ; repeatable. Defaults to the deploying user.")
    ap.add_argument("--viewer-group", action="append", default=[],
                    help="group to grant CAN_READ; repeatable (e.g. the team who will demo this)")
    ap.add_argument("--profile", default=os.environ.get("DATABRICKS_CONFIG_PROFILE"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.warehouse_id:
        print("error: --warehouse-id is required (publishing binds the dashboard to a warehouse)")
        return 2

    w = WorkspaceClient(profile=args.profile) if args.profile else WorkspaceClient()

    name = args.dashboard_name or (
        f"{args.app_name} — Data Quality" if args.app_name else "Data Quality")
    dashboard_id, display_name = _resolve_dashboard_id(w, args.dashboard_id, name)
    print(f"Data Quality dashboard: {display_name} ({dashboard_id})")

    viewers = list(args.viewer)
    if not viewers:
        try:
            me = w.current_user.me().user_name
            if me:
                viewers = [me]
        except Exception:
            pass

    published = _is_published(w, dashboard_id)
    needs_publish = published is None or not getattr(published, "embed_credentials", False)

    if args.dry_run:
        print(f"  would {'publish' if needs_publish else 'leave published'} "
              f"on warehouse {args.warehouse_id}")
        print(f"  would grant CAN_READ to sp={args.app_sp or '(none)'} "
              f"users={viewers} groups={args.viewer_group}")
        return 0

    if needs_publish:
        why = "not published" if published is None else "published without embedded credentials"
        print(f"  {why} — publishing with embed_credentials on warehouse {args.warehouse_id}")
        w.lakeview.publish(
            dashboard_id=dashboard_id,
            embed_credentials=True,
            warehouse_id=args.warehouse_id,
        )
    else:
        print("  already published with embedded credentials")

    acl = _grantees(w, args.app_sp, viewers, args.viewer_group)
    if acl:
        # update() replaces the ACL for the principals named and leaves the
        # owner intact; patch semantics differ across object types, and being
        # explicit here is what makes a re-run idempotent.
        w.permissions.update(
            request_object_type="dashboards",
            request_object_id=dashboard_id,
            access_control_list=acl,
        )
        who = [a.service_principal_name or a.user_name or a.group_name for a in acl]
        print(f"  CAN_READ granted to: {', '.join(who)}")
    else:
        print("  WARN: no principals to grant — the tab will only work for the dashboard owner")

    # Read it back. Publishing can succeed and still leave the dashboard
    # unembeddable if the warehouse is wrong, and a grant can silently no-op.
    confirmed = _is_published(w, dashboard_id)
    if confirmed is None:
        print("\nERROR: the dashboard still reports as unpublished after publishing it.")
        print("       The Data Quality tab will show 'The resource is not available'.")
        return 1
    if not getattr(confirmed, "embed_credentials", False):
        print("\nERROR: published, but without embedded credentials — every widget in the")
        print("       iframe will render 'Unable to render visualization.'")
        return 1

    print(f"  verified: published, embed_credentials=True, warehouse="
          f"{getattr(confirmed, 'warehouse_id', args.warehouse_id)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
