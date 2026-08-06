"""Read the traces a live run produced and say what actually happened.

The load-bearing idea: **span status is not tool health.** Every tool in this
agent catches its own failures and returns them as content, so a SQL query
against a table that does not exist, a vector search that matches nothing and a
detector with no telemetry all finish with ``SpanStatusCode.OK`` and
``status: success``. Counting span statuses would report a perfect run. This
classifies each tool call by reading its payload instead:

  ok     — returned usable data
  empty  — succeeded and returned nothing (no rows, no matches, zero points)
  error  — carried an error message, whatever the span status claimed

``empty`` is kept separate from ``error`` on purpose. An empty result is
sometimes correct (no anomalies is good news) and sometimes the symptom of
querying something nothing writes to, and only the rate across many questions
tells you which.

Usage:
  python -m evaluations.analyze_live --run /tmp/live_run_99.jsonl \
      --experiment 3657367258539158 --out /tmp/eval_summary.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

# Substrings that mark a payload as carrying a failure. Deliberately narrow:
# matching the bare word "error" would catch the error_rate column that several
# legitimate results contain.
ERROR_MARKERS = (
    "NOT_FOUND", "cannot be found", "Traceback", "Exception",
    "PERMISSION_DENIED", "ParseException", "AnalysisException",
    "failed", "Invalid", "denied",
    # Added after a run reported 100% healthy tools while these were in the
    # payloads. Spark reports a bad column as UNRESOLVED_COLUMN, never as
    # NOT_FOUND, so every wrong column name in the codebase scored as ok.
    "UNRESOLVED_COLUMN", "cannot be resolved", "UNBOUND_SQL_PARAMETER",
    "DIVIDE_BY_ZERO", "timed out", "Timeout", "TimeoutError",
)

# Not errors, but not answers either: the tool ran and truthfully reported that
# it found nothing. Distinguished from ok because a specialist citing one of
# these is grounding an answer in an empty result, which is the exact failure
# the confidence score is supposed to catch.
EMPTY_MARKERS = (
    "no data found", "no telemetry data", "no rows", "no results",
    "not enough data", "no matching", "returned no",
)


def _unwrap(output: Any) -> Any:
    """Reduce a tool span's output to the payload the model actually saw.

    LangChain tool spans wrap the result in a ToolMessage envelope whose
    ``content`` is usually a JSON string; SQL tools return a bare list. Both
    shapes appear in the same trace.
    """
    if isinstance(output, str):
        try:
            output = json.loads(output)
        except (ValueError, TypeError):
            return output
    if isinstance(output, dict) and "content" in output and set(output) & {"tool_call_id", "additional_kwargs"}:
        output = output["content"]
    if isinstance(output, str):
        try:
            return json.loads(output)
        except (ValueError, TypeError):
            return output
    return output


def _is_hollow(value: Any) -> bool:
    """True when a value carries no information, however deeply it is nested.

    A shallow len()==0 test missed ``{"data": {"items": []}, "count": 0}``:
    the outer dict has one key, so it looked populated and the tool scored ok.
    """
    if value is None or value == 0 or value == "":
        return True
    if isinstance(value, (list, tuple, set)):
        return all(_is_hollow(v) for v in value)
    if isinstance(value, dict):
        return all(_is_hollow(v) for v in value.values())
    return False


def classify(output: Any) -> tuple[str, str]:
    """Return (verdict, detail) for one tool result.

    A tool span completing without raising says nothing about whether the tool
    worked: these tools catch their own exceptions and return a payload with
    ``status: success`` alongside a message explaining that the table does not
    exist. So the payload, not the span, decides the verdict.
    """
    payload = _unwrap(output)

    if payload is None:
        return "empty", ""
    if isinstance(payload, str):
        if any(m in payload for m in ERROR_MARKERS):
            return "error", payload[:200]
        if any(m in payload.lower() for m in EMPTY_MARKERS):
            return "empty", payload[:200]
        return ("empty", "") if not payload.strip() else ("ok", "")

    blob = json.dumps(payload, default=str) if isinstance(payload, (list, dict)) else str(payload)

    if isinstance(payload, list):
        if not payload:
            return "empty", ""
        # Errors do not always arrive under an "error" key, and not always in
        # the first row: `[{"message": "TABLE_OR_VIEW_NOT_FOUND"}]` was ok.
        for row in payload:
            if isinstance(row, dict):
                for key in ("error", "message", "detail", "status_message"):
                    val = row.get(key)
                    if val and any(m in str(val) for m in ERROR_MARKERS):
                        return "error", str(val)[:200]
                if row.get("error"):
                    return "error", str(row["error"])[:200]
        if any(m in blob for m in ERROR_MARKERS):
            idx = blob.find(next(m for m in ERROR_MARKERS if m in blob))
            return "error", blob[max(0, idx - 80): idx + 120]
        return ("empty", "") if _is_hollow(payload) else ("ok", "")

    if isinstance(payload, dict):
        err = payload.get("error")
        if err:
            return "error", str(err)[:200]
        if any(m in blob for m in ERROR_MARKERS):
            match = next(m for m in ERROR_MARKERS if m in blob)
            idx = blob.find(match)
            return "error", blob[max(0, idx - 80): idx + 120]
        if any(m in blob.lower() for m in EMPTY_MARKERS):
            return "empty", blob[:200]
        # Ignore envelope keys so a bare {"status": "success"} is not mistaken
        # for a result.
        values = [v for k, v in payload.items() if k not in ("status", "name", "type", "id")]
        if not values or all(_is_hollow(v) for v in values):
            return "empty", ""
        return "ok", ""
    return "ok", ""


def _missing_table(detail: str) -> str | None:
    m = re.search(r"`([A-Za-z_][A-Za-z0-9_]*)` cannot be found", detail)
    return m.group(1) if m else None


def _latency_by_specialist(per_question: list[dict]) -> dict:
    """Where each specialist's time goes: the model, or waiting on tools.

    The distinction decides what to do about a slow specialist. Time in
    CHAT_MODEL spans is answered by a cheaper or faster model; time in TOOL
    spans is not, and swapping the model there buys nothing. ``llm_calls`` is
    the third number worth reading — a specialist making eight round trips is
    slow because of its loop, and a faster model only scales that down
    linearly.

    Reported as medians. The distribution is wide enough that a mean is
    dominated by whichever question happened to hit a Genie cold start.
    """
    by: dict[str, list[dict]] = defaultdict(list)
    for q in per_question:
        if q.get("error"):
            continue
        by[q["actual"] or "ERROR"].append(q)

    out = {}
    for name, qs in by.items():
        total = [q["latency_ms"] for q in qs]
        llm = [q["llm_ms"] for q in qs]
        tool = [q["tool_ms"] for q in qs]
        med_total = statistics.median(total)
        out[name] = {
            "n": len(qs),
            "p50_total_ms": int(med_total),
            "max_total_ms": max(total),
            "p50_llm_ms": int(statistics.median(llm)),
            "p50_tool_ms": int(statistics.median(tool)),
            "llm_share": round(statistics.median(llm) / med_total, 3) if med_total else 0,
            "p50_llm_calls": int(statistics.median([q["llm_calls"] for q in qs])),
            "p50_tool_calls": int(statistics.median([q["tool_calls"] for q in qs])),
        }
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["p50_total_ms"]))


def _richest_per_query(matches: dict[str, list]) -> dict:
    """One trace per question: the one with the most spans.

    A single question leaves several traces behind — the agent run plus
    smaller ones for classification and synthesis. Averaging over all of them
    drags every median toward the small ones and makes each specialist look
    like it does one model call and no tool calls, which is the opposite of
    what the agent trace shows.
    """
    return {q: max(ts, key=lambda t: len(t.data.spans or [])) for q, ts in matches.items() if ts}


def analyze(run_path: Path, experiment: str, warehouse: str, since_ms: int = 0) -> dict:
    import logging
    import os

    # Traces live in Delta tables now, and reading them goes through SQL rather
    # than the tracking API. Without this the search fails with a bare "SQL
    # warehouse ID is required" that says nothing about which warehouse.
    os.environ.setdefault("MLFLOW_TRACING_SQL_WAREHOUSE_ID", warehouse)

    import mlflow

    logging.disable(logging.WARNING)

    rows = [json.loads(line) for line in run_path.read_text().splitlines() if line.strip()]
    by_query = {r["query"]: r for r in rows}

    # Scope to this run. The same questions get asked every run, so matching on
    # query text alone silently folds previous runs' traces into the medians.
    filt = f"timestamp_ms > {since_ms}" if since_ms else None
    traces = mlflow.search_traces(
        locations=[experiment], filter_string=filt, max_results=1000, return_type="list"
    )
    print(f"{len(rows)} run rows, {len(traces)} traces in scope")

    tool_stats: dict[str, Counter] = defaultdict(Counter)
    tool_latency: dict[str, list[float]] = defaultdict(list)
    error_details: Counter = Counter()
    missing_tables: Counter = Counter()
    confidence: Counter = Counter()
    conf_by_specialist: dict[str, Counter] = defaultdict(Counter)
    per_question: list[dict] = []
    matched = 0

    candidates: dict[str, list] = defaultdict(list)
    for tr in traces:
        spans = tr.data.spans if tr.data else []
        if not spans:
            continue
        inputs = spans[0].inputs
        if isinstance(inputs, str):
            try:
                inputs = json.loads(inputs)
            except ValueError:
                inputs = {}
        query = (inputs or {}).get("query") if isinstance(inputs, dict) else None
        if query in by_query:
            candidates[query].append(tr)

    for query, tr in _richest_per_query(candidates).items():
        spans = tr.data.spans or []
        row = by_query[query]
        matched += 1

        conf = (tr.info.tags or {}).get("confidence")
        if conf:
            confidence[conf] += 1
            # The specialist that RAN, not the one we hoped would. Keying this
            # by expectation meant every mis-routed question credited its
            # confidence to a specialist that never executed, and the error was
            # systematic rather than noisy: whichever specialist absorbs the
            # mis-routes donates its confidence to whoever was expected.
            conf_by_specialist[row.get("actual_specialist") or "ERROR"][conf] += 1
        else:
            # Absent is a distinct outcome from LOW (agent.py says so) and used
            # to vanish entirely, leaving a distribution with no denominator —
            # {"HIGH": 31, "MEDIUM": 12} next to "questions: 99" reads as 56 LOW.
            confidence["UNPARSED"] += 1

        tools_here: list[str] = []
        llm_calls = 0
        llm_ms = 0.0
        tool_ms = 0.0
        for s in spans:
            dur = (s.end_time_ns - s.start_time_ns) / 1e6 if s.end_time_ns and s.start_time_ns else 0.0
            if s.span_type == "TOOL":
                verdict, detail = classify(s.outputs)
                tool_stats[s.name][verdict] += 1
                tool_stats[s.name]["calls"] += 1
                tool_latency[s.name].append(dur)
                tool_ms += dur
                tools_here.append(s.name)
                if verdict == "error":
                    error_details[f"{s.name}: {detail[:120]}"] += 1
                    tbl = _missing_table(detail)
                    if tbl:
                        missing_tables[tbl] += 1
            elif s.span_type == "CHAT_MODEL":
                llm_calls += 1
                llm_ms += dur

        per_question.append({
            "query": query,
            "expected": row["expected_specialist"],
            "actual": row["actual_specialist"],
            "routed_ok": row["actual_specialist"] == row["expected_specialist"],
            "confidence": conf,
            "latency_ms": row["latency_ms"],
            "content_chars": row["content_chars"],
            "tool_calls": len(tools_here),
            "distinct_tools": sorted(set(tools_here)),
            "llm_calls": llm_calls,
            "llm_ms": round(llm_ms),
            "tool_ms": round(tool_ms),
            "error": row.get("error"),
        })

    lat = [r["latency_ms"] for r in rows if not r.get("error")]
    routed_ok = sum(1 for r in rows if r["actual_specialist"] == r["expected_specialist"])
    confusion: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        confusion[r["expected_specialist"]][r["actual_specialist"] or "ERROR"] += 1

    summary = {
        "questions": len(rows),
        "traces_matched": matched,
        "errors": sum(1 for r in rows if r.get("error")),
        "routing": {
            "correct": routed_ok,
            "accuracy": round(routed_ok / len(rows), 4) if rows else 0,
            "confusion": {k: dict(v) for k, v in sorted(confusion.items())},
        },
        "latency_ms": {
            "p50": int(statistics.median(lat)) if lat else 0,
            "p90": int(sorted(lat)[int(len(lat) * 0.9)]) if lat else 0,
            "max": max(lat) if lat else 0,
            "mean": int(statistics.mean(lat)) if lat else 0,
        },
        "tools": {
            name: {
                "calls": c["calls"],
                "ok": c["ok"],
                "empty": c["empty"],
                "error": c["error"],
                "ok_rate": round(c["ok"] / c["calls"], 3) if c["calls"] else 0,
                "p50_ms": int(statistics.median(tool_latency[name])) if tool_latency[name] else 0,
            }
            for name, c in sorted(tool_stats.items(), key=lambda kv: -kv[1]["calls"])
        },
        "latency_by_specialist": _latency_by_specialist(per_question),
        "top_errors": error_details.most_common(12),
        "missing_tables": missing_tables.most_common(10),
        "confidence": dict(confidence),
        "confidence_by_specialist": {k: dict(v) for k, v in sorted(conf_by_specialist.items())},
        "per_question": per_question,
    }
    return summary


def _resolve_since_ms(since: str, run_path: Path) -> int:
    """Lower bound on trace timestamps, inferred from the run when not given.

    Leaving this at 0 was a quiet correctness bug rather than a convenience:
    traces are matched to questions by query text, and these 99 questions get
    replayed every time, so the "richest trace" for a question could easily be
    one from last week. Latency came from this run's JSONL while tool results
    came from the older trace, and the two disagreed with no sign of it.

    run_live.py stamps every row with ``sent_at``, so the run knows its own
    start time. The 60s of slack covers clock skew between the client and the
    trace backend.
    """
    if since:
        return int(datetime.fromisoformat(since).timestamp() * 1000)

    stamps = []
    for line in run_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            sent_at = json.loads(line).get("sent_at")
        except ValueError:
            continue
        if sent_at:
            try:
                stamps.append(datetime.fromisoformat(sent_at))
            except ValueError:
                pass
    if not stamps:
        print("WARN: no sent_at in the run file — traces from earlier runs of the "
              "same questions may be folded in. Pass --since to scope them.")
        return 0
    earliest = min(stamps)
    print(f"scoping traces to >= {earliest.isoformat()} (inferred from the run file)")
    return int(earliest.timestamp() * 1000) - 60_000


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", required=True)
    ap.add_argument("--experiment", required=True)
    ap.add_argument("--warehouse", default=os.environ.get("DATABRICKS_WAREHOUSE_ID"),
                    help="SQL warehouse that reads the UC-backed trace tables "
                         "(default: $DATABRICKS_WAREHOUSE_ID)")
    ap.add_argument("--out", default="/tmp/eval_summary.json")
    ap.add_argument(
        "--since",
        default="",
        help="ISO time of the run start (e.g. 2026-08-05T13:56). Defaults to the "
        "earliest sent_at in the run file, which is what you want; override only "
        "to widen the window deliberately.",
    )
    args = ap.parse_args()

    since_ms = _resolve_since_ms(args.since, Path(args.run))

    summary = analyze(Path(args.run), args.experiment, args.warehouse, since_ms)
    Path(args.out).write_text(json.dumps(summary, indent=2), encoding="utf-8")

    s = summary
    print(f"\nrouting accuracy : {s['routing']['correct']}/{s['questions']} "
          f"({s['routing']['accuracy']:.0%})")
    print(f"errors           : {s['errors']}")
    print(f"latency p50/p90  : {s['latency_ms']['p50']}ms / {s['latency_ms']['p90']}ms")
    print(f"confidence       : {s['confidence']}")
    print("\ntool                        calls    ok  empty  error   p50")
    for name, t in s["tools"].items():
        print(f"  {name:24} {t['calls']:5} {t['ok']:5} {t['empty']:6} {t['error']:6} {t['p50_ms']:5}")
    print("\nspecialist                 n   p50_total   p50_llm  p50_tool  llm%  calls(llm/tool)")
    for name, d in s["latency_by_specialist"].items():
        print(f"  {name:22} {d['n']:3} {d['p50_total_ms']:9}ms {d['p50_llm_ms']:8}ms "
              f"{d['p50_tool_ms']:8}ms {d['llm_share']:5.0%}  {d['p50_llm_calls']}/{d['p50_tool_calls']}")
    if s["missing_tables"]:
        print("\nmissing tables:", dict(s["missing_tables"]))
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
