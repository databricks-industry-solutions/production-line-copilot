"""Scorers for ProdLine agent evaluation.

Three groups:

- Built-in judges (``Correctness``, ``ToolCallCorrectness``,
  ``RetrievalGroundedness``, ``Safety``) exposed via ``built_in_scorers()``
  so ``run_eval`` can pick a subset per environment.

- Three LLM-backed custom judges via ``make_judge``:
    * recovery_respects_buffers  — trace + outputs
    * optimizer_choice_appropriate — trace + inputs
    * cites_live_evidence        — trace + outputs

- One deterministic scorer via ``@scorer``:
    * incident_matches_specialist — checks ``outputs.specialist_used`` against
      the expected specialist encoded in ``expectations.expected_specialist``.
      No LLM call. Free, fast, and non-flaky.

The LLM judges take a project-appropriate ``model`` string (Databricks-hosted
by default). Override at the call site to A/B judge quality across models.

References:
    make_judge template variables + trace-based requirement:
    https://docs.databricks.com/aws/en/mlflow3/genai/eval-monitor/custom-judge/
    Built-in scorer list:
    https://docs.databricks.com/aws/en/mlflow3/genai/eval-monitor/concepts/judges/
"""
from __future__ import annotations

import os
from typing import Literal

# All imports are top-level; the module is optional and only loaded by
# ``run_eval``, so pulling mlflow.genai here doesn't slow down the API server.
from mlflow.genai import scorer
from mlflow.genai.judges import make_judge
from mlflow.genai.scorers import (
    Correctness,
    RetrievalGroundedness,
    Safety,
    ToolCallEfficiency,
)

# Default judge model. Databricks-hosted GPT-5 mini is fast enough that a
# 30-record eval finishes in ~2 min and cheap enough for pre-deploy CI use.
# Override with ``EVAL_JUDGE_MODEL`` for A/B on judge quality.
DEFAULT_JUDGE_MODEL = os.environ.get(
    "EVAL_JUDGE_MODEL", "databricks:/databricks-gpt-5-mini"
)


# ── Custom LLM judges ────────────────────────────────────────────────

def recovery_respects_buffers(model: str = DEFAULT_JUDGE_MODEL):
    """Any speed / setpoint change must cite upstream OR downstream buffer state.

    Rationale: the single most common failure mode of "helpful" LLM ops
    assistants is recommending a speed reduction on a starved machine — which
    just makes the downstream backlog worse. The specialist prompts already
    tell the model to check buffers; this judge verifies it actually did.
    """
    return make_judge(
        name="recovery_respects_buffers",
        model=model,
        feedback_value_type=Literal["respects", "violates", "not_applicable"],
        instructions=(
            "You are evaluating a manufacturing line assistant.\n\n"
            "Read {{ outputs }} and decide:\n"
            "  - If the response recommends changing a line speed, machine "
            "setpoint, or throughput target, verify it also references "
            "upstream OR downstream buffer state (buffer level, buffer_pct, "
            "starvation/blocking risk, or the surrounding machine states) "
            "from the context. If it does, return 'respects'. If it makes a "
            "speed/setpoint recommendation without any such reference, return "
            "'violates'.\n"
            "  - If no speed or setpoint recommendation is made, return "
            "'not_applicable'."
        ),
    )


def optimizer_choice_appropriate(model: str = DEFAULT_JUDGE_MODEL):
    """Correct optimizer tool was invoked for the query intent.

    Reads the tool spans in the trace and cross-checks against the intent
    encoded in the query. The mapping is deliberately embedded in the judge
    instructions rather than a table lookup because the intent-detection is
    itself fuzzy (a query about "make up the shift target" could route to
    schedule_recovery or multi_shift_plan — both are 'appropriate').
    """
    return make_judge(
        name="optimizer_choice_appropriate",
        model=model,
        feedback_value_type=Literal[
            "appropriate", "wrong_optimizer", "no_optimizer_needed"
        ],
        instructions=(
            "You are evaluating a manufacturing agent's tool choice.\n\n"
            "Read the tool calls in {{ trace }} and the user query in "
            "{{ inputs }}. Decide:\n\n"
            "Intent → acceptable optimizers:\n"
            "  - material shortage / stockout → schedule_recovery OR nothing "
            "(supply lookup only)\n"
            "  - changeover delay / rush order / shift-target recovery → "
            "schedule_recovery OR multi_shift_plan OR "
            "stochastic_schedule_recovery\n"
            "  - quality deviation / hold decision → quality_risk_assessment\n"
            "  - OEE loss decomposition → oee_loss_analysis\n"
            "  - predictive maintenance / RUL / vibration → estimate_rul\n"
            "  - production forecast / next-shift plan → production_forecast\n"
            "  - status / briefing / general question → NO optimizer needed\n\n"
            "Return 'appropriate' if the tool call matches the mapping, "
            "'wrong_optimizer' if a mismatched optimizer was called, or "
            "'no_optimizer_needed' if the query didn't warrant one AND none "
            "was invoked."
        ),
    )


def cites_live_evidence(model: str = DEFAULT_JUDGE_MODEL):
    """Per-machine claims must be traceable to context data in the trace.

    Grounds the agent's specific claims about named machines (FILLER,
    LABELLER, CAPPER, PALLETIZER, CHECKWEIGHER, etc.) against the SQL /
    Genie / context-builder spans in the trace. Generic statements
    ("consider running a diagnostic") don't count as claims.
    """
    return make_judge(
        name="cites_live_evidence",
        model=model,
        feedback_value_type=Literal["grounded", "hallucinated", "not_applicable"],
        instructions=(
            "You are checking a manufacturing agent for hallucination.\n\n"
            "Scan {{ outputs }} for specific claims about NAMED machines — "
            "any of: FILLER, LABELLER, CAPPER, PALLETIZER, CHECKWEIGHER, "
            "SEALER, CARTONER, DEPALLETIZER, CASE_PACKER, or similar all-caps "
            "identifiers. A 'claim' is a factual statement about that "
            "machine's status, throughput, speed, alarms, or condition.\n\n"
            "For every such claim, look in {{ trace }} spans (SQL query "
            "results, Genie responses, context builder outputs, tool inputs) "
            "for a supporting value. If every claim is supported by trace "
            "evidence, return 'grounded'. If ANY claim has no supporting "
            "evidence in the trace, return 'hallucinated'. If the answer "
            "makes NO per-machine claim (e.g. general strategic advice), "
            "return 'not_applicable'."
        ),
    )


# ── Deterministic scorer ─────────────────────────────────────────────
# incident_matches_specialist: no LLM. The agent's returned dict includes
# ``specialist_used``, and the seed dataset carries ``expected_specialist``
# under expectations, so this is a straight equality check. Runs in <1ms
# per record and is 100% reliable — critical for the CI gate not to flake.

# Valid values for the routed specialist (mirrors _classify_intent in agent.py).
_VALID_SPECIALISTS = frozenset({
    "downtime_analyst", "quality_specialist", "supply_chain_advisor",
    "oee_coach", "schedule_optimizer", "maintenance_predictor",
    "strategic_advisor", "shift_briefing", "general",
})


@scorer
def incident_matches_specialist(outputs, expectations):
    """Return 'match' if the routed specialist equals the expected one, else
    'mismatch'. Returns 'no_ground_truth' when the record has no
    ``expected_specialist`` in expectations (e.g. trace-derived records) so
    the aggregation ignores it instead of counting it as a failure.

    The MLflow eval harness passes ``outputs`` as either the dict returned
    from predict_fn or a wrapped ``{"response": ...}`` shape depending on the
    scorer signature; we handle both defensively.
    """
    if not expectations or "expected_specialist" not in expectations:
        return "no_ground_truth"

    expected = expectations["expected_specialist"]
    if expected not in _VALID_SPECIALISTS:
        # Guard against typos in the dataset. Fail loudly rather than pretend
        # the record scored correctly.
        return "invalid_expected_specialist"

    # ``outputs`` is what predict_fn returned. Our agent.invoke returns a
    # dict with ``specialist_used``, so we look for that key.
    actual = None
    if isinstance(outputs, dict):
        actual = outputs.get("specialist_used")
    if actual is None:
        return "no_specialist_in_output"

    return "match" if actual == expected else "mismatch"


# ── Aggregators ──────────────────────────────────────────────────────

def built_in_scorers() -> list:
    """Return the built-in scorer instances we want to run.

    Skipped built-ins:
      - RetrievalRelevance / RetrievalSufficiency: require a retrieval span
        shape we don't emit today; enable after step 4 (Knowledge Assistant).
      - ToolCallCorrectness: overlaps with our project-specific
        ``optimizer_choice_appropriate``; keeping ToolCallEfficiency for the
        latency signal.
    """
    return [
        Correctness(),
        RetrievalGroundedness(),
        Safety(),
        ToolCallEfficiency(),
    ]


def custom_scorers(judge_model: str = DEFAULT_JUDGE_MODEL) -> list:
    """Return the four project-specific scorers."""
    return [
        recovery_respects_buffers(model=judge_model),
        optimizer_choice_appropriate(model=judge_model),
        cites_live_evidence(model=judge_model),
        incident_matches_specialist,
    ]


def all_scorers(judge_model: str = DEFAULT_JUDGE_MODEL) -> list:
    return built_in_scorers() + custom_scorers(judge_model)
