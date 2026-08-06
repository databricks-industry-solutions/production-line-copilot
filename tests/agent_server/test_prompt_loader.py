"""Tests for ``agent_server/prompts/loader.py``.

Every test here is about the same question: when the registry is wrong,
unreachable, or subtly edited, does the agent still get a usable prompt?

The failure this guards against is quiet. A prompt that fails to load does not
raise — it produces an agent that answers slightly worse, which surfaces days
later as "the model seems dumber lately" rather than as an error.
"""
from __future__ import annotations

import pytest

from agent_server.prompts import loader
from agent_server.prompts.catalog import BY_SLUG, CATALOG


@pytest.fixture(autouse=True)
def _clean_cache():
    loader.reset_cache()
    yield
    loader.reset_cache()


class FakePrompt:
    def __init__(self, template):
        self.template = template


def _wire(monkeypatch, prompts: dict[str, str], *, enabled=True, explode=False):
    """Point the loader at ``prompts``, keyed by slug."""
    monkeypatch.setenv("PROMPTS_FROM_REGISTRY", "true" if enabled else "false")

    def fake_load(_uri=None):
        raise AssertionError("unused")

    def fake_registry():
        if explode:
            raise RuntimeError("registry on fire")
        return dict(prompts)

    monkeypatch.setattr(loader, "_load_from_registry", fake_registry)


class TestDisabledByDefault:
    def test_without_the_flag_every_prompt_comes_from_code(self, monkeypatch):
        monkeypatch.delenv("PROMPTS_FROM_REGISTRY", raising=False)
        assert loader.prompt_text("orchestrator_system") == BY_SLUG["orchestrator_system"].text

    def test_the_flag_is_read_late_enough_to_be_set_by_config(self, monkeypatch):
        monkeypatch.setenv("PROMPTS_FROM_REGISTRY", "true")
        assert loader.registry_enabled()
        monkeypatch.setenv("PROMPTS_FROM_REGISTRY", "no")
        assert not loader.registry_enabled()


class TestFallback:
    def test_an_unreachable_registry_does_not_break_the_agent(self, monkeypatch):
        _wire(monkeypatch, {}, explode=True)
        for spec in CATALOG:
            assert loader.prompt_text(spec.slug) == spec.text

    def test_a_prompt_absent_from_the_registry_falls_back_alone(self, monkeypatch):
        _wire(monkeypatch, {"orchestrator_system": "registered text"})
        assert loader.prompt_text("orchestrator_system") == "registered text"
        assert loader.prompt_text("oee_coach_system") == BY_SLUG["oee_coach_system"].text

    def test_registry_text_is_preferred_when_present(self, monkeypatch):
        _wire(monkeypatch, {"oee_coach_system": "newer coaching prompt"})
        assert loader.prompt_text("oee_coach_system") == "newer coaching prompt"


class TestTheTextIsUsable:
    """``_usable`` is the gate between "a version exists" and "we will send it"."""

    def test_jinja_braces_are_converted_back_for_str_format(self):
        spec = BY_SLUG["general_system"]
        text = loader._usable(spec, spec.registry_template)
        assert text == spec.text
        assert text.format(context="ctx").endswith("ctx")

    def test_a_registered_template_missing_a_variable_is_rejected(self):
        """Someone edits the version in the UI and drops the context block.

        Accepting it sends the general route a prompt with no line data in it.
        """
        spec = BY_SLUG["general_system"]
        assert loader._usable(spec, "You are a helpful assistant.") is None

    def test_blank_text_is_rejected(self):
        spec = BY_SLUG["orchestrator_system"]
        assert loader._usable(spec, "   \n ") is None
        assert loader._usable(spec, None) is None

    def test_a_static_prompt_needs_no_variables(self):
        spec = BY_SLUG["orchestrator_system"]
        assert loader._usable(spec, "any text at all") == "any text at all"


class TestRoundTrip:
    def test_every_catalogued_prompt_survives_the_registry_and_back(self):
        """The conversion is lossless, so loading cannot change the prompt."""
        for spec in CATALOG:
            assert spec.runtime_text(spec.registry_template) == spec.text

    def test_a_loaded_template_still_renders_with_its_call_site_variables(self):
        for spec in CATALOG:
            if not spec.variables:
                continue
            text = loader._usable(spec, spec.registry_template)
            assert text is not None, spec.slug
            rendered = text.format(**{v: "x" for v in spec.variables})
            assert "{{" not in rendered, spec.slug


class TestResolutionIsStable:
    def test_prompts_are_resolved_once(self, monkeypatch):
        calls = []

        monkeypatch.setenv("PROMPTS_FROM_REGISTRY", "true")

        def counting():
            calls.append(1)
            return {}

        monkeypatch.setattr(loader, "_load_from_registry", counting)
        loader.prompt_text("orchestrator_system")
        loader.prompt_text("oee_coach_system")
        assert len(calls) == 1

    def test_an_unknown_slug_is_a_programming_error(self, monkeypatch):
        monkeypatch.delenv("PROMPTS_FROM_REGISTRY", raising=False)
        with pytest.raises(KeyError):
            loader.prompt_text("no_such_prompt")
