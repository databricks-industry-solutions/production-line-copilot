#!/usr/bin/env python3
"""Make the Data Quality dashboard openable, and prove it.

The app no longer embeds this dashboard — the Data Integrity tab draws those
figures itself from ``/ops/quality-metrics`` — but the dashboard is still
deployed and still linked from that tab as the place to explore rather than
glance. So two things have to be true, and each of them used to be assumed
rather than checked:

  1. the dashboard exists in this workspace and is published, so the link opens
     a dashboard rather than an editor
  2. the app service principal can read it (that is how the tab resolves the
     link at all, via ``/ops/dashboard-info``), and so can the humans clicking
     it

Published WITHOUT embedded credentials, deliberately. Embedding required them —
they make the dashboard's queries run as whoever published it rather than as
whoever is looking — and that is a privilege elevation nothing needs now that
the iframe is gone. Anyone opening the link sees it through their own Unity
Catalog permissions. This script actively republishes a dashboard it finds
carrying embedded credentials, so environments set up for the old embed lose
them on the next deploy.

Nothing here is load-bearing for the tab: if it all fails, the figures still
render and only the "Explore in Databricks" link disappears.

Usage:
  python3 scripts/ensure_dashboard_access.py --warehouse-id W [--dashboard-id D]
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
        # Metadata only: the app resolves the dashboard's id as itself, via
        # /ops/dashboard-info, to build the link. It reads none of the data.
        acl.append(AccessControlRequest(
            service_principal_name=app_sp, permission_level=PermissionLevel.CAN_READ))
    for v in viewers:
        # Humans, who now open the dashboard directly and see it through their
        # own permissions. An earlier version granted the app SP only, so the
        # link worked for whoever deployed and 404'd for everyone they showed.
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
    # Republishing an embed-credentialed dashboard is the point, not a side
    # effect: that is how an environment built for the old iframe stops running
    # these queries as its publisher.
    carries_embed_credentials = bool(getattr(published, "embed_credentials", False))
    needs_publish = published is None or carries_embed_credentials

    if args.dry_run:
        print(f"  would {'publish' if needs_publish else 'leave published'} "
              f"on warehouse {args.warehouse_id}")
        print(f"  would grant CAN_READ to sp={args.app_sp or '(none)'} "
              f"users={viewers} groups={args.viewer_group}")
        return 0

    if needs_publish:
        why = (
            "published with embedded credentials, which nothing needs any more"
            if carries_embed_credentials
            else "not published"
        )
        print(f"  {why} — publishing on warehouse {args.warehouse_id}")
        w.lakeview.publish(
            dashboard_id=dashboard_id,
            embed_credentials=False,
            warehouse_id=args.warehouse_id,
        )
    else:
        print("  already published, on the viewer's own credentials")

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
        print("  WARN: no principals to grant — the link will only open for the owner")

    # Read it back: publishing can succeed and still land on the wrong
    # warehouse, and a grant can silently no-op.
    confirmed = _is_published(w, dashboard_id)
    if confirmed is None:
        print("\nERROR: the dashboard still reports as unpublished after publishing it.")
        print("       The tab's 'Explore in Databricks' link will open an empty draft.")
        return 1
    if getattr(confirmed, "embed_credentials", False):
        print("\nERROR: still published with embedded credentials, so its queries run as")
        print("       the publisher rather than as whoever opens it.")
        return 1

    print(f"  verified: published on the viewer's credentials, warehouse="
          f"{getattr(confirmed, 'warehouse_id', args.warehouse_id)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
