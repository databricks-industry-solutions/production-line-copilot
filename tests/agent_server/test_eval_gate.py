"""Integration test that runs the MLflow GenAI eval gate on the seed dataset.

Marked ``integration`` so ``pytest`` doesn't hit real LLM endpoints by
default. Run with:

    RUN_EVAL_GATE=1 pytest tests/agent_server/test_eval_gate.py -m integration

or via ``scripts/run_eval_gate.sh`` which sets the env vars and forwards the
exit code to the deploy pipeline.

This is intentionally minimal — a single test that asserts the gate result.
The eval body lives in ``evaluations.run_eval.run`` so a failure here points
at that module, not at test plumbing.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def eval_summary():
    """Run the eval once per module; skip cleanly when opt-in is off."""
    if os.environ.get("RUN_EVAL_GATE") != "1":
        pytest.skip(
            "RUN_EVAL_GATE=1 not set — skipping the LLM-backed eval gate. "
            "Run scripts/run_eval_gate.sh to execute."
        )

    pytest.importorskip("mlflow")
    from evaluations.run_eval import run

    # Small default cap so a broken workspace doesn't burn 30 LLM calls before
    # failing. Override with EVAL_LIMIT env var for a fuller run.
    limit = int(os.environ.get("EVAL_LIMIT", "10"))
    return run(source="seed", limit=limit)


class TestEvalGate:
    def test_no_records_scored_is_a_failure(self, eval_summary):
        """Empty result usually means predict_fn broke — treat as a hard fail."""
        assert eval_summary["n_scored"] > 0, (
            f"No records were scored — likely predict_fn error. Summary: {eval_summary}"
        )

    def test_gate_passes(self, eval_summary):
        """The composite gate must pass. Individual scorer values printed in
        the ``breakdown`` field help debug regressions."""
        breakdown = eval_summary["breakdown"]
        failures = [name for name, r in breakdown.items() if not r["pass"]]
        assert eval_summary["passed"], (
            f"Eval gate failed on: {failures}\nFull breakdown: {breakdown}"
        )
