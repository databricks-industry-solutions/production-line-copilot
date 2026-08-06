"""Tests for ``agent_server/prompts/catalog.py``.

The catalogue exists so the registration script can enumerate the prompts. Its
failure mode is silent: add a specialist, forget the catalogue entry, and the
new prompt simply never reaches the registry while every deploy keeps reporting
success. The completeness test below turns that into a red build.

The second concern is brace syntax. The registry renders with Jinja
(``{{var}}``) and the app formats with ``str.format`` (``{var}``), so the
conversion has to be exact in both directions: convert too little and the
registered template has no variables, convert too much and the literal JSON
examples inside three system prompts become variables that no call site fills.
"""
from __future__ import annotations

import ast
from pathlib import Path

from agent_server.prompts.catalog import BY_SLUG, CATALOG, NAME_PREFIX, uc_name

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "agent_server" / "prompts"

# Modules that hold no prompt of their own.
_NOT_PROMPTS = {"__init__", "catalog", "loader"}

# Fragments composed into other prompts at import time rather than sent on their
# own. They reach the registry inside the specialist prompts that embed them.
_FRAGMENTS = {
    "DATA_CITATION_RULES",
    "GOVERNED_MODEL_NOTE",
    "RESPONSE_STRUCTURE",
    "LIVE_QUALITY_SCHEMA",
    "LIVE_DATA_TABLES",
}


def _exported_prompt_constants() -> dict[str, str]:
    """UPPER_CASE string constants across ``agent_server/prompts/*.py``.

    Read with ``ast`` rather than by importing so the test describes what the
    modules declare, and so a constant assembled at import time cannot hide
    from it.
    """
    found: dict[str, str] = {}
    for path in sorted(PROMPTS_DIR.glob("*.py")):
        if path.stem in _NOT_PROMPTS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    found[target.id] = path.stem
    return found


class TestCompleteness:
    def test_every_prompt_constant_is_catalogued(self):
        """Add a specialist prompt and you must register it. That is the point."""
        catalogued = {spec.text for spec in CATALOG}
        missing = []
        for const, module in _exported_prompt_constants().items():
            if const in _FRAGMENTS:
                continue
            mod = __import__(f"agent_server.prompts.{module}", fromlist=[const])
            if getattr(mod, const) not in catalogued:
                missing.append(f"{module}.{const}")
        assert not missing, (
            "prompt constants absent from CATALOG (they would never be "
            f"registered): {missing}"
        )

    def test_fragments_reach_the_registry_inside_their_hosts(self):
        """The exclusion above is only safe while this holds."""
        from agent_server.prompts._shared import RESPONSE_STRUCTURE

        hosts = [s.slug for s in CATALOG if RESPONSE_STRUCTURE in s.text]
        assert len(hosts) >= 7, hosts

    def test_slugs_are_unique(self):
        slugs = [spec.slug for spec in CATALOG]
        assert len(slugs) == len(set(slugs))


# A system prompt written inline is invisible to the completeness test above,
# which can only see the prompts package. That is not hypothetical: the
# "general" route's prompt sat in agent.py for the whole life of the registry
# work, so the one route with no tools — and therefore the one most dependent
# on its prompt — was also the one route whose prompt had no version behind it.
AGENT_SERVER = Path(__file__).resolve().parents[2] / "agent_server"

# Prompt text is long. A short literal is a role name or a separator, not a
# prompt, and failing those would only teach people to route around the test.
_LITERAL_BUDGET = 40


def _literal_chars(node: ast.AST) -> int:
    """Characters of string literal inside ``node``, f-string segments included."""
    return sum(
        len(sub.value)
        for sub in ast.walk(node)
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str)
    )


class TestNoPromptIsBuiltOutsideThePackage:
    def test_system_messages_are_composed_from_catalogued_constants(self):
        """``SystemMessage(content=...)`` must reference a prompt, not inline one.

        Passing a name, an attribute, or a ``.format()`` call is fine — that
        text lives in the prompts package and the completeness test can see it.
        A literal or an f-string is a prompt that only exists at the call site.
        """
        offenders = []
        for path in sorted(AGENT_SERVER.rglob("*.py")):
            if PROMPTS_DIR in path.parents:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if name != "SystemMessage":
                    continue
                content = next(
                    (kw.value for kw in node.keywords if kw.arg == "content"),
                    node.args[0] if node.args else None,
                )
                if content is None:
                    continue
                if _literal_chars(content) >= _LITERAL_BUDGET:
                    rel = path.relative_to(AGENT_SERVER.parent)
                    offenders.append(f"{rel}:{node.lineno}")

        assert not offenders, (
            "system prompts built from literals outside agent_server/prompts — "
            "they cannot be registered, versioned, or diffed against a trace: "
            f"{offenders}"
        )


class TestBraceConversion:
    def test_declared_variables_match_what_format_would_need(self):
        """A template whose declared variables drift from its text renders wrong."""
        for spec in CATALOG:
            if spec.variables:
                assert spec.format_variables == spec.variables, spec.slug

    def test_every_declared_variable_is_doubled_for_the_registry(self):
        for spec in CATALOG:
            for var in spec.variables:
                assert "{{" + var + "}}" in spec.registry_template, (spec.slug, var)
                assert "{" + var + "}" not in spec.registry_template.replace(
                    "{{" + var + "}}", ""
                ), (spec.slug, var)

    def test_static_prompts_are_registered_verbatim(self):
        """Nothing to substitute, so nothing may be rewritten."""
        for spec in CATALOG:
            if not spec.variables:
                assert spec.registry_template == spec.text, spec.slug

    def test_literal_json_braces_survive_conversion(self):
        """These look like format fields but are examples the model should read.

        ``str.format`` would raise on them, which is why the specs that contain
        them declare no variables.
        """
        spec = BY_SLUG["oee_coach_system"]
        assert '{"shift_id"' in spec.registry_template
        assert '{{"shift_id"' not in spec.registry_template


class TestNaming:
    def test_names_are_prefixed_away_from_table_names(self):
        """A UC schema is one namespace and this one is full of tables."""
        for spec in CATALOG:
            assert spec.name.startswith(NAME_PREFIX)

    def test_uc_name_is_three_level(self):
        assert uc_name(CATALOG[0], "c", "s") == f"c.s.{CATALOG[0].name}"
