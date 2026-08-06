"""Every prompt this app sends to a model, as one enumerable list.

The prompt text lives in the sibling modules, where it is readable and
reviewable next to the specialist that uses it. This module adds the one thing
those modules cannot express: the *set*. Registration (and, later, loading
from the MLflow Prompt Registry) needs to iterate the prompts, and a set
reconstructed by hand in a script drifts the first time someone adds a
specialist. ``test_prompt_catalog.py`` fails when a prompt module exports a
constant that is missing here, so the drift is caught rather than discovered.

Two things about the text are worth knowing before you compare a registered
prompt against what the app actually ran:

**Brace syntax.** The registry renders templates with Jinja, so a variable is
``{{name}}``. Our runtime uses ``str.format``, so a variable is ``{name}``.
``registry_template`` converts between them, but only for the variables a spec
declares — a blanket regex would silently promote the literal ``{"shift_id":
...}`` JSON examples in three of the system prompts into template variables.

**The governed-domain suffix.** A specialist's runtime system prompt is its
constant plus ``governed_domain_prompt(domain)``, built at agent construction
and empty when that domain has no Genie space provisioned. So no single
registered version equals what a governed specialist receives. The suffix is
registered on its own (``governed_domain_suffix``) and each governed spec names
its domain in ``governed_domain``, which becomes a tag: between the two, every
character the model sees has a version behind it, and a reader reconciling a
trace against the registry can assemble the whole prompt rather than finding
text that appears nowhere.
"""
from __future__ import annotations

from dataclasses import dataclass
from string import Formatter

from agent_server.prompts.downtime_analyst import DOWNTIME_ANALYST_SYSTEM
from agent_server.prompts.general import GENERAL_SYSTEM_TEMPLATE
from agent_server.prompts.governed_domain import GOVERNED_DOMAIN_SUFFIX_TEMPLATE
from agent_server.prompts.maintenance_predictor import MAINTENANCE_PREDICTOR_SYSTEM
from agent_server.prompts.oee_coach import OEE_COACH_SYSTEM
from agent_server.prompts.orchestrator import (
    INTENT_CLASSIFY_TEMPLATE,
    ORCHESTRATOR_SYSTEM,
)
from agent_server.prompts.quality_specialist import QUALITY_SPECIALIST_SYSTEM
from agent_server.prompts.response_synthesis import SYNTHESIS_TEMPLATE
from agent_server.prompts.schedule_optimizer import SCHEDULE_OPTIMIZER_SYSTEM
from agent_server.prompts.shift_briefing import SHIFT_BRIEFING_SYSTEM
from agent_server.prompts.strategic_advisor import STRATEGIC_ADVISOR_SYSTEM
from agent_server.prompts.supply_chain_advisor import SUPPLY_CHAIN_SYSTEM

# Registry names are prefixed because a Unity Catalog schema is one namespace:
# prompts sit alongside this app's tables, and an unprefixed `oee_coach` would
# be indistinguishable from a table of the same name in a listing.
NAME_PREFIX = "prompt_"


@dataclass(frozen=True)
class PromptSpec:
    """One registrable prompt.

    ``text`` is exactly what the app sends today, in ``str.format`` syntax.
    ``variables`` is empty for a static system prompt and, for a template, is
    every name the call site fills — asserted against the text by the tests, so
    a variable added to one and not the other fails rather than registering a
    template nobody can render.

    An empty ``variables`` means more than "no variables": it means the text is
    never passed through ``str.format`` at all. Three of the system prompts
    carry literal JSON examples like ``{"shift_id": "shift_1"}``, which
    ``str.format`` reads as a field named ``"shift_id"`` and raises on. Jinja,
    which the registry uses, treats a single brace as literal, so they register
    and render correctly — but do not reach for ``.format`` on a static prompt.
    """

    slug: str
    text: str
    variables: tuple[str, ...] = ()
    role: str | None = None
    governed_domain: str | None = None
    purpose: str = ""

    @property
    def name(self) -> str:
        """Unqualified registry name. Prepend ``catalog.schema.`` for UC."""
        return f"{NAME_PREFIX}{self.slug}"

    @property
    def registry_template(self) -> str:
        """``text`` in the registry's ``{{variable}}`` syntax.

        Substitutes only declared variables, so literal braces survive intact.
        """
        out = self.text
        for var in self.variables:
            out = out.replace("{" + var + "}", "{{" + var + "}}")
        return out

    def runtime_text(self, template: str) -> str:
        """``template`` from the registry, back in ``str.format`` syntax.

        The inverse of ``registry_template``, and not optional: ``str.format``
        reads ``{{query}}`` as the literal text ``{query}``, so a registered
        template used as-is would render a prompt whose variables are visibly
        unfilled rather than raising.
        """
        out = template
        for var in self.variables:
            out = out.replace("{{" + var + "}}", "{" + var + "}")
        return out

    @property
    def format_variables(self) -> tuple[str, ...]:
        """The field names ``str.format`` would look for in ``text``.

        Meaningful for templates only, where the tests compare it against
        ``variables`` so a variable added to the text but not the call site
        (or the reverse) fails. On a static system prompt it reports the
        literal JSON braces described in ``variables`` above, which is the
        parser being right about text that is never formatted.
        """
        names = {
            name
            for _, name, _, _ in Formatter().parse(self.text)
            if name
        }
        return tuple(sorted(names))


CATALOG: tuple[PromptSpec, ...] = (
    PromptSpec(
        slug="orchestrator_system",
        text=ORCHESTRATOR_SYSTEM,
        role="intent_router",
        purpose="System prompt for the router that picks a specialist.",
    ),
    PromptSpec(
        slug="intent_classify",
        text=INTENT_CLASSIFY_TEMPLATE,
        variables=(
            "active_events_summary",
            "line_state_summary",
            "query",
            "scenario_name",
            "shift_id",
            "timestamp",
        ),
        role="intent_router",
        purpose="User turn handed to the router, carrying the live line state.",
    ),
    PromptSpec(
        slug="response_synthesis",
        text=SYNTHESIS_TEMPLATE,
        variables=(
            "active_events_count",
            "actual_cases",
            "current_oee",
            "planned_cases",
            "scenario_name",
            "shift_id",
            "specialist",
            "specialist_response",
        ),
        role="general",
        purpose="Reformats a specialist draft into the UI's section contract.",
    ),
    PromptSpec(
        slug="downtime_analyst_system",
        text=DOWNTIME_ANALYST_SYSTEM,
        role="downtime_analyst",
        governed_domain="asset",
        purpose="Stoppage root cause and recovery.",
    ),
    PromptSpec(
        slug="quality_specialist_system",
        text=QUALITY_SPECIALIST_SYSTEM,
        role="quality_specialist",
        governed_domain="quality",
        purpose="Defect and spec-limit analysis.",
    ),
    PromptSpec(
        slug="supply_chain_advisor_system",
        text=SUPPLY_CHAIN_SYSTEM,
        role="supply_chain_advisor",
        governed_domain="inventory",
        purpose="Material availability and depletion risk.",
    ),
    PromptSpec(
        slug="oee_coach_system",
        text=OEE_COACH_SYSTEM,
        role="oee_coach",
        governed_domain="production",
        purpose="A x P x Q breakdown and loss Pareto.",
    ),
    PromptSpec(
        slug="schedule_optimizer_system",
        text=SCHEDULE_OPTIMIZER_SYSTEM,
        role="schedule_optimizer",
        governed_domain="production",
        purpose="Sequencing and changeover trade-offs.",
    ),
    PromptSpec(
        slug="maintenance_predictor_system",
        text=MAINTENANCE_PREDICTOR_SYSTEM,
        role="maintenance_predictor",
        governed_domain="asset",
        purpose="Failure risk from vibration and PM history.",
    ),
    PromptSpec(
        slug="strategic_advisor_system",
        text=STRATEGIC_ADVISOR_SYSTEM,
        role="strategic_advisor",
        governed_domain="production",
        purpose="Cross-shift and cross-line strategy.",
    ),
    PromptSpec(
        slug="shift_briefing_system",
        text=SHIFT_BRIEFING_SYSTEM,
        role="shift_briefing",
        purpose="Pre- and post-shift handover briefings.",
    ),
    PromptSpec(
        slug="general_system",
        text=GENERAL_SYSTEM_TEMPLATE,
        variables=("context",),
        role="general",
        purpose="No-tool fallback used when the router picks no specialist.",
    ),
    PromptSpec(
        slug="governed_domain_suffix",
        text=GOVERNED_DOMAIN_SUFFIX_TEMPLATE,
        variables=("domain", "entities"),
        purpose=(
            "Appended to a governed specialist's system prompt at construction. "
            "Registered on its own because every governed specialist shares it."
        ),
    ),
)

BY_SLUG: dict[str, PromptSpec] = {spec.slug: spec for spec in CATALOG}


def uc_name(spec: PromptSpec, catalog: str, schema: str) -> str:
    """Fully qualified Unity Catalog name for ``spec``."""
    return f"{catalog}.{schema}.{spec.name}"
