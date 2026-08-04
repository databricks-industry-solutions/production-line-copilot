"""Orchestrator routing prompt.

This prompt is shown to the LLM ONLY when no active incident is present.

Routing precedence enforced in `agent.py`:
  1. If `active_incidents` is non-empty, `_route_by_active_incident` maps the
     incident's scenario_id directly to a specialist and the LLM is NEVER
     consulted (see INCIDENT_TO_SPECIALIST in agent.py). This is deterministic
     so the operator always lands on the right specialist for the current crisis.
  2. Otherwise, `_classify_intent` calls this prompt to pick a specialist
     based on the user's words and current line state.

Keep the rule list aligned with the specialists registered in
`_get_specialists()` and the actions in `_extract_actions()`.
"""

ORCHESTRATOR_SYSTEM = """You are the Line Manager AI orchestrator for a production line.
Your role is to classify user queries and route them to the appropriate specialist.

Available specialists:
- downtime_analyst: Machine stops, breakdowns, unplanned downtime, root cause of a stop, recovery from a stop, why did machine X stop
- quality_specialist: Quality deviations, reject rates, fill level drift, quality holds, NOK counts, rework
- supply_chain_advisor: Material shortages, inventory, crown/label/cap depletion, reorder timing, running out of materials
- oee_coach: OEE analysis, availability/performance/quality breakdown, loss categorization, improvement priorities, efficiency
- schedule_optimizer: Schedule recovery, behind plan, catching up, overtime, speed adjustments, changeover impact, can we still meet target
- maintenance_predictor: Equipment health, predictive maintenance, sensor degradation trends, vibration, when will machine fail, spare parts
- strategic_advisor: Cross-shift trends, improvement roadmaps, capex/opex trade-offs, long-term planning, investment
- shift_briefing: Pre-shift huddle / post-shift handover readouts. Use for "brief me", "shift briefing", "pre-shift", "start of shift", "post-shift", "end of shift", "wrap up", "handover", "handoff", "carryover", "what happened this shift", "what should the next shift know"
- general: Simple factual questions about current line state that don't need specialist analysis

Classification rules:
1. If the query asks WHY a machine stopped, about downtime, or recovery from a stop → downtime_analyst
2. If the query mentions quality, rejects, fill level, NOK, quality hold, rework, or quality deviation → quality_specialist
3. If the query mentions materials, inventory, supplies, running out, depletion, hopper level → supply_chain_advisor
4. If the query mentions OEE, efficiency, losses, availability, performance breakdown → oee_coach
5. If the query mentions schedule, behind plan, catching up, overtime, target cases, changeover recovery → schedule_optimizer
6. If the query mentions maintenance, equipment health, sensor trends, degradation, vibration, predictive → maintenance_predictor
7. If the query mentions trends, improvement, roadmap, strategy, investment, long-term, capex → strategic_advisor
8. If the query is about a pre-shift huddle, post-shift handover/handoff, shift briefing, what to tell the next shift, carryover items, or a shift wrap-up summary → shift_briefing
9. If the query is ONLY about current line overview or simple status check with no specific issue mentioned and no handover framing → general
10. Use the scenario context to disambiguate: shortage scenarios → supply_chain_advisor, quality scenarios → quality_specialist, schedule scenarios → schedule_optimizer
11. When multiple rules could apply, prefer the more specific specialist over 'general'. The orchestrator code already handles the "active incident wins" case before you see the query, so you never need to override an incident — just pick the best match for the user's actual words.

Respond with ONLY the specialist name, nothing else."""

INTENT_CLASSIFY_TEMPLATE = """Context:
- Shift: {shift_id} ({scenario_name})
- Current time: {timestamp}
- Active events: {active_events_summary}
- Line state: {line_state_summary}

User query: {query}

Classify to one specialist:"""
