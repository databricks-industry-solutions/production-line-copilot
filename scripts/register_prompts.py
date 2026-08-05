#!/usr/bin/env python3
"""Register this app's prompts in the MLflow Prompt Registry (Unity Catalog).

What this buys: a prompt change becomes a diffable version with an author and a
commit message, instead of a line in a Python file that only shows up in a code
review. Traces and eval runs can then be read against "which wording was live",
which is the question you actually want to answer when answer quality moves.

The app does NOT read prompts back from the registry — the constants in
``agent_server/prompts/`` remain the source of truth at runtime, and this script
publishes them. Registry-first loading is a deliberate follow-up (see the
Backlog in ``docs/ROADMAP.md``); until it lands, treat the registry as a record,
not a control surface. Editing a prompt version in the UI changes nothing that
runs.

Idempotency is the whole game here, because ``scripts/deploy.sh`` runs this on
every deploy. Registering unconditionally would mint eleven identical versions
per deploy and bury the ones where the wording actually changed. So each prompt
is compared against the latest registered version and only re-registered when
the text differs. The alias is re-pointed every run regardless, which is what
makes the step self-healing on an environment that was restored or partly set
up.

Usage:
  python3 scripts/register_prompts.py --catalog <cat> --schema <schema>
  python3 scripts/register_prompts.py            # reads UC_CATALOG / UC_SCHEMA
  python3 scripts/register_prompts.py --dry-run  # print the plan, write nothing
  python3 scripts/register_prompts.py --alias staging

Requires ``CREATE FUNCTION`` on the target schema (a UC prompt is stored as a
schema-scoped object) plus the usual ``USE CATALOG`` / ``USE SCHEMA``.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent_server.prompts.catalog import CATALOG, PromptSpec, uc_name  # noqa: E402

# A UC prompt version is addressed the same way a UC model version is.
UC_REGISTRY_URI = "databricks-uc"

# Which version the app would load once registry-first loading lands. Pointing
# it every run means an environment can be brought to a known state by re-running
# this script rather than by clicking through the UI.
DEFAULT_ALIAS = "production"


def _tags(spec: PromptSpec) -> dict[str, str]:
    """Registry tags for ``spec``.

    ``governed_domain`` is the load-bearing one: it records that the running
    system prompt is this text plus ``governed_domain_prompt(<domain>)``, which
    is built per deployment and cannot be pinned to a version. Without the tag,
    a reader comparing the registered prompt against a trace would find text in
    the trace that appears nowhere in the registry and reasonably conclude the
    registry was stale.
    """
    tags = {"source": "agent_server.prompts", "slug": spec.slug}
    if spec.role:
        tags["role"] = spec.role
    if spec.purpose:
        tags["purpose"] = spec.purpose
    if spec.governed_domain:
        tags["governed_domain"] = spec.governed_domain
        tags["runtime_suffix"] = f"governed_domain_prompt({spec.governed_domain!r})"
    return tags


def _is_missing(exc: Exception) -> bool:
    """Whether ``exc`` means "no such prompt" rather than a real failure.

    Matched on the message because the REST error code arrives inside a generic
    ``RestException``, and importing the Databricks-specific exception type
    here would make this script depend on which backend it is talking to.
    """
    text = str(exc).upper()
    return "NOT_FOUND" in text or "DOES NOT EXIST" in text


def _latest(client, name: str):
    """The newest registered version of ``name``, or None if never registered.

    Uses ``search_prompt_versions`` rather than the ``@latest`` URI the
    ``load_prompt`` docstring advertises: against Unity Catalog that URI
    resolves to None even for a prompt with versions, so a guard built on it
    concludes "never registered" every time and re-registers unchanged text on
    every deploy. That is not hypothetical — it is what this script did on its
    first two runs.

    Takes the maximum version rather than the first result, so the answer does
    not depend on the server's ordering.

    A lookup failure degrades to "unknown", which re-registers. Publishing a
    redundant version is a worse outcome than skipping, but a far better one
    than aborting a deploy over a read.
    """
    try:
        versions = list(client.search_prompt_versions(name) or [])
    except Exception as exc:  # noqa: BLE001 - any lookup failure means "unknown"
        # A prompt that has never been registered raises NOT_FOUND rather than
        # returning empty. That is the expected first run against a fresh
        # schema, so it stays quiet; anything else is worth seeing in the
        # deploy log even though both paths go on to register.
        if not _is_missing(exc):
            print(f"  ! could not read existing versions ({type(exc).__name__}: {exc})")
        return None
    if not versions:
        return None
    return max(versions, key=lambda v: int(v.version))


def register_all(
    catalog: str,
    schema: str,
    alias: str = DEFAULT_ALIAS,
    commit_message: str = "",
    dry_run: bool = False,
) -> int:
    """Publish every catalogued prompt. Returns the number of new versions."""
    import mlflow
    import mlflow.genai as genai
    from mlflow import MlflowClient

    mlflow.set_registry_uri(UC_REGISTRY_URI)
    client = MlflowClient()

    created = 0
    for spec in CATALOG:
        name = uc_name(spec, catalog, schema)
        template = spec.registry_template
        print(f"- {name}")

        existing = None if dry_run else _latest(client, name)
        if existing is not None and existing.template == template:
            print(f"  unchanged, still v{existing.version}")
            if not dry_run:
                genai.set_prompt_alias(name=name, alias=alias, version=existing.version)
            continue

        if dry_run:
            state = "would register v1 (new)" if existing is None else "would register a new version"
            print(f"  {state}; {len(template)} chars, variables={list(spec.variables)}")
            continue

        version = genai.register_prompt(
            name=name,
            template=template,
            commit_message=commit_message or f"Sync {spec.slug} from agent_server.prompts",
            tags=_tags(spec),
        )
        genai.set_prompt_alias(name=name, alias=alias, version=version.version)
        created += 1
        print(f"  registered v{version.version} -> @{alias}")

    return created


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--catalog", default=os.environ.get("UC_CATALOG", ""))
    ap.add_argument("--schema", default=os.environ.get("UC_SCHEMA", ""))
    ap.add_argument("--alias", default=DEFAULT_ALIAS)
    ap.add_argument("--commit-message", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.dry_run and (not args.catalog or not args.schema):
        ap.error(
            "catalog and schema are required — pass --catalog/--schema or set "
            "UC_CATALOG/UC_SCHEMA"
        )

    catalog = args.catalog or "<catalog>"
    schema = args.schema or "<schema>"
    print(
        f"{'Planning' if args.dry_run else 'Registering'} {len(CATALOG)} prompts "
        f"in {catalog}.{schema} (alias @{args.alias})"
    )

    try:
        created = register_all(
            catalog=catalog,
            schema=schema,
            alias=args.alias,
            commit_message=args.commit_message,
            dry_run=args.dry_run,
        )
    except Exception as exc:  # noqa: BLE001 - report cleanly, deploy decides fatality
        print(f"ERROR: prompt registration failed: {type(exc).__name__}: {exc}")
        return 1

    if args.dry_run:
        print("Dry run — nothing written.")
    else:
        print(f"Done. {created} new version(s); {len(CATALOG) - created} unchanged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
