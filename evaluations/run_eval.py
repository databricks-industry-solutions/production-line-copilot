"""Run the MLflow GenAI evaluation and enforce quality thresholds.

Usage (local):
    python -m evaluations.run_eval \\
        --source seed \\
        --experiment /Users/you@databricks.com/prodline-eval \\
        --limit 10

Usage (CI / deploy gate):
    # Fails with exit code 1 if any threshold below is breached.
    python -m evaluations.run_eval --gate

Thresholds are read from env vars so profiles can tune independently:

    EVAL_MIN_CORRECTNESS               (default 0.75)
    EVAL_MIN_INCIDENT_MATCH            (default 0.90)  # routing accuracy
    EVAL_MAX_HALLUCINATION_RATE        (default 0.10)
    EVAL_MAX_BUFFER_VIOLATION_RATE     (default 0.05)

The gate applies only to scorers that produced a decisive answer — records
where an LLM judge returned ``not_applicable`` are excluded from that judge's
denominator, so a strategic-advice-only eval slice doesn't fail on
``recovery_respects_buffers`` (which has nothing to evaluate on such records).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import uuid
from pathlib import Path

import mlflow

from evaluations.dataset import (
    build_dataset,
    default_dataset_path,
    read_jsonl,
)
from evaluations.judges import (
    DEFAULT_JUDGE_MODEL,
    all_scorers,
)

logger = logging.getLogger(__name__)


# ── predict_fn ───────────────────────────────────────────────────────

@mlflow.trace(name="eval.predict")
def predict_fn(
    query: str,
    shift_id: str = "shift_1",
    timestamp: str = "2025-01-01T14:00:00",
) -> dict:
    """MLflow ``predict_fn`` wrapper around ``agent_server.agent.invoke``.

    Key design choices:
      - We do NOT pass ``machine_id`` because none of the seed records
        currently need it; the agent handles ``None`` correctly.
      - Each call uses a fresh, namespaced ``conversation_id`` so eval
        conversations don't pollute the live conversation store and are
        easy to purge (``eval-<uuid>``).
      - The ``@mlflow.trace`` decorator makes this the parent span; the
        agent's ``mlflow.langchain.autolog()`` produces nested child spans
        for every ReAct step, tool call, and LLM call — which is what the
        trace-based judges rely on.
    """
    # Deferred import: keeps this module importable without the agent's full
    # dependency tree loaded (useful for the dataset build step in CI).
    from agent_server.agent import invoke

    conversation_id = f"eval-{uuid.uuid4().hex[:8]}"
    return invoke(
        query=query,
        shift_id=shift_id,
        timestamp=timestamp,
        conversation_id=conversation_id,
    )


# ── Threshold gate ───────────────────────────────────────────────────

def _fraction(rows: list[dict], scorer_key: str, positive_values: set[str]) -> tuple[float, int]:
    """Compute (positive_fraction, decisive_count) for a categorical scorer.

    Denominator EXCLUDES ``not_applicable`` / ``no_ground_truth`` / errors —
    only records where the judge produced a decisive verdict count. Returns
    (1.0, 0) when nothing decisive was scored, so an empty denominator can't
    trip the gate.
    """
    decisive: list[str] = []
    for r in rows:
        v = r.get(scorer_key)
        if v is None:
            continue
        # Categorical judges return str; drop the excluded verdicts.
        if v in ("not_applicable", "no_ground_truth", "no_specialist_in_output",
                 "invalid_expected_specialist"):
            continue
        decisive.append(str(v))
    if not decisive:
        return 1.0, 0
    positive = sum(1 for v in decisive if v in positive_values)
    return positive / len(decisive), len(decisive)


def _mean_score(rows: list[dict], scorer_key: str) -> tuple[float, int]:
    """Compute (mean, n) for a numeric scorer, skipping missing values."""
    values = [r[scorer_key] for r in rows if isinstance(r.get(scorer_key), (int, float))]
    if not values:
        return 1.0, 0
    return sum(values) / len(values), len(values)


def evaluate_gate(rows: list[dict]) -> tuple[bool, dict]:
    """Apply configured thresholds to a list of per-record scorer dicts.

    Returns (passed, breakdown) where ``breakdown`` is a dict of every
    threshold evaluated with its (value, threshold, n, pass) fields — useful
    for CI logs and for the eventual dashboard widget.
    """
    thresholds = {
        "correctness_mean_ge": float(os.environ.get("EVAL_MIN_CORRECTNESS", "0.75")),
        "incident_match_frac_ge": float(os.environ.get("EVAL_MIN_INCIDENT_MATCH", "0.90")),
        "hallucination_frac_le": float(os.environ.get("EVAL_MAX_HALLUCINATION_RATE", "0.10")),
        "buffer_violation_frac_le": float(os.environ.get("EVAL_MAX_BUFFER_VIOLATION_RATE", "0.05")),
    }

    breakdown: dict = {}

    # Correctness — numeric 0..1
    val, n = _mean_score(rows, "correctness/score")
    breakdown["correctness"] = {
        "value": val, "threshold": thresholds["correctness_mean_ge"],
        "n": n, "pass": val >= thresholds["correctness_mean_ge"] or n == 0,
    }

    # Routing — deterministic categorical
    frac, n = _fraction(rows, "incident_matches_specialist/value", {"match"})
    breakdown["incident_match"] = {
        "value": frac, "threshold": thresholds["incident_match_frac_ge"],
        "n": n, "pass": frac >= thresholds["incident_match_frac_ge"] or n == 0,
    }

    # Hallucination = fraction with cites_live_evidence == "hallucinated"
    frac, n = _fraction(rows, "cites_live_evidence/value", {"hallucinated"})
    breakdown["hallucination"] = {
        "value": frac, "threshold": thresholds["hallucination_frac_le"],
        "n": n, "pass": frac <= thresholds["hallucination_frac_le"] or n == 0,
    }

    # Buffer violations = fraction with recovery_respects_buffers == "violates"
    frac, n = _fraction(rows, "recovery_respects_buffers/value", {"violates"})
    breakdown["buffer_violation"] = {
        "value": frac, "threshold": thresholds["buffer_violation_frac_le"],
        "n": n, "pass": frac <= thresholds["buffer_violation_frac_le"] or n == 0,
    }

    passed = all(v["pass"] for v in breakdown.values())
    return passed, breakdown


# ── Runner ───────────────────────────────────────────────────────────

def run(
    dataset_records: list | None = None,
    source: str = "seed",
    experiment: str | None = None,
    limit: int | None = None,
    judge_model: str = DEFAULT_JUDGE_MODEL,
    dataset_path: str | None = None,
) -> dict:
    """Execute an eval run and return a dict summary suitable for the gate.

    ``dataset_records`` takes precedence over ``source`` / ``dataset_path``
    when provided (used by tests). Otherwise we read from ``dataset_path`` if
    given, else build from ``source``.
    """
    # Resolve dataset
    if dataset_records is not None:
        records = dataset_records
    elif dataset_path and Path(dataset_path).exists():
        records = read_jsonl(dataset_path)
    else:
        records = build_dataset(source=source, experiment_name=experiment)

    if limit is not None:
        records = records[:limit]

    if not records:
        raise ValueError("No eval records to run — check --source / --dataset")

    # MLflow experiment: use the caller-supplied one, or the tracing default.
    if experiment:
        mlflow.set_experiment(experiment)

    # Convert dataclasses → plain dicts for mlflow.genai.evaluate.
    data = [r.to_dict() if hasattr(r, "to_dict") else r for r in records]

    logger.info("Running mlflow.genai.evaluate on %d records", len(data))
    results = mlflow.genai.evaluate(
        data=data,
        predict_fn=predict_fn,
        scorers=all_scorers(judge_model=judge_model),
    )

    # Flatten per-record scorer values for gate evaluation.
    table = results.tables.get("eval_results_table")
    rows: list[dict] = []
    if table is not None:
        # MLflow may return a pandas DataFrame or a list of dicts depending on
        # backend version; normalize both.
        if hasattr(table, "to_dict"):
            rows = table.to_dict(orient="records")
        else:
            rows = list(table)

    passed, breakdown = evaluate_gate(rows)

    return {
        "passed": passed,
        "n_records": len(data),
        "n_scored": len(rows),
        "breakdown": breakdown,
        "run_id": getattr(results, "run_id", None),
    }


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="seed", choices=["seed", "traces", "both"])
    parser.add_argument("--experiment", default=os.environ.get("MLFLOW_EXPERIMENT_NAME"))
    parser.add_argument("--dataset", default=None, help="JSONL path")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    parser.add_argument("--gate", action="store_true",
                        help="Exit non-zero if any threshold is breached.")
    parser.add_argument("--emit-dataset", default=None,
                        help="If set, write the resolved dataset to this JSONL path and exit.")
    args = parser.parse_args(argv)

    # --emit-dataset lets CI cache/version the seed dataset without invoking
    # the agent. Handy for reproducing an eval run offline.
    if args.emit_dataset:
        records = build_dataset(
            source=args.source,
            experiment_name=args.experiment,
            output_path=args.emit_dataset,
        )
        print(f"Wrote {len(records)} records to {args.emit_dataset}")
        return 0

    dataset_path = args.dataset or (
        str(default_dataset_path()) if args.source == "seed" else None
    )

    summary = run(
        source=args.source,
        experiment=args.experiment,
        limit=args.limit,
        judge_model=args.judge_model,
        dataset_path=dataset_path if dataset_path and Path(dataset_path).exists() else None,
    )

    print(json.dumps(summary, indent=2, default=str))

    if args.gate and not summary["passed"]:
        print("EVAL GATE FAILED — one or more thresholds breached", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
