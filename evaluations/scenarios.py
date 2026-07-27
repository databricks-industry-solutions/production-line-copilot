"""Seed data for the eval dataset — no pytest dependency.

Extracted from ``tests/agent_server/test_agent_evaluation.py`` so both the
existing unit tests AND the MLflow eval harness can consume it. Any addition
here shows up in the next eval-dataset build (``build_seed_records``).

Design notes:
  - ``INTENT_SCENARIOS`` exercises routing: one query per specialist plus one
    that should land on ``general``. Judge = ``incident_matches_specialist``
    (deterministic).
  - ``GOLDEN_RESPONSES`` exercises answer quality: a query paired with keywords
    that must appear (and, optionally, phrases that must not). Judge =
    ``Correctness`` via ``expected_facts``.
"""
from __future__ import annotations

INTENT_SCENARIOS: list[dict] = [
    {
        "query": "The labeller stopped, label misalignment due to adhesive failure",
        "context_events": [{"machine_id": "LABELLER", "event_type": "major_stop"}],
        "expected_specialist": "downtime_analyst",
    },
    {
        "query": "Quality checks are failing on the checkweigher, weights are off",
        "context_events": [{"machine_id": "CHECKWEIGHER", "event_type": "quality_deviation"}],
        "expected_specialist": "quality_specialist",
    },
    {
        "query": "We're running low on crown caps, when will we run out?",
        "context_events": [],
        "expected_specialist": "supply_chain_advisor",
    },
    {
        "query": "What is the current OEE and how can we improve it?",
        "context_events": [],
        "expected_specialist": "oee_coach",
    },
    {
        "query": "Can we still meet the shift target? How much overtime do we need?",
        "context_events": [],
        "expected_specialist": "schedule_optimizer",
    },
    {
        "query": "The filler motor current is trending up, should we schedule maintenance?",
        "context_events": [],
        "expected_specialist": "maintenance_predictor",
    },
    {
        "query": "Quarterly OEE roadmap and capex priorities for the next year",
        "context_events": [],
        "expected_specialist": "strategic_advisor",
    },
    {
        "query": "Give me a pre-shift briefing for the crew coming on now",
        "context_events": [],
        "expected_specialist": "shift_briefing",
    },
    {
        "query": "Wrap up this shift and prep the handover note for the next crew",
        "context_events": [],
        "expected_specialist": "shift_briefing",
    },
    {
        "query": "Hello, how are you?",
        "context_events": [],
        "expected_specialist": "general",
    },
]


GOLDEN_RESPONSES: list[dict] = [
    {
        "scenario": "downtime_labeller",
        "shift_id": "shift_2",
        "query": "Analyze the labeller downtime root cause and impact",
        "must_contain_keywords": ["labeller", "downtime", "impact"],
        "must_not_contain": ["I don't know", "no data available"],
    },
    {
        "scenario": "material_shortage",
        "shift_id": "shift_5",
        "query": "Crown caps are running low, what should I do?",
        "must_contain_keywords": ["crown", "material", "supply"],
        "must_not_contain": [],
    },
]
