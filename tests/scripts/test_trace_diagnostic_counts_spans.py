"""The trace diagnostic has to distinguish "not written" from "not read".

Both look identical from the MLflow UI — an experiment with no traces in it —
and the script used to answer only the first, so it could report every check
green on an environment serving traces perfectly well and then send the reader
off to re-grant service principal write access. The two need telling apart, and
the span count is what tells them apart.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def diag():
    spec = importlib.util.spec_from_file_location(
        "diagnose_trace_storage", ROOT / "scripts" / "diagnose_trace_storage.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestWhatTheReportSays:
    def test_stored_spans_point_at_the_reader_not_the_writer(self, diag, capsys):
        rc = diag._report([], spans=(639, "2026-08-06T15:29:15Z"))
        out = capsys.readouterr().out

        assert rc == 0
        assert "639 span(s) stored" in out
        assert "reading problem" in out
        assert "MLFLOW_TRACING_SQL_WAREHOUSE_ID" in out
        # The old advice is actively misleading once spans are known to exist.
        assert "lack write access" not in out

    def test_an_empty_table_still_blames_the_writer(self, diag, capsys):
        diag._report([], spans=(0, "never"))
        out = capsys.readouterr().out

        assert "0 span(s) stored" in out
        assert "write access" in out
        assert "reading problem" not in out

    def test_an_uncountable_table_falls_back_to_the_old_advice(self, diag, capsys):
        # No warehouse configured, or the query failed. The script must still
        # produce a report rather than nothing.
        diag._report([], spans=None)
        out = capsys.readouterr().out

        assert "correctly provisioned" in out
        assert "write access" in out

    def test_real_problems_still_take_priority_over_the_count(self, diag, capsys):
        rc = diag._report(["re-run the deploy"], spans=(639, "whenever"))
        out = capsys.readouterr().out

        assert rc == 1
        assert "What to do:" in out
        assert "re-run the deploy" in out


class TestCountingIsBestEffort:
    def test_no_span_table_means_no_count(self, diag):
        # Tags name logs and annotations too; only the spans table is countable.
        assert diag._count_spans(Path("unused.yaml"), "prod", []) is None
        assert diag._count_spans(Path("unused.yaml"), "prod", ["cat.sch.t_otel_logs"]) is None

    def test_a_broken_warehouse_does_not_break_the_diagnosis(self, diag, tmp_path, capsys):
        """A diagnostic that dies while diagnosing is worse than a partial one."""
        settings = tmp_path / "s.yaml"
        settings.write_text(
            "profiles:\n"
            "  line_manager_prod:\n"
            "    bundle_target: line_manager_prod\n"
            "    sql_warehouse_id: 'SET_ME'\n",
            encoding="utf-8",
        )
        assert diag._count_spans(settings, "prod", ["cat.sch.t_otel_spans"]) is None

    def test_the_documented_cause_is_the_one_the_script_reports(self):
        """Docs and script must not drift on the first thing to check."""
        deploy_doc = (ROOT / "docs" / "solution-accelerator" / "DEPLOY.md").read_text(encoding="utf-8")
        assert "MLFLOW_TRACING_SQL_WAREHOUSE_ID" in deploy_doc
        assert "served by a SQL warehouse" in deploy_doc
