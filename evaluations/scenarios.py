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
    # ── Expanded coverage: additional paraphrases / edge cases per specialist ──
    # These broaden routing robustness. Each maps unambiguously to one
    # specialist; keep new entries clearly routable so the deterministic
    # incident_matches_specialist gate stays meaningful.
    {
        "query": "The capper jammed and the line stopped, what caused it?",
        "context_events": [{"machine_id": "CAPPER", "event_type": "major_stop"}],
        "expected_specialist": "downtime_analyst",
    },
    {
        "query": "Why did the filler go down about twenty minutes ago?",
        "context_events": [{"machine_id": "FILLER", "event_type": "major_stop"}],
        "expected_specialist": "downtime_analyst",
    },
    {
        "query": "Reject rate on the checkweigher spiked this hour, what is going on?",
        "context_events": [{"machine_id": "CHECKWEIGHER", "event_type": "quality_deviation"}],
        "expected_specialist": "quality_specialist",
    },
    {
        "query": "We are seeing underweight bottles, should we place a quality hold?",
        "context_events": [],
        "expected_specialist": "quality_specialist",
    },
    {
        "query": "How much bottle stock is left before we hit a stockout?",
        "context_events": [],
        "expected_specialist": "supply_chain_advisor",
    },
    {
        "query": "When is the next delivery of labels arriving?",
        "context_events": [],
        "expected_specialist": "supply_chain_advisor",
    },
    {
        "query": "Break down our OEE losses for this shift into availability, performance, and quality",
        "context_events": [],
        "expected_specialist": "oee_coach",
    },
    {
        "query": "Our performance rate dropped, how do we recover OEE before end of shift?",
        "context_events": [],
        "expected_specialist": "oee_coach",
    },
    {
        "query": "A rush order just came in, can we fit it before end of shift?",
        "context_events": [],
        "expected_specialist": "schedule_optimizer",
    },
    {
        "query": "How do we catch up the backlog after the changeover?",
        "context_events": [],
        "expected_specialist": "schedule_optimizer",
    },
    {
        "query": "The capper bearing vibration is climbing, how long until failure?",
        "context_events": [],
        "expected_specialist": "maintenance_predictor",
    },
    {
        "query": "Estimate the remaining useful life on the filler pump",
        "context_events": [],
        "expected_specialist": "maintenance_predictor",
    },
    {
        "query": "What capex investments would most improve line reliability next year?",
        "context_events": [],
        "expected_specialist": "strategic_advisor",
    },
    {
        "query": "Give me a strategic plan to reach 90% OEE over the next two quarters",
        "context_events": [],
        "expected_specialist": "strategic_advisor",
    },
    {
        "query": "Summarize the last hour for the incoming shift lead",
        "context_events": [],
        "expected_specialist": "shift_briefing",
    },
    {
        "query": "What can you help me with?",
        "context_events": [],
        "expected_specialist": "general",
    },
    {
        "query": "Thanks, that is all for now",
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
    {
        "scenario": "oee_loss_breakdown",
        "shift_id": "shift_1",
        "query": "What is driving our OEE loss this shift?",
        "must_contain_keywords": ["oee", "availability", "performance"],
        "must_not_contain": ["I don't know", "no data available"],
    },
    {
        "scenario": "maintenance_rul",
        "shift_id": "shift_3",
        "query": "Estimate remaining useful life for the filler pump given the vibration trend",
        "must_contain_keywords": ["filler", "maintenance", "remaining useful life"],
        "must_not_contain": ["I don't know"],
    },
    {
        "scenario": "schedule_recovery",
        "shift_id": "shift_2",
        "query": "Can we still meet the shift target after the changeover delay?",
        "must_contain_keywords": ["shift target", "recovery", "overtime"],
        "must_not_contain": ["no data available"],
    },
    {
        # Ad-hoc analytical question — exercises the Genie-backed data path
        # ("ask the line"). The agent should ground a trend answer in queried
        # data rather than guess.
        "scenario": "genie_oee_trend",
        "shift_id": "shift_1",
        "query": "What is the OEE trend over the last 5 shifts?",
        "must_contain_keywords": ["oee", "trend", "shift"],
        "must_not_contain": ["I don't know"],
    },
]
