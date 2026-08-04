"""Per-domain Genie tools over the governed industry model (MVM).

The governed model organises its twelve entities into four schemas — ``asset``,
``quality``, ``inventory`` and ``production`` — and each one gets its own Genie
space, curated by ``scripts/genie_space_config_mvm.py --domain <name>``. A
specialist is handed only the space for the domain it works in.

Two reasons the split follows the model's schemas rather than our agent list:

  * **Accuracy.** Genie's answer quality falls off as the table count and the
    instruction block grow. A two-table Quality space with its own examples
    beats a twelve-table space that also has to explain inventory reorder rules.
  * **Governance.** The boundary is a Unity Catalog schema, so per-domain grants
    are a real permission story rather than a diagram. The Quality Specialist
    can be given the quality domain without the inventory domain.

These tools AUGMENT ``sql_query``; they do not replace it. Genie is poll-based
and nondeterministic, so it earns its place on open-ended questions while the
fixed analytical paths a specialist runs every shift stay on direct SQL. The
prompts say the same thing — see ``agent_server.prompts._shared``.

A domain whose space id is unset yields no tool at all, so a partially
provisioned workspace degrades to plain ``sql_query`` instead of offering the
model a tool that always errors.
"""

from __future__ import annotations

import logging
import os

from langchain_core.tools import BaseTool, StructuredTool

from agent_server.tools.genie_query import run_genie_query

logger = logging.getLogger(__name__)

# Domain -> the env var carrying its Genie space id. Set by render_app_yaml from
# the ``genie_domain_spaces`` block in config/project-settings.yaml.
DOMAIN_ENV: dict[str, str] = {
    "asset": "GENIE_SPACE_ASSET",
    "quality": "GENIE_SPACE_QUALITY",
    "inventory": "GENIE_SPACE_INVENTORY",
    "production": "GENIE_SPACE_PRODUCTION",
}

# What each space actually contains, so the tool description can tell the model
# where the boundary is. Structured as (table, gloss) pairs rather than prose:
# the System Map renders the bare table names as chips, and parsing them back
# out of a sentence broke the moment a gloss contained a comma.
# Keep in sync with the table list in scripts/genie_space_config_mvm.py.
DOMAIN_ENTITIES: dict[str, list[tuple[str, str]]] = {
    "asset": [
        ("equipment_register", "asset master"),
        ("location", "functional locations"),
        ("pm_schedule", "preventive maintenance due dates"),
        ("failure_record", "failure history with downtime minutes"),
    ],
    "quality": [
        ("inspection_result", "measurements against spec limits"),
        ("ncr", "non-conformance reports with defect codes and severity"),
    ],
    "inventory": [
        ("material_master", "material definitions"),
        ("stock_balance", "on-hand quantities, reorder points, stock status"),
    ],
    "production": [
        ("production_line", "governed OEE and line state"),
        ("work_center", "capacity per shift"),
        ("schedule", "planned runs"),
        ("run", "planned vs actual quantity"),
    ],
}


def domain_tables(domain: str) -> list[str]:
    """Bare table names in a domain, for callers that render them as data."""
    return [table for table, _ in DOMAIN_ENTITIES[domain]]


def _entity_prose(domain: str) -> str:
    return ", ".join(f"{table} ({gloss})" for table, gloss in DOMAIN_ENTITIES[domain])

# Incident-driven tables are empty on a healthy line. Saying so in the tool
# description stops a specialist reading "no rows" as "the tool is broken" and
# retrying, which is the failure mode we saw before the honesty gates went in.
_EMPTY_AT_STEADY_STATE: dict[str, str] = {
    "asset": " failure_record is empty at steady state and fills during incidents.",
    "quality": (
        " Both tables are empty at steady state and fill during quality "
        "incidents; zero rows is a valid, meaningful answer."
    ),
}


def domain_space_id(domain: str) -> str:
    """Resolve a domain's Genie space id from the environment.

    Read at call time rather than import time so a test or a redeploy can change
    the wiring without reimporting the module.
    """
    env = DOMAIN_ENV.get(domain)
    if not env:
        return ""
    return os.environ.get(env, "").strip()


def configured_domains() -> list[str]:
    """Domains that actually have a space provisioned, in declaration order."""
    return [d for d in DOMAIN_ENV if domain_space_id(d)]


def _description(domain: str) -> str:
    return (
        f"Ask a natural language question against the GOVERNED {domain} domain of "
        f"the manufacturing industry data model. Contains: {_entity_prose(domain)}."
        f"{_EMPTY_AT_STEADY_STATE.get(domain, '')}\n\n"
        "Use this for standards-aligned, enterprise-schema questions — governed "
        "master data, cross-entity joins within the domain, and audit-style "
        "questions. For live per-second line telemetry, or for a lookup where you "
        "already know the exact SQL, use sql_query instead: it is faster and "
        "deterministic.\n\n"
        "Args:\n"
        "    question: A clear natural language question about the "
        f"{domain} domain.\n\n"
        "Returns:\n"
        "    A dict with 'answer' (text summary), optionally 'sql' (the generated "
        "query), 'tables' (referenced tables) and 'data' (result rows, max 20)."
    )


def governed_domain_tool(domain: str) -> BaseTool | None:
    """Build the Genie tool for one governed domain, or None if unprovisioned."""
    if domain not in DOMAIN_ENV:
        raise ValueError(
            f"unknown governed domain {domain!r}; expected one of {', '.join(DOMAIN_ENV)}"
        )

    space = domain_space_id(domain)
    if not space:
        logger.info(
            "governed %s domain space not configured (%s unset) — specialist "
            "falls back to sql_query",
            domain,
            DOMAIN_ENV[domain],
        )
        return None

    def _run(question: str) -> dict:
        return run_genie_query(question, space_id=space)

    return StructuredTool.from_function(
        func=_run,
        name=f"governed_{domain}_query",
        description=_description(domain),
    )


def governed_domain_tools(*domains: str) -> list[BaseTool]:
    """Tools for the given domains, skipping any that are unprovisioned."""
    built = (governed_domain_tool(d) for d in domains)
    return [t for t in built if t is not None]


def governed_domain_prompt(domain: str) -> str:
    """Prompt fragment telling a specialist about its own governed domain space.

    Empty when the domain has no space provisioned — the tool would not exist,
    and a prompt describing a tool the agent cannot call wastes context and
    invites hallucinated calls. Built at agent-construction time rather than as
    an import-time constant so it always matches the tool list actually handed
    to the model.
    """
    if not domain_space_id(domain):
        return ""
    return (
        f"\n\nYOUR GOVERNED DOMAIN SPACE (governed_{domain}_query):\n"
        f"- You have a dedicated Genie space over the governed {domain} domain: "
        f"{_entity_prose(domain)}.\n"
        "- Prefer it for open-ended or exploratory questions about that domain, "
        "and for governed/enterprise phrasing (equipment register, PM schedule, "
        "NCR, stock balance, plan-vs-actual).\n"
        "- Prefer sql_query for the live streaming tables, for anything "
        "time-critical, and whenever you already know the exact query — it is "
        "faster and deterministic. Genie is a round trip.\n"
        "- The space covers ONLY your domain. Do not ask it about other domains; "
        "use sql_query with fully qualified names for cross-domain questions."
    )
