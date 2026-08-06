"""The trace-storage diagnostic has to name the failure, not just detect it.

Empty traces have several causes that are indistinguishable in the MLflow UI,
and the most dangerous is the one that reports success: an experiment linked
without a telemetry destination accepts traces and discards every span.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

diagnose_mod = importlib.import_module("diagnose_trace_storage")


VALUE_FROM_BLOCK = """\
env:
  - name: SOMETHING_ELSE
    value: 'keep me'
  - name: MLFLOW_EXPERIMENT_ID
    valueFrom: experiment
  - name: AFTER
    value: 'x'
"""

LITERAL_BLOCK = """\
env:
  - name: MLFLOW_EXPERIMENT_ID
    value: '647374559734684'
"""

COMMENTED_BLOCK = """\
env:
  - name: MLFLOW_EXPERIMENT_ID
    # a comment between the name and the value
    value: '12345'
"""


class TestReadingWhatTheAppWasTold:
    """The app.yaml setting is the difference between the two experiments."""

    def _read(self, tmp_path, monkeypatch, text):
        path = tmp_path / "app.yaml"
        path.write_text(text, encoding="utf-8")
        monkeypatch.setattr(diagnose_mod, "ENV_TO_APP_YAML", {"prod": path})
        monkeypatch.setattr(diagnose_mod, "_pick_app_yaml", lambda p: p)
        return diagnose_mod._app_experiment_setting("prod")

    def test_it_reports_the_bundle_binding_as_such(self, tmp_path, monkeypatch):
        """`valueFrom: experiment` is the shipped default and the actual bug.

        It resolves to the bundle's classic experiment, whose spans upload to a
        host the Apps runtime cannot reach.
        """
        _, value = self._read(tmp_path, monkeypatch, VALUE_FROM_BLOCK)
        assert value == "valueFrom:experiment"

    def test_it_reads_a_literal_id(self, tmp_path, monkeypatch):
        _, value = self._read(tmp_path, monkeypatch, LITERAL_BLOCK)
        assert value == "647374559734684"

    def test_a_comment_between_name_and_value_does_not_hide_it(self, tmp_path, monkeypatch):
        _, value = self._read(tmp_path, monkeypatch, COMMENTED_BLOCK)
        assert value == "12345"

    def test_a_missing_block_is_reported_empty_rather_than_guessed(
        self, tmp_path, monkeypatch
    ):
        _, value = self._read(tmp_path, monkeypatch, "env:\n  - name: OTHER\n    value: 'x'\n")
        assert value == ""


class TestTheVerdict:
    """A clean run and a broken one have to be distinguishable by exit code."""

    def test_no_problems_means_success(self, capsys):
        assert diagnose_mod._report([]) == 0
        assert "correctly provisioned" in capsys.readouterr().out

    def test_problems_are_listed_and_fail(self, capsys):
        assert diagnose_mod._report(["delete the experiment", "re-run the deploy"]) == 1
        out = capsys.readouterr().out
        assert "1. delete the experiment" in out
        assert "2. re-run the deploy" in out


class TestTheTagsItChecks:
    """These tag names are MLflow's, and a typo would silently pass everything."""

    @pytest.mark.parametrize(
        "tag",
        [
            "mlflow.experiment.databricksTraceDestinationPath",
            "mlflow.experiment.databricksTelemetryDestinationId",
        ],
    )
    def test_the_diagnostic_and_the_provisioner_agree_on_tag_names(self, tag):
        assert tag in (diagnose_mod.LINK_TAG, diagnose_mod.DESTINATION_TAG)

    def test_link_and_destination_are_distinct_checks(self):
        # An experiment can carry the first without the second — that is the
        # state that reports as linked and discards every span.
        assert diagnose_mod.LINK_TAG != diagnose_mod.DESTINATION_TAG


class TestTheDeployWarnsWhenTraceStorageIsMissing:
    """A failure here is non-fatal, so the deploy has to say so at the end.

    Otherwise the last thing on screen is "Deploy complete" and the first sign
    of trouble is an empty experiment days later.
    """

    @pytest.fixture(scope="class")
    def deploy_sh(self):
        return (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")

    def test_the_success_marker_matches_what_the_provisioner_prints(self, deploy_sh):
        # Both wordings are success; either must silence the warning.
        assert "MLFLOW_EXPERIMENT_ID (updated|already correct)" in deploy_sh
        provisioner = (ROOT / "scripts" / "provision_trace_storage.py").read_text(
            encoding="utf-8"
        )
        assert "'updated' if changed else 'already correct'" in provisioner

    def test_the_warning_is_printed_after_deploy_complete(self, deploy_sh):
        complete = deploy_sh.index("=== Deploy complete")
        warning = deploy_sh.index("MLflow traces will be empty")
        assert warning > complete, "a warning above the summary is one nobody reads"

    def test_the_warning_names_the_usual_cause_and_the_permanence(self, deploy_sh):
        tail = deploy_sh[deploy_sh.index("=== Deploy complete") :]
        assert "SET_ME" in tail
        assert "permanent" in tail
