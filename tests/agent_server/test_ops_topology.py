"""Tests for /ops/topology, the endpoint behind the System Map.

The map draws a line from each specialist to its governed Genie space. The
endpoint's job is to report which of those lines actually exist in this
deployment, so the two failure modes worth pinning are: claiming a domain is
wired when its space never provisioned, and losing the specialist-to-domain
mapping that the diagram is built from.
"""
from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient

DOMAIN_ENVS = {
    "asset": "GENIE_SPACE_ASSET",
    "quality": "GENIE_SPACE_QUALITY",
    "inventory": "GENIE_SPACE_INVENTORY",
    "production": "GENIE_SPACE_PRODUCTION",
}


@pytest.fixture
def client(monkeypatch) -> Iterator[TestClient]:
    from agent_server import start_server as ss

    monkeypatch.setattr(ss, "_startup_sql_probe", lambda: None, raising=False)
    with TestClient(ss.app) as c:
        yield c


@pytest.fixture(autouse=True)
def no_domains_provisioned(monkeypatch):
    for env in DOMAIN_ENVS.values():
        monkeypatch.delenv(env, raising=False)


def test_reports_every_domain_even_when_none_are_provisioned(client):
    """The map still renders the model; it just draws the spaces as absent."""
    body = client.get("/ops/topology").json()
    assert [d["domain"] for d in body["domains"]] == list(DOMAIN_ENVS)
    assert body["configured"] == 0
    assert body["total"] == 4
    assert all(d["configured"] is False for d in body["domains"])
    assert all(d["tool"] is None for d in body["domains"])


def test_provisioned_domains_report_their_tool(client, monkeypatch):
    monkeypatch.setenv("GENIE_SPACE_QUALITY", "space-quality")
    monkeypatch.setenv("GENIE_SPACE_ASSET", "space-asset")
    body = client.get("/ops/topology").json()
    by_domain = {d["domain"]: d for d in body["domains"]}

    assert body["configured"] == 2
    assert by_domain["quality"]["configured"] is True
    assert by_domain["quality"]["tool"] == "governed_quality_query"
    assert by_domain["inventory"]["configured"] is False
    assert by_domain["inventory"]["tool"] is None


def test_specialist_mapping_covers_every_specialist_with_a_domain(client):
    """The map is drawn from this mapping, so a dropped agent is a missing box."""
    body = client.get("/ops/topology").json()
    owners = {s for d in body["domains"] for s in d["specialists"]}
    assert owners == {
        "maintenance_predictor",
        "downtime_analyst",
        "quality_specialist",
        "supply_chain_advisor",
        "oee_coach",
        "schedule_optimizer",
        "strategic_advisor",
    }
    # Shift Briefing reads the live streaming space only and must not appear.
    assert "shift_briefing" not in owners


def test_each_specialist_belongs_to_exactly_one_domain(client):
    """Two owners for one agent would draw the same box twice."""
    body = client.get("/ops/topology").json()
    seen: list[str] = [s for d in body["domains"] for s in d["specialists"]]
    assert len(seen) == len(set(seen))


def test_every_domain_lists_entities_and_owners(client):
    body = client.get("/ops/topology").json()
    for domain in body["domains"]:
        assert domain["entities"], domain["domain"]
        assert domain["specialists"], domain["domain"]
        assert domain["label"] and domain["label"][0].isupper()


def test_entities_are_bare_table_names(client):
    """The UI renders these as chips, so parenthetical prose would overflow."""
    body = client.get("/ops/topology").json()
    for domain in body["domains"]:
        for entity in domain["entities"]:
            assert "(" not in entity and " " not in entity, entity


def test_streaming_space_is_reported_separately(client, monkeypatch):
    """The live space serves a different audience from the governed domains."""
    from agent_server.tools import genie_query

    monkeypatch.setattr(genie_query, "space_resolves", lambda *_a, **_k: True)
    monkeypatch.delenv("GENIE_SPACE_ID", raising=False)
    assert client.get("/ops/topology").json()["streaming_space"] is False
    monkeypatch.setenv("GENIE_SPACE_ID", "streaming-space")
    assert client.get("/ops/topology").json()["streaming_space"] is True


def test_the_plant_documents_assistant_is_reported(client, monkeypatch):
    """The chat panel offers document questions only when they can be answered.

    Without this the suggestions would read as a broken feature on every
    deployment that left the assistant off, which is the default.
    """
    from agent_server.tools import plant_docs_query

    monkeypatch.setattr(plant_docs_query, "_ENDPOINT", "")
    assert client.get("/ops/topology").json()["plant_docs"] is False
    monkeypatch.setattr(plant_docs_query, "_ENDPOINT", "ka-plant-docs-endpoint")
    assert client.get("/ops/topology").json()["plant_docs"] is True


def test_a_deleted_streaming_space_is_not_reported_as_present(client, monkeypatch):
    """Set is not the same as live.

    dev's space was trashed by a teardown and the System Map went on drawing
    the connection, because the only check was that the variable was non-empty.
    """
    from agent_server.tools import genie_query

    monkeypatch.setenv("GENIE_SPACE_ID", "trashed-space")
    monkeypatch.setattr(genie_query, "space_resolves", lambda *_a, **_k: False)
    assert client.get("/ops/topology").json()["streaming_space"] is False
