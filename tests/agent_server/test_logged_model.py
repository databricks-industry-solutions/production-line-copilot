"""Tests for ``agent_server/logged_model.py``.

The module runs at import time of ``agent_server.agent``, in a Databricks App
whose workspace may have no experiment configured, no permission on it, or no
network path to it. So the behaviour under test is mostly "what happens when
MLflow says no", and the answer must always be "the app still starts".
"""
from __future__ import annotations

from agent_server import logged_model


class TestName:
    def test_defaults_to_a_stable_name(self, monkeypatch):
        monkeypatch.delenv("AGENT_MODEL_NAME", raising=False)
        monkeypatch.delenv("AGENT_MODEL_VERSION", raising=False)
        assert logged_model.model_name() == logged_model.DEFAULT_NAME

    def test_a_version_makes_releases_comparable(self, monkeypatch):
        monkeypatch.delenv("AGENT_MODEL_NAME", raising=False)
        monkeypatch.setenv("AGENT_MODEL_VERSION", "0.3.6")
        assert logged_model.model_name() == "prodline_copilot-0.3.6"

    def test_blank_env_is_not_a_name(self, monkeypatch):
        monkeypatch.setenv("AGENT_MODEL_NAME", "   ")
        monkeypatch.setenv("AGENT_MODEL_VERSION", "  ")
        assert logged_model.model_name() == logged_model.DEFAULT_NAME


class FakeActive:
    def __init__(self, model_id="m-1"):
        self.model_id = model_id


class TestActivate:
    def test_links_traces_and_records_params_on_first_boot(self, monkeypatch):
        import mlflow

        logged: dict = {}
        monkeypatch.setattr(mlflow, "set_active_model", lambda **kw: FakeActive())
        monkeypatch.setattr(
            mlflow, "get_logged_model", lambda model_id: type("M", (), {"params": {}})()
        )
        monkeypatch.setattr(
            mlflow,
            "log_model_params",
            lambda params, model_id=None: logged.update(params),
        )

        assert logged_model.activate() == "m-1"
        assert logged["prompt_source"] in {"code_constants"} or logged[
            "prompt_source"
        ].startswith("registry@")
        assert any(k.startswith("endpoint.") for k in logged)

    def test_a_restart_does_not_rewrite_immutable_params(self, monkeypatch):
        """Params are write-once; re-logging them would raise on every restart."""
        import mlflow

        calls = []
        monkeypatch.setattr(mlflow, "set_active_model", lambda **kw: FakeActive())
        monkeypatch.setattr(
            mlflow,
            "get_logged_model",
            lambda model_id: type("M", (), {"params": {"prompt_source": "code_constants"}})(),
        )
        monkeypatch.setattr(
            mlflow, "log_model_params", lambda params, model_id=None: calls.append(params)
        )

        logged_model.activate()
        assert calls == []

    def test_an_mlflow_failure_never_blocks_startup(self, monkeypatch):
        import mlflow

        def boom(**kwargs):
            raise RuntimeError("no experiment configured")

        monkeypatch.setattr(mlflow, "set_active_model", boom)
        assert logged_model.activate() is None

    def test_a_params_failure_still_leaves_traces_linked(self, monkeypatch):
        """The link is the point; the params are a nicety."""
        import mlflow

        monkeypatch.setattr(mlflow, "set_active_model", lambda **kw: FakeActive("m-2"))
        monkeypatch.setattr(
            mlflow, "get_logged_model", lambda model_id: type("M", (), {"params": {}})()
        )

        def boom(params, model_id=None):
            raise RuntimeError("permission denied")

        monkeypatch.setattr(mlflow, "log_model_params", boom)
        assert logged_model.activate() == "m-2"

    def test_a_model_without_an_id_is_not_reported_as_linked(self, monkeypatch):
        import mlflow

        monkeypatch.setattr(mlflow, "set_active_model", lambda **kw: FakeActive(None))
        assert logged_model.activate() is None
