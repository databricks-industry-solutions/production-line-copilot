"""Every specialist with a governed domain must know the whole governed model.

A specialist's Genie space covers ONE domain, and Genie cannot join across
spaces. Cross-domain governed questions therefore have exactly one route:
``sql_query`` against the four schemas, which share a catalog and so can be
joined in a single query. That route only exists if the prompt says the schemas
are there — otherwise the data is unreachable by either path, silently, and the
specialist answers from the live tables as if the governed model did not exist.

Strategic Advisor was missing the note — cross-domain trends are its whole job,
so it was the worst possible omission — which is the failure this file prevents
from recurring.
"""
from __future__ import annotations

import importlib

import pytest

from agent_server.prompts._shared import GOVERNED_MODEL_NOTE
from tests.agent_server.test_specialist_domain_wiring import EXPECTED_DOMAIN, NO_DOMAIN

# The governed schemas, as named in the note. A specialist that can only reach
# its own Genie space needs all four spelled out to write a cross-schema join.
GOVERNED_SCHEMAS = ["asset.", "production.", "quality.", "inventory."]

# Owns a governed domain, but is deliberately NOT told about the other three.
# Schedule Optimizer is the specialist a user hits mid-disruption: its prompt
# caps it at three tool calls, and its constraints already arrive
# deterministically via load_plan_and_constraints and the schedule_recovery
# MILP, which auto-loads speed limits and CIP windows from line_constraints.
# Four more schemas would buy exploration on the one specialist whose value is
# answering fast. Pinned by test_governed_model_prompt.py; kept here so the two
# tests cannot drift into contradicting each other.
DELIBERATELY_EXCLUDED = {"schedule_optimizer"}

GOVERNED_AND_INFORMED = sorted(set(EXPECTED_DOMAIN) - DELIBERATELY_EXCLUDED)


def _prompt(specialist: str) -> str:
    """The module's system prompt, found by suffix.

    Not derived from the module name: supply_chain_advisor exports
    SUPPLY_CHAIN_SYSTEM, so the convention is close but not mechanical.
    """
    module = importlib.import_module(f"agent_server.prompts.{specialist}")
    names = [n for n in vars(module) if n.endswith("_SYSTEM")]
    assert len(names) == 1, f"expected one *_SYSTEM in {specialist}, found {names}"
    return getattr(module, names[0])


@pytest.mark.parametrize("specialist", GOVERNED_AND_INFORMED)
def test_governed_specialists_are_told_the_model_exists(specialist):
    assert GOVERNED_MODEL_NOTE in _prompt(specialist), (
        f"{specialist} owns a governed domain but its prompt never mentions the "
        "governed model, so the other three schemas are unreachable"
    )


@pytest.mark.parametrize("specialist", GOVERNED_AND_INFORMED)
def test_the_note_names_every_governed_schema(specialist):
    prompt = _prompt(specialist)
    for schema in GOVERNED_SCHEMAS:
        assert schema in prompt, f"{specialist} is not told about {schema}*"


@pytest.mark.parametrize("specialist", sorted(DELIBERATELY_EXCLUDED))
def test_excluded_specialists_stay_excluded(specialist):
    """The exclusion is a decision, so it gets a test rather than an absence."""
    assert GOVERNED_MODEL_NOTE not in _prompt(specialist)


def test_every_governed_specialist_is_either_informed_or_excluded():
    """No specialist may fall between the two lists and go unchecked."""
    assert set(GOVERNED_AND_INFORMED) | DELIBERATELY_EXCLUDED == set(EXPECTED_DOMAIN)
    assert not DELIBERATELY_EXCLUDED & set(GOVERNED_AND_INFORMED)


@pytest.mark.parametrize("specialist", NO_DOMAIN)
def test_streaming_only_specialists_stay_out_of_the_governed_model(specialist):
    """Shift Briefing reports the shift that just happened, from live tables.

    Pointing it at the governed model would trade seconds-fresh rows for
    audit-grade ones it does not need, on the one specialist whose value is
    being fast.
    """
    assert GOVERNED_MODEL_NOTE not in _prompt(specialist)


def test_the_note_directs_cross_domain_work_to_sql():
    """The note is the only place the single-Genie-space limit is explained."""
    assert "sql_query" in GOVERNED_MODEL_NOTE
    assert "Fully qualify" in GOVERNED_MODEL_NOTE
