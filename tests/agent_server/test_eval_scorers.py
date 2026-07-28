"""Unit tests for the deterministic eval plumbing (no LLM calls).

Covers the gate wiring in ``evaluations.run_eval.evaluate_gate`` (including the
``forbidden_phrase`` threshold added for the ``response_avoids_forbidden``
scorer) and the shape/growth of the seed dataset. These run in CI without a
workspace connection; the LLM-backed judges are exercised separately by the
opt-in ``test_eval_gate`` integration test.
"""
from __future__ import annotations

import pytest

pytest.importorskip("mlflow")

from evaluations.dataset import build_seed_records  # noqa: E402
from evaluations.judges import all_scorers  # noqa: E402
from evaluations.run_eval import evaluate_gate  # noqa: E402
from evaluations.scenarios import GOLDEN_RESPONSES, INTENT_SCENARIOS  # noqa: E402

_VALID_SPECIALISTS = {
    "downtime_analyst", "quality_specialist", "supply_chain_advisor",
    "oee_coach", "schedule_optimizer", "maintenance_predictor",
    "strategic_advisor", "shift_briefing", "general",
}


class TestForbiddenPhraseGate:
    def test_contains_forbidden_fails_gate(self):
        rows = [{"response_avoids_forbidden/value": "contains_forbidden"}]
        passed, breakdown = evaluate_gate(rows)
        assert breakdown["forbidden_phrase"]["pass"] is False
        assert passed is False

    def test_clean_passes_gate(self):
        rows = [{"response_avoids_forbidden/value": "clean"}]
        passed, breakdown = evaluate_gate(rows)
        assert breakdown["forbidden_phrase"]["pass"] is True

    def test_not_applicable_excluded_from_denominator(self):
        # All records opt out → nothing decisive → cannot trip the gate.
        rows = [{"response_avoids_forbidden/value": "not_applicable"} for _ in range(5)]
        _, breakdown = evaluate_gate(rows)
        assert breakdown["forbidden_phrase"]["n"] == 0
        assert breakdown["forbidden_phrase"]["pass"] is True


class TestScorerRegistration:
    def test_forbidden_scorer_registered(self):
        names = {getattr(s, "name", getattr(s, "__name__", "")) for s in all_scorers()}
        assert "response_avoids_forbidden" in names
        assert "incident_matches_specialist" in names


class TestSeedDatasetGrowth:
    def test_dataset_has_grown(self):
        records = build_seed_records()
        # 30 routing scenarios + golden responses after the Theme 3a expansion.
        assert len(records) >= 30

    def test_every_routing_scenario_targets_a_valid_specialist(self):
        for s in INTENT_SCENARIOS:
            assert s["expected_specialist"] in _VALID_SPECIALISTS, s["query"]

    def test_every_specialist_has_at_least_one_scenario(self):
        covered = {s["expected_specialist"] for s in INTENT_SCENARIOS}
        # Every specialist that can be routed to should have coverage.
        assert _VALID_SPECIALISTS.issubset(covered)

    def test_golden_responses_carry_expected_facts(self):
        for g in GOLDEN_RESPONSES:
            assert g["must_contain_keywords"], g["scenario"]
