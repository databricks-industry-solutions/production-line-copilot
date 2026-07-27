"""Build and push comprehensive Genie Space configuration.

Run:
  python3 scripts/genie_space_config.py                              # uses defaults below
  python3 scripts/genie_space_config.py --profile <your-profile> --space-id <your-space-id>
  python3 scripts/genie_space_config.py --dry-run                    # write payload only

The script lays down sample questions, table descriptions, example SQLs and
benchmarks — including the **shift-briefing** entries that power the Genie
mobile handover path alongside the in-app shift_briefing specialist.
"""

import argparse
import json
import subprocess
import sys

DEFAULT_SPACE_ID = "SET_ME"
# This is the legacy, manual Genie helper. Normal deploys create + configure a
# Genie space automatically via scripts/genie_space_config_streaming.py. To use
# this script directly, create a Genie space in your workspace via the UI, then
# re-run with --space-id <id> --profile <profile> --catalog <your_catalog>
# --schema <your_schema>.
DEFAULT_PROFILE = "DEFAULT"
DEFAULT_WAREHOUSE_ID = "SET_ME"
DEFAULT_CATALOG = "main"
# The script template was originally written against ``main.line_manager_ai_kit.*``
# table names. We rewrite all of those at push-time so the same payload
# can target ``main.line_manager_streaming.*`` (the live-data schema) or any
# other catalog/schema pair via CLI.
DEFAULT_SCHEMA = "line_manager_streaming"
TEMPLATE_FQ_PREFIX = "main.line_manager_ai_kit"

serialized_space = {
    "version": 2,

    # ── Config: sample questions shown to users ──
    # Briefing/handover questions are surfaced first so Genie mobile users
    # see them at the top of the suggestion list — matches the pre/post-shift
    # huddle workflow that the in-app shift_briefing specialist also covers.
    "config": {
        # Ordering: Genie sorts by id and renders the top chips on mobile. The
        # four shift-arc briefings come first so a line manager opening the
        # Databricks app on their phone sees them at the top of the chip list.
        # See the sibling streaming config for the same pattern.
        "sample_questions": [
            # ── Shift-arc briefings (top of the chip list on mobile) ──
            {"id": "a1000000000000000000000000000001", "question": ["Brief me for my shift"]},
            {"id": "a1000000000000000000000000000002", "question": ["How are we doing right now?"]},
            {"id": "a1000000000000000000000000000003", "question": ["Wrap up this shift"]},
            {"id": "a1000000000000000000000000000004", "question": ["What's carrying over?"]},
            # ── Operational drill-downs ──
            {"id": "a1000000000000000000000000000010", "question": ["What is the OEE breakdown (availability, performance, quality) for each shift?"]},
            {"id": "a1000000000000000000000000000011", "question": ["Which machine has the most downtime events across all shifts?"]},
            {"id": "a1000000000000000000000000000012", "question": ["Compare quality reject rates by machine"]},
            {"id": "a1000000000000000000000000000013", "question": ["What is the average speed percentage by machine for shift_3?"]},
            {"id": "a1000000000000000000000000000014", "question": ["Which materials are below safety stock?"]},
            {"id": "a1000000000000000000000000000015", "question": ["Show me the production plan completion rate per shift"]},
            {"id": "a1000000000000000000000000000016", "question": ["What were the top 5 longest downtime events?"]},
        ]
    },

    # ── Data Sources: tables with column configs ──
    "data_sources": {
        "tables": sorted([
            {
                "identifier": "main.line_manager_ai_kit.oee_gold",
                "description": ["Shift-level OEE summary. Use this table when asked about OEE, availability, performance, quality, production output, or reject counts. One row per shift."],
                "column_configs": [
                    {"column_name": "shift_id", "description": ["Unique identifier for the shift (shift_1 through shift_7)"], "enable_format_assistance": True, "enable_entity_matching": True},
                    {"column_name": "sku_id", "description": ["SKU being produced during this shift"], "synonyms": ["product", "beer type", "SKU"]},
                    {"column_name": "availability", "description": ["Availability component of OEE, stored as a percentage 0-100 (e.g. 98.0 means 98%). Do not multiply by 100 again."], "synonyms": ["uptime"]},
                    {"column_name": "performance", "description": ["Performance component of OEE, stored as a percentage 0-100. Ratio of actual speed to ideal speed."], "synonyms": ["speed efficiency", "rate efficiency"]},
                    {"column_name": "quality", "description": ["Quality component of OEE, stored as a percentage 0-100. Ratio of good units to total units."], "synonyms": ["yield", "quality rate"]},
                    {"column_name": "oee", "description": ["Overall Equipment Effectiveness, stored as a percentage 0-100 (e.g. 84.75 means 84.75%). Computed as availability * performance * quality / 10000 from the same row."], "synonyms": ["OEE", "overall efficiency"]},
                    {"column_name": "total_cases", "description": ["Actual number of cases produced during the shift"], "synonyms": ["actual cases", "output", "produced"]},
                    {"column_name": "planned_cases", "description": ["Target number of cases for the shift from production plan"], "synonyms": ["target cases", "plan"]},
                    {"column_name": "downtime_minutes", "description": ["Total unplanned downtime in minutes during the shift"], "synonyms": ["downtime", "stopped time"]},
                    {"column_name": "reject_count", "description": ["Number of rejected/defective cases during the shift"], "synonyms": ["rejects", "defects", "scrap"]},
                    {"column_name": "speed_loss_minutes", "description": ["Minutes lost due to running below ideal speed"], "synonyms": ["speed loss"]},
                    {"column_name": "changeover_minutes", "description": ["Minutes spent on product/format changeovers"], "synonyms": ["changeover time"]},
                ]
            },
            {
                "identifier": "main.line_manager_ai_kit.machine_state",
                "description": ["Per-minute machine state and performance data. Use this table for machine-level analysis: speed, throughput, status over time. One row per machine per minute."],
                "column_configs": [
                    {"column_name": "ts", "description": ["ISO 8601 timestamp truncated to the minute"], "enable_format_assistance": True},
                    {"column_name": "shift_id", "description": ["Shift this minute belongs to"], "enable_format_assistance": True, "enable_entity_matching": True},
                    {"column_name": "machine_id", "description": ["Machine identifier (e.g. FILLER, CAPPER, LABELLER, PACKER, PALLETIZER, CONVEYOR_1..4)"], "enable_format_assistance": True, "enable_entity_matching": True},
                    {"column_name": "status", "description": ["Current machine status: running, stopped, starved, or idle"], "enable_format_assistance": True, "enable_entity_matching": True},
                    {"column_name": "throughput_per_min", "description": ["Bottles or cases processed in this minute"], "synonyms": ["throughput", "output rate"]},
                    {"column_name": "speed_pct", "description": ["Machine speed as percentage of ideal rate (0-100)"], "synonyms": ["speed", "rate"]},
                    {"column_name": "counter_delta", "description": ["Incremental counter of units processed in this minute"]},
                    {"column_name": "primary_sensor_1", "description": ["Primary sensor reading 1 (machine-specific: temperature, pressure, etc.)"]},
                    {"column_name": "primary_sensor_2", "description": ["Primary sensor reading 2 (machine-specific)"]},
                    {"column_name": "material_level_pct", "description": ["Material/consumable level as percentage (0-100)"], "synonyms": ["material level", "consumable level"]},
                    {"column_name": "alarm_count", "description": ["Number of alarms triggered in this minute"]},
                ]
            },
            {
                "identifier": "main.line_manager_ai_kit.event_log",
                "description": ["Production events log: downtime events, quality holds, speed reductions, changeovers, stops, safety incidents. Use this table for event analysis, downtime root causes, and incident investigation."],
                "column_configs": [
                    {"column_name": "event_id", "description": ["Unique event identifier"]},
                    {"column_name": "shift_id", "description": ["Shift in which the event occurred"], "enable_format_assistance": True, "enable_entity_matching": True},
                    {"column_name": "machine_id", "description": ["Machine where the event occurred"], "enable_format_assistance": True, "enable_entity_matching": True},
                    {"column_name": "event_type", "description": ["Type of event: major_stop, micro_stop, speed_reduction, changeover, quality_hold, upstream_delay, near_miss, safety_first_aid, safety_near_miss"], "enable_format_assistance": True, "enable_entity_matching": True},
                    {"column_name": "severity", "description": ["Event severity: critical, major, minor, or info"], "enable_format_assistance": True, "enable_entity_matching": True},
                    {"column_name": "timestamp_start", "description": ["ISO 8601 timestamp when the event started"], "enable_format_assistance": True},
                    {"column_name": "timestamp_end", "description": ["ISO 8601 timestamp when the event ended (NULL if ongoing)"], "enable_format_assistance": True},
                    {"column_name": "duration_minutes", "description": ["Duration of the event in minutes"], "synonyms": ["event duration", "length"]},
                    {"column_name": "root_cause", "description": ["Identified root cause of the event"]},
                    {"column_name": "description", "description": ["Human-readable description of what happened"]},
                    {"column_name": "corrective_action", "description": ["Action taken to resolve the event"]},
                    {"column_name": "resolution_minutes", "description": ["Time in minutes to resolve the event"]},
                    {"column_name": "outcome_oee_impact", "description": ["Estimated OEE impact of this event as a decimal"]},
                ]
            },
            {
                "identifier": "main.line_manager_ai_kit.production_schedule",
                "description": ["Shift production plan and targets. Use this table for plan vs actual analysis, shift scheduling, and target tracking. One row per shift."],
                "column_configs": [
                    {"column_name": "shift_id", "description": ["Shift identifier (shift_1 through shift_7)"], "enable_format_assistance": True, "enable_entity_matching": True},
                    {"column_name": "date", "description": ["Production date in YYYY-MM-DD format"], "enable_format_assistance": True},
                    {"column_name": "shift_number", "description": ["Numeric shift number (1, 2, or 3 for day/evening/night)"]},
                    {"column_name": "start_time", "description": ["Shift start time in ISO 8601 format"], "enable_format_assistance": True},
                    {"column_name": "end_time", "description": ["Shift end time in ISO 8601 format"], "enable_format_assistance": True},
                    {"column_name": "sku", "description": ["SKU code being produced"], "synonyms": ["product code"]},
                    {"column_name": "sku_name", "description": ["Human-readable SKU name (e.g. Lager 330ml, Wheat 500ml)"], "synonyms": ["product name", "beer"]},
                    {"column_name": "planned_cases", "description": ["Target number of cases for this shift"], "synonyms": ["target", "plan"]},
                    {"column_name": "actual_cases", "description": ["Actual cases produced (updated at shift end)"], "synonyms": ["output", "produced"]},
                    {"column_name": "ideal_rate_bpm", "description": ["Ideal production rate in bottles per minute"], "synonyms": ["ideal speed", "line speed"]},
                    {"column_name": "status", "description": ["Shift status: planned, in_progress, or completed"], "enable_format_assistance": True, "enable_entity_matching": True},
                    {"column_name": "batch_id", "description": ["Batch identifier for traceability"]},
                ]
            },
            {
                "identifier": "main.line_manager_ai_kit.quality_checks",
                "description": ["Quality inspection results. Use this table for quality analysis, pass/fail rates, spec compliance. One row per quality check."],
                "column_configs": [
                    {"column_name": "check_id", "description": ["Unique check identifier"]},
                    {"column_name": "shift_id", "description": ["Shift when the check was performed"], "enable_format_assistance": True, "enable_entity_matching": True},
                    {"column_name": "machine_id", "description": ["Machine being checked"], "enable_format_assistance": True, "enable_entity_matching": True},
                    {"column_name": "timestamp", "description": ["When the check was performed"], "enable_format_assistance": True},
                    {"column_name": "parameter", "description": ["Quality parameter measured: fill_volume, cap_torque, case_weight, or label_alignment"], "enable_format_assistance": True, "enable_entity_matching": True},
                    {"column_name": "value", "description": ["Measured value"]},
                    {"column_name": "spec_min", "description": ["Minimum acceptable specification value"]},
                    {"column_name": "spec_max", "description": ["Maximum acceptable specification value"]},
                    {"column_name": "result", "description": ["Check result: pass or fail"], "enable_format_assistance": True, "enable_entity_matching": True},
                ]
            },
            {
                "identifier": "main.line_manager_ai_kit.inventory_status",
                "description": ["Material inventory levels and status. Use this table for inventory analysis, stock alerts, and supply chain questions. Multiple snapshots per shift."],
                "column_configs": [
                    {"column_name": "shift_id", "description": ["Shift for this inventory snapshot"], "enable_format_assistance": True, "enable_entity_matching": True},
                    {"column_name": "material_id", "description": ["Material identifier (e.g. PRODUCT_BEER_LAGER, CROWN_STD_26, LABEL_PAPER_FB_LAGER)"], "enable_format_assistance": True, "enable_entity_matching": True},
                    {"column_name": "timestamp", "description": ["When this inventory snapshot was taken"], "enable_format_assistance": True},
                    {"column_name": "on_hand_qty", "description": ["Current quantity on hand"], "synonyms": ["stock", "inventory level", "quantity"]},
                    {"column_name": "safety_stock", "description": ["Minimum safety stock level"]},
                    {"column_name": "reorder_point", "description": ["Quantity at which reorder should be triggered"]},
                    {"column_name": "status", "description": ["Inventory status: OK or LOW"], "enable_format_assistance": True, "enable_entity_matching": True},
                ]
            },
            {
                "identifier": "main.line_manager_ai_kit.machine_telemetry",
                "description": ["Raw machine sensor readings at high frequency. Use for sensor trend analysis, anomaly detection, and detailed machine monitoring. One row per sensor per reading."],
                "column_configs": [
                    {"column_name": "timestamp", "description": ["ISO 8601 timestamp of the sensor reading"], "enable_format_assistance": True},
                    {"column_name": "shift_id", "description": ["Shift this reading belongs to"], "enable_format_assistance": True, "enable_entity_matching": True},
                    {"column_name": "machine_id", "description": ["Machine the sensor belongs to"], "enable_format_assistance": True, "enable_entity_matching": True},
                    {"column_name": "sensor_name", "description": ["Sensor identifier (e.g. filler_fill_volume_avg, capper_torque_avg, label_glue_temp)"], "enable_format_assistance": True, "enable_entity_matching": True},
                    {"column_name": "value", "description": ["Sensor reading value (units depend on sensor type)"]},
                ]
            },
            {
                "identifier": "main.line_manager_ai_kit.consumption_log",
                "description": ["Material consumption tracking. Use for consumption rate analysis and depletion forecasting. One row per material per machine per timestamp."],
                "column_configs": [
                    {"column_name": "shift_id", "description": ["Shift of this consumption record"], "enable_format_assistance": True, "enable_entity_matching": True},
                    {"column_name": "machine_id", "description": ["Machine consuming the material"], "enable_format_assistance": True, "enable_entity_matching": True},
                    {"column_name": "material_id", "description": ["Material being consumed"], "enable_format_assistance": True, "enable_entity_matching": True},
                    {"column_name": "timestamp", "description": ["When the consumption was recorded"], "enable_format_assistance": True},
                    {"column_name": "quantity_consumed", "description": ["Quantity consumed in this interval"], "synonyms": ["consumption", "usage"]},
                    {"column_name": "cumulative_consumed", "description": ["Cumulative quantity consumed since shift start"], "synonyms": ["total consumption"]},
                ]
            },
            {
                "identifier": "main.line_manager_ai_kit.maintenance_schedule",
                "description": ["Maintenance work orders: planned preventive maintenance (PM), corrective, and emergency maintenance. Use for maintenance analysis and planning."],
                "column_configs": [
                    {"column_name": "work_order_id", "description": ["Unique work order identifier"]},
                    {"column_name": "shift_id", "description": ["Shift when maintenance is scheduled"], "enable_format_assistance": True, "enable_entity_matching": True},
                    {"column_name": "machine_id", "description": ["Machine requiring maintenance"], "enable_format_assistance": True, "enable_entity_matching": True},
                    {"column_name": "maintenance_type", "description": ["Type: PM (preventive), corrective, or emergency"], "enable_format_assistance": True, "enable_entity_matching": True},
                    {"column_name": "planned_start", "description": ["Planned start time"], "enable_format_assistance": True},
                    {"column_name": "planned_end", "description": ["Planned end time"], "enable_format_assistance": True},
                    {"column_name": "actual_start", "description": ["Actual start time (NULL if not started)"], "enable_format_assistance": True},
                    {"column_name": "actual_end", "description": ["Actual end time (NULL if not completed)"], "enable_format_assistance": True},
                    {"column_name": "technician", "description": ["Assigned technician name"]},
                    {"column_name": "status", "description": ["Work order status: scheduled, in_progress, completed, or cancelled"], "enable_format_assistance": True, "enable_entity_matching": True},
                    {"column_name": "description", "description": ["Description of maintenance work"]},
                ]
            },
            {
                "identifier": "main.line_manager_ai_kit.cip_schedule",
                "description": ["Clean-in-Place (CIP) schedule for hygiene cleaning cycles. Use for CIP planning and compliance tracking."],
                "column_configs": [
                    {"column_name": "cip_id", "description": ["Unique CIP cycle identifier"]},
                    {"column_name": "shift_id", "description": ["Shift when CIP is scheduled"], "enable_format_assistance": True, "enable_entity_matching": True},
                    {"column_name": "machine_id", "description": ["Machine to be cleaned"], "enable_format_assistance": True, "enable_entity_matching": True},
                    {"column_name": "planned_start", "description": ["Planned CIP start time"], "enable_format_assistance": True},
                    {"column_name": "planned_end", "description": ["Planned CIP end time"], "enable_format_assistance": True},
                    {"column_name": "actual_start", "description": ["Actual CIP start time"], "enable_format_assistance": True},
                    {"column_name": "actual_end", "description": ["Actual CIP end time"], "enable_format_assistance": True},
                    {"column_name": "cip_type", "description": ["CIP type: full (caustic + rinse) or rinse (rinse only)"], "enable_format_assistance": True, "enable_entity_matching": True},
                    {"column_name": "chemical_used", "description": ["Chemical used for cleaning"]},
                    {"column_name": "duration_minutes", "description": ["Planned duration of the CIP cycle in minutes"]},
                    {"column_name": "status", "description": ["CIP status: scheduled, in_progress, completed, or cancelled"], "enable_format_assistance": True, "enable_entity_matching": True},
                ]
            },
            {
                "identifier": "main.line_manager_ai_kit.shift_crew",
                "description": ["Shift crew assignments: operators, technicians, supervisors and their machine assignments. Use for staffing, skill analysis, and crew planning."],
                "column_configs": [
                    {"column_name": "shift_id", "description": ["Shift this crew member is assigned to"], "enable_format_assistance": True, "enable_entity_matching": True},
                    {"column_name": "operator_id", "description": ["Unique operator identifier"]},
                    {"column_name": "operator_name", "description": ["Operator's full name"], "synonyms": ["name", "crew member"]},
                    {"column_name": "role", "description": ["Crew role: operator, mechanic, supervisor, or qa"], "enable_format_assistance": True, "enable_entity_matching": True},
                    {"column_name": "assigned_machine", "description": ["Machine this crew member is assigned to"], "enable_format_assistance": True, "enable_entity_matching": True},
                    {"column_name": "skill_level", "description": ["Operator skill level (1-5, where 5 is expert)"]},
                    {"column_name": "skills", "description": ["Comma-separated list of operator skills/certifications"]},
                    {"column_name": "status", "description": ["Crew member status: active or absent"], "enable_format_assistance": True, "enable_entity_matching": True},
                ]
            },
        ], key=lambda t: t["identifier"])
    },

    # ── Instructions ──
    "instructions": {

        # General text instructions. The Genie API enforces "at most one item"
        # here, so all guidance — domain rules AND the shift-briefing handover
        # playbook — must live in this single block.
        "text_instructions": [
            {
                "id": "b1000000000000000000000000000001",
                "content": [
                    "This dataset represents a brewery packaging line producing bottled beer. ",
                    "The line has 19 machines: DEPALLETIZER, UNCASER, BOTTLE_WASHER, EMPTY_INSPECTOR, FILLER, CAPPER, PASTEURIZER, LABELLER, CODER, CHECKWEIGHER, CASE_PACKER, CASE_SEALER, SHRINK_WRAPPER, STRETCH_WRAPPER, PALLETIZER, and CONVEYOR_1 through CONVEYOR_4. ",
                    "Shifts are numbered shift_1 through shift_7, each representing an 8-hour production period. ",
                    "OEE (Overall Equipment Effectiveness) = (Availability/100) x (Performance/100) x (Quality/100) x 100. ",
                    "In the oee_gold table the columns oee, availability, performance and quality are ALREADY stored as percentages (0-100, e.g. 84.75 means 84.75%). DO NOT multiply by 100 again. Good OEE is above 75. ",
                    "Machine statuses are: running (normal operation), stopped (unplanned downtime), starved (waiting for upstream input), idle (no demand). ",
                    "Event severities from most to least critical: critical > major > minor > info. ",
                    "Inventory status LOW means material is below safety stock and needs attention. OK means sufficient stock. ",
                    "When calculating production completion rate, use: actual_cases / planned_cases * 100. ",
                    "When comparing across shifts, always ORDER BY shift_id to maintain chronological order. ",
                    "JOIN RULES: All tables share shift_id as the primary join key. ",
                    "event_log, machine_state, quality_checks, machine_telemetry, consumption_log, maintenance_schedule, cip_schedule, and shift_crew all have shift_id and machine_id columns. ",
                    "Join event_log to production_schedule ON shift_id for event impact on targets. ",
                    "Join oee_gold to production_schedule ON shift_id for OEE with plan context. ",
                    "Join inventory_status to consumption_log ON shift_id AND material_id for depletion analysis. ",
                    "Join shift_crew to production_schedule ON shift_id for staffing context. ",
                    "When joining machine_state to event_log, use BOTH shift_id AND machine_id to avoid cross-machine matches. ",
                    "Be careful with large joins involving machine_state (high row count); always filter by shift_id first. ",
                    "\n\n",
                    "MOBILE BRIEFING QUERIES — this Genie space is the line manager's mobile companion. ",
                    "They open the Databricks app on their phone while walking to the line and tap a chip. ",
                    "Map chip phrases (or any equivalent natural-language variant) to the right curated SQL: ",
                    "  * 'Brief me for my shift' / 'pre-shift briefing' / 'kickoff' / 'huddle' → PRE-SHIFT body below. ",
                    "  * 'How are we doing right now?' / 'mid-shift status' / 'how's the line?' → for this schema (no streaming line_kpi), return the most recent oee_gold + production_schedule row joined to currently open event_log rows (timestamp_end IS NULL). ",
                    "  * 'Wrap up this shift' / 'post-shift summary' / 'shift wrap' / 'handover note' / 'handoff' → POST-SHIFT body below. ",
                    "  * \"What's carrying over?\" / 'carryover' / 'handoff risks' → CARRYOVER body below. ",
                    "All briefing answers must be a single result set combining the most relevant fields for that mode: ",
                    "  * PRE-SHIFT (incoming crew): production_schedule (planned_cases, sku_name, ",
                    "    start_time, end_time, ideal_rate_bpm), shift_crew (operator_name, role, ",
                    "    assigned_machine, status), scheduled maintenance_schedule and cip_schedule ",
                    "    rows whose planned_start falls inside the shift window, and inventory_status ",
                    "    rows where status = 'LOW' or on_hand_qty <= reorder_point. ",
                    "  * POST-SHIFT (outgoing crew handover): oee_gold (oee, availability, performance, ",
                    "    quality, total_cases, planned_cases, downtime_minutes, reject_count) for the ",
                    "    shift, the top event_log rows by duration_minutes for that shift, and any ",
                    "    event_log rows with timestamp_end IS NULL (open at hand-over). ",
                    "  * CARRYOVER (risks for the NEXT shift): event_log rows with timestamp_end IS NULL ",
                    "    plus inventory_status rows where status = 'LOW', plus maintenance_schedule rows ",
                    "    where status IN ('scheduled', 'in_progress') whose planned_start is within the ",
                    "    next 8 hours of the current shift's end_time. ",
                    "Always include the relevant shift_id and machine_id / material_id columns so the ",
                    "operator can act on each row. If the user names a shift (e.g. 'shift_2'), filter ",
                    "the briefing to that shift; if they just say 'this shift' or 'the next shift', ",
                    "use the most recent shift in production_schedule.status = 'in_progress' (this ",
                    "shift) or the next 'planned' row (next shift)."
                ]
            }
        ],

        # Example SQL queries
        "example_question_sqls": [
            {
                "id": "c1000000000000000000000000000001",
                "question": ["What is the OEE breakdown for each shift?"],
                "sql": [
                    "SELECT shift_id,\n",
                    "  ROUND(availability, 1) AS availability_pct,\n",
                    "  ROUND(performance, 1) AS performance_pct,\n",
                    "  ROUND(quality, 1) AS quality_pct,\n",
                    "  ROUND(oee, 1) AS oee_pct,\n",
                    "  total_cases, planned_cases\n",
                    "FROM main.line_manager_ai_kit.oee_gold\n",
                    "ORDER BY shift_id"
                ],
                "usage_guidance": [
                    "Use when asked about OEE, availability, performance, quality breakdown per shift. ",
                    "Columns are already percentages (0-100); do NOT multiply by 100."
                ]
            },
            {
                "id": "c1000000000000000000000000000002",
                "question": ["Which machine has the most downtime events?"],
                "sql": [
                    "SELECT machine_id,\n",
                    "  COUNT(*) AS event_count,\n",
                    "  ROUND(SUM(duration_minutes), 1) AS total_downtime_min,\n",
                    "  ROUND(AVG(duration_minutes), 1) AS avg_duration_min\n",
                    "FROM main.line_manager_ai_kit.event_log\n",
                    "WHERE event_type IN ('major_stop', 'micro_stop')\n",
                    "GROUP BY machine_id\n",
                    "ORDER BY total_downtime_min DESC"
                ],
                "usage_guidance": ["Use when asked about downtime by machine, most problematic machines, or downtime ranking"]
            },
            {
                "id": "c1000000000000000000000000000003",
                "question": ["Show production plan completion rate per shift"],
                "sql": [
                    "SELECT shift_id, sku_name, planned_cases, actual_cases,\n",
                    "  ROUND(actual_cases * 100.0 / NULLIF(planned_cases, 0), 1) AS completion_pct\n",
                    "FROM main.line_manager_ai_kit.production_schedule\n",
                    "ORDER BY shift_id"
                ],
                "usage_guidance": ["Use when asked about plan completion, production targets, actual vs planned"]
            },
            {
                "id": "c1000000000000000000000000000004",
                "question": ["What is the quality fail rate by machine?"],
                "sql": [
                    "SELECT machine_id, parameter,\n",
                    "  COUNT(*) AS total_checks,\n",
                    "  SUM(CASE WHEN result = 'fail' THEN 1 ELSE 0 END) AS fail_count,\n",
                    "  ROUND(SUM(CASE WHEN result = 'fail' THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) AS fail_rate_pct\n",
                    "FROM main.line_manager_ai_kit.quality_checks\n",
                    "GROUP BY machine_id, parameter\n",
                    "ORDER BY fail_rate_pct DESC"
                ],
                "usage_guidance": ["Use when asked about quality failures, reject rates, pass/fail analysis by machine"]
            },
            {
                "id": "c1000000000000000000000000000005",
                "question": ["Which materials are below safety stock?"],
                "sql": [
                    "SELECT material_id, shift_id, on_hand_qty, safety_stock, reorder_point, status\n",
                    "FROM main.line_manager_ai_kit.inventory_status\n",
                    "WHERE status = 'LOW'\n",
                    "ORDER BY on_hand_qty ASC"
                ],
                "usage_guidance": ["Use when asked about low stock, material shortages, inventory alerts, or supply risks"]
            },
            {
                "id": "c1000000000000000000000000000006",
                "question": ["What is the average machine speed and throughput for a specific shift?"],
                "sql": [
                    "SELECT machine_id,\n",
                    "  ROUND(AVG(speed_pct), 1) AS avg_speed_pct,\n",
                    "  ROUND(AVG(throughput_per_min), 1) AS avg_throughput,\n",
                    "  SUM(CASE WHEN status = 'stopped' THEN 1 ELSE 0 END) AS stopped_minutes,\n",
                    "  SUM(CASE WHEN status = 'starved' THEN 1 ELSE 0 END) AS starved_minutes\n",
                    "FROM main.line_manager_ai_kit.machine_state\n",
                    "WHERE shift_id = :shift_id\n",
                    "GROUP BY machine_id\n",
                    "ORDER BY machine_id"
                ],
                "parameters": [
                    {"name": "shift_id", "type_hint": "STRING", "description": ["The shift to analyze (e.g. shift_1, shift_3)"], "default_value": {"values": ["shift_1"]}}
                ],
                "usage_guidance": ["Use when asked about machine speed, throughput, or uptime for a specific shift"]
            },
            {
                "id": "c1000000000000000000000000000007",
                "question": ["What are the top 5 longest downtime events?"],
                "sql": [
                    "SELECT event_id, shift_id, machine_id, event_type, severity,\n",
                    "  ROUND(duration_minutes, 1) AS duration_min,\n",
                    "  root_cause, corrective_action\n",
                    "FROM main.line_manager_ai_kit.event_log\n",
                    "WHERE event_type IN ('major_stop', 'micro_stop')\n",
                    "ORDER BY duration_minutes DESC\n",
                    "LIMIT 5"
                ],
                "usage_guidance": ["Use when asked about worst events, longest downtime, or biggest production disruptions"]
            },
            # ── Shift handover / briefing examples ────────────────────────────
            # These power both the Genie mobile path (operators reading on a
            # phone) and the in-app shift_briefing specialist when it routes
            # exploratory questions through genie_query.
            {
                "id": "c1000000000000000000000000000010",
                "question": ["Pre-shift briefing for a specific shift"],
                "sql": [
                    "WITH plan AS (\n",
                    "  SELECT shift_id, sku_name, planned_cases, start_time, end_time, ideal_rate_bpm, status\n",
                    "  FROM main.line_manager_ai_kit.production_schedule\n",
                    "  WHERE shift_id = :shift_id\n",
                    "),\n",
                    "crew AS (\n",
                    "  SELECT shift_id,\n",
                    "    COUNT(*) AS crew_size,\n",
                    "    SUM(CASE WHEN status = 'absent' THEN 1 ELSE 0 END) AS absent_count,\n",
                    "    COLLECT_LIST(STRUCT(operator_name, role, assigned_machine, status)) AS roster\n",
                    "  FROM main.line_manager_ai_kit.shift_crew\n",
                    "  WHERE shift_id = :shift_id\n",
                    "  GROUP BY shift_id\n",
                    "),\n",
                    "low_inv AS (\n",
                    "  SELECT shift_id,\n",
                    "    COUNT(*) AS low_material_count,\n",
                    "    COLLECT_LIST(STRUCT(material_id, on_hand_qty, reorder_point)) AS low_materials\n",
                    "  FROM main.line_manager_ai_kit.inventory_status\n",
                    "  WHERE shift_id = :shift_id AND status = 'LOW'\n",
                    "  GROUP BY shift_id\n",
                    "),\n",
                    "scheduled_pm AS (\n",
                    "  SELECT shift_id,\n",
                    "    COUNT(*) AS scheduled_work_orders,\n",
                    "    COLLECT_LIST(STRUCT(machine_id, maintenance_type, planned_start, technician)) AS work_orders\n",
                    "  FROM main.line_manager_ai_kit.maintenance_schedule\n",
                    "  WHERE shift_id = :shift_id AND status IN ('scheduled', 'in_progress')\n",
                    "  GROUP BY shift_id\n",
                    "),\n",
                    "scheduled_cip AS (\n",
                    "  SELECT shift_id,\n",
                    "    COUNT(*) AS scheduled_cip,\n",
                    "    COLLECT_LIST(STRUCT(machine_id, cip_type, planned_start, duration_minutes)) AS cip_cycles\n",
                    "  FROM main.line_manager_ai_kit.cip_schedule\n",
                    "  WHERE shift_id = :shift_id AND status IN ('scheduled', 'in_progress')\n",
                    "  GROUP BY shift_id\n",
                    ")\n",
                    "SELECT plan.shift_id, plan.sku_name, plan.planned_cases, plan.start_time, plan.end_time,\n",
                    "  plan.ideal_rate_bpm, plan.status,\n",
                    "  COALESCE(crew.crew_size, 0) AS crew_size,\n",
                    "  COALESCE(crew.absent_count, 0) AS absent_count, crew.roster,\n",
                    "  COALESCE(low_inv.low_material_count, 0) AS low_material_count, low_inv.low_materials,\n",
                    "  COALESCE(scheduled_pm.scheduled_work_orders, 0) AS scheduled_work_orders, scheduled_pm.work_orders,\n",
                    "  COALESCE(scheduled_cip.scheduled_cip, 0) AS scheduled_cip, scheduled_cip.cip_cycles\n",
                    "FROM plan\n",
                    "LEFT JOIN crew USING (shift_id)\n",
                    "LEFT JOIN low_inv USING (shift_id)\n",
                    "LEFT JOIN scheduled_pm USING (shift_id)\n",
                    "LEFT JOIN scheduled_cip USING (shift_id)"
                ],
                "parameters": [
                    {"name": "shift_id", "type_hint": "STRING", "description": ["The shift to brief (e.g. shift_1, shift_3)"], "default_value": {"values": ["shift_1"]}}
                ],
                "usage_guidance": [
                    "Use for 'pre-shift briefing', 'kickoff for shift X', 'what's on for this shift'. ",
                    "Returns a one-row digest with the plan, crew roster, low-inventory items, scheduled PM, and scheduled CIP for the named shift."
                ]
            },
            {
                "id": "c1000000000000000000000000000011",
                "question": ["Post-shift summary and handover for a specific shift"],
                "sql": [
                    "WITH oee AS (\n",
                    "  SELECT shift_id, ROUND(oee, 1) AS oee_pct,\n",
                    "    ROUND(availability, 1) AS availability_pct,\n",
                    "    ROUND(performance, 1) AS performance_pct,\n",
                    "    ROUND(quality, 1) AS quality_pct,\n",
                    "    total_cases, planned_cases, downtime_minutes, reject_count\n",
                    "  FROM main.line_manager_ai_kit.oee_gold\n",
                    "  WHERE shift_id = :shift_id\n",
                    "),\n",
                    "top_events AS (\n",
                    "  SELECT shift_id,\n",
                    "    COLLECT_LIST(STRUCT(machine_id, event_type, severity, ROUND(duration_minutes, 1) AS duration_min, root_cause, corrective_action)) AS top_events\n",
                    "  FROM (\n",
                    "    SELECT * FROM main.line_manager_ai_kit.event_log\n",
                    "    WHERE shift_id = :shift_id AND event_type IN ('major_stop', 'micro_stop', 'quality_hold')\n",
                    "    ORDER BY duration_minutes DESC LIMIT 5\n",
                    "  )\n",
                    "  GROUP BY shift_id\n",
                    "),\n",
                    "open_at_handover AS (\n",
                    "  SELECT shift_id,\n",
                    "    COUNT(*) AS open_event_count,\n",
                    "    COLLECT_LIST(STRUCT(machine_id, event_type, severity, timestamp_start, description)) AS open_events\n",
                    "  FROM main.line_manager_ai_kit.event_log\n",
                    "  WHERE shift_id = :shift_id AND timestamp_end IS NULL\n",
                    "  GROUP BY shift_id\n",
                    ")\n",
                    "SELECT oee.*,\n",
                    "  ROUND(oee.total_cases * 100.0 / NULLIF(oee.planned_cases, 0), 1) AS completion_pct,\n",
                    "  top_events.top_events,\n",
                    "  COALESCE(open_at_handover.open_event_count, 0) AS open_event_count,\n",
                    "  open_at_handover.open_events\n",
                    "FROM oee\n",
                    "LEFT JOIN top_events USING (shift_id)\n",
                    "LEFT JOIN open_at_handover USING (shift_id)"
                ],
                "parameters": [
                    {"name": "shift_id", "type_hint": "STRING", "description": ["The shift to summarise (e.g. shift_1, shift_3)"], "default_value": {"values": ["shift_1"]}}
                ],
                "usage_guidance": [
                    "Use for 'post-shift summary', 'shift wrap up', 'handover note', 'what happened in shift X'. ",
                    "Returns one row per shift with OEE breakdown, plan-completion %, top 5 events by duration, and any open events still unresolved at hand-over."
                ]
            },
            {
                "id": "c1000000000000000000000000000012",
                "question": ["Carryover risks for the next shift"],
                "sql": [
                    "WITH this_shift AS (\n",
                    "  SELECT shift_id, end_time\n",
                    "  FROM main.line_manager_ai_kit.production_schedule\n",
                    "  WHERE shift_id = :shift_id\n",
                    "),\n",
                    "open_events AS (\n",
                    "  SELECT e.shift_id, e.machine_id, e.event_type, e.severity,\n",
                    "    e.timestamp_start, e.description\n",
                    "  FROM main.line_manager_ai_kit.event_log e\n",
                    "  WHERE e.shift_id = :shift_id AND e.timestamp_end IS NULL\n",
                    "),\n",
                    "low_inventory AS (\n",
                    "  SELECT shift_id, material_id, on_hand_qty, reorder_point, safety_stock\n",
                    "  FROM main.line_manager_ai_kit.inventory_status\n",
                    "  WHERE shift_id = :shift_id AND status = 'LOW'\n",
                    "),\n",
                    "scheduled_pm AS (\n",
                    "  SELECT m.machine_id, m.maintenance_type, m.planned_start, m.technician\n",
                    "  FROM main.line_manager_ai_kit.maintenance_schedule m\n",
                    "  JOIN this_shift ts ON 1=1\n",
                    "  WHERE m.status IN ('scheduled', 'in_progress')\n",
                    "    AND m.planned_start BETWEEN ts.end_time AND ts.end_time + INTERVAL 8 HOURS\n",
                    ")\n",
                    "SELECT 'open_event' AS risk_type, machine_id AS subject,\n",
                    "  CONCAT(event_type, ' / ', severity) AS detail, CAST(timestamp_start AS STRING) AS started_at\n",
                    "FROM open_events\n",
                    "UNION ALL\n",
                    "SELECT 'low_inventory', material_id,\n",
                    "  CONCAT('on_hand=', CAST(on_hand_qty AS STRING), ' reorder=', CAST(reorder_point AS STRING)),\n",
                    "  NULL\n",
                    "FROM low_inventory\n",
                    "UNION ALL\n",
                    "SELECT 'upcoming_pm', machine_id,\n",
                    "  CONCAT(maintenance_type, ' / tech=', COALESCE(technician, 'unassigned')),\n",
                    "  CAST(planned_start AS STRING)\n",
                    "FROM scheduled_pm"
                ],
                "parameters": [
                    {"name": "shift_id", "type_hint": "STRING", "description": ["The shift handing over (e.g. shift_1)"], "default_value": {"values": ["shift_1"]}}
                ],
                "usage_guidance": [
                    "Use for 'carryover', 'what's carrying over to next shift', 'handover risks'. ",
                    "Returns a single ranked list of risk_type / subject / detail rows: open events, low-inventory materials, and any preventive maintenance scheduled within the next 8 hours."
                ]
            },
            {
                "id": "c1000000000000000000000000000013",
                "question": ["What's still open right now on the active shift?"],
                "sql": [
                    "WITH active AS (\n",
                    "  SELECT shift_id FROM main.line_manager_ai_kit.production_schedule WHERE status = 'in_progress' LIMIT 1\n",
                    ")\n",
                    "SELECT e.shift_id, e.machine_id, e.event_type, e.severity, e.timestamp_start, e.description\n",
                    "FROM main.line_manager_ai_kit.event_log e\n",
                    "JOIN active USING (shift_id)\n",
                    "WHERE e.timestamp_end IS NULL\n",
                    "ORDER BY e.severity, e.timestamp_start"
                ],
                "usage_guidance": [
                    "Use for 'what's open right now', 'mid-shift status', 'live issues'. ",
                    "Returns currently-open events on whichever shift is in_progress in the production schedule."
                ]
            },
        ],

        # Join specifications — documented in text instructions instead
        # (v2 API has strict proto parsing for sql field)

        # SQL Snippets: reusable measures, expressions, filters
        "sql_snippets": {
            "measures": [
                {
                    "id": "e1000000000000000000000000000001",
                    "alias": "oee_pct",
                    "sql": ["ROUND(oee, 1)"],
                    "display_name": "OEE percentage",
                    "synonyms": ["OEE %", "overall efficiency"],
                    "instruction": [
                        "Use when displaying OEE as a percentage. The oee column is already 0-100, ",
                        "so DO NOT multiply by 100 — just round to one decimal."
                    ]
                },
                {
                    "id": "e1000000000000000000000000000002",
                    "alias": "completion_pct",
                    "sql": ["ROUND(actual_cases * 100.0 / NULLIF(planned_cases, 0), 1)"],
                    "display_name": "plan completion rate",
                    "synonyms": ["completion %", "target achievement"],
                    "instruction": ["Use when calculating production plan completion"]
                },
                {
                    "id": "e1000000000000000000000000000003",
                    "alias": "fail_rate_pct",
                    "sql": ["ROUND(SUM(CASE WHEN result = 'fail' THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1)"],
                    "display_name": "quality fail rate",
                    "synonyms": ["reject rate", "defect rate", "failure rate"],
                    "instruction": ["Use when calculating quality check failure percentage from quality_checks table"]
                },
            ],
            "filters": [
                {
                    "id": "e2000000000000000000000000000001",
                    "sql": ["event_type IN ('major_stop', 'micro_stop')"],
                    "display_name": "downtime events only",
                    "synonyms": ["downtime", "stops", "outages"],
                    "instruction": ["Apply when the user asks about downtime, stops, or outages to exclude non-stop events"]
                },
                {
                    "id": "e2000000000000000000000000000002",
                    "sql": ["status = 'LOW'"],
                    "display_name": "low inventory materials",
                    "synonyms": ["low stock", "material shortage", "below safety stock"],
                    "instruction": ["Apply to inventory_status when user asks about materials that need attention"]
                },
                {
                    "id": "e2000000000000000000000000000003",
                    "sql": ["severity IN ('critical', 'major')"],
                    "display_name": "critical and major events",
                    "synonyms": ["serious events", "important alerts", "high severity"],
                    "instruction": ["Apply when user asks about serious, critical, or important events"]
                },
            ],
            "expressions": [
                {
                    "id": "e3000000000000000000000000000001",
                    "alias": "uptime_minutes",
                    "sql": ["SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END)"],
                    "display_name": "uptime minutes",
                    "synonyms": ["running time", "production time"],
                    "instruction": ["Use when calculating how many minutes a machine was running from machine_state"]
                },
                {
                    "id": "e3000000000000000000000000000002",
                    "alias": "downtime_minutes_calc",
                    "sql": ["SUM(CASE WHEN status IN ('stopped', 'starved') THEN 1 ELSE 0 END)"],
                    "display_name": "downtime minutes",
                    "synonyms": ["stopped time", "unproductive time"],
                    "instruction": ["Use when calculating downtime from machine_state (includes both stopped and starved)"]
                },
            ]
        }
    },

    # ── Benchmarks ──
    "benchmarks": {
        "questions": [
            {
                "id": "f1000000000000000000000000000001",
                "question": ["What is the OEE for shift_1?"],
                "answer": [
                    {
                        "format": "SQL",
                        "content": [
                            "SELECT shift_id, ROUND(oee, 1) AS oee_pct\n",
                            "FROM main.line_manager_ai_kit.oee_gold\n",
                            "WHERE shift_id = 'shift_1'"
                        ]
                    }
                ]
            },
            {
                "id": "f1000000000000000000000000000002",
                "question": ["How many downtime events were there in total?"],
                "answer": [
                    {
                        "format": "SQL",
                        "content": [
                            "SELECT COUNT(*) AS total_downtime_events\n",
                            "FROM main.line_manager_ai_kit.event_log\n",
                            "WHERE event_type IN ('major_stop', 'micro_stop')"
                        ]
                    }
                ]
            },
            {
                "id": "f1000000000000000000000000000003",
                "question": ["Which materials are below safety stock in shift_3?"],
                "answer": [
                    {
                        "format": "SQL",
                        "content": [
                            "SELECT material_id, on_hand_qty, safety_stock, status\n",
                            "FROM main.line_manager_ai_kit.inventory_status\n",
                            "WHERE shift_id = 'shift_3' AND status = 'LOW'\n",
                            "ORDER BY on_hand_qty ASC"
                        ]
                    }
                ]
            },
            {
                "id": "f1000000000000000000000000000004",
                "question": ["What is the production completion rate for all shifts?"],
                "answer": [
                    {
                        "format": "SQL",
                        "content": [
                            "SELECT shift_id, planned_cases, actual_cases,\n",
                            "  ROUND(actual_cases * 100.0 / NULLIF(planned_cases, 0), 1) AS completion_pct\n",
                            "FROM main.line_manager_ai_kit.production_schedule\n",
                            "ORDER BY shift_id"
                        ]
                    }
                ]
            },
            {
                "id": "f1000000000000000000000000000005",
                "question": ["What is the quality fail rate for the FILLER?"],
                "answer": [
                    {
                        "format": "SQL",
                        "content": [
                            "SELECT parameter,\n",
                            "  COUNT(*) AS total_checks,\n",
                            "  SUM(CASE WHEN result = 'fail' THEN 1 ELSE 0 END) AS fails,\n",
                            "  ROUND(SUM(CASE WHEN result = 'fail' THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) AS fail_rate_pct\n",
                            "FROM main.line_manager_ai_kit.quality_checks\n",
                            "WHERE machine_id = 'FILLER'\n",
                            "GROUP BY parameter"
                        ]
                    }
                ]
            },
            # ── Shift handover benchmarks ─────────────────────────────────
            {
                "id": "f1000000000000000000000000000006",
                "question": ["Give me the post-shift summary for shift_2"],
                "answer": [
                    {
                        "format": "SQL",
                        "content": [
                            "SELECT shift_id,\n",
                            "  ROUND(oee, 1) AS oee_pct,\n",
                            "  ROUND(availability, 1) AS availability_pct,\n",
                            "  ROUND(performance, 1) AS performance_pct,\n",
                            "  ROUND(quality, 1) AS quality_pct,\n",
                            "  total_cases, planned_cases,\n",
                            "  ROUND(total_cases * 100.0 / NULLIF(planned_cases, 0), 1) AS completion_pct,\n",
                            "  downtime_minutes, reject_count\n",
                            "FROM main.line_manager_ai_kit.oee_gold\n",
                            "WHERE shift_id = 'shift_2'"
                        ]
                    }
                ]
            },
            {
                "id": "f1000000000000000000000000000007",
                "question": ["What's carrying over from shift_1 to shift_2?"],
                "answer": [
                    {
                        "format": "SQL",
                        "content": [
                            "SELECT 'open_event' AS risk_type, machine_id AS subject,\n",
                            "  CONCAT(event_type, ' / ', severity) AS detail\n",
                            "FROM main.line_manager_ai_kit.event_log\n",
                            "WHERE shift_id = 'shift_1' AND timestamp_end IS NULL\n",
                            "UNION ALL\n",
                            "SELECT 'low_inventory', material_id,\n",
                            "  CONCAT('on_hand=', CAST(on_hand_qty AS STRING))\n",
                            "FROM main.line_manager_ai_kit.inventory_status\n",
                            "WHERE shift_id = 'shift_1' AND status = 'LOW'"
                        ]
                    }
                ]
            },
        ]
    }
}


# Post-process: sort column_configs alphabetically (API requirement)
for _tbl in serialized_space["data_sources"]["tables"]:
    if "column_configs" in _tbl:
        _tbl["column_configs"] = sorted(_tbl["column_configs"], key=lambda c: c["column_name"])


def main():
    parser = argparse.ArgumentParser(description="Push curated Genie Space config")
    parser.add_argument("--space-id", default=DEFAULT_SPACE_ID,
                        help=f"Genie space id (default: {DEFAULT_SPACE_ID})")
    parser.add_argument("--profile", default=DEFAULT_PROFILE,
                        help=f"Databricks CLI profile (default: {DEFAULT_PROFILE})")
    parser.add_argument("--warehouse-id", default=DEFAULT_WAREHOUSE_ID,
                        help="SQL warehouse id the space queries through")
    parser.add_argument("--catalog", default=DEFAULT_CATALOG,
                        help=("UC catalog the Genie space should target "
                              f"(default: {DEFAULT_CATALOG})"))
    parser.add_argument("--schema", default=DEFAULT_SCHEMA,
                        help=("UC schema the Genie space should target. The script's "
                              f"table refs are rewritten from {TEMPLATE_FQ_PREFIX!s} to "
                              f"<catalog>.<schema>. Default: {DEFAULT_SCHEMA}"))
    parser.add_argument("--title", default="ProdLine CoPilot - Brewery Production Analytics")
    parser.add_argument("--dry-run", action="store_true",
                        help="Write payload to /tmp/genie_curated.json without calling Databricks")
    args = parser.parse_args()

    target_prefix = f"{args.catalog}.{args.schema}"
    # Rewrite the script's template prefix (``main.line_manager_ai_kit``) into
    # the requested catalog/schema. We do this on the JSON-serialized blob so
    # every table identifier, every example SQL body, and every benchmark
    # answer string is rewritten consistently in one pass.
    serialized_blob = json.dumps(serialized_space).replace(TEMPLATE_FQ_PREFIX, target_prefix)

    payload = json.dumps({
        "warehouse_id": args.warehouse_id,
        "title": args.title,
        "description": ("Curated Genie Space for brewery packaging line analytics. "
                        "Covers OEE, machine states, events, quality, inventory, "
                        "maintenance, CIP, crew, and production scheduling across "
                        "7 shifts with 19 machines. Includes pre-shift briefing, "
                        "post-shift summary, and carryover-risk SQLs for handover."),
        "serialized_space": serialized_blob,
    })

    payload_path = "/tmp/genie_curated.json"
    with open(payload_path, "w") as f:
        f.write(payload)

    ss = serialized_space
    tables = ss.get("data_sources", {}).get("tables", [])
    examples = ss.get("instructions", {}).get("example_question_sqls", [])
    benchmarks = ss.get("benchmarks", {}).get("questions", [])
    samples = ss.get("config", {}).get("sample_questions", [])
    print(f"Payload written: {payload_path}")
    print(f"  warehouse_id : {args.warehouse_id}")
    print(f"  table prefix : {target_prefix}  "
          f"(rewritten from {TEMPLATE_FQ_PREFIX})")
    print(f"  tables       : {len(tables)}")
    print(f"  sample qs    : {len(samples)}")
    print(f"  example SQLs : {len(examples)}")
    print(f"  benchmarks   : {len(benchmarks)}")

    if args.dry_run:
        print("--dry-run set — skipping databricks api patch")
        return

    print(f"PATCH /api/2.0/genie/spaces/{args.space_id} via profile={args.profile}")
    result = subprocess.run(
        ["databricks", "-p", args.profile, "api", "patch",
         f"/api/2.0/genie/spaces/{args.space_id}",
         "--json", f"@{payload_path}"],
        capture_output=True, text=True
    )

    if result.returncode != 0:
        print(f"ERROR: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    resp = json.loads(result.stdout)
    print(f"Updated space: {resp.get('space_id')}")
    print(f"Title: {resp.get('title')}")


if __name__ == "__main__":
    main()
