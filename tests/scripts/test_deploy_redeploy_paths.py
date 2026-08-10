"""Static guards on the redeploy paths in scripts/deploy.sh.

`render_app_yaml.py` writes ``app.<env>.local.yaml``, but the bundle uploads
``app.yaml`` — so a re-render only reaches the running app if
``do_app_yaml_swap`` runs between the render and the bundle deploy.

Skipping the swap fails silently in the worst way: the deploy reports success,
the app restarts, and it comes up with the env var still unset. That happened
once with the governed domain space ids, so the ordering is pinned here rather
than left to reviewer memory. Shell is hard to unit-test; reading the source is
worth more than nothing.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SH = ROOT / "scripts" / "deploy.sh"


@pytest.fixture(scope="module")
def lines() -> list[str]:
    return DEPLOY_SH.read_text(encoding="utf-8").splitlines()


def _code_lines(lines: list[str]) -> list[tuple[int, str]]:
    """Line numbers and text, skipping comments and blanks."""
    out = []
    for i, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if stripped and not stripped.startswith("#"):
            out.append((i, stripped))
    return out


def test_every_rerender_swaps_before_deploying(lines):
    """A render followed by a bundle deploy must have a swap in between."""
    code = _code_lines(lines)
    renders = [n for n, text in code if "render_app_yaml.py" in text]
    assert renders, "no render_app_yaml.py calls found — has deploy.sh moved?"

    for render_line in renders:
        # The next bundle deploy after this render, if any.
        following = [n for n, text in code if n > render_line and "run_bundle_deploy" in text]
        if not following:
            continue
        deploy_line = following[0]
        swaps = [
            n
            for n, text in code
            if render_line < n < deploy_line and "do_app_yaml_swap" in text
        ]
        assert swaps, (
            f"render_app_yaml.py at line {render_line} is followed by "
            f"run_bundle_deploy at line {deploy_line} with no do_app_yaml_swap "
            "between them — the redeploy would ship the pre-render app.yaml"
        )


def test_domain_genie_block_provisions_all_four_domains(lines):
    """All four governed domains must be in the provisioning loop."""
    text = "\n".join(lines)
    match = re.search(r"for DOMAIN in ([a-z ]+); do", text)
    assert match, "the per-domain Genie provisioning loop is missing"
    assert match.group(1).split() == ["asset", "quality", "inventory", "production"]


def test_domain_spaces_are_granted_to_the_app_sp(lines):
    """A space the app SP cannot run is a space the specialist cannot query."""
    text = "\n".join(lines)
    block = text.split("for DOMAIN in", 1)[1]
    assert "permissions/genie/$DOMAIN_ID" in block
    assert "CAN_RUN" in block.split("permissions/genie/$DOMAIN_ID")[0][-400:]


def test_newly_created_domain_spaces_trigger_a_redeploy(lines):
    """Ids captured after the app deployed only reach it via a second deploy."""
    text = "\n".join(lines)
    assert "DOMAIN_SPACES_CREATED=1" in text, "creation must flag that a redeploy is needed"
    block = text.split('if [[ "$DOMAIN_SPACES_CREATED" == "1" ]]; then', 1)
    assert len(block) == 2, "no redeploy guarded on DOMAIN_SPACES_CREATED"
    assert "databricks apps deploy" in block[1]


def test_trace_storage_is_provisioned_before_the_app_ever_runs(lines):
    """A UC trace destination only attaches to an experiment with no traces.

    Once the app has written a single trace to an experiment, that experiment
    can never be converted — the API rejects it outright. So provisioning has
    to happen before the app deploy, not merely somewhere in the script. Get
    this backwards and the deploy still succeeds while tracing is quietly
    broken for the life of the environment.
    """
    code = _code_lines(lines)
    provision = [n for n, t in code if "provision_trace_storage.py" in t]
    assert provision, "no provision_trace_storage.py call found in deploy.sh"

    app_deploys = [n for n, t in code if "databricks apps deploy" in t]
    assert app_deploys, "no app deploy found — has deploy.sh moved?"
    assert min(provision) < min(app_deploys), (
        f"provision_trace_storage.py runs at line {min(provision)}, after the "
        f"app deploy at {min(app_deploys)}; the experiment will already hold "
        "traces and can never be linked to Unity Catalog"
    )


def test_trace_storage_runs_after_the_app_sp_is_known(lines):
    """Without the SP there is nobody to grant, and the app cannot write."""
    code = _code_lines(lines)
    provision = min(n for n, t in code if "provision_trace_storage.py" in t)
    captures = [n for n, t in code if "APP_SP_ID=" in t]
    assert captures and min(captures) < provision, (
        "provision_trace_storage.py runs before APP_SP_ID is captured, so the "
        "trace tables are created with no grant to the app"
    )


def test_a_new_experiment_id_triggers_a_redeploy(lines):
    """The id lands in app.<env>.local.yaml, which only ships on a deploy.

    Comments are stripped before the window is measured. This read a fixed
    slice of raw source until twice in a row a comment explaining that step
    pushed the wiring past the 600th character and failed a test about
    redeploys. The invariant is unchanged; it is just no longer possible to
    break it by writing prose.
    """
    text = "\n".join(t for _, t in _code_lines(lines))
    block = text.split("provision_trace_storage.py", 1)[1][:600]
    assert "MLFLOW_EXPERIMENT_ID updated" in block and "NEEDS_REDEPLOY=1" in block


def test_steps_needing_project_deps_use_the_project_interpreter(lines):
    """System python3 has neither mlflow nor the SDK.

    Both of these steps are deliberately non-fatal, so calling them with the
    wrong interpreter does not fail the deploy — it prints a warning in a few
    hundred lines of output and moves on, leaving prompts unpublished and trace
    storage unprovisioned. That is how the first run of this step failed.
    """
    for script in ("register_prompts.py", "provision_trace_storage.py"):
        # Match the invocation by its path, not by the bare filename: the
        # filename also appears in echoed headings and in the warning text that
        # tells the reader which step to look at, and neither is a call.
        calls = [
            t for _, t in _code_lines(lines) if f"scripts/{script}" in t and "echo" not in t
        ]
        assert calls, f"{script} is not called by deploy.sh"
        for call in calls:
            assert "PROJECT_PY" in call, (
                f"{script} is invoked with the system interpreter ({call!r}); "
                "it needs the project venv, which deploy.sh exposes as PROJECT_PY"
            )


class TestTheVectorSearchGateReadsTheShippedConfig:
    """Which app.yaml the gate reads decides whether the step runs at all.

    `app.yaml` is the committed prod template and is deliberately blank, so a
    hardcoded read of it reported "not enabled" for every environment —
    including one whose gitignored `app.<env>.local.yaml` had just been
    rendered with an endpoint name a few lines earlier. The step then skipped,
    the app kept its keyword fallback, and the deploy log said so in a line
    nobody reads.
    """

    def test_the_gate_reads_app_source_yaml_not_a_fixed_filename(self, lines):
        block = "\n".join(lines)
        gate = block.split("VS_ENDPOINT_NAME=$(", 1)
        assert len(gate) == 2, "the Vector Search gate has moved or been renamed"
        snippet = gate[1].split(")\n", 1)[0]
        assert "$APP_SOURCE_YAML" in snippet, (
            "the gate must read the file this deploy ships; a literal app.yaml "
            "is blank in the repo and skips provisioning on every environment"
        )
        assert "open('app.yaml')" not in snippet

    def test_the_operator_is_told_the_first_run_is_slow(self, lines):
        """`--wait` blocks with no output; without a warning it looks hung."""
        block = "\n".join(lines)
        vs = block.split("Provisioning Vector Search", 1)[1][:1600]
        assert "15-40" in vs and "minutes" in vs, (
            "no duration hint near the provisioning step — someone will Ctrl-C it"
        )
        assert "one-time" in vs, "say it is per-environment, not per-deploy"
