"""Judge-alignment toolkit for the ProdLine agent eval harness.

This package turns "we'd need real labeled data" into a ready-to-run workflow.
It does NOT fabricate labels — every step here either runs now without labels
(managed UC dataset) or waits for real SME labels collected via the MLflow
Review App before it does anything (labeling session, MemAlign alignment).

Modules:
  * ``base_judges``  — the alignable LLM judges + matching label schemas
                       (schema name == judge name, the MemAlign invariant).
  * ``uc_dataset``   — migrate the checked-in JSONL to a managed, versioned
                       Unity Catalog evaluation dataset (runnable now).
  * ``labeling``     — create a Review App labeling session for SMEs.
  * ``align``        — run MemAlign on labeled traces + register the aligned
                       judge (runnable once real labels exist).

See ``README.md`` for the end-to-end runbook.
"""
from __future__ import annotations

from evaluations.alignment.base_judges import (
    ALIGNABLE_JUDGE_FACTORIES,
    judge_categories,
    label_schema_specs,
    make_base_judges,
)

__all__ = [
    "ALIGNABLE_JUDGE_FACTORIES",
    "judge_categories",
    "label_schema_specs",
    "make_base_judges",
]
