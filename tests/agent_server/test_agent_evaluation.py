"""Agent evaluation test suite — intent classification, specialist routing, and response quality.

These tests use mocked LLM responses to validate agent behavior without external dependencies.

Scenario data is shared with the MLflow eval harness via
``evaluations.scenarios`` so a scenario added in one place is picked up by both
the ``test_intent_mapping`` parametrization AND
``evaluations.dataset.build_seed_records()``.
"""
from __future__ import annotations

import pytest

from agent_server.agent import (
    INCIDENT_TO_SPECIALIST,
    VALID_SPECIALISTS,
    _route_by_active_incident,
)
from agent_server.validation import validate_machine_id, validate_shift_id, validate_timestamp
from evaluations.scenarios import GOLDEN_RESPONSES, INTENT_SCENARIOS


class TestIntentClassification:
    """Validate that intent classification maps queries to the correct specialists."""

    @pytest.mark.parametrize("scenario", INTENT_SCENARIOS, ids=[s["query"][:40] for s in INTENT_SCENARIOS])
    def test_expected_specialist_is_one_the_router_can_return(self, scenario):
        """Guards the scenario corpus against naming a specialist that cannot win.

        This used to compare against a set copied into the test file, so it
        asserted the fixture agreed with itself. Importing the router's own
        VALID_SPECIALISTS means renaming a specialist in one place now fails
        here instead of quietly routing 99 scenarios to "general".
        """
        assert scenario["expected_specialist"] in VALID_SPECIALISTS

    @pytest.mark.parametrize("scenario_id,expected", sorted(INCIDENT_TO_SPECIALIST.items()))
    def test_an_active_incident_routes_deterministically(self, scenario_id, expected):
        """The no-LLM routing path, actually executed rather than described."""
        routed = _route_by_active_incident({"active_incidents": [{"scenario_id": scenario_id}]})
        assert routed == expected

    def test_an_unknown_incident_defers_to_the_classifier(self):
        assert _route_by_active_incident({"active_incidents": [{"scenario_id": "nonsense"}]}) is None

    def test_no_incident_defers_to_the_classifier(self):
        assert _route_by_active_incident({"active_incidents": []}) is None

    def test_every_incident_routes_to_a_specialist_that_exists(self):
        """A typo in INCIDENT_TO_SPECIALIST would route into the general fallback."""
        unroutable = {v for v in INCIDENT_TO_SPECIALIST.values() if v not in VALID_SPECIALISTS}
        assert not unroutable, f"incident map points at unknown specialists: {sorted(unroutable)}"


# ── Input validation tests ───────────────────────────────────────────

class TestInputValidation:
    def test_valid_shift_id(self):
        assert validate_shift_id("shift_1") == "shift_1"

    def test_invalid_shift_id_sql_injection(self):
        with pytest.raises(ValueError):
            validate_shift_id("shift_1'; DROP TABLE --")

    def test_valid_timestamp(self):
        result = validate_timestamp("2025-01-01T14:00:00")
        assert result == "2025-01-01T14:00:00"

    def test_timestamp_strips_z(self):
        result = validate_timestamp("2025-01-01T14:00:00.000Z")
        assert result == "2025-01-01T14:00:00"

    def test_invalid_timestamp(self):
        with pytest.raises(ValueError):
            validate_timestamp("not-a-timestamp")

    def test_valid_machine_id(self):
        assert validate_machine_id("LABELLER") == "LABELLER"

    def test_none_machine_id(self):
        assert validate_machine_id(None) is None

    def test_empty_machine_id(self):
        assert validate_machine_id("") is None

    def test_injection_machine_id(self):
        with pytest.raises(ValueError):
            validate_machine_id("LABELLER'; DROP TABLE--")

    def test_shift_id_max_length(self):
        long_id = "a" * 100
        assert validate_shift_id(long_id) == long_id


# ── Response quality checks ──────────────────────────────────────────
# GOLDEN_RESPONSES lives in evaluations.scenarios (imported above).


class TestResponseQuality:
    """Validate response content quality using keyword checks."""

    @pytest.mark.parametrize("golden", GOLDEN_RESPONSES, ids=[g["scenario"] for g in GOLDEN_RESPONSES])
    def test_the_golden_record_is_usable_as_a_check(self, golden):
        """Validates the fixture, which is all a unit test can do here.

        What stood here before built a fake response by concatenating
        `must_contain_keywords` and then asserted those keywords appeared in
        it — true by construction, for any keywords, forever. Real answer
        quality needs a live model and is scored by evaluations/run_eval.py;
        the useful job at this level is making sure the golden records
        themselves could ever fail something.
        """
        keywords = golden["must_contain_keywords"]
        assert keywords, f"{golden['scenario']} has no keywords, so it asserts nothing"
        assert all(k.strip() for k in keywords), "a blank keyword matches everything"
        # Topic words lifted from the question are free to satisfy. At least
        # one keyword has to demand something the question does not already
        # contain, or a model that paraphrases the prompt scores full marks.
        beyond_echo = [k for k in keywords if k.lower() not in golden["query"].lower()]
        assert beyond_echo, (
            f"{golden['scenario']}: every keyword {keywords} already appears in "
            "the question, so restating it would pass this record in full"
        )


# ── Query length validation ──────────────────────────────────────────

class TestQueryLengthValidation:
    def test_max_query_length_enforcement(self):
        pytest.importorskip("mlflow")
        from agent_server.agent import MAX_QUERY_LENGTH
        assert MAX_QUERY_LENGTH > 0
        assert MAX_QUERY_LENGTH <= 10000

    def test_model_map_completeness(self):
        """Every registered specialist must appear in MODEL_MAP so the UI's
        reasoning-trace card can surface which endpoint answered. Keep this
        set in sync with `_get_specialists()` in agent.py."""
        pytest.importorskip("mlflow")
        from agent_server.agent import MODEL_MAP
        assert set(MODEL_MAP.keys()) == set(VALID_SPECIALISTS)


# ── Specialist timeout configuration ─────────────────────────────────

class TestSpecialistConfig:
    def test_timeout_is_positive(self):
        pytest.importorskip("mlflow")
        from agent_server.agent import SPECIALIST_TIMEOUT_SECONDS
        assert SPECIALIST_TIMEOUT_SECONDS > 0

    def test_recursion_limit_set(self):
        """The ReAct loop needs a ceiling, or a confused specialist bills forever.

        The body of this test was `pass` — it named a real risk and checked
        nothing for as long as it has existed.
        """
        pytest.importorskip("mlflow")
        from agent_server.agent import SPECIALIST_RECURSION_LIMIT
        assert SPECIALIST_RECURSION_LIMIT > 0
        # Each step is a model call: high enough for a multi-tool answer, low
        # enough that a loop is capped rather than merely discouraged.
        assert 4 <= SPECIALIST_RECURSION_LIMIT <= 40

    def test_every_specialist_is_invoked_with_the_recursion_limit(self):
        """A factory that forgets to pass it inherits LangGraph's default of 25."""
        import re
        from pathlib import Path

        missing = []
        for path in sorted(Path("agent_server/specialists").glob("*.py")):
            text = path.read_text()
            if "create_react_agent" not in text:
                continue
            if not re.search(r"recursion_limit", text):
                missing.append(path.name)
        assert not missing, (
            f"specialists build a ReAct agent without a recursion limit: {missing}"
        )
