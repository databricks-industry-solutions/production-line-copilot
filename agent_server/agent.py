"""Top-level agent orchestrator: query → context → routing → specialist → answer.

Public entry points (both used by `agent_server/start_server.py`):
    invoke()          synchronous; returns a single dict — used by /api/agent
                      and by every Python-side caller (CLI smoke tests, eval).
    invoke_stream()   generator; yields SSE events — used by the chat UI so
                      the user sees the snapshot + tool trace as it happens.

Flow (both paths share the same first 4 steps):
    1. validate query length and load/persist the conversation
    2. build_context() pulls the historical bundle from UC
    3. _overlay_live_state() replaces operational slices with the live
       ai_serving.* Lakebase snapshot (same source as the dashboard SSE)
    4. _quick_snapshot() produces a deterministic data-only summary that
       is shown immediately, so the user has something to read while the
       specialist is still thinking
    5. ROUTING:
         a. _route_by_active_incident() — if there's a P1 incident on the
            line, deterministically pick the right specialist for its
            scenario_id. This preempts shift_briefing so an operator asking
            "brief me" during a breakdown still gets the downtime analyst.
         b. _classify_intent() — otherwise LLM-classify the query against
            ORCHESTRATOR_SYSTEM and pick a specialist.
    6. _run_specialist*() runs the ReAct agent (or the lighter "general"
       fallback when routing returned "general" or an unregistered name).
    7. _synthesize_response() reformats the specialist's output to the
       SYNTHESIS_TEMPLATE structure unless the response is already long,
       a timeout, or an explicit error — those bypass synthesis to avoid
       a second LLM call on a known-bad payload.
    8. _extract_actions() produces the deterministic HITL action chips
       shown next to the chat answer, keyed off specialist + active incidents.

Why so many LLM endpoints? Each role runs on a model chosen for the shape of
its work, across six vendors — routing on the cheapest fast model because every
request waits on it, root-cause analysis on the most reliable tool-caller,
strategy on a flagship reasoner because that is the one place its price is
worth paying. `agent_server/model_roster.py` holds the assignments, the
evidence behind them, and the fallback that keeps a workspace missing one of
these vendors working anyway.
"""
import json
import logging
import os
import queue
import re
import threading
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout

import mlflow
from databricks_langchain import ChatDatabricks
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from agent_server.context_builder import build_context
from agent_server.events import active_events, context_now_ts, shift_progress, to_epoch
from agent_server.llm_output import message_text
from agent_server.logged_model import activate as activate_logged_model
from agent_server.memory.conversation_store import ConversationStore
from agent_server.model_roster import ROSTER, describe, resolve

# Imported as a module: this file already has a local named ``prompt_text``,
# and the prompts are resolved through ``loader.prompt_text(slug)`` so the
# registry can supply them without the call sites caring which source won.
from agent_server.prompts import loader
from agent_server.specialists.downtime_analyst import create_downtime_analyst
from agent_server.specialists.maintenance_predictor import create_maintenance_predictor
from agent_server.specialists.oee_coach import create_oee_coach
from agent_server.specialists.quality_specialist import create_quality_specialist
from agent_server.specialists.schedule_optimizer import create_schedule_optimizer
from agent_server.specialists.shift_briefing import create_shift_briefing
from agent_server.specialists.strategic_advisor import create_strategic_advisor
from agent_server.specialists.supply_chain_advisor import create_supply_chain_advisor
from agent_server.tool_evidence import evidence_from_call, evidence_from_result
from agent_server.trace_link import current_trace_url

logger = logging.getLogger(__name__)

# Captures every LangChain ChatDatabricks/ReAct step as an MLflow trace span so
# we can query latency + tool-call breakdowns from the experiment view.
mlflow.langchain.autolog()

# Give those spans a model to hang off, so traces can be compared per release
# rather than only per experiment. Best-effort by construction — see the module.
activate_logged_model()

# Intent classification, the "general" fallback path and response synthesis are
# three different jobs, so they no longer share one endpoint. Routing picks one
# label from nine and every request waits on it, which wants the cheapest fast
# model; the other two are read verbatim by the user, which wants prose quality.
# See agent_server/model_roster.py.
_ROUTER_ROLE = "intent_router"
_GENERAL_ROLE = "general"


def _overlay_live_state(context: dict, shift_id: str | None = None) -> dict:
    """Replace operational slices of `context` with the live Lakebase snapshot.

    ``shift_id`` must be the shift the caller asked about — the same one passed
    to ``build_context``. It used to be read from ``SIM_SHIFT_ID`` here while
    the historical half of the context came from the caller's argument, so the
    two halves could describe different shifts: historical events for one,
    live machine state, KPIs and active incidents for another. Routing reads
    ``active_incidents``, so the specialist could be chosen by an incident on a
    shift nobody asked about.

    The HTTP path never hit it, because ``_server_shift_id`` deliberately
    overrides whatever the browser sends with the environment's id, making both
    halves agree by coincidence rather than by construction. The eval harness
    does hit it: ``evaluations/run_eval.py`` calls ``invoke`` directly with the
    shift from its dataset.

    Live machine / KPI / event / inventory / incident state is read from the
    same ``ai_serving.*`` Lakebase tables that feed the dashboard SSE stream, so
    the agent reasons over EXACTLY what the operator sees on screen. Reference
    data (constraints, crew, schedules, recommendations) stays as pulled from UC
    — the producer doesn't model those. Only the slices that change tick-to-tick
    get overwritten:
        line_state, current_kpi, active_events, inventory_snapshot,
        active_incidents, scenario.current_timestamp, scenario.scenario_name

    Returns the same dict (mutated). If Lakebase is unavailable or has no live
    rows for this shift yet, returns context untouched so the historical UC
    bundle is what the agent sees.

    Any failure here is swallowed and logged: a partial-overlay is worse than
    a clean historical fallback because specialists may already have read the
    half-overlaid line_state by the time we raise.

    Datetime columns (``ts``, ``created_at``, ``timestamp_start``) come back as
    Python ``datetime`` objects; they serialize fine because every context
    dump uses ``json.dumps(..., default=str)``.
    """
    try:
        from agent_server.serving.realtime_store import (
            read_commands_active,
            read_event_log_recent,
            read_inventory_latest,
            read_line_kpi_latest,
            read_machine_state_latest,
        )

        # The environment id is the fallback for callers that do not track a
        # shift at all, not an override of one that does.
        shift_id = (shift_id or "").strip() or os.environ.get("SIM_SHIFT_ID", "shift_1")
        machines = read_machine_state_latest(shift_id)
        if not machines:
            # No live rows (Lakebase unavailable, or the shift hasn't started
            # producing yet) — keep the historical UC bundle untouched.
            return context

        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()

        # machine_state_latest rows already carry the shape specialists read:
        # machine_id, status, throughput_per_min, speed_pct, alarm_count, ts.
        context["line_state"] = machines

        # KPI is precomputed by the streaming pipeline (same row the dashboard
        # KPIStrip renders), so no client-side synthesis — the agent's OEE / A /
        # P / Q match the screen exactly.
        kpi = read_line_kpi_latest(shift_id)
        if kpi:
            context["current_kpi"] = kpi

        # 20-event cap mirrors what the EventFeed UI shows; more is wasted tokens.
        context["active_events"] = read_event_log_recent(shift_id, limit=20)
        context["inventory_snapshot"] = read_inventory_latest(shift_id)
        # Active scenario commands keyed by scenario_id; the routing layer reads
        # this to deterministically pick a specialist.
        context["active_incidents"] = read_commands_active(shift_id)

        # Replaced rather than mutated in place. `build_context` hands out
        # `copy.copy` of its cached bundle, so this nested dict is the *same
        # object* the cache holds: writing into it stamped the cached entry as
        # live for the rest of its TTL. The next request to hit that entry with
        # Lakebase down then returned early above — on the documented promise of
        # keeping "the historical UC bundle untouched" — and handed the
        # specialist historical UC readings under a live scenario name and a
        # current timestamp. That is the half-overlaid state this function's own
        # docstring calls worse than a clean fallback.
        # The shift clock has to move with the timestamp. Advancing
        # current_timestamp while leaving elapsed/remaining as the UC bundle
        # computed them froze the clock at whatever it read when the bundle was
        # built: the snapshot said "Time remaining: 240 min" hours apart, and
        # in dev — where scenario_index has no row at all — the figure was
        # never live to begin with. Derive it the way the producer and the
        # header do, from the UTC-aligned shift window, so all three agree.
        elapsed_min, remaining_min = shift_progress()
        context["scenario"] = {
            **context.get("scenario", {}),
            "current_timestamp": now_iso,
            "scenario_name": "Live Real-Time (Lakebase)",
            "elapsed_minutes": elapsed_min,
            "remaining_minutes": remaining_min,
        }

        logger.info(
            "Overlaid live Lakebase state: shift=%s machines=%d events=%d incidents=%d",
            shift_id, len(machines), len(context["active_events"]),
            len(context["active_incidents"]),
        )
    except Exception:
        logger.exception("Failed to overlay live state — using historical context")

    return context

# 90 was under the work. Timing the downtime analyst end to end, on both
# environments: about 34s across seven tool calls, then roughly 40s of silence
# while the model composes over their combined output, then ten seconds of
# prose — 88s on dev and 93s on prod for the same question. A budget inside
# that spread does not bound a runaway, it decides coin flips, and the losing
# side of the flip threw the answer away. The tools are not the cost here and
# trimming them would not have bought enough.
SPECIALIST_TIMEOUT_SECONDS = int(os.environ.get("SPECIALIST_TIMEOUT_SECONDS", "150"))

# LangGraph counts super-steps, and one ReAct round costs two of them — the
# model node, then the tool node — with a final model node to write the answer.
# So this is 2 * tool_rounds + 1, and the old value of 10 allowed four rounds.
# That is not enough for a specialist that recovers from a bad query: the
# Quality Specialist spends one round on its governed domain, one on live SQL,
# one more when the first live query names a column that doesn't exist, and it
# was failing roughly two runs in three. Seven rounds leaves room to recover
# without becoming the thing that bounds a runaway loop. SPECIALIST_TIMEOUT_SECONDS
# bounds how long the USER waits, in wall clock rather than step count — but see
# below: it does not bound how long the work runs.
SPECIALIST_RECURSION_LIMIT = int(os.environ.get("SPECIALIST_RECURSION_LIMIT", "16"))
# A specialist run holds a worker until it finishes, which is NOT the same as
# "up to SPECIALIST_TIMEOUT_SECONDS". `future.result(timeout=…)` stops the
# caller waiting; it cannot interrupt the callable, because nothing in Python
# can interrupt a thread mid-call. Both paths therefore narrow it cooperatively:
# they drive the graph with `stream()` and check a cancellation flag between
# ReAct super-steps, so an abandoned run stops after the model or tool call
# already in flight rather than running to completion for nobody.
#
# The synchronous path used to be the exception — one blocking `agent.invoke`
# with no seam to check — so a timed-out question kept its worker for as long
# as the model and tools took. Six of those starved the pool. It now streams in
# `values` mode, whose last emission is what `invoke` returned anyway.
#
# The overrun is bounded but not zero, so the recursion limit still matters as
# much as the timeout. Sizing follows from that: at two workers a third
# simultaneous question waited behind two long runs and the gateway returned 502
# before the app ever answered — which is what a room full of people asking at
# once looks like. These threads are almost entirely blocked on the serving
# endpoint and the warehouse, so the cost of more of them is concurrent load on
# those, not CPU here.
#
# The streaming path additionally uses one unbounded daemon thread per request
# to drain the event queue; that thread spends its life blocked on this pool,
# so concurrency is still bounded here, but the thread count is not.
_SPECIALIST_WORKERS = int(os.environ.get("SPECIALIST_WORKERS", "6"))
_executor = ThreadPoolExecutor(
    max_workers=_SPECIALIST_WORKERS, thread_name_prefix="specialist"
)
_conversation_store = ConversationStore()


# Lazy-initialized specialists
_specialists: dict | None = None

def _get_specialists() -> dict:
    global _specialists
    if _specialists is None:
        _specialists = {
            "downtime_analyst": create_downtime_analyst(),
            "quality_specialist": create_quality_specialist(),
            "supply_chain_advisor": create_supply_chain_advisor(),
            "oee_coach": create_oee_coach(),
            "schedule_optimizer": create_schedule_optimizer(),
            "maintenance_predictor": create_maintenance_predictor(),
            "strategic_advisor": create_strategic_advisor(),
            "shift_briefing": create_shift_briefing(),
        }
    return _specialists

# Every name the router may return. A module constant rather than a literal
# inside _classify_intent because the evaluation scenarios have to be checked
# against it: when that set was inlined, the test suite kept its own copy and
# so could not notice a scenario naming a specialist the router would never
# emit — it would just be silently downgraded to "general" at runtime.
VALID_SPECIALISTS: frozenset[str] = frozenset({
    "downtime_analyst", "quality_specialist", "supply_chain_advisor",
    "oee_coach", "schedule_optimizer", "maintenance_predictor",
    "strategic_advisor", "shift_briefing", "general",
})

INCIDENT_TO_SPECIALIST: dict[str, str] = {
    "machine_breakdown": "downtime_analyst",
    "sensor_failure": "downtime_analyst",
    "speed_loss": "oee_coach",
    "minor_stops": "oee_coach",
    "quality_deviation": "quality_specialist",
    "contamination": "quality_specialist",
    "material_shortage": "supply_chain_advisor",
    "supply_delay": "supply_chain_advisor",
    "vibration_anomaly": "maintenance_predictor",
    "maintenance_overdue": "maintenance_predictor",
    "safety_incident": "downtime_analyst",  # no dedicated safety specialist; downtime handles stops
    "zone_lockout": "downtime_analyst",
    "changeover_delay": "schedule_optimizer",
    "rush_order": "schedule_optimizer",
}


def _route_by_active_incident(context: dict) -> str | None:
    """If there's an active incident, deterministically route to its specialist.

    NOTE: This deliberately preempts shift_briefing routing. If a user asks
    "give me a handover" while a P1 machine_breakdown is live, the
    downtime_analyst still wins — the operator must triage the incident first,
    and the briefing agent would just surface the same incident anyway. To get
    a briefing during an active incident, the user must first resolve it (or
    we can revisit this when we add an explicit "briefing despite incident"
    intent).
    """
    active_cmds = context.get("active_incidents", [])
    if not active_cmds:
        return None
    latest = active_cmds[0]
    scenario_id = latest.get("scenario_id", "")
    specialist = INCIDENT_TO_SPECIALIST.get(scenario_id)
    if specialist:
        logger.info("Deterministic routing: incident '%s' → %s", scenario_id, specialist)
    return specialist


@mlflow.trace(name="classify_intent", span_type="CHAIN")
def _classify_intent(query: str, context: dict) -> str:
    """LLM-route a query to one of the registered specialist names.

    Only called when `_route_by_active_incident` returned None — see the
    module docstring for routing precedence. Returns one of the valid
    specialist names or "general"; an unrecognised LLM output is logged
    and downgraded to "general" so the user still gets a response.

    Wrapped in an MLflow span so the trace shows the routing decision as a
    named step distinct from the specialist execution below.
    """
    # The only completion cap left in the system, and the only one where
    # truncation is harmless: this returns a specialist name, so a cut-off
    # completion either still matches or falls through to the default. 50 is
    # enough for any name plus slack, and anything longer is the model echoing
    # the question back — which we would discard anyway.
    #
    # The answer paths carry no cap. Their output contract is 250 words, but a
    # reasoning model spends most of a completion budget on reasoning tokens
    # that count against it and never reach the user, so a cap sized for the
    # answer truncates it instead — from the bottom, taking Priority Actions,
    # Estimated Recovery and Confidence, and leaving something that still looks
    # like a finished answer. Length is already bounded three other ways: the
    # prompt's word cap, SPECIALIST_RECURSION_LIMIT on calls, and
    # SPECIALIST_TIMEOUT_SECONDS on the wait.
    llm = ChatDatabricks(endpoint=resolve(_ROUTER_ROLE), max_tokens=50)

    # Build summary strings for the classification prompt. Unreconciled, this
    # fed the router resolve markers as though they were incidents
    # ("DEPALLETIZER: resolved") alongside faults that were already over.
    events_summary = ", ".join(
        f"{e.get('machine_id', '?')}: {e.get('event_type', '?')}"
        for e in active_events(context.get("active_events", []), context_now_ts(context))[:5]
    ) or "None"

    line_summary_parts = []
    for m in context.get("line_state", [])[:5]:
        line_summary_parts.append(f"{m.get('machine_id', '?')}={m.get('status', '?')}")
    line_summary = ", ".join(line_summary_parts) or "All running"

    prompt_text = loader.prompt_text("intent_classify").format(
        shift_id=context.get("scenario", {}).get("shift_id", "?"),
        scenario_name=context.get("scenario", {}).get("scenario_name", "?"),
        timestamp=context.get("scenario", {}).get("current_timestamp", "?"),
        active_events_summary=events_summary,
        line_state_summary=line_summary,
        query=query,
    )

    messages = [
        SystemMessage(content=loader.prompt_text("orchestrator_system")),
        HumanMessage(content=prompt_text),
    ]

    try:
        response = llm.invoke(messages)
        intent = message_text(response).strip().lower().replace(" ", "_")
        if intent not in VALID_SPECIALISTS:
            logger.warning("Unknown intent '%s', defaulting to general", intent)
            return "general"
        return intent
    except Exception:
        logger.exception("Intent classification failed")
        return "general"

_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def _event_detail(event: dict) -> str:
    """The event's description, unless it only restates the event type.

    The producer writes ``description = f"Incident started: {scenario_id}"`` and
    ``event_type = scenario_id``, so rendering both gave the operator
    "machine_breakdown (high): Incident started: machine_breakdown" — three
    words of signal padded out to a full line. Backfilled events carry real
    prose ("Label web break during reel changeover") and that is worth showing.
    """
    detail = (event.get("description") or "").strip()
    if not detail:
        return ""
    event_type = (event.get("event_type") or "").strip()
    if event_type and detail.replace("_", " ").lower().endswith(
        event_type.replace("_", " ").lower()
    ):
        # "Incident started: machine_breakdown" against event_type
        # "machine_breakdown" — the description ends where the type begins.
        remainder = detail[: -len(event_type)].strip(" :-—")
        if not remainder or remainder.lower() in ("incident started", "incident resolved"):
            return ""
    return detail[:80]


def _snapshot_alerts(events: list, context: dict, query: str = "") -> list[dict]:
    """The (at most three) open alerts worth putting in front of the operator.

    Three separate problems produced the DEPALLETIZER report on a FILLER
    question. Resolved incidents counted as open, because closure lives in a
    marker row rather than in ``timestamp_end``. The same machine appeared once
    per repeat fault, so one flapping unit could fill all three slots. And the
    list was ordered by recency alone, with no idea which machine had been asked
    about, so the machine in the question could be crowded out entirely.
    """
    open_events = active_events(events, context_now_ts(context))

    # One line per machine: the operator needs to know a machine is down, not
    # how many times it has gone down. Most severe wins, then most recent.
    by_machine: dict[str, dict] = {}
    for e in open_events:
        machine = e.get("machine_id") or "?"
        held = by_machine.get(machine)
        if held is None or (
            _SEVERITY_RANK.get((e.get("severity") or "").lower(), 9),
            -to_epoch(e.get("timestamp_start")),
        ) < (
            _SEVERITY_RANK.get((held.get("severity") or "").lower(), 9),
            -to_epoch(held.get("timestamp_start")),
        ):
            by_machine[machine] = e

    asked_about = {
        m for m in by_machine
        if m and re.search(rf"\b{re.escape(m)}\b", query or "", re.IGNORECASE)
    }
    return sorted(
        by_machine.values(),
        key=lambda e: (
            0 if (e.get("machine_id") or "") in asked_about else 1,
            _SEVERITY_RANK.get((e.get("severity") or "").lower(), 9),
            -to_epoch(e.get("timestamp_start")),
        ),
    )[:3]


def _quick_snapshot(context: dict, query: str = "") -> str:
    """Build a deterministic markdown snapshot of the line, no LLM involved.

    Yielded immediately after routing so the user has something concrete to
    read while the specialist runs (which can take 5-15s). Pure
    string-formatting — never blocks on a network call. If any field is
    missing the corresponding line is skipped rather than rendered as "None".
    """
    scenario = context.get("scenario", {})
    kpi = context.get("current_kpi", {})
    plan = context.get("production_plan", {})
    events = context.get("active_events", [])
    line_state = context.get("line_state", [])

    parts = [f"**Shift {scenario.get('shift_id', '?')}** — {scenario.get('scenario_name', '?')}"]

    oee = kpi.get("oee_to_now")
    if oee is not None:
        # `or 0` rather than a .get default: these arrive from `SELECT *`, so a
        # NULL column is a present key holding None and the default never fires.
        parts.append(f"  OEE: **{float(oee):.1f}%** | "
                     f"A: {float(kpi.get('availability_to_now') or 0):.1f}% · "
                     f"P: {float(kpi.get('performance_to_now') or 0):.1f}% · "
                     f"Q: {float(kpi.get('quality_to_now') or 0):.1f}%")

    actual = kpi.get("cumulative_cases")
    planned = plan.get("planned_cases")
    if actual is not None and planned and int(planned) > 0:
        a, p = int(actual), int(planned)
        pct = a / p * 100
        # ``cumulative_cases`` is a per-shift tally (producer resets it every 8h
        # window). If it dwarfs a single-shift plan the producer is running an
        # older build that never reset — showing "% of plan" would be nonsense
        # (e.g. 6233%), so suppress the ratio and just report the count.
        if pct > 150:
            parts.append(f"  Cases: **{a:,}** (plan {p:,}/shift)")
        else:
            parts.append(f"  Cases: **{a:,}** / {p:,} ({pct:.0f}% of plan)")

    # "blocked" is a status the cascade sets when a machine cannot discharge
    # downstream. Leaving it out counted such a machine as neither running nor
    # an issue, so the line did not add up: 16 running and 2 issues on a
    # 19-machine line, with the third silently missing.
    stopped = [
        m.get("machine_id", "?") for m in line_state
        if m.get("status") in ("stopped", "degraded", "starved", "blocked")
    ]
    running_count = sum(1 for m in line_state if m.get("status") == "running")
    if stopped:
        shown = ", ".join(stopped[:5])
        if len(stopped) > 5:
            shown += f", +{len(stopped) - 5} more"
        parts.append(f"  Line: {running_count} running, **{len(stopped)} issue(s)**: {shown}")
    else:
        parts.append(f"  Line: All {running_count} machines running")

    for e in _snapshot_alerts(events, context, query):
        line = f"  Alert: **{e.get('machine_id', '?')}** — {e.get('event_type', '?')} ({e.get('severity', '?')})"
        detail = _event_detail(e)
        parts.append(f"{line}: {detail}" if detail else line)

    remaining = scenario.get("remaining_minutes")
    if remaining is not None:
        parts.append(f"  Time remaining: **{int(remaining)} min**")

    return "\n".join(parts)


def _trim_context(context: dict, specialist_name: str) -> dict:
    """Return a per-specialist slice of `context` capped to ~4K tokens.

    Each specialist sees a different shape — downtime gets stopped machines,
    quality gets quality_checks, supply_chain gets inventory, briefings get
    the kitchen sink. This is THE token-budget control point: changes here
    are the most common cause of "context too large" errors at runtime.

    Always-included slices: scenario, current_kpi, production_plan,
    active_events (capped 10), conversation_history (last 4), active_incidents
    (capped 5). Everything else is opt-in per specialist.
    """
    slim = {
        "scenario": context.get("scenario", {}),
        "current_kpi": context.get("current_kpi", {}),
        "production_plan": context.get("production_plan", {}),
    }

    # ``active_events`` is the recent-events feed, not a list of open ones, and
    # handing it to a specialist under that name is how a FILLER diagnosis came
    # to reason about three DEPALLETIZER breakdowns that had been resolved
    # ninety minutes earlier. Closure lives in a separate marker row, so it has
    # to be reconciled before the slice.
    events = context.get("active_events", [])
    slim["active_events"] = active_events(events, context_now_ts(context))[:10]

    line_state = context.get("line_state", [])
    if specialist_name in ("downtime_analyst", "maintenance_predictor"):
        stopped = [m for m in line_state if m.get("status") in ("stopped", "degraded", "starved")]
        slim["line_state"] = stopped or line_state[:5]
    elif specialist_name == "shift_briefing":
        # Briefings need the full line picture (status of every machine), capped
        # to keep tokens reasonable. The synthesizer otherwise only sees a 4-count
        # summary which isn't enough to mention specific stopped/degraded units.
        slim["line_state"] = line_state[:12]
    else:
        slim["line_state_summary"] = {
            "total_machines": len(line_state),
            "running": sum(1 for m in line_state if m.get("status") == "running"),
            "stopped": sum(1 for m in line_state if m.get("status") == "stopped"),
            "degraded": sum(1 for m in line_state if m.get("status") == "degraded"),
            "starved": sum(1 for m in line_state if m.get("status") == "starved"),
        }

    if specialist_name == "quality_specialist":
        slim["quality_checks"] = context.get("quality_checks", [])[:10]
    if specialist_name == "supply_chain_advisor":
        slim["inventory_snapshot"] = context.get("inventory_snapshot", [])
    if specialist_name in ("schedule_optimizer", "oee_coach"):
        slim["constraints"] = context.get("constraints", [])[:5]
    if specialist_name == "maintenance_predictor":
        slim["maintenance"] = context.get("maintenance", [])[:5]
    if specialist_name == "shift_briefing":
        # The handover briefing pulls from the kitchen sink: who's on shift,
        # what PMs / CIPs are scheduled, what's low in inventory, what's already
        # flagged. Cap row counts to keep total ~5K tokens.
        slim["inventory_snapshot"] = context.get("inventory_snapshot", [])[:20]
        slim["crew"] = context.get("crew", [])[:10]
        slim["maintenance"] = context.get("maintenance", [])[:10]
        slim["cip_schedule"] = context.get("cip_schedule", [])[:10]
        slim["constraints"] = context.get("constraints", [])[:5]
        slim["recommendations"] = context.get("recommendations", [])[:6]
        # Recent quality signal helps name carryover holds — cap tight.
        slim["quality_checks"] = context.get("quality_checks", [])[:5]

    active_incidents = context.get("active_incidents", [])
    if active_incidents:
        slim["active_incidents"] = [
            {k: v for k, v in inc.items() if k in (
                "scenario_id", "target_machine_id", "severity", "status"
            )}
            for inc in active_incidents[:5]
        ]

    history = context.get("conversation_history")
    if history:
        slim["conversation_history"] = history[-4:]

    return slim


TOOL_DISPLAY_NAMES = {
    "sql_query": "Querying production database",
    "genie_query": "Asking Genie data analyst",
    "detect_anomalies": "Running anomaly detection",
    "calculator": "Running calculations",
    "load_plan_and_constraints": "Loading plan & constraints",
    "retrieve_similar_cases": "Searching historical cases",
    "schedule_recovery": "Running MILP schedule optimizer",
    "production_forecast": "Running Monte Carlo forecast",
    "quality_risk_assessment": "Running Bayesian quality model",
    "oee_loss_analysis": "Running OEE loss Pareto analysis",
    "stochastic_schedule_recovery": "Running stochastic schedule optimizer",
    "multi_shift_plan": "Running multi-shift planner",
    "estimate_rul": "Estimating remaining useful life",
    # One per governed domain. Without these the live trace fell back to
    # "Using governed_quality_query", which is the tool's name, not a step a
    # line manager can read.
    "governed_asset_query": "Asking the governed asset domain",
    "governed_quality_query": "Asking the governed quality domain",
    "governed_inventory_query": "Asking the governed inventory domain",
    "governed_production_query": "Asking the governed production domain",
}

# Past tense for the verb each label opens with. Spelled out because English
# will not be derived: the rule this replaced was
# ``label.replace("ing ", "ed ").replace("Running ", "Ran ")``, which put
# "Runned anomaly detection" and "Queryed production database" in front of the
# user on ten of the seventeen labels — the first substitution had already
# consumed the "Running " the second was looking for. "Estimating remaining
# useful life" came out as "Estimated remained useful life", because a blanket
# replace cannot tell a verb from the middle of a noun phrase.
_PAST_TENSE = {
    "Asking": "Asked",
    "Estimating": "Estimated",
    "Loading": "Loaded",
    "Querying": "Queried",
    "Running": "Ran",
    "Searching": "Searched",
    # The fallback label for a tool with no display name of its own.
    "Using": "Used",
}


def completed_label(label: str) -> str:
    """The past-tense form of a running-tool label, for the ✓ line.

    Only the leading verb is touched, and only if we know it. An unrecognised
    verb keeps the present tense, which reads as slightly off rather than
    wrong — ``test_tool_labels.py`` fails on any label this cannot conjugate,
    so a new one gets a real past tense rather than this fallback.
    """
    verb, _, rest = label.partition(" ")
    past = _PAST_TENSE.get(verb)
    if past is None:
        return label
    return f"{past} {rest}".rstrip()


def _attach_evidence(tool_trace: list[dict], label: str, evidence: dict) -> None:
    """Record a tool's provenance against the step that produced it.

    Attaches to the oldest step with that label that has no evidence yet, which
    is the only ordering that survives a specialist calling one tool twice in a
    single turn. Matching the newest instead put both results on the second
    step — the first kept its label and lost its query, and the second showed
    one call's SQL beside another call's row count. Since the trail exists to
    be checkable, evidence on the wrong step is worse than none.

    Once every step with that label is accounted for, later evidence merges
    into the newest, and a label with no step at all — possible if a result
    outran its own call event — is dropped rather than inventing a step the
    user never saw run.
    """
    matching = [step for step in tool_trace if step["action"] == label]
    if not matching:
        return
    target = next((step for step in matching if not step.get("evidence")), matching[-1])
    target.setdefault("evidence", {}).update(evidence)


def _tool_steps(tool_trace: list[dict]) -> list[dict]:
    """Turn the tool trace into reasoning steps, carrying evidence through.

    The evidence is what makes the chat's trail checkable rather than merely
    descriptive: the query that ran, the tables it touched, the row count.
    """
    steps = []
    for entry in tool_trace:
        step = {"label": entry["action"], "detail": "Tool call by specialist agent"}
        if entry.get("evidence"):
            step["evidence"] = entry["evidence"]
        steps.append(step)
    return steps


# Every answer template ends with `## Confidence` followed by one line reading
# `**HIGH** — <justification>` (see prompts/response_synthesis.py). Lenient about
# the bold markers and the case because this parses model output, not our own:
# a heading the model formatted slightly differently should cost us the tag, not
# raise. `re.MULTILINE` so `^##` only matches a real heading.
_CONFIDENCE_RE = re.compile(
    r"^##\s*Confidence\s*$\s*\**\s*(HIGH|MEDIUM|LOW)\b",
    re.MULTILINE | re.IGNORECASE,
)

# What a specialist returns when its ReAct loop finished without producing an
# answer message. Defined once because it is both returned and recognised.
NO_ANSWER = "Analysis complete but no response generated."


def _confidence_level(answer: str) -> str | None:
    """The self-reported HIGH/MEDIUM/LOW from an answer, or None if absent.

    Recorded on the span so low-confidence answers are findable without
    re-reading every trace body — `mlflow traces search` can filter on it.
    Absent is a distinct outcome from LOW and must stay distinguishable: it
    means the model skipped a required section, which is a formatting failure
    worth seeing rather than a hedge worth acting on.
    """
    if not answer:
        return None
    m = _CONFIDENCE_RE.search(answer)
    return m.group(1).upper() if m else None


def _final_answer_text(result: Mapping) -> str:
    """The answer out of a finished ReAct run's message list.

    Walks back to the last AIMessage carrying real content. A message holding
    nothing but reasoning blocks is not an answer, so it keeps walking rather
    than returning an empty string.
    """
    for msg in reversed(result.get("messages", [])):
        if isinstance(msg, AIMessage) and msg.content:
            text = message_text(msg)
            if text:
                return text
    return NO_ANSWER


def _tag_confidence(answer: str) -> None:
    """Promote the answer's confidence to a trace tag.

    A span output is only readable by opening the trace; a tag is queryable, so
    `mlflow traces search --filter-string "tag.confidence = 'LOW'"` can pull
    every hedged answer at once. Tagging is an observability nicety and must
    never take a reply down with it, so a tracing failure is logged and
    swallowed.
    """
    level = _confidence_level(answer)
    if not level:
        return
    try:
        mlflow.update_current_trace(tags={"confidence": level})
    except Exception as exc:  # noqa: BLE001 - tracing must not break answering
        logger.debug("Could not tag trace confidence: %s", exc)


def _record_answer(span, answer: str) -> None:
    """Attach an answer to its span.

    Without this the span carries inputs and no outputs, which is how every
    specialist trace in both experiments came to render an empty "Model output"
    in the MLflow UI — the question was recorded and the answer was not, making
    the traces useless for judging answer quality.
    """
    span.set_outputs({"response": answer, "confidence": _confidence_level(answer)})
    _tag_confidence(answer)


def _run_specialist_streaming(specialist_name: str, query: str, context: dict, event_queue: queue.Queue) -> str:
    """Run a specialist, streaming tool-call events to `event_queue`.

    Companion to `_run_specialist` for the SSE path. The agent's `.stream()`
    yields LangGraph node events; we filter for AIMessage(tool_calls=…) to
    emit "tool" events and ToolMessage to emit "tool_done" events. The
    final AIMessage (one without tool_calls) is captured as the answer.

    Always emits exactly one ("done", result) sentinel so the caller can
    stop draining the queue without busy-waiting.
    """
    specialists = _get_specialists()
    trimmed = _trim_context(context, specialist_name)

    if specialist_name == "general" or specialist_name not in specialists:
        result = _run_specialist(specialist_name, query, context)
        event_queue.put(("done", result))
        # The general path runs its one LLM call on this thread, so the trace it
        # opened is the one this lookup finds.
        url = current_trace_url()
        if url:
            event_queue.put(("trace_url", url))
        return result

    agent = specialists[specialist_name]
    context_str = json.dumps(trimmed, indent=2, default=str)
    input_messages = [
        HumanMessage(content=f"Context:\n{context_str}\n\nUser query: {query}"),
    ]

    # A timed-out future is abandoned, not stopped: `future.result(timeout=…)`
    # gives up waiting but the worker keeps running the ReAct loop, holding one
    # of SPECIALIST_WORKERS slots for as long as the model and its tools take.
    # A handful of slow questions could therefore starve the pool for everyone
    # else — the failure the pool was widened to six to avoid in the first
    # place. Python cannot kill a thread, so the loop below cooperates: it
    # checks this between super-steps and stops, which bounds the overrun to
    # the one model or tool call already in flight.
    cancelled = threading.Event()

    # What the model has said so far, as the tokens go out. `final_content` only
    # becomes the answer once a whole message lands, so on a timeout it is still
    # NO_ANSWER even when prose has been streaming for ten seconds — and the
    # bare timeout string would replace, in the UI, an answer the reader had
    # already started reading. This is the submitting thread's window into that.
    streamed: list[str] = []

    def _stream_invoke() -> tuple[str, str | None]:
        final_content = NO_ANSWER
        try:
            # Named span in the WORKER thread so autolog's ReAct tool/LLM spans
            # nest under one "specialist.<name>" parent (see _invoke above).
            with mlflow.start_span(name=f"specialist.{specialist_name}", span_type="AGENT") as span:
                span.set_inputs({"query": query, "specialist": specialist_name})
                # Two stream modes at once. "updates" carries whole messages
                # per super-step and is what the tool trace is built from;
                # "messages" carries the model's output token by token. Asking
                # for both changes the item shape to (mode, payload) — with a
                # single mode LangGraph yields the payload bare.
                #
                # Token streaming costs nothing in latency: the tokens were
                # already crossing the wire, they were simply accumulated in
                # this worker and released in one piece at the end.
                for mode, payload in agent.stream(
                    {"messages": input_messages},
                    {"recursion_limit": SPECIALIST_RECURSION_LIMIT},
                    stream_mode=["updates", "messages"],
                ):
                    if cancelled.is_set():
                        logger.warning(
                            "Specialist %s abandoned after timeout; releasing the worker",
                            specialist_name,
                        )
                        break

                    if mode == "messages":
                        chunk = payload[0] if isinstance(payload, tuple) else payload
                        # The tools node emits on this stream too, so an
                        # unfiltered handler would type raw tool JSON into the
                        # answer. Only the model's own prose qualifies, and
                        # only when it is not the arguments of a tool call
                        # being assembled a fragment at a time.
                        if (
                            isinstance(chunk, AIMessageChunk)
                            and not getattr(chunk, "tool_calls", None)
                            and not getattr(chunk, "tool_call_chunks", None)
                        ):
                            token = message_text(chunk)
                            if token:
                                streamed.append(token)
                                event_queue.put(("token", token))
                        continue

                    event = payload
                    for node_name, node_output in event.items():
                        msgs = node_output.get("messages", [])
                        for msg in msgs:
                            if isinstance(msg, AIMessage) and hasattr(msg, 'tool_calls') and msg.tool_calls:
                                for tc in msg.tool_calls:
                                    tool_name = tc.get("name", "unknown")
                                    display = TOOL_DISPLAY_NAMES.get(tool_name, f"Using {tool_name}")
                                    event_queue.put(("tool", display))
                                    call_evidence = evidence_from_call(tool_name, tc.get("args"))
                                    if call_evidence:
                                        event_queue.put(("tool_evidence", (display, call_evidence)))
                            if isinstance(msg, ToolMessage):
                                # Anything the model said before reaching for a
                                # tool was thinking aloud, not the answer — it
                                # is discarded from `final_content` when the
                                # next message supersedes it. Without this the
                                # streamed draft would keep the preamble and
                                # glue the real answer onto the end of it.
                                event_queue.put(("token_reset", None))
                                # Same reason the UI resets: what came before a
                                # tool call was thinking aloud, not the answer.
                                streamed.clear()
                                tool_name = getattr(msg, 'name', '') or ''
                                result_evidence = evidence_from_result(tool_name, msg.content)
                                if result_evidence:
                                    # Label it the same way the "tool" event did,
                                    # so the trace entry it belongs to is findable.
                                    event_queue.put((
                                        "tool_evidence",
                                        (TOOL_DISPLAY_NAMES.get(tool_name, f"Using {tool_name}"), result_evidence),
                                    ))
                                display = TOOL_DISPLAY_NAMES.get(tool_name, '')
                                if display:
                                    event_queue.put(("tool_done", display))
                            if isinstance(msg, AIMessage) and msg.content and not getattr(msg, 'tool_calls', None):
                                text = message_text(msg)
                                # Empty means the model emitted only private
                                # reasoning; keep the previous answer rather
                                # than blanking the reply.
                                if text:
                                    final_content = text
                # Inside the `with`, so it lands on the span rather than after
                # it has closed. On an exception the span is already marked
                # failed and carries the traceback, which is the better record.
                _record_answer(span, final_content)
        except Exception as exc:
            logger.exception("Streaming specialist %s failed: %s", specialist_name, exc)
            final_content = (
                f"The {specialist_name.replace('_', ' ')} encountered an error: {type(exc).__name__}. "
                "Please try rephrasing your question."
            )

        # Resolved here, inside the worker, because the trace belongs to this
        # thread: the submitting thread cannot see it.
        return final_content, current_trace_url()

    trace_url: str | None = None
    try:
        future = _executor.submit(_stream_invoke)
        result, trace_url = future.result(timeout=SPECIALIST_TIMEOUT_SECONDS)
    except FuturesTimeout:
        cancelled.set()
        logger.error("Specialist %s timed out after %ds (stream)", specialist_name, SPECIALIST_TIMEOUT_SECONDS)
        # The answer starts streaming late — the tools finish early and the
        # model then spends a long stretch composing — so a cut usually lands
        # partway through prose the reader can already see. Cut answers are
        # worth keeping: the sections are ordered root cause first, so what
        # arrived is the part that matters most.
        partial = "".join(streamed).strip()
        if partial:
            result = (
                f"{partial}\n\n---\n*This analysis was cut short at "
                f"{SPECIALIST_TIMEOUT_SECONDS}s. The findings above are complete as far as "
                "they go; ask about one machine or one time window for the rest.*"
            )
        else:
            result = (
                f"The {specialist_name.replace('_', ' ')} analysis timed out before it "
                "reached an answer. Ask about one machine or one time window to narrow it."
            )

    event_queue.put(("done", result))
    if trace_url:
        event_queue.put(("trace_url", trace_url))
    return result


def _run_specialist(specialist_name: str, query: str, context: dict) -> str:
    """Run the appropriate specialist agent with timeout and structured error handling."""
    specialists = _get_specialists()

    trimmed = _trim_context(context, specialist_name)

    if specialist_name == "general" or specialist_name not in specialists:
        # "General" is the no-tool path: a single chat call with the trimmed
        # context as a system message. Used when the LLM router couldn't find
        # a specialist match — usually "give me a status" or "what's going on".
        llm = ChatDatabricks(endpoint=resolve(_GENERAL_ROLE))
        context_str = json.dumps(trimmed, indent=2, default=str)
        messages = [
            SystemMessage(
                content=loader.prompt_text("general_system").format(context=context_str)
            ),
            HumanMessage(content=query),
        ]
        try:
            response = llm.invoke(messages)
            return message_text(response)
        except Exception:
            logger.exception("General query failed")
            return "I'm sorry, I couldn't process your query. Please try again."

    agent = specialists[specialist_name]
    context_str = json.dumps(trimmed, indent=2, default=str)
    input_messages = [
        HumanMessage(content=f"Context:\n{context_str}\n\nUser query: {query}"),
    ]

    # Same cooperative cancellation as the streaming path. `future.result(timeout)`
    # only stops the CALLER waiting; the worker keeps running the ReAct loop and
    # holds one of SPECIALIST_WORKERS slots until the model and its tools finish
    # on their own. Six of those and the pool is starved for everyone. Python
    # cannot kill a thread, so the loop below checks this between super-steps,
    # which bounds the overrun to the one model or tool call already in flight.
    cancelled = threading.Event()

    def _invoke() -> str:
        # Open a named span in the WORKER thread (where mlflow.langchain.autolog
        # creates the ReAct spans) so every tool/LLM span nests under a single
        # "specialist.<name>" parent instead of appearing as loose siblings.
        with mlflow.start_span(name=f"specialist.{specialist_name}", span_type="AGENT") as span:
            span.set_inputs({"query": query, "specialist": specialist_name})
            # `stream(stream_mode="values")` yields the whole graph state after
            # each super-step, so the last one is exactly what `invoke` would
            # have returned — but with a gap between steps to notice a timeout.
            result: Mapping | None = None
            for state in agent.stream(
                {"messages": input_messages},
                {"recursion_limit": SPECIALIST_RECURSION_LIMIT},
                stream_mode="values",
            ):
                if cancelled.is_set():
                    logger.warning(
                        "Specialist %s abandoned after timeout; releasing the worker",
                        specialist_name,
                    )
                    break
                result = state
            # Extracted here rather than in the caller so the answer is known
            # while the span is still open. Returning the raw message list and
            # picking the answer out afterwards is what left these spans with
            # no outputs at all.
            answer = _final_answer_text(result) if result is not None else NO_ANSWER
            _record_answer(span, answer)
            return answer

    try:
        future = _executor.submit(_invoke)
        return future.result(timeout=SPECIALIST_TIMEOUT_SECONDS)
    except FuturesTimeout:
        # Tell the worker to stop at its next super-step. Without this it runs
        # to completion against a result nobody is waiting for.
        cancelled.set()
        logger.error("Specialist %s timed out after %ds", specialist_name, SPECIALIST_TIMEOUT_SECONDS)
        return (
            f"The {specialist_name.replace('_', ' ')} analysis timed out. "
            "This usually means the query required too many tool calls. "
            "Please try a more specific question."
        )
    except Exception as exc:
        logger.exception("Specialist %s failed: %s", specialist_name, exc)
        # No fallback runs. This used to promise "Falling back to a general
        # analysis", which was read by an operator as "something still looked
        # at my question" — nothing did, and `_should_skip_synthesis` matches
        # "encountered an error" and skips the synthesis pass too. Telling
        # someone an analysis happened when none did is worse than the error.
        return (
            f"The {specialist_name.replace('_', ' ')} encountered an error: {type(exc).__name__}. "
            "No analysis was produced. Please try rephrasing your question."
        )

@mlflow.trace(name="synthesize_response", span_type="LLM")
def _synthesize_response(specialist_name: str, specialist_response: str, context: dict) -> str:
    """Reformat the specialist output to the SYNTHESIS_TEMPLATE structure.

    Traced because this pass, when it runs, produces the text the manager
    actually reads — including the `## Confidence` line. Untraced, the only
    answer stored anywhere was the specialist's draft, so a trace could not
    show what was shown on screen. The decorator records the return value, so
    unlike the spans above this needs no explicit output call.

    The UI parses `## Root Cause`, `## Impact`, etc. headings to fold answers
    into expandable cards. Specialists don't always produce the right
    structure on their own (especially the ReAct path mid-tool-loop), so we
    do a second LLM pass here. Callers MUST gate this by `_should_skip_synthesis`
    so we don't burn a second call on a known-bad payload.

    On any failure, returns the raw specialist response — fluent text is
    always better than an apology card.
    """
    scenario = context.get("scenario", {})
    plan = context.get("production_plan", {})

    llm = ChatDatabricks(endpoint=resolve(_GENERAL_ROLE))

    kpi = context.get("current_kpi", {})
    synthesis_prompt = loader.prompt_text("response_synthesis").format(
        specialist=specialist_name,
        specialist_response=specialist_response,
        shift_id=scenario.get("shift_id", "?"),
        scenario_name=scenario.get("scenario_name", "?"),
        active_events_count=len(context.get("active_events", [])),
        current_oee=kpi.get("oee_to_now", "N/A"),
        actual_cases=kpi.get("cumulative_cases", "?"),
        planned_cases=plan.get("planned_cases", "?"),
    )

    try:
        response = llm.invoke([HumanMessage(content=synthesis_prompt)])
        answer = message_text(response) or specialist_response
    except Exception:
        logger.exception("Synthesis failed, returning raw specialist response")
        answer = specialist_response
    _tag_confidence(answer)
    return answer

# The headings ChatPanel folds into expandable cards (ALL_KNOWN_SECTIONS in
# client/src/components/ChatPanel.tsx). Synthesis exists to produce these, so
# they are also how we tell whether it has anything left to do.
_UI_SECTIONS = (
    "Root Cause",
    "Impact",
    "Priority Actions",
    "Estimated Recovery",
    "Confidence",
    "Situation",
    "Key Risks",
    "Outlook",
    "Current State",
    "Recommendation",
    "Expected Outcome",
)

# Above this, a synthesis pass costs real tokens and tends to truncate rather
# than restructure, so an unstructured answer this long is better shown whole.
_SYNTHESIS_CEILING = 6000


def _already_structured(text: str) -> bool:
    """Whether the UI can already fold this into cards.

    Two headings rather than one: a specialist that happens to write a single
    "## Impact" has not produced the template, and treating it as though it had
    is what leaves an answer rendering as a wall of text.
    """
    return sum(f"## {section}" in text for section in _UI_SECTIONS) >= 2


def _should_skip_synthesis(specialist_name: str, specialist_response: str) -> bool:
    """Return True when the synthesis LLM call would waste tokens.

    We skip when:
      * the routing fell through to "general" — that path is already a
        short, structured chat call, no second pass needed
      * the answer already carries the headings the UI parses — a second pass
        would rewrite text that is already in the right shape
      * the answer is unstructured but very long — see `_SYNTHESIS_CEILING`
      * the response is this module's own timeout or error sentinel —
        synthesis would just re-emit the same apology

    Length used to stand in for the structure test, at 800 characters. It is a
    poor proxy in the direction that matters: a long ReAct answer with no
    headings is exactly the one the reader wants folded into cards, and it was
    the one guaranteed to skip. The failure test used to be a substring search
    for "timed out" anywhere in the text, which also caught real answers that
    merely mentioned a timeout — a downtime analysis of a PLC that timed out
    would skip its own formatting.
    """
    if specialist_name == "general":
        return True
    if _already_structured(specialist_response):
        return True
    if len(specialist_response) > _SYNTHESIS_CEILING:
        return True
    # Matched against the sentences the timeout and error handlers above build,
    # anchored at the start rather than searched for anywhere. Both the sync
    # and streaming timeouts are covered: they continue differently ("timed
    # out." and "timed out before it reached an answer"), so the marker stops
    # at the words they share.
    prefix = f"The {specialist_name.replace('_', ' ')} "
    return specialist_response.startswith(
        (f"{prefix}analysis timed out", f"{prefix}encountered an error:")
    )


def _extract_actions(specialist_name: str, context: dict) -> list[dict]:
    """Return the deterministic HITL action chips for the chat answer.

    These are NOT generated by the LLM — they're a static lookup table keyed
    on specialist + the most recent active incident's target_machine_id.
    Keeping them deterministic means the same incident always produces the
    same action set, which is what operators expect (and lets the UI cache).

    Add a new specialist? Add an entry to ACTIONS_BY_SPECIALIST below — if
    you forget, the generic fallback at the bottom keeps the UI from
    rendering an empty actions panel.
    """
    incidents = context.get("active_incidents", [])
    machine = incidents[0].get("target_machine_id", "unknown") if incidents else "line"

    ACTIONS_BY_SPECIALIST: dict[str, list[dict]] = {
        "downtime_analyst": [
            {"id": "create_ticket", "title": "Create Maintenance Ticket",
             "description": f"Open a work order in CMMS for {machine}",
             "icon": "ticket", "system": "CMMS / SAP PM"},
            {"id": "dispatch_tech", "title": "Dispatch Technician",
             "description": f"Assign on-call technician to {machine}",
             "icon": "person", "system": "Workforce Mgmt"},
            {"id": "check_spare_parts", "title": "Check Spare Parts",
             "description": f"Verify part availability for {machine} in warehouse",
             "icon": "inventory", "system": "SAP MM"},
            {"id": "update_schedule", "title": "Adjust Production Schedule",
             "description": "Notify planning of downtime impact on shift output",
             "icon": "calendar", "system": "SAP PP"},
        ],
        "quality_specialist": [
            {"id": "hold_product", "title": "Place Product on QC Hold",
             "description": f"Quarantine affected batches from {machine}",
             "icon": "shield", "system": "LIMS"},
            {"id": "lab_sample", "title": "Request Lab Analysis",
             "description": "Send samples for off-line quality verification",
             "icon": "science", "system": "LIMS"},
            {"id": "notify_qa", "title": "Notify QA Manager",
             "description": "Escalate quality deviation for review",
             "icon": "person", "system": "Email / Teams"},
            {"id": "deviation_report", "title": "Open Deviation Report",
             "description": "Document non-conformance per SOP",
             "icon": "document", "system": "QMS"},
        ],
        "supply_chain_advisor": [
            {"id": "emergency_order", "title": "Place Emergency Order",
             "description": "Expedite material replenishment from supplier",
             "icon": "truck", "system": "SAP MM"},
            {"id": "check_alt_supplier", "title": "Check Alternate Suppliers",
             "description": "Query backup suppliers for immediate availability",
             "icon": "search", "system": "SAP SRM"},
            {"id": "adjust_bom", "title": "Review BOM Alternatives",
             "description": "Check if substitute materials can be used",
             "icon": "swap", "system": "SAP PP"},
            {"id": "update_forecast", "title": "Update Demand Forecast",
             "description": "Adjust downstream planning for material gap",
             "icon": "chart", "system": "SAP IBP"},
        ],
        "oee_coach": [
            {"id": "speed_adjust", "title": "Adjust Line Speed",
             "description": "Modify machine parameters to optimize throughput",
             "icon": "speed", "system": "SCADA / HMI"},
            {"id": "micro_stop_log", "title": "Review Micro-Stop Log",
             "description": "Analyze patterns of minor stoppages",
             "icon": "list", "system": "MES"},
            {"id": "operator_training", "title": "Schedule Operator Refresher",
             "description": "Book targeted training for affected area",
             "icon": "school", "system": "LMS"},
        ],
        "maintenance_predictor": [
            {"id": "schedule_pm", "title": "Schedule Preventive Maintenance",
             "description": f"Plan PM window for {machine} before predicted failure",
             "icon": "wrench", "system": "CMMS / SAP PM"},
            {"id": "order_parts", "title": "Pre-Order Spare Parts",
             "description": "Reserve components for upcoming maintenance",
             "icon": "inventory", "system": "SAP MM"},
            {"id": "sensor_check", "title": "Calibrate Sensors",
             "description": f"Verify sensor accuracy on {machine}",
             "icon": "sensor", "system": "SCADA"},
        ],
        "schedule_optimizer": [
            {"id": "resequence", "title": "Resequence Production Orders",
             "description": "Optimize order sequence for remaining shift time",
             "icon": "sort", "system": "SAP PP"},
            {"id": "notify_logistics", "title": "Notify Logistics",
             "description": "Update dispatch schedule for delayed output",
             "icon": "truck", "system": "SAP TM"},
            {"id": "overtime_request", "title": "Request Shift Extension",
             "description": "Submit overtime authorization to recover plan",
             "icon": "clock", "system": "HR / Workforce"},
        ],
        "shift_briefing": [
            {"id": "open_handover_note", "title": "Open Handover Note",
             "description": "Draft a handover note for the next shift in the OPS log",
             "icon": "document", "system": "MES / Ops Log"},
            {"id": "confirm_crew", "title": "Confirm Crew Coverage",
             "description": "Verify scheduled roles are covered and flag callouts",
             "icon": "person", "system": "Workforce Mgmt"},
            {"id": "send_to_next_shift", "title": "Send Briefing to Next Shift",
             "description": "Forward the briefing to the incoming shift supervisor",
             "icon": "send", "system": "Email / Teams"},
            {"id": "file_carryover", "title": "File Carryover Items",
             "description": "Persist open incidents and parking-lot items for next shift",
             "icon": "list", "system": "MES / Ops Log"},
        ],
        "strategic_advisor": [
            {"id": "share_with_leadership", "title": "Share with Plant Leadership",
             "description": "Forward the recommendation deck to the plant manager and ops director",
             "icon": "send", "system": "Email / Teams"},
            {"id": "draft_business_case", "title": "Draft Business Case",
             "description": "Open a one-pager template pre-filled with the cited KPIs",
             "icon": "document", "system": "Confluence / Docs"},
            {"id": "schedule_review", "title": "Schedule Improvement Review",
             "description": "Book a 30-min slot with the continuous-improvement team",
             "icon": "calendar", "system": "Outlook / Calendar"},
        ],
    }

    actions = ACTIONS_BY_SPECIALIST.get(specialist_name, [
        {"id": "generic_review", "title": "Review Recommendation",
         "description": "Review the AI suggestion and take appropriate action",
         "icon": "check", "system": "Manual"},
    ])

    return [{"status": "pending", **a} for a in actions]


MAX_QUERY_LENGTH = int(os.environ.get("MAX_QUERY_LENGTH", "2000"))

# Specialist → the endpoint that actually answered, for the reasoning trace and
# the "via <model>" line in the chat UI. The live ChatDatabricks clients are
# built inside each `create_*` factory; both they and this mapping call
# `model_roster.resolve`, so the badge cannot drift from the model that ran —
# the previous hand-maintained copy of the tier rules could, and would have,
# now that the two differ per specialist.
#
# Resolution is deferred to first access rather than computed at import: it may
# ask the workspace which models are served, and neither the test suite nor app
# start-up should pay a credentialed round-trip just to import this module.
_MODEL_MAP_ROLES: tuple[str, ...] = tuple(a.role for a in ROSTER if a.role != _ROUTER_ROLE)


class _ResolvedModelMap(Mapping):
    """Read-through view of specialist → serving endpoint."""

    def __getitem__(self, key: str) -> str:
        if key not in _MODEL_MAP_ROLES:
            raise KeyError(key)
        return resolve(key)

    def __iter__(self):
        return iter(_MODEL_MAP_ROLES)

    def __len__(self) -> int:
        return len(_MODEL_MAP_ROLES)


MODEL_MAP: Mapping[str, str] = _ResolvedModelMap()


def invoke(
    query: str,
    shift_id: str = "shift_1",
    timestamp: str = "2025-01-01T14:00:00",
    machine_id: str | None = None,
    conversation_id: str | None = None,
) -> dict:
    """Synchronous entry point — returns a single dict, no streaming.

    Used by `/api/agent`, the eval harness, and CLI smoke tests. For the chat
    UI use `invoke_stream` so the user sees the snapshot/tool-trace as it
    happens. See the module docstring for the full pipeline.

    Args:
        query: user prompt; rejected with a friendly message if longer than
               MAX_QUERY_LENGTH (default 2000 chars).
        shift_id: shift namespace. The historical defaults below ("shift_1" /
                  the fixed 2025-01-01 timestamp) work when the shift is
                  producing because `_overlay_live_state` rewrites the
                  operational slices from live Lakebase state.
        timestamp: ISO timestamp used to bound queries in build_context.
        machine_id: optional focus; passed through to context.machine_id.
        conversation_id: previous turn's id; a new one is created if absent.

    Returns:
        dict with keys: content, quick_snapshot, specialist_used, model_used,
        response_time_ms, conversation_id.
    """
    start = time.time()

    if len(query) > MAX_QUERY_LENGTH:
        return {
            "content": f"Query too long ({len(query)} chars). Please limit to {MAX_QUERY_LENGTH} characters.",
            "specialist_used": "none",
            "model_used": "none",
            "response_time_ms": 0,
        }

    if not conversation_id:
        conversation_id = _conversation_store.create_conversation(shift_id)

    _conversation_store.save_message(conversation_id, "user", query, {"shift_id": shift_id, "timestamp": timestamp})

    recent_history = _conversation_store.load_history(conversation_id, limit=6)

    context = build_context(shift_id, timestamp, machine_id)
    context = _overlay_live_state(context, shift_id)

    snapshot = _quick_snapshot(context, query)

    if recent_history and len(recent_history) > 1:
        context["conversation_history"] = [
            {"role": m.get("role", "user"), "content": m.get("content", "")[:500]}
            for m in recent_history[:-1]
        ]

    specialist_name = _route_by_active_incident(context) or _classify_intent(query, context)
    logger.info("Intent classified as: %s", specialist_name)

    specialist_response = _run_specialist(specialist_name, query, context)

    if _should_skip_synthesis(specialist_name, specialist_response):
        final_response = specialist_response
    else:
        final_response = _synthesize_response(specialist_name, specialist_response, context)

    _conversation_store.save_message(
        conversation_id, "assistant", final_response,
        {"specialist": specialist_name, "model": MODEL_MAP.get(specialist_name)},
    )

    elapsed_ms = int((time.time() - start) * 1000)

    endpoint_used = resolve(specialist_name)
    model_display, model_vendor = describe(endpoint_used)
    return {
        "content": final_response,
        "quick_snapshot": snapshot,
        "specialist_used": specialist_name,
        "model_used": endpoint_used,
        "model_display": model_display,
        "model_vendor": model_vendor,
        "response_time_ms": elapsed_ms,
        "conversation_id": conversation_id,
    }


def invoke_stream(
    query: str,
    shift_id: str = "shift_1",
    timestamp: str = "2025-01-01T14:00:00",
    machine_id: str | None = None,
    conversation_id: str | None = None,
):
    """Streaming entry point — yields SSE events through the full pipeline.

    Event types emitted (in order):
        snapshot     — the _quick_snapshot markdown, yielded BEFORE routing
                       so the user sees something within ~1s
        status       — "Routed to <Specialist>" once routing decides
        tool_update  — every tool the ReAct loop calls + a "Formatting..."
                       message before synthesis
        answer       — the final dict with content, tool_trace, reasoning_steps,
                       trace_url, and actions
        [DONE]       — stream terminator

    The specialist runs in a background thread; tool events are pulled off
    `tool_queue` and yielded as soon as they arrive so the UI animates the
    chain-of-thought trace live.
    """
    import json as _json
    start = time.time()

    if len(query) > MAX_QUERY_LENGTH:
        yield f"data: {_json.dumps({'type': 'error', 'content': f'Query too long ({len(query)} chars).'})}\n\n"
        return

    if not conversation_id:
        conversation_id = _conversation_store.create_conversation(shift_id)

    _conversation_store.save_message(conversation_id, "user", query, {"shift_id": shift_id, "timestamp": timestamp})
    recent_history = _conversation_store.load_history(conversation_id, limit=6)

    context = build_context(shift_id, timestamp, machine_id)
    context = _overlay_live_state(context, shift_id)

    snapshot = _quick_snapshot(context, query)

    yield f"data: {_json.dumps({'type': 'snapshot', 'content': snapshot})}\n\n"

    if recent_history and len(recent_history) > 1:
        context["conversation_history"] = [
            {"role": m.get("role", "user"), "content": m.get("content", "")[:500]}
            for m in recent_history[:-1]
        ]

    specialist_name = _route_by_active_incident(context) or _classify_intent(query, context)
    logger.info("Intent classified as: %s (stream)", specialist_name)

    specialist_label = specialist_name.replace("_", " ").title()
    yield f"data: {_json.dumps({'type': 'status', 'content': f'Routed to {specialist_label}...'})}\n\n"

    tool_queue: queue.Queue = queue.Queue()
    tool_trace: list[dict] = []

    def _run_in_thread():
        _run_specialist_streaming(specialist_name, query, context, tool_queue)

    thread = threading.Thread(target=_run_in_thread, daemon=True)
    thread.start()

    specialist_response = "Analysis could not be completed."
    trace_url: str | None = None
    while True:
        try:
            evt_type, evt_data = tool_queue.get(timeout=1.0)
            if evt_type == "token":
                # A draft, not the answer. The authoritative text still arrives
                # on the `answer` event below — synthesis may rewrite it, and a
                # later ReAct round may replace it outright — so the client
                # shows these as they land and drops them when the answer does.
                yield f"data: {_json.dumps({'type': 'token', 'content': evt_data})}\n\n"
            elif evt_type == "token_reset":
                yield f"data: {_json.dumps({'type': 'token_reset'})}\n\n"
            elif evt_type == "tool":
                tool_trace.append({"step": len(tool_trace) + 1, "action": evt_data, "status": "running"})
                yield f"data: {_json.dumps({'type': 'tool_update', 'content': evt_data})}\n\n"
            elif evt_type == "tool_evidence":
                _attach_evidence(tool_trace, *evt_data)
            elif evt_type == "tool_done":
                for t in reversed(tool_trace):
                    if t["action"] == evt_data and t["status"] == "running":
                        t["status"] = "done"
                        break
                yield f"data: {_json.dumps({'type': 'tool_update', 'content': f'{completed_label(evt_data)} ✓'})}\n\n"
            elif evt_type == "trace_url":
                trace_url = evt_data
            elif evt_type == "done":
                specialist_response = evt_data if evt_data else specialist_response
                break
        except queue.Empty:
            if not thread.is_alive():
                break
            # A question that needs no tools produces no events at all between
            # the status frame and the answer — one LLM call, up to
            # SPECIALIST_TIMEOUT_SECONDS of complete silence on the wire. The
            # app sits behind a proxy that is entitled to drop an idle
            # connection, and the user sees a spinner that never resolves.
            #
            # An SSE comment costs two bytes of traffic a second and is not a
            # message: the client's reader skips any line that does not begin
            # with "data: ", so this cannot reach the event parser.
            yield ": ping\n\n"

    thread.join(timeout=SPECIALIST_TIMEOUT_SECONDS + 10)

    # The loop above exits on `queue.Empty` if the worker has since finished, and
    # the worker puts `done` then `trace_url` immediately before it exits — so a
    # thread that completes inside that one-second window leaves both behind.
    # Dropping `done` here meant the user got "Analysis could not be completed"
    # next to a working link to the run that had, in fact, completed.
    while not tool_queue.empty():
        try:
            evt_type, evt_data = tool_queue.get_nowait()
            if evt_type == "trace_url":
                trace_url = evt_data
            elif evt_type == "done" and evt_data:
                specialist_response = evt_data
        except queue.Empty:
            break

    for t in tool_trace:
        if t["status"] == "running":
            t["status"] = "done"

    skip_synthesis = _should_skip_synthesis(specialist_name, specialist_response)

    yield f"data: {_json.dumps({'type': 'tool_update', 'content': 'Formatting structured response...'})}\n\n"

    final_response = specialist_response if skip_synthesis else _synthesize_response(specialist_name, specialist_response, context)

    _conversation_store.save_message(
        conversation_id, "assistant", final_response,
        {"specialist": specialist_name, "model": MODEL_MAP.get(specialist_name)},
    )

    elapsed_ms = int((time.time() - start) * 1000)

    reasoning_steps = [
        {"label": "Context builder", "detail": "Loaded shift data, KPIs, events, machine states via parallel SQL queries"},
        {"label": "Intent classifier", "detail": f"LLM classified query intent → routed to {specialist_label}"},
        {"label": f"{specialist_label} (LLM)", "detail": f"ReAct agent with model {resolve(specialist_name)}"},
    ]
    reasoning_steps.extend(_tool_steps(tool_trace))
    if not skip_synthesis:
        reasoning_steps.append({"label": "Response synthesis (LLM)", "detail": "Structured and grounded the specialist output"})

    actions = _extract_actions(specialist_name, context)

    endpoint_used = resolve(specialist_name)
    model_display, model_vendor = describe(endpoint_used)
    answer_payload = {
        'type': 'answer',
        'content': final_response,
        'specialist_used': specialist_name,
        'model_used': endpoint_used,
        # Friendly name + vendor so the chat can attribute the answer without
        # the UI having to keep its own copy of the endpoint-name mapping.
        'model_display': model_display,
        'model_vendor': model_vendor,
        'response_time_ms': elapsed_ms,
        'conversation_id': conversation_id,
        'tool_trace': tool_trace,
        'reasoning_steps': reasoning_steps,
        # Absent rather than null when tracing isn't configured, so the UI's
        # "is there a link?" check is a plain truthiness test.
        **({'trace_url': trace_url} if trace_url else {}),
        'actions': actions,
    }
    yield f"data: {_json.dumps(answer_payload)}\n\n"
    yield "data: [DONE]\n\n"
