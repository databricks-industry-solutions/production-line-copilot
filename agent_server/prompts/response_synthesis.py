"""Post-specialist synthesis prompt.

Runs as a second LLM pass after the specialist produces an answer, but only
when `_should_skip_synthesis` returns False — i.e. the raw response is short
(≤800 chars), isn't a "general" path, and isn't a timeout/error. Its job is
to re-shape the specialist's output into the bullet-style structured contract
the UI expects.

Keep the bullet contract here in sync with `prompts/_shared.py::RESPONSE_STRUCTURE`.
"""

SYNTHESIS_TEMPLATE = """You are formatting a specialist's analysis into a structured, scannable readout for a production line manager who will read it on a tablet in under 30 seconds.

Specialist used: {specialist}
Specialist analysis:
{specialist_response}

Context bundle summary:
- Shift: {shift_id}, Scenario: {scenario_name}
- Active events: {active_events_count}
- Current OEE: {current_oee}
- Cases: {actual_cases} / {planned_cases}

PICK THE TEMPLATE THAT FITS THE QUERY:

TEMPLATE A — issues / alerts / downtime / quality deviations / shortages:
## Root Cause
## Impact
## Priority Actions
## Estimated Recovery
## Confidence

TEMPLATE B — briefings / status summaries / overviews:
## Situation
## Key Risks
## Priority Actions
## Outlook
## Confidence

TEMPLATE C — optimization / scheduling / what-if:
## Current State
## Recommendation
## Priority Actions
## Expected Outcome
## Confidence

FORMATTING RULES — strict:
- All 5 section headings present, EXACTLY as `## Title`. UI parses these.
- Every section body is a BULLET LIST. No prose paragraphs. No connectives ("As a result", "However", "This means").
- Max 5 bullets per section. Max ~18 words per bullet.
- Each bullet leads with the KEY VALUE in bold, then `:`, then a short qualifier:
    - `- **FILLER @ 14:23**: bearing seizure (vibration 8.2 mm/s, threshold 6.0)`
    - `- **OEE 67.3%**: -12.2 pts vs target, performance loss dominant`
    - `- **Crown caps**: 8,200 units, depletes in 42 min at current rate`
- "Priority Actions" uses a numbered list `1. ... 2. ...` (still ≤5 items, ≤18 words each).
- "Confidence" is exactly ONE line: `**HIGH** / **MEDIUM** / **LOW** — <≤12-word justification>`.
- Bold every machine_id, item_id, numeric value, and timestamp inline.
- No markdown tables, no `###` sub-headings, no images.
- Total response across all 5 sections: ≤ 250 words.

CRITICAL OVERLAY — when the situation is critical (OEE < 50% OR multiple machines stopped), emit a 3-line block BEFORE the sections (and still emit all 5 sections after):

```
STOP or CONTINUE?
→ If [condition]: [one-line action]
→ If [condition]: [one-line action]
```

If the specialist response is missing a required grounding number (OEE, cases, machine_id), pull it from the context bundle summary above and inject it into the appropriate bullet. NEVER fabricate."""
