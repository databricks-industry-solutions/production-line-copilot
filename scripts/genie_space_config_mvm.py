"""Curated Genie Space config for the GOVERNED industry data model (MVM).

This is a SEPARATE Genie space from the live streaming one
(``scripts/genie_space_config_streaming.py``). The two exist for different
audiences and must not be merged:

  * The *streaming* space answers real-time, mobile shift-briefing questions
    against the live per-second tables (``line_kpi``, ``machine_state``, ...).
    It is the in-app agent's Genie surface and is tuned for that demo.
  * THIS *governed* space answers standards-aligned, enterprise-schema
    questions against the Manufacturing MVM silver layer — a governed model
    whose tables mirror the canonical industry schema (``asset.*``,
    ``production.*``, ``quality.*``, ``inventory.*``). It is the "governed
    data + knowledge" story: an analyst or auditor can ask about the equipment
    register, PM schedule, production runs, NCRs, or stock balances and get
    answers grounded in the governed model rather than the raw stream.

Honesty rules baked into this config (do NOT drift from them):
  * The MVM tables carry the FULL enterprise schema (dozens of columns each),
    but this demo only POPULATES a curated subset from the simulator. Every
    other column is NULL. This space therefore describes ONLY the populated
    columns, and the instructions tell Genie not to rely on the empty ones.
  * The three incident-driven facts — ``asset.failure_record``,
    ``quality.inspection_result`` and ``quality.ncr`` — are intentionally
    EMPTY at steady state and self-populate only during incidents (they are
    fed by the streaming-silver MERGE path). Genie is told to expect zero rows
    when the line is healthy.
  * ``production.production_line.actual_oee_percentage`` is already a 0-100
    percentage (it mirrors ``line_kpi.oee_to_now``). DO NOT multiply by 100.
  * Cross-table joins use STABLE SURROGATE KEYS derived with ``xxhash64`` over
    a natural key (machine_id / zone / shift_id|sku / material_id). The join
    graph is documented in the instructions block below.

Unlike the streaming config, every table lives in a DIFFERENT schema within
the SAME catalog, so only the catalog is templated (``__CAT__``); the schema
is baked into each identifier.

PER-DOMAIN SPACES: ``--domain <asset|quality|inventory|production>`` renders a
narrow space covering just that schema — its tables, only the instructions and
join rules that apply to them, and only the examples whose SQL stays inside the
domain. This module is the single source of truth for all five spaces, so a
column description is written once. Each domain space is the Genie surface for
the specialists that work in that domain (see
``agent_server/tools/governed_domain_query.py``); the default ``--domain all``
still renders the combined analyst/auditor space.

Usage (update an existing space)::

    python scripts/genie_space_config_mvm.py \\
        --profile <your-profile> \\
        --space-id <genie_space_uuid> \\
        --warehouse-id <warehouse_id> \\
        --catalog <mvm_catalog>

Usage (create a NEW space — omit --space-id or pass SET_ME)::

    python scripts/genie_space_config_mvm.py \\
        --profile <your-profile> \\
        --warehouse-id <warehouse_id> \\
        --catalog <mvm_catalog> \\
        --parent-path /Users/<you>@<co>.com

On create the script POSTs ``/api/2.0/genie/spaces`` and prints a
``GENIE_SPACE_ID=<id>`` line so a deploy script can capture the new id. With a
real ``--space-id`` it PATCHes that space in place instead. Run with
``--dry-run`` to inspect the payload at ``/tmp/genie_curated_mvm.json`` without
calling the workspace.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

# Defaults are placeholders — override every value at the CLI. The script
# refuses to apply with placeholders in place (see main()).
DEFAULT_SPACE_ID = "SET_ME"
DEFAULT_PROFILE = "SET_ME"
DEFAULT_WAREHOUSE_ID = "SET_ME"
DEFAULT_CATALOG = "SET_ME"
CAT = "__CAT__"  # only the catalog is templated; schema is per-table literal


# ── Tables registered in the space ─────────────────────────────────────────
# Each table describes ONLY the columns the silver mapper populates
# (agent_server/serving/silver_mapper.py). The full MVM schema has many more
# columns, all NULL in this demo — see the module docstring.

tables = [
    {
        "identifier": f"{CAT}.asset.equipment_register",
        "description": [
            "Governed equipment register — one row per machine on the line "
            "(seeded from machine_registry). The asset master for the line."
        ],
        "column_configs": [
            {"column_name": "equipment_register_id", "description": ["Surrogate asset key = xxhash64(machine_id). Join key to failure_record, pm_schedule, inspection_result, ncr."]},
            {"column_name": "asset_number", "description": ["Natural machine id (FILLER, CAPPER, ...)."], "synonyms": ["machine id", "asset id"]},
            {"column_name": "equipment_name", "description": ["Human-readable machine name."]},
            {"column_name": "equipment_class", "description": ["Functional class / machine type (filler, capper, ...)."]},
            {"column_name": "asset_category", "description": ["Always 'Production Equipment' in this demo."]},
            {"column_name": "manufacturer_name", "description": ["OEM / manufacturer."]},
            {"column_name": "rated_capacity", "description": ["Rated cases per minute."]},
            {"column_name": "operational_status", "description": ["'active' for all line assets in this demo."]},
            {"column_name": "criticality_ranking", "description": ["Asset criticality (High in this demo)."]},
            {"column_name": "location_id", "description": ["FK to asset.location = xxhash64(zone)."]},
            {"column_name": "work_center_id", "description": ["FK to production.work_center = xxhash64(machine_id)."]},
        ],
    },
    {
        "identifier": f"{CAT}.asset.location",
        "description": ["Governed functional locations — one row per production zone."],
        "column_configs": [
            {"column_name": "location_id", "description": ["Surrogate location key = xxhash64(zone). FK target from equipment_register and work_center."]},
            {"column_name": "location_name", "description": ["Zone name."]},
            {"column_name": "location_type", "description": ["'production_zone' in this demo."]},
            {"column_name": "production_line_code", "description": ["Line code (LINE_1)."]},
            {"column_name": "site_name", "description": ["Plant/site name."]},
            {"column_name": "location_status", "description": ["'active'."]},
        ],
    },
    {
        "identifier": f"{CAT}.asset.pm_schedule",
        "description": [
            "Governed preventive-maintenance schedule — one row per machine "
            "(seeded from maintenance_windows). Use next_due_date to find PMs "
            "due or overdue."
        ],
        "column_configs": [
            {"column_name": "pm_schedule_id", "description": ["Surrogate PM key = xxhash64(machine_id)."]},
            {"column_name": "equipment_register_id", "description": ["FK to asset.equipment_register = xxhash64(machine_id)."]},
            {"column_name": "schedule_name", "description": ["e.g. 'PM - FILLER'."]},
            {"column_name": "schedule_number", "description": ["Natural machine id."]},
            {"column_name": "maintenance_type", "description": ["PM type (preventive, inspection, lubrication)."]},
            {"column_name": "trigger_type", "description": ["'time_based'."]},
            {"column_name": "frequency_unit", "description": ["'hours'."]},
            {"column_name": "frequency_value", "description": ["Interval in hours between PMs (string)."]},
            {"column_name": "last_completion_date", "description": ["Date the last PM was completed."]},
            {"column_name": "next_due_date", "description": ["Date the next PM is due. OVERDUE when < current_date()."], "synonyms": ["due date", "next pm"]},
            {"column_name": "schedule_status", "description": ["'active'."]},
        ],
    },
    {
        "identifier": f"{CAT}.asset.failure_record",
        "description": [
            "Governed failure records (downtime events). EMPTY at steady state "
            "— self-populates via the streaming-silver path during downtime "
            "incidents. Expect zero rows when the line is healthy."
        ],
        "column_configs": [
            {"column_name": "failure_record_id", "description": ["Surrogate key = xxhash64(event_id)."]},
            {"column_name": "equipment_register_id", "description": ["FK to asset.equipment_register = xxhash64(machine_id)."]},
            {"column_name": "production_line_id", "description": ["FK to production.production_line."]},
            {"column_name": "failure_number", "description": ["Natural event id."]},
            {"column_name": "failure_datetime", "description": ["When the failure started."]},
            {"column_name": "downtime_start_datetime", "description": ["Downtime start."]},
            {"column_name": "downtime_end_datetime", "description": ["Downtime end (NULL while still open)."]},
            {"column_name": "downtime_duration_minutes", "description": ["Downtime minutes (NULL while open)."]},
            {"column_name": "failure_mode_description", "description": ["Event type / failure mode."]},
            {"column_name": "failure_cause_description", "description": ["Root cause tag."]},
            {"column_name": "failure_narrative", "description": ["Long-form description."]},
            {"column_name": "remedy_description", "description": ["Corrective action taken."]},
            {"column_name": "severity_rating", "description": ["critical > major > minor > info."]},
            {"column_name": "maintenance_type", "description": ["'corrective'."]},
            {"column_name": "record_status", "description": ["'closed'."]},
        ],
    },
    {
        "identifier": f"{CAT}.production.production_line",
        "description": [
            "Governed production line master — a SINGLE row for the line, "
            "carrying the current OEE. actual_oee_percentage is refreshed at "
            "streaming latency from the latest line_kpi row."
        ],
        "column_configs": [
            {"column_name": "production_line_id", "description": ["Stable line key = xxhash64('LINE_1')."]},
            {"column_name": "line_code", "description": ["LINE_1."]},
            {"column_name": "line_name", "description": ["'Production Line 1'."]},
            {"column_name": "line_type", "description": ["'packaging'."]},
            {"column_name": "operational_status", "description": ["'running'."]},
            {"column_name": "actual_oee_percentage", "description": ["Current OEE as a PERCENTAGE 0-100 (e.g. 74.5 = 74.5%). DO NOT multiply by 100."], "synonyms": ["OEE", "overall equipment effectiveness"]},
            {"column_name": "target_oee_percentage", "description": ["Target OEE (85.0)."]},
            {"column_name": "number_of_stations", "description": ["Machine count on the line (string)."]},
            {"column_name": "throughput_unit_of_measure", "description": ["'cases_per_min'."]},
        ],
    },
    {
        "identifier": f"{CAT}.production.schedule",
        "description": ["Governed production schedule — one row per shift/SKU (planned quantity)."],
        "column_configs": [
            {"column_name": "schedule_id", "description": ["Surrogate key = xxhash64(shift_id|sku). Join key to production.run."]},
            {"column_name": "schedule_number", "description": ["Natural shift id."]},
            {"column_name": "planned_quantity", "description": ["Planned cases for the shift."], "synonyms": ["plan", "target cases"]},
            {"column_name": "schedule_status", "description": ["planned | in_progress | completed | aborted."]},
            {"column_name": "unit_of_measure", "description": ["'cases'."]},
        ],
    },
    {
        "identifier": f"{CAT}.production.run",
        "description": [
            "Governed production runs — one row per scheduled shift, with "
            "planned vs actual quantity. Joins to production.schedule via "
            "schedule_id."
        ],
        "column_configs": [
            {"column_name": "run_id", "description": ["Surrogate key = xxhash64(shift_id|sku)."]},
            {"column_name": "schedule_id", "description": ["FK to production.schedule = xxhash64(shift_id|sku)."]},
            {"column_name": "run_number", "description": ["Natural shift id."]},
            {"column_name": "run_type", "description": ["'production'."]},
            {"column_name": "run_status", "description": ["planned | in_progress | completed | aborted."]},
            {"column_name": "planned_quantity", "description": ["Planned cases."], "synonyms": ["planned cases"]},
            {"column_name": "actual_quantity", "description": ["Actual cases produced."], "synonyms": ["actual cases"]},
            {"column_name": "unit_of_measure", "description": ["'cases'."]},
        ],
    },
    {
        "identifier": f"{CAT}.production.work_center",
        "description": ["Governed work centers — one per machine/station (seeded from machine_registry)."],
        "column_configs": [
            {"column_name": "work_center_id", "description": ["Surrogate key = xxhash64(machine_id). Matches equipment_register.work_center_id."]},
            {"column_name": "location_id", "description": ["FK to asset.location = xxhash64(zone)."]},
            {"column_name": "work_center_code", "description": ["Natural machine id."]},
            {"column_name": "work_center_name", "description": ["Machine name."]},
            {"column_name": "work_center_category", "description": ["Machine type."]},
            {"column_name": "work_center_status", "description": ["'active'."]},
            {"column_name": "number_of_machines", "description": ["'1' per work center in this demo (string)."]},
            {"column_name": "available_capacity_per_shift", "description": ["Nominal capacity (cases/min)."]},
        ],
    },
    {
        "identifier": f"{CAT}.quality.inspection_result",
        "description": [
            "Governed inspection results (quality samples). EMPTY at steady "
            "state — self-populates via the streaming-silver path during "
            "quality activity. Expect zero rows when nothing is being sampled."
        ],
        "column_configs": [
            {"column_name": "inspection_result_id", "description": ["Surrogate key = xxhash64(check_id)."]},
            {"column_name": "equipment_register_id", "description": ["FK to asset.equipment_register = xxhash64(machine_id)."]},
            {"column_name": "characteristic_type", "description": ["'variable'."]},
            {"column_name": "attribute_result", "description": ["pass | fail | not_applicable."]},
            {"column_name": "measured_value", "description": ["Measured value."]},
            {"column_name": "nominal_value", "description": ["Midpoint of spec limits."]},
            {"column_name": "lower_spec_limit", "description": ["Lower spec limit."]},
            {"column_name": "upper_spec_limit", "description": ["Upper spec limit."]},
            {"column_name": "is_out_of_spec", "description": ["TRUE when the sample failed spec."]},
            {"column_name": "result_status", "description": ["Raw result (OK / NOK)."]},
            {"column_name": "defect_description", "description": ["Parameter that was measured."]},
            {"column_name": "inspection_timestamp", "description": ["When the sample was taken."]},
            {"column_name": "inspection_date", "description": ["Date of the sample."]},
        ],
    },
    {
        "identifier": f"{CAT}.quality.ncr",
        "description": [
            "Governed non-conformance reports. EMPTY at steady state — "
            "self-populates via the streaming-silver path from FAILED quality "
            "checks during quality incidents. Zero rows means no failures."
        ],
        "column_configs": [
            {"column_name": "ncr_id", "description": ["Surrogate key = xxhash64(check_id)."]},
            {"column_name": "equipment_register_id", "description": ["FK to asset.equipment_register = xxhash64(machine_id)."]},
            {"column_name": "production_line_id", "description": ["FK to production.production_line."]},
            {"column_name": "ncr_number", "description": ["Natural check id."]},
            {"column_name": "ncr_status", "description": ["'open' when raised."]},
            {"column_name": "ncr_type", "description": ["'in_process'."]},
            {"column_name": "detection_source", "description": ["'in_line_inspection'."]},
            {"column_name": "defect_code", "description": ["Parameter that failed."]},
            {"column_name": "nonconformance_description", "description": ["Out-of-spec description with value."]},
            {"column_name": "severity", "description": ["'major'."]},
            {"column_name": "detection_timestamp", "description": ["When the failure was detected."]},
            {"column_name": "reported_timestamp", "description": ["When the NCR was reported."]},
        ],
    },
    {
        "identifier": f"{CAT}.inventory.material_master",
        "description": ["Governed material master — one row per distinct BOM material."],
        "column_configs": [
            {"column_name": "material_master_id", "description": ["Surrogate key = xxhash64(material_id). FK target from stock_balance."]},
            {"column_name": "material_number", "description": ["Natural material id."]},
            {"column_name": "material_description", "description": ["Material name."]},
            {"column_name": "base_unit_of_measure", "description": ["Base UoM."]},
            {"column_name": "material_type", "description": ["'RAW'."]},
            {"column_name": "material_status", "description": ["'active'."]},
        ],
    },
    {
        "identifier": f"{CAT}.inventory.stock_balance",
        "description": [
            "Governed stock balance — latest on-hand per material. Refreshed "
            "at streaming latency. Low stock = quantity_on_hand <= reorder_point."
        ],
        "column_configs": [
            {"column_name": "stock_balance_id", "description": ["Surrogate key = xxhash64(material_id)."]},
            {"column_name": "material_master_id", "description": ["FK to inventory.material_master = xxhash64(material_id)."]},
            {"column_name": "quantity_on_hand", "description": ["Current on-hand quantity."], "synonyms": ["on hand", "stock"]},
            {"column_name": "available_quantity", "description": ["Available quantity (= on hand in this demo)."]},
            {"column_name": "safety_stock_quantity", "description": ["Safety stock level."]},
            {"column_name": "reorder_point", "description": ["Reorder point. Reorder when quantity_on_hand <= reorder_point."]},
            {"column_name": "stock_status", "description": ["OK or LOW."]},
            {"column_name": "unit_of_measure", "description": ["'unit'."]},
            {"column_name": "last_updated_timestamp", "description": ["Snapshot timestamp."]},
        ],
    },
]


# ── Sample questions ───────────────────────────────────────────────────────

sample_questions = [
    {"id": "a3000000000000000000000000000001", "question": ["List every machine in the equipment register with its manufacturer"]},
    {"id": "a3000000000000000000000000000002", "question": ["Which preventive-maintenance schedules are overdue?"]},
    {"id": "a3000000000000000000000000000003", "question": ["Show planned vs actual quantity for each production run"]},
    {"id": "a3000000000000000000000000000004", "question": ["What is the current OEE from the governed production line model?"]},
    {"id": "a3000000000000000000000000000005", "question": ["List open non-conformance reports (NCRs)"]},
    {"id": "a3000000000000000000000000000006", "question": ["Which materials are at or below their reorder point?"]},
    {"id": "a3000000000000000000000000000007", "question": ["Show recent failure records with downtime minutes"]},
    {"id": "a3000000000000000000000000000008", "question": ["List work centers and their available capacity per shift"]},
    {"id": "a3000000000000000000000000000009", "question": ["How many open NCRs are there by defect code?"]},
    {"id": "a3000000000000000000000000000010", "question": ["Which inspection results fell outside their specification limits?"]},
]


# ── Example SQLs ───────────────────────────────────────────────────────────

example_question_sqls = [
    {
        "id": "c3000000000000000000000000000001",
        "question": ["List every machine in the equipment register with its manufacturer"],
        "sql": [
            "SELECT asset_number, equipment_name, equipment_class,\n",
            "  manufacturer_name, rated_capacity, criticality_ranking\n",
            "FROM __CAT__.asset.equipment_register\n",
            "ORDER BY asset_number",
        ],
        "parameters": [],
        "usage_guidance": [
            "The governed asset master. One row per machine, seeded from the line's machine registry."
        ],
    },
    {
        "id": "c3000000000000000000000000000002",
        "question": ["Which preventive-maintenance schedules are overdue or due soon?"],
        "sql": [
            "SELECT p.schedule_number AS machine, p.maintenance_type,\n",
            "  CAST(p.next_due_date AS STRING) AS next_due_date,\n",
            "  CASE WHEN p.next_due_date < current_date() THEN 'OVERDUE'\n",
            "       WHEN p.next_due_date <= current_date() + INTERVAL 7 DAYS THEN 'DUE_SOON'\n",
            "       ELSE 'future' END AS pm_status\n",
            "FROM __CAT__.asset.pm_schedule p\n",
            "ORDER BY p.next_due_date",
        ],
        "parameters": [],
        "usage_guidance": [
            "Governed PM plan. next_due_date drives overdue/due-soon status."
        ],
    },
    {
        "id": "c3000000000000000000000000000003",
        "question": ["Show planned vs actual quantity for each production run"],
        "sql": [
            "SELECT run_number, run_status,\n",
            "  planned_quantity, actual_quantity,\n",
            "  ROUND(actual_quantity * 100.0 / NULLIF(planned_quantity, 0), 1) AS completion_pct\n",
            "FROM __CAT__.production.run\n",
            "ORDER BY run_number",
        ],
        "parameters": [],
    },
    {
        "id": "c3000000000000000000000000000004",
        "question": ["What is the current OEE from the governed production line model?"],
        "sql": [
            "SELECT line_code, line_name, operational_status,\n",
            "  actual_oee_percentage, target_oee_percentage\n",
            "FROM __CAT__.production.production_line",
        ],
        "parameters": [],
        "usage_guidance": [
            "actual_oee_percentage is ALREADY 0-100 — do NOT multiply by 100. Single-row table."
        ],
    },
    {
        "id": "c3000000000000000000000000000005",
        "question": ["List open non-conformance reports (NCRs)"],
        "sql": [
            "SELECT n.ncr_number, e.asset_number AS machine, n.defect_code,\n",
            "  n.nonconformance_description, n.severity,\n",
            "  CAST(n.detection_timestamp AS STRING) AS detected_at\n",
            "FROM __CAT__.quality.ncr n\n",
            "LEFT JOIN __CAT__.asset.equipment_register e\n",
            "  ON n.equipment_register_id = e.equipment_register_id\n",
            "WHERE n.ncr_status = 'open'\n",
            "ORDER BY n.detection_timestamp DESC",
        ],
        "parameters": [],
        "usage_guidance": [
            "NCRs are EMPTY at steady state and populate during quality incidents. Zero rows = no failures."
        ],
    },
    # The two examples below deliberately stay inside the quality schema. The
    # NCR example above joins asset.equipment_register, so it is dropped from
    # the Quality domain space — which would otherwise ship with no examples at
    # all, the condition Genie handles worst.
    {
        "id": "c3000000000000000000000000000007",
        "question": ["How many open NCRs are there by defect code?"],
        "sql": [
            "SELECT defect_code, severity, COUNT(*) AS ncr_count\n",
            "FROM __CAT__.quality.ncr\n",
            "WHERE ncr_status = 'open'\n",
            "GROUP BY defect_code, severity\n",
            "ORDER BY ncr_count DESC",
        ],
        "parameters": [],
        "usage_guidance": [
            "Quality-domain rollup with no cross-domain join. Empty at steady state — "
            "report zero honestly rather than inventing defect codes."
        ],
    },
    {
        "id": "c3000000000000000000000000000008",
        "question": ["Which inspection results fell outside their specification limits?"],
        "sql": [
            "SELECT characteristic_type, attribute_result, measured_value,\n",
            "  nominal_value, lower_spec_limit, upper_spec_limit\n",
            "FROM __CAT__.quality.inspection_result\n",
            "WHERE measured_value < lower_spec_limit\n",
            "   OR measured_value > upper_spec_limit\n",
            "ORDER BY measured_value DESC",
        ],
        "parameters": [],
        "usage_guidance": [
            "Out-of-spec detection reads the limit columns rather than assuming a "
            "tolerance. Populated only during quality incidents."
        ],
    },
    {
        "id": "c3000000000000000000000000000006",
        "question": ["Which materials are at or below their reorder point?"],
        "sql": [
            "SELECT m.material_number, m.material_description,\n",
            "  s.quantity_on_hand, s.reorder_point, s.stock_status\n",
            "FROM __CAT__.inventory.stock_balance s\n",
            "JOIN __CAT__.inventory.material_master m\n",
            "  ON s.material_master_id = m.material_master_id\n",
            "WHERE s.quantity_on_hand <= s.reorder_point OR s.stock_status = 'LOW'\n",
            "ORDER BY s.quantity_on_hand",
        ],
        "parameters": [],
    },
]


# ── Domains ────────────────────────────────────────────────────────────────
# The governed model organises its entities into four schemas, and those are
# the boundaries the per-domain spaces follow. They come from the manufacturing
# standard rather than from our agent list, which is what makes them defensible
# to a customer: the reason Quality and Asset are separate spaces is that they
# are separate domains in the model, not that we happen to have two agents.
#
# Narrow scope is also what makes Genie accurate. A space holding two
# well-described tables answers far more reliably than one holding twelve, so
# the split is a quality decision before it is a story.

DOMAINS: dict[str, dict[str, str]] = {
    "asset": {
        "title": "ProdLine CoPilot — Governed Asset Domain",
        "description": (
            "Governed asset domain: the equipment register, functional locations, "
            "preventive-maintenance schedule and failure records. Use for equipment "
            "master data, PM due/overdue questions and failure history."
        ),
    },
    "quality": {
        "title": "ProdLine CoPilot — Governed Quality Domain",
        "description": (
            "Governed quality domain: inspection results and non-conformance reports "
            "(NCRs). Use for inspection outcomes, defect classification and open "
            "quality holds against the canonical schema."
        ),
    },
    "inventory": {
        "title": "ProdLine CoPilot — Governed Inventory Domain",
        "description": (
            "Governed inventory domain: material master and stock balances. Use for "
            "on-hand quantities, reorder points and material coverage."
        ),
    },
    "production": {
        "title": "ProdLine CoPilot — Governed Production Domain",
        "description": (
            "Governed production domain: the production line, work centers, the "
            "schedule and production runs. Use for planned-vs-actual, run history, "
            "governed OEE and capacity questions."
        ),
    },
}

# Sample questions carry no schema reference, so the mapping is explicit.
# Anything missing here fails the build rather than being silently dropped —
# see build_space().
SAMPLE_QUESTION_DOMAINS: dict[str, str] = {
    "a3000000000000000000000000000001": "asset",
    "a3000000000000000000000000000002": "asset",
    "a3000000000000000000000000000003": "production",
    "a3000000000000000000000000000004": "production",
    "a3000000000000000000000000000005": "quality",
    "a3000000000000000000000000000006": "inventory",
    "a3000000000000000000000000000007": "asset",
    "a3000000000000000000000000000008": "production",
    "a3000000000000000000000000000009": "quality",
    "a3000000000000000000000000000010": "quality",
}


# ── Text instructions (single block per Genie API rules) ───────────────────
# Structured as tagged fragments so a domain space carries only the rules that
# apply to the tables it can see. Telling a two-table Quality space how to join
# the inventory graph is noise, and noise is what degrades Genie.

_PREAMBLE = [
    "This Genie space answers questions against the GOVERNED Manufacturing ",
    "industry data model (MVM) — a canonical enterprise schema materialized as ",
    "a silver layer. It is SEPARATE from the live streaming space; use it for ",
    "standards-aligned, analytical questions about the governed model, not for ",
    "per-second real-time monitoring. ",
    "The MVM tables carry the FULL enterprise schema (dozens of columns each), ",
    "but this demo POPULATES ONLY a curated subset. Every other column is NULL ",
    "— do not build answers on columns not described here. ",
]

# (domains this fact applies to, text)
_FACTS: list[tuple[frozenset[str], list[str]]] = [
    (
        frozenset({"asset", "quality"}),
        [
            "Incident-driven facts — asset.failure_record, quality.inspection_result ",
            "and quality.ncr — are EMPTY at steady state and self-populate only ",
            "during incidents (fed by the streaming-silver MERGE path). Return zero ",
            "rows honestly when the line is healthy; do not fabricate rows. ",
        ],
    ),
    (
        frozenset({"production"}),
        [
            "production.production_line has ONE row. actual_oee_percentage is ",
            "already a PERCENTAGE 0-100 (mirrors line_kpi.oee_to_now) — DO NOT ",
            "multiply by 100. ",
        ],
    ),
    (
        frozenset({"asset"}),
        [
            "asset.pm_schedule: a PM is OVERDUE when next_due_date < current_date(); ",
            "DUE_SOON within the next 7 days. ",
        ],
    ),
    (
        frozenset({"inventory"}),
        [
            "inventory.stock_balance: a material needs reorder when ",
            "quantity_on_hand <= reorder_point OR stock_status = 'LOW'. ",
        ],
    ),
]

# Join rules, tagged with every domain they span. A rule survives only when the
# space can see both sides of it.
_JOINS: list[tuple[frozenset[str], list[str]]] = [
    (
        frozenset({"asset"}),
        [
            "asset.equipment_register.equipment_register_id = ",
            "asset.failure_record.equipment_register_id = ",
            "asset.pm_schedule.equipment_register_id (= xxhash64(machine_id)). ",
        ],
    ),
    (
        frozenset({"asset", "quality"}),
        [
            "asset.equipment_register.equipment_register_id = ",
            "quality.inspection_result.equipment_register_id = ",
            "quality.ncr.equipment_register_id (all = xxhash64(machine_id)). ",
        ],
    ),
    (
        frozenset({"asset", "production"}),
        [
            "asset.equipment_register.work_center_id = ",
            "production.work_center.work_center_id (= xxhash64(machine_id)). ",
            "asset.equipment_register.location_id = asset.location.location_id = ",
            "production.work_center.location_id (= xxhash64(zone)). ",
        ],
    ),
    (
        frozenset({"production"}),
        [
            "production.schedule.schedule_id = production.run.schedule_id ",
            "(= xxhash64(shift_id|sku)). ",
        ],
    ),
    (
        frozenset({"inventory"}),
        [
            "inventory.stock_balance.material_master_id = ",
            "inventory.material_master.material_master_id (= xxhash64(material_id)). ",
        ],
    ),
]

_CLOSING = [
    "Prefer joining on the surrogate *_id keys, and surface the natural ",
    "identifiers (asset_number, material_number, run_number) in results so a ",
    "human can read them.",
]


def table_domain(identifier: str) -> str:
    """``__CAT__.quality.ncr`` -> ``quality``."""
    parts = identifier.split(".")
    return parts[1] if len(parts) >= 3 else ""


def sql_domains(entry: dict) -> set[str]:
    """Every governed domain an example SQL touches."""
    blob = "".join(entry.get("sql", []))
    return {d for d in DOMAINS if f"__CAT__.{d}." in blob}


def build_instructions(scope: set[str]) -> list[str]:
    """Assemble the instruction block for a space covering ``scope``."""
    content = list(_PREAMBLE)
    facts = [text for domains, text in _FACTS if domains & scope]
    if facts:
        content.append("Key facts: ")
        for text in facts:
            content.append("  * ")
            content.extend(text)
    # A join rule is only usable when the space can see every side of it.
    joins = [text for domains, text in _JOINS if domains <= scope]
    if joins:
        content.append("JOIN GRAPH (all keys are xxhash64 surrogate keys): ")
        for text in joins:
            content.append("  * ")
            content.extend(text)
    content.extend(_CLOSING)
    return content


def build_space(scope: set[str]) -> dict:
    """Build the serialized_space payload for the given governed domains.

    ``scope`` of all four domains reproduces the single combined space, which is
    what the standalone governed space still uses.
    """
    unknown = scope - set(DOMAINS)
    if unknown:
        raise SystemExit(f"unknown governed domain(s): {', '.join(sorted(unknown))}")

    scoped_tables = [t for t in tables if table_domain(t["identifier"]) in scope]
    if not scoped_tables:
        raise SystemExit(f"no tables for domain scope {sorted(scope)}")

    untagged = {q["id"] for q in sample_questions} - set(SAMPLE_QUESTION_DOMAINS)
    if untagged:
        raise SystemExit(
            "sample question(s) missing from SAMPLE_QUESTION_DOMAINS: "
            + ", ".join(sorted(untagged))
        )
    scoped_questions = [q for q in sample_questions if SAMPLE_QUESTION_DOMAINS[q["id"]] in scope]

    # Keep an example only when every table it references is in scope —
    # a cross-domain example would teach the space to query tables it cannot see.
    scoped_sqls = [e for e in example_question_sqls if sql_domains(e) and sql_domains(e) <= scope]

    return {
        "version": 2,
        "data_sources": {"tables": scoped_tables},
        "config": {"sample_questions": scoped_questions},
        "instructions": {
            "text_instructions": [
                {
                    "id": "b3000000000000000000000000000001",
                    "content": build_instructions(scope),
                }
            ],
            "example_question_sqls": scoped_sqls,
        },
    }


# The combined space, exposed at module level so callers (and tests) can read
# the full governed model without invoking the CLI. `--domain all` renders this.
serialized_space = build_space(set(DOMAINS))


def main():
    parser = argparse.ArgumentParser(description="Push governed MVM Genie Space config")
    parser.add_argument("--space-id", default=DEFAULT_SPACE_ID,
                        help=f"Genie space id (default: {DEFAULT_SPACE_ID})")
    parser.add_argument("--profile", default=DEFAULT_PROFILE,
                        help=f"Databricks CLI profile (default: {DEFAULT_PROFILE})")
    parser.add_argument("--warehouse-id", default=DEFAULT_WAREHOUSE_ID,
                        help="SQL warehouse id the space queries through")
    parser.add_argument("--catalog", default=DEFAULT_CATALOG,
                        help=f"MVM catalog (default: {DEFAULT_CATALOG})")
    parser.add_argument("--title", default="")
    parser.add_argument("--title-suffix", default="",
                        help="Appended to every space title (e.g. 'dev'). Needed "
                             "when one workspace hosts more than one environment, "
                             "since Genie spaces collide on name.")
    parser.add_argument(
        "--domain",
        default="all",
        choices=["all", *sorted(DOMAINS)],
        help="Governed domain this space covers. 'all' (default) builds the "
             "single combined space; a domain name builds the narrow space the "
             "matching specialists query.",
    )
    parser.add_argument("--parent-path", default="",
                        help="Workspace folder to register a NEWLY created space under "
                             "(e.g. /Users/me@co.com). Only used when creating.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Write payload to /tmp without calling Databricks")
    args = parser.parse_args()

    # A blank / SET_ME --space-id means "create a new space"; a real id means
    # "update that space in place".
    create_mode = (args.space_id or "").strip() in ("", "SET_ME")

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

    scope = set(DOMAINS) if args.domain == "all" else {args.domain}
    serialized_space = build_space(scope)

    if args.domain == "all":
        title = args.title or "ProdLine CoPilot — Governed Industry Model (MVM)"
        description = (
            "Governed Manufacturing industry data model (MVM) analytics. Covers the "
            "equipment register, functional locations, PM schedule, production line + "
            "runs, work centers, inspection results, non-conformance reports, and "
            "inventory stock balances. Standards-aligned schema; a curated subset of "
            "columns is populated in this demo."
        )
    else:
        title = args.title or DOMAINS[args.domain]["title"]
        description = DOMAINS[args.domain]["description"]

    # Genie spaces are workspace files, so two envs in one workspace collide on
    # name ("Node named '<title>.geniespace.json' already exists"). --title only
    # names one space and this script creates five, so the suffix is applied
    # here rather than asking the caller to name each one.
    if (args.title_suffix or "").strip():
        title = f"{title} — {args.title_suffix.strip()}"

    # Genie's export proto enforces alphabetical ordering everywhere. Sort in
    # place before serialization so the script is order-independent.
    serialized_space["data_sources"]["tables"].sort(key=lambda t: t["identifier"])
    for t in serialized_space["data_sources"]["tables"]:
        t["column_configs"].sort(key=lambda c: c["column_name"])
    serialized_space["config"]["sample_questions"].sort(key=lambda q: q["id"])
    serialized_space["instructions"]["example_question_sqls"].sort(key=lambda e: e["id"])

    # Substitute the catalog placeholder in every identifier and SQL.
    blob = json.dumps(serialized_space)
    blob = blob.replace("__CAT__", args.catalog)

    payload_dict = {
        "warehouse_id": args.warehouse_id,
        "title": title,
        "description": description,
        "serialized_space": blob,
    }
    if create_mode and (args.parent_path or "").strip():
        payload_dict["parent_path"] = args.parent_path.strip()
    payload = json.dumps(payload_dict)

    suffix = "" if args.domain == "all" else f"_{args.domain}"
    payload_path = Path(f"/tmp/genie_curated_mvm{suffix}.json")
    payload_path.write_text(payload)
    print(f"Payload written: {payload_path}")
    print(f"  mode         : {'create' if create_mode else 'update'}")
    print(f"  domain       : {args.domain}")
    print(f"  warehouse_id : {args.warehouse_id}")
    print(f"  catalog      : {args.catalog}")
    print(f"  tables       : {len(serialized_space['data_sources']['tables'])}")
    print(f"  sample qs    : {len(serialized_space['config']['sample_questions'])}")
    print(f"  example SQLs : {len(serialized_space['instructions']['example_question_sqls'])}")

    if args.dry_run:
        print("--dry-run set — skipping databricks api call")
        return

    if create_mode:
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
