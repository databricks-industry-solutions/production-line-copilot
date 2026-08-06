"""Curated Genie Space config for the live streaming workspace.

The streaming workspace runs the LIVE streaming demo, whose UC schema
(``<your_catalog>.line_manager_streaming``) is structurally different from
the static seed schema used in the seed-schema (demo workspace) path:

  * OEE lives in the streaming ``gold_history_line_kpi`` table (one row per second), not in
    a pre-aggregated ``oee_gold`` table. Each metric (``oee_to_now``,
    ``availability_to_now``, ``performance_to_now``, ``quality_to_now``) is
    already a percentage 0-100, so DO NOT multiply by 100.
  * Per-machine state lives in the streaming ``gold_history_machine_state`` table — many
    rows per machine over time. To get the *current* state you need
    ``ROW_NUMBER() OVER (PARTITION BY machine_id ORDER BY ts DESC)``.
  * There is **no** ``shift_crew``, ``cip_schedule``, ``consumption_log``, or
    ``production_schedule`` in this schema. Crew + plan come from the YAML
    fixtures the in-app agent loads via the ``load_plan_and_constraints``
    tool, not from Genie.
  * ``scenario_index`` carries only metadata (scenario_name, type, start/end as
    *strings*). It does NOT hold a ``planned_cases`` target.
  * ``maintenance_windows`` is a PM-cadence table keyed by ``machine_id`` only
    (no ``shift_id``). Filter overdue / due PMs by ``next_pm`` timestamp.

This file is a sibling of ``scripts/genie_space_config_mvm.py`` (which targets
the governed industry model). The two configs are kept separate because the
table identifiers, column descriptions, sample questions, and SQL bodies are
all different — sharing a single script would have made every line conditional
on a "mode" flag.

Usage (update an existing space)::

    python scripts/genie_space_config_streaming.py \\
        --profile <your-profile> \\
        --space-id <genie_space_uuid> \\
        --warehouse-id <warehouse_id> \\
        --catalog <uc_catalog>

Usage (create a NEW space — omit --space-id or pass SET_ME)::

    python scripts/genie_space_config_streaming.py \\
        --profile <your-profile> \\
        --warehouse-id <warehouse_id> \\
        --catalog <uc_catalog> \\
        --parent-path /Users/<you>@<co>.com

On create the script POSTs ``/api/2.0/genie/spaces`` (GA Mar 2026) and prints a
``GENIE_SPACE_ID=<id>`` line so a deploy script can capture the new id. With a
real ``--space-id`` it PATCHes that space in place instead.

Run with ``--dry-run`` to inspect the payload at ``/tmp/genie_curated_streaming.json``
without calling the workspace.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

# Defaults are placeholders — override every value at the CLI (--space-id,
# --profile, --warehouse-id, --catalog, --schema) or via environment vars.
# Hardcoded workspace identifiers were removed during the public-release
# scrub; the script will refuse to apply with placeholders in place.
DEFAULT_SPACE_ID = "SET_ME"
DEFAULT_PROFILE = "SET_ME"
DEFAULT_WAREHOUSE_ID = "SET_ME"
DEFAULT_CATALOG = "SET_ME"
DEFAULT_SCHEMA = "line_manager_streaming"
TEMPLATE_FQ_PREFIX = "__CAT__.__SCH__"


# ── Tables registered in the space ─────────────────────────────────────────

tables = [
    {
        "identifier": f"{TEMPLATE_FQ_PREFIX}.gold_history_line_kpi",
        "description": [
            "Streaming roll-up KPI table — one row per second per shift_id. "
            "Latest row IS the current state; older rows are the historical "
            "running view. To get the latest per shift, use "
            "ROW_NUMBER() OVER (PARTITION BY shift_id ORDER BY ts DESC)."
        ],
        "column_configs": [
            {"column_name": "shift_id", "description": ["Shift identifier as written by the running line, e.g. 'shift_1_dev' in dev and 'shift_1' in prod. There is no 'live' shift. When in doubt do not filter on it; bound on ts instead."]},
            {"column_name": "ts", "description": ["UTC timestamp the KPI row was emitted (~1 row per second)."]},
            {"column_name": "machines_running", "description": ["Count of machines currently in 'running' status."]},
            {"column_name": "machines_stopped", "description": ["Count of machines currently in 'stopped' status (unplanned downtime)."]},
            {"column_name": "machines_degraded", "description": ["Count of machines in 'degraded' status (running below ideal speed)."]},
            {"column_name": "machines_starved", "description": [
                "Count of machines idled by lack of flow, in either direction: starved "
                "(nothing arriving from upstream) AND blocked (downstream cannot accept "
                "output) both increment this one counter. There is no machines_blocked "
                "column. The two have opposite remedies, so do not attribute this number "
                "to upstream stoppage; use machine_state.status to split 'starved' from "
                "'blocked' per machine."]},
            {"column_name": "total_throughput", "description": ["Line output rate right now, in bottle-equivalents per minute, measured at the last station. This is a series line, so it is NOT the sum of the per-machine rates in machine_state - every bottle passes through all 19 machines. Divide by 24 for cases per minute."]},
            {"column_name": "avg_speed_pct", "description": ["Average speed percentage across all machines, 0-100."]},
            {"column_name": "availability_to_now", "description": ["Availability % from start of shift, 0-100. Already a percentage; DO NOT multiply by 100."]},
            {"column_name": "performance_to_now", "description": ["Performance % from start of shift, 0-100. Already a percentage."]},
            {"column_name": "quality_to_now", "description": ["Quality % from start of shift, 0-100. Already a percentage."]},
            {"column_name": "oee_to_now", "description": ["OEE % from start of shift, 0-100 (e.g. 74.5 means 74.5%). Already a percentage; DO NOT multiply by 100."], "synonyms": ["OEE", "overall efficiency"]},
            {"column_name": "cumulative_cases", "description": ["Total cases produced since shift start."]},
        ],
    },
    {
        "identifier": f"{TEMPLATE_FQ_PREFIX}.gold_history_machine_state",
        "description": [
            "Streaming per-machine state, ~1 row per machine per second. To get "
            "the CURRENT status of each machine, take the latest row per "
            "machine_id: "
            "ROW_NUMBER() OVER (PARTITION BY machine_id ORDER BY ts DESC) = 1."
        ],
        "column_configs": [
            {"column_name": "shift_id", "description": ["Shift identifier."]},
            {"column_name": "machine_id", "description": ["Machine identifier (FILLER, CAPPER, etc.)."]},
            {"column_name": "ts", "description": ["UTC snapshot timestamp."]},
            {"column_name": "status", "description": ["One of: running, blocked, starved, degraded, stopped (lowercase)."], "synonyms": ["state"]},
            {"column_name": "throughput_per_min", "description": ["Bottle-equivalents this machine passed in the most recent minute (~300 when healthy). Divide by 24 for cases/min. Not cases."]},
            {"column_name": "speed_pct", "description": ["Current speed as percentage of nominal capacity, 0-100."]},
            {"column_name": "alarm_count", "description": ["Active alarms on this machine."]},
        ],
    },
    {
        "identifier": f"{TEMPLATE_FQ_PREFIX}.gold_history_event_log",
        "description": [
            "Incidents and notable events on the line. This log is APPEND-ONLY: "
            "resolving an incident appends a separate row with "
            "event_type = 'resolved' rather than updating the original, so the "
            "original keeps timestamp_end IS NULL forever. Do NOT treat "
            "timestamp_end IS NULL as still open — that counts every incident of "
            "the whole shift. An event is still open only if no 'resolved' row "
            "exists for the same machine at or after its timestamp_start."
        ],
        "column_configs": [
            {"column_name": "event_id", "description": ["Unique event identifier."]},
            {"column_name": "shift_id", "description": ["Shift identifier the event belongs to."]},
            {"column_name": "machine_id", "description": ["Machine where the event occurred."]},
            {"column_name": "event_type", "description": ["Event class (e.g. machine_breakdown, quality_deviation, material_low)."]},
            {"column_name": "severity", "description": ["One of: critical > major > minor > info."]},
            {"column_name": "timestamp_start", "description": ["UTC start of the event."]},
            {"column_name": "timestamp_end", "description": ["UTC end of the event. NULL = still open (carryover at handover)."]},
            {"column_name": "duration_minutes", "description": ["Event duration in minutes (NULL while still open)."]},
            {"column_name": "root_cause", "description": ["Short root-cause tag (e.g. bearing_failure, low_co2)."]},
            {"column_name": "description", "description": ["Long-form description of the event."]},
            {"column_name": "corrective_action", "description": ["What the operator did to resolve it."]},
        ],
    },
    {
        "identifier": f"{TEMPLATE_FQ_PREFIX}.inventory_status",
        "description": ["Material inventory snapshot per shift."],
        "column_configs": [
            {"column_name": "shift_id", "description": ["Shift identifier."]},
            {"column_name": "material_id", "description": ["Material identifier (CROWNS, LABELS, CAPS, etc.)."], "synonyms": ["material", "item"]},
            {"column_name": "on_hand_qty", "description": ["Current on-hand quantity."]},
            {"column_name": "safety_stock", "description": ["Minimum quantity before LOW alert fires."]},
            {"column_name": "reorder_point", "description": ["Quantity at which a reorder must be triggered."]},
            {"column_name": "status", "description": ["OK | LOW | CRITICAL, uppercase. CRITICAL = at or below safety_stock; LOW = between safety_stock and reorder_point; OK = above reorder_point."]},
            {"column_name": "consumption_rate_per_hour", "description": ["Estimated consumption rate based on current line speed."]},
            {"column_name": "feeds_machine_id", "description": ["Machine that consumes this material."]},
            {"column_name": "ts", "description": ["UTC snapshot timestamp."]},
        ],
    },
    {
        "identifier": f"{TEMPLATE_FQ_PREFIX}.quality_checks_bronze",
        "description": ["Quality samples taken throughout the shift."],
        "column_configs": [
            {"column_name": "check_id", "description": ["Sample identifier."]},
            {"column_name": "shift_id", "description": ["Shift identifier."]},
            {"column_name": "machine_id", "description": ["Machine sampled."]},
            {"column_name": "timestamp", "description": ["UTC sample timestamp."]},
            {"column_name": "parameter", "description": ["What was measured (fill_level, cap_torque, etc.)."]},
            {"column_name": "value", "description": ["Measured value."]},
            {"column_name": "spec_min", "description": ["Lower spec limit."]},
            {"column_name": "spec_max", "description": ["Upper spec limit."]},
            {"column_name": "result", "description": ["'pass' or 'fail' (lowercase). A 'fail' row is a quality reject."]},
        ],
    },
    {
        "identifier": f"{TEMPLATE_FQ_PREFIX}.maintenance_windows",
        "description": [
            "Preventive maintenance cadence per machine. NO shift_id column — "
            "use next_pm timestamp to find PMs due/overdue. A PM is OVERDUE "
            "when next_pm < CURRENT_TIMESTAMP(); DUE SOON when next_pm "
            "is within the next ~8h."
        ],
        "column_configs": [
            {"column_name": "machine_id", "description": ["Machine identifier."]},
            {"column_name": "pm_type", "description": ["Preventive maintenance type (preventive, inspection, lubrication)."]},
            {"column_name": "interval_hours", "description": ["Interval in hours between PMs."]},
            {"column_name": "last_pm", "description": ["UTC timestamp of the last completed PM."]},
            {"column_name": "next_pm", "description": ["UTC timestamp of the next scheduled PM."]},
        ],
    },
    {
        "identifier": f"{TEMPLATE_FQ_PREFIX}.scenario_index",
        "description": [
            "Metadata about each scenario / shift available in the demo. Note: "
            "shift_start and shift_end are STRINGS, not timestamps. There is "
            "NO planned_cases / sku_name column here — for production targets, "
            "the agent uses the YAML fixtures via load_plan_and_constraints, "
            "not this table."
        ],
        "column_configs": [
            {"column_name": "shift_id", "description": ["Shift identifier as written by the running line (e.g. 'shift_1_dev'). There is no 'live' shift."]},
            {"column_name": "scenario_name", "description": ["Human-readable scenario name."]},
            {"column_name": "scenario_type", "description": ["E.g. realtime, replay, what-if."]},
            {"column_name": "shift_start", "description": ["Shift start as ISO-8601 string."]},
            {"column_name": "shift_end", "description": ["Shift end as ISO-8601 string."]},
            {"column_name": "description", "description": ["Free-form scenario description."]},
        ],
    },
    {
        "identifier": f"{TEMPLATE_FQ_PREFIX}.machine_registry",
        "description": ["Static metadata about each machine on the line."],
        "column_configs": [
            {"column_name": "machine_id", "description": ["Machine identifier."]},
            {"column_name": "machine_name", "description": ["Human-readable name."]},
            {"column_name": "machine_type", "description": ["Functional type (filler, capper, depalletizer, ...)."]},
            {"column_name": "line_position", "description": ["Position in the line, 1 = upstream."]},
            {"column_name": "nominal_capacity", "description": ["Rated bottle-equivalents per minute (420 for most machines). The case packer's 17.5 cases/min is scaled to the same bottle basis so it does not appear to throttle the line."]},
            {"column_name": "zone", "description": ["Physical zone of the line."]},
            {"column_name": "manufacturer", "description": ["OEM."]},
        ],
    },
    {
        "identifier": f"{TEMPLATE_FQ_PREFIX}.kpi_recommendations",
        "description": [
            "Twelve static coaching recommendations. Not alerts and not tickets: "
            "they are always present, have no shift_id, and none of them opens "
            "or closes. Nothing here is triggered by a threshold."
        ],
        "column_configs": [
            {"column_name": "recommendation_id", "description": ["Stable identifier (R001 ... R012)."]},
            {"column_name": "category", "description": ["cost | quality | throughput | maintenance and similar."]},
            {"column_name": "title", "description": ["Short name of the recommendation."]},
            {"column_name": "description", "description": ["The recommended action, in full."]},
            {"column_name": "priority", "description": ["high | medium | low. There is no severity column."]},
            {"column_name": "estimated_impact", "description": ["Expected effect as text, e.g. '+3% OEE'."]},
        ],
    },
    {
        "identifier": f"{TEMPLATE_FQ_PREFIX}.telemetry_bronze",
        "description": [
            "Long-form per-sensor telemetry (one row per sensor per timestamp). "
            "Heavy table — always filter by shift_id and time window when joining."
        ],
        "column_configs": [
            {"column_name": "shift_id", "description": ["Shift identifier."]},
            {"column_name": "timestamp", "description": ["UTC sample timestamp."]},
            {"column_name": "machine_id", "description": ["Machine the sensor is attached to."]},
            {"column_name": "sensor_name", "description": ["Sensor name (vibration, temperature, pressure, ...)."]},
            {"column_name": "value", "description": ["Sensor reading."]},
        ],
    },
    {
        "identifier": f"{TEMPLATE_FQ_PREFIX}.shift_crew",
        "description": [
            "Operator roster per shift. 12 rows per shift_id with operator_name, "
            "role, assigned_machine, skill_level (1-5), comma-separated skills "
            "string, and status (present, absent, late). The pre-shift briefing "
            "uses this to surface staffing coverage by role/machine."
        ],
        "column_configs": [
            {"column_name": "assigned_machine", "description": ["Primary machine_id this operator covers this shift."]},
            {"column_name": "operator_id", "description": ["Stable operator identifier (e.g. OP001)."]},
            {"column_name": "operator_name", "description": ["Full name of the operator."]},
            {"column_name": "role", "description": ["operator | mechanic | qa | supervisor. There is no 'lead', 'maintenance' or 'quality' role — filtering for those returns nothing."]},
            {"column_name": "shift_id", "description": ["Shift identifier. Matches gold_history_event_log / gold_history_line_kpi / gold_history_machine_state shift_id."]},
            {"column_name": "skill_level", "description": ["1-5 skill rating (5 = expert)."]},
            {"column_name": "skills", "description": ["Comma-separated machine_ids the operator is qualified on."]},
            {"column_name": "status", "description": ["present | absent | late. Drives 'crew coverage' callouts in pre-shift briefings."]},
        ],
    },
    {
        "identifier": f"{TEMPLATE_FQ_PREFIX}.production_schedule",
        "description": [
            "Planned production per shift with SKU, planned_cases, actual_cases, "
            "ideal_rate_bpm, time window and status. The 'plan' the briefing "
            "compares cumulative output against. A shift can have MORE THAN ONE "
            "ROW: a changeover segment carries planned_cases = 0 and status = "
            "'changeover', and the production segment follows it. For the "
            "shift's target, SUM(planned_cases) or take the row with the "
            "largest planned_cases — never an arbitrary single row, or you get "
            "the changeover's zero."
        ],
        "column_configs": [
            {"column_name": "actual_cases", "description": ["Cases actually produced (post-shift). 0 / NULL pre-shift."]},
            {"column_name": "batch_id", "description": ["Production batch identifier."]},
            {"column_name": "date", "description": ["Production date as ISO-8601 string."]},
            {"column_name": "end_time", "description": ["Shift end as ISO-8601 string."]},
            {"column_name": "ideal_rate_bpm", "description": ["Ideal bottles-per-minute for the SKU/format combo."], "synonyms": ["target speed", "design rate"]},
            {"column_name": "planned_cases", "description": ["Target case count for the shift. The number the line manager has to hit."], "synonyms": ["target cases", "plan", "target"]},
            {"column_name": "shift_id", "description": ["Shift identifier."]},
            {"column_name": "shift_number", "description": ["1-7 shift index within the production week."]},
            {"column_name": "sku", "description": ["SKU id (e.g. LAGER_330_GLASS_24)."]},
            {"column_name": "sku_name", "description": ["Human-readable SKU name (e.g. 'Lager 330ml Glass 24-pack')."]},
            {"column_name": "start_time", "description": ["Shift start as ISO-8601 string."]},
            {"column_name": "status", "description": ["planned | in_progress | completed | aborted."]},
        ],
    },
    {
        "identifier": f"{TEMPLATE_FQ_PREFIX}.cip_schedule",
        "description": [
            "Clean-in-place windows scheduled during a shift. Multiple rows per "
            "shift, each scoped to a machine_id with planned_start / planned_end. "
            "Pre-shift briefings list CIP windows so the crew knows when the line "
            "(or a specific machine like FILLER / PASTEURIZER) will be offline."
        ],
        "column_configs": [
            {"column_name": "actual_end", "description": ["Actual end timestamp (NULL until CIP completes)."]},
            {"column_name": "actual_start", "description": ["Actual start timestamp (NULL until CIP begins)."]},
            {"column_name": "chemical_used", "description": ["E.g. 'Caustic + Acid', 'Acid descaler'."]},
            {"column_name": "cip_id", "description": ["Stable CIP identifier (e.g. CIP-001)."]},
            {"column_name": "cip_type", "description": ["full | rinse | sanitise."]},
            {"column_name": "duration_minutes", "description": ["Planned duration in minutes."]},
            {"column_name": "machine_id", "description": ["Machine to be cleaned (FILLER, PASTEURIZER, ...)."]},
            {"column_name": "planned_end", "description": ["Planned end as ISO-8601 string."]},
            {"column_name": "planned_start", "description": ["Planned start as ISO-8601 string."]},
            {"column_name": "shift_id", "description": ["Shift identifier the CIP belongs to."]},
            {"column_name": "status", "description": ["planned | in_progress | completed | aborted."]},
        ],
    },
    {
        "identifier": f"{TEMPLATE_FQ_PREFIX}.scenario_moments",
        "description": [
            "Pre-scripted demo moments (annotations on the timeline). Use "
            "when the user asks 'what happened at <time>' on a replay scenario."
        ],
        "column_configs": [
            {"column_name": "shift_id", "description": ["Shift identifier."]},
            {"column_name": "timestamp", "description": ["Annotation timestamp (string)."]},
            {"column_name": "moment_type", "description": ["E.g. incident, recovery, milestone."]},
            {"column_name": "machine_id", "description": ["Machine involved (may be NULL)."]},
            {"column_name": "description", "description": ["Free-form annotation."]},
            {"column_name": "severity", "description": ["info | minor | major | critical."]},
        ],
    },
]


# ── Sample questions (briefing trio first so they surface in Genie mobile) ─

# Ordering: Genie sorts sample questions by id alphanumerically and surfaces
# the top N as chips on mobile. We deliberately give the four shift-arc
# briefings (pre / mid / post / carryover) the lowest IDs so the line manager
# walking to the line sees them first when they open ProdLine CoPilot on
# the Databricks mobile app — that's the demo story this Genie space is for.
# Operational drill-down questions (latest OEE, stopped machines, etc.) follow.
sample_questions = [
    # ── Shift-arc briefings (top of the chip list on mobile) ──
    {"id": "a2000000000000000000000000000001", "question": ["Brief me for my shift"]},
    {"id": "a2000000000000000000000000000002", "question": ["How are we doing right now?"]},
    {"id": "a2000000000000000000000000000003", "question": ["Wrap up this shift"]},
    {"id": "a2000000000000000000000000000004", "question": ["What's carrying over?"]},
    # ── Operational drill-downs ──
    {"id": "a2000000000000000000000000000010", "question": ["Who is on __SHIFT__ today?"]},
    {"id": "a2000000000000000000000000000011", "question": ["What CIP windows are scheduled this shift?"]},
    {"id": "a2000000000000000000000000000012", "question": ["What is the latest OEE for __SHIFT__ right now?"]},
    {"id": "a2000000000000000000000000000013", "question": ["Which machines are stopped or degraded right now?"]},
    {"id": "a2000000000000000000000000000014", "question": ["Show the top 5 longest events on __SHIFT__ so far"]},
    {"id": "a2000000000000000000000000000015", "question": ["Which materials are below safety stock right now?"]},
    {"id": "a2000000000000000000000000000016", "question": ["What is the quality reject rate per machine on __SHIFT__?"]},
    {"id": "a2000000000000000000000000000017", "question": ["Which PMs are overdue or due in the next 8 hours?"]},
    # ── Ad-hoc analytics (the "ask the line" data path: trends & comparisons) ──
    {"id": "a2000000000000000000000000000020", "question": ["What is the OEE trend over the last 5 shifts?"]},
    {"id": "a2000000000000000000000000000021", "question": ["Which machine has the most downtime this shift?"]},
    {"id": "a2000000000000000000000000000022", "question": ["Compare quality reject rates across machines this shift"]},
]


# ── Example SQLs ───────────────────────────────────────────────────────────

example_question_sqls = [
    {
        "id": "c2000000000000000000000000000001",
        "question": ["What is the latest OEE for __SHIFT__ right now?"],
        "sql": [
            "WITH latest AS (\n",
            "  SELECT *, ROW_NUMBER() OVER (PARTITION BY shift_id ORDER BY ts DESC) AS rn\n",
            "  FROM __CAT__.__SCH__.gold_history_line_kpi\n",
            "  WHERE shift_id = :shift_id\n",
            ")\n",
            "SELECT shift_id,\n",
            "  ROUND(oee_to_now, 1) AS oee_pct,\n",
            "  ROUND(availability_to_now, 1) AS availability_pct,\n",
            "  ROUND(performance_to_now, 1) AS performance_pct,\n",
            "  ROUND(quality_to_now, 1) AS quality_pct,\n",
            "  machines_running, machines_stopped, machines_degraded,\n",
            "  ROUND(total_throughput, 1) AS throughput_per_min,\n",
            "  cumulative_cases, ts\n",
            "FROM latest WHERE rn = 1"
        ],
        "parameters": [{"name": "shift_id", "type_hint": "STRING", "description": ["The shift to query. Default: __SHIFT__, the shift this environment's producer writes. There is no 'live' shift."], "default_value": {"values": ["__SHIFT__"]}}],
        "usage_guidance": [
            "Use for any 'right now' / 'latest' OEE question. The gold_history_line_kpi table emits one row per second; ",
            "ROW_NUMBER() picks the most recent. Columns are already 0-100 percentages — DO NOT multiply by 100."
        ],
    },
    {
        "id": "c2000000000000000000000000000002",
        "question": ["Which machines are stopped or degraded right now?"],
        "sql": [
            "WITH latest AS (\n",
            "  SELECT *, ROW_NUMBER() OVER (PARTITION BY machine_id ORDER BY ts DESC) AS rn\n",
            "  FROM __CAT__.__SCH__.gold_history_machine_state\n",
            "  WHERE shift_id = :shift_id\n",
            ")\n",
            "SELECT machine_id, status, ROUND(speed_pct, 1) AS speed_pct,\n",
            "  ROUND(throughput_per_min, 1) AS throughput_per_min, alarm_count\n",
            "FROM latest\n",
            "WHERE rn = 1 AND status IN ('stopped', 'degraded', 'starved')\n",
            "ORDER BY CASE status WHEN 'stopped' THEN 1 WHEN 'degraded' THEN 2 ELSE 3 END, machine_id"
        ],
        "parameters": [{"name": "shift_id", "type_hint": "STRING", "description": ["The shift to query. Default: __SHIFT__, the shift this environment's producer writes. There is no 'live' shift."], "default_value": {"values": ["__SHIFT__"]}}],
    },
    {
        "id": "c2000000000000000000000000000003",
        "question": ["Show the top 5 longest events on a shift"],
        "sql": [
            "SELECT event_id, machine_id, event_type, severity,\n",
            "  ROUND(duration_minutes, 1) AS duration_min,\n",
            "  root_cause, description,\n",
            "  CASE WHEN timestamp_end IS NULL AND event_type <> 'resolved' THEN 'OPEN' ELSE 'closed' END AS state\n",
            "FROM __CAT__.__SCH__.gold_history_event_log\n",
            "WHERE shift_id = :shift_id\n",
            "ORDER BY duration_minutes DESC NULLS LAST\n",
            "LIMIT 5"
        ],
        "parameters": [{"name": "shift_id", "type_hint": "STRING", "description": ["The shift to query. Default: __SHIFT__, the shift this environment's producer writes. There is no 'live' shift."], "default_value": {"values": ["__SHIFT__"]}}],
    },
    {
        "id": "c2000000000000000000000000000004",
        "question": ["Which materials are below safety stock right now?"],
        "sql": [
            "SELECT material_id, ROUND(on_hand_qty, 1) AS on_hand_qty,\n",
            "  reorder_point, safety_stock, status,\n",
            "  ROUND(consumption_rate_per_hour, 1) AS consumption_per_hour,\n",
            "  CASE WHEN consumption_rate_per_hour > 0\n",
            "       THEN ROUND(on_hand_qty / consumption_rate_per_hour, 1)\n",
            "       ELSE NULL END AS hours_to_depletion,\n",
            "  feeds_machine_id\n",
            "FROM __CAT__.__SCH__.inventory_status\n",
            "WHERE shift_id = :shift_id\n",
            "  AND (status = 'LOW' OR on_hand_qty <= reorder_point)\n",
            "ORDER BY hours_to_depletion NULLS LAST"
        ],
        "parameters": [{"name": "shift_id", "type_hint": "STRING", "description": ["The shift to query. Default: __SHIFT__, the shift this environment's producer writes. There is no 'live' shift."], "default_value": {"values": ["__SHIFT__"]}}],
    },
    {
        "id": "c2000000000000000000000000000005",
        "question": ["What is the quality reject rate per machine?"],
        "sql": [
            "SELECT machine_id,\n",
            "  COUNT(*) AS total_checks,\n",
            "  SUM(CASE WHEN result = 'fail' THEN 1 ELSE 0 END) AS fail_count,\n",
            "  ROUND(100.0 * SUM(CASE WHEN result = 'fail' THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 2) AS reject_pct\n",
            "FROM __CAT__.__SCH__.quality_checks_bronze\n",
            "WHERE shift_id = :shift_id\n",
            "GROUP BY machine_id\n",
            "HAVING COUNT(*) > 0\n",
            "ORDER BY reject_pct DESC"
        ],
        "parameters": [{"name": "shift_id", "type_hint": "STRING", "description": ["The shift to query. Default: __SHIFT__, the shift this environment's producer writes. There is no 'live' shift."], "default_value": {"values": ["__SHIFT__"]}}],
    },
    {
        "id": "c2000000000000000000000000000006",
        "question": ["Which PMs are overdue or due in the next 8 hours?"],
        "sql": [
            "SELECT machine_id, pm_type,\n",
            "  CAST(next_pm AS STRING) AS next_pm,\n",
            "  CASE\n",
            "    WHEN next_pm < CURRENT_TIMESTAMP() THEN 'OVERDUE'\n",
            "    WHEN next_pm <= CURRENT_TIMESTAMP() + INTERVAL 8 HOURS THEN 'DUE_SOON'\n",
            "    ELSE 'future'\n",
            "  END AS pm_status,\n",
            "  ROUND((unix_timestamp(next_pm) - unix_timestamp(CURRENT_TIMESTAMP())) / 3600.0, 1) AS hours_until_due\n",
            "FROM __CAT__.__SCH__.maintenance_windows\n",
            "WHERE next_pm <= CURRENT_TIMESTAMP() + INTERVAL 8 HOURS\n",
            "ORDER BY next_pm"
        ],
        "parameters": [],
    },
    {
        "id": "c2000000000000000000000000000009",
        "question": ["How are we doing right now? Mid-shift status"],
        "sql": [
            "WITH latest_kpi AS (\n",
            "  SELECT *, ROW_NUMBER() OVER (PARTITION BY shift_id ORDER BY ts DESC) AS rn\n",
            "  FROM __CAT__.__SCH__.gold_history_line_kpi WHERE shift_id = :shift_id\n",
            "),\n",
            "kpi_row AS (\n",
            "  SELECT 'kpi_now' AS section, CAST(NULL AS STRING) AS subject,\n",
            "    CONCAT('oee=', ROUND(oee_to_now, 1),\n",
            "           '%, running=', machines_running,\n",
            "           ', stopped=', machines_stopped,\n",
            "           ', degraded=', machines_degraded,\n",
            "           ', cases=', cumulative_cases,\n",
            "           ', throughput=', ROUND(total_throughput, 1), '/min') AS detail\n",
            "  FROM latest_kpi WHERE rn = 1\n",
            "),\n",
            "open_now AS (\n",
            "  SELECT 'open_event' AS section, machine_id AS subject,\n",
            "    CONCAT(event_type, ' / ', severity, ' / ',\n",
            "           ROUND((unix_timestamp(CURRENT_TIMESTAMP()) - unix_timestamp(timestamp_start)) / 60.0, 1),\n",
            "           ' min open') AS detail\n",
            "  FROM __CAT__.__SCH__.gold_history_event_log\n",
            "  WHERE shift_id = :shift_id AND timestamp_end IS NULL AND event_type <> 'resolved' AND NOT EXISTS (SELECT 1 FROM __CAT__.__SCH__.gold_history_event_log r WHERE r.shift_id = gold_history_event_log.shift_id AND r.machine_id = gold_history_event_log.machine_id AND r.event_type = 'resolved' AND r.timestamp_start >= gold_history_event_log.timestamp_start)\n",
            ")\n",
            "SELECT * FROM kpi_row\n",
            "UNION ALL SELECT * FROM open_now"
        ],
        "parameters": [{"name": "shift_id", "type_hint": "STRING", "description": ["The shift to query. Default: __SHIFT__, the shift this environment's producer writes."], "default_value": {"values": ["__SHIFT__"]}}],
        "usage_guidance": [
            "Use for the mid-shift 'How are we doing?' walk-to-the-line check. Returns the latest ",
            "gold_history_line_kpi row (OEE, machine counts, throughput, cumulative cases) plus any currently ",
            "open events with how long they've been open. Tight enough to fit on a phone screen."
        ],
    },
    {
        "id": "c2000000000000000000000000000007",
        "question": ["Who is on shift?"],
        "sql": [
            "SELECT operator_id, operator_name, role, assigned_machine, skill_level, status\n",
            "FROM __CAT__.__SCH__.shift_crew\n",
            "WHERE shift_id = :shift_id\n",
            "ORDER BY CASE role WHEN 'supervisor' THEN 1 WHEN 'mechanic' THEN 2\n",
            "                    WHEN 'qa' THEN 3 WHEN 'operator' THEN 4 ELSE 9 END,\n",
            "         assigned_machine"
        ],
        "parameters": [{"name": "shift_id", "type_hint": "STRING", "description": ["The shift to roster. Default: __SHIFT__."], "default_value": {"values": ["__SHIFT__"]}}],
        "usage_guidance": [
            "Returns the full operator roster for the shift, ordered by role seniority. ",
            "Surface absences and skill-level gaps when the briefing flags 'staffing risk'."
        ],
    },
    {
        "id": "c2000000000000000000000000000008",
        "question": ["What CIP windows are scheduled this shift?"],
        "sql": [
            "SELECT cip_id, machine_id, cip_type, planned_start, planned_end,\n",
            "       duration_minutes, chemical_used, status\n",
            "FROM __CAT__.__SCH__.cip_schedule\n",
            "WHERE shift_id = :shift_id\n",
            "ORDER BY planned_start"
        ],
        "parameters": [{"name": "shift_id", "type_hint": "STRING", "description": ["The shift. Default: __SHIFT__."], "default_value": {"values": ["__SHIFT__"]}}],
        "usage_guidance": [
            "Use when the line manager asks about cleaning windows, downtime windows, or 'when is the FILLER offline today'."
        ],
    },
    {
        "id": "c2000000000000000000000000000010",
        "question": ["Pre-shift briefing for a specific shift"],
        "sql": [
            "WITH plan AS (\n",
            "  SELECT 'plan' AS section, sku_name AS subject,\n",
            "    CONCAT('planned_cases=', planned_cases,\n",
            "           ', ideal_rate_bpm=', ideal_rate_bpm,\n",
            "           ', start=', start_time, ', end=', end_time,\n",
            "           ', status=', status) AS detail\n",
            "  FROM __CAT__.__SCH__.production_schedule\n",
            "  WHERE shift_id = :shift_id\n",
            "),\n",
            "crew_summary AS (\n",
            "  SELECT 'crew' AS section, role AS subject,\n",
            "    CONCAT('present=', SUM(CASE WHEN status = 'present' THEN 1 ELSE 0 END),\n",
            "           ', absent=', SUM(CASE WHEN status = 'absent' THEN 1 ELSE 0 END),\n",
            "           ', late=', SUM(CASE WHEN status = 'late' THEN 1 ELSE 0 END),\n",
            "           ', total=', COUNT(*)) AS detail\n",
            "  FROM __CAT__.__SCH__.shift_crew\n",
            "  WHERE shift_id = :shift_id\n",
            "  GROUP BY role\n",
            "),\n",
            "latest_kpi AS (\n",
            "  SELECT *, ROW_NUMBER() OVER (PARTITION BY shift_id ORDER BY ts DESC) AS rn\n",
            "  FROM __CAT__.__SCH__.gold_history_line_kpi WHERE shift_id = :shift_id\n",
            "),\n",
            "kpi_summary AS (\n",
            "  SELECT 'kpi' AS section, CAST(NULL AS STRING) AS subject,\n",
            "    CONCAT('oee=', ROUND(oee_to_now, 1),\n",
            "           '%, machines_running=', machines_running,\n",
            "           ', stopped=', machines_stopped,\n",
            "           ', degraded=', machines_degraded,\n",
            "           ', cumulative_cases=', cumulative_cases) AS detail\n",
            "  FROM latest_kpi WHERE rn = 1\n",
            "),\n",
            "open_events AS (\n",
            "  SELECT 'open_event' AS section, machine_id AS subject,\n",
            "    CONCAT(event_type, ' / ', severity, ' / ', COALESCE(root_cause, 'unknown_root_cause')) AS detail\n",
            "  FROM __CAT__.__SCH__.gold_history_event_log\n",
            "  WHERE shift_id = :shift_id AND timestamp_end IS NULL AND event_type <> 'resolved' AND NOT EXISTS (SELECT 1 FROM __CAT__.__SCH__.gold_history_event_log r WHERE r.shift_id = gold_history_event_log.shift_id AND r.machine_id = gold_history_event_log.machine_id AND r.event_type = 'resolved' AND r.timestamp_start >= gold_history_event_log.timestamp_start)\n",
            "),\n",
            "cip_windows AS (\n",
            "  SELECT 'cip_window' AS section, machine_id AS subject,\n",
            "    CONCAT(cip_type, ' / planned ', planned_start, '..', planned_end,\n",
            "           ' / ', duration_minutes, ' min / ', status) AS detail\n",
            "  FROM __CAT__.__SCH__.cip_schedule\n",
            "  WHERE shift_id = :shift_id\n",
            "    AND status IN ('planned', 'in_progress')\n",
            "),\n",
            "low_inv AS (\n",
            "  SELECT 'low_inventory' AS section, material_id AS subject,\n",
            "    CONCAT('on_hand=', CAST(ROUND(on_hand_qty, 1) AS STRING),\n",
            "           ' reorder_point=', CAST(reorder_point AS STRING),\n",
            "           ' feeds=', COALESCE(feeds_machine_id, '?')) AS detail\n",
            "  FROM __CAT__.__SCH__.inventory_status\n",
            "  WHERE shift_id = :shift_id\n",
            "    AND (status = 'LOW' OR on_hand_qty <= reorder_point)\n",
            "),\n",
            "pm_due AS (\n",
            "  SELECT 'pm_due_or_overdue' AS section, machine_id AS subject,\n",
            "    CONCAT(pm_type, ' next_pm=', CAST(next_pm AS STRING),\n",
            "           ' (', CASE WHEN next_pm < CURRENT_TIMESTAMP() THEN 'OVERDUE'\n",
            "                       ELSE 'DUE_SOON' END, ')') AS detail\n",
            "  FROM __CAT__.__SCH__.maintenance_windows\n",
            "  WHERE next_pm <= CURRENT_TIMESTAMP() + INTERVAL 8 HOURS\n",
            ")\n",
            "SELECT * FROM plan\n",
            "UNION ALL SELECT * FROM crew_summary\n",
            "UNION ALL SELECT * FROM kpi_summary\n",
            "UNION ALL SELECT * FROM open_events\n",
            "UNION ALL SELECT * FROM cip_windows\n",
            "UNION ALL SELECT * FROM low_inv\n",
            "UNION ALL SELECT * FROM pm_due"
        ],
        "parameters": [{"name": "shift_id", "type_hint": "STRING", "description": ["The shift to query. Default: __SHIFT__, the shift this environment's producer writes."], "default_value": {"values": ["__SHIFT__"]}}],
        "usage_guidance": [
            "Single-result-set pre-shift briefing for the incoming crew. The 'section' column groups: ",
            "'plan' (SKU + target cases), 'crew' (one row per role with present/absent/late counts), ",
            "'kpi' (latest OEE + machine counts), 'open_event' (any unresolved event), 'cip_window' ",
            "(CIPs scheduled during the shift), 'low_inventory', 'pm_due_or_overdue'. ",
            "Set :shift_id to the shift the user names; default __SHIFT__ is what this environment's producer writes."
        ],
    },
    {
        "id": "c2000000000000000000000000000011",
        "question": ["Post-shift summary and handover for a specific shift"],
        "sql": [
            "WITH plan AS (\n",
            "  SELECT 'plan_vs_actual' AS section, sku_name AS subject,\n",
            "    CONCAT('planned=', planned_cases,\n",
            "           ', actual=', COALESCE(CAST(actual_cases AS STRING), '?'),\n",
            "           ', completion_pct=',\n",
            "             ROUND(actual_cases * 100.0 / NULLIF(planned_cases, 0), 1),\n",
            "           '%, status=', status) AS detail\n",
            "  FROM __CAT__.__SCH__.production_schedule\n",
            "  WHERE shift_id = :shift_id\n",
            "),\n",
            "latest_kpi AS (\n",
            "  SELECT *, ROW_NUMBER() OVER (PARTITION BY shift_id ORDER BY ts DESC) AS rn\n",
            "  FROM __CAT__.__SCH__.gold_history_line_kpi WHERE shift_id = :shift_id\n",
            "),\n",
            "kpi_row AS (\n",
            "  SELECT 'kpi' AS section, CAST(NULL AS STRING) AS subject,\n",
            "    CONCAT('oee=', ROUND(oee_to_now, 1),\n",
            "           '% (a=', ROUND(availability_to_now, 1),\n",
            "           ', p=', ROUND(performance_to_now, 1),\n",
            "           ', q=', ROUND(quality_to_now, 1),\n",
            "           ') cases=', cumulative_cases) AS detail\n",
            "  FROM latest_kpi WHERE rn = 1\n",
            "),\n",
            "absent_crew AS (\n",
            "  SELECT 'absent_crew' AS section, role AS subject,\n",
            "    CONCAT(operator_name, ' (', assigned_machine, ')') AS detail\n",
            "  FROM __CAT__.__SCH__.shift_crew\n",
            "  WHERE shift_id = :shift_id AND status IN ('absent', 'late')\n",
            "),\n",
            "top_events AS (\n",
            "  SELECT 'top_event' AS section, machine_id AS subject,\n",
            "    CONCAT(event_type, ' / ', severity, ' / ', ROUND(duration_minutes, 1), 'min',\n",
            "           CASE WHEN timestamp_end IS NULL AND event_type <> 'resolved' THEN ' / OPEN' ELSE '' END) AS detail\n",
            "  FROM __CAT__.__SCH__.gold_history_event_log\n",
            "  WHERE shift_id = :shift_id\n",
            "  ORDER BY duration_minutes DESC NULLS LAST\n",
            "  LIMIT 5\n",
            "),\n",
            "open_at_handover AS (\n",
            "  SELECT 'open_at_handover' AS section, machine_id AS subject,\n",
            "    CONCAT(event_type, ' / ', severity, ' / opened ', CAST(timestamp_start AS STRING)) AS detail\n",
            "  FROM __CAT__.__SCH__.gold_history_event_log\n",
            "  WHERE shift_id = :shift_id AND timestamp_end IS NULL AND event_type <> 'resolved' AND NOT EXISTS (SELECT 1 FROM __CAT__.__SCH__.gold_history_event_log r WHERE r.shift_id = gold_history_event_log.shift_id AND r.machine_id = gold_history_event_log.machine_id AND r.event_type = 'resolved' AND r.timestamp_start >= gold_history_event_log.timestamp_start)\n",
            ")\n",
            "SELECT * FROM plan\n",
            "UNION ALL SELECT * FROM kpi_row\n",
            "UNION ALL SELECT * FROM absent_crew\n",
            "UNION ALL SELECT * FROM top_events\n",
            "UNION ALL SELECT * FROM open_at_handover"
        ],
        "parameters": [{"name": "shift_id", "type_hint": "STRING", "description": ["The shift to summarise. Default: __SHIFT__."], "default_value": {"values": ["__SHIFT__"]}}],
        "usage_guidance": [
            "Post-shift handover note. 'section' groups: 'plan_vs_actual' (planned vs actual cases + completion %), ",
            "'kpi' (final OEE breakdown), 'absent_crew' (anyone marked absent / late this shift — flag for HR), ",
            "'top_event' (top 5 longest events by duration), 'open_at_handover' (events still open). ",
        ],
    },
    {
        "id": "c2000000000000000000000000000012",
        "question": ["Carryover risks for the next shift"],
        "sql": [
            "SELECT 'open_event' AS risk_type, machine_id AS subject,\n",
            "  CONCAT(event_type, ' / ', severity, ' / ', COALESCE(root_cause, '?')) AS detail\n",
            "FROM __CAT__.__SCH__.gold_history_event_log\n",
            "WHERE timestamp_end IS NULL AND event_type <> 'resolved' AND NOT EXISTS (SELECT 1 FROM __CAT__.__SCH__.gold_history_event_log r WHERE r.shift_id = gold_history_event_log.shift_id AND r.machine_id = gold_history_event_log.machine_id AND r.event_type = 'resolved' AND r.timestamp_start >= gold_history_event_log.timestamp_start)\n",
            "UNION ALL\n",
            "SELECT 'low_inventory', material_id,\n",
            "  CONCAT('on_hand=', CAST(ROUND(on_hand_qty, 1) AS STRING),\n",
            "         ' reorder_point=', CAST(reorder_point AS STRING))\n",
            "FROM __CAT__.__SCH__.inventory_status\n",
            "WHERE status = 'LOW' OR on_hand_qty <= reorder_point\n",
            "UNION ALL\n",
            "SELECT 'overdue_pm', machine_id,\n",
            "  CONCAT(pm_type, ' next_pm=', CAST(next_pm AS STRING))\n",
            "FROM __CAT__.__SCH__.maintenance_windows\n",
            "WHERE next_pm < CURRENT_TIMESTAMP()"
        ],
        "parameters": [],
        "usage_guidance": [
            "Unfiltered live carryover view: every still-open event, every LOW inventory item, ",
            "every overdue PM — across all shifts. The incoming crew should action each row."
        ],
    },
    {
        "id": "c2000000000000000000000000000013",
        "question": ["What's still open right now on the active shift?"],
        "sql": [
            "SELECT event_id, machine_id, event_type, severity, root_cause,\n",
            "  CAST(timestamp_start AS STRING) AS opened_at,\n",
            "  ROUND((unix_timestamp(CURRENT_TIMESTAMP()) - unix_timestamp(timestamp_start)) / 60.0, 1)\n",
            "    AS open_for_minutes\n",
            "FROM __CAT__.__SCH__.gold_history_event_log\n",
            "WHERE shift_id = :shift_id AND timestamp_end IS NULL AND event_type <> 'resolved' AND NOT EXISTS (SELECT 1 FROM __CAT__.__SCH__.gold_history_event_log r WHERE r.shift_id = gold_history_event_log.shift_id AND r.machine_id = gold_history_event_log.machine_id AND r.event_type = 'resolved' AND r.timestamp_start >= gold_history_event_log.timestamp_start)\n",
            "ORDER BY timestamp_start"
        ],
        "parameters": [{"name": "shift_id", "type_hint": "STRING", "description": ["The shift to query. Default: __SHIFT__, the shift this environment's producer writes. There is no 'live' shift."], "default_value": {"values": ["__SHIFT__"]}}],
    },
]


# ── Benchmarks (used by Genie eval) ────────────────────────────────────────

benchmarks = [
    {
        "id": "f2000000000000000000000000000001",
        "question": ["What is the latest OEE for the live shift?"],
        "answer": [{
            "format": "SQL",
            "content": [
                "SELECT shift_id, ROUND(oee_to_now, 1) AS oee_pct, ts\n",
                "FROM __CAT__.__SCH__.gold_history_line_kpi\n",
                "WHERE ts > current_timestamp() - INTERVAL 15 MINUTES\n",
                "ORDER BY ts DESC LIMIT 1"
            ]
        }],
    },
    {
        "id": "f2000000000000000000000000000002",
        "question": ["Which machines are stopped right now?"],
        "answer": [{
            "format": "SQL",
            "content": [
                "WITH latest AS (\n",
                "  SELECT *, ROW_NUMBER() OVER (PARTITION BY machine_id ORDER BY ts DESC) AS rn\n",
                "  FROM __CAT__.__SCH__.gold_history_machine_state\n",
                "  WHERE ts > current_timestamp() - INTERVAL 15 MINUTES\n",
                ")\n",
                "SELECT machine_id, status FROM latest WHERE rn = 1 AND status = 'stopped'"
            ]
        }],
    },
    {
        "id": "f2000000000000000000000000000003",
        "question": ["Pre-shift briefing for the live shift"],
        "answer": [{
            "format": "SQL",
            "content": [
                "-- See example_question_sqls c2000000000000000000000000000010 for the canonical body."
            ]
        }],
    },
    {
        "id": "f2000000000000000000000000000004",
        "question": ["Post-shift summary for the live shift"],
        "answer": [{
            "format": "SQL",
            "content": [
                "-- See example_question_sqls c2000000000000000000000000000011 for the canonical body."
            ]
        }],
    },
    {
        "id": "f2000000000000000000000000000005",
        "question": ["What's carrying over right now?"],
        "answer": [{
            "format": "SQL",
            "content": [
                "-- See example_question_sqls c2000000000000000000000000000012 for the canonical body."
            ]
        }],
    },
]


# ── Text instructions (single block per Genie API rules) ───────────────────

text_instructions_content = [
    "This dataset is the LIVE streaming demo for a production line ",
    "running in the streaming workspace. It is structurally different from ",
    "the static seed schema used by the seed-schema (demo workspace) path. ",
    "Key facts: ",
    "  * gold_history_line_kpi emits ~1 row per second per shift_id. To get the CURRENT ",
    "    state, take ROW_NUMBER() OVER (PARTITION BY shift_id ORDER BY ts DESC) = 1. ",
    "  * gold_history_machine_state emits ~1 row per machine per second. To get the CURRENT ",
    "    machine status, take ROW_NUMBER() OVER (PARTITION BY machine_id ORDER BY ts DESC) = 1. ",
    "  * OEE columns (oee_to_now, availability_to_now, performance_to_now, ",
    "    quality_to_now) and speed_pct are stored as PERCENTAGES 0-100. DO NOT ",
    "    multiply by 100. (e.g. oee_to_now = 74.5 means 74.5%.) ",
    "  * gold_history_event_log is append-only: a resolved incident gets a separate "
    "event_type = 'resolved' row and the original keeps timestamp_end IS NULL "
    "forever. An event is still OPEN only when no 'resolved' row exists for that "
    "machine at or after its timestamp_start. These are ",
    "    the carryover candidates at any handover. ",
    "  * maintenance_windows has no shift_id; filter by next_pm timestamp. ",
    "    An OVERDUE PM has next_pm < CURRENT_TIMESTAMP(); DUE_SOON within 8h. ",
    "  * inventory_status.status is OK | LOW | CRITICAL (uppercase). CRITICAL ",
    "    is at or below safety_stock, LOW is between safety_stock and ",
    "    reorder_point. status IN ('LOW','CRITICAL') needs reorder attention. ",
    "  * shift_crew has 12 rows per shift (roles: operator, mechanic, qa, ",
    "    supervisor — there is no 'lead', 'maintenance' or 'quality' role). ",
    "    status IN ('present', 'absent', 'late'). The ",
    "    skills column is a COMMA-SEPARATED STRING of machine_ids — use ",
    "    array_contains(split(skills, ','), 'FILLER') or LIKE matching to query. ",
    "  * production_schedule has ONE OR MORE ROWS per shift_id with planned_cases, ",
    "    actual_cases, ideal_rate_bpm, sku/sku_name, start_time/end_time as ",
    "    ISO-8601 strings, and status (completed | changeover | disrupted). ",
    "    A changeover segment carries planned_cases = 0, so for a shift target ",
    "    use SUM(planned_cases), not an arbitrary row. ",
    "    Completion % = SUM(actual_cases) / SUM(planned_cases) * 100. ",
    "  * cip_schedule has 0..N rows per shift_id with planned_start / planned_end ",
    "    as strings and machine_id of the unit being cleaned. ",
    "  * Machine statuses, lowercase: running, blocked, starved, degraded, stopped. ",
    "    There is no 'idle'. Event severities, most to least critical: ",
    "    critical > high > medium > low. Quality check result is 'pass' or 'fail'. ",
    "SHIFT IDs COME IN TWO INCOMPATIBLE FORMS — this is the most common cause of ",
    "a query that runs cleanly and returns nothing: ",
    "  * LIVE tables are keyed by the environment-suffixed id the running line ",
    "    writes, e.g. 'shift_1_dev': gold_history_line_kpi, gold_history_machine_state, ",
    "    gold_history_event_log, quality_checks_bronze, telemetry_bronze, ",
    "    inventory_status, gold_history_inventory_status. ",
    "  * REFERENCE tables are keyed by the base id with no suffix, e.g. 'shift_1': ",
    "    production_schedule, shift_crew, cip_schedule, maintenance_schedule, ",
    "    scenario_index, scenario_moments. ",
    "  * The two sets never overlap. NEVER join a live table to a reference table ",
    "    on shift_id — the result is always empty. Relate them on machine_id, or ",
    "    strip a trailing '_dev'/'_prod' before filtering a reference table. ",
    "  * Reference tables carry nominal scenario timestamps, not wall-clock time. ",
    "    Never bound a live table by a start_time read from a schedule table. ",
    "JOIN RULES: within the live set, gold_history_event_log, gold_history_machine_state, ",
    "quality_checks_bronze and telemetry_bronze share both shift_id and machine_id. ",
    "shift_crew (via assigned_machine), cip_schedule and maintenance_schedule relate ",
    "to the live set by machine_id only. Always filter to one shift_id and bound ts ",
    "before joining — the live tables hold millions of rows and an unbounded join ",
    "will time out. For production-target questions use production_schedule.planned_cases ",
    "(NOT scenario_index, which has no target column). ",
    "\n\n",
    "MOBILE BRIEFING QUERIES — this Genie space is the line manager's mobile ",
    "companion. They open the Databricks app on their phone while walking to ",
    "the line and tap one of four chips. Map each chip to the right curated SQL: ",
    "  * 'Brief me for my shift' / 'pre-shift briefing' / 'kickoff' → ",
    "    use example c2000000000000000000000000000010 (latest gold_history_line_kpi + open ",
    "    events + low inventory + due/overdue PMs). ",
    "  * 'How are we doing right now?' / 'mid-shift status' / 'how's the ",
    "    line?' → use example c2000000000000000000000000000009 (latest ",
    "    gold_history_line_kpi snapshot + currently-open events with open-duration). ",
    "  * 'Wrap up this shift' / 'post-shift summary' / 'shift wrap' / ",
    "    'handover note' → use example c2000000000000000000000000000011 ",
    "    (final OEE breakdown + top 5 events + still-open events). ",
    "  * \"What's carrying over?\" / 'carryover' / 'handoff risks' → use ",
    "    example c2000000000000000000000000000012 (all open events + LOW ",
    "    inventory + overdue PMs across the line). ",
    "If the user names a shift (e.g. 'shift_1_dev'), substitute it for the ",
    ":shift_id parameter. If they say 'this shift', 'the live shift', or do ",
    "not specify, use '__SHIFT__' (what this environment's producer writes). ",
    "Crew, plan, and CIP data ARE in this Genie space (shift_crew, ",
    "production_schedule, cip_schedule). Cite specific operator_names, ",
    "planned_cases targets and CIP windows directly in handover briefings."
]


# ── Assemble the serialized_space payload ──────────────────────────────────

serialized_space = {
    "version": 2,
    "data_sources": {"tables": tables},
    "config": {"sample_questions": sample_questions},
    "instructions": {
        "text_instructions": [
            {"id": "b2000000000000000000000000000001", "content": text_instructions_content}
        ],
        "example_question_sqls": example_question_sqls,
    },
    "benchmarks": {"questions": benchmarks},
}


def main():
    parser = argparse.ArgumentParser(description="Push streaming Genie Space config")
    parser.add_argument("--space-id", default=DEFAULT_SPACE_ID,
                        help=f"Genie space id (default: {DEFAULT_SPACE_ID})")
    parser.add_argument("--profile", default=DEFAULT_PROFILE,
                        help=f"Databricks CLI profile (default: {DEFAULT_PROFILE})")
    parser.add_argument("--warehouse-id", default=DEFAULT_WAREHOUSE_ID,
                        help="SQL warehouse id the space queries through")
    parser.add_argument("--catalog", default=DEFAULT_CATALOG,
                        help=f"UC catalog (default: {DEFAULT_CATALOG})")
    parser.add_argument("--schema", default=DEFAULT_SCHEMA,
                        help=f"UC schema (default: {DEFAULT_SCHEMA})")
    parser.add_argument("--shift-id", default="shift_1",
                        help="The shift this environment's producer writes (e.g. "
                             "shift_1_dev). Becomes the default :shift_id in every "
                             "curated SQL. Getting this wrong is silent: the query "
                             "runs and returns nothing.")
    parser.add_argument("--title", default="ProdLine CoPilot — Production Line Analytics (streaming)")
    parser.add_argument("--parent-path", default="",
                        help="Workspace folder to register a NEWLY created space under "
                             "(e.g. /Users/me@co.com). Only used when creating.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Write payload to /tmp without calling Databricks")
    args = parser.parse_args()

    # A blank / SET_ME --space-id means "create a new space"; a real id means
    # "update that space in place". Only --space-id is optional depending on mode.
    create_mode = (args.space_id or "").strip() in ("", "SET_ME")

    # Fail fast on placeholder values so the script never tries to apply a
    # half-configured space against a real workspace.
    if not args.dry_run:
        required = [
            ("--profile", args.profile),
            ("--warehouse-id", args.warehouse_id),
            ("--catalog", args.catalog),
        ]
        if not create_mode:
            required.insert(0, ("--space-id", args.space_id))
        missing = [
            name for name, value in required
            if (value or "").strip() in ("", "SET_ME")
        ]
        if missing:
            sys.stderr.write(
                "ERROR: missing required values (committed defaults are SET_ME placeholders): "
                + ", ".join(missing) + "\n"
            )
            sys.exit(2)

    # Genie's export proto enforces alphabetical ordering everywhere — on
    # tables, on column_configs inside each table, on sample_questions, on
    # example_question_sqls and on benchmarks. Sort in place before
    # serialization so the script is order-independent.
    serialized_space["data_sources"]["tables"].sort(key=lambda t: t["identifier"])
    for t in serialized_space["data_sources"]["tables"]:
        t["column_configs"].sort(key=lambda c: c["column_name"])
    serialized_space["config"]["sample_questions"].sort(key=lambda q: q["id"])
    serialized_space["instructions"]["example_question_sqls"].sort(key=lambda e: e["id"])
    serialized_space["benchmarks"]["questions"].sort(key=lambda b: b["id"])

    # Substitute the catalog/schema placeholders in every identifier and SQL,
    # and the shift placeholder in every :shift_id default. The shift used to be
    # hardcoded to shift_1, so the dev space defaulted to a shift its own
    # producer never writes: every shift-scoped question ran cleanly and
    # answered "0 machines", which reads as a broken line rather than a
    # misconfigured space.
    blob = json.dumps(serialized_space)
    blob = (
        blob.replace("__CAT__", args.catalog)
        .replace("__SCH__", args.schema)
        .replace("__SHIFT__", args.shift_id)
    )

    payload_dict = {
        "warehouse_id": args.warehouse_id,
        "title": args.title,
        "description": (
            "Live streaming production line analytics for the streaming workspace. "
            "Covers OEE, machine state, events, quality, inventory, and PM scheduling. "
            "Includes pre-shift briefing, post-shift summary, and carryover-risk SQLs "
            "tailored to the streaming schema (gold_history_line_kpi, gold_history_machine_state, gold_history_event_log, "
            "inventory_status, maintenance_windows)."
        ),
        "serialized_space": blob,
    }
    # ``parent_path`` is only accepted by the create endpoint (it registers the
    # new space under a workspace folder); PATCH rejects unknown fields.
    if create_mode and (args.parent_path or "").strip():
        payload_dict["parent_path"] = args.parent_path.strip()
    payload = json.dumps(payload_dict)

    payload_path = Path("/tmp/genie_curated_streaming.json")
    payload_path.write_text(payload)
    print(f"Payload written: {payload_path}")
    print(f"  mode         : {'create' if create_mode else 'update'}")
    print(f"  warehouse_id : {args.warehouse_id}")
    print(f"  table prefix : {args.catalog}.{args.schema}")
    print(f"  default shift: {args.shift_id}")
    print(f"  tables       : {len(tables)}")
    print(f"  sample qs    : {len(sample_questions)}")
    print(f"  example SQLs : {len(example_question_sqls)}")
    print(f"  benchmarks   : {len(benchmarks)}")

    if args.dry_run:
        print("--dry-run set — skipping databricks api call")
        return

    if create_mode:
        # POST /api/2.0/genie/spaces — create a brand-new space (GA Mar 2026).
        cmd = ["databricks", "--profile", args.profile, "api", "post",
               "/api/2.0/genie/spaces", "--json", f"@{payload_path}"]
        print(f"POST /api/2.0/genie/spaces via profile={args.profile}")
    else:
        cmd = ["databricks", "--profile", args.profile, "api", "patch",
               f"/api/2.0/genie/spaces/{args.space_id}",
               "--json", f"@{payload_path}"]
        print(f"PATCH /api/2.0/genie/spaces/{args.space_id} via profile={args.profile}")

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR: {result.stderr.strip() or result.stdout.strip()}", file=sys.stderr)
        sys.exit(result.returncode)
    try:
        resp = json.loads(result.stdout)
        space_id = resp.get("space_id") or args.space_id
        print(f"{'Created' if create_mode else 'Updated'} space: {space_id}")
        print(f"Title: {resp.get('title')}")
        # Machine-readable line so deploy.sh can capture the new id on create.
        print(f"GENIE_SPACE_ID={space_id}")
    except Exception:
        print(result.stdout[:500])


if __name__ == "__main__":
    main()
