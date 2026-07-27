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

Why three LLM endpoints? The orchestrator-tier calls (classification,
synthesis, general fallback) are short and structured — the mini endpoint
handles them at lower cost and latency. The specialist ReAct loops do real
multi-tool reasoning and use the larger MODEL_MAP-driven endpoints picked
per specialist inside their `create_*()` factories.
"""
import json
import logging
import os
import queue
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout

import mlflow
from databricks_langchain import ChatDatabricks
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from agent_server.context_builder import build_context
from agent_server.memory.conversation_store import ConversationStore
from agent_server.prompts.orchestrator import INTENT_CLASSIFY_TEMPLATE, ORCHESTRATOR_SYSTEM
from agent_server.prompts.response_synthesis import SYNTHESIS_TEMPLATE
from agent_server.specialists.downtime_analyst import create_downtime_analyst
from agent_server.specialists.maintenance_predictor import create_maintenance_predictor
from agent_server.specialists.oee_coach import create_oee_coach
from agent_server.specialists.quality_specialist import create_quality_specialist
from agent_server.specialists.schedule_optimizer import create_schedule_optimizer
from agent_server.specialists.shift_briefing import create_shift_briefing
from agent_server.specialists.strategic_advisor import create_strategic_advisor
from agent_server.specialists.supply_chain_advisor import create_supply_chain_advisor

logger = logging.getLogger(__name__)

# Captures every LangChain ChatDatabricks/ReAct step as an MLflow trace span so
# we can query latency + tool-call breakdowns from the experiment view.
mlflow.langchain.autolog()

# Used for intent classification, the "general" fallback path, and response
# synthesis. Kept as a single small endpoint because all three are short,
# structured tasks where the larger models add cost without measurable quality.
_ORCHESTRATOR_MODEL = os.environ.get("MODEL_ORCHESTRATOR", "databricks-gpt-5-mini")


def _overlay_live_state(context: dict) -> dict:
    """Replace operational slices of `context` with the live Lakebase snapshot.

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

        shift_id = os.environ.get("SIM_SHIFT_ID", "shift_1")
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

        context.setdefault("scenario", {})
        context["scenario"]["current_timestamp"] = now_iso
        context["scenario"]["scenario_name"] = "Live Real-Time (Lakebase)"

        logger.info(
            "Overlaid live Lakebase state: shift=%s machines=%d events=%d incidents=%d",
            shift_id, len(machines), len(context["active_events"]),
            len(context["active_incidents"]),
        )
    except Exception:
        logger.exception("Failed to overlay live state — using historical context")

    return context

SPECIALIST_TIMEOUT_SECONDS = int(os.environ.get("SPECIALIST_TIMEOUT_SECONDS", "90"))
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="specialist")
_conversation_store = ConversationStore()


def _capture_mlflow_trace_spans() -> list[dict]:
    """Capture MLflow trace spans from the current thread's last active trace.

    Returns a list of serialized span dicts for inline display. Returns [] if
    the trace can't be retrieved (e.g. thread-local limitations).
    """
    try:
        last_trace = mlflow.get_last_active_trace()
        if not last_trace or not last_trace.data or not last_trace.data.spans:
            return []

        spans = []
        for span in last_trace.data.spans:
            duration_ms = 0
            if span.start_time_ns and span.end_time_ns:
                duration_ms = round((span.end_time_ns - span.start_time_ns) / 1_000_000)

            span_dict: dict = {
                "name": span.name or "unknown",
                "span_type": getattr(span, "span_type", None) or getattr(span, "_span_type", "UNKNOWN"),
                "status": span.status.status_code.value if span.status else "UNSET",
                "duration_ms": duration_ms,
            }

            inputs = getattr(span, "inputs", None)
            if inputs:
                input_str = json.dumps(inputs, default=str) if not isinstance(inputs, str) else inputs
                span_dict["inputs"] = input_str[:200] + ("…" if len(input_str) > 200 else "")

            outputs = getattr(span, "outputs", None)
            if outputs:
                output_str = json.dumps(outputs, default=str) if not isinstance(outputs, str) else outputs
                span_dict["outputs"] = output_str[:200] + ("…" if len(output_str) > 200 else "")

            spans.append(span_dict)

        return spans
    except Exception:
        logger.debug("Could not retrieve MLflow trace spans", exc_info=True)
        return []

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


def _classify_intent(query: str, context: dict) -> str:
    """LLM-route a query to one of the registered specialist names.

    Only called when `_route_by_active_incident` returned None — see the
    module docstring for routing precedence. Returns one of the valid
    specialist names or "general"; an unrecognised LLM output is logged
    and downgraded to "general" so the user still gets a response.
    """
    # max_tokens=50 is enough for any specialist name + slack. Anything longer
    # is almost certainly the model echoing the question — we'd discard it.
    llm = ChatDatabricks(endpoint=_ORCHESTRATOR_MODEL, max_tokens=50)

    # Build summary strings for the classification prompt
    events_summary = ", ".join(
        f"{e.get('machine_id', '?')}: {e.get('event_type', '?')}"
        for e in context.get("active_events", [])[:5]
    ) or "None"

    line_summary_parts = []
    for m in context.get("line_state", [])[:5]:
        line_summary_parts.append(f"{m.get('machine_id', '?')}={m.get('status', '?')}")
    line_summary = ", ".join(line_summary_parts) or "All running"

    prompt_text = INTENT_CLASSIFY_TEMPLATE.format(
        shift_id=context.get("scenario", {}).get("shift_id", "?"),
        scenario_name=context.get("scenario", {}).get("scenario_name", "?"),
        timestamp=context.get("scenario", {}).get("current_timestamp", "?"),
        active_events_summary=events_summary,
        line_state_summary=line_summary,
        query=query,
    )

    messages = [
        SystemMessage(content=ORCHESTRATOR_SYSTEM),
        HumanMessage(content=prompt_text),
    ]

    try:
        response = llm.invoke(messages)
        intent = response.content.strip().lower().replace(" ", "_")
        valid = {
            "downtime_analyst", "quality_specialist", "supply_chain_advisor",
            "oee_coach", "schedule_optimizer", "maintenance_predictor",
            "strategic_advisor", "shift_briefing", "general",
        }
        if intent not in valid:
            logger.warning("Unknown intent '%s', defaulting to general", intent)
            return "general"
        return intent
    except Exception:
        logger.exception("Intent classification failed")
        return "general"

def _quick_snapshot(context: dict) -> str:
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
        parts.append(f"  OEE: **{float(oee):.1f}%** | "
                     f"A: {float(kpi.get('availability_to_now', 0)):.1f}% · "
                     f"P: {float(kpi.get('performance_to_now', 0)):.1f}% · "
                     f"Q: {float(kpi.get('quality_to_now', 0)):.1f}%")

    actual = kpi.get("cumulative_cases")
    planned = plan.get("planned_cases")
    if actual is not None and planned and int(planned) > 0:
        pct = int(actual) / int(planned) * 100
        parts.append(f"  Cases: **{int(actual):,}** / {int(planned):,} ({pct:.0f}% of plan)")

    stopped = [m.get("machine_id", "?") for m in line_state if m.get("status") in ("stopped", "degraded", "starved")]
    running_count = sum(1 for m in line_state if m.get("status") == "running")
    if stopped:
        parts.append(f"  Line: {running_count} running, **{len(stopped)} issue(s)**: {', '.join(stopped[:5])}")
    else:
        parts.append(f"  Line: All {running_count} machines running")

    active = [e for e in events if not e.get("timestamp_end")]
    if active:
        for e in active[:3]:
            parts.append(f"  Alert: **{e.get('machine_id', '?')}** — {e.get('event_type', '?')} ({e.get('severity', '?')}): {e.get('description', '')[:80]}")

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

    events = context.get("active_events", [])
    slim["active_events"] = events[:10]

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
}


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
        trace_spans = _capture_mlflow_trace_spans()
        if trace_spans:
            event_queue.put(("trace_spans", trace_spans))
        return result

    agent = specialists[specialist_name]
    context_str = json.dumps(trimmed, indent=2, default=str)
    input_messages = [
        HumanMessage(content=f"Context:\n{context_str}\n\nUser query: {query}"),
    ]

    def _stream_invoke() -> tuple[str, str | None]:
        final_content = "Analysis complete but no response generated."
        try:
            for event in agent.stream({"messages": input_messages}, {"recursion_limit": 10}):
                for node_name, node_output in event.items():
                    msgs = node_output.get("messages", [])
                    for msg in msgs:
                        if isinstance(msg, AIMessage) and hasattr(msg, 'tool_calls') and msg.tool_calls:
                            for tc in msg.tool_calls:
                                tool_name = tc.get("name", "unknown")
                                display = TOOL_DISPLAY_NAMES.get(tool_name, f"Using {tool_name}")
                                event_queue.put(("tool", display))
                        if isinstance(msg, ToolMessage):
                            tool_name = getattr(msg, 'name', '') or ''
                            display = TOOL_DISPLAY_NAMES.get(tool_name, '')
                            if display:
                                event_queue.put(("tool_done", display))
                        if isinstance(msg, AIMessage) and msg.content and not getattr(msg, 'tool_calls', None):
                            final_content = msg.content
        except Exception as exc:
            logger.exception("Streaming specialist %s failed: %s", specialist_name, exc)
            final_content = (
                f"The {specialist_name.replace('_', ' ')} encountered an error: {type(exc).__name__}. "
                "Please try rephrasing your question."
            )

        trace_spans = _capture_mlflow_trace_spans()
        return final_content, trace_spans

    trace_spans: list[dict] = []
    try:
        future = _executor.submit(_stream_invoke)
        result, trace_spans = future.result(timeout=SPECIALIST_TIMEOUT_SECONDS)
    except FuturesTimeout:
        logger.error("Specialist %s timed out after %ds (stream)", specialist_name, SPECIALIST_TIMEOUT_SECONDS)
        result = (
            f"The {specialist_name.replace('_', ' ')} analysis timed out. "
            "Please try a more specific question."
        )

    event_queue.put(("done", result))
    if trace_spans:
        event_queue.put(("trace_spans", trace_spans))
    return result


def _run_specialist(specialist_name: str, query: str, context: dict) -> str:
    """Run the appropriate specialist agent with timeout and structured error handling."""
    specialists = _get_specialists()

    trimmed = _trim_context(context, specialist_name)

    if specialist_name == "general" or specialist_name not in specialists:
        # "General" is the no-tool path: a single chat call with the trimmed
        # context as a system message. Used when the LLM router couldn't find
        # a specialist match — usually "give me a status" or "what's going on".
        llm = ChatDatabricks(endpoint=_ORCHESTRATOR_MODEL, max_tokens=1500)
        context_str = json.dumps(trimmed, indent=2, default=str)
        messages = [
            SystemMessage(content=f"You are a helpful Line Manager AI assistant. Here is the current context:\n{context_str}"),
            HumanMessage(content=query),
        ]
        try:
            response = llm.invoke(messages)
            return response.content
        except Exception:
            logger.exception("General query failed")
            return "I'm sorry, I couldn't process your query. Please try again."

    agent = specialists[specialist_name]
    context_str = json.dumps(trimmed, indent=2, default=str)
    input_messages = [
        HumanMessage(content=f"Context:\n{context_str}\n\nUser query: {query}"),
    ]

    def _invoke():
        return agent.invoke({"messages": input_messages}, {"recursion_limit": 10})

    try:
        future = _executor.submit(_invoke)
        result = future.result(timeout=SPECIALIST_TIMEOUT_SECONDS)
        for msg in reversed(result.get("messages", [])):
            if isinstance(msg, AIMessage) and msg.content:
                return msg.content
        return "Analysis complete but no response generated."
    except FuturesTimeout:
        logger.error("Specialist %s timed out after %ds", specialist_name, SPECIALIST_TIMEOUT_SECONDS)
        return (
            f"The {specialist_name.replace('_', ' ')} analysis timed out. "
            "This usually means the query required too many tool calls. "
            "Please try a more specific question."
        )
    except Exception as exc:
        logger.exception("Specialist %s failed: %s", specialist_name, exc)
        return (
            f"The {specialist_name.replace('_', ' ')} encountered an error: {type(exc).__name__}. "
            "Falling back to a general analysis. Please try rephrasing your question."
        )

def _synthesize_response(specialist_name: str, specialist_response: str, context: dict) -> str:
    """Reformat the specialist output to the SYNTHESIS_TEMPLATE structure.

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

    llm = ChatDatabricks(endpoint=_ORCHESTRATOR_MODEL, max_tokens=2000)

    kpi = context.get("current_kpi", {})
    synthesis_prompt = SYNTHESIS_TEMPLATE.format(
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
        return response.content
    except Exception:
        logger.exception("Synthesis failed, returning raw specialist response")
        return specialist_response

def _should_skip_synthesis(specialist_name: str, specialist_response: str) -> bool:
    """Return True when the synthesis LLM call would waste tokens.

    We skip when:
      * the routing fell through to "general" — that path is already a
        short, structured chat call, no second pass needed
      * the specialist already produced a long answer (>800 chars) — likely
        already in the right shape, second pass tends to over-truncate
      * the response is a timeout or explicit error — synthesis would just
        re-emit the same apology
    """
    if specialist_name == "general":
        return True
    if len(specialist_response) > 800:
        return True
    low = specialist_response.lower()
    return "timed out" in low or "encountered an error" in low


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

# Display-only mapping of specialist → endpoint name. The actual ChatDatabricks
# clients live inside each `create_*` factory in agent_server/specialists/;
# this dict is exposed so the UI / reasoning trace can show which endpoint
# answered, and so `test_model_map_completeness` enforces a registered entry
# for every specialist. Override at deploy time via env vars:
#   MODEL_SPECIALIST   — light specialists (mini model)
#   MODEL_ADVANCED     — heavier reasoning specialists (oee_coach, strategic_advisor)
# `general` is here so the "general" routing path also surfaces an endpoint
# in the reasoning trace; functionally it uses _ORCHESTRATOR_MODEL.
MODEL_MAP: dict[str, str] = {
    "downtime_analyst": os.environ.get("MODEL_SPECIALIST", "databricks-gpt-5-mini"),
    "quality_specialist": os.environ.get("MODEL_SPECIALIST", "databricks-gpt-5-mini"),
    "supply_chain_advisor": os.environ.get("MODEL_SPECIALIST", "databricks-gpt-5-mini"),
    "oee_coach": os.environ.get("MODEL_ADVANCED", "databricks-gpt-5"),
    "schedule_optimizer": os.environ.get("MODEL_SPECIALIST", "databricks-gpt-5-mini"),
    "maintenance_predictor": os.environ.get("MODEL_SPECIALIST", "databricks-gpt-5-mini"),
    "strategic_advisor": os.environ.get("MODEL_ADVANCED", "databricks-gpt-5"),
    "shift_briefing": os.environ.get("MODEL_SPECIALIST", "databricks-gpt-5-mini"),
    "general": _ORCHESTRATOR_MODEL,
}


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
    context = _overlay_live_state(context)

    snapshot = _quick_snapshot(context)

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

    return {
        "content": final_response,
        "quick_snapshot": snapshot,
        "specialist_used": specialist_name,
        "model_used": MODEL_MAP.get(specialist_name, "databricks-gpt-5-mini"),
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
                       trace_spans, and actions
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
    context = _overlay_live_state(context)

    snapshot = _quick_snapshot(context)

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

    import threading

    def _run_in_thread():
        _run_specialist_streaming(specialist_name, query, context, tool_queue)

    thread = threading.Thread(target=_run_in_thread, daemon=True)
    thread.start()

    specialist_response = "Analysis could not be completed."
    trace_spans: list[dict] = []
    while True:
        try:
            evt_type, evt_data = tool_queue.get(timeout=1.0)
            if evt_type == "tool":
                tool_trace.append({"step": len(tool_trace) + 1, "action": evt_data, "status": "running"})
                yield f"data: {_json.dumps({'type': 'tool_update', 'content': evt_data})}\n\n"
            elif evt_type == "tool_done":
                for t in reversed(tool_trace):
                    if t["action"] == evt_data and t["status"] == "running":
                        t["status"] = "done"
                        break
                done_label = evt_data.replace("ing ", "ed ").replace("Running ", "Ran ")
                yield f"data: {_json.dumps({'type': 'tool_update', 'content': f'{done_label} ✓'})}\n\n"
            elif evt_type == "trace_spans":
                trace_spans = evt_data
            elif evt_type == "done":
                specialist_response = evt_data if evt_data else specialist_response
                break
        except queue.Empty:
            if not thread.is_alive():
                break

    thread.join(timeout=SPECIALIST_TIMEOUT_SECONDS + 10)

    while not tool_queue.empty():
        try:
            evt_type, evt_data = tool_queue.get_nowait()
            if evt_type == "trace_spans":
                trace_spans = evt_data
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
        {"label": f"{specialist_label} (LLM)", "detail": f"ReAct agent with model {MODEL_MAP.get(specialist_name, 'databricks-gpt-5-mini')}"},
    ]
    for t in tool_trace:
        reasoning_steps.append({"label": t["action"], "detail": "Tool call by specialist agent"})
    if not skip_synthesis:
        reasoning_steps.append({"label": "Response synthesis (LLM)", "detail": "Structured and grounded the specialist output"})

    actions = _extract_actions(specialist_name, context)

    answer_payload = {
        'type': 'answer',
        'content': final_response,
        'specialist_used': specialist_name,
        'model_used': MODEL_MAP.get(specialist_name, 'databricks-gpt-5-mini'),
        'response_time_ms': elapsed_ms,
        'conversation_id': conversation_id,
        'tool_trace': tool_trace,
        'reasoning_steps': reasoning_steps,
        'trace_spans': trace_spans,
        'actions': actions,
    }
    yield f"data: {_json.dumps(answer_payload)}\n\n"
    yield "data: [DONE]\n\n"
