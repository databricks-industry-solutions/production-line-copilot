#!/usr/bin/env bash
# Package the solution accelerator for distribution — SAFELY.
#
# WHY THIS EXISTS
# ---------------
# The repo is safe to publish via git (real workspace identifiers live only in
# gitignored files: .env, .databricks/, config/*.local.yaml, config/
# bundle-targets.yaml, app.*.local.yaml). But if you package by *copying the
# working directory* into a zip/tarball, those untracked-but-present files ride
# along and leak your workspace host, warehouse ID, SP UUIDs, and Lakebase
# endpoints.
#
# `git archive` exports ONLY tracked files at a committed ref, so it structurally
# cannot include untracked or gitignored content. Always package with this
# script (or `git archive` directly) — never `zip -r ... .`.
#
# Usage:
#   ./scripts/package_accelerator.sh            # archive HEAD
#   ./scripts/package_accelerator.sh <ref>      # archive a specific tag/branch/sha
#
# Output: build/production-line-copilot-<ref>.tar.gz  (build/ is gitignored)
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

REF="${1:-HEAD}"
SHORT="$(git rev-parse --short "$REF")"
OUT_DIR="build"
OUT="$OUT_DIR/production-line-copilot-${SHORT}.tar.gz"
PREFIX="production-line-copilot-${SHORT}/"

mkdir -p "$OUT_DIR"

echo "=== git archive $REF → $OUT ==="
git archive --format=tar.gz --prefix="$PREFIX" -o "$OUT" "$REF"

# Belt-and-braces: fail loudly if any sensitive path somehow made it in (would
# only happen if such a file were force-added / committed by mistake).
echo "=== leak guard: scanning archive contents ==="
LEAKS="$(tar -tzf "$OUT" \
  | grep -E '(/\.env$|/\.env\.|/\.databricks/|/\.local\.yaml$|/bundle-targets\.yaml$|/\.cursor/)' \
  | grep -Ev '/\.env\.example$' \
  || true)"
if [[ -n "$LEAKS" ]]; then
  echo "ERROR: sensitive paths found in the archive — aborting:" >&2
  echo "$LEAKS" >&2
  rm -f "$OUT"
  exit 1
fi

echo "=== OK — clean archive ($(du -h "$OUT" | cut -f1)) ==="
echo "    $OUT"
echo "    Contains only files tracked at $REF ($(git rev-parse "$REF"))."
