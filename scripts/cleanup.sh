#!/bin/bash

# ProdLine CoPilot — Databricks Asset Bundle cleanup / teardown.
#
# Destroys everything scripts/deploy.sh created for one environment (App, jobs,
# dashboard, MLflow experiment, bundle workspace files). Unity Catalog data,
# Lakebase, and secret scopes provisioned as prerequisites are NOT removed here —
# tear those down separately if you no longer need them.
#
# Usage:
#   ./scripts/cleanup.sh dev     # destroy line_manager_dev  (production-line-copilot-dev)
#   ./scripts/cleanup.sh prod    # destroy line_manager_prod (production-line-copilot-prod)
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
  *)    error "Usage: $0 <dev|prod>"; exit 1 ;;
esac

if ! command -v databricks &> /dev/null; then
  error "Databricks CLI is not installed or not in PATH"
  exit 1
fi

# Regenerate bundle targets so `bundle destroy` sees the same target/host that
# `deploy.sh` used (config/bundle-targets.yaml is gitignored / generated).
info "Regenerating bundle targets from config/project-settings(.local).yaml ..."
python3 "$ROOT/scripts/sync_project_settings.py"

info "Deployment summary for target: $TARGET (profile: $PROFILE)"
DATABRICKS_PROFILE="$PROFILE" databricks bundle summary -t "$TARGET" || true

echo ""
warn "This will permanently delete the deployed bundle resources for '$TARGET':"
warn "   • Databricks App (production-line-copilot-$1)"
warn "   • Setup / streaming / quality-sync jobs"
warn "   • Data Quality dashboard and MLflow experiment"
warn "   • Bundle workspace files"
echo ""

read -p "Type 'destroy $1' to continue: " -r REPLY
echo
if [[ "$REPLY" == "destroy $1" ]]; then
  info "Destroying bundle target $TARGET ..."
  if DATABRICKS_PROFILE="$PROFILE" databricks bundle destroy -t "$TARGET" --auto-approve; then
    success "Bundle '$TARGET' destroyed."
  else
    error "Bundle destruction failed"
    exit 1
  fi
else
  info "Cleanup cancelled."
  exit 0
fi
