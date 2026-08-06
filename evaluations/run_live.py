"""Submit the routing corpus to a *deployed* app and record what came back.

Why this exists alongside ``run_eval.py``: that harness calls
``agent_server.agent.invoke`` in-process, which is the right shape for
``mlflow.genai.evaluate`` but only tells you about the agent as it runs on the
machine you are sitting at. Off-workspace that is not the agent users get —
Lakebase auth fails, so live reads come back empty and specialists fall back to
whatever context they can assemble. Pointing the same corpus at the deployed
app exercises the service principal, the Genie spaces, Lakebase and the model
endpoints as configured, which is what "do the tools work" actually asks.

Scoring is not done here. This writes one JSONL row per question; judgement
comes from the MLflow traces the app itself emits, joined back on the query
text (every corpus query is unique).

Usage:
  python -m evaluations.run_live --env dev --out /tmp/live_run.jsonl
  python -m evaluations.run_live --env dev --limit 10 --concurrency 2
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

APP_NAMES = {
    "dev": "production-line-copilot-dev",
    "prod": "production-line-copilot-prod",
}


@dataclass
class Result:
    """One question's outcome. Kept flat so the JSONL loads into a DataFrame."""

    index: int
    query: str
    expected_specialist: str
    actual_specialist: str | None
    model_display: str | None
    latency_ms: int
    server_reported_ms: int | None
    content_chars: int
    content_head: str
    http_status: int | None
    error: str | None
    sent_at: str


def _run(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def _token(profile: str) -> str:
    return json.loads(_run(["databricks", "auth", "token", "-p", profile]))["access_token"]


def _app_url(app: str, profile: str) -> str:
    info = json.loads(_run(["databricks", "apps", "get", app, "--profile", profile, "-o", "json"]))
    url = info.get("url")
    if not url:
        raise SystemExit(f"App {app} has no url; is it deployed?")
    return url.rstrip("/")


def ask(session, url: str, token: str, scenario: dict, index: int, shift_id: str, timeout: int) -> Result:
    """One question, one row. Failures are recorded, never raised.

    A question that errors is a finding, not a reason to abandon the run, so
    every exit path produces a Result.
    """
    started = time.time()
    sent_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    common = dict(
        index=index,
        query=scenario["query"],
        expected_specialist=scenario["expected_specialist"],
        sent_at=sent_at,
    )
    try:
        resp = session.post(
            f"{url}/api/agent",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"query": scenario["query"], "shift_id": shift_id},
            timeout=timeout,
        )
        elapsed = int((time.time() - started) * 1000)
        if resp.status_code != 200:
            return Result(**common, actual_specialist=None, model_display=None,
                          latency_ms=elapsed, server_reported_ms=None, content_chars=0,
                          content_head="", http_status=resp.status_code,
                          error=f"HTTP {resp.status_code}: {resp.text[:200]}")
        body = resp.json()
        content = body.get("content") or ""
        return Result(**common,
                      actual_specialist=body.get("specialist_used"),
                      model_display=body.get("model_display"),
                      latency_ms=elapsed,
                      server_reported_ms=body.get("response_time_ms"),
                      content_chars=len(content),
                      content_head=content[:400],
                      http_status=200,
                      error=None)
    except Exception as exc:  # noqa: BLE001 - a failed question is data
        return Result(**common, actual_specialist=None, model_display=None,
                      latency_ms=int((time.time() - started) * 1000),
                      server_reported_ms=None, content_chars=0, content_head="",
                      http_status=None, error=f"{type(exc).__name__}: {exc}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--env", choices=["dev", "prod"], default="dev")
    # No default: the previous one was the author's personal CLI profile, which
    # in a public repo either fails for everyone else or, worse, resolves to
    # something unrelated in their ~/.databrickscfg.
    ap.add_argument("--profile", default=os.environ.get("DATABRICKS_CONFIG_PROFILE"),
                    help="Databricks CLI profile (default: $DATABRICKS_CONFIG_PROFILE)")
    ap.add_argument("--shift-id", default="shift_1")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--concurrency", type=int, default=4,
                    help="the app runs 6 specialist workers; stay under that")
    ap.add_argument("--timeout", type=int, default=240)
    ap.add_argument("--out", default="/tmp/live_run.jsonl")
    args = ap.parse_args()

    import requests

    from evaluations.scenarios import INTENT_SCENARIOS

    scenarios = INTENT_SCENARIOS[: args.limit] if args.limit else list(INTENT_SCENARIOS)
    token = _token(args.profile)
    url = _app_url(APP_NAMES[args.env], args.profile)
    print(f"{len(scenarios)} questions -> {url} (concurrency {args.concurrency})", flush=True)

    session = requests.Session()
    results: list[Result] = []
    started = time.time()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {
            pool.submit(ask, session, url, token, s, i, args.shift_id, args.timeout): i
            for i, s in enumerate(scenarios)
        }
        for done, fut in enumerate(as_completed(futures), start=1):
            res = fut.result()
            results.append(res)
            flag = "ERR " if res.error else ("   " if res.actual_specialist == res.expected_specialist else "MISS")
            print(f"  [{done:3}/{len(scenarios)}] {flag} {res.latency_ms:6}ms "
                  f"{res.expected_specialist:22} -> {res.actual_specialist or '-'}", flush=True)

    results.sort(key=lambda r: r.index)
    out = Path(args.out)
    out.write_text("\n".join(json.dumps(asdict(r)) for r in results) + "\n", encoding="utf-8")

    errors = sum(1 for r in results if r.error)
    hits = sum(1 for r in results if r.actual_specialist == r.expected_specialist)
    print(f"\nwrote {out}  ({time.time() - started:.0f}s wall)")
    print(f"  routed as expected : {hits}/{len(results)}")
    print(f"  errors             : {errors}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
