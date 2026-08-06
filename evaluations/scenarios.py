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
    # ── Routing corpus v2 ────────────────────────────────────────────────
    # Eight more per route, written the way a line manager actually types:
    # terse, mid-shift, sometimes two questions at once. The point is to test
    # routing under paraphrase and register shifts rather than to add more of
    # the same sentence. Every entry still maps to exactly one specialist, so
    # the deterministic gate keeps its meaning; genuinely ambiguous phrasings
    # belong in a separate ambiguity study, not in a pass/fail routing set.
    # ── downtime_analyst ──
    {"query": "case packer keeps tripping, what's the root cause?", "context_events": [{"machine_id": "CASE_PACKER", "event_type": "major_stop"}], "expected_specialist": "downtime_analyst"},
    {"query": "Line's been down 12 minutes. Talk me through why.", "context_events": [], "expected_specialist": "downtime_analyst"},
    {"query": "What's driving our unplanned stops today?", "context_events": [], "expected_specialist": "downtime_analyst"},
    {"query": "shrink wrapper faulted again - third time this shift", "context_events": [{"machine_id": "SHRINK_WRAPPER", "event_type": "major_stop"}], "expected_specialist": "downtime_analyst"},
    {"query": "Which machine has cost us the most downtime minutes today?", "context_events": [], "expected_specialist": "downtime_analyst"},
    {"query": "capper stopped - is this the same fault as this morning?", "context_events": [{"machine_id": "CAPPER", "event_type": "major_stop"}], "expected_specialist": "downtime_analyst"},
    {"query": "Give me the Pareto of stop reasons for this shift", "context_events": [], "expected_specialist": "downtime_analyst"},
    {"query": "why do we keep losing the filler", "context_events": [{"machine_id": "FILLER", "event_type": "major_stop"}], "expected_specialist": "downtime_analyst"},
    # ── quality_specialist ──
    {"query": "fill weights drifting low on the filler, what do I do?", "context_events": [], "expected_specialist": "quality_specialist"},
    {"query": "Are we within spec on net content right now?", "context_events": [], "expected_specialist": "quality_specialist"},
    {"query": "torque readings on the capper look off", "context_events": [{"machine_id": "CAPPER", "event_type": "quality_deviation"}], "expected_specialist": "quality_specialist"},
    {"query": "Do I need to quarantine the last pallet?", "context_events": [], "expected_specialist": "quality_specialist"},
    {"query": "label placement is out of tolerance, how bad is it?", "context_events": [{"machine_id": "LABELLER", "event_type": "quality_deviation"}], "expected_specialist": "quality_specialist"},
    {"query": "What's our first pass yield this shift?", "context_events": [], "expected_specialist": "quality_specialist"},
    {"query": "checkweigher rejects climbing - trend or blip?", "context_events": [{"machine_id": "CHECKWEIGHER", "event_type": "quality_deviation"}], "expected_specialist": "quality_specialist"},
    {"query": "any quality risk I should flag to QA before handover?", "context_events": [], "expected_specialist": "quality_specialist"},
    # ── supply_chain_advisor ──
    {"query": "how many caps left?", "context_events": [], "expected_specialist": "supply_chain_advisor"},
    {"query": "Will materials last to end of shift?", "context_events": [], "expected_specialist": "supply_chain_advisor"},
    {"query": "which component runs out first at current consumption?", "context_events": [], "expected_specialist": "supply_chain_advisor"},
    {"query": "Do I need to chase the warehouse for shrink film?", "context_events": [], "expected_specialist": "supply_chain_advisor"},
    {"query": "consumption rate on preforms vs what we have staged", "context_events": [], "expected_specialist": "supply_chain_advisor"},
    {"query": "Are any materials below safety stock?", "context_events": [], "expected_specialist": "supply_chain_advisor"},
    {"query": "when's the next pallet of cartons due in", "context_events": [], "expected_specialist": "supply_chain_advisor"},
    {"query": "risk of a material stockout in the next two hours?", "context_events": [], "expected_specialist": "supply_chain_advisor"},
    # ── oee_coach ──
    {"query": "how's OEE looking", "context_events": [], "expected_specialist": "oee_coach"},
    {"query": "Which of the three OEE factors is hurting us most?", "context_events": [], "expected_specialist": "oee_coach"},
    {"query": "availability vs performance - where's the bigger gap?", "context_events": [], "expected_specialist": "oee_coach"},
    {"query": "What would move OEE the most in the next hour?", "context_events": [], "expected_specialist": "oee_coach"},
    {"query": "are we better or worse than yesterday on OEE?", "context_events": [], "expected_specialist": "oee_coach"},
    {"query": "quantify our performance losses in cases", "context_events": [], "expected_specialist": "oee_coach"},
    {"query": "what's our biggest single loss bucket right now", "context_events": [], "expected_specialist": "oee_coach"},
    {"query": "Explain why OEE dropped since the start of the shift", "context_events": [], "expected_specialist": "oee_coach"},
    # ── schedule_optimizer ──
    {"query": "can we still hit target?", "context_events": [], "expected_specialist": "schedule_optimizer"},
    {"query": "How much overtime to close the plan gap?", "context_events": [], "expected_specialist": "schedule_optimizer"},
    {"query": "should I run the line faster to catch up?", "context_events": [], "expected_specialist": "schedule_optimizer"},
    {"query": "Reschedule the remaining orders for me", "context_events": [], "expected_specialist": "schedule_optimizer"},
    {"query": "what speed do I need to make plan by end of shift", "context_events": [], "expected_specialist": "schedule_optimizer"},
    {"query": "Is it worth a changeover now or after the break?", "context_events": [], "expected_specialist": "schedule_optimizer"},
    {"query": "cases still to run vs time left - are we short?", "context_events": [], "expected_specialist": "schedule_optimizer"},
    {"query": "best sequence for the rest of today's orders?", "context_events": [], "expected_specialist": "schedule_optimizer"},
    # ── maintenance_predictor ──
    {"query": "anything about to fail?", "context_events": [], "expected_specialist": "maintenance_predictor"},
    {"query": "vibration on the capper is creeping up - how long have we got?", "context_events": [], "expected_specialist": "maintenance_predictor"},
    {"query": "Which asset should I service at the next planned stop?", "context_events": [], "expected_specialist": "maintenance_predictor"},
    {"query": "bearing temp trend on the case packer", "context_events": [], "expected_specialist": "maintenance_predictor"},
    {"query": "Predict the next breakdown on this line", "context_events": [], "expected_specialist": "maintenance_predictor"},
    {"query": "is the labeller due for preventive maintenance?", "context_events": [], "expected_specialist": "maintenance_predictor"},
    {"query": "what maintenance would prevent the most downtime next week", "context_events": [], "expected_specialist": "maintenance_predictor"},
    {"query": "motor current climbing on the filler, failure risk?", "context_events": [], "expected_specialist": "maintenance_predictor"},
    # ── strategic_advisor ──
    {"query": "Where should we invest capex next year?", "context_events": [], "expected_specialist": "strategic_advisor"},
    {"query": "Build the business case for a new labeller", "context_events": [], "expected_specialist": "strategic_advisor"},
    {"query": "what's our biggest structural constraint on this line?", "context_events": [], "expected_specialist": "strategic_advisor"},
    {"query": "How do we get to 85% OEE over the next two quarters?", "context_events": [], "expected_specialist": "strategic_advisor"},
    {"query": "ROI on upgrading the case packer", "context_events": [], "expected_specialist": "strategic_advisor"},
    {"query": "Long term plan to cut changeover losses", "context_events": [], "expected_specialist": "strategic_advisor"},
    {"query": "benchmark our line against industry OEE", "context_events": [], "expected_specialist": "strategic_advisor"},
    {"query": "what should go in next year's improvement roadmap", "context_events": [], "expected_specialist": "strategic_advisor"},
    # ── shift_briefing ──
    {"query": "brief me", "context_events": [], "expected_specialist": "shift_briefing"},
    {"query": "What do I need to know before I take over?", "context_events": [], "expected_specialist": "shift_briefing"},
    {"query": "Write the handover for the night crew", "context_events": [], "expected_specialist": "shift_briefing"},
    {"query": "give me the shift summary so far", "context_events": [], "expected_specialist": "shift_briefing"},
    {"query": "catch me up, I've been off the floor an hour", "context_events": [], "expected_specialist": "shift_briefing"},
    {"query": "What are the open issues for the next shift?", "context_events": [], "expected_specialist": "shift_briefing"},
    {"query": "end of shift report please", "context_events": [], "expected_specialist": "shift_briefing"},
    {"query": "top three things to watch this shift", "context_events": [], "expected_specialist": "shift_briefing"},
    # ── general ──
    {"query": "hi", "context_events": [], "expected_specialist": "general"},
    {"query": "who built you?", "context_events": [], "expected_specialist": "general"},
    {"query": "What data can you actually see?", "context_events": [], "expected_specialist": "general"},
    {"query": "what's the weather tomorrow?", "context_events": [], "expected_specialist": "general"},
    {"query": "never mind", "context_events": [], "expected_specialist": "general"},
    {"query": "can you explain what OEE stands for?", "context_events": [], "expected_specialist": "general"},
    {"query": "good morning", "context_events": [], "expected_specialist": "general"},
    {"query": "are you able to change machine setpoints yourself?", "context_events": [], "expected_specialist": "general"},
]


GOLDEN_RESPONSES: list[dict] = [
    {
        "scenario": "downtime_labeller",
        "shift_id": "shift_2",
        "query": "Analyze the labeller downtime root cause and impact",
        # "root cause" and "minutes" are the load-bearing ones: the first three
        # words all appear in the question, so a model that merely restated it
        # used to satisfy this record in full.
        "must_contain_keywords": ["labeller", "downtime", "root cause", "minutes"],
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
        # A trend answer has to carry a number; "%" is the cheapest proof that
        # the model queried rather than paraphrased the question back.
        "must_contain_keywords": ["oee", "trend", "%"],
        "must_not_contain": ["I don't know"],
    },
]
