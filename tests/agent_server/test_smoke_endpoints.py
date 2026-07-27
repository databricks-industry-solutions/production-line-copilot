"""Smoke tests for the FastAPI agent server.

These guard the public contract that ``scripts/start-app.sh`` relies on —
the server boots without bringing in workspace-bound side effects, ``/health``
returns 200, and the deterministic agent router maps every incident scenario
to a known specialist.

Run-time keep these cheap: we instantiate the FastAPI app once via
``TestClient`` (no real network), monkey-patch the SQL startup probe to a
no-op, and exercise only side-effect-free routes.
"""

from __future__ import annotations

import importlib
from typing import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(module_monkeypatch_session) -> Iterator[TestClient]:
    """Bootstrapped FastAPI ``TestClient`` for the agent server.

    Module-scoped so all smoke tests share the same import + startup
    cost (~2 s). The TestClient context manager fires the FastAPI
    ``on_event("startup")`` hooks; we patch the SQL startup probe to a
    no-op so the test doesn't try to talk to the workspace.
    """
    # Patch the heaviest startup side-effects to no-ops BEFORE importing
    # the module — start_server captures references at import time.
    from agent_server import start_server as ss

    module_monkeypatch_session.setattr(ss, "_startup_sql_probe", lambda: None, raising=False)
    # Re-import is not needed; the FastAPI app object lives at module scope
    # and on_event hooks fire when TestClient enters its context manager.
    with TestClient(ss.app) as c:
        yield c


@pytest.fixture(scope="module")
def module_monkeypatch_session():
    """Module-scoped equivalent of the function-scoped ``monkeypatch`` fixture.

    Pytest's built-in ``monkeypatch`` is function-scoped, so it can't share
    state with module-scoped fixtures. This minimal stand-in lets us undo
    patches in module teardown.
    """
    from _pytest.monkeypatch import MonkeyPatch

    mp = MonkeyPatch()
    try:
        yield mp
    finally:
        mp.undo()


def test_health_returns_200_and_ok_body(client: TestClient) -> None:
    """``GET /health`` is the Databricks Apps liveness probe — must be cheap and stable."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_openapi_loads_and_lists_core_routes(client: TestClient) -> None:
    """OpenAPI loads cleanly (catches Pydantic / type-annotation regressions)
    and the core operator routes are registered.
    """
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    spec = resp.json()
    paths = spec.get("paths") or {}
    for route in ("/health", "/health/deep", "/invocations", "/sim/status"):
        assert route in paths, f"missing route in OpenAPI spec: {route}"


def test_cors_default_is_locked_down() -> None:
    """Default CORS policy (no AGENT_CORS_ALLOW_ORIGINS) blocks cross-origin
    requests. We import start_server fresh under a clean env and check the
    middleware config rather than spinning up a second TestClient.
    """
    import os

    # Snapshot + clear the env var, reload the module, inspect, restore.
    original = os.environ.pop("AGENT_CORS_ALLOW_ORIGINS", None)
    try:
        from agent_server import start_server as ss

        ss = importlib.reload(ss)
        # CORSMiddleware is added; inspect the stack for its allow_origins.
        # FastAPI keeps user_middleware as a list of Middleware objects.
        cors_origins: list[str] | None = None
        for mw in ss.app.user_middleware:
            if mw.cls.__name__ == "CORSMiddleware":
                # kwargs is a dict on the Middleware class (Starlette / FastAPI).
                cors_origins = mw.kwargs.get("allow_origins") or []
                break
        assert cors_origins is not None, "CORSMiddleware not registered"
        assert cors_origins == [], (
            f"Default CORS policy must be empty, got {cors_origins!r}. Set "
            "AGENT_CORS_ALLOW_ORIGINS explicitly to open it up."
        )
    finally:
        if original is not None:
            os.environ["AGENT_CORS_ALLOW_ORIGINS"] = original


def test_cors_env_var_opens_up() -> None:
    """``AGENT_CORS_ALLOW_ORIGINS`` set to ``*`` switches the middleware to
    accept any origin.
    """
    import os

    original = os.environ.get("AGENT_CORS_ALLOW_ORIGINS")
    os.environ["AGENT_CORS_ALLOW_ORIGINS"] = "*"
    try:
        from agent_server import start_server as ss

        ss = importlib.reload(ss)
        cors_origins: list[str] | None = None
        for mw in ss.app.user_middleware:
            if mw.cls.__name__ == "CORSMiddleware":
                cors_origins = mw.kwargs.get("allow_origins") or []
                break
        assert cors_origins == ["*"]
    finally:
        if original is None:
            os.environ.pop("AGENT_CORS_ALLOW_ORIGINS", None)
        else:
            os.environ["AGENT_CORS_ALLOW_ORIGINS"] = original
        # Re-reload so subsequent tests see the default (empty) policy.
        from agent_server import start_server as _ss  # noqa: F401
        importlib.reload(_ss)


# ── Agent routing smoke tests ───────────────────────────────────────────────
#
# These reload ``agent_server.agent`` defensively because other tests in the
# suite monkeypatch the specialist factory / _get_specialists() helper and
# don't always unwind their patches before the next test imports the module.
# A fresh reload guarantees we see the real registry, not a leaked mock.


def _agent_module_fresh():
    import importlib

    from agent_server import agent as _agent

    return importlib.reload(_agent)


def test_routing_map_covers_known_scenarios() -> None:
    """Every scenario id that simulator.scenarios.py emits should resolve to
    a registered specialist via ``INCIDENT_TO_SPECIALIST``. A new scenario
    without a mapping silently falls through to LLM intent classification —
    surfacing that gap here is cheaper than discovering it in production.
    """
    agent = _agent_module_fresh()
    specialists = agent._get_specialists()
    for scenario_id, specialist_name in agent.INCIDENT_TO_SPECIALIST.items():
        assert specialist_name in specialists, (
            f"incident routing map references unknown specialist "
            f"{specialist_name!r} for scenario {scenario_id!r}"
        )


def test_route_by_active_incident_returns_specialist_for_known_scenario() -> None:
    """Active incident with a known scenario_id → deterministic specialist."""
    agent = _agent_module_fresh()
    sample_scenario, expected_specialist = next(iter(agent.INCIDENT_TO_SPECIALIST.items()))
    context = {"active_incidents": [{"scenario_id": sample_scenario}]}
    assert agent._route_by_active_incident(context) == expected_specialist


def test_route_by_active_incident_returns_none_when_no_active_incident() -> None:
    """Empty / missing ``active_incidents`` → LLM classifier path (None)."""
    agent = _agent_module_fresh()
    assert agent._route_by_active_incident({}) is None
    assert agent._route_by_active_incident({"active_incidents": []}) is None
