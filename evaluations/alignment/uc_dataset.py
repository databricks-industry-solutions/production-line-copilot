"""Migrate the checked-in JSONL eval set to a managed Unity Catalog dataset.

A managed ``mlflow.genai.datasets`` dataset is versioned, trace-linked, and
governed by UC — the prerequisite for judge alignment (labels live next to the
records) and the honest upgrade over a file in git. This step needs NO labels,
so it is runnable today.

Run on Databricks (notebook/job/serverless) where the dataset backend + Spark
are available:

    python -m evaluations.alignment.uc_dataset \\
        --uc-table my_catalog.my_schema.prodline_eval_v1

Idempotent: ``merge_records`` upserts, so re-running refreshes the dataset.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from evaluations.dataset import EvalRecord, build_dataset, read_jsonl

logger = logging.getLogger(__name__)


def records_to_rows(records: list[EvalRecord]) -> list[dict]:
    """Convert ``EvalRecord``s to the ``{inputs, expectations}`` dict rows that
    ``EvaluationDataset.merge_records`` expects. Pure — no workspace needed.
    """
    rows: list[dict] = []
    for r in records:
        d = r.to_dict() if hasattr(r, "to_dict") else dict(r)
        row: dict = {"inputs": d.get("inputs", {})}
        expectations = d.get("expectations") or {}
        if expectations:
            row["expectations"] = expectations
        rows.append(row)
    return rows


def load_records(source: str = "seed", dataset_path: str | None = None,
                 experiment_name: str | None = None) -> list[EvalRecord]:
    """Resolve records from a JSONL path (if given/exists) else build them."""
    if dataset_path and Path(dataset_path).exists():
        return read_jsonl(dataset_path)
    return build_dataset(source=source, experiment_name=experiment_name)


def build_uc_dataset(
    uc_table_name: str,
    source: str = "seed",
    dataset_path: str | None = None,
    experiment_id: str | None = None,
    experiment_name: str | None = None,
):
    """Create-or-get the managed UC dataset and merge the eval records into it.

    Returns the ``EvaluationDataset``. Requires a Databricks workspace context.
    """
    from mlflow.genai import datasets

    try:
        dataset = datasets.get_dataset(name=uc_table_name)
        logger.info("Loaded existing UC dataset %s", uc_table_name)
    except Exception:
        dataset = datasets.create_dataset(name=uc_table_name, experiment_id=experiment_id)
        logger.info("Created UC dataset %s", uc_table_name)

    rows = records_to_rows(load_records(source, dataset_path, experiment_name))
    dataset.merge_records(rows)
    logger.info("Merged %d records into %s", len(rows), uc_table_name)
    return dataset


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--uc-table", required=True,
                   help="Fully-qualified UC table name, e.g. cat.schema.prodline_eval_v1")
    p.add_argument("--source", default="seed", choices=["seed", "traces", "both"])
    p.add_argument("--dataset", default=None, help="JSONL path (overrides --source when it exists)")
    p.add_argument("--experiment-id", default=None)
    p.add_argument("--experiment-name", default=None)
    args = p.parse_args(argv)

    dataset = build_uc_dataset(
        uc_table_name=args.uc_table,
        source=args.source,
        dataset_path=args.dataset,
        experiment_id=args.experiment_id,
        experiment_name=args.experiment_name,
    )
    n = len(dataset.to_df()) if hasattr(dataset, "to_df") else "?"
    print(f"UC dataset {args.uc_table} now has {n} records")
    return 0


if __name__ == "__main__":
    sys.exit(main())
