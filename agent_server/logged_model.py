"""Attach this app's traces to an MLflow LoggedModel.

The agent is served by this app, not by Model Serving, so there is no model
artifact to log and nothing to deploy from the registry. What is still worth
having is the *identity*: an external LoggedModel gives every trace a model to
hang off, which is what lets you ask "did answers get better after the release"
instead of eyeballing two experiment views side by side. The configuration that
shaped those answers — which endpoint served which role, where the prompts came
from — is recorded as params on the model rather than being reconstructed from
memory months later.

``set_active_model(name=...)`` creates the model on first boot and reuses the
latest thereafter, so this needs no id threaded through the deploy. Set
``AGENT_MODEL_VERSION`` (the deploy knows the git SHA) to get one model per
release and comparable trace sets; leave it unset and every deploy shares one.

Every failure here is swallowed. Tracing is observability: an app that will not
start because it could not name a model is a worse outcome than traces that are
not linked to one.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_NAME = "prodline_copilot"


def model_name() -> str:
    """Name for this deployment's LoggedModel."""
    base = os.environ.get("AGENT_MODEL_NAME", "").strip() or DEFAULT_NAME
    version = os.environ.get("AGENT_MODEL_VERSION", "").strip()
    return f"{base}-{version}" if version else base


def _params() -> dict[str, str]:
    """What we would want to know when comparing two releases' traces."""
    from agent_server.model_roster import resolved_roster
    from agent_server.prompts.loader import ALIAS, registry_enabled
    from agent_server.uc import get_uc_location

    loc = get_uc_location()
    params = {
        "prompt_source": f"registry@{ALIAS}" if registry_enabled() else "code_constants",
        "uc_catalog": loc.catalog,
        "uc_schema": loc.schema,
    }
    # The endpoint actually resolved, not the preferred one: a workspace that
    # substituted a model is the case where this param earns its keep.
    for assignment in resolved_roster():
        params[f"endpoint.{assignment['role']}"] = str(assignment["endpoint"])
    return params


def activate() -> str | None:
    """Make this deployment's LoggedModel active. Returns its id, or None.

    Params are written only when the model has none, because they are immutable
    once set and a restart must not turn a benign no-op into a startup error.
    """
    try:
        import mlflow

        active = mlflow.set_active_model(name=model_name())
        model_id = getattr(active, "model_id", None)
        if not model_id:
            return None

        try:
            existing = mlflow.get_logged_model(model_id)
            if not getattr(existing, "params", None):
                mlflow.log_model_params(_params(), model_id=model_id)
        except Exception as exc:  # noqa: BLE001 - params are a nicety, the link is not
            logger.warning("could not record model params: %s", exc)

        logger.info("traces linked to LoggedModel %s (%s)", model_name(), model_id)
        return model_id
    except Exception as exc:  # noqa: BLE001 - never block startup over tracing
        logger.warning("could not set an active MLflow model: %s", exc)
        return None
