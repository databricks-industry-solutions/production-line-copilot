"""A code default must not contradict the value every app yaml ships.

``os.environ.get(NAME, default)`` is what runs when configuration is missing —
a bare ``uvicorn``, a container built without the rendered yaml, a test harness,
a notebook. So the default is not a throwaway second argument; it is the
behaviour of the system in its least-configured state, and three of them had
quietly drifted to the opposite of what the deployment says:

  * ``OPS_DEFAULT_ROLE`` shipped as ``viewer`` and defaulted to ``operator``,
    promoting every unrecognised caller — including the literal ``"unknown"``
    that ``_ops_user`` returns when no identity header arrives — into the role
    that may POST ``/ops/control/action``.
  * ``SSE_BUFFER_SECONDS`` shipped as ``2`` and defaulted to ``20``, opening the
    stream eighteen seconds further into the past and replaying forward, so the
    dashboard looked live and lagged.
  * ``OPS_VS_ENDPOINT_ALWAYS_ON`` shipped as ``false`` and defaulted to ``true``,
    billing hourly Vector Search against a configuration that names no endpoint.

None of the three was reachable from a normal deploy, which is why all three
survived. This test does not care about the three; it enforces the rule, so the
fourth is caught the day it is written.

Two defaults are legitimately allowed to differ. An empty string is the explicit
"not configured" sentinel — ``/serving/governed`` answers 503 on it rather than
guessing a schema — and a yaml value that is still a placeholder (``SET_ME``, a
``<...>`` sample, an unexpanded ``${VAR}``) is not a value at all.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]

# Only literal string defaults; a computed default (see OPS_VS_ENDPOINT_ALWAYS_ON,
# which now reads VS_ENDPOINT) cannot contradict a constant and is not matched.
_GET = re.compile(
    r'os\.environ\.get\(\s*["\']([A-Z][A-Z0-9_]*)["\']\s*,\s*["\']([^"\']*)["\']\s*\)'
)


def _is_placeholder(value: str) -> bool:
    v = value.strip()
    return (
        not v
        or v == "SET_ME"
        or "${" in v
        or ("<" in v and ">" in v)
    )


def _shipped(app_yaml: str) -> dict[str, str]:
    doc = yaml.safe_load((ROOT / app_yaml).read_text()) or {}
    return {e["name"]: str(e.get("value")) for e in doc.get("env", []) if "value" in e}


def _code_defaults() -> list[tuple[str, str, str]]:
    out = []
    for path in sorted((ROOT / "agent_server").rglob("*.py")):
        for m in _GET.finditer(path.read_text()):
            out.append((m.group(1), m.group(2), str(path.relative_to(ROOT))))
    return out


def _is_dev_suffixed(dev_value: str, prod_value: str) -> bool:
    """Dev exists to be the same thing with a suffix.

    ``shift_1`` → ``shift_1_dev``, ``line_manager_streaming`` →
    ``line_manager_streaming_dev``. The code default tracks the unsuffixed prod
    value and dev overrides it, which is the arrangement working, not drifting.
    """
    return prod_value != "" and dev_value in (f"{prod_value}_dev", f"{prod_value}-dev")


@pytest.mark.parametrize("app_yaml", ["app.yaml", "app.dev.yaml"])
def test_no_code_default_contradicts_the_shipped_value(app_yaml):
    shipped = _shipped(app_yaml)
    prod = _shipped("app.yaml")
    conflicts = []
    for name, default, where in _code_defaults():
        if name not in shipped or _is_placeholder(shipped[name]):
            continue
        if default == "":  # explicit "not configured" sentinel
            continue
        if _is_dev_suffixed(shipped[name], prod.get(name, "")):
            continue
        if default.strip().lower() != shipped[name].strip().lower():
            conflicts.append(f"{name}: code {default!r} vs {app_yaml} {shipped[name]!r} ({where})")
    assert not conflicts, (
        "a default is what runs when the yaml is absent, so it must not say the "
        "opposite of the yaml:\n  " + "\n  ".join(conflicts)
    )


def test_the_three_that_prompted_this_stay_fixed():
    defaults = {name: default for name, default, _ in _code_defaults()}
    assert defaults.get("OPS_DEFAULT_ROLE") == "viewer"
    assert defaults.get("SSE_BUFFER_SECONDS") == "2"
    assert "OPS_VS_ENDPOINT_ALWAYS_ON" not in defaults, (
        "it should be derived from VS_ENDPOINT, not a constant"
    )


def test_an_unidentified_caller_is_not_an_operator(monkeypatch):
    for key in ("OPS_DEFAULT_ROLE", "OPS_ADMIN_EMAILS", "OPS_OPERATOR_EMAILS"):
        monkeypatch.delenv(key, raising=False)
    from agent_server.start_server import _ops_role

    # "unknown" is what _ops_user returns when no identity header arrives.
    assert _ops_role("unknown") == "viewer"
    assert _ops_role("nobody@example.com") == "viewer"


def test_the_cost_panel_bills_vector_search_only_when_one_is_configured(monkeypatch):
    """The derived default, read off the same signal render_app_yaml.py uses."""
    src = (ROOT / "agent_server/start_server.py").read_text()
    line = next(ln for ln in src.splitlines() if "_vs_default =" in ln)
    assert "VS_ENDPOINT" in line, "the billing default must follow the endpoint"
    assert '"true" if' in line and 'else "false"' in line
