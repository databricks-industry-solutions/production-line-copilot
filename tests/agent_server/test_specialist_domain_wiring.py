"""Each specialist must be wired to the governed domain it actually works in.

This is the failure the rest of the domain tests cannot catch: a specialist
handed the wrong domain still answers, just from the wrong governed schema, and
the answer reads as plausible. The mapping is asserted against built agents
rather than source text so a refactor of how tools are assembled cannot quietly
break it.

Agents build offline — endpoint resolution falls back to the preferred model
when the workspace is unreachable — so no network is required.
"""
from __future__ import annotations

import pytest

from agent_server.tools import governed_domain_query as gdq

# specialist factory -> the ONE governed domain it should reach
EXPECTED_DOMAIN = {
    "downtime_analyst": "asset",
    "maintenance_predictor": "asset",
    "quality_specialist": "quality",
    "supply_chain_advisor": "inventory",
    "oee_coach": "production",
    "schedule_optimizer": "production",
    "strategic_advisor": "production",
}

# Specialists that deliberately have no governed domain. Shift Briefing reads
# the live streaming space only — its job is the current shift, not the
# enterprise schema.
NO_DOMAIN = ["shift_briefing"]


def _factory(name: str):
    import importlib

    module = importlib.import_module(f"agent_server.specialists.{name}")
    return getattr(module, f"create_{name}")


def _tool_names(agent) -> list[str]:
    """Tool names bound to a built agent, across LangGraph layouts."""
    node = agent.nodes.get("tools")
    if node is not None:
        runnable = getattr(node, "bound", node)
        tools_by_name = getattr(runnable, "tools_by_name", None)
        if tools_by_name:
            return sorted(tools_by_name)
    pytest.skip("cannot introspect tools from this LangGraph version")


@pytest.fixture(autouse=True)
def all_domains_provisioned(monkeypatch):
    """Provision every domain, so a mis-wired specialist gets a tool, not silence."""
    for domain, env in gdq.DOMAIN_ENV.items():
        monkeypatch.setenv(env, f"space-{domain}")


@pytest.mark.parametrize("name,domain", sorted(EXPECTED_DOMAIN.items()))
def test_specialist_gets_exactly_its_own_domain_tool(name, domain):
    agent = _factory(name)()
    governed = [t for t in _tool_names(agent) if t.startswith("governed_")]
    assert governed == [f"governed_{domain}_query"], (
        f"{name} should reach only the {domain} domain, got {governed}"
    )


@pytest.mark.parametrize("name", NO_DOMAIN)
def test_streaming_only_specialists_get_no_domain_tool(name):
    agent = _factory(name)()
    assert [t for t in _tool_names(agent) if t.startswith("governed_")] == []


@pytest.mark.parametrize("name,domain", sorted(EXPECTED_DOMAIN.items()))
def test_specialist_keeps_sql_query_alongside_genie(name, domain):
    """Genie augments direct SQL; it must never replace it."""
    assert "sql_query" in _tool_names(_factory(name)())


@pytest.mark.parametrize("name", sorted(EXPECTED_DOMAIN))
def test_unprovisioned_domain_degrades_to_sql_only(monkeypatch, name):
    """A workspace without the spaces must still build every specialist."""
    for env in gdq.DOMAIN_ENV.values():
        monkeypatch.delenv(env, raising=False)
    names = _tool_names(_factory(name)())
    assert [t for t in names if t.startswith("governed_")] == []
    assert "sql_query" in names


@pytest.mark.parametrize("name,domain", sorted(EXPECTED_DOMAIN.items()))
def test_domain_tools_cover_every_domain_exactly_once_per_specialist(name, domain):
    """Guards against a specialist accumulating a second domain by copy-paste."""
    governed = [t for t in _tool_names(_factory(name)()) if t.startswith("governed_")]
    assert len(governed) == len(set(governed)) == 1


def test_every_domain_is_owned_by_at_least_one_specialist():
    """A provisioned space nobody queries is cost without benefit."""
    assert set(EXPECTED_DOMAIN.values()) == set(gdq.DOMAIN_ENV)
