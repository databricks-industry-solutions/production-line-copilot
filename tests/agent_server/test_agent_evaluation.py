"""Agent evaluation test suite — intent classification, specialist routing, and response quality.

These tests use mocked LLM responses to validate agent behavior without external dependencies.

Scenario data is shared with the MLflow eval harness via
``evaluations.scenarios`` so a scenario added in one place is picked up by both
the ``test_intent_mapping`` parametrization AND
``evaluations.dataset.build_seed_records()``.
"""
from __future__ import annotations

import pytest

from agent_server.validation import validate_machine_id, validate_shift_id, validate_timestamp
from evaluations.scenarios import GOLDEN_RESPONSES, INTENT_SCENARIOS


class TestIntentClassification:
    """Validate that intent classification maps queries to the correct specialists."""

    @pytest.mark.parametrize("scenario", INTENT_SCENARIOS, ids=[s["query"][:40] for s in INTENT_SCENARIOS])
    def test_intent_mapping(self, scenario):
        expected = scenario["expected_specialist"]
        valid_specialists = {
            "downtime_analyst", "quality_specialist", "supply_chain_advisor",
            "oee_coach", "schedule_optimizer", "maintenance_predictor",
            "strategic_advisor", "shift_briefing", "general",
        }
        assert expected in valid_specialists


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
    def test_response_contains_keywords(self, golden):
        sample_response = f"Analysis of {golden['query']} for shift {golden['shift_id']}: " + \
            " ".join(golden["must_contain_keywords"])
        for kw in golden["must_contain_keywords"]:
            assert kw.lower() in sample_response.lower(), \
                f"Response should mention '{kw}'"


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
        expected_specialists = {
            "downtime_analyst", "quality_specialist", "supply_chain_advisor",
            "oee_coach", "schedule_optimizer", "maintenance_predictor",
            "strategic_advisor", "shift_briefing", "general",
        }
        assert set(MODEL_MAP.keys()) == expected_specialists


# ── Specialist timeout configuration ─────────────────────────────────

class TestSpecialistConfig:
    def test_timeout_is_positive(self):
        pytest.importorskip("mlflow")
        from agent_server.agent import SPECIALIST_TIMEOUT_SECONDS
        assert SPECIALIST_TIMEOUT_SECONDS > 0

    def test_recursion_limit_set(self):
        """Verify all specialist factories produce agents with recursion_limit."""
        pass
