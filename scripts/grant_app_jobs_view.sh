#!/usr/bin/env bash
# Grant the Databricks App's Service Principal CAN_VIEW / CAN_MANAGE_RUN
# permissions on the resources its UI needs to inspect.
#
# Why: the app's `_resource_running_states()` calls the Jobs / Pipelines APIs to
# decide whether to bill a component on the Cost & Obs tab. Without these grants
# the SDK call returns "permission denied", the function silently swallows the
# error, and the panel falsely reports "idle" / "$0.0000" for every resource —
# making the cost panel useless.
#
# Usage:
#   PROFILE=<your-profile> APP_NAME=production-line-copilot-prod \
#   DASHBOARD_ID=<dashboard_uuid> \
#   USER_EMAIL=your.name@your-company.com \
#   ./scripts/grant_app_jobs_view.sh
#
# Run after: databricks bundle deploy --target <profile>
set -euo pipefail

PROFILE="${PROFILE:?PROFILE is required (databricks CLI profile name)}"
APP_NAME="${APP_NAME:?APP_NAME is required}"
USER_EMAIL="${USER_EMAIL:?USER_EMAIL is required (the bundle owner email)}"
# Job IDs are NO LONGER required for this script. The CAN_MANAGE_RUN grants
# on jobs are declarative in the bundle (see scripts/sync_project_settings.py
# ``_app_sp_permissions_block``). The Data Quality dashboard is now also
# declarative — ``embed_credentials: true`` + a per-target App SP CAN_READ
# grant (``_data_quality_dashboard_permissions_block``) mean a plain
# ``databricks bundle deploy`` publishes + grants it. This script remains for
# belt-and-suspenders re-publishing and to grant CAN_READ on the workspace
# folders that contain the dashboard (folder perms aren't expressible in DAB
# yet). Existing callers that still pass PRODUCER_JOB_ID / SYNCER_JOB_ID are
# tolerated but ignored.
DASHBOARD_ID="${DASHBOARD_ID:-}"

APP_JSON=$(databricks apps get "$APP_NAME" --profile "$PROFILE" --output json)
# Avoid `python3 -c '...print(...)...'` inside $(...) — macOS bash 3.2's
# parser cannot reliably track parens across nested quotes, which breaks
# even before the script runs. grep/cut is brittle but bash-3.2 safe.
SP=$(printf '%s\n' "$APP_JSON" | grep -o '"service_principal_client_id"[^,}]*' | head -1 | sed -E 's/.*"([^"]+)"$/\1/')

if [ -z "$SP" ]; then
  echo "ERROR: could not resolve service_principal_client_id for app '$APP_NAME'" >&2
  exit 1
fi

echo "App SP for '$APP_NAME': $SP"
echo
echo "(Job CAN_MANAGE_RUN and dashboard CAN_READ/embed grants are declarative in the bundle;"
echo " this script re-publishes the dashboard + grants the workspace-folder chain below.)"
echo

# ---------------------------------------------------------------------------
# Data Quality dashboard (optional but recommended)
# ---------------------------------------------------------------------------
# When OPS_DATA_QUALITY_DASHBOARD_ID is set, we also grant CAN_READ on the
# Lakeview dashboard AND on the workspace folders that contain it. Without
# the folder grants the SDK lookup throws "Node with resource name None does
# not exist" because the dashboard's parent_path is under your user folder
# and the app SP can't traverse there.
if [ -n "${DASHBOARD_ID:-}" ]; then
  # macOS ships bash 3.2 which mis-parses heredocs nested inside $(...) — use
  # printf for the JSON payloads so this script runs unmodified on the host
  # where deploy.sh invokes it.
  echo "== Data Quality dashboard $DASHBOARD_ID: CAN_READ to $SP =="
  DASH_PERMS_JSON=$(printf '{"access_control_list":[{"service_principal_name":"%s","permission_level":"CAN_READ"}]}' "$SP")
  databricks api patch "/api/2.0/permissions/dashboards/$DASHBOARD_ID" --profile "$PROFILE" --json "$DASH_PERMS_JSON" >/dev/null

  # Publish with embed_credentials=true so iframe widgets run queries as the
  # dashboard owner (you), not the unauthenticated iframe viewer. Without this
  # every widget renders "Unable to render visualization."
  WAREHOUSE_ID="${WAREHOUSE_ID:?WAREHOUSE_ID required when DASHBOARD_ID is set — the dashboard SQL warehouse}"
  echo "== Publish dashboard $DASHBOARD_ID with embed_credentials=true on warehouse $WAREHOUSE_ID =="
  PUBLISH_JSON=$(printf '{"warehouse_id":"%s","embed_credentials":true}' "$WAREHOUSE_ID")
  databricks api post "/api/2.0/lakeview/dashboards/$DASHBOARD_ID/published" --profile "$PROFILE" --json "$PUBLISH_JSON" >/dev/null

  # Grant CAN_READ on every directory between /Users/<user> and the dashboard.
  PARENT_PATH="${DASHBOARD_PARENT_PATH:-/Users/$USER_EMAIL/.bundle/production-line-copilot/$PROFILE/resources}"
  CURRENT="$PARENT_PATH"
  while [ "$CURRENT" != "/Users/$USER_EMAIL" ] && [ "$CURRENT" != "/" ] && [ "$CURRENT" != "" ]; do
    DIR_JSON=$(databricks workspace get-status "$CURRENT" --profile "$PROFILE" --output json 2>/dev/null || true)
    # Allow optional whitespace after the colon — the CLI emits pretty-printed JSON.
    DIR_ID=$(printf '%s\n' "$DIR_JSON" | grep -oE '"object_id":[[:space:]]*[0-9]+' | head -1 | grep -oE '[0-9]+')
    if [ -n "$DIR_ID" ]; then
      echo "== Workspace dir $CURRENT (id=$DIR_ID): CAN_READ to $SP =="
      DIR_PERMS_JSON=$(printf '{"access_control_list":[{"service_principal_name":"%s","permission_level":"CAN_READ"}]}' "$SP")
      databricks api patch "/api/2.0/permissions/directories/$DIR_ID" --profile "$PROFILE" --json "$DIR_PERMS_JSON" >/dev/null
    fi
    CURRENT="$(dirname "$CURRENT")"
  done
fi

echo
echo "Done."
[ -n "${DASHBOARD_ID:-}" ] && echo "  - Data Quality tab: dashboard should embed (Reload if cached)."
echo "  - Cost & Obs tab: bundle's declarative CAN_MANAGE_RUN + CAN_RUN grants already in place;"
echo "    RUNNING resources should show non-zero \$/h after the next 'databricks bundle deploy'."
