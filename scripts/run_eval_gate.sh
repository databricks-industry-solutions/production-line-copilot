#!/usr/bin/env bash
# Thin wrapper that runs the MLflow GenAI eval gate.
#
# Usage:
#   ./scripts/run_eval_gate.sh                # run against the seed dataset
#   EVAL_LIMIT=5 ./scripts/run_eval_gate.sh   # faster local check
#
# Env vars honored:
#   MLFLOW_EXPERIMENT_NAME             — required when --source traces
#   EVAL_LIMIT                         — cap on records evaluated
#   EVAL_JUDGE_MODEL                   — override judge model
#   EVAL_MIN_CORRECTNESS               — 0..1, default 0.75
#   EVAL_MIN_INCIDENT_MATCH            — 0..1, default 0.90
#   EVAL_MAX_HALLUCINATION_RATE        — 0..1, default 0.10
#   EVAL_MAX_BUFFER_VIOLATION_RATE     — 0..1, default 0.05
#   DATABRICKS_PROFILE                 — CLI profile for MLflow tracking URI
#                                        (needed when running outside the
#                                        workspace; unused in-workspace)
#
# Exit codes: forwarded from python -m evaluations.run_eval (0=pass, 1=fail).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR"

# LIMIT default kept modest (10) so the wrapper is safe to run interactively
# without approving a 30-call LLM budget by accident. Override with EVAL_LIMIT.
LIMIT="${EVAL_LIMIT:-10}"

# Ensure src is importable when running from a fresh checkout without `pip -e .`.
export PYTHONPATH="$ROOT_DIR:${PYTHONPATH:-}"

echo "=== MLflow GenAI eval gate — limit=$LIMIT judge=${EVAL_JUDGE_MODEL:-default} ==="
python3 -m evaluations.run_eval \
  --source seed \
  --limit "$LIMIT" \
  --gate \
  "$@"
