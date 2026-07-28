"""Alignable base judges + their SME label schemas.

MemAlign pairs an LLM judge's scores with SME feedback on the SAME traces by
matching the **label-schema name to the judge name**. This module is the single
source of truth for that invariant: label schemas are DERIVED from the judge
objects (name + categories), so the two can never drift.

We align only the three subjective LLM judges — the deterministic scorers
(``incident_matches_specialist``, ``response_avoids_forbidden``) and the
ground-truth built-ins (``Correctness``) need no alignment.
"""
from __future__ import annotations

import typing

from evaluations.judges import (
    DEFAULT_JUDGE_MODEL,
    cites_live_evidence,
    optimizer_choice_appropriate,
    recovery_respects_buffers,
)

# The judges we align to SME preference, in registration order.
ALIGNABLE_JUDGE_FACTORIES = (
    recovery_respects_buffers,
    optimizer_choice_appropriate,
    cites_live_evidence,
)

# Short, human-facing instructions shown to SMEs in the Review App. Kept
# separate from the judge's own (template-variable) instructions so raters see
# plain English, not ``{{ outputs }}`` placeholders. Keys MUST be judge names.
_SME_INSTRUCTIONS: dict[str, str] = {
    "recovery_respects_buffers": (
        "When the assistant recommends changing a line speed, machine setpoint, "
        "or throughput target, did it justify the change using upstream OR "
        "downstream buffer state (buffer level, starvation/blocking risk, "
        "surrounding machine states)?\n"
        "  - respects: speed/setpoint change is justified by buffer state\n"
        "  - violates: speed/setpoint change with no buffer reasoning\n"
        "  - not_applicable: no speed/setpoint change was recommended"
    ),
    "optimizer_choice_appropriate": (
        "Given the user's question, did the assistant call the right optimizer "
        "tool (or correctly call none)?\n"
        "  - appropriate: tool choice matches the question's intent\n"
        "  - wrong_optimizer: a mismatched optimizer was invoked\n"
        "  - no_optimizer_needed: none was needed and none was called"
    ),
    "cites_live_evidence": (
        "For every specific claim about a NAMED machine (FILLER, CAPPER, "
        "PALLETIZER, …) — its status, throughput, speed, alarms, or condition — "
        "was the claim supported by live data in the trace?\n"
        "  - grounded: every per-machine claim is supported by trace evidence\n"
        "  - hallucinated: at least one per-machine claim is unsupported\n"
        "  - not_applicable: the answer makes no per-machine claim"
    ),
}


def make_base_judges(model: str = DEFAULT_JUDGE_MODEL) -> list:
    """Instantiate the alignable base judges (unaligned baseline)."""
    return [factory(model=model) for factory in ALIGNABLE_JUDGE_FACTORIES]


def judge_categories(judge) -> tuple[str, ...]:
    """Return the categorical options of a ``make_judge`` Literal judge.

    Derives the options from ``judge.feedback_value_type`` so a label schema
    built from this can never disagree with what the judge actually emits.
    """
    return tuple(typing.get_args(judge.feedback_value_type))


def label_schema_specs(model: str = DEFAULT_JUDGE_MODEL) -> list[dict]:
    """Pure description of the SME label schemas — no workspace calls.

    Each spec is ``{name, categories, instruction, enable_comment}`` where
    ``name`` equals the judge name (the MemAlign pairing key) and ``categories``
    is derived from the judge itself. Consumed by ``create_label_schemas`` and
    asserted by the tests.
    """
    specs: list[dict] = []
    for judge in make_base_judges(model=model):
        specs.append({
            "name": judge.name,
            "categories": list(judge_categories(judge)),
            "instruction": _SME_INSTRUCTIONS[judge.name],
            "enable_comment": True,
        })
    return specs


def register_base_judges(experiment_id: str, model: str = DEFAULT_JUDGE_MODEL) -> list[str]:
    """Register each base judge to the experiment (creates the record ``align``
    later updates). Returns the registered judge names. Requires a workspace.
    """
    names: list[str] = []
    for judge in make_base_judges(model=model):
        judge.register(experiment_id=experiment_id)
        names.append(judge.name)
    return names


def create_label_schemas(experiment_id: str, model: str = DEFAULT_JUDGE_MODEL) -> list[str]:
    """Create/overwrite one categorical feedback label schema per judge, named
    identically to the judge (the MemAlign invariant). Requires a workspace.
    """
    from mlflow.genai import label_schemas as ls

    created: list[str] = []
    for spec in label_schema_specs(model=model):
        ls.create_label_schema(
            name=spec["name"],
            type="feedback",
            title=spec["name"],
            input=ls.InputCategorical(options=spec["categories"]),
            instruction=spec["instruction"],
            enable_comment=spec["enable_comment"],
            overwrite=True,
            experiment_id=experiment_id,
        )
        created.append(spec["name"])
    return created
