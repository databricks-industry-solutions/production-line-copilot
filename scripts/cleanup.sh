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
#   ./scripts/cleanup.sh dev                  # destroy bundle + Genie + config reset
#   ./scripts/cleanup.sh dev --purge-data     # ... and DROP the UC schemas + Lakebase schemas
#   ./scripts/cleanup.sh dev --purge-shared   # ... and resources shared with other targets
#   ./scripts/cleanup.sh prod
#
# --purge-shared is for tearing down EVERY environment, e.g. before re-testing
# the deploy from scratch. Resources that several profiles point at — the
# governed MVM schemas, the Lakebase ai_serving/ai_chatbot namespaces, the
# AI Search endpoint — are skipped by default precisely so that tearing
# down dev cannot take prod's data with it. This flag says you mean it. It
# implies --purge-data.
#
# NOT removed (provisioned as prerequisites, or holding data you may want): the
# UC catalog itself, the Lakebase INSTANCE, secret scopes, the SQL warehouse,
# and model serving endpoints. The secret scope in particular must survive —
# it holds the Zerobus client secret, and we cannot put that value back.
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
PURGE_SHARED=0
for arg in "${@:2}"; do
  case "$arg" in
    --purge-data)   PURGE_DATA=1 ;;
    --purge-shared) PURGE_SHARED=1; PURGE_DATA=1 ;;
    *) error "Unknown flag: $arg"; exit 1 ;;
  esac
done

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
if [[ -n "$MVM_SHARED_WITH" && "$PURGE_SHARED" != "1" ]]; then
  MVM_PURGE_CATALOG=""
fi
WAREHOUSE_ID=$(_field sql_warehouse_id)
GENIE_ID=$(_field genie_space_id)
# Legacy. deploy.sh no longer creates the combined MVM space — nothing in the
# app ever called it — but an environment deployed before that removal still
# has one, and its id still sits in that operator's settings file. Teardown
# keeps reading the key so those spaces get collected instead of orphaned; it
# resolves to empty on any environment provisioned since.
MVM_GENIE_ID=$(_field mvm_genie_space_id)

# The four per-domain governed spaces deploy.sh creates (step 7c). These were
# missed entirely: a teardown deleted two of the six spaces and left their ids
# in the settings file, so the next deploy "refreshed" spaces that no longer
# existed and leaked four more on every cycle.
DOMAIN_GENIE_IDS=()
for _d in asset quality inventory production; do
  DOMAIN_GENIE_IDS+=("$(_field "genie_space_${_d}")")
done

# AI Search. The endpoint bills hourly and takes 15-40 minutes to rebuild,
# so it is only removed under --purge-shared. Indexes live inside the schema
# being dropped and go either way.
VS_ENDPOINT=$(_field vector_search_endpoint)

# The Knowledge Assistant. Not gated on --purge-shared: it is created per
# profile from `knowledge_assistant_name`, exactly like the Genie spaces, and it
# serves from an agent endpoint that bills while it is online. The document
# volume it reads lives inside the schema this script drops.
KA_ID=$(_field knowledge_assistant_id)

# Lakebase. The instance is a prerequisite and survives; its schemas are ours.
# ai_serving is shared across environments by design (shift_id is what isolates
# them), so dropping it needs --purge-shared.
LAKEBASE_PG_HOST=$(_field lakebase_pg_host)
LAKEBASE_PG_DB=$(_field lakebase_pg_database)
LAKEBASE_ENDPOINT=$(_field lakebase_endpoint)
APP_CLIENT_ID=$(_field app_client_id)
# deploy.sh registers a Lakebase role for the producer SP as well as the app SP.
# The producer SP itself is an operator-created prerequisite and survives, but
# the role deploy.sh granted it does not.
PRODUCER_CLIENT_ID=$(_field producer_client_id)

# The UC-backed traces experiment provision_trace_storage.py creates. It lives
# outside the bundle, so `bundle destroy` leaves it behind — and its trace
# tables are inside the schema dropped below. Because a UC trace link is
# permanent and the backend refuses to unset it, an experiment whose tables
# were dropped is scrap: the next deploy either fails on it or silently falls
# through to the "-2" fallback name, leaking one experiment per teardown cycle.
MLFLOW_EXPERIMENT_PATH=$(_field mlflow_experiment_path)

echo ""
warn "This will permanently delete the deployed bundle resources for '$TARGET':"
warn "   • Databricks App (production-line-copilot-$1) and its service principal"
warn "   • Setup / streaming / quality-sync jobs"
warn "   • Data Quality dashboard and MLflow experiment"
[[ -n "$MLFLOW_EXPERIMENT_PATH" && "$MLFLOW_EXPERIMENT_PATH" != "SET_ME" ]] && \
  warn "   • UC traces experiment ${MLFLOW_EXPERIMENT_PATH}-traces (and its -2..-7 fallbacks)"
warn "   • Bundle workspace files"
warn "   • Genie spaces created by deploy.sh (streaming, MVM, and the 4 governed domains)"
warn "   • app_client_id / lakebase_pg_user / genie space ids reset to SET_ME"
if [[ "$PURGE_DATA" == "1" ]]; then
  warn "   • UC SCHEMA ${UC_CATALOG}.${UC_SCHEMA} (CASCADE — tables AND checkpoints)"
  if [[ "$PURGE_SHARED" == "1" ]]; then
    warn "   • SHARED: Lakebase schemas ai_serving + ai_chatbot on ${LAKEBASE_PG_HOST:-?}"
    [[ -n "$VS_ENDPOINT" ]] && warn "   • SHARED: AI Search endpoint '${VS_ENDPOINT}' and its indexes (15-40 min to rebuild)"
  else
    info "   • Lakebase schemas and the AI Search endpoint are KEPT (--purge-shared to drop)"
  fi
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
for space in "$GENIE_ID" "$MVM_GENIE_ID" "${DOMAIN_GENIE_IDS[@]}"; do
  if [[ -n "$space" && "$space" != "SET_ME" ]]; then
    info "Deleting Genie space $space ..."
    databricks api delete "/api/2.0/genie/spaces/$space" --profile "$PROFILE" >/dev/null 2>&1 \
      && success "Genie space $space deleted." \
      || warn "Could not delete Genie space $space (already gone?) — check manually."
  fi
done

# ── AI Search indexes: this environment's always, before the schema goes ─────
# An index is a UC object inside the schema, but DROP SCHEMA CASCADE removes it
# without telling the serving side, so it has to go through the API *first* —
# which is why this runs above the purge and not with the endpoint below.
#
# The index is not shared either, and gating it on --purge-shared alongside the
# endpoint meant a dev teardown left a dev index serving over a table it had
# just dropped. The next "fresh" deploy then found an index already there,
# created nothing, and proved nothing. So this environment's own indexes — the
# ones inside the schema this script drops — go on every run.
if [[ -n "$VS_ENDPOINT" ]]; then
  _vs_indexes() {
    databricks vector-search-indexes list-indexes "$VS_ENDPOINT" --profile "$PROFILE" --output json 2>/dev/null \
      | python3 -c "
import json,sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
prefix = '$1'
for i in (d if isinstance(d, list) else d.get('vector_indexes', [])):
    name = i.get('name','')
    if name and name.startswith(prefix):
        print(name)
" 2>/dev/null || echo ""
  }
  if [[ "$PURGE_SHARED" == "1" ]]; then
    info "Deleting ALL AI Search indexes on '$VS_ENDPOINT' ..."
    VS_INDEXES=$(_vs_indexes "")
  else
    info "Deleting AI Search indexes under ${UC_CATALOG}.${UC_SCHEMA} on '$VS_ENDPOINT' ..."
    VS_INDEXES=$(_vs_indexes "${UC_CATALOG}.${UC_SCHEMA}.")
  fi
  while IFS= read -r idx; do
    [[ -z "$idx" ]] && continue
    databricks vector-search-indexes delete-index "$idx" --profile "$PROFILE" >/dev/null 2>&1 \
      && success "  index $idx deleted." \
      || warn "  could not delete index $idx — check manually."
  done <<< "$VS_INDEXES"
fi

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

  # The traces experiment's storage tables were inside the schema just dropped.
  # A UC trace link cannot be unset, so the experiment is now unusable; leaving
  # it means the next deploy skips to the "-2" fallback name and the workspace
  # accumulates a dead experiment per cycle. Delete the whole family.
  if [[ -n "$MLFLOW_EXPERIMENT_PATH" && "$MLFLOW_EXPERIMENT_PATH" != "SET_ME" ]]; then
    info "Deleting UC traces experiment(s) for ${MLFLOW_EXPERIMENT_PATH}-traces ..."
    DATABRICKS_CONFIG_PROFILE="$PROFILE" "${SDK_PY[@]}" - "$MLFLOW_EXPERIMENT_PATH" <<'PY' || \
      warn "Could not delete the traces experiment — remove it by hand before redeploying."
import sys

from databricks.sdk import WorkspaceClient

base = f"{sys.argv[1]}-traces"
# provision_trace_storage.py falls back to -2..-7 when a name is already scrap.
names = [base] + [f"{base}-{n}" for n in range(2, 8)]

w = WorkspaceClient()
for name in names:
    try:
        exp = w.experiments.get_by_name(name).experiment
    except Exception:
        continue
    if exp is None:
        continue
    try:
        w.experiments.delete_experiment(exp.experiment_id)
        print(f"  deleted {name} ({exp.experiment_id})")
    except Exception as exc:  # noqa: BLE001 - report and keep going
        print(f"  WARN: {name}: {exc}")
PY
  fi
fi

# ── Knowledge Assistant (opt-in) ─────────────────────────────────────────────
# Deleting the assistant takes its knowledge sources and its serving endpoint
# with it. Left behind, the next deploy finds an assistant whose corpus volume
# was dropped underneath it, and re-syncs an empty knowledge base.
if [[ -n "$KA_ID" && "$KA_ID" != "SET_ME" ]]; then
  info "Deleting Knowledge Assistant 'knowledge-assistants/$KA_ID' ..."
  databricks knowledge-assistants delete-knowledge-assistant \
    "knowledge-assistants/$KA_ID" --profile "$PROFILE" >/dev/null 2>&1 \
    && success "Knowledge Assistant deleted." \
    || warn "Could not delete knowledge-assistants/$KA_ID (already gone, or the CLI predates the command group) — check the Agents page."
fi

# ── AI Search endpoint (shared) ──────────────────────────────────────────────
# The indexes on it went earlier, before the schema drop. The endpoint itself is
# the expensive part to rebuild — 15-40 minutes — and is shared with any other
# profile naming it, hence --purge-shared.
if [[ "$PURGE_SHARED" == "1" && -n "$VS_ENDPOINT" ]]; then
  info "Deleting AI Search endpoint '$VS_ENDPOINT' ..."
  databricks vector-search-endpoints delete-endpoint "$VS_ENDPOINT" --profile "$PROFILE" >/dev/null 2>&1 \
    && success "AI Search endpoint deleted." \
    || warn "Could not delete endpoint '$VS_ENDPOINT' (already gone?) — check manually."
fi

# ── Lakebase schemas + app SP role (shared) ──────────────────────────────────
# The instance and the database survive — they are prerequisites. What we own
# inside them is the ai_serving namespace (live SSE tables), ai_chatbot
# (conversation memory), and the Postgres role registered for the app SP. The
# role has to go via the Postgres API rather than DROP ROLE: it was created
# through w.postgres.create_role with LAKEBASE_OAUTH_V1, and a SQL-level drop
# leaves the managed-identity registry pointing at nothing.
if [[ "$PURGE_SHARED" == "1" && -n "$LAKEBASE_PG_HOST" ]]; then
  info "Dropping Lakebase schemas ai_serving + ai_chatbot on $LAKEBASE_PG_HOST ..."
  if DATABRICKS_CONFIG_PROFILE="$PROFILE" "${SDK_PY[@]}" - \
       "$LAKEBASE_PG_HOST" "$LAKEBASE_PG_DB" "$LAKEBASE_ENDPOINT" "$DEPLOY_USER" <<'PY'
import sys

import psycopg2
from databricks.sdk import WorkspaceClient

host, database, endpoint, user = sys.argv[1:5]
w = WorkspaceClient()


def _token() -> str:
    """Same path the producer and the sink use (src/streaming_sim/lakebase_sink.py)."""
    if endpoint:
        try:
            return w.postgres.generate_database_credential(endpoint=endpoint).token
        except Exception as exc:  # noqa: BLE001
            print(f"  generate_database_credential failed ({str(exc)[:100]}) — using workspace token")
    tok = w.config.authenticate()
    if callable(tok):
        import requests

        prepared = requests.Request("GET", "https://dummy").prepare()
        tok(prepared)
        return prepared.headers.get("Authorization", "").replace("Bearer ", "")
    return str(tok)


conn = psycopg2.connect(
    host=host, dbname=database, user=user, password=_token(),
    sslmode="require", connect_timeout=20,
)
conn.autocommit = True
with conn.cursor() as cur:
    for schema in ("ai_serving", "ai_chatbot", "appkit"):
        print(f"  DROP SCHEMA IF EXISTS {schema} CASCADE")
        cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    # AppKit's Lakebase cache. Newer versions put it in an `appkit` schema
    # (dropped above); older ones landed it in `public`, where DROP SCHEMA is
    # not an option because `public` is not ours to remove.
    print("  DROP TABLE IF EXISTS public.appkit_cache_entries")
    cur.execute("DROP TABLE IF EXISTS public.appkit_cache_entries")
conn.close()
PY
  then
    success "Lakebase schemas dropped."
  else
    warn "Lakebase purge failed — drop ai_serving / ai_chatbot manually before redeploying."
  fi

  # deploy.sh registers a role for both the app SP and the producer SP, so both
  # have to come back off. The role is addressed by its resource NAME
  # (projects/<p>/branches/<b>/roles/rol-…), not by the SP client id — the id
  # only appears as status.postgres_role. Deleting by client id silently 404s.
  if [[ -n "$LAKEBASE_ENDPOINT" ]]; then
    LAKEBASE_PARENT="${LAKEBASE_ENDPOINT%/endpoints/*}"
    for _sp_pair in "app SP:$APP_CLIENT_ID" "producer SP:$PRODUCER_CLIENT_ID"; do
      _sp_label="${_sp_pair%%:*}"
      _sp_id="${_sp_pair#*:}"
      [[ -z "$_sp_id" || "$_sp_id" == "SET_ME" ]] && continue
      info "Removing Lakebase role for $_sp_label $_sp_id ..."
      ROLE_NAME=$(databricks api get "/api/2.0/postgres/$LAKEBASE_PARENT/roles" --profile "$PROFILE" 2>/dev/null \
        | python3 -c "
import json, sys
try:
    roles = json.load(sys.stdin).get('roles', [])
except Exception:
    sys.exit(0)
for r in roles:
    if (r.get('status') or {}).get('postgres_role') == '$_sp_id':
        print(r.get('name', ''))
        break
" 2>/dev/null || echo "")
      if [[ -n "$ROLE_NAME" ]]; then
        databricks api delete "/api/2.0/postgres/$ROLE_NAME" --profile "$PROFILE" >/dev/null 2>&1 \
          && success "Lakebase role $ROLE_NAME removed." \
          || warn "Could not remove Lakebase role $ROLE_NAME — check manually."
      else
        info "No Lakebase role registered for $_sp_id — nothing to remove."
      fi
    done
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
KEYS = (
    "app_client_id", "lakebase_pg_user", "genie_space_id",
    # Legacy, kept so a settings file written before the combined MVM space was
    # removed does not keep a dangling id for a space this teardown deleted.
    "mvm_genie_space_id",
    # deploy.sh writes these back too (step 7c). Leaving them behind made the
    # next deploy "refresh" four spaces it had just deleted.
    "genie_space_asset", "genie_space_quality",
    "genie_space_inventory", "genie_space_production",
    # The assistant this teardown just deleted. Left behind, the next deploy
    # points the app at an endpoint that no longer exists and syncs an assistant
    # that is gone — while reporting the document path as configured.
    "knowledge_assistant_endpoint", "knowledge_assistant_id",
)

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
