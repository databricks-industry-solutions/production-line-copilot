"""The reader-facing docs must not describe code that no longer exists.

This repo is published as a solution accelerator, so README.md and
docs/ARCHITECTURE.md are the first things anyone reads and the last things
anyone updates. Two claims had drifted far enough to contradict both the code
and the project's own other documents:

  * ARCHITECTURE said all agents use `langgraph.prebuilt.create_react_agent`
    with a recursion limit of 10. The migration to `langchain.agents
    .create_agent` had been completed and recorded in docs/ai/planning, and the
    limit had been raised to 16 with a comment in agent.py explaining why 10
    was too low.
  * README said each specialist reaches "its own governed Genie space". Three
    specialists share `production`, two share `asset`, and shift_briefing has
    none — which ARCHITECTURE.md's own table already stated correctly.

These pin the specific claims rather than the prose around them: a doc should
be free to be rewritten, but not to reintroduce a statement the code refutes.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ARCHITECTURE = (REPO / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
README = (REPO / "README.md").read_text(encoding="utf-8")
SPECIALISTS = sorted((REPO / "agent_server" / "specialists").glob("*.py"))
AGENT = (REPO / "agent_server" / "agent.py").read_text(encoding="utf-8")


def _factories() -> list[Path]:
    return [p for p in SPECIALISTS if not p.name.startswith("_")]


class TestTheAgentFrameworkIsDescribedCorrectly:
    def test_every_specialist_really_does_use_create_agent(self):
        """If this fails the docs may be right and the assertion below wrong."""
        wrong = [p.name for p in _factories() if "from langchain.agents import create_agent" not in p.read_text()]
        assert not wrong, f"{wrong} no longer use langchain.agents.create_agent"

    def test_the_docs_do_not_name_the_retired_constructor(self):
        assert "create_react_agent" not in ARCHITECTURE, (
            "ARCHITECTURE.md names langgraph.prebuilt.create_react_agent; the "
            "specialists migrated to langchain.agents.create_agent"
        )

    def test_the_documented_recursion_limit_is_the_one_in_the_code(self):
        m = re.search(
            r'SPECIALIST_RECURSION_LIMIT = int\(os\.environ\.get\("SPECIALIST_RECURSION_LIMIT", "(\d+)"\)\)',
            AGENT,
        )
        assert m, "could not find SPECIALIST_RECURSION_LIMIT in agent.py"
        limit = m.group(1)
        claim = re.search(r"`recursion_limit` of (\d+)", ARCHITECTURE)
        assert claim, "ARCHITECTURE.md no longer states a recursion limit"
        assert claim.group(1) == limit, (
            f"ARCHITECTURE.md says {claim.group(1)}, the code defaults to {limit}"
        )


class TestTheGovernedDomainMappingIsDescribedCorrectly:
    """Genie spaces are per DOMAIN; specialists map onto them many-to-one."""

    def _domain_of(self, path: Path) -> str | None:
        m = re.search(r'governed_domain_tools\("(\w+)"\)', path.read_text())
        return m.group(1) if m else None

    def test_the_mapping_is_many_to_one_and_one_specialist_has_none(self):
        domains = [self._domain_of(p) for p in _factories()]
        assert domains.count("production") > 1, "production is no longer shared"
        assert None in domains, "every specialist now has a governed domain"

    def test_the_readme_does_not_claim_one_space_per_specialist(self):
        assert "Each specialist reaches its own governed Genie space" not in README, (
            "README claims per-specialist isolation; production is shared by "
            "three specialists, asset by two, and shift_briefing has none"
        )

    def test_the_readme_says_the_spaces_are_opt_in(self):
        # Absent by default in a fresh workspace; the reader should not have to
        # reach /ops/topology to discover that.
        assert "GENIE_SPACE_*" in README
