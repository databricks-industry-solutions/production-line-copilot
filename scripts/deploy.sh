#!/usr/bin/env bash
# Unified dev/prod deploy script for the streaming workspace.
#
# Usage:
#   ./scripts/deploy.sh dev            # deploy to line_manager_dev (production-line-copilot-dev)
#   ./scripts/deploy.sh prod           # deploy to line_manager_prod (production-line-copilot-prod)
#   ./scripts/deploy.sh dev --skip-app # bundle deploy only, skip ``databricks apps deploy``
#   ./scripts/deploy.sh dev --capabilities  # print what would be built, deploy nothing
#   MINIMAL=1 ./scripts/deploy.sh dev  # skip the two capabilities that bill hourly
#   VS_WAIT=1 ./scripts/deploy.sh dev  # block until AI Search is online (15-40 min)
#
# Layered deploy:
#   1. ``python3 scripts/sync_project_settings.py``  → bundle-targets.yaml
#   2. ``databricks bundle deploy --target <target>`` → jobs / dashboard
#   3. ``python3 scripts/refresh_app_yaml_ids.py``  → write fresh OPS_* IDs into app.<env>.yaml
#   4. ``cp app.<env>.yaml app.yaml`` (back up & restore on EXIT trap)
#   5. ``databricks apps deploy <app-name>`` → push the new env vars / build
#   6. Idempotent app-SP UC grants (self-heal: re-applies USE CATALOG/SCHEMA +
#      SELECT so a redeployed/recreated app SP never crashes on startup SQL)
#
# Profile: defaults to ``DEFAULT`` (Databricks CLI profile pointing at the
# workspace selected for the bundle target). Override with DATABRICKS_PROFILE.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR"

ENV="${1:-}"
shift || true

case "$ENV" in
  dev|prod)
    : ;;
  *)
    cat >&2 <<EOF
Usage: $0 <dev|prod> [--skip-app] [--capabilities]
  dev   → bundle target line_manager_dev,  app production-line-copilot-dev
  prod  → bundle target line_manager_prod, app production-line-copilot-prod

  --capabilities  print which capabilities this env would build, then exit
  MINIMAL=1       skip the two capabilities that bill hourly (AI Search,
                  Knowledge Assistant); everything else still deploys
  VS_WAIT=1       block until the AI Search endpoint is online (15-40 min)
                  instead of letting the next deploy build the index

Profile: \$DATABRICKS_PROFILE (default: DEFAULT)
EOF
    exit 2 ;;
esac

SKIP_APP=0
EXTRA=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-app) SKIP_APP=1; shift ;;
    --capabilities) CAPABILITIES_ONLY=1; shift ;;
    --) shift; EXTRA+=("$@"); break ;;
    *)  EXTRA+=("$1"); shift ;;
  esac
done

# Read-only inventory, before anything below can touch a file or a workspace:
# someone asking what a deploy would build has not asked for a deploy.
# No array: this runs under bash 3.2, where `set -u` treats an empty "${a[@]}"
# as an unbound variable and aborts.
if [[ "${CAPABILITIES_ONLY:-0}" == "1" ]]; then
  if [[ "${MINIMAL:-0}" == "1" ]]; then
    exec python3 scripts/capabilities.py "$ENV" --minimal
  fi
  exec python3 scripts/capabilities.py "$ENV"
fi

PROFILE="${DATABRICKS_PROFILE:-DEFAULT}"

# Resolve the app source yaml. Operator-local values (real UC catalog, PG
# host, app SP UUID, refreshed OPS_* IDs) live in app.local.yaml /
# app.dev.local.yaml — both gitignored. The committed app.yaml /
# app.dev.yaml ship as SET_ME placeholders. If a .local.yaml sibling
# exists, prefer it; otherwise fall back to the committed file and let the
# SET_ME guard below fail the deploy with a clear message.
if [[ "$ENV" == "dev" ]]; then
  BUNDLE_TARGET="line_manager_dev"
  APP_NAME="production-line-copilot-dev"
  if [[ -f "$ROOT_DIR/app.dev.local.yaml" ]]; then
    APP_SOURCE_YAML="app.dev.local.yaml"
  else
    APP_SOURCE_YAML="app.dev.yaml"
  fi
else
  BUNDLE_TARGET="line_manager_prod"
  APP_NAME="production-line-copilot-prod"
  if [[ -f "$ROOT_DIR/app.local.yaml" ]]; then
    APP_SOURCE_YAML="app.local.yaml"
  else
    APP_SOURCE_YAML="app.yaml"  # already prod-shaped
  fi
fi
echo "=== APP_SOURCE_YAML=$APP_SOURCE_YAML ==="

# Single source of truth: shared app env (UC catalog/schema, Lakebase host +
# endpoint, shift id, Genie) is DERIVED from project-settings by
# render_app_yaml.py below. Ensure we edit a gitignored app.<env>.local.yaml so
# the committed template stays clean on forks; from here on APP_SOURCE_YAML
# always points at the local file.
if [[ "$APP_SOURCE_YAML" != *".local.yaml" ]]; then
  APP_LOCAL_YAML="${APP_SOURCE_YAML%.yaml}.local.yaml"
  if [[ ! -f "$ROOT_DIR/$APP_LOCAL_YAML" ]]; then
    cp "$ROOT_DIR/$APP_SOURCE_YAML" "$ROOT_DIR/$APP_LOCAL_YAML"
    echo "=== Created $APP_LOCAL_YAML from template ==="
  fi
  APP_SOURCE_YAML="$APP_LOCAL_YAML"
fi

# Resolve the project-settings.yaml that drives bundle-targets + lakebase
# wiring. Same pattern: prefer the gitignored .local.yaml when present.
if [[ -f "$ROOT_DIR/config/project-settings.local.yaml" ]]; then
  PROJECT_SETTINGS_YAML="config/project-settings.local.yaml"
else
  PROJECT_SETTINGS_YAML="config/project-settings.yaml"
fi
echo "=== PROJECT_SETTINGS_YAML=$PROJECT_SETTINGS_YAML ==="

# The plan, before the work: which capabilities this run builds, what each one
# costs, and which of them are slow. Printed here rather than only at the end
# because the timings are worth knowing before the wait, not during it.
if [[ "${MINIMAL:-0}" == "1" ]]; then
  python3 scripts/capabilities.py "$ENV" --settings "$PROJECT_SETTINGS_YAML" --minimal || true
else
  python3 scripts/capabilities.py "$ENV" --settings "$PROJECT_SETTINGS_YAML" || true
fi
echo

# Optional preflight: set RUN_PREFLIGHT=1 to run scripts/preflight.py before deploy.
if [[ "${RUN_PREFLIGHT:-0}" == "1" ]]; then
  echo "=== preflight.py (RUN_PREFLIGHT=1) ==="
  python3 scripts/preflight.py "$ENV" --profile "$PROFILE" || {
    echo "ERROR: preflight failed — fix the issues above or deploy with RUN_PREFLIGHT=0." >&2
    exit 1
  }
fi

# ── Guard: normalize uv.lock to public PyPI ────────────────────────────
# The Databricks Apps build sandbox can only reach public PyPI. Some corporate
# environments inject a private PyPI mirror (via ~/.config/uv/uv.toml or
# pip.conf), so a local ``uv lock`` bakes that mirror's index/file URLs into
# uv.lock and the app build then hangs/fails downloading dependencies. Rewrite
# any non-public mirror host back to public PyPI in place — package versions and
# hashes are preserved, so this is safe and idempotent.
if [[ -f "$ROOT_DIR/uv.lock" ]]; then
  python3 - "$ROOT_DIR/uv.lock" <<'PY'
import re, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as f:
    original = f.read()
text = re.sub(r"https://[A-Za-z0-9.\-]+/simple/", "https://pypi.org/simple/", original)
text = re.sub(r'(url = "https://)[A-Za-z0-9.\-]+(/packages/)', r"\1files.pythonhosted.org\2", text)
if text != original:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print("WARN: rewrote private PyPI mirror URLs in uv.lock → public PyPI (commit this).", file=sys.stderr)
PY
fi

# ── Guard: normalize package-lock.json to public npm ───────────────────
# Same failure mode as uv.lock, one layer up: if a developer's global npm
# registry points at a private/internal mirror (e.g. an npm-proxy host), a
# local ``npm install`` bakes that host into package-lock.json's ``resolved``
# URLs. The Databricks Apps build sandbox can only reach public npm, so it
# times out fetching from the unreachable mirror and the app never starts.
# Internal mirrors proxy npmjs path-for-path, so a bare host swap is safe:
# tarball paths are preserved and integrity hashes are content-based. Idempotent.
if [[ -f "$ROOT_DIR/package-lock.json" ]]; then
  python3 - "$ROOT_DIR/package-lock.json" <<'PY'
import re, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as f:
    original = f.read()
# Only rewrite the HOST of npm *tarball* resolved URLs (path contains "/-/" and
# ends in ".tgz"), and only when the host isn't already public npm. This leaves
# git/github "resolved" URLs (different path shape) untouched.
def _swap(m):
    host = m.group(1)
    if host == "registry.npmjs.org":
        return m.group(0)
    return f'"resolved": "https://registry.npmjs.org/{m.group(2)}'
text = re.sub(r'"resolved":\s*"https://([A-Za-z0-9.\-]+)/([^"]*/-/[^"]*\.tgz")', _swap, original)
if text != original:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print("WARN: rewrote private npm mirror URLs in package-lock.json → public npm (commit this).", file=sys.stderr)
PY
fi

# ── Safety: branch must match deploy target ─────────────────────────────
# Convention (set 2026-06-22): feature work commits to ``dev``, then merges
# to ``main`` when promoting to prod. So:
#   ./scripts/deploy.sh dev   → must be on branch ``dev``
#   ./scripts/deploy.sh prod  → must be on branch ``main``
# This guard prevents the most common footgun (deploying ``dev`` work to the
# prod app because you forgot to ``git checkout main`` after merging).
# Override for CI tag-deploys, detached-HEAD checks, or genuine hotfixes:
#   DEPLOY_IGNORE_BRANCH=1 ./scripts/deploy.sh prod
if command -v git >/dev/null 2>&1 && git -C "$ROOT_DIR" rev-parse --git-dir >/dev/null 2>&1; then
  CURRENT_BRANCH="$(git -C "$ROOT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "HEAD")"
  if [[ "$ENV" == "prod" ]]; then
    EXPECTED_BRANCH="main"
  else
    EXPECTED_BRANCH="dev"
  fi
  # Fork-friendly: only apply the convention when the expected branch actually
  # exists locally. Forks that don't use the dev/main split (e.g. a single
  # ``main`` branch) are never blocked. Prod is still a hard stop (deploying
  # unreviewed code to prod is the costly mistake); dev is a soft warning.
  if git -C "$ROOT_DIR" rev-parse --verify --quiet "$EXPECTED_BRANCH" >/dev/null 2>&1 \
     && [[ "$CURRENT_BRANCH" != "$EXPECTED_BRANCH" && "${DEPLOY_IGNORE_BRANCH:-0}" != "1" ]]; then
    if [[ "$ENV" == "prod" ]]; then
      echo >&2
      echo "ERROR: ./scripts/deploy.sh prod expects git branch '$EXPECTED_BRANCH', but you are on '$CURRENT_BRANCH'." >&2
      echo "       Convention: feature work → 'dev', merge to 'main' before prod deploy." >&2
      echo "       Override (CI / detached HEAD / hotfix): DEPLOY_IGNORE_BRANCH=1 ./scripts/deploy.sh prod" >&2
      exit 1
    else
      echo "WARN: deploying 'dev' from branch '$CURRENT_BRANCH' (convention is '$EXPECTED_BRANCH'). Continuing." >&2
    fi
  fi
fi

# ── Safety: prod requires explicit confirmation ─────────────────────────
if [[ "$ENV" == "prod" ]]; then
  echo
  echo "════════════════════════════════════════════════════════════════"
  echo "  PROD DEPLOY — line_manager_prod / production-line-copilot-prod"
  echo "  Profile:        $PROFILE"
  echo "  Bundle target:  $BUNDLE_TARGET"
  echo "  App:            $APP_NAME"
  echo "════════════════════════════════════════════════════════════════"
  echo
  read -r -p "Type 'deploy prod' to continue: " confirm
  if [[ "$confirm" != "deploy prod" ]]; then
    echo "Aborted." >&2
    exit 1
  fi
fi

# Resolve the deployer email FIRST so it can be threaded into both
# sync_project_settings.py and the app.yaml templating step below.
# `databricks current-user me` for the active CLI profile is the source of
# truth; override with DEPLOYER_EMAIL=… when deploying as a service principal.
DEPLOY_USER=$(databricks current-user me --profile "$PROFILE" --output json 2>/dev/null | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d.get("userName") or d.get("emails",[{}])[0].get("value",""))' 2>/dev/null || echo "")
DEPLOY_USER="${DEPLOYER_EMAIL:-$DEPLOY_USER}"
if [[ -z "$DEPLOY_USER" ]]; then
  echo "ERROR: could not resolve deployer email (databricks current-user me failed and DEPLOYER_EMAIL is unset)." >&2
  echo "       Set DEPLOYER_EMAIL=you@company.com or fix the CLI profile and retry." >&2
  exit 1
fi
export DEPLOYER_EMAIL="$DEPLOY_USER"

# ── Auth pre-flight against the path Terraform uses ──────────────────────────
# `current-user me` above proves the CLI can authenticate. It does not prove
# `bundle deploy` can, because that spawns Terraform, whose provider resolves
# credentials itself — from a filtered environment holding only DATABRICKS_HOST,
# DATABRICKS_ACCOUNT_ID, DATABRICKS_WORKSPACE_ID and DATABRICKS_CONFIG_PROFILE.
# Two consequences, both of which have broken a deploy here:
#
#   * The provider reads the DEFAULT (OS keyring) token cache, because
#     DATABRICKS_AUTH_STORAGE is not among the variables it is handed. Setting
#     it changes where the CLI looks and not where the provider looks — so a
#     `DATABRICKS_AUTH_STORAGE=plaintext` login makes things worse: it mints a
#     session that invalidates the refresh token in the keyring copy Terraform
#     is still using. Log in on one storage and stay there.
#   * A U2M OAuth session is shared. Anything else on the profile (an IDE, an
#     MCP server, a second deploy) refreshes it, the refresh token rotates, and
#     the copy Terraform holds goes invalid — "the refresh token is invalid".
#     Where the keyring is unreachable altogether ("OS keyring unreachable: exit
#     status 44" — CI, ssh, headless shells), the credential has to be one the
#     provider can resolve from the profile itself: a PAT, or a service
#     principal client id + secret. Neither rotates, which is what a 20-45
#     minute deploy wants regardless.
#
# All of it used to surface deep inside a later bundle pass, on whatever resource
# Terraform read first, tens of minutes in — after the setup job had run and the
# billing endpoints were up. `bundle plan` runs the same provider against the
# same state and costs seconds, so the failure lands here instead.
#
# Only an auth signature is fatal. A plan can fail for reasons that a deploy
# then fixes, and this is a probe for one blind spot, not a new gate.
_auth_failure_in() {
  grep -qiE "keyring unreachable|refresh token is invalid|cannot get access token|default auth: " "$1"
}
_explain_auth_failure() {
  echo "" >&2
  echo "This is an authentication failure inside Terraform, not a problem with the" >&2
  echo "bundle. The CLI authenticates fine; the provider it spawns resolves its own" >&2
  echo "credential from profile '$PROFILE' and the DEFAULT token cache." >&2
  echo "" >&2
  echo "  • Stale or rotated OAuth session — re-login on the default storage, which" >&2
  echo "    is the one the provider reads:" >&2
  echo "      databricks auth login --profile $PROFILE" >&2
  echo "    Do NOT set DATABRICKS_AUTH_STORAGE for this: the provider never sees it," >&2
  echo "    and logging in elsewhere invalidates the copy it is using." >&2
  echo "  • Keyring unreachable (CI, ssh, headless), or a session that keeps being" >&2
  echo "    rotated by other tools on this profile — give the profile a credential" >&2
  echo "    that does not rotate: a PAT (token = …) or a service principal" >&2
  echo "    (client_id + client_secret) in ~/.databrickscfg." >&2
  echo "" >&2
}
PREFLIGHT_LOG="$(mktemp -t bundle_plan.XXXXXX)"
if databricks bundle plan --target "$BUNDLE_TARGET" --profile "$PROFILE" > "$PREFLIGHT_LOG" 2>&1; then
  echo "=== Auth pre-flight: Terraform can authenticate (bundle plan) ==="
elif _auth_failure_in "$PREFLIGHT_LOG"; then
  echo "ERROR: auth pre-flight failed — 'bundle plan' cannot authenticate as $PROFILE." >&2
  sed -n '1,12p' "$PREFLIGHT_LOG" >&2
  _explain_auth_failure
  echo "Nothing has been deployed. Fix the credential and re-run." >&2
  rm -f "$PREFLIGHT_LOG"
  exit 1
else
  echo "=== Auth pre-flight: 'bundle plan' did not succeed, but not on auth — continuing ==="
  sed -n '1,6p' "$PREFLIGHT_LOG"
fi
rm -f "$PREFLIGHT_LOG"

# Trap setup: working copies of app.yaml + bundle-targets.yaml get the
# resolved deployer email substituted in before bundle/apps deploy. The
# committed copies ship with `${DEPLOYER_EMAIL}` literal so the repo is
# forkable without baking in the original author's identity. On exit
# (success or failure), the cleanup trap restores both files from their
# backups so `git status` stays clean.
APP_YAML_BACKUP=""
BUNDLE_TARGETS_BACKUP=""
DASHBOARD_BACKUP=""

# Restoring from a backup is only as good as the file we backed up. If an
# earlier deploy died before its trap ran, it left a RENDERED template on disk;
# this run then backs that up and faithfully restores the pollution, so one
# missed trap pins the committed template to a catalog forever. For files that
# were clean against HEAD when we started, git is the authoritative copy — use
# it, so the loop can't perpetuate.
_was_clean_at_start() {
  [[ "${IN_GIT_REPO:-0}" == "1" ]] || return 1
  git -C "$ROOT_DIR" diff --quiet HEAD -- "$1" 2>/dev/null
}
_restore_template() {
  local rel="$1" backup="$2" clean_at_start="$3"
  if [[ "$clean_at_start" == "1" ]]; then
    git -C "$ROOT_DIR" checkout -- "$rel" 2>/dev/null && return 0
  fi
  [[ -n "$backup" && -f "$backup" ]] && cp "$backup" "$ROOT_DIR/$rel"
}
IN_GIT_REPO=0
git -C "$ROOT_DIR" rev-parse --git-dir >/dev/null 2>&1 && IN_GIT_REPO=1
APP_YAML_WAS_CLEAN=0; _was_clean_at_start app.yaml && APP_YAML_WAS_CLEAN=1
DASHBOARD_WAS_CLEAN=0
_was_clean_at_start dashboards/data_quality.lvdash.json && DASHBOARD_WAS_CLEAN=1

cleanup() {
  if [[ -n "$APP_YAML_BACKUP" && -f "$APP_YAML_BACKUP" ]]; then
    _restore_template app.yaml "$APP_YAML_BACKUP" "$APP_YAML_WAS_CLEAN"
    rm -f "$APP_YAML_BACKUP"
  fi
  if [[ -n "$BUNDLE_TARGETS_BACKUP" && -f "$BUNDLE_TARGETS_BACKUP" ]]; then
    cp "$BUNDLE_TARGETS_BACKUP" "$ROOT_DIR/config/bundle-targets.yaml"
    rm -f "$BUNDLE_TARGETS_BACKUP"
  fi
  if [[ -n "$DASHBOARD_BACKUP" && -f "$DASHBOARD_BACKUP" ]]; then
    _restore_template dashboards/data_quality.lvdash.json \
      "$DASHBOARD_BACKUP" "$DASHBOARD_WAS_CLEAN"
    rm -f "$DASHBOARD_BACKUP"
  fi
  # Last line of defence: a rendered template must never survive the deploy that
  # rendered it. Say so loudly rather than let it get committed by accident.
  if [[ "${IN_GIT_REPO:-0}" == "1" ]]; then
    local leaked
    leaked=$(git -C "$ROOT_DIR" diff --name-only HEAD -- \
      app.yaml dashboards/data_quality.lvdash.json 2>/dev/null)
    if [[ -n "$leaked" ]]; then
      echo "WARN: deploy left rendered template(s) modified:" >&2
      echo "$leaked" | sed 's/^/        /' >&2
      echo "      Restore with: git checkout -- $(echo "$leaked" | tr '\n' ' ')" >&2
    fi
  fi
}
trap cleanup EXIT
# An interrupted deploy must still restore the templates. Exiting from these
# handlers runs the EXIT trap above exactly once; without them, Ctrl-C leaves a
# rendered dashboard/app.yaml committed to one workspace's catalog.
trap 'exit 130' INT
trap 'exit 143' TERM

# ── 1. Sync project-settings → bundle-targets + render dashboards ──────────
# Back up the committed (placeholder-carrying) bundle-targets.yaml AND the
# data_quality dashboard JSON first so the trap can restore both after the
# substitution-enabled regen below. The committed dashboard ships with
# {{UC_CATALOG}} / {{UC_SCHEMA}} placeholders so the same file works across
# every target; sync_project_settings.py --target <bt> renders it in place
# before bundle deploy uploads it to Lakeview.
if [[ -f "$ROOT_DIR/config/bundle-targets.yaml" ]]; then
  BUNDLE_TARGETS_BACKUP="$(mktemp -t bundle_targets_bak.XXXXXX)"
  cp "$ROOT_DIR/config/bundle-targets.yaml" "$BUNDLE_TARGETS_BACKUP"
fi
if [[ -f "$ROOT_DIR/dashboards/data_quality.lvdash.json" ]]; then
  DASHBOARD_BACKUP="$(mktemp -t data_quality_lvdash_bak.XXXXXX)"
  cp "$ROOT_DIR/dashboards/data_quality.lvdash.json" "$DASHBOARD_BACKUP"
fi
echo "=== sync_project_settings.py (deployer=${DEPLOYER_EMAIL}, target=${BUNDLE_TARGET}) ==="
python3 scripts/sync_project_settings.py --deployer-email "$DEPLOYER_EMAIL" --target "$BUNDLE_TARGET"

# ── 1b. Fail fast on any remaining SET_ME values for the target profile ──
# The committed config/project-settings.yaml ships as a placeholder template.
# Operators copy it to project-settings.local.yaml and fill in real values
# for the profile(s) they deploy. This guard surfaces an unfilled SET_ME
# before bundle deploy starts uploading resources against ``SET_ME`` hosts
# or trying to register service principals named ``SET_ME`` — at that point
# you'd get cryptic auth errors deep in the stack.
echo "=== SET_ME guard: $PROJECT_SETTINGS_YAML profile for $BUNDLE_TARGET ==="
python3 - "$ROOT_DIR/$PROJECT_SETTINGS_YAML" "$BUNDLE_TARGET" <<'PY'
import re
import sys
import yaml

path, bundle_target = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as f:
    settings = yaml.safe_load(f) or {}

# Find the profile for this bundle_target.
matching = []
for profile_name, prof in (settings.get("profiles") or {}).items():
    if (prof or {}).get("bundle_target") == bundle_target:
        matching.append((profile_name, prof))
if not matching:
    sys.stderr.write(
        f"ERROR: no profile in {path} has bundle_target={bundle_target!r}\n"
    )
    sys.exit(2)

# Fields that are REQUIRED to be non-SET_ME for a working deploy.
# We deliberately skip fields that are optional (lakebase_catalog,
# genie_space_id) or commonly empty (lakebase_schema).
# NOTE: app_client_id is intentionally NOT required here — it is auto-captured
# from ``databricks apps get`` after the first bundle deploy creates the app SP
# (chicken-and-egg: the SP does not exist until the app is created). Same for
# lakebase_pg_user (defaults to the app SP; the runtime resolves it from the
# injected DATABRICKS_CLIENT_ID anyway).
REQUIRED = {
    "workspace_host",
    "sql_warehouse_id",
    "uc_catalog",
    "uc_schema",
    "producer_client_id",
    "lakebase_pg_host",
    "lakebase_endpoint",
    "zerobus_server_endpoint",
    # Ships as /Volumes/<your_catalog>/... and was never checked, so an unfilled
    # value sent streaming checkpoints to a volume in a catalog that does not
    # exist — a failure that surfaces inside the quality-sync job, not here.
    "lakebase_checkpoint_volume",
}

unfilled: list[str] = []
for profile_name, prof in matching:
    for key in sorted(REQUIRED):
        value = (prof or {}).get(key)
        if value is None:
            continue
        text = str(value).strip()
        # An EMBEDDED placeholder counts. Several settings are paths with the
        # token in the middle — `projects/<your-lakebase-project>/branches/main`
        # — which passed a startswith/endswith check, so the guard reported the
        # config complete and the deploy failed later, deep in Lakebase role
        # registration, with the literal text as a project name.
        if text == "SET_ME" or re.search(r"<[^<>]+>", text):
            unfilled.append(f"  {profile_name}.{key} = {text!r}")

if unfilled:
    sys.stderr.write(
        f"ERROR: {len(unfilled)} required field(s) still carry placeholders\n"
        f"       in {path} for bundle_target={bundle_target!r}:\n"
    )
    sys.stderr.write("\n".join(unfilled) + "\n")
    sys.stderr.write(
        "\nFix: copy config/project-settings.yaml to\n"
        "     config/project-settings.local.yaml and fill in your values.\n"
    )
    sys.exit(2)

print(f"SET_ME guard passed: all required fields filled for {bundle_target!r}.")
PY

# ── 1b-ii. Seed-catalog reachability ─────────────────────────────────────
# src_catalog/src_schema are OPTIONAL: empty means the setup job generates
# reference data in-workspace. When they ARE set, the setup job copies the
# dimension tables from that seed schema — and if the catalog isn't readable
# here, it fails with INSUFFICIENT_PERMISSIONS in `bootstrap_reference`, which
# runs ~15 minutes into the job, AFTER init_uc and the industry-model install.
# Checking reachability now turns a long, late, cryptic failure into an
# immediate one that names the fix.
SRC_CATALOG=$(python3 - "$ROOT_DIR/$PROJECT_SETTINGS_YAML" "$BUNDLE_TARGET" <<'PY'
import sys

import yaml

path, bundle_target = sys.argv[1], sys.argv[2]
doc = yaml.safe_load(open(path, encoding="utf-8")) or {}
for prof in (doc.get("profiles") or {}).values():
    if (prof or {}).get("bundle_target") == bundle_target:
        print(str((prof or {}).get("src_catalog") or "").strip())
        break
PY
)
if [[ -n "$SRC_CATALOG" && "$SRC_CATALOG" != "SET_ME" ]]; then
  echo "=== Seed-catalog check: reference data will be COPIED from '$SRC_CATALOG' ==="
  if ! databricks catalogs get "$SRC_CATALOG" --profile "$PROFILE" >/dev/null 2>&1; then
    echo >&2
    echo "ERROR: src_catalog '$SRC_CATALOG' is not readable with profile '$PROFILE'." >&2
    echo "       The setup job's bootstrap_reference step would fail against it." >&2
    echo "       Fix: clear src_catalog/src_schema in $PROJECT_SETTINGS_YAML for" >&2
    echo "       '$BUNDLE_TARGET' to GENERATE reference data in-workspace instead," >&2
    echo "       or point them at a seed schema this workspace can read." >&2
    exit 2
  fi
fi

# ── 1c. Derive shared app env from project-settings (single source of truth) ──
# UC catalog/schema, Lakebase host + endpoint, shift id, and Genie space id live
# in project-settings; render them into app.<env>.local.yaml so users never keep
# two files in sync by hand. Runs before the swap/bundle deploy below.
echo "=== render_app_yaml.py $ENV (single source of truth) ==="
python3 scripts/render_app_yaml.py "$ENV" --settings "$ROOT_DIR/$PROJECT_SETTINGS_YAML" || {
  echo "WARN: render_app_yaml.py failed — app.yaml may carry stale/duplicated values." >&2
}

# ── Helpers: Lakebase role registration + app SP capture ────────────────
# Reusable pieces invoked AFTER the first bundle deploy (below). Chicken-and-egg
# fix: the App's service principal is created by the FIRST ``databricks bundle
# deploy`` (the bundle's ``apps:`` resource), so external users can't paste
# ``app_client_id`` up front. We capture it from ``databricks apps get`` after
# that deploy and thread it back into project-settings.local.yaml so a second
# deploy can apply the SP's CAN_MANAGE_RUN job grants. The app runtime resolves
# its own SP from the injected DATABRICKS_CLIENT_ID (agent_server/memory/pg_pool.py),
# so PGUSER in app.yaml is not needed at runtime.
LAKEBASE_PG_HOST=$(awk '/^  - name: PGHOST/{getline; gsub(/[ '"'"'"]/,""); sub(/^value:/,""); print; exit}' "$ROOT_DIR/$APP_SOURCE_YAML")

# Some steps need project dependencies (psycopg2 for init_lakebase_role.py, the
# Databricks SDK for the roster check). The system python3 used for the
# lightweight YAML steps above usually lacks them, so pick an interpreter that
# has them: prefer the project venv, then uv, then system python3.
if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PROJECT_PY=("$ROOT_DIR/.venv/bin/python")
elif command -v uv >/dev/null 2>&1; then
  PROJECT_PY=(uv run --quiet python)
else
  PROJECT_PY=(python3)
fi

# Which model answers for which agent role, and whether this workspace serves
# it. Printed on every deploy, not just under RUN_PREFLIGHT: the roster spans
# six vendors and a workspace missing one silently degrades that role to a
# fallback tier. That is a supported outcome, but the operator should see it
# here rather than discover it in the About tab. Never fatal.
echo "=== model roster coverage ==="
"${PROJECT_PY[@]}" scripts/preflight.py "$ENV" --models --profile "$PROFILE" || {
  echo "WARN: model roster check failed — continuing (deploy is unaffected)." >&2
}

_profile_field() {
  # Echo a field from the deploy target's profile in project-settings.
  python3 -c "
import yaml
with open('$ROOT_DIR/$PROJECT_SETTINGS_YAML') as f:
    d = yaml.safe_load(f)
for prof in (d.get('profiles') or {}).values():
    if prof.get('bundle_target') == '$BUNDLE_TARGET':
        print((prof.get('$1') or '').strip())
        break
" 2>/dev/null || echo ""
}

PRODUCER_SP_ID=$(_profile_field producer_client_id)

# What each optional capability actually did, collected as the deploy goes and
# printed together at the end. The skip lines below are true when they print and
# then scroll past hundreds of other lines, so the question they answer — "did I
# get the whole product or a subset?" — was unanswerable by the time anyone
# thought to ask it.
CAPABILITY_OUTCOMES=()
note_capability() { CAPABILITY_OUTCOMES+=("$1"); }

register_sp() {
  local sp_id="$1"
  local label="$2"
  if [[ -z "$sp_id" || "$sp_id" == "SET_ME" ]]; then
    echo "WARN: skipping $label — not configured."
    return 0
  fi
  local ep LAKEBASE_PROJECT LAKEBASE_BRANCH
  ep=$(_profile_field lakebase_endpoint)
  LAKEBASE_PROJECT=$(printf '%s' "$ep" | awk -F/ '$1=="projects"{print $2}')
  LAKEBASE_BRANCH=$(printf '%s' "$ep" | awk -F/ '$3=="branches"{print $4}')
  [[ -z "$LAKEBASE_BRANCH" ]] && LAKEBASE_BRANCH="production"
  if [[ -z "$LAKEBASE_PROJECT" || "$LAKEBASE_PROJECT" == "SET_ME" ]]; then
    echo "WARN: skipping $label — lakebase_endpoint in project-settings.yaml is missing or SET_ME." >&2
    return 0
  fi
  echo "=== init_lakebase_role.py — register $label ($sp_id) on project $LAKEBASE_PROJECT/$LAKEBASE_BRANCH ==="
  "${PROJECT_PY[@]}" scripts/init_lakebase_role.py \
    --profile "$PROFILE" \
    --project "$LAKEBASE_PROJECT" \
    --branch "$LAKEBASE_BRANCH" \
    --pg-host "$LAKEBASE_PG_HOST" \
    --pg-database databricks_postgres \
    --pg-admin-user "$DEPLOY_USER" \
    --sp-client-id "$sp_id" || {
      echo "WARN: init_lakebase_role failed for $label — Lakebase auth may not work." >&2
  }
}

# Write app_client_id + lakebase_pg_user for the deploy target's profile into
# project-settings.local.yaml, preserving comments/formatting (line-based,
# indentation-scoped to the target profile block). Creates the .local.yaml
# from the committed template on first use so external forks never edit the
# tracked placeholder file.
write_app_client_id() {
  local sp="$1"
  local local_settings="$ROOT_DIR/config/project-settings.local.yaml"
  if [[ ! -f "$local_settings" ]]; then
    cp "$ROOT_DIR/config/project-settings.yaml" "$local_settings"
    echo "=== Created config/project-settings.local.yaml from template ==="
  fi
  python3 - "$local_settings" "$BUNDLE_TARGET" "$sp" <<'PY'
import re, sys, yaml
path, target, sp = sys.argv[1], sys.argv[2], sys.argv[3]
lines = open(path, encoding="utf-8").read().split("\n")
doc = yaml.safe_load("\n".join(lines)) or {}
pname = None
for name, prof in (doc.get("profiles") or {}).items():
    if (prof or {}).get("bundle_target") == target:
        pname = name
        break
if not pname:
    sys.exit(0)
out, in_block, block_indent = [], False, None
for line in lines:
    hm = re.match(r"^(\s+)([A-Za-z0-9_.-]+):\s*$", line)
    if hm and hm.group(2) == pname:
        in_block = True
        block_indent = len(hm.group(1))
        out.append(line)
        continue
    if in_block:
        cur = re.match(r"^(\s*)\S", line)
        cur_indent = len(cur.group(1)) if cur else 999
        if line.strip() and cur_indent <= block_indent:
            in_block = False
        else:
            pk = re.match(r"^(\s+)(app_client_id|lakebase_pg_user):\s*.*$", line)
            if pk:
                out.append(f'{pk.group(1)}{pk.group(2)}: "{sp}"')
                continue
    out.append(line)
open(path, "w", encoding="utf-8").write("\n".join(out))
print(f"Wrote app_client_id/lakebase_pg_user for profile {pname!r}.")
PY
}

# Write a genie space id for the deploy target's profile into
# project-settings.local.yaml (same line-based, profile-scoped rewrite as
# write_app_client_id). Used after auto-creating a Genie space on a fresh env.
# $1 = value; $2 = key to write (default genie_space_id; the per-domain spaces
# use genie_space_<domain>, and the Knowledge Assistant step writes
# knowledge_assistant_endpoint / knowledge_assistant_id through the same
# helper — the rewrite is key-agnostic, only the name is historical).
write_genie_space_id() {
  local space_id="$1"
  local key="${2:-genie_space_id}"
  local local_settings="$ROOT_DIR/config/project-settings.local.yaml"
  if [[ ! -f "$local_settings" ]]; then
    cp "$ROOT_DIR/config/project-settings.yaml" "$local_settings"
    echo "=== Created config/project-settings.local.yaml from template ==="
  fi
  python3 - "$local_settings" "$BUNDLE_TARGET" "$space_id" "$key" <<'PY'
import re, sys, yaml
path, target, space_id, key = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
lines = open(path, encoding="utf-8").read().split("\n")
doc = yaml.safe_load("\n".join(lines)) or {}
pname = None
for name, prof in (doc.get("profiles") or {}).items():
    if (prof or {}).get("bundle_target") == target:
        pname = name
        break
if not pname:
    sys.exit(0)
out, in_block, block_indent, wrote = [], False, None, False
# Where the profile block ends, and how its keys are indented — needed to APPEND
# a key the file predates. A local settings file copied from an older template
# has no genie_space_<domain> line to rewrite, and silently skipping it would
# leave the space provisioned but never handed to the app.
last_block_idx, item_indent = None, None
for line in lines:
    hm = re.match(r"^(\s+)([A-Za-z0-9_.-]+):\s*$", line)
    if hm and hm.group(2) == pname:
        in_block = True
        block_indent = len(hm.group(1))
        out.append(line)
        last_block_idx = len(out) - 1
        continue
    if in_block:
        cur = re.match(r"^(\s*)\S", line)
        cur_indent = len(cur.group(1)) if cur else 999
        if line.strip() and cur_indent <= block_indent:
            in_block = False
        else:
            if line.strip() and item_indent is None:
                item_indent = cur_indent
            # Match the exact key only. Several keys here share a prefix
            # (genie_space_id, genie_space_asset, knowledge_assistant_id …),
            # so a loose match would write the value into a sibling field.
            pk = re.match(rf"^(\s+)({re.escape(key)}):\s*.*$", line)
            if pk:
                out.append(f'{pk.group(1)}{pk.group(2)}: "{space_id}"')
                wrote = True
                last_block_idx = len(out) - 1
                continue
            out.append(line)
            if line.strip():
                last_block_idx = len(out) - 1
            continue
    out.append(line)
if not wrote and last_block_idx is not None:
    indent = " " * (item_indent if item_indent is not None else (block_indent or 0) + 2)
    out.insert(last_block_idx + 1, f'{indent}{key}: "{space_id}"')
    wrote = True
    appended = True
else:
    appended = False
open(path, "w", encoding="utf-8").write("\n".join(out))
print(f"{'Appended' if appended else 'Wrote'} {key} for profile {pname!r}." if wrote
      else f"WARN: no {key} key found in profile {pname!r} to update.")
PY
}

# Swap app.<env>.(local.)yaml → app.yaml and substitute ${DEPLOYER_EMAIL}.
# The bundle's ``apps:`` resource has ``source_code_path: ./``, so bundle
# deploy uploads ./app.yaml to the workspace and the app spec is read from
# THERE — the swap MUST happen before each bundle deploy.
do_app_yaml_swap() {
  if [[ "$APP_SOURCE_YAML" != "app.yaml" ]]; then
    cp "$ROOT_DIR/$APP_SOURCE_YAML" "$ROOT_DIR/app.yaml"
    echo "=== Swapped $APP_SOURCE_YAML → app.yaml (will restore on exit) ==="
  fi
  if grep -q '\${DEPLOYER_EMAIL}' "$ROOT_DIR/app.yaml"; then
    echo "=== Substituting \${DEPLOYER_EMAIL} → $DEPLOYER_EMAIL in app.yaml ==="
    python3 - "$ROOT_DIR/app.yaml" "$DEPLOYER_EMAIL" <<'PY'
import sys
path, email = sys.argv[1], sys.argv[2]
with open(path, "r", encoding="utf-8") as f:
    text = f.read()
with open(path, "w", encoding="utf-8") as f:
    f.write(text.replace("${DEPLOYER_EMAIL}", email))
PY
  fi
}

run_bundle_deploy() {
  echo "=== databricks bundle deploy --target $BUNDLE_TARGET --profile $PROFILE ==="
  local deploy_args=(bundle deploy --target "$BUNDLE_TARGET" --profile "$PROFILE")
  if ((${#EXTRA[@]} > 0)); then
    deploy_args+=("${EXTRA[@]}")
  fi
  # Auto-recover from a STALE deployment lock. If a previous ``bundle deploy``
  # was interrupted (Ctrl-C, killed shell, dropped connection) the lock it took
  # is left behind, and the next run fails with "Failed to acquire deployment
  # lock". For a single-operator accelerator deploy that's virtually always a
  # stale lock (not a genuine concurrent deploy), so retry ONCE with
  # --force-lock. We skip the auto-retry if the caller already passed
  # --force-lock (then the first attempt already had it and any lock error is
  # real). Output is tee'd so the operator still sees it live.
  local logf rc=0
  logf="$(mktemp -t bundle_deploy.XXXXXX)"
  if databricks "${deploy_args[@]}" 2>&1 | tee "$logf"; then rc=0; else rc=${PIPESTATUS[0]}; fi
  if (( rc != 0 )) && grep -q "Failed to acquire deployment lock" "$logf" \
     && ! printf '%s\n' "${EXTRA[@]:-}" | grep -qx -- "--force-lock"; then
    echo "WARN: stale deployment lock detected — a previous deploy was likely interrupted." >&2
    echo "=== Retrying bundle deploy once with --force-lock ===" >&2
    rm -f "$logf"
    databricks "${deploy_args[@]}" --force-lock
    return $?
  fi
  # An OAuth session can go invalid mid-deploy — it is shared, and a refresh
  # elsewhere rotates it. Terraform then prints one stanza per resource it could
  # not read, which buries both the cause and the fact that re-running is the
  # whole fix: every step of this deploy is idempotent, so a second run skips the
  # setup job and reuses the spaces, the index and the assistant.
  if (( rc != 0 )) && _auth_failure_in "$logf"; then
    echo "ERROR: bundle deploy lost its credential part-way through." >&2
    _explain_auth_failure
    echo "Then re-run the same command. The deploy resumes: it detects what already" >&2
    echo "exists and rebuilds nothing — but until it finishes, the app is running one" >&2
    echo "pass behind, without the env this deploy was about to write." >&2
  fi
  rm -f "$logf"
  return $rc
}

# ── 2. Optional pre-deploy MLflow GenAI eval gate ───────────────────────
# Runs the seed eval slice against the current agent code and blocks the
# deploy if any threshold is breached (see evaluations/run_eval.py). Opt-in:
# set RUN_EVAL_GATE=1 to enable.
if [[ "${RUN_EVAL_GATE:-0}" == "1" ]]; then
  echo "=== MLflow GenAI eval gate (RUN_EVAL_GATE=1) ==="
  DATABRICKS_PROFILE="$PROFILE" bash "$SCRIPT_DIR/run_eval_gate.sh" || {
    echo "ERROR: eval gate failed — aborting deploy. Fix the regression or" >&2
    echo "       re-run with RUN_EVAL_GATE=0 to bypass (record the reason)." >&2
    exit 1
  }
fi

# ── 3. First bundle deploy: creates jobs + dashboard + app (+ SP) ────────
# Uploads the best-known app.yaml first. On a first external deploy where
# app_client_id is still SET_ME, sync_project_settings emits NO SP job grants
# (see _app_sp_permissions_block), so this deploy creates the app + SP cleanly;
# the second deploy below applies the grants once we've captured the SP.
APP_YAML_BACKUP="$(mktemp -t app_yaml_bak.XXXXXX)"
cp "$ROOT_DIR/app.yaml" "$APP_YAML_BACKUP"
do_app_yaml_swap
run_bundle_deploy

if [[ "$SKIP_APP" -eq 1 ]]; then
  echo "=== --skip-app set — first bundle deploy complete, app deploy skipped ==="
  exit 0
fi

# ── 4. Capture the app SP (chicken-and-egg fix) ─────────────────────────
NEEDS_REDEPLOY=0
CURRENT_APP_SP=$(_profile_field app_client_id)
# Always ASK the workspace who the app SP is, rather than trusting a stored id.
# `bundle destroy` deletes the app and its service principal but leaves
# app_client_id populated in project-settings.local.yaml, so a destroy/redeploy
# cycle would otherwise graft every grant (UC, Lakebase role, Genie CAN_RUN,
# job permissions) onto a principal that no longer exists — and still exit 0,
# with the new app holding no access at all. Comparing makes that self-healing.
echo "=== Resolving app SP from 'databricks apps get $APP_NAME' ==="
CAPTURED_SP=$(databricks apps get "$APP_NAME" --profile "$PROFILE" --output json 2>/dev/null \
  | python3 -c 'import json,sys
try:
    print(json.load(sys.stdin).get("service_principal_client_id") or "")
except Exception:
    print("")' 2>/dev/null || echo "")
if [[ -n "$CAPTURED_SP" && "$CAPTURED_SP" != "$CURRENT_APP_SP" ]]; then
    if [[ -n "$CURRENT_APP_SP" && "$CURRENT_APP_SP" != "SET_ME" ]]; then
      echo "=== App SP changed ($CURRENT_APP_SP -> $CAPTURED_SP) — the app was recreated ==="
    else
      echo "=== Captured app SP: $CAPTURED_SP — writing to project-settings.local.yaml ==="
    fi
    write_app_client_id "$CAPTURED_SP"
    APP_SP_ID="$CAPTURED_SP"
    NEEDS_REDEPLOY=1
    # Ensure refresh_app_yaml_ids + subsequent swaps target a gitignored
    # .local.yaml so the committed app.<env>.yaml template stays clean on forks.
    if [[ "$APP_SOURCE_YAML" != *".local.yaml" ]]; then
      APP_LOCAL_YAML="${APP_SOURCE_YAML%.yaml}.local.yaml"
      if [[ ! -f "$ROOT_DIR/$APP_LOCAL_YAML" ]]; then
        cp "$ROOT_DIR/$APP_SOURCE_YAML" "$ROOT_DIR/$APP_LOCAL_YAML"
        echo "=== Created $APP_LOCAL_YAML from template ==="
      fi
      APP_SOURCE_YAML="$APP_LOCAL_YAML"
    fi
    # Regenerate bundle-targets so the SP's CAN_MANAGE_RUN grants are emitted,
    # and re-resolve PROJECT_SETTINGS_YAML to the freshly-created local file.
    PROJECT_SETTINGS_YAML="config/project-settings.local.yaml"
    echo "=== Re-sync bundle-targets with captured app SP ==="
    python3 scripts/sync_project_settings.py --deployer-email "$DEPLOYER_EMAIL" --target "$BUNDLE_TARGET"
elif [[ -n "$CAPTURED_SP" ]]; then
  APP_SP_ID="$CAPTURED_SP"          # unchanged — stored id is still correct
elif [[ -n "$CURRENT_APP_SP" && "$CURRENT_APP_SP" != "SET_ME" ]]; then
  # The app exists per config but `apps get` told us nothing (CLI hiccup, or the
  # app is mid-create). Fall back to the stored id rather than dropping grants.
  APP_SP_ID="$CURRENT_APP_SP"
  echo "WARN: could not read app SP from 'apps get' — falling back to stored $CURRENT_APP_SP." >&2
else
  echo "WARN: could not capture app SP from apps get — SP job grants + Lakebase role skipped." >&2
fi

# ── 4a. Register App + Producer SPs as Lakebase roles (idempotent) ──────
if [[ -n "$LAKEBASE_PG_HOST" && -n "$DEPLOY_USER" ]]; then
  register_sp "${APP_SP_ID:-}" "App SP"
  register_sp "$PRODUCER_SP_ID" "Producer SP"
else
  echo "WARN: could not parse PGHOST or detect deploy user — skipping Lakebase role registration."
fi

# ── 4b. Refresh OPS_* IDs AFTER resources exist ─────────────────────────
# CRITICAL ordering: the jobs + Data Quality dashboard only exist after the
# bundle deploy above, so refreshing here (not before) is what fills real IDs
# into app.<env>.local.yaml. Previously this ran before the first deploy, so a
# fresh workspace shipped OPS_DATA_QUALITY_DASHBOARD_ID='SET_ME' and the
# Quality tab 404'd. If any ID changes, a second bundle deploy re-uploads the
# corrected app.yaml.
echo "=== refresh_app_yaml_ids.py $ENV (after bundle deploy) ==="
REFRESH_OUT=$(python3 scripts/refresh_app_yaml_ids.py "$ENV" --profile "$PROFILE" 2>&1) || true
echo "$REFRESH_OUT"
if echo "$REFRESH_OUT" | grep -qE '^Patched [1-9]'; then
  NEEDS_REDEPLOY=1
fi

# ── 4b-ii. Unity Catalog-backed trace storage ───────────────────────────
# A classic experiment uploads span payloads to <region>.storage.cloud.databricks.com,
# which the Apps runtime gets "Connection refused" from: trace metadata lands,
# every span is dropped, and since export is async and non-fatal nothing says
# so — the MLflow UI just shows traces with no spans. UC-backed traces write to
# Delta through the workspace host the app already reaches.
#
# Ordering is load-bearing twice over. It runs AFTER 4/4b so the app SP exists
# to be granted, and BEFORE the app deploy in step 5 because a UC destination
# can only be linked to an experiment that holds no traces — once the app has
# written one, that experiment can never be converted. It also writes
# MLFLOW_EXPERIMENT_ID into app.<env>.local.yaml, so it has to land before the
# yaml swap and the redeploy below pick the file up.
#
# Non-fatal: a failure costs observability, not a working app.
#
# Who may READ the traces, on top of the app SP that writes them. Unset means
# the script's default (every workspace user); set trace_viewers in
# project-settings to narrow it, or to 'none' to grant nobody.
TRACE_VIEWERS=$(_profile_field trace_viewers)
echo "=== provision_trace_storage.py $ENV ==="
# Two env vars this call cannot do without, and the second was hidden behind
# the first.
#
# DATABRICKS_CONFIG_PROFILE, because the script builds a bare WorkspaceClient()
# and takes no --profile. Without it the SDK runs its own default chain and
# ignores the profile the rest of this deploy is using — so a non-default
# DATABRICKS_PROFILE would provision trace storage, and grant read on it, in
# whatever workspace the default profile happens to point at. It fails loudly
# when that profile has no valid token, which is the good case; the bad case is
# a valid token for the wrong workspace.
#
# MLFLOW_TRACKING_URI, because mlflow defaults to a local ./mlruns store when
# it is unset. The script then creates its experiment on disk, and the UC link
# fails with "trace_location is only supported with a Databricks tracking URI"
# — after leaving an mlruns directory in the deploy tree. This never surfaced
# while the profile was missing, because auth failed first.
TRACE_OUT=$(DATABRICKS_CONFIG_PROFILE="$PROFILE" MLFLOW_TRACKING_URI=databricks \
  "${PROJECT_PY[@]}" scripts/provision_trace_storage.py "$ENV" \
  --settings "$ROOT_DIR/$PROJECT_SETTINGS_YAML" \
  ${APP_SP_ID:+--app-sp "$APP_SP_ID"} \
  ${TRACE_VIEWERS:+--trace-viewers "$TRACE_VIEWERS"} 2>&1) || true
echo "$TRACE_OUT"
if echo "$TRACE_OUT" | grep -q 'MLFLOW_EXPERIMENT_ID updated'; then
  NEEDS_REDEPLOY=1
fi
# Held for the closing summary. Failing here is non-fatal by design, but the
# consequence is invisible in the product: MLFLOW_EXPERIMENT_ID keeps the
# template's `valueFrom: experiment`, the app writes to the bundle's classic
# experiment, and its spans upload to a storage host the Apps runtime cannot
# reach. What you get is an experiment that looks alive, holds no spans, and
# offers to "Enable UC trace storage" — with a green "Deploy complete" above
# it. A warning 500 lines up is a warning nobody reads.
TRACE_WARNING=""
if ! echo "$TRACE_OUT" | grep -qE 'MLFLOW_EXPERIMENT_ID (updated|already correct)'; then
  TRACE_WARNING=$(echo "$TRACE_OUT" | grep -E '^(ERROR|.*SystemExit|profile has no)' | head -2)
  TRACE_WARNING="${TRACE_WARNING:-see the provision_trace_storage.py output above}"
fi

# ── 4c. Second bundle deploy (only if SP captured or IDs changed) ───────
if [[ "$NEEDS_REDEPLOY" -eq 1 ]]; then
  echo "=== Re-deploy: app env / SP grants changed on first pass ==="
  do_app_yaml_swap
  run_bundle_deploy
fi

# ── 5. App deploy ────────────────────────────────────────────────────────
# Two first-deploy footguns handled here:
#   (a) A freshly bundle-created app is STOPPED; ``apps deploy`` requires the
#       compute to be RUNNING, so start it (and wait) if needed.
#   (b) The first-ever ``apps deploy`` needs an explicit ``--source-code-path``
#       (there is no prior active deployment to reuse). The bundle already
#       uploaded the repo to its workspace files path, so we pass that. It is
#       also valid on subsequent deploys, so we always pass it when known.
APP_STATE=$(databricks apps get "$APP_NAME" --profile "$PROFILE" --output json 2>/dev/null \
  | python3 -c 'import json,sys
try: print((json.load(sys.stdin).get("compute_status") or {}).get("state") or "")
except Exception: print("")' 2>/dev/null || echo "")
if [[ "$APP_STATE" != "ACTIVE" && "$APP_STATE" != "RUNNING" ]]; then
  echo "=== app compute is '${APP_STATE:-unknown}' — starting $APP_NAME (waits for RUNNING) ==="
  databricks apps start "$APP_NAME" --profile "$PROFILE" || {
    echo "ERROR: apps start failed for $APP_NAME." >&2
    exit 1
  }
fi

APP_SRC_PATH=$(databricks bundle summary --target "$BUNDLE_TARGET" --profile "$PROFILE" --output json 2>/dev/null \
  | python3 -c 'import json,sys
try: print((json.load(sys.stdin).get("workspace") or {}).get("file_path") or "")
except Exception: print("")' 2>/dev/null || echo "")

echo "=== databricks apps deploy $APP_NAME (source=${APP_SRC_PATH:-<default>}) --profile $PROFILE ==="
APP_DEPLOY_ARGS=(apps deploy "$APP_NAME" --profile "$PROFILE")
if [[ -n "$APP_SRC_PATH" ]]; then
  APP_DEPLOY_ARGS+=(--source-code-path "$APP_SRC_PATH")
fi

# The Apps build sandbox is occasionally flaky on the FIRST attempt, and the
# failure modes all clear the same way — with a stop → start (fresh container
# filesystem) → redeploy:
#   * "App process did not start within 10 minutes" — a slow build ran into the
#     health-check window.
#   * "Error installing packages" / npm "ENOTEMPTY" — a corrupted node_modules
#     left in the build cache by a previous failed build.
#   * "active deployment in progress" — a prior attempt is still settling.
# So instead of forcing the operator to diagnose and hand-run the recovery (as
# was needed during bring-up), we retry ONCE automatically after a stop/start.
app_deploy_attempt() { databricks "${APP_DEPLOY_ARGS[@]}"; }

if ! app_deploy_attempt; then
  echo "WARN: apps deploy failed. This is usually a transient build-sandbox issue" >&2
  echo "      (npm/registry blip, OOM, 10-min start timeout, or a stale build cache)." >&2
  echo "=== Recovery: stop → start → redeploy (forces a fresh build container) ===" >&2
  databricks apps stop "$APP_NAME" --profile "$PROFILE" || true
  databricks apps start "$APP_NAME" --profile "$PROFILE" || {
    echo "ERROR: apps start failed during recovery — aborting." >&2
    exit 1
  }
  if ! app_deploy_attempt; then
    echo "ERROR: apps deploy failed again after stop/start recovery." >&2
    echo "       Inspect the build + runtime logs:" >&2
    echo "         databricks apps logs $APP_NAME --tail-lines 200 --profile $PROFILE" >&2
    echo "       Restore app.yaml from $APP_YAML_BACKUP if needed." >&2
    exit 1
  fi
fi

# ── 6. Data Quality dashboard grants + publish ──────────────────────────
# The Lakeview dashboard ships in the bundle but its CAN_READ grant for the
# app SP, the workspace-folder traversal grants, and the
# embed_credentials=true publish are not expressible declaratively in DAB
# yet. Without this step the Quality tab iframe loads but every widget
# renders "Unable to render visualization." (See SKIP message in
# scripts/grant_app_jobs_view.sh for the underlying API calls.)
DASHBOARD_ID=$(awk '/^  - name: OPS_DATA_QUALITY_DASHBOARD_ID/{getline; gsub(/[ '"'"'"]/,""); sub(/^value:/,""); print; exit}' "$ROOT_DIR/$APP_SOURCE_YAML")
# Resolve the bundle target's SQL warehouse from project-settings.yaml. The
# app's DATABRICKS_WAREHOUSE_ID env is injected via valueFrom: sql-warehouse,
# so it isn't a literal in app.<env>.yaml — go to the source instead.
WAREHOUSE_ID=$(python3 -c "
import yaml
with open('$ROOT_DIR/$PROJECT_SETTINGS_YAML') as f:
    d = yaml.safe_load(f)
for prof in d.get('profiles', {}).values():
    if prof.get('bundle_target') == '$BUNDLE_TARGET':
        print((prof.get('sql_warehouse_id') or '').strip())
        break
" 2>/dev/null || echo "")

# `SET_ME` is a non-empty string, so the old -n test passed and this ran the
# grant/publish calls against a literal id of "SET_ME": the PATCH failed, the
# real dashboard was never published, and the Data Quality tab embedded a 404.
if [[ "$DASHBOARD_ID" == "SET_ME" ]]; then
  DASHBOARD_ID=""
  echo "WARN: OPS_DATA_QUALITY_DASHBOARD_ID is still SET_ME — refresh_app_yaml_ids.py" >&2
  echo "      did not fill it. The Data Quality tab will not embed." >&2
fi

if [[ -n "$DASHBOARD_ID" && -n "$WAREHOUSE_ID" && -n "$DEPLOY_USER" ]]; then
  echo "=== grant_app_jobs_view.sh — Data Quality dashboard ==="
  # The dashboard is uploaded under the BUNDLE TARGET name (not the CLI
  # profile name), so override the script's default parent path to match.
  PROFILE="$PROFILE" \
  APP_NAME="$APP_NAME" \
  USER_EMAIL="$DEPLOY_USER" \
  DASHBOARD_ID="$DASHBOARD_ID" \
  WAREHOUSE_ID="$WAREHOUSE_ID" \
  DASHBOARD_PARENT_PATH="/Users/$DEPLOY_USER/.bundle/production-line-copilot/$BUNDLE_TARGET/resources" \
  bash "$SCRIPT_DIR/grant_app_jobs_view.sh" || {
    echo "WARN: grant_app_jobs_view.sh failed — Data Quality tab may show empty widgets." >&2
    echo "      Re-run manually with the env block printed above." >&2
  }
else
  echo "WARN: skipping the dashboard folder-chain grants (DASHBOARD_ID='$DASHBOARD_ID', WAREHOUSE_ID='$WAREHOUSE_ID')." >&2
fi

# The step above grants the workspace folder chain the app SP needs to traverse.
# This one owns what the tab actually depends on — published, with embedded
# credentials, readable by the app AND by the humans opening it — and reads the
# state back instead of assuming.
#
# It deliberately sits OUTSIDE the block above. That block needs an id from
# app.yaml, and the case that produced a 404 in a colleague's workspace was
# precisely the one where the id was still SET_ME, so every dashboard step was
# skipped over a warning. This resolves the dashboard by display name on its
# own, so a missing id is no longer a reason to do nothing.
#
# It is also the only step here that fails the deploy. An unpublished dashboard
# renders as a bare Databricks "The resource is not available" page with nothing
# anywhere to explain it, and a warning this far into a long deploy log is a
# warning nobody reads.
if [[ -n "$WAREHOUSE_ID" ]]; then
  echo
  echo "=== Data Quality dashboard: publish + grants ==="
  DASHBOARD_VIEWER_ARGS=()
  [[ -n "$DEPLOY_USER" ]] && DASHBOARD_VIEWER_ARGS+=(--viewer "$DEPLOY_USER")
  # Comma-separated groups who should be able to open the tab, e.g.
  # DASHBOARD_VIEWER_GROUPS="data-team,plant-managers" ./scripts/deploy.sh prod
  if [[ -n "${DASHBOARD_VIEWER_GROUPS:-}" ]]; then
    IFS=',' read -ra _dash_groups <<< "$DASHBOARD_VIEWER_GROUPS"
    for _g in "${_dash_groups[@]}"; do
      [[ -n "${_g// /}" ]] && DASHBOARD_VIEWER_ARGS+=(--viewer-group "${_g// /}")
    done
  fi
  if ! DATABRICKS_CONFIG_PROFILE="$PROFILE" \
      "${PROJECT_PY[@]}" scripts/ensure_dashboard_embed.py \
        --profile "$PROFILE" \
        --warehouse-id "$WAREHOUSE_ID" \
        --dashboard-id "$DASHBOARD_ID" \
        --app-name "$APP_NAME" \
        --app-sp "${APP_SP_ID:-}" \
        "${DASHBOARD_VIEWER_ARGS[@]}"; then
    echo >&2
    echo "ERROR: the Data Quality dashboard is not embeddable." >&2
    echo "       The tab will show Databricks' 'The resource is not available' page." >&2
    echo "       Fix the cause above and re-run, or set SKIP_DASHBOARD_EMBED=1 to" >&2
    echo "       deploy without it." >&2
    [[ "${SKIP_DASHBOARD_EMBED:-0}" == "1" ]] || exit 1
  fi
else
  echo "WARN: no SQL warehouse resolved — skipping the dashboard publish step." >&2
  echo "      The Data Quality tab will not embed until you set sql_warehouse_id." >&2
fi

# ── 6b. Auto-detect a fresh environment ─────────────────────────────────
# Operators shouldn't have to remember RUN_SETUP=1 on a first deploy. If
# RUN_SETUP is unset, probe UC for the schema's marker bronze table: missing →
# fresh env → auto-provision (schema + reference data + Genie); present →
# code-only redeploy, skip. An explicit RUN_SETUP=0/1 always wins.
if [[ -z "${RUN_SETUP:-}" ]]; then
  UC_CATALOG_PROBE=$(_profile_field uc_catalog)
  UC_SCHEMA_PROBE=$(_profile_field uc_schema)
  MARKER_TABLE="$UC_CATALOG_PROBE.$UC_SCHEMA_PROBE.machine_state_bronze"
  if [[ -n "$UC_CATALOG_PROBE" && "$UC_CATALOG_PROBE" != "SET_ME" ]] \
     && databricks api get "/api/2.1/unity-catalog/tables/$MARKER_TABLE" --profile "$PROFILE" >/dev/null 2>&1; then
    RUN_SETUP=0
    echo "=== Fresh-env check: $MARKER_TABLE exists → RUN_SETUP=0 (code-only redeploy) ==="
  else
    RUN_SETUP=1
    echo "=== Fresh-env check: $MARKER_TABLE missing → RUN_SETUP=1 (auto-provision schema + data + Genie) ==="
  fi
fi

# ── 7. One-time data provisioning + Genie space ──────────────────────────
# A code-only redeploy to an existing env does NOT need this. But a FRESH env
# has an empty UC schema, so the app/agents have no data until the setup job
# runs (creates the schema + bronze/live/reference tables, bootstraps ref data,
# and wires the Continuous Synced Tables to Lakebase). This step also
# auto-creates the curated Genie space (step 7a) once the tables exist — so a
# truly fresh workspace (external fork) gets one without any manual UI step.
# Auto-enabled by the fresh-env check above; force with RUN_SETUP=1, or opt out
# with RUN_SETUP=0. ``bundle run`` triggers run-now and blocks until the job
# finishes. The continuous producer + quality-sync jobs deploy UNPAUSED, so
# they start on their own once the schema exists — no manual start needed.
if [[ "${RUN_SETUP:-0}" == "1" ]]; then
  echo "=== RUN_SETUP=1: running one-time setup job (schema + reference data + synced tables) ==="
  databricks bundle run solution_accelerator_setup --target "$BUNDLE_TARGET" --profile "$PROFILE" || {
    echo "ERROR: setup job failed — inspect the run in the Databricks Jobs UI." >&2
    exit 1
  }
  echo "=== Setup job complete — UC schema provisioned for $BUNDLE_TARGET ==="
fi

# ── 7a. Genie space provisioning ──────────────────────────────────────────
# The curated space references catalog.schema.<table>, so this has to run
# after the setup job above — but NOT only when that job runs. It used to sit
# inside the RUN_SETUP branch, and RUN_SETUP is auto-detected by probing for
# machine_state_bronze. Once the schema exists every later deploy resolves to
# RUN_SETUP=0, so a Genie space deleted after the first deploy could never be
# restored by redeploying: dev lost its space to a teardown, project-settings
# correctly reset the id to SET_ME, and no subsequent deploy so much as looked
# at it. Step 7b (the governed MVM space) was already unconditional; this is
# the same shape.
#
# Fresh or repaired (genie_space_id=SET_ME, or an id that no longer resolves):
# POST-create via the public /api/2.0/genie/spaces API, capture the new id,
# write it back, grant the app SP CAN_RUN, then re-wire GENIE_SPACE_ID into
# the app + redeploy. Existing: PATCH so the curated config stays fresh.
GENIE_ID=$(_profile_field genie_space_id)
UC_CATALOG=$(_profile_field uc_catalog)
UC_SCHEMA=$(_profile_field uc_schema)
# A configured id is not the same as a live space. A trashed space still fails
# every genie_query while the app reports itself configured, so treat an id
# that does not resolve exactly like SET_ME and rebuild it.
if [[ -n "$GENIE_ID" && "$GENIE_ID" != "SET_ME" ]] \
   && ! databricks api get "/api/2.0/genie/spaces/$GENIE_ID" --profile "$PROFILE" >/dev/null 2>&1; then
  echo "WARN: Genie space $GENIE_ID no longer resolves (deleted or trashed) — recreating it." >&2
  GENIE_ID=""
fi
if [[ -z "$WAREHOUSE_ID" || -z "$UC_CATALOG" || "$UC_CATALOG" == "SET_ME" ]]; then
  echo "WARN: skipping Genie provisioning (warehouse='$WAREHOUSE_ID', catalog='$UC_CATALOG')." >&2
else
  # The curated SQLs default :shift_id, and a default naming a shift this
  # environment's producer never writes returns zero rows from a query that
  # ran cleanly — dev's space defaulted to shift_1 and answered every
  # shift-scoped question with "0 machines".
  GENIE_SHIFT=$(_profile_field sim_shift_id)
  # Title has to differ per env. Many workspaces host dev and prod side by
  # side, and Genie spaces are workspace files: a second space created — or
  # refreshed into — the same folder under the same name fails with "Node
  # named '<title>.geniespace.json' already exists".
  GENIE_TITLE="ProdLine CoPilot — Production Line Analytics (streaming)"
  [[ "$ENV" != "prod" ]] && GENIE_TITLE="$GENIE_TITLE — $ENV"
  GENIE_ARGS=(--profile "$PROFILE" --warehouse-id "$WAREHOUSE_ID" --catalog "$UC_CATALOG" --schema "$UC_SCHEMA"
              --title "$GENIE_TITLE" ${GENIE_SHIFT:+--shift-id "$GENIE_SHIFT"})
  if [[ -z "$GENIE_ID" || "$GENIE_ID" == "SET_ME" ]]; then
    echo "=== Creating Genie space (fresh env) ==="
    GENIE_OUT=$(python3 scripts/genie_space_config_streaming.py "${GENIE_ARGS[@]}" \
      --parent-path "/Users/$DEPLOY_USER" 2>&1) || {
      echo "$GENIE_OUT" >&2
      echo "WARN: Genie space create failed — briefing features unavailable until fixed." >&2
      GENIE_OUT=""
    }
    [[ -n "$GENIE_OUT" ]] && echo "$GENIE_OUT"
    NEW_GENIE_ID=$(printf '%s\n' "$GENIE_OUT" | awk -F= '/^GENIE_SPACE_ID=/{print $2; exit}')
    if [[ -n "$NEW_GENIE_ID" ]]; then
      write_genie_space_id "$NEW_GENIE_ID"
      GENIE_ID="$NEW_GENIE_ID"
      if [[ -n "${APP_SP_ID:-}" ]]; then
        echo "=== Granting app SP $APP_SP_ID CAN_RUN on Genie space $GENIE_ID ==="
        GENIE_PERMS_JSON=$(printf '{"access_control_list":[{"service_principal_name":"%s","permission_level":"CAN_RUN"}]}' "$APP_SP_ID")
        databricks api patch "/api/2.0/permissions/genie/$GENIE_ID" --profile "$PROFILE" \
          --json "$GENIE_PERMS_JSON" >/dev/null \
          || echo "WARN: could not grant app SP CAN_RUN on Genie space — grant manually." >&2
      fi
      echo "=== Re-wiring GENIE_SPACE_ID into the app + redeploying ==="
      python3 scripts/render_app_yaml.py "$ENV" --settings "$ROOT_DIR/$PROJECT_SETTINGS_YAML" || true
      do_app_yaml_swap
      run_bundle_deploy
      databricks apps deploy "$APP_NAME" --profile "$PROFILE" \
        ${APP_SRC_PATH:+--source-code-path "$APP_SRC_PATH"} \
        || echo "WARN: app redeploy after Genie wiring failed — re-run deploy to pick up GENIE_SPACE_ID." >&2
    fi
  else
    echo "=== Refreshing existing Genie space $GENIE_ID (curated config) ==="
    python3 scripts/genie_space_config_streaming.py "${GENIE_ARGS[@]}" --space-id "$GENIE_ID" \
      || echo "WARN: Genie space refresh failed — space kept its previous config." >&2
  fi
fi

# ── 7b. Governed Genie spaces over the MVM model (only when silver is on) ──
# These sit over the MVM silver model (asset/production/quality/inventory), so
# they only make sense when the model is populated, i.e. mvm_silver_enabled=true.
# They do NOT rewire the streaming space: the in-app agent keeps using
# genie_space_id for live line questions and reaches the governed model through
# the per-domain tool instead.
#
# A single combined space over all twelve MVM tables used to be created here
# too, as a standalone analyst surface. It was removed because nothing ever
# called it: no env var carried its id into the app, so it cost a space and a
# grant on every deploy and answered no one. The per-domain spaces below cover
# the same tables, and genie_space_config_mvm.py argues narrow scope is the
# more accurate shape anyway.
#
# mvm_silver_enabled is a YAML boolean, so _profile_field (which calls .strip())
# can't read it — coerce to a lowercase "true"/"false" string inline instead.
MVM_SILVER=$(python3 -c "
import yaml
with open('$ROOT_DIR/$PROJECT_SETTINGS_YAML') as f:
    d = yaml.safe_load(f)
for prof in (d.get('profiles') or {}).values():
    if prof.get('bundle_target') == '$BUNDLE_TARGET':
        print(str(prof.get('mvm_silver_enabled', False)).lower()); break
" 2>/dev/null || echo "false")
MVM_CAT=$(_profile_field mvm_catalog)
# Empty mvm_catalog means "the live catalog", which is what sync_project_settings
# does with it and what the settings comment promises. Requiring it here instead
# meant the shipped default — silver on, catalog blank — created no domain spaces
# at all, and said nothing about it.
if [[ -z "$MVM_CAT" || "$MVM_CAT" == "SET_ME" ]]; then
  MVM_CAT=$(_profile_field uc_catalog)
fi
if [[ "$MVM_SILVER" == "true" && -n "$WAREHOUSE_ID" && -n "$MVM_CAT" && "$MVM_CAT" != "SET_ME" ]]; then
  # Same name-collision rule as the streaming space above.
  MVM_GENIE_ARGS=(--profile "$PROFILE" --warehouse-id "$WAREHOUSE_ID" --catalog "$MVM_CAT")
  [[ "$ENV" != "prod" ]] && MVM_GENIE_ARGS+=(--title-suffix "$ENV")

  # ── 7c. Per-domain governed Genie spaces ────────────────────────────────
  # One narrow space per schema of the governed model. These ARE wired into
  # the app: each specialist is handed the tool for its own domain
  # (agent_server/tools/governed_domain_query.py).
  # Same create-or-refresh-then-grant pattern, run per domain. A domain that
  # fails to provision leaves its id empty, and the specialist simply falls
  # back to sql_query rather than getting a tool that always errors.
  DOMAIN_SPACES_CREATED=0
  for DOMAIN in asset quality inventory production; do
    DOMAIN_FIELD="genie_space_${DOMAIN}"
    DOMAIN_ID=$(_profile_field "$DOMAIN_FIELD")
    if [[ -z "$DOMAIN_ID" || "$DOMAIN_ID" == "SET_ME" ]]; then
      echo "=== Creating governed Genie space for domain '$DOMAIN' (fresh) ==="
      DOMAIN_OUT=$(python3 scripts/genie_space_config_mvm.py "${MVM_GENIE_ARGS[@]}" \
        --domain "$DOMAIN" --parent-path "/Users/$DEPLOY_USER" 2>&1) || {
        echo "$DOMAIN_OUT" >&2
        echo "WARN: governed '$DOMAIN' Genie space create failed — that specialist falls back to sql_query." >&2
        DOMAIN_OUT=""
      }
      [[ -n "$DOMAIN_OUT" ]] && echo "$DOMAIN_OUT"
      NEW_DOMAIN_ID=$(printf '%s\n' "$DOMAIN_OUT" | awk -F= '/^GENIE_SPACE_ID=/{print $2; exit}')
      if [[ -n "$NEW_DOMAIN_ID" ]]; then
        write_genie_space_id "$NEW_DOMAIN_ID" "$DOMAIN_FIELD"
        DOMAIN_ID="$NEW_DOMAIN_ID"
        DOMAIN_SPACES_CREATED=1
      fi
    else
      echo "=== Refreshing governed '$DOMAIN' Genie space $DOMAIN_ID ==="
      python3 scripts/genie_space_config_mvm.py "${MVM_GENIE_ARGS[@]}" \
        --domain "$DOMAIN" --space-id "$DOMAIN_ID" \
        || echo "WARN: governed '$DOMAIN' Genie refresh failed — space kept its previous config." >&2
    fi
    if [[ -n "$DOMAIN_ID" && "$DOMAIN_ID" != "SET_ME" && -n "${APP_SP_ID:-}" ]]; then
      DOMAIN_PERMS_JSON=$(printf '{"access_control_list":[{"service_principal_name":"%s","permission_level":"CAN_RUN"}]}' "$APP_SP_ID")
      databricks api patch "/api/2.0/permissions/genie/$DOMAIN_ID" --profile "$PROFILE" \
        --json "$DOMAIN_PERMS_JSON" >/dev/null \
        || echo "WARN: could not grant app SP CAN_RUN on '$DOMAIN' Genie space — grant manually." >&2
    fi
  done

  # Newly created ids land in project-settings AFTER step 5 already deployed
  # the app, so the running app has no GENIE_SPACE_<DOMAIN> and would build no
  # domain tools. Re-render and redeploy, exactly as the streaming space does.
  # Refreshes alone need no redeploy — the ids did not change.
  if [[ "$DOMAIN_SPACES_CREATED" == "1" ]]; then
    echo "=== Re-rendering app config with the new domain Genie space ids ==="
    python3 scripts/render_app_yaml.py "$ENV" --settings "$ROOT_DIR/$PROJECT_SETTINGS_YAML" \
      || echo "WARN: re-render failed — domain tools stay unbuilt until the next deploy." >&2
    # render_app_yaml writes app.<env>.local.yaml, but the bundle uploads
    # app.yaml. Without the swap the redeploy ships the pre-render file and the
    # app comes up with no domain space ids — which it did, once.
    do_app_yaml_swap
    run_bundle_deploy
    databricks apps deploy "$APP_NAME" --profile "$PROFILE" \
      ${APP_SRC_PATH:+--source-code-path "$APP_SRC_PATH"} \
      || echo "WARN: app redeploy after domain Genie wiring failed — re-run deploy to pick up the space ids." >&2
  fi
  note_capability "Governed model:       4 domain Genie spaces over $MVM_CAT"
elif [[ "$MVM_SILVER" != "true" ]]; then
  # This branch did not exist, so the four governed Genie spaces — and the
  # per-domain tools the specialists get from them — were the one capability a
  # fresh deploy never mentioned in any form. It read as a product that does not
  # have them rather than one that was switched off.
  echo "Skipping the governed model (mvm_silver_enabled is false)."
  echo "    The domain specialists fall back to sql_query on the raw tables, and the"
  echo "    four governed Genie spaces (asset, quality, inventory, production) are"
  echo "    not created. To enable: set mvm_silver_enabled: true in"
  echo "    config/project-settings.local.yaml. Costs nothing beyond the queries."
  note_capability "Governed model:       off (mvm_silver_enabled is false)"
else
  echo "WARN: mvm_silver_enabled is true but catalog/warehouse are not resolvable" >&2
  echo "      (catalog='$MVM_CAT' warehouse='$WAREHOUSE_ID') — no governed Genie spaces." >&2
  note_capability "Governed model:       MISCONFIGURED — see the warning above"
fi

# ── 8. Idempotent app-SP UC grants (self-heal on every deploy) ──────────
# The one-time setup job (step 7, RUN_SETUP=1) grants the app SP USE CATALOG /
# USE SCHEMA / SELECT via init_realtime_uc.py. But a code-only redeploy
# (RUN_SETUP=0) skips that job, so if the app SP identity has CHANGED since the
# schema was created — e.g. the app was deleted+recreated (new SP), renamed, or
# its grants were revoked — the new SP has no UC access and the agent server
# crashes on its startup SQL check ([INSUFFICIENT_PERMISSIONS] USE CATALOG).
# Re-applying the grants here on EVERY deploy closes that gap. GRANT is a no-op
# when the privilege is already held, so this is cheap, safe, and idempotent.
# Skipped on a fresh env (RUN_SETUP=1 already granted, and the schema may not
# be ready before the app SP is known).
if [[ "${RUN_SETUP:-0}" != "1" && -n "${APP_SP_ID:-}" && -n "$WAREHOUSE_ID" ]]; then
  UC_CATALOG_GRANT=$(_profile_field uc_catalog)
  UC_SCHEMA_GRANT=$(_profile_field uc_schema)
  if [[ -n "$UC_CATALOG_GRANT" && "$UC_CATALOG_GRANT" != "SET_ME" && -n "$UC_SCHEMA_GRANT" ]]; then
    FQN_GRANT="$UC_CATALOG_GRANT.$UC_SCHEMA_GRANT"
    echo "=== Re-applying idempotent app-SP UC grants ($APP_SP_ID → $FQN_GRANT) ==="
    _grant_sql() {
      local rc
      rc=$(databricks api post /api/2.0/sql/statements --profile "$PROFILE" \
        --json "{\"warehouse_id\":\"$WAREHOUSE_ID\",\"statement\":\"$1\",\"wait_timeout\":\"30s\"}" 2>/dev/null \
        | python3 -c 'import json,sys
try: print((json.load(sys.stdin).get("status") or {}).get("state") or "UNKNOWN")
except Exception: print("UNKNOWN")' 2>/dev/null || echo "UNKNOWN")
      if [[ "$rc" == "SUCCEEDED" ]]; then
        echo "  ok: $1"
      else
        echo "  WARN ($rc): $1" >&2
      fi
    }
    _grant_sql "GRANT USE CATALOG ON CATALOG $UC_CATALOG_GRANT TO \`$APP_SP_ID\`"
    _grant_sql "GRANT USE SCHEMA ON SCHEMA $FQN_GRANT TO \`$APP_SP_ID\`"
    _grant_sql "GRANT SELECT ON SCHEMA $FQN_GRANT TO \`$APP_SP_ID\`"
    _grant_sql "GRANT SELECT, MODIFY ON TABLE $FQN_GRANT.scenario_commands TO \`$APP_SP_ID\`"
  else
    echo "WARN: skipping idempotent app-SP UC grants (catalog='$UC_CATALOG_GRANT', schema='$UC_SCHEMA_GRANT')." >&2
  fi
fi

# ── 8b. Idempotent app-SP Genie grant (self-heal on every deploy) ───────
# The Genie CAN_RUN grant in step 7a only runs when a NEW space is created
# (fresh env). For an EXISTING space — the common case on code-only redeploys —
# the app SP would otherwise never be added to the space ACL, so the Genie
# tool 404s (Genie returns 404, not 403, when the principal lacks
# access). PATCH is additive + idempotent, so re-applying it every deploy is
# cheap and closes that gap.
if [[ -n "${APP_SP_ID:-}" ]]; then
  GENIE_ID_GRANT=$(_profile_field genie_space_id)
  if [[ -n "$GENIE_ID_GRANT" && "$GENIE_ID_GRANT" != "SET_ME" ]]; then
    echo "=== Re-applying idempotent app-SP Genie CAN_RUN grant ($APP_SP_ID → space $GENIE_ID_GRANT) ==="
    GENIE_PERMS_JSON=$(printf '{"access_control_list":[{"service_principal_name":"%s","permission_level":"CAN_RUN"}]}' "$APP_SP_ID")
    databricks api patch "/api/2.0/permissions/genie/$GENIE_ID_GRANT" --profile "$PROFILE" \
      --json "$GENIE_PERMS_JSON" >/dev/null \
      && echo "  ok: CAN_RUN on Genie space $GENIE_ID_GRANT" \
      || echo "  WARN: could not grant app SP CAN_RUN on Genie space — grant manually." >&2
  fi
fi

# ── 8c. Publish prompts to the MLflow Prompt Registry (idempotent) ──────
# Gives every prompt change a version, an author and a commit message, so a
# shift in answer quality can be read against the wording that was live at the
# time. The app does NOT load these back — the constants in
# agent_server/prompts/ stay the runtime source of truth — so a failure here
# leaves a deployed app that behaves identically, just with a stale registry.
# Non-fatal for that reason: this must never be the thing that fails a deploy.
# The script compares each prompt against its latest registered version and
# only writes when the text differs, which is what keeps a per-deploy run from
# burying real edits under identical versions.
UC_CATALOG_PROMPTS=$(_profile_field uc_catalog)
UC_SCHEMA_PROMPTS=$(_profile_field uc_schema)
if [[ -n "$UC_CATALOG_PROMPTS" && "$UC_CATALOG_PROMPTS" != "SET_ME" && -n "$UC_SCHEMA_PROMPTS" ]]; then
  echo "=== Publishing prompts to the MLflow Prompt Registry ($UC_CATALOG_PROMPTS.$UC_SCHEMA_PROMPTS) ==="
  PROMPT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)
  DATABRICKS_CONFIG_PROFILE="$PROFILE" MLFLOW_TRACKING_URI=databricks \
    "${PROJECT_PY[@]}" scripts/register_prompts.py \
      --catalog "$UC_CATALOG_PROMPTS" --schema "$UC_SCHEMA_PROMPTS" \
      --commit-message "deploy $ENV @ $PROMPT_SHA" \
    || echo "  WARN: prompt registration failed — app is unaffected, registry may be stale." >&2
else
  echo "WARN: skipping prompt registration (catalog='$UC_CATALOG_PROMPTS', schema='$UC_SCHEMA_PROMPTS')." >&2
fi

# AI Search for retrieve_similar_cases. Controlled by `vector_search_endpoint`
# in config/project-settings(.local).yaml, which render_app_yaml turns into
# VS_ENDPOINT below; empty means the tool ranks past events by keyword overlap
# instead, which is a real fallback and not an error.
#
# Ships ON, and does NOT block: creating an endpoint takes 15-40 minutes against
# seconds for everything else here, so this starts it and moves on, and the index
# is built by the next deploy — by which time the endpoint is online and that run
# costs seconds. Until the index exists the tool keeps using keyword overlap.
# VS_WAIT=1 blocks for the endpoint instead, for the deploy that has to come out
# of it fully working. MINIMAL=1 skips the step: an endpoint bills hourly whether
# or not anything queries it.
# Idempotent either way: endpoint and index are created once, later runs re-sync
# the case library, so the wait is one-time per environment, not per deploy.
# Read the file this deploy actually ships ($APP_SOURCE_YAML), not app.yaml.
# app.yaml is the committed prod template and is kept deliberately blank here,
# so a hardcoded read of it reported "not enabled" for every environment —
# including the one whose gitignored app.<env>.local.yaml had just been
# rendered with the endpoint name a few lines above.
VS_ENDPOINT_NAME=$(python3 -c "
import yaml
try:
    env = yaml.safe_load(open('$APP_SOURCE_YAML')).get('env') or []
    print(next((e.get('value','') for e in env if e.get('name')=='VS_ENDPOINT'), ''))
except Exception:
    print('')
" 2>/dev/null || echo "")

# Read the catalog/schema from settings rather than reusing $UC_CATALOG: that
# variable is only assigned inside the RUN_SETUP=1 branch, so on a code-only
# redeploy it is empty and this step skipped while blaming VS_ENDPOINT.
VS_CATALOG=$(_profile_field uc_catalog)
VS_SCHEMA=$(_profile_field uc_schema)

if [[ "${MINIMAL:-0}" == "1" && -n "$VS_ENDPOINT_NAME" ]]; then
  echo "Skipping AI Search (MINIMAL=1) — '$VS_ENDPOINT_NAME' is configured but not built."
  echo "    retrieve_similar_cases uses keyword overlap, and logs a warning per call"
  echo "    because the app still names the endpoint. Deploy without MINIMAL to build it."
  note_capability "AI Search:            SKIPPED by MINIMAL=1 (keyword retrieval in use)"
elif [[ -n "$VS_ENDPOINT_NAME" && -n "$WAREHOUSE_ID" && -n "$VS_CATALOG" && "$VS_CATALOG" != "SET_ME" ]]; then
  echo "=== Provisioning AI Search ($VS_ENDPOINT_NAME) ==="
  # Say up front which kind of run this is. Without it the deploy looks hung:
  # `--wait` blocks with no output while the endpoint comes up, and someone
  # watching a script that has printed a line every few seconds until now will
  # reasonably assume it has died and Ctrl-C it half way through.
  # PROJECT_PY, not bare python3: the SDK lives in the project venv, and a
  # system interpreter without it would report "unknown" on every run.
  VS_EXISTS=$(DATABRICKS_CONFIG_PROFILE="$PROFILE" "${PROJECT_PY[@]}" -c "
from databricks.sdk import WorkspaceClient
try:
    names = {e.name for e in WorkspaceClient().vector_search_endpoints.list_endpoints()}
    print('yes' if '$VS_ENDPOINT_NAME' in names else 'no')
except Exception:
    print('unknown')
" 2>/dev/null || echo "unknown")
  if [[ "$VS_EXISTS" == "yes" ]]; then
    echo "    (endpoint already exists — building or re-syncing the index, a few seconds)"
  elif [[ "${VS_WAIT:-0}" == "1" ]]; then
    # Includes the unknown case on purpose: warning about a wait that does not
    # happen costs nothing, while staying quiet through a 40-minute silence
    # gets the deploy killed half way through creating an endpoint.
    echo "    NOTE: VS_WAIT=1, so this step blocks until the endpoint is online —"
    echo "          15-40 minutes if it has to be created. It prints nothing while"
    echo "          it waits. Leave it running."
  else
    echo "    NOTE: creating the endpoint takes 15-40 minutes on Databricks' side, so"
    echo "          this deploy starts it and moves on. The index is built by your"
    echo "          NEXT deploy; until then retrieve_similar_cases uses keyword"
    echo "          overlap. Pass VS_WAIT=1 to block here instead. Either way it is a"
    echo "          one-time cost per environment, not per deploy."
  fi
  # No --wait by default: see the block comment above. Exit 3 is the script's
  # "endpoint created, still coming up" — not a failure, and reporting it as one
  # is how a deploy teaches people that its warnings can be ignored.
  VS_WAIT_FLAG=""
  [[ "${VS_WAIT:-0}" == "1" ]] && VS_WAIT_FLAG="--wait"
  VS_RC=0
  DATABRICKS_CONFIG_PROFILE="$PROFILE" \
    "${PROJECT_PY[@]}" scripts/provision_vector_search.py \
      --catalog "$VS_CATALOG" --schema "$VS_SCHEMA" \
      --warehouse-id "$WAREHOUSE_ID" --endpoint "$VS_ENDPOINT_NAME" \
      ${VS_WAIT_FLAG} \
    || VS_RC=$?
  if [[ "$VS_RC" == "0" ]]; then
    note_capability "AI Search:            index provisioned on '$VS_ENDPOINT_NAME'"
  elif [[ "$VS_RC" == "3" ]]; then
    echo "  Endpoint '$VS_ENDPOINT_NAME' is still coming up — nothing is wrong."
    echo "  Re-run this deploy once it is online to build the index over the cases."
    note_capability "AI Search:            endpoint starting; re-run the deploy to build the index"
  else
    echo "  WARN: AI Search provisioning failed — retrieve_similar_cases falls back to keyword search." >&2
    note_capability "AI Search:            FAILED (keyword retrieval in use)"
  fi
elif [[ -z "$VS_ENDPOINT_NAME" ]]; then
  echo "Skipping AI Search provisioning (not enabled — keyword retrieval will be used)."
  echo "    To enable: set vector_search_endpoint in config/project-settings.local.yaml"
  echo "    (starts a 15-40 min endpoint build, then bills hourly while it is online)."
  note_capability "AI Search:            off (vector_search_endpoint not set)"
else
  note_capability "AI Search:            MISCONFIGURED — see the warning above"
  echo "WARN: VS_ENDPOINT='$VS_ENDPOINT_NAME' is set but catalog/warehouse are not" >&2
  echo "      (catalog='$VS_CATALOG' warehouse='$WAREHOUSE_ID') — index NOT provisioned," >&2
  echo "      yet the app will try the semantic path. Blank VS_ENDPOINT or fix settings." >&2
fi

# ── Knowledge Assistant over the plant documents ───────────────────────────
# Controlled by `knowledge_assistant_name`. Empty is a supported state, not an
# error: the four specialists still carry `query_plant_docs`, and it reports that
# no document assistant is configured — deliberately, so a question about an SOP
# is answered with "the documents were not read" rather than from the model's
# general knowledge of packaging lines.
#
# Ships ON, and like AI Search it does NOT block: indexing the corpus takes from
# minutes to a couple of hours, the assistant is usable the moment it is built,
# and nothing else in the deploy waits on it — the endpoint name is picked up by a
# later deploy. MINIMAL=1 skips it, because the assistant serves from an agent
# endpoint that bills while it is online.
#
# Idempotent: the script looks the assistant up by display name and only creates
# one when absent, then re-uploads the corpus and asks for a sync so edited
# documents actually reach it.
KA_NAME=$(_profile_field knowledge_assistant_name)
# The corpus volume defaults to this environment's own schema. `knowledge_docs_*`
# override it so two environments can point at ONE volume and therefore share one
# assistant: the assistant is looked up by display name and carries a knowledge
# source per *path*, so two schemas mean two sources over the same documents —
# and the API refuses the second one anyway, since source names must be unique.
# Pointing dev at prod's volume makes the second deploy find the source already
# there and skip it, which is the same trick the shared AI Search endpoint uses.
KA_CATALOG=$(_profile_field knowledge_docs_catalog)
[[ -z "$KA_CATALOG" || "$KA_CATALOG" == "SET_ME" ]] && KA_CATALOG=$(_profile_field uc_catalog)
KA_SCHEMA=$(_profile_field knowledge_docs_schema)
[[ -z "$KA_SCHEMA" || "$KA_SCHEMA" == "SET_ME" ]] && KA_SCHEMA=$(_profile_field uc_schema)
KA_VOLUME=$(_profile_field knowledge_docs_volume)
# What the RUNNING app was deployed with, read from the rendered template steps
# 3-5 deployed it from — not from settings. Settings is the wrong side of the
# comparison: provisioning writes the endpoint there, so anyone who ran
# provision_knowledge_assistant.py by hand (or whose earlier redeploy failed
# after the write-back) has settings already agreeing with the new endpoint while
# the app itself has never seen it. The re-wire below would then be skipped on
# every future deploy and the tool would stay inert with no warning.
KA_ENDPOINT_IN_APP=$(awk '/^  - name: DOCS_KA_ENDPOINT/{getline; gsub(/[ '"'"'"]/,""); sub(/^value:/,""); print; exit}' "$ROOT_DIR/$APP_SOURCE_YAML")
[[ -z "$KA_VOLUME" || "$KA_VOLUME" == "SET_ME" ]] && KA_VOLUME="plant_docs"

if [[ "${MINIMAL:-0}" == "1" && -n "$KA_NAME" && "$KA_NAME" != "SET_ME" ]]; then
  echo
  echo "Skipping the Knowledge Assistant (MINIMAL=1) — '$KA_NAME' is configured but"
  echo "    not built, so query_plant_docs reports no assistant and the specialists say"
  echo "    the documents were not read. Deploy without MINIMAL to build it."
  note_capability "Knowledge Assistant:  SKIPPED by MINIMAL=1 (documents not readable)"
elif [[ -n "$KA_NAME" && "$KA_NAME" != "SET_ME" && -n "$KA_CATALOG" && "$KA_CATALOG" != "SET_ME" ]]; then
  echo
  echo "=== Provisioning the Knowledge Assistant ($KA_NAME) ==="
  KA_ARGS=(--catalog "$KA_CATALOG" --schema "$KA_SCHEMA" --display-name "$KA_NAME"
           --volume "$KA_VOLUME")
  # The app queries the assistant as its own service principal, and a principal
  # with no grant gets a 403 that surfaces to an operator as "no answer".
  [[ -n "${APP_SP_ID:-}" ]] && KA_ARGS+=(--grant-principal "$APP_SP_ID")
  KA_FAILED=0
  KA_OUT=$(DATABRICKS_CONFIG_PROFILE="$PROFILE" \
    "${PROJECT_PY[@]}" scripts/provision_knowledge_assistant.py "${KA_ARGS[@]}" 2>&1) || {
    echo "$KA_OUT" >&2
    echo "  WARN: Knowledge Assistant provisioning failed — query_plant_docs stays" >&2
    echo "        unavailable and the specialists say the documents were not read." >&2
    KA_OUT=""
    KA_FAILED=1
  }
  [[ -n "$KA_OUT" ]] && echo "$KA_OUT"

  KA_ENDPOINT_NEW=$(printf '%s\n' "$KA_OUT" | awk -F= '/^knowledge_assistant_endpoint=/{print $2; exit}')
  KA_ID_NEW=$(printf '%s\n' "$KA_OUT" | awk -F= '/^knowledge_assistant_id=/{print $2; exit}')
  KA_STATE=$(printf '%s\n' "$KA_OUT" | awk -F= '/^knowledge_assistant_state=/{print $2; exit}')
  [[ -n "$KA_ID_NEW" ]] && write_genie_space_id "$KA_ID_NEW" knowledge_assistant_id
  if [[ -n "$KA_ENDPOINT_NEW" ]]; then
    write_genie_space_id "$KA_ENDPOINT_NEW" knowledge_assistant_endpoint
    # Steps 3-5 deployed the app before this ran, so on the deploy that first
    # provisions the assistant the running app has no DOCS_KA_ENDPOINT and the
    # tool would report itself unconfigured until someone deployed again. Only
    # re-wire when the app is not already serving this endpoint; a re-provision of
    # the same assistant needs no redeploy.
    KA_WIRED=1
    if [[ "$KA_ENDPOINT_NEW" != "$KA_ENDPOINT_IN_APP" ]]; then
      echo "=== Re-wiring DOCS_KA_ENDPOINT into the app + redeploying ==="
      python3 scripts/render_app_yaml.py "$ENV" --settings "$ROOT_DIR/$PROJECT_SETTINGS_YAML" \
        || echo "  WARN: re-render failed — the tool stays inert until the next deploy." >&2
      do_app_yaml_swap
      if ! run_bundle_deploy; then KA_WIRED=0; fi
      if (( KA_WIRED == 1 )); then
        databricks apps deploy "$APP_NAME" --profile "$PROFILE" \
          ${APP_SRC_PATH:+--source-code-path "$APP_SRC_PATH"} \
          || { KA_WIRED=0; echo "  WARN: app redeploy after wiring the assistant failed." >&2; }
      fi
    fi
    # Reporting "serving" when the pass that tells the app about it failed is the
    # one thing this summary must not do. That pass is last, so it is the one an
    # expiring credential kills, and the result is invisible from the outside: an
    # agent endpoint that is up and billing, an app that never queries it, and a
    # green deploy above. Re-read the rendered template rather than trusting the
    # write — this reports what the app was actually given.
    if (( KA_WIRED == 1 )) && grep -q "$KA_ENDPOINT_NEW" "$ROOT_DIR/$APP_SOURCE_YAML" 2>/dev/null; then
      # "Serving" was measured to be too generous: on a first deploy the endpoint
      # answers within seconds while the corpus indexes for the best part of an
      # hour, and one such run reported serving over an assistant whose ingest had
      # failed outright. Say which of the two this is, since only the second one
      # means a question about the documents gets a documented answer.
      if [[ "$KA_STATE" == "indexing" ]]; then
        note_capability "Knowledge Assistant:  built on $KA_ENDPOINT_NEW, still indexing the documents — questions come back ungrounded until it finishes"
      else
        note_capability "Knowledge Assistant:  answering on $KA_ENDPOINT_NEW"
      fi
    else
      note_capability "Knowledge Assistant:  BUILT on $KA_ENDPOINT_NEW but the app was not given it — re-run the deploy (it bills meanwhile)"
    fi
  elif (( KA_FAILED == 1 )); then
    # Distinct from "no endpoint yet": waiting fixes the second and not the first.
    note_capability "Knowledge Assistant:  provisioning FAILED — see the warning above"
  else
    echo "    (no endpoint yet — the assistant is still being built. Re-run the"
    echo "     deploy once it reports one; nothing else here depends on it.)"
    note_capability "Knowledge Assistant:  building; re-run the deploy to wire the endpoint"
  fi
elif [[ -z "$KA_NAME" || "$KA_NAME" == "SET_ME" ]]; then
  echo
  echo "Skipping the Knowledge Assistant (not enabled — the specialists answer from"
  echo "    telemetry only and say so when asked about documents)."
  echo "    To enable: set knowledge_assistant_name in config/project-settings.local.yaml"
  echo "    (uploads data/plant_docs/ to a volume; the agent endpoint bills while online)."
  note_capability "Knowledge Assistant:  off (knowledge_assistant_name not set)"
else
  echo "WARN: knowledge_assistant_name='$KA_NAME' is set but uc_catalog is not" >&2
  echo "      (catalog='$KA_CATALOG') — the assistant was NOT provisioned." >&2
  note_capability "Knowledge Assistant:  MISCONFIGURED — see the warning above"
fi

# ── Restart the continuous streaming jobs onto the code just deployed ──────
# bundle deploy replaces the files these jobs run from, but a run already in
# flight keeps executing what it started with — and these never terminate on
# their own. Without this, every deploy after the first leaves the producer and
# quality-sync running yesterday's build, which presents as a fix that changed
# nothing. Cancelling is the restart; `continuous` brings them straight back.
echo
echo "=== Restarting continuous streaming jobs onto the new code ==="
JOB_SUFFIX=""
[[ "$ENV" == "dev" ]] && JOB_SUFFIX="-dev"
"${PROJECT_PY[@]}" scripts/restart_continuous_jobs.py \
  --profile "$PROFILE" --suffix="$JOB_SUFFIX" \
  || echo "  WARN: could not restart the streaming jobs — they may still be running the previous build." >&2
echo "    (a fresh run appears within a minute or two; the SSE stream gaps briefly)"

# The event log is append-only: resolving an incident appends a 'resolved' row
# and the original keeps timestamp_end NULL for good, so every incident of the
# shift still matches "timestamp_end IS NULL". The board reconciles against the
# resolve markers and shows nothing; anything that does not, counts the lot.
# Reconcile the stored rows so both rules agree, and close carryover nobody is
# coming back for. Must run AFTER the job restart above, because it UPDATEs a
# Delta source the quality-sync stream reads and that stream needs the
# ignoreChanges build to survive it.
echo
echo "=== Closing incidents that were resolved but still look open ==="
SIM_SHIFT=$(_profile_field sim_shift_id)
# DATABRICKS_CONFIG_PROFILE for the same reason as the trace provisioning
# above: this one builds a bare WorkspaceClient() too, and would otherwise
# reconcile incidents through whatever workspace the default profile names.
DATABRICKS_CONFIG_PROFILE="$PROFILE" \
  "${PROJECT_PY[@]}" scripts/close_open_incidents.py \
  --catalog "$UC_CATALOG" --schema "$UC_SCHEMA" \
  --shift-id "${SIM_SHIFT:-shift_1}" --warehouse-id "$WAREHOUSE_ID" \
  || echo "  WARN: incident reconciliation failed — the assistant may report incidents the board does not show." >&2

echo
echo "=== Deploy complete (env=$ENV target=$BUNDLE_TARGET app=$APP_NAME) ==="
if [[ "${RUN_SETUP:-0}" != "1" ]]; then
  echo "    (Existing env — skipped one-time setup. Force re-provision with: RUN_SETUP=1 $0 $ENV)"
fi
if [[ -n "$TRACE_WARNING" ]]; then
  echo
  echo "    !! The app is running, but MLflow traces will be empty." >&2
  echo "       UC-backed trace storage was not provisioned:" >&2
  echo "       $TRACE_WARNING" >&2
  echo "       The app falls back to the bundle's experiment, whose spans upload to a" >&2
  echo "       storage host the Apps runtime cannot reach — so the MLflow UI will show" >&2
  echo "       traces with no spans and offer to 'Enable UC trace storage'." >&2
  echo "       Usual cause: uc_catalog / sql_warehouse_id / mlflow_experiment_path still" >&2
  echo "       SET_ME in config/project-settings.local.yaml. Fix, then re-run this deploy." >&2
  echo "       A UC link is permanent, so a half-linked experiment is scrap; the script" >&2
  echo "       moves to the next name (-2, -3) rather than reusing it." >&2
fi

# What you actually got. Each of these was reported at the moment it happened,
# hundreds of lines ago, which is not where the question gets asked.
if ((${#CAPABILITY_OUTCOMES[@]} > 0)); then
  echo
  echo "=== Capabilities ==="
  for outcome in "${CAPABILITY_OUTCOMES[@]}"; do
    echo "    $outcome"
  done
  echo "    (full inventory, including what is off and why: $0 $ENV --capabilities)"
fi
