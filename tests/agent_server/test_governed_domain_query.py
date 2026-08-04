"""Tests for the per-domain governed Genie tools.

Two behaviours carry real risk and are pinned here:

  * A specialist must reach its OWN domain space and no other. Getting this
    wrong is silent — the tool still answers, just from the wrong governed
    schema, and the answer looks plausible.
  * An unprovisioned domain must yield no tool at all. Handing the model a tool
    that always errors burns a turn per call and pollutes the transcript; the
    specialist should simply fall back to sql_query.
"""
from __future__ import annotations

import pytest

from agent_server.tools import governed_domain_query as gdq

DOMAINS = ["asset", "quality", "inventory", "production"]


@pytest.fixture(autouse=True)
def clear_domain_env(monkeypatch):
    """Start every test from a workspace with no domain spaces provisioned."""
    for env in gdq.DOMAIN_ENV.values():
        monkeypatch.delenv(env, raising=False)


def test_unprovisioned_domain_yields_no_tool():
    assert gdq.governed_domain_tool("quality") is None
    assert gdq.governed_domain_tools("quality", "asset") == []
    assert gdq.configured_domains() == []


def test_unknown_domain_is_a_programming_error():
    with pytest.raises(ValueError, match="unknown governed domain"):
        gdq.governed_domain_tool("logistics")


@pytest.mark.parametrize("domain", DOMAINS)
def test_provisioned_domain_builds_a_named_tool(monkeypatch, domain):
    monkeypatch.setenv(gdq.DOMAIN_ENV[domain], f"space-{domain}")
    tool = gdq.governed_domain_tool(domain)
    assert tool is not None
    assert tool.name == f"governed_{domain}_query"
    assert list(tool.args) == ["question"]


@pytest.mark.parametrize("domain", DOMAINS)
def test_tool_queries_its_own_space(monkeypatch, domain):
    """The whole point of the split: each tool hits exactly one space id."""
    for d in DOMAINS:
        monkeypatch.setenv(gdq.DOMAIN_ENV[d], f"space-{d}")

    seen: dict = {}

    def fake_run(question, space_id=None):
        seen["question"] = question
        seen["space_id"] = space_id
        return {"answer": "ok"}

    monkeypatch.setattr(gdq, "run_genie_query", fake_run)
    tool = gdq.governed_domain_tool(domain)
    assert tool.invoke({"question": "how many rows are there?"}) == {"answer": "ok"}
    assert seen["space_id"] == f"space-{domain}"


def test_only_provisioned_domains_are_reported(monkeypatch):
    monkeypatch.setenv(gdq.DOMAIN_ENV["asset"], "space-asset")
    monkeypatch.setenv(gdq.DOMAIN_ENV["production"], "   ")  # whitespace is not a space id
    assert gdq.configured_domains() == ["asset"]
    assert [t.name for t in gdq.governed_domain_tools(*DOMAINS)] == ["governed_asset_query"]


@pytest.mark.parametrize("domain", DOMAINS)
def test_tool_description_names_the_domain_entities(monkeypatch, domain):
    """The model picks tools off the description, so it must say what is inside."""
    monkeypatch.setenv(gdq.DOMAIN_ENV[domain], f"space-{domain}")
    description = gdq.governed_domain_tool(domain).description
    assert domain in description
    assert "sql_query" in description, "must tell the model when NOT to use Genie"
    for table in gdq.domain_tables(domain):
        assert table in description, f"{domain} description omits {table}"


def test_incident_driven_domains_warn_that_empty_is_valid(monkeypatch):
    """Zero rows on a healthy line is an answer, not a malfunction to retry."""
    for domain in ("asset", "quality"):
        monkeypatch.setenv(gdq.DOMAIN_ENV[domain], f"space-{domain}")
        assert "empty at steady state" in gdq.governed_domain_tool(domain).description


def test_prompt_fragment_is_empty_when_unprovisioned():
    assert gdq.governed_domain_prompt("quality") == ""


@pytest.mark.parametrize("domain", DOMAINS)
def test_prompt_fragment_names_the_tool_and_its_limits(monkeypatch, domain):
    monkeypatch.setenv(gdq.DOMAIN_ENV[domain], f"space-{domain}")
    prompt = gdq.governed_domain_prompt(domain)
    assert f"governed_{domain}_query" in prompt
    assert "sql_query" in prompt, "must state when direct SQL is the better choice"
    assert "ONLY your domain" in prompt


def test_space_id_is_read_at_call_time(monkeypatch):
    """Import-time capture would freeze a redeploy's new id out of the app."""
    assert gdq.domain_space_id("asset") == ""
    monkeypatch.setenv(gdq.DOMAIN_ENV["asset"], "late-binding")
    assert gdq.domain_space_id("asset") == "late-binding"
