"""Tests for the judge-alignment scaffolding (evaluations/alignment).

Covers the pure, offline-checkable logic:
  * the MemAlign invariant — label-schema names == judge names, categories
    derived from the judges (no drift);
  * UC-dataset row shaping from EvalRecords;
  * run_eval.resolve_scorers falling back to base judges when no aligned judge
    is registered.

Workspace-only steps (create_dataset, create_labeling_session, align) are not
exercised here — they require a live Databricks experiment.
"""
from __future__ import annotations

import pytest

pytest.importorskip("mlflow")

from evaluations.alignment import base_judges as bj  # noqa: E402
from evaluations.alignment.uc_dataset import records_to_rows  # noqa: E402


class TestLabelSchemaInvariant:
    def test_schema_names_match_judge_names(self):
        judge_names = {j.name for j in bj.make_base_judges()}
        schema_names = {s["name"] for s in bj.label_schema_specs()}
        # The MemAlign pairing key: every alignable judge has an identically
        # named label schema, and there are no stray schemas.
        assert schema_names == judge_names

    def test_factory_name_equals_judge_name(self):
        # run_eval.resolve_scorers relies on factory.__name__ == judge.name.
        for factory in bj.ALIGNABLE_JUDGE_FACTORIES:
            assert factory().name == factory.__name__

    def test_categories_derived_from_judges(self):
        by_name = {s["name"]: s for s in bj.label_schema_specs()}
        for judge in bj.make_base_judges():
            assert by_name[judge.name]["categories"] == list(bj.judge_categories(judge))
            assert len(by_name[judge.name]["categories"]) >= 2

    def test_every_schema_has_instruction(self):
        for spec in bj.label_schema_specs():
            assert spec["instruction"].strip()
            assert spec["enable_comment"] is True


class TestUcDatasetRows:
    def test_rows_from_seed_records(self):
        from evaluations.dataset import build_seed_records

        rows = records_to_rows(build_seed_records())
        assert rows and all("inputs" in r for r in rows)
        # A golden record carries expectations; a routing-only record may not —
        # but when present it must be a dict.
        assert any("expectations" in r for r in rows)
        for r in rows:
            if "expectations" in r:
                assert isinstance(r["expectations"], dict) and r["expectations"]

    def test_empty_expectations_dropped(self):
        from evaluations.dataset import EvalRecord

        rows = records_to_rows([EvalRecord(inputs={"query": "hi"}, expectations={})])
        assert rows == [{"inputs": {"query": "hi"}}]


class TestResolveScorers:
    def test_base_by_default(self):
        from evaluations.run_eval import resolve_scorers

        names = {getattr(s, "name", None) for s in resolve_scorers(use_aligned=False)}
        assert {"recovery_respects_buffers", "optimizer_choice_appropriate",
                "cites_live_evidence"} <= names

    def test_fallback_when_no_aligned_judge(self, monkeypatch):
        import mlflow.genai.scorers as scorers_mod

        from evaluations.run_eval import resolve_scorers

        def _boom(*a, **k):
            raise RuntimeError("no aligned judge registered")

        monkeypatch.setattr(scorers_mod, "get_scorer", _boom)
        # use_aligned=True but nothing registered → must not raise, must still
        # return the base judges by name.
        names = {getattr(s, "name", None) for s in resolve_scorers(use_aligned=True)}
        assert {"recovery_respects_buffers", "optimizer_choice_appropriate",
                "cites_live_evidence"} <= names
