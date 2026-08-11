#!/usr/bin/env python3
"""What this deploy will and will not build — the whole inventory, in one place.

The accelerator has grown capabilities that are switched on by a single settings
key each: semantic retrieval over the case history, a Knowledge Assistant over
the plant documents, the governed industry model with its four per-domain Genie
spaces, DQX quality checks. Every one of them was invisible unless you already
knew it existed. The deploy mentioned some of them in a skip line buried in a
log hundreds of lines long, mentioned others nowhere at all, and someone
deploying a fresh clone had no way to learn that the demo they just stood up was
a subset of the product.

So the inventory is a thing you can ask for::

    ./scripts/deploy.sh dev --capabilities     # prints this, deploys nothing

and the deploy prints it twice: once up front as the plan, so the timings are
known before the wait rather than during it, and once at the end as what
actually happened.

Reads the same settings file the deploy reads (``.local.yaml`` when present), so
what it prints is what will run.

Usage:
  python3 scripts/capabilities.py <dev|prod> [--minimal] [--json]
                                  [--settings config/project-settings.yaml]
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

ENV_TO_BUNDLE_TARGET = {"dev": "line_manager_dev", "prod": "line_manager_prod"}

# The two that bill by the hour. MINIMAL=1 exists for the deploy that wants the
# product without the meter running, so it is exactly these two — DQX and the
# governed model cost nothing beyond compute already running, and turning them
# off would make a "minimal" deploy quietly less capable for no saving.
BILLING = ("vector_search", "knowledge_assistant")


@dataclass(frozen=True)
class Capability:
    id: str
    title: str
    key: str
    # What the app does when this is off. Not "nothing": every one of these has a
    # defined unconfigured behaviour, and knowing it is how you decide.
    without: str
    cost: str
    first_deploy: str
    # A key whose value is a name/id is on when non-empty; a boolean key is on
    # when true. Placeholders are off — SET_ME is not a value.
    boolean: bool = False
    # Always provisioned; listed so the inventory is the whole picture.
    always: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)


CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        id="genie_space",
        title="Genie space over the live shift",
        key="genie_space_id",
        without="not optional — the in-app agent answers line questions through it",
        cost="per query",
        first_deploy="~1 min, created automatically",
        always=True,
    ),
    Capability(
        id="zerobus",
        title="Zerobus ingest (records straight into bronze)",
        key="zerobus_ingest_enabled",
        without="the producer writes through the SQL path instead",
        cost="free",
        first_deploy="none",
        boolean=True,
    ),
    Capability(
        id="dqx",
        title="DQX quality checks (freshness, range, referential)",
        key="dqx_enabled",
        without="rows are QC'd by the inline predicates only, no dqx_warnings table",
        cost="free — runs inside the streaming job already running",
        first_deploy="none",
        boolean=True,
    ),
    Capability(
        id="governed_model",
        title="Governed industry model + 4 domain Genie spaces",
        key="mvm_silver_enabled",
        without="the domain specialists fall back to sql_query on the raw tables",
        cost="free beyond the queries themselves",
        first_deploy="~5 min to create the four spaces",
        boolean=True,
    ),
    Capability(
        id="vector_search",
        title="Semantic retrieval over the case history",
        key="vector_search_endpoint",
        without="retrieve_similar_cases ranks past events by keyword overlap",
        cost="BILLS HOURLY while the endpoint is online",
        first_deploy="endpoint takes 15-40 min on Databricks' side; the deploy does "
        "not wait, and the index is built by your next deploy (VS_WAIT=1 blocks)",
    ),
    Capability(
        id="knowledge_assistant",
        title="Knowledge Assistant over the plant documents",
        key="knowledge_assistant_name",
        without="query_plant_docs reports that no assistant is configured, so an SOP "
        "question is answered with 'the documents were not read'",
        cost="BILLS HOURLY while the agent endpoint is online",
        first_deploy="indexing the corpus takes minutes to a couple of hours; the "
        "deploy does not wait, and answers are ungrounded until it finishes",
    ),
)


def _is_placeholder(value: object) -> bool:
    text = str(value if value is not None else "").strip()
    return not text or text == "SET_ME" or ("<" in text and ">" in text)


def settings_path(explicit: str | None = None) -> Path:
    """The file the deploy would read: the local override when it exists."""
    if explicit:
        return Path(explicit) if Path(explicit).is_absolute() else ROOT / explicit
    local = ROOT / "config/project-settings.local.yaml"
    return local if local.exists() else ROOT / "config/project-settings.yaml"


def load_profile(env: str, path: Path) -> dict:
    doc = yaml.safe_load(path.read_text()) or {}
    target = ENV_TO_BUNDLE_TARGET[env]
    for profile in (doc.get("profiles") or {}).values():
        if profile.get("bundle_target") == target:
            return profile
    raise SystemExit(f"error: no profile with bundle_target {target!r} in {path}")


def state(cap: Capability, profile: dict, minimal: bool = False) -> dict:
    value = profile.get(cap.key)
    if cap.boolean:
        enabled = bool(value)
    else:
        enabled = not _is_placeholder(value)
    skipped = bool(minimal and cap.id in BILLING and enabled)
    if cap.always:
        # Its key holds an id the deploy captures, so it reads as a placeholder
        # right up until the deploy that creates it. That is not "off".
        enabled = True
    return {
        "id": cap.id,
        "title": cap.title,
        "key": cap.key,
        "value": "" if value is None else str(value),
        "enabled": enabled and not skipped,
        "configured": enabled,
        "skipped_by_minimal": skipped,
        "always": cap.always,
        "bills": cap.id in BILLING,
        "without": cap.without,
        "cost": cap.cost,
        "first_deploy": cap.first_deploy,
    }


def report(env: str, path: Path, minimal: bool = False) -> list[dict]:
    profile = load_profile(env, path)
    return [state(cap, profile, minimal) for cap in CAPABILITIES]


def render(rows: list[dict], env: str, path: Path, minimal: bool) -> str:
    out = [f"Capabilities for {env} (from {path.name}):", ""]
    for row in rows:
        if row["always"]:
            mark, tail = "on ", "always"
        elif row["skipped_by_minimal"]:
            mark, tail = "off", "configured, skipped by MINIMAL=1"
        elif row["enabled"]:
            mark, tail = "on ", row["key"]
        else:
            mark, tail = "off", f"set {row['key']} to turn on"
        out.append(f"  [{mark}] {row['title']}  ({tail})")
        if row["always"]:
            continue
        if row["enabled"]:
            out.append(f"          cost: {row['cost']}")
            if row["first_deploy"] not in ("none", ""):
                out.append(f"          first deploy: {row['first_deploy']}")
        else:
            out.append(f"          without it: {row['without']}")
    billing_on = [r for r in rows if r["bills"] and r["enabled"]]
    out.append("")
    if billing_on:
        out.append(
            "  Bills hourly once online: " + ", ".join(r["title"] for r in billing_on)
        )
        out.append("  MINIMAL=1 ./scripts/deploy.sh <env> deploys everything else.")
        out.append("  scripts/cleanup.sh removes them when you are done.")
    else:
        out.append("  Nothing here bills hourly in this configuration.")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Report the capability inventory.")
    ap.add_argument("env", choices=sorted(ENV_TO_BUNDLE_TARGET))
    ap.add_argument("--minimal", action="store_true", help="as MINIMAL=1 would deploy")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--settings", default=None)
    args = ap.parse_args(argv)

    path = settings_path(args.settings)
    rows = report(args.env, path, args.minimal)
    if args.as_json:
        print(json.dumps({"env": args.env, "minimal": args.minimal,
                          "settings": str(path), "capabilities": rows}, indent=2))
    else:
        print(render(rows, args.env, path, args.minimal))
    return 0


if __name__ == "__main__":
    sys.exit(main())
