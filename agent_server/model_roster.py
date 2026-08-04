"""Which model answers which question — and what to run when it isn't there.

Every specialist used to share one of two OpenAI endpoints, a mini tier and a
full tier. That works, but it wastes the thing Databricks is actually good at:
one governance plane, one billing surface and one API in front of every major
model vendor. A line manager asking "why did the filler stop?" and one asking
"what should we prioritise next quarter?" are not the same workload, and they
should not be answered by the same model just because it was convenient.

So each role below names a preferred model chosen for the shape of its work.
The picks are grounded in what these endpoints actually do inside *this* agent,
not in leaderboard position — several plausible candidates were rejected on
evidence:

  * Gemini 3.x needed a repair before it could hold a specialist seat. It calls
    a tool correctly, but the follow-up turn was rejected with "Function call is
    missing a thought_signature in functionCall parts" — not a model limit, but
    the Databricks LangChain integration rebuilding tool calls from a fixed
    four-field shape and dropping the signature Google requires back.
    ``agent_server/vendor_compat.py`` carries the field across, after which both
    Gemini endpoints complete multi-tool ReAct loops cleanly. They are eligible
    here; ``model_roles`` in project-settings will put one in any seat.
  * GPT-5.5 and the GPT-5.6 family refuse function tools on the
    chat-completions API unless reasoning effort is switched off, which throws
    away the reason to pick them; GPT-5.5-pro is Responses-API only. GPT-5.4 is
    therefore the strongest usable OpenAI model here, not the newest one.

RESOLUTION ORDER, per role:

  1. ``MODEL_<ROLE>`` env var (e.g. ``MODEL_DOWNTIME_ANALYST``) — an explicit
     operator override, used verbatim and never second-guessed.
  2. The preferred endpoint below, when the workspace actually serves it.
  3. The role's tier default (``MODEL_SPECIALIST`` / ``MODEL_ADVANCED``).

Step 3 is what keeps this shippable. The roster spans six vendors, and no
customer workspace is guaranteed to serve all of them — availability varies by
region, and some models need cross-geography routing enabled. Rather than 404
on first deploy, a missing model quietly degrades that one role to the tier
default and logs which substitution it made. Setting ``MODEL_MULTI_VENDOR=0``
turns the whole roster off and restores the old two-tier behaviour.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Tier fallbacks. These are the endpoints the accelerator shipped with before
# the roster existed, and they remain the safest thing to land on: OpenAI
# models are served in more workspaces than any other vendor here.
SPECIALIST_TIER_DEFAULT = "databricks-gpt-5-mini"
ADVANCED_TIER_DEFAULT = "databricks-gpt-5"


@dataclass(frozen=True)
class Assignment:
    """A role's preferred model and why it was chosen."""

    role: str
    endpoint: str
    vendor: str
    display: str
    tier: str  # "specialist" | "advanced" — which default to fall back to
    rationale: str


ROSTER: tuple[Assignment, ...] = (
    Assignment(
        role="downtime_analyst",
        endpoint="databricks-claude-sonnet-5",
        vendor="Anthropic",
        display="Claude Sonnet 5",
        tier="specialist",
        rationale=(
            "Root-cause work chains five tools and has to hold a causal thread "
            "across them; Sonnet's tool use is the most reliable we measured, at "
            "roughly 40% of Opus cost."
        ),
    ),
    Assignment(
        role="quality_specialist",
        endpoint="databricks-glm-5-2",
        vendor="Zhipu AI",
        display="GLM-5.2",
        tier="specialist",
        rationale=(
            "Bayesian contamination risk and SPC reasoning over six tools. Held "
            "structure on every repeat run and grounds its arithmetic in tool "
            "output rather than restating the prompt."
        ),
    ),
    Assignment(
        role="supply_chain_advisor",
        endpoint="databricks-llama-4-maverick",
        vendor="Meta",
        display="Llama 4 Maverick",
        tier="specialist",
        rationale=(
            "Depletion and reorder timing is arithmetic against a plan, not open "
            "reasoning. The fastest model that answered correctly, at a third of "
            "the input cost of the proprietary tiers."
        ),
    ),
    Assignment(
        role="oee_coach",
        endpoint="databricks-gpt-5-4",
        vendor="OpenAI",
        display="GPT-5.4",
        tier="advanced",
        rationale=(
            "Availability x Performance x Quality decomposition over six tools, "
            "where a dropped factor silently corrupts the answer. The strongest "
            "OpenAI model that still supports function tools on this API."
        ),
    ),
    Assignment(
        role="schedule_optimizer",
        endpoint="databricks-gpt-oss-120b",
        vendor="OpenAI (open weights)",
        display="GPT-OSS 120B",
        tier="specialist",
        rationale=(
            "The MILP and Monte-Carlo tools do the optimising; the model narrates "
            "their output. That is the cheapest capable slot in the roster, and it "
            "puts an open-weights model on the same governance plane as the rest."
        ),
    ),
    Assignment(
        role="maintenance_predictor",
        endpoint="databricks-qwen35-122b-a10b",
        vendor="Alibaba",
        display="Qwen 3.5 122B",
        tier="specialist",
        rationale=(
            "Remaining-useful-life trending is regression reasoning over sensor "
            "history — a long-context, low-creativity task Qwen handles at a "
            "tenth of the input cost of the advanced tier."
        ),
    ),
    Assignment(
        role="strategic_advisor",
        endpoint="databricks-claude-opus-5",
        vendor="Anthropic",
        display="Claude Opus 5",
        tier="advanced",
        rationale=(
            "The only role that argues rather than reports: multi-shift trade-offs "
            "and capex framing in 3000-token answers. Lowest call volume in the "
            "roster, which is what makes flagship pricing defensible here."
        ),
    ),
    Assignment(
        role="shift_briefing",
        endpoint="databricks-claude-haiku-4-5",
        vendor="Anthropic",
        display="Claude Haiku 4.5",
        tier="specialist",
        rationale=(
            "Largest prompt in the app and read at every shift change, so tone and "
            "format discipline matter more than depth. Haiku held the briefing "
            "structure on every run at specialist-tier latency."
        ),
    ),
    Assignment(
        role="general",
        endpoint="databricks-gpt-5-mini",
        vendor="OpenAI",
        display="GPT-5 mini",
        tier="specialist",
        rationale=(
            "Answers the unrouted questions and reformats every specialist reply "
            "into the card structure the UI parses. Highest call volume in the "
            "app, and its output is read verbatim, so it stays on a known-good "
            "prose model."
        ),
    ),
    Assignment(
        role="intent_router",
        endpoint="databricks-gemma-3-12b",
        vendor="Google",
        display="Gemma 3 12B",
        tier="specialist",
        rationale=(
            "Picks one label from nine and nothing else — every request pays this "
            "latency before any work starts. Scored 9/9 on the routing set at the "
            "cheapest rate on the platform."
        ),
    ),
)

_BY_ROLE: dict[str, Assignment] = {a.role: a for a in ROSTER}

# Display metadata for endpoints that can appear via tier fallback or an
# operator override, so the UI can still name a vendor for them.
_EXTRA_CATALOG: dict[str, tuple[str, str]] = {
    "databricks-gpt-5": ("GPT-5", "OpenAI"),
    "databricks-gpt-5-nano": ("GPT-5 nano", "OpenAI"),
    "databricks-gpt-5-4-mini": ("GPT-5.4 mini", "OpenAI"),
    "databricks-claude-opus-4-5": ("Claude Opus 4.5", "Anthropic"),
    "databricks-claude-sonnet-4-5": ("Claude Sonnet 4.5", "Anthropic"),
    "databricks-meta-llama-3-3-70b-instruct": ("Llama 3.3 70B", "Meta"),
    "databricks-qwen3-next-80b-a3b-instruct": ("Qwen 3 Next 80B", "Alibaba"),
    "databricks-gemini-3-5-flash": ("Gemini 3.5 Flash", "Google"),
    "databricks-gemini-3-1-flash-lite": ("Gemini 3.1 Flash Lite", "Google"),
}

# Substring -> vendor, for endpoints nobody has catalogued. Ordered because
# "gpt-oss" must be tested before the bare "gpt".
_VENDOR_HINTS: tuple[tuple[str, str], ...] = (
    ("claude", "Anthropic"),
    ("gpt-oss", "OpenAI (open weights)"),
    ("gpt", "OpenAI"),
    ("gemma", "Google"),
    ("gemini", "Google"),
    ("llama", "Meta"),
    ("qwen", "Alibaba"),
    ("glm", "Zhipu AI"),
    ("mistral", "Mistral AI"),
    ("mixtral", "Mistral AI"),
    ("dbrx", "Databricks"),
)

_UNSET = object()
_available: object = _UNSET
_lock = threading.Lock()


# Both orchestrator roles used to share one endpoint named by MODEL_ORCHESTRATOR.
# Deployments that pinned it keep working: it still wins over the roster for
# these two roles. It is no longer rendered by default, so a fresh install gets
# the roster picks instead.
_LEGACY_ORCHESTRATOR_ROLES = frozenset({"general", "intent_router"})


def _env_override(role: str) -> str:
    explicit = os.environ.get(f"MODEL_{role.upper()}", "").strip()
    if explicit:
        return explicit
    if role in _LEGACY_ORCHESTRATOR_ROLES:
        return os.environ.get("MODEL_ORCHESTRATOR", "").strip()
    return ""


def _tier_endpoint(tier: str) -> str:
    if tier == "advanced":
        return os.environ.get("MODEL_ADVANCED", ADVANCED_TIER_DEFAULT).strip() or ADVANCED_TIER_DEFAULT
    return os.environ.get("MODEL_SPECIALIST", SPECIALIST_TIER_DEFAULT).strip() or SPECIALIST_TIER_DEFAULT


def multi_vendor_enabled() -> bool:
    """False when ``MODEL_MULTI_VENDOR`` is set to a falsey value."""
    return os.environ.get("MODEL_MULTI_VENDOR", "1").strip().lower() not in {"0", "false", "no", "off"}


def available_endpoints() -> frozenset[str] | None:
    """Serving endpoint names in this workspace, or None if we couldn't look.

    Cached for the process: the roster is resolved once, when the specialists
    are first built. None is deliberately distinct from an empty set — "we could
    not check" must not silently downgrade every role to the tier default, so
    callers treat None as "assume the preferred model is there".
    """
    global _available
    if _available is not _UNSET:
        return _available  # type: ignore[return-value]
    with _lock:
        if _available is not _UNSET:
            return _available  # type: ignore[return-value]
        try:
            from databricks.sdk import WorkspaceClient

            names = {e.name for e in WorkspaceClient().serving_endpoints.list() if e.name}
            _available = frozenset(names)
            logger.info("Model roster: workspace serves %d endpoints", len(names))
        except Exception as exc:  # noqa: BLE001 - never block startup on this
            logger.warning(
                "Model roster: could not list serving endpoints (%s: %s); "
                "assuming preferred models are available",
                type(exc).__name__,
                exc,
            )
            _available = None
    return _available  # type: ignore[return-value]


def reset_cache() -> None:
    """Forget the cached endpoint listing. For tests."""
    global _available
    with _lock:
        _available = _UNSET


def resolve(role: str) -> str:
    """Return the serving endpoint that should answer for ``role``.

    Unknown roles fall back to the specialist tier rather than raising: a new
    specialist that nobody added to the roster should still get a model.
    """
    override = _env_override(role)
    if override:
        return override

    assignment = _BY_ROLE.get(role)
    if assignment is None:
        logger.warning("Model roster: no assignment for role %r, using specialist tier", role)
        return _tier_endpoint("specialist")

    if not multi_vendor_enabled():
        return _tier_endpoint(assignment.tier)

    serving = available_endpoints()
    if serving is not None and assignment.endpoint not in serving:
        substitute = _tier_endpoint(assignment.tier)
        logger.warning(
            "Model roster: %s is not served in this workspace; %s falls back to %s",
            assignment.endpoint,
            role,
            substitute,
        )
        return substitute

    return assignment.endpoint


def describe(endpoint: str) -> tuple[str, str]:
    """Return ``(display_name, vendor)`` for a serving endpoint name."""
    if not endpoint:
        return ("unknown", "unknown")
    for assignment in ROSTER:
        if assignment.endpoint == endpoint:
            return (assignment.display, assignment.vendor)
    if endpoint in _EXTRA_CATALOG:
        return _EXTRA_CATALOG[endpoint]
    lowered = endpoint.lower()
    for hint, vendor in _VENDOR_HINTS:
        if hint in lowered:
            return (endpoint.removeprefix("databricks-"), vendor)
    return (endpoint.removeprefix("databricks-"), "unknown")


def resolved_roster() -> list[dict[str, object]]:
    """The roster as actually resolved here — what the About tab renders.

    Reports the preferred pick alongside what is really running, so a workspace
    that substituted a model shows that plainly instead of advertising a vendor
    it never called.
    """
    view: list[dict[str, object]] = []
    for assignment in ROSTER:
        endpoint = resolve(assignment.role)
        display, vendor = describe(endpoint)
        view.append(
            {
                "role": assignment.role,
                "endpoint": endpoint,
                "display": display,
                "vendor": vendor,
                "rationale": assignment.rationale,
                "preferred_endpoint": assignment.endpoint,
                "preferred_display": assignment.display,
                "preferred_vendor": assignment.vendor,
                "substituted": endpoint != assignment.endpoint,
            }
        )
    return view


def vendor_summary() -> list[str]:
    """Distinct vendors actually answering here, in roster order."""
    seen: list[str] = []
    for entry in resolved_roster():
        vendor = str(entry["vendor"])
        if vendor not in seen and vendor != "unknown":
            seen.append(vendor)
    return seen
