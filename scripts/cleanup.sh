#!/bin/bash

# ProdLine CoPilot — Databricks Asset Bundle cleanup / teardown.
#
# Destroys everything scripts/deploy.sh created for one environment (App, jobs,
# dashboard, MLflow experiment, bundle workspace files), deletes the Genie spaces
# it provisioned, and resets the config keys deploy.sh wrote back into
# config/project-settings.local.yaml so the NEXT deploy is genuinely fresh.
#
# With --purge-data it also drops the Unity Catalog streaming schema (and the
# governed MVM schemas), which is what you want before re-testing the one-line
# deploy from scratch.
#
# Usage:
#   ./scripts/cleanup.sh dev                # destroy bundle + Genie + config reset
#   ./scripts/cleanup.sh dev --purge-data   # ... and DROP the UC schemas
#   ./scripts/cleanup.sh prod
#
# NOT removed (provisioned as prerequisites, shared, or holding data you may
# want): the UC catalog itself, the Lakebase instance, secret scopes, the SQL
# warehouse, and model serving endpoints.
#
# Auth: uses the DATABRICKS_PROFILE env var (default: DEFAULT), same as deploy.sh.

set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
warn()    { echo -e "${YELLOW}[WARNING]${NC} $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROFILE="${DATABRICKS_PROFILE:-DEFAULT}"

case "${1:-}" in
  dev)  TARGET="line_manager_dev" ;;
  prod) TARGET="line_manager_prod" ;;
  *)    error "Usage: $0 <dev|prod> [--purge-data]"; exit 1 ;;
esac

PURGE_DATA=0
[[ "${2:-}" == "--purge-data" ]] && PURGE_DATA=1

SETTINGS="$ROOT/config/project-settings.local.yaml"
[[ -f "$SETTINGS" ]] || SETTINGS="$ROOT/config/project-settings.yaml"

# The UC purge needs the Databricks SDK, which the system python3 used for the
# lightweight YAML steps usually lacks. Same interpreter preference as deploy.sh:
# project venv, then uv, then system python3.
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  SDK_PY=("$ROOT/.venv/bin/python")
elif command -v uv >/dev/null 2>&1; then
  SDK_PY=(uv run --quiet python)
else
  SDK_PY=(python3)
fi

# Read one field from the profile whose bundle_target matches $TARGET.
# str() before strip() so a YAML boolean or int doesn't raise (and get silently
# swallowed into an empty string, which reads as "key missing").
_field() {
  python3 -c "
import sys, yaml
doc = yaml.safe_load(open('$SETTINGS', encoding='utf-8')) or {}
for _n, p in (doc.get('profiles') or {}).items():
    if (p or {}).get('bundle_target') == '$TARGET':
        print(str(p.get('$1') or '').strip())
        break
" 2>/dev/null || echo ""
}

if ! command -v databricks &> /dev/null; then
  error "Databricks CLI is not installed or not in PATH"
  exit 1
fi

# Regenerate bundle targets so `bundle destroy` sees the same target/host that
# `deploy.sh` used (config/bundle-targets.yaml is gitignored / generated).
#
# The deployer email must be substituted here exactly as deploy.sh does it.
# project-settings.yaml ships with a literal `${DEPLOYER_EMAIL}` placeholder so
# the repo is forkable, and Terraform reads an unsubstituted `${...}` as a
# resource reference — which fails `bundle destroy` with "Invalid reference"
# before it deletes anything.
DEPLOY_USER=$(databricks current-user me --profile "$PROFILE" --output json 2>/dev/null \
  | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d.get("userName") or d.get("emails",[{}])[0].get("value",""))' 2>/dev/null \
  || echo "")
DEPLOY_USER="${DEPLOYER_EMAIL:-$DEPLOY_USER}"
if [[ -z "$DEPLOY_USER" ]]; then
  error "Could not resolve deployer email (databricks current-user me failed and DEPLOYER_EMAIL is unset)."
  error "Set DEPLOYER_EMAIL=you@company.com or fix the CLI profile and retry."
  exit 1
fi

#
# Deliberately NO --target: that flag renders dashboards/data_quality.lvdash.json
# in place, and only deploy.sh has the backup/restore trap that puts the
# {{UC_CATALOG}} placeholders back. A teardown doesn't need a rendered
# dashboard, and destroying one must never leave the committed template
# pinned to the catalog of whichever environment was last torn down.
info "Regenerating bundle targets from config/project-settings(.local).yaml ..."
python3 "$ROOT/scripts/sync_project_settings.py" --deployer-email "$DEPLOY_USER"

info "Deployment summary for target: $TARGET (profile: $PROFILE)"
DATABRICKS_PROFILE="$PROFILE" databricks bundle summary -t "$TARGET" || true

UC_CATALOG=$(_field uc_catalog)
UC_SCHEMA=$(_field uc_schema)
MVM_CATALOG=$(_field mvm_catalog)

# The governed MVM schemas (asset/production/quality/inventory) are catalog-level
# and carry NO environment suffix, unlike uc_schema. Every profile pointing at
# the same mvm_catalog therefore shares one physical governed model, and
# dropping it while tearing down one environment would silently destroy the
# other's. Only purge when this target is the sole owner of that catalog.
MVM_SHARED_WITH=$(python3 - "$SETTINGS" "$TARGET" <<'PY'
import sys

import yaml

path, target = sys.argv[1], sys.argv[2]
doc = yaml.safe_load(open(path, encoding="utf-8")) or {}
profiles = (doc.get("profiles") or {}).values()
mine = next((p for p in profiles if (p or {}).get("bundle_target") == target), {}) or {}
mvm = str(mine.get("mvm_catalog") or "").strip()
if not mvm or mvm == "SET_ME":
    sys.exit(0)
print(",".join(
    str((p or {}).get("bundle_target"))
    for p in profiles
    if (p or {}).get("bundle_target") != target
    and str((p or {}).get("mvm_catalog") or "").strip() == mvm
))
PY
)
MVM_PURGE_CATALOG="$MVM_CATALOG"
[[ -n "$MVM_SHARED_WITH" ]] && MVM_PURGE_CATALOG=""
WAREHOUSE_ID=$(_field sql_warehouse_id)
GENIE_ID=$(_field genie_space_id)
MVM_GENIE_ID=$(_field mvm_genie_space_id)

echo ""
warn "This will permanently delete the deployed bundle resources for '$TARGET':"
warn "   • Databricks App (production-line-copilot-$1) and its service principal"
warn "   • Setup / streaming / quality-sync jobs"
warn "   • Data Quality dashboard and MLflow experiment"
warn "   • Bundle workspace files"
warn "   • Genie spaces created by deploy.sh"
warn "   • app_client_id / lakebase_pg_user / genie space ids reset to SET_ME"
if [[ "$PURGE_DATA" == "1" ]]; then
  warn "   • UC SCHEMA ${UC_CATALOG}.${UC_SCHEMA} (CASCADE — tables AND checkpoints)"
  if [[ -n "$MVM_PURGE_CATALOG" ]]; then
    warn "   • Governed MVM schemas in ${MVM_PURGE_CATALOG} (asset/production/quality/inventory)"
  elif [[ -n "$MVM_SHARED_WITH" ]]; then
    info "   • Governed MVM schemas in ${MVM_CATALOG} are KEPT — shared with: ${MVM_SHARED_WITH}"
  fi
else
  info "Unity Catalog data is KEPT. Re-run with --purge-data to drop it."
fi
echo ""

read -p "Type 'destroy $1' to continue: " -r REPLY
echo
if [[ "$REPLY" != "destroy $1" ]]; then
  info "Cleanup cancelled."
  exit 0
fi

info "Destroying bundle target $TARGET ..."
if DATABRICKS_PROFILE="$PROFILE" databricks bundle destroy -t "$TARGET" --auto-approve; then
  success "Bundle '$TARGET' destroyed."
else
  error "Bundle destruction failed"
  exit 1
fi

# ── Genie spaces ─────────────────────────────────────────────────────────────
# bundle destroy doesn't know about these (created via REST by deploy.sh). They
# must go BEFORE the config reset below: deploy.sh creates a new space whenever
# the stored id is SET_ME, so resetting without deleting leaks a duplicate space
# on every teardown/redeploy cycle.
for space in "$GENIE_ID" "$MVM_GENIE_ID"; do
  if [[ -n "$space" && "$space" != "SET_ME" ]]; then
    info "Deleting Genie space $space ..."
    databricks api delete "/api/2.0/genie/spaces/$space" --profile "$PROFILE" >/dev/null 2>&1 \
      && success "Genie space $space deleted." \
      || warn "Could not delete Genie space $space (already gone?) — check manually."
  fi
done

# ── Unity Catalog data (opt-in) ──────────────────────────────────────────────
# DROP SCHEMA ... CASCADE, deliberately not table-by-table: the streaming
# checkpoints live in a UC volume (<schema>._checkpoints) inside this schema. A
# checkpoint pins the source table's id, so dropping tables while keeping the
# volume leaves the next deploy's streaming job crash-looping against
# checkpoints that reference tables which no longer exist.
if [[ "$PURGE_DATA" == "1" ]]; then
  if [[ -z "$WAREHOUSE_ID" || -z "$UC_CATALOG" || -z "$UC_SCHEMA" ]]; then
    warn "warehouse_id / uc_catalog / uc_schema not resolved — skipping UC purge."
  else
    info "Dropping ${UC_CATALOG}.${UC_SCHEMA} CASCADE (includes _checkpoints volume) ..."
    if DATABRICKS_CONFIG_PROFILE="$PROFILE" "${SDK_PY[@]}" - \
         "$ROOT" "$WAREHOUSE_ID" "$UC_CATALOG" "$UC_SCHEMA" "$MVM_PURGE_CATALOG" <<'PY'
import sys

root, wid, cat, sch, mvm = sys.argv[1:6]
sys.path.insert(0, f"{root}/scripts")
from _uc_sql import StmtCursor  # noqa: E402

stmts = [f"DROP SCHEMA IF EXISTS `{cat}`.`{sch}` CASCADE"]
if mvm and mvm != "SET_ME":
    stmts += [
        f"DROP SCHEMA IF EXISTS `{mvm}`.`{s}` CASCADE"
        for s in ("asset", "production", "quality", "inventory")
    ]

cur = StmtCursor(wid)
for stmt in stmts:
    print(f"  {stmt}")
    cur.execute(stmt)
PY
    then
      success "UC schemas dropped."
    else
      warn "UC purge failed — drop the schemas manually before redeploying."
    fi
  fi
fi

# ── Reset the config keys deploy.sh mutated ──────────────────────────────────
# deploy.sh writes the app SP and Genie ids back into the local settings file.
# Leaving them behind makes the next deploy graft grants onto a destroyed
# principal and skip the fresh-env setup entirely, while still exiting 0.
if [[ -f "$ROOT/config/project-settings.local.yaml" ]]; then
  info "Resetting deploy-managed keys in config/project-settings.local.yaml ..."
  python3 - "$ROOT/config/project-settings.local.yaml" "$TARGET" <<'PY'
import re, sys, yaml

path, target = sys.argv[1], sys.argv[2]
KEYS = ("app_client_id", "lakebase_pg_user", "genie_space_id", "mvm_genie_space_id")

lines = open(path, encoding="utf-8").read().split("\n")
doc = yaml.safe_load("\n".join(lines)) or {}
pname = next(
    (n for n, p in (doc.get("profiles") or {}).items()
     if (p or {}).get("bundle_target") == target),
    None,
)
if not pname:
    print(f"WARN: no profile with bundle_target={target!r}; nothing reset.")
    sys.exit(0)

out, in_block, block_indent, reset = [], False, None, []
for line in lines:
    hm = re.match(r"^(\s+)([A-Za-z0-9_.-]+):\s*$", line)
    if hm and hm.group(2) == pname:
        in_block, block_indent = True, len(hm.group(1))
        out.append(line)
        continue
    if in_block:
        cur = re.match(r"^(\s*)\S", line)
        if line.strip() and len(cur.group(1) if cur else " " * 999) <= block_indent:
            in_block = False
        else:
            pk = re.match(r"^(\s+)([A-Za-z0-9_]+):\s*.*$", line)
            if pk and pk.group(2) in KEYS:
                out.append(f'{pk.group(1)}{pk.group(2)}: "SET_ME"')
                reset.append(pk.group(2))
                continue
    out.append(line)

open(path, "w", encoding="utf-8").write("\n".join(out))
print(f"Reset {', '.join(reset) or '(none)'} for profile {pname!r}.")
PY
fi

echo ""
success "Teardown complete for '$TARGET'."
if [[ "$PURGE_DATA" != "1" ]]; then
  info "UC data kept. For a truly fresh deploy test, re-run with --purge-data."
fi
