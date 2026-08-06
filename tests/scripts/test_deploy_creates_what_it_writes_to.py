"""Deploy steps that run before the setup job must create their own schema.

deploy.sh provisions trace storage long before the one-time setup job, because
the experiment id has to be in app.yaml before the app is deployed. On a fresh
workspace the schema therefore does not exist yet, and two steps assumed it
did:

* ``provision_trace_storage.py`` linked the experiment to a UC location in a
  schema that was not there. The link failed — and a UC trace link is permanent
  and cannot be unset, so the experiment it had just created was scrap. The
  next deploy fell through to the "-2" fallback name and the workspace
  collected one dead experiment per attempt.

* ``install_industry_data_model.py`` created the domain schemas shipped by the
  upstream model but not ``_metrics``, where every metric view lives. All 95
  views failed with SCHEMA_NOT_FOUND, on the first pass and again on the retry,
  and the install still printed a summary and moved on.

Both were invisible in a re-deploy, because by then the schema existed.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TRACE = (REPO / "scripts" / "provision_trace_storage.py").read_text(encoding="utf-8")
MVM = (REPO / "scripts" / "install_industry_data_model.py").read_text(encoding="utf-8")
DEPLOY = (REPO / "scripts" / "deploy.sh").read_text(encoding="utf-8")


class TestTraceStorageDoesNotAssumeTheSchema:
    def test_the_schema_is_created_before_the_link(self):
        assert "_ensure_schema" in TRACE, (
            "provision_trace_storage no longer creates its schema; a fresh "
            "workspace will produce an experiment linked to nothing, permanently"
        )
        ensure_at = TRACE.index("_ensure_schema(warehouse")
        link_at = TRACE.index("def link(candidate")
        assert ensure_at < link_at, "the schema must exist before the link is attempted"

    def test_it_uses_create_if_not_exists(self):
        """Re-deploys must not fail on a schema that is already there."""
        assert re.search(r"CREATE SCHEMA IF NOT EXISTS", TRACE)

    def test_it_still_runs_before_the_setup_job(self):
        """If this ordering ever changes, the workaround above is dead weight."""
        trace_at = DEPLOY.index("provision_trace_storage.py")
        setup_at = DEPLOY.index("running one-time setup job")
        assert trace_at < setup_at, (
            "trace provisioning now runs after setup — _ensure_schema is "
            "redundant and this guard should be reconsidered"
        )


class TestTheIndustryModelCreatesItsMetricSchema:
    def test_metric_schemas_are_ensured(self):
        assert "_ensure_metric_schemas" in MVM, (
            "the _metrics schema is never created, so every metric view fails "
            "with SCHEMA_NOT_FOUND on a fresh catalog"
        )

    def test_it_runs_before_the_metric_phase(self):
        ensure_at = MVM.index("_ensure_metric_schemas(exec_one")
        phase_at = MVM.index('do("metric", threads)')
        assert ensure_at < phase_at

    def test_the_schema_names_come_from_the_statements(self):
        """Hard-coding `_metrics` would miss a model that adds another."""
        assert "_METRIC_TARGET" in MVM
        assert 'm.group("schema")' in MVM

    def test_the_target_pattern_matches_a_real_metric_view(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "install_industry_data_model", REPO / "scripts" / "install_industry_data_model.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        stmt = (
            "CREATE OR REPLACE VIEW `my_catalog`.`_metrics`.`asset_failure_record` "
            "WITH METRICS LANGUAGE YAML AS $$ ... $$"
        )
        match = module._METRIC_TARGET.search(stmt)
        assert match, "the pattern no longer matches the metric views it was written for"
        assert match.group("schema") == "_metrics"
        assert match.group("catalog") == "my_catalog"
