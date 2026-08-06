#!/usr/bin/env bash
# Every gate a change has to pass, in one command.
#
# This exists because the CI workflow cannot be relied on to be the only place
# the checks live. Hosted runners are disabled on some GitHub Enterprise
# repositories, this one included, and a workflow that cannot start is a gate
# that protects nothing while looking like it does. So the checks are defined
# here, .github/workflows/tests.yml calls this same script, and the two cannot
# drift apart into "green in CI, broken locally" or the reverse.
#
# Usage:
#   ./scripts/check.sh          # everything
#   ./scripts/check.sh python   # just the Python gates
#   ./scripts/check.sh client   # just the TypeScript gates
#
# Exits non-zero on the first failure, and says which gate failed.
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

WHICH="${1:-all}"
FAILED=()

# Prefer the project venv when there is one, so a developer who has not
# activated it still runs the same interpreter CI does.
if [[ -x .venv/bin/python ]]; then
  PY=".venv/bin/python"
else
  PY="python3"
fi

# Colour only for a terminal. A CI log full of escape codes is harder to read
# and harder to grep, which is the opposite of what the highlighting is for.
if [[ -t 1 && "${NO_COLOR:-}" == "" ]]; then
  BOLD=$'\033[1m'; RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; OFF=$'\033[0m'
else
  BOLD=""; RED=""; GREEN=""; YELLOW=""; OFF=""
fi

run() {
  local name="$1"; shift
  printf '\n%s── %s ─────────────────────────────────────%s\n' "$BOLD" "$name" "$OFF"
  if "$@"; then
    printf '%sok%s  %s\n' "$GREEN" "$OFF" "$name"
  else
    printf '%sFAILED%s  %s\n' "$RED" "$OFF" "$name"
    FAILED+=("$name")
  fi
}

if [[ "$WHICH" == "all" || "$WHICH" == "python" ]]; then
  # uvx so a missing local ruff is not a reason to skip linting.
  if command -v uvx >/dev/null 2>&1; then
    run "ruff" uvx ruff check .
  elif "$PY" -c "import ruff" >/dev/null 2>&1; then
    run "ruff" "$PY" -m ruff check .
  else
    printf '\n%sskip%s  ruff (install uv, or pip install ruff)\n' "$YELLOW" "$OFF"
  fi
  run "pytest" "$PY" -m pytest -q
fi

if [[ "$WHICH" == "all" || "$WHICH" == "client" ]]; then
  if [[ -d node_modules ]]; then
    run "tsc" npm run --silent typecheck
    run "eslint" npm run --silent lint
    # Not `npm test`, which chains the Playwright smoke run: that boots the real
    # server against a warehouse and Lakebase. Run it yourself with
    # `npm run test:smoke` when you have a workspace.
    run "vitest" npx vitest run
  else
    printf '\n%sskip%s  client gates (run npm install first)\n' "$YELLOW" "$OFF"
  fi
fi

printf '\n'
if (( ${#FAILED[@]} )); then
  printf '%s%d gate(s) failed:%s %s\n' "$RED" "${#FAILED[@]}" "$OFF" "${FAILED[*]}"
  exit 1
fi
printf '%sAll gates passed.%s\n' "$GREEN" "$OFF"
