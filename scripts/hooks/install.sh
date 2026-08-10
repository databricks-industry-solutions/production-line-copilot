#!/usr/bin/env bash
# Link this repository's git hooks into its own .git/hooks directory.
#
#   ./scripts/hooks/install.sh
#
# Symlinks rather than copies, so the installed hook tracks the file under
# version control and cannot drift from it.
#
# This changes nothing in your git config, and that is the point. `.git/hooks`
# is normally bypassed when `core.hooksPath` is set — as it is for anyone with
# the Databricks managed hooks installed — but that managed pre-push runs the
# repo-local hook first and aborts the push if it exits non-zero. So the hook
# fires either way: through git directly when no `core.hooksPath` is set, and
# through the managed hook when one is. Nobody has to choose between the
# project's gate and their organisation's secret scanning.
#
# Note the deliberate absence of `git rev-parse --git-path hooks`, which looks
# like the right way to find the hooks directory and is not: it resolves
# *through* `core.hooksPath`, so on a machine with managed hooks it returns the
# shared directory. Writing there does not install a repo hook — it replaces
# the organisation's, for every repository on the machine. This script asks for
# the git directory instead and appends `hooks` itself, then refuses to write
# anywhere that is not inside this repository.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.."

ROOT="$(git rev-parse --show-toplevel)"
GIT_DIR="$(git rev-parse --git-common-dir)"
# --git-common-dir is relative ('.git') from the top level; make it absolute so
# the containment check below compares like with like.
[[ "$GIT_DIR" = /* ]] || GIT_DIR="$ROOT/$GIT_DIR"
HOOKS_DIR="$GIT_DIR/hooks"

# Refuse to touch anything outside this repository, whatever git reports.
case "$HOOKS_DIR" in
  "$ROOT"/*) ;;
  *)
    echo "refusing to install: $HOOKS_DIR is outside $ROOT" >&2
    exit 1
    ;;
esac

mkdir -p "$HOOKS_DIR"

for hook in scripts/hooks/*; do
  name="$(basename "$hook")"
  [[ "$name" == "install.sh" ]] && continue

  target="$HOOKS_DIR/$name"
  source_path="$ROOT/scripts/hooks/$name"

  chmod +x "$source_path"

  if [[ -L "$target" && "$(readlink "$target")" == "$source_path" ]]; then
    echo "  already installed: $target"
    continue
  fi

  # Never clobber someone's own hook without keeping a copy of it.
  if [[ -e "$target" && ! -L "$target" ]]; then
    backup="$target.local.$(date +%Y%m%d%H%M%S)"
    mv "$target" "$backup"
    echo "  existing $name moved to $backup"
  fi

  ln -sf "$source_path" "$target"
  echo "  installed: $target -> $source_path"
done

echo
echo "Pushes now run ./scripts/check.sh first. Bypass with: SKIP_CHECKS=1 git push"
