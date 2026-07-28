"""Align a judge to SME labels with MemAlign, then register the aligned judge.

Run this AFTER SMEs have completed a labeling session (``labeling.py``). It
pulls the labeled traces, distills SME feedback into the judge's instructions
via MemAlign, and persists the aligned judge back to the experiment so
``run_eval --use-aligned-judges`` (and production monitoring) pick it up.

Requires ``dspy`` in the runtime (MemAlign's optimizer backend):
    pip install dspy

Run on Databricks:

    python -m evaluations.alignment.align \\
        --experiment-id 123456 \\
        --judge recovery_respects_buffers \\
        --labels-filter "tag.eval = 'complete'"
"""
from __future__ import annotations

import argparse
import logging
import sys

logger = logging.getLogger(__name__)

DEFAULT_REFLECTION_MODEL = "databricks:/databricks-gpt-5"
DEFAULT_EMBEDDING_MODEL = "databricks:/databricks-gte-large-en"


def _memalign_optimizer(reflection_model: str, embedding_model: str, retrieval_k: int):
    """Construct a MemAlign optimizer. Imported lazily because it needs dspy,
    which we don't ship in the base image.
    """
    try:
        from mlflow.genai.judges.optimizers import MemAlignOptimizer
    except Exception as exc:  # pragma: no cover - import guard
        raise RuntimeError(
            "MemAlign optimizer unavailable — install its backend with "
            "`pip install dspy`."
        ) from exc
    return MemAlignOptimizer(
        reflection_lm=reflection_model,
        retrieval_k=retrieval_k,
        embedding_model=embedding_model,
    )


def align_and_register(
    judge_name: str,
    experiment_id: str,
    labels_filter: str = "tag.eval = 'complete'",
    reflection_model: str = DEFAULT_REFLECTION_MODEL,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    retrieval_k: int = 5,
):
    """Align ``judge_name`` on labeled traces and update the registered judge
    in-place. Returns the aligned judge. Requires a workspace + dspy.
    """
    import mlflow
    from mlflow.genai.scorers import ScorerSamplingConfig, get_scorer

    traces = mlflow.search_traces(
        locations=[experiment_id],
        filter_string=labels_filter,
        return_type="list",   # align() requires list format
    )
    if not traces:
        raise ValueError(
            f"No labeled traces matched {labels_filter!r} in experiment "
            f"{experiment_id}. Have SMEs completed the labeling session?"
        )
    logger.info("Aligning %s on %d labeled traces", judge_name, len(traces))

    base_judge = get_scorer(name=judge_name, experiment_id=experiment_id)
    optimizer = _memalign_optimizer(reflection_model, embedding_model, retrieval_k)
    aligned = base_judge.align(traces=traces, optimizer=optimizer)

    # Update the existing record in-place; sample_rate=0 keeps it off live
    # traffic until you explicitly start monitoring with it.
    aligned.update(
        experiment_id=experiment_id,
        sampling_config=ScorerSamplingConfig(sample_rate=0.0),
    )
    logger.info("Registered aligned judge %s", judge_name)
    return aligned


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--experiment-id", required=True)
    p.add_argument("--judge", required=True, help="Judge name to align (== label schema name)")
    p.add_argument("--labels-filter", default="tag.eval = 'complete'")
    p.add_argument("--reflection-model", default=DEFAULT_REFLECTION_MODEL)
    p.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    p.add_argument("--retrieval-k", type=int, default=5)
    args = p.parse_args(argv)

    align_and_register(
        judge_name=args.judge,
        experiment_id=args.experiment_id,
        labels_filter=args.labels_filter,
        reflection_model=args.reflection_model,
        embedding_model=args.embedding_model,
        retrieval_k=args.retrieval_k,
    )
    print(f"Aligned + registered judge: {args.judge}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
