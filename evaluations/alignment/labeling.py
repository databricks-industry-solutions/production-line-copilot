"""Create a Review App labeling session so SMEs can rate agent traces.

This is the ONLY place real judge-alignment signal comes from: line managers /
process engineers open the session URL and rate each response against the same
categories the LLM judge uses. Their ratings are what ``align`` later distills.

Prerequisite: an ``evaluate()`` run has produced traces in the experiment
(the checked-in gate run does this). Tag the traces you want reviewed with
``eval=complete`` (see the runbook) or pass an explicit dataset.

Run on Databricks:

    python -m evaluations.alignment.labeling \\
        --experiment-id 123456 \\
        --users alice@co.com bob@co.com \\
        --dataset-name my_catalog.my_schema.prodline_eval_v1
"""
from __future__ import annotations

import argparse
import logging
import sys

from evaluations.alignment.base_judges import create_label_schemas
from evaluations.judges import DEFAULT_JUDGE_MODEL

logger = logging.getLogger(__name__)


def create_alignment_session(
    experiment_id: str,
    assigned_users: list[str],
    dataset_name: str | None = None,
    session_name: str = "prodline_judge_alignment",
    model: str = DEFAULT_JUDGE_MODEL,
):
    """Create label schemas (named to match the judges) + a labeling session.

    Returns the ``LabelingSession`` (``.url`` is the SME share link). Requires
    a workspace.
    """
    from mlflow.genai import create_labeling_session

    schema_names = create_label_schemas(experiment_id=experiment_id, model=model)
    logger.info("Ensured %d label schemas: %s", len(schema_names), schema_names)

    session = create_labeling_session(
        name=session_name,
        assigned_users=assigned_users,
        label_schemas=schema_names,   # names == judge names (MemAlign invariant)
    )
    if dataset_name:
        session = session.add_dataset(dataset_name=dataset_name)
        logger.info("Attached dataset %s to session", dataset_name)

    logger.info("Labeling session ready: %s", getattr(session, "url", "<no url>"))
    return session


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--experiment-id", required=True)
    p.add_argument("--users", nargs="+", required=True, help="SME emails to assign")
    p.add_argument("--dataset-name", default=None, help="Managed UC dataset to attach")
    p.add_argument("--session-name", default="prodline_judge_alignment")
    p.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    args = p.parse_args(argv)

    session = create_alignment_session(
        experiment_id=args.experiment_id,
        assigned_users=args.users,
        dataset_name=args.dataset_name,
        session_name=args.session_name,
        model=args.judge_model,
    )
    print(f"Share with SMEs: {getattr(session, 'url', '<open the Review App>')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
