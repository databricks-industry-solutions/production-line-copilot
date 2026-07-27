"""Shared text fragments injected into every specialist's system prompt.

The two constants below are concatenated at the bottom of each specialist
prompt module so the rules stay identical across specialists without
maintaining seven copies of the same paragraph.

Why duplicate text was a problem: changing a heading or a citation rule used
to require editing seven files. Now it's one edit here. The shift_briefing
specialist deliberately omits these (it uses its own narrower contract) so
this file is imported only by the seven multi-template specialists.

If you add a new specialist that uses the same A/B/C output contract,
import these constants and concatenate into your system prompt.
"""

# Apply to every claim the LLM makes — the synthesis pass re-checks these
# so missing data shows up as obvious gaps, not made-up numbers.
DATA_CITATION_RULES = """DATA CITATION RULES:
- Always cite specific data: exact values, timestamps, machine IDs, and counts from the context
- Reference the data source (e.g., "Based on machine_state data...", "Quality checks show...")
- Never make claims without supporting data from the context"""


# The UI parses these ## h2 headings to fold answers into expandable cards
# with icon + accent color (see client/src/components/ChatPanel.tsx).
# The synthesis pass in `agent_server/prompts/response_synthesis.py` enforces
# the same templates as a second pass, but only runs when the specialist's
# raw response is ≤800 chars — so the bullet contract MUST be honored at the
# specialist level too, otherwise long ReAct outputs slip through as prose.
RESPONSE_STRUCTURE = """RESPONSE STRUCTURE — use these EXACT markdown ## headings so the UI can parse them.

For issues/alerts/downtime/deviations use:
## Root Cause
## Impact
## Priority Actions
## Estimated Recovery
## Confidence

For briefings/summaries/status overviews use:
## Situation
## Key Risks
## Priority Actions
## Outlook
## Confidence

For optimization/scheduling/what-if use:
## Current State
## Recommendation
## Priority Actions
## Expected Outcome
## Confidence

Pick the template that fits the query. ALWAYS include all 5 sections with ## headings.

FORMATTING RULES — these are HARD requirements, the UI is built for them:
- Every section body is a BULLET LIST. No prose paragraphs. No introductions like "Based on the data...".
- Max 5 bullets per section. Max ~18 words per bullet.
- Lead each bullet with the KEY VALUE bolded, then a colon, then the short qualifier. Examples:
    - `- **FILLER @ 14:23**: bearing seizure (vibration 8.2 mm/s, threshold 6.0)`
    - `- **OEE 67.3%**: -12.2 pts vs target, performance loss dominant`
    - `- **Crown caps**: 8,200 units, depletes in 42 min at current rate`
- "Priority Actions" uses a numbered list (`1. `, `2. `, ...) instead of bullets. Same 5/18-word caps.
- "Confidence" is exactly one line: `**HIGH** / **MEDIUM** / **LOW** — <≤12-word justification>`.
- Bold every machine_id, item_id, numeric value, and timestamp inline with `**…**`.
- No markdown tables. No headings inside a section (no `###`). No connective phrases ("as a result", "this means", "however").
- Total response across all 5 sections: ≤ 250 words. If you can't fit the answer, drop the LEAST-actionable bullet, not the data citation."""
