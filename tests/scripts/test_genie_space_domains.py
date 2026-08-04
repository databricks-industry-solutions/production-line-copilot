"""Tests for the per-domain governed Genie spaces.

``scripts/genie_space_config_mvm.py`` is the single source of truth for five
spaces: the combined governed space plus one narrow space per schema of the
governed model. The invariants that matter are containment ones — a domain space
must never describe a table it cannot see, teach an example that queries one, or
carry a join rule spanning a schema outside its scope. Those are the failures
that quietly degrade Genie, and none of them are visible in the workspace UI.

No network and no CLI: everything builds from the module.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture(scope="module")
def mod():
    return importlib.import_module("scripts.genie_space_config_mvm")


def test_domains_match_the_governed_schemas(mod):
    """The four domains are exactly the schemas the table list uses."""
    from_tables = {mod.table_domain(t["identifier"]) for t in mod.tables}
    assert from_tables == set(mod.DOMAINS)


@pytest.mark.parametrize("domain", ["asset", "quality", "inventory", "production"])
def test_domain_space_only_holds_its_own_tables(mod, domain):
    space = mod.build_space({domain})
    identifiers = [t["identifier"] for t in space["data_sources"]["tables"]]
    assert identifiers, f"{domain} space has no tables"
    for ident in identifiers:
        assert mod.table_domain(ident) == domain, f"{ident} leaked into {domain}"


@pytest.mark.parametrize("domain", ["asset", "quality", "inventory", "production"])
def test_domain_examples_never_query_outside_the_space(mod, domain):
    """An example that joins a table the space cannot see teaches broken SQL."""
    space = mod.build_space({domain})
    for example in space["instructions"]["example_question_sqls"]:
        touched = mod.sql_domains(example)
        assert touched <= {domain}, (
            f"{domain} example {example['id']} references {sorted(touched - {domain})}"
        )


@pytest.mark.parametrize("domain", ["asset", "quality", "inventory", "production"])
def test_every_domain_space_ships_with_examples(mod, domain):
    """Genie is at its worst with no examples, so each space must have some.

    Quality regressed here once: its only NCR example joined the asset register,
    so scoping left the space with zero. Two quality-native examples were added.
    """
    space = mod.build_space({domain})
    assert space["instructions"]["example_question_sqls"], f"{domain} has no example SQL"
    assert space["config"]["sample_questions"], f"{domain} has no sample questions"


@pytest.mark.parametrize("domain", ["asset", "quality", "inventory", "production"])
def test_domain_instructions_omit_unusable_join_rules(mod, domain):
    """A join rule survives only when the space can see every side of it.

    Scoped to the JOIN GRAPH section: the facts section above it may legitimately
    name another schema (the incident-emptiness rule spans asset and quality).
    """
    text = "".join(mod.build_instructions({domain}))
    if "JOIN GRAPH" not in text:
        return  # No usable joins for this domain, which is a valid outcome.
    join_section = text.split("JOIN GRAPH", 1)[1]
    for other in set(mod.DOMAINS) - {domain}:
        assert f"{other}." not in join_section, (
            f"{domain} join graph references the {other} schema it cannot query"
        )


def test_combined_space_is_the_union_of_the_domains(mod):
    """`--domain all` must stay a superset — the combined space still ships."""
    combined = mod.build_space(set(mod.DOMAINS))
    combined_ids = {t["identifier"] for t in combined["data_sources"]["tables"]}
    union: set[str] = set()
    for domain in mod.DOMAINS:
        union |= {t["identifier"] for t in mod.build_space({domain})["data_sources"]["tables"]}
    assert combined_ids == union
    assert len(combined_ids) == len(mod.tables)


def test_every_sample_question_is_assigned_a_domain(mod):
    """An untagged question would vanish from all four domain spaces."""
    tagged = set(mod.SAMPLE_QUESTION_DOMAINS)
    actual = {q["id"] for q in mod.sample_questions}
    assert actual == tagged, f"untagged: {sorted(actual - tagged)}, stale: {sorted(tagged - actual)}"
    assert set(mod.SAMPLE_QUESTION_DOMAINS.values()) <= set(mod.DOMAINS)


def test_unknown_domain_is_rejected(mod):
    with pytest.raises(SystemExit):
        mod.build_space({"logistics"})


def test_tool_entity_lists_match_the_provisioned_tables(mod):
    """The tool description and the space must agree on what is in the domain.

    ``governed_domain_query.DOMAIN_ENTITIES`` tells the model what it can ask
    about; this config decides what the space can actually see. Drift means the
    model is invited to query a table that is not registered — so the two are
    cross-checked rather than kept in sync by comment.
    """
    from agent_server.tools.governed_domain_query import DOMAIN_ENV, domain_tables

    assert set(DOMAIN_ENV) == set(mod.DOMAINS), "domain lists disagree"
    for domain in mod.DOMAINS:
        provisioned = {
            t["identifier"].rsplit(".", 1)[-1]
            for t in mod.build_space({domain})["data_sources"]["tables"]
        }
        assert set(domain_tables(domain)) == provisioned, (
            f"{domain}: tool advertises {sorted(domain_tables(domain))}, "
            f"space registers {sorted(provisioned)}"
        )


def test_every_domain_has_a_title_and_description(mod):
    """These reach the workspace UI, so an empty one is a visible defect."""
    for domain, meta in mod.DOMAINS.items():
        assert meta["title"].strip(), domain
        assert len(meta["description"].strip()) > 40, domain
