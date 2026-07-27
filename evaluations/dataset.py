"""Build an MLflow GenAI evaluation dataset for the ProdLine agent.

Two sources are supported:

- ``seed``   — the ten hand-curated ``INTENT_SCENARIOS`` (plus the two
               ``GOLDEN_RESPONSES``) already living in
               ``tests/agent_server/test_agent_evaluation.py``. These are the
               only records with ground truth (``expected_specialist``,
               ``expected_facts``) and are the source of the CI eval-gate
               dataset.
- ``traces`` — recent production traces pulled via ``mlflow.search_traces``
               against a configured experiment. No ground truth, so only the
               non-Correctness scorers apply. Used to catch regressions on
               real user queries without hand-labelling.

Output shape follows the MLflow 3 GenAI convention documented at
https://mlflow.org/docs/latest/genai/eval-monitor/quickstart/ :

    {
        "inputs":       {"query": "...", "shift_id": "...", "timestamp": "..."},
        "expectations": {"expected_specialist": "...",
                         "expected_facts": [...],       # optional
                         "expected_response": "..."},   # optional
    }

The ``inputs`` keys must match the kwargs of ``predict_fn`` (see
``run_eval.predict_fn``). The reserved ``expectations`` keys (``expected_facts``,
``expected_response``) are consumed by the built-in ``Correctness`` judge;
everything else is available to custom judges via ``{{ expectations }}``.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Literal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EvalRecord:
    """A single evaluation record.

    ``inputs`` is expanded as ``**kwargs`` into the ``predict_fn``, so its
    keys must match the argument names of ``agent_server.agent.invoke``.

    ``expectations`` uses MLflow's reserved keys where possible so built-in
    judges pick them up automatically. Non-reserved keys (e.g.
    ``expected_specialist``) are still accessible to custom judges via the
    ``{{ expectations }}`` template variable.
    """

    inputs: dict
    expectations: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# The shift_id/timestamp defaults intentionally mirror agent.invoke's defaults.
# When the shift is producing these are ignored anyway because
# _overlay_live_state rewrites the operational slices from live Lakebase state,
# but they still route through validate_shift_id/validate_timestamp so the
# strings must be syntactically valid.
DEFAULT_SHIFT_ID = "shift_1"
DEFAULT_TIMESTAMP = "2025-01-01T14:00:00"


def build_seed_records() -> list[EvalRecord]:
    """Materialize eval records from the shared scenarios module.

    Source of truth: ``evaluations.scenarios`` — a plain module (no pytest
    import) shared with the unit tests, so a scenario added in one place shows
    up in both the ``test_intent_mapping`` parametrization and the next
    ``build_seed_records`` call.
    """
    from evaluations.scenarios import GOLDEN_RESPONSES, INTENT_SCENARIOS

    records: list[EvalRecord] = []

    for scenario in INTENT_SCENARIOS:
        # We DO NOT use ``expected_response`` here because these scenarios
        # exercise routing, not exact-text correctness — the LLM ``Correctness``
        # judge would give a false negative on free-form specialist answers.
        # The routing check lives in the ``incident_matches_specialist``
        # deterministic scorer, which reads ``expected_specialist`` from the
        # non-reserved key below.
        records.append(EvalRecord(
            inputs={
                "query": scenario["query"],
                "shift_id": DEFAULT_SHIFT_ID,
                "timestamp": DEFAULT_TIMESTAMP,
            },
            expectations={
                "expected_specialist": scenario["expected_specialist"],
            },
        ))

    for golden in GOLDEN_RESPONSES:
        records.append(EvalRecord(
            inputs={
                "query": golden["query"],
                "shift_id": golden["shift_id"],
                "timestamp": DEFAULT_TIMESTAMP,
            },
            expectations={
                # ``expected_facts`` is a reserved key that Correctness reads —
                # it treats each keyword as a fact that must appear (semantically,
                # not literally) in the answer.
                "expected_facts": golden["must_contain_keywords"],
                # Kept for custom judges / negative-checks (custom scorer to add
                # later if needed).
                "must_not_contain": golden.get("must_not_contain", []),
            },
        ))

    return records


def build_trace_records(
    experiment_name: str,
    limit: int = 30,
    min_query_length: int = 10,
) -> list[EvalRecord]:
    """Pull recent real user queries from an MLflow experiment.

    Returns records with no ``expectations`` — only the trace-based judges
    (ToolCallCorrectness, custom LLM judges) can score them. The Correctness
    judge is skipped silently when ``expected_response``/``expected_facts``
    is absent.

    A record is kept only if the trace has an ``input.query`` string longer
    than ``min_query_length`` characters. This drops health checks, empty
    prompts, and the ``/api/agent`` synthetic warm-up pings that would
    otherwise dominate the sample.
    """
    import mlflow  # deferred so unit tests can import the module without a
                   # workspace connection.

    mlflow.set_experiment(experiment_name)
    traces_df = mlflow.search_traces(max_results=limit * 4)  # over-pull, filter
    records: list[EvalRecord] = []
    for _, row in traces_df.iterrows():
        # ``request`` is the JSON-encoded predict_fn kwargs; MLflow 3 traces
        # expose it as either row["request"] or row["inputs"] depending on
        # provider. Handle both defensively.
        raw = row.get("request") or row.get("inputs")
        if not raw:
            continue
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            continue
        query = payload.get("query") or payload.get("prompt")
        if not query or len(str(query)) < min_query_length:
            continue
        records.append(EvalRecord(
            inputs={
                "query": query,
                "shift_id": payload.get("shift_id", DEFAULT_SHIFT_ID),
                "timestamp": payload.get("timestamp", DEFAULT_TIMESTAMP),
            },
        ))
        if len(records) >= limit:
            break
    logger.info("Pulled %d trace-derived records from experiment %r",
                len(records), experiment_name)
    return records


def write_jsonl(records: Iterable[EvalRecord], path: str | Path) -> Path:
    """Serialize records to a JSONL file. Path may be a UC volume path
    (``/Volumes/<catalog>/<schema>/<volume>/…``) when running on Databricks,
    or any local path.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")
    return p


def read_jsonl(path: str | Path) -> list[EvalRecord]:
    """Load records written by ``write_jsonl``."""
    p = Path(path)
    records: list[EvalRecord] = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            records.append(EvalRecord(
                inputs=d.get("inputs", {}),
                expectations=d.get("expectations", {}),
            ))
    return records


def build_dataset(
    source: Literal["seed", "traces", "both"] = "seed",
    output_path: str | Path | None = None,
    experiment_name: str | None = None,
    trace_limit: int = 30,
) -> list[EvalRecord]:
    """Assemble an eval dataset and optionally persist it as JSONL.

    Returns the in-memory list either way so callers can pass it straight to
    ``mlflow.genai.evaluate``.
    """
    records: list[EvalRecord] = []
    if source in ("seed", "both"):
        records.extend(build_seed_records())
    if source in ("traces", "both"):
        if not experiment_name:
            raise ValueError(
                "experiment_name is required when source includes 'traces'"
            )
        records.extend(build_trace_records(experiment_name, limit=trace_limit))
    if output_path:
        write_jsonl(records, output_path)
        logger.info("Wrote %d eval records to %s", len(records), output_path)
    return records


def default_dataset_path() -> Path:
    """Return the canonical local path for the seed eval dataset.

    Used by the CI gate — deterministic, checked-in (via git) so eval runs are
    reproducible without a workspace round-trip to fetch the dataset.
    """
    root = Path(os.environ.get("EVAL_DATASET_ROOT", "evaluations/data"))
    return root / "seed_v1.jsonl"
