#!/usr/bin/env bash
# Publish a release to the public mirror, without the internal-only files.
#
# WHY THIS EXISTS
# ---------------
# The public repository is a chain of squashed commits: one per release, each
# one a full copy of this tree parented on the previous public commit. Copying
# the whole tree is the default, so every tracked file is published unless
# something stops it — and until this script existed, the only thing stopping it
# was the person doing the release remembering to delete files by hand.
#
# That is not a control. It is a habit, and it fails on the release you do in a
# hurry. scripts/public-exclude.txt names the paths that must not be published,
# this script strips them, and it refuses to produce a mirror commit that still
# contains one.
#
# It also refuses to produce a mirror that is broken *by* the stripping: a
# README linking a file that is no longer there, or a build that only works
# because an excluded source file happened to exist. Those checks run against
# the published tree itself, not against this one.
#
# WHAT IT DOES NOT DO
# -------------------
# It does not push unless you ask. By default it builds the commit, verifies it
# and tells you what it would push, so you can inspect the result first.
#
# Usage:
#   ./scripts/mirror_public.sh --message-file notes.md         # build + verify
#   ./scripts/mirror_public.sh --message-file notes.md --push  # and publish
#   ./scripts/mirror_public.sh --dry-run                       # verify only
#
# The message file holds the public commit message: a subject line
# "ProdLine CoPilot vX.Y.Z — <what changed>" and the prose body, in the style of
# `git log solutions2/main`.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

REMOTE="${MIRROR_REMOTE:-solutions2}"
BRANCH="${MIRROR_BRANCH:-main}"
EXCLUDE_FILE="scripts/public-exclude.txt"

MESSAGE_FILE=""
PUSH=0
DRY_RUN=0
SOURCE_REF="HEAD"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --message-file) MESSAGE_FILE="${2:-}"; shift 2 ;;
    --push)         PUSH=1; shift ;;
    --dry-run)      DRY_RUN=1; shift ;;
    --ref)          SOURCE_REF="${2:-}"; shift 2 ;;
    -h|--help)      sed -n '1,36p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ $PUSH -eq 1 && -z "$MESSAGE_FILE" ]]; then
  echo "ERROR: --push needs --message-file (the public release notes)." >&2
  exit 2
fi

# Read the exclusion list, dropping comments and blank lines. Written as a read
# loop rather than `mapfile` because macOS still ships bash 3.2.
EXCLUDED=()
while IFS= read -r line; do
  [[ -n "$line" ]] && EXCLUDED+=("$line")
done < <(grep -Ev '^[[:space:]]*(#|$)' "$EXCLUDE_FILE")
if ((${#EXCLUDED[@]} == 0)); then
  echo "ERROR: $EXCLUDE_FILE lists no paths — refusing to run." >&2
  exit 1
fi

echo "=== source: $SOURCE_REF ($(git rev-parse --short "$SOURCE_REF")) ==="
echo "=== excluding ${#EXCLUDED[@]} path(s) from the published tree ==="
printf '    %s\n' "${EXCLUDED[@]}"

# Build the published tree in a scratch index, so the working tree is never
# touched and a failed run leaves nothing to clean up.
TMP_INDEX="$(mktemp -t mirror-index.XXXXXX)"
WORKTREE=""
cleanup() {
  rm -f "$TMP_INDEX"
  if [[ -n "$WORKTREE" && -d "$WORKTREE" ]]; then
    git worktree remove --force "$WORKTREE" >/dev/null 2>&1 || rm -rf "$WORKTREE"
  fi
}
trap cleanup EXIT

echo "=== building the published tree ==="
GIT_INDEX_FILE="$TMP_INDEX" git read-tree "$SOURCE_REF"
for path in "${EXCLUDED[@]}"; do
  # --ignore-unmatch: a path already gone (or never added) is not an error. The
  # point of the list is the guarantee that it is absent, not that it was here.
  GIT_INDEX_FILE="$TMP_INDEX" git rm -r --cached --quiet --ignore-unmatch -- "$path"
done
TREE="$(GIT_INDEX_FILE="$TMP_INDEX" git write-tree)"

# Guard 1: the excluded paths really are gone from the tree we are about to
# publish. This is the whole promise, so it is checked against the tree object
# rather than against the index we just edited.
echo "=== leak guard: excluded paths absent from the published tree ==="
LEAKS="$(git ls-tree -r --name-only "$TREE" | grep -F -f <(printf '%s\n' "${EXCLUDED[@]}") || true)"
if [[ -n "$LEAKS" ]]; then
  echo "ERROR: excluded paths are still in the tree — aborting:" >&2
  echo "$LEAKS" >&2
  exit 1
fi
echo "    ok — none of the ${#EXCLUDED[@]} excluded paths survive"

# Guard 2: no published *document* points at a file that will not be there. Code
# may refer to an excluded path conditionally (build_film.py treats the deep
# dive as an optional target, its tests skip without it); prose cannot, because
# a link in a README is a promise the reader can click.
echo "=== link guard: no published doc references an excluded path ==="
DANGLING=""
while IFS= read -r doc; do
  for path in "${EXCLUDED[@]}"; do
    if git cat-file blob "$TREE:$doc" 2>/dev/null | grep -qF "$path"; then
      DANGLING+="$doc -> $path"$'\n'
    fi
  done
done < <(git ls-tree -r --name-only "$TREE" | grep -E '\.(md|html)$' || true)
if [[ -n "$DANGLING" ]]; then
  echo "ERROR: published docs reference files that will not exist:" >&2
  echo "$DANGLING" >&2
  exit 1
fi
echo "    ok — no dangling references in published .md/.html"

# Guard 3: the published tree stands on its own. Check it out and run the checks
# that the stripping could plausibly break — the film has to still rebuild from
# the source that remains, and its tests have to pass rather than error on a
# missing file.
COMMIT_MSG="${MESSAGE_FILE:+$(cat "$MESSAGE_FILE")}"
COMMIT_MSG="${COMMIT_MSG:-mirror dry run $(git rev-parse --short "$SOURCE_REF")}"

git fetch --quiet "$REMOTE" "$BRANCH"
PARENT="$(git rev-parse "$REMOTE/$BRANCH")"
COMMIT="$(printf '%s' "$COMMIT_MSG" | git commit-tree "$TREE" -p "$PARENT")"

WORKTREE="$(mktemp -d -t mirror-tree.XXXXXX)"
rm -rf "$WORKTREE"
git worktree add --quiet --detach "$WORKTREE" "$COMMIT"

PY="$ROOT_DIR/.venv/bin/python"
[[ -x "$PY" ]] || PY="python3"

echo "=== build guard: the film rebuilds from the source that remains ==="
(cd "$WORKTREE" && "$PY" scripts/build_film.py --check)
echo "    ok"

echo "=== test guard: the film tests pass on the published tree ==="
(cd "$WORKTREE" && "$PY" -m pytest tests/scripts/test_the_film_can_be_rebuilt_from_its_source.py -q)

git worktree remove --force "$WORKTREE" >/dev/null 2>&1 || true
WORKTREE=""

VERSION="$("$PY" -c 'import json,pathlib;print(json.loads(pathlib.Path("package.json").read_text())["version"])')"
TAG="v${VERSION}-public"

echo
echo "=== mirror commit built ==="
echo "    commit:  $COMMIT"
echo "    parent:  $PARENT ($REMOTE/$BRANCH)"
echo "    tag:     $TAG"
echo "    subject: $(printf '%s' "$COMMIT_MSG" | head -1)"
echo "    files:   $(git ls-tree -r --name-only "$TREE" | wc -l | tr -d ' ') published, ${#EXCLUDED[@]} path(s) withheld"

if [[ $DRY_RUN -eq 1 || $PUSH -eq 0 ]]; then
  echo
  echo "Not pushing. To publish this exact commit:"
  echo "    git push $REMOTE $COMMIT:refs/heads/$BRANCH"
  echo "    git tag -a $TAG $COMMIT -m 'ProdLine CoPilot $VERSION (public mirror)'"
  echo "    git push $REMOTE $TAG"
  echo
  echo "Or re-run with --push."
  exit 0
fi

if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  echo "ERROR: tag $TAG already exists — bump the version or delete the tag." >&2
  exit 1
fi

echo "=== pushing to $REMOTE ==="
git push "$REMOTE" "$COMMIT:refs/heads/$BRANCH"
git tag -a "$TAG" "$COMMIT" -m "ProdLine CoPilot $VERSION (public mirror)"
git push "$REMOTE" "$TAG"
echo "=== published $TAG ==="
