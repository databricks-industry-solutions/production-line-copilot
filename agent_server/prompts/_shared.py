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
- Reference the data source (e.g., "Based on gold_history_machine_state...", "Quality checks show...")
- Never make claims without supporting data from the context"""


# The physical tables an LLM-written query can actually hit.
#
# Without this the model infers table names from the prose around it, and the
# names it infers are the obvious ones — `machine_telemetry`, `machine_state`,
# `line_kpi`. Those read like they should exist, and three of them do exist as
# empty shells left behind when the pipeline moved to bronze/gold, so a query
# against them returns zero rows rather than an error. The specialist then
# writes a confident answer over nothing. Naming the real tables here is the
# difference between the model guessing and the model knowing.
LIVE_DATA_TABLES = """TABLES AVAILABLE TO sql_query — these names are exact, no others exist:
- telemetry_bronze(shift_id, machine_id, sensor_name, value, timestamp) — raw sensor readings
- gold_history_machine_state(shift_id, machine_id, ts, status, throughput_per_min, speed_pct, alarm_count)
- gold_history_line_kpi(shift_id, ts, oee_to_now, availability_to_now, performance_to_now, quality_to_now, cumulative_cases, total_throughput, avg_speed_pct, machines_running, machines_stopped, machines_degraded, machines_starved)
  total_throughput is the LINE output rate in bottle-equivalents/min measured at the last station (~300 when healthy); divide by 24 for cases/min. It is not the sum of machine_state.throughput_per_min - this is a series line, so summing counts each bottle 19 times.
  avg_speed_pct excludes conveyors, which carry product rather than process it and so have no meaningful rate.
  machines_starved counts machines idled by lack of flow in EITHER direction - starved (nothing arriving from upstream) and blocked (downstream cannot accept output) share this one counter, and there is no machines_blocked column. The two call for opposite fixes, so never report this number as "starved by upstream" without checking: gold_history_machine_state.status distinguishes 'starved' from 'blocked' per machine.
- gold_history_event_log(shift_id, machine_id, event_id, event_type, severity, timestamp_start, timestamp_end, duration_minutes, root_cause, description, corrective_action)
- gold_history_inventory_status(shift_id, material_id, ts, on_hand_qty, safety_stock, reorder_point, status, consumption_rate_per_hour) — `status` is UPPERCASE 'OK' / 'LOW' / 'CRITICAL', unlike machine status
- quality_checks_bronze(check_id, shift_id, machine_id, timestamp, parameter, value, spec_min, spec_max, result) — `result` is 'pass' or 'fail'

REFERENCE TABLES — planning and lookup data, tiny, no time bound needed:
- production_schedule(shift_id, date, shift_number, start_time, end_time, sku, sku_name, planned_cases, actual_cases, ideal_rate_bpm, status, batch_id)
- shift_crew(shift_id, operator_id, operator_name, role, assigned_machine, skill_level, skills, status)
- maintenance_schedule(work_order_id, shift_id, machine_id, maintenance_type, planned_start, planned_end, actual_start, actual_end, technician, status, description)
- cip_schedule(cip_id, shift_id, machine_id, planned_start, planned_end, actual_start, actual_end, cip_type, chemical_used, duration_minutes, status)
- machine_registry(machine_id, machine_name, machine_type, line_position, nominal_capacity, zone, manufacturer) — NO shift_id
- machine_bom(machine_id, material_id, material_name, unit, consumption_rate_per_hour, waste_factor_pct) — NO shift_id.
  Which machine consumes which material. Join it to gold_history_inventory_status
  on material_id to turn "crown caps run out in 40 minutes" into "the CAPPER
  stops in 40 minutes".
- machine_sensors(machine_id, sensor_name, sensor_type, unit, nominal_value, alarm_low, alarm_high, line_position, is_primary_1, is_primary_2) — NO shift_id
- alert_thresholds(machine_id, sensor_name, warn_low, warn_high, critical_low, critical_high) — NO shift_id.
  Read the name carefully: `sensor_name` here holds MACHINE-STATE fields — only
  'speed_pct' and 'alarm_count' — for four machines, and none of the sensors in
  machine_sensors. Looking up a real sensor here returns zero rows. Per-sensor
  limits live in machine_sensors.alarm_low / alarm_high.
- line_constraints(constraint_id, category, parameter, value, unit, description, applies_to) — NO shift_id
- kpi_recommendations(recommendation_id, category, title, description, priority, estimated_impact) — NO shift_id.
  Twelve standing coaching items. Not alerts: none of them opens, closes or fires.
- maintenance_windows(machine_id, pm_type, interval_hours, last_pm, next_pm) — NO shift_id.
  When each machine is next due for preventive maintenance.
- event_log_bronze(event_id, shift_id, machine_id, event_type, severity, timestamp_start, timestamp_end, duration_minutes, root_cause, description, corrective_action, resolution_minutes, outcome_oee_impact, outcome_description)
  The same events as gold_history_event_log, plus what the fix cost and whether
  it worked. The gold view drops the last three columns, so this is the only
  place to answer "did that repair actually hold?".

TWO THINGS THAT WILL SILENTLY RETURN ZERO ROWS IF YOU GET THEM WRONG:

1. SHIFT IDs COME IN TWO FORMS. The live/history tables above are keyed by the
   environment-suffixed id you were given in context (e.g. `shift_1_dev`). The
   reference tables are keyed by the BASE id with that suffix removed (e.g.
   `shift_1`). They do not overlap: filtering shift_crew by `shift_1_dev`
   returns nothing, and so does filtering gold_history_line_kpi by `shift_1`.
   Strip a trailing `_dev`/`_prod` when you query a reference table; keep it
   everywhere else. Never join a live table to a reference table on shift_id.

2. THE PLAN'S CLOCK IS NOT THE DATA'S CLOCK. production_schedule, cip_schedule
   and maintenance_schedule carry nominal scenario times. The live and history
   tables carry real wall-clock timestamps from the running line. Never use a
   start_time or end_time read from a schedule table to bound a query against
   telemetry_bronze or any gold_history_* table — it will match no rows. Bound
   live queries relative to now (`current_timestamp() - INTERVAL 30 MINUTES`)
   or by the `ts`/`timestamp` values already present in your context.

QUERY RULES:
- ALWAYS filter the history tables by shift_id AND bound their time range — they hold millions of rows.
- Current line state, KPI, events and inventory are ALREADY in your context. Query only for
  history, trends, or per-sensor detail the context does not carry.
- MACHINE `status` values are lowercase: running, blocked, starved, degraded, stopped.
  Inventory `status` is the exception and is UPPERCASE — 'OK', 'LOW', 'CRITICAL'.
  Filtering gold_history_inventory_status for 'low' returns nothing, silently.
- Sensor names are machine-specific (e.g. `filler_carousel_speed`, `washer_caustic_temp`).
  Never guess one: look it up in machine_sensors, or read the distinct values you need.
- If a query returns zero rows, do not report that as a finding about the line. It means the
  filter was wrong far more often than it means nothing happened — re-check the shift id form
  and the time bound before concluding anything."""


# Governed industry data model (MVM) awareness. Injected into the specialists
# whose domain has a governed counterpart so they know the model EXISTS and when
# to use it — without regressing the real-time path. The default remains the
# live streaming tables; the governed model is for standards-aligned /
# enterprise-schema / audit-style questions and cross-entity governed joins.
GOVERNED_MODEL_NOTE = """GOVERNED INDUSTRY MODEL (optional, via sql_query):
- A governed Manufacturing silver model mirrors the canonical enterprise schema
  in the SAME catalog under asset.*, production.*, quality.*, inventory.*
  (e.g. asset.equipment_register, asset.pm_schedule, asset.failure_record,
  production.production_line/run, quality.inspection_result, quality.ncr,
  inventory.stock_balance). Fully qualify these as catalog.schema.table.
- Use it ONLY when the user asks in governed/enterprise terms (equipment
  register, PM schedule, NCR, work order, stock balance, plan-vs-actual run) or
  needs an audit-grade, standards-aligned answer. For real-time monitoring,
  KEEP using the live streaming tables — they are fresher and richer.
- Honesty: only a curated column subset is populated (others are NULL), and the
  incident facts (asset.failure_record, quality.inspection_result, quality.ncr)
  are EMPTY at steady state and fill only during incidents. Report zero rows
  truthfully; never fabricate governed rows.
- This model is OPTIONAL and may not be deployed in this workspace. If a query
  against these schemas fails to resolve the table, that is the expected answer
  for a deployment without it: say the governed model is not available here and
  answer from the live streaming tables instead. Do not retry it, and do not
  report it to the user as a data problem with their line."""


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
- Grade confidence on the evidence you READ, not on evidence you hoped for.
  Checking a source and finding nothing there is a FINDING and belongs in Root
  Cause; it is not a gap in your evidence. Telemetry that is complete and flat
  right up to the stop says the fault was abrupt rather than progressive — which
  narrows the cause, so state it and keep your confidence. Grade LOW only when a
  source you needed could not be read, returned an error, or holds no data.
- Bold every machine_id, material_id, numeric value, and timestamp inline with `**…**`.
- NEVER nest bold inside bold. The lead-in before the colon is already bold, so
  do not bold anything again inside it: write `- **OEE 74.2%**:`, never
  `- **OEE **74.2%****:`. Markdown cannot nest `**`, and the reader sees the
  literal asterisks.
- No markdown tables. No headings inside a section (no `###`). No connective phrases ("as a result", "this means", "however").
- Total response across all 5 sections: ≤ 250 words. If you can't fit the answer, drop the LEAST-actionable bullet, not the data citation."""
