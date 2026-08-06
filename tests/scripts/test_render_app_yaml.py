"""Tests for scripts/render_app_yaml.py — the single-source-of-truth renderer
that derives ``app.<env>.yaml`` env values from a project-settings profile.

Runs on every deploy, so a regression here silently ships wrong UC/Lakebase/
model values to a fork. Covered here:

  * ``_is_placeholder`` classification
  * field → env mapping for a real profile
  * genie_space_id (optional) is skipped when it's a placeholder
  * MODEL_ORCHESTRATOR falls back to MODEL_SPECIALIST when unset, but honours
    an explicit override
  * "nothing to patch" when every source value is a placeholder
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import scripts.render_app_yaml as render  # noqa: E402

_APP_YAML = """\
env:
  - name: UC_CATALOG
    value: 'OLD'
  - name: UC_SCHEMA
    value: 'OLD'
  - name: PGHOST
    value: 'OLD'
  - name: GENIE_SPACE_ID
    value: 'OLD'
  - name: MODEL_SPECIALIST
    value: 'OLD'
  - name: MODEL_ADVANCED
    value: 'OLD'
  - name: MODEL_ORCHESTRATOR
    value: 'OLD'
  - name: MVM_SILVER_ENABLED
    value: 'OLD'
"""


def _write_settings(path: Path, dev_profile: dict) -> None:
    import yaml

    path.write_text(
        yaml.safe_dump({"profiles": {"line_manager_dev": dev_profile}}),
        encoding="utf-8",
    )


def _run(monkeypatch, tmp_path: Path, dev_profile: dict) -> str:
    app_yaml = tmp_path / "app.dev.yaml"
    app_yaml.write_text(_APP_YAML, encoding="utf-8")
    settings = tmp_path / "settings.yaml"
    _write_settings(settings, dev_profile)

    monkeypatch.setattr(render, "ROOT", tmp_path)
    monkeypatch.setattr(
        render, "DEFAULT_APP_YAML", {"dev": app_yaml, "prod": tmp_path / "app.yaml"}
    )
    monkeypatch.setattr(sys, "argv", ["render", "dev", "--settings", str(settings)])
    render.main()
    return app_yaml.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# _is_placeholder
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", ["", "   ", "SET_ME", "<your-host>"])
def test_is_placeholder_true(value):
    assert render._is_placeholder(value) is True


@pytest.mark.parametrize("value", ["real", "main", "databricks-gpt-5", 42])
def test_is_placeholder_false(value):
    assert render._is_placeholder(value) is False


# --------------------------------------------------------------------------- #
# main()
# --------------------------------------------------------------------------- #
def test_fills_fields_and_skips_placeholder_genie(monkeypatch, tmp_path):
    out = _run(
        monkeypatch,
        tmp_path,
        {
            "bundle_target": "line_manager_dev",
            "uc_catalog": "mycat",
            "uc_schema": "mysch",
            "lakebase_pg_host": "myhost",
            "genie_space_id": "SET_ME",  # placeholder → must be skipped
            "model_specialist_endpoint": "spec-ep",
            "model_advanced_endpoint": "adv-ep",
            "model_orchestrator_endpoint": "",  # empty → fallback
        },
    )
    assert "value: 'mycat'" in out
    assert "value: 'mysch'" in out
    assert "value: 'myhost'" in out
    assert "value: 'spec-ep'" in out
    assert "value: 'adv-ep'" in out
    # genie stays at its original template value (never clobbered by SET_ME)
    genie_line = [ln for ln in out.splitlines() if "GENIE_SPACE_ID" in ln]
    idx = out.splitlines().index(genie_line[0])
    assert out.splitlines()[idx + 1].strip() == "value: 'OLD'"


def test_unset_orchestrator_is_stripped_so_the_roster_can_choose(monkeypatch, tmp_path):
    """A stale MODEL_ORCHESTRATOR must be removed, not left to override the roster.

    Routing and synthesis now want different models, so this var is a legacy
    pin. It used to be defaulted to the specialist endpoint, which means every
    app.yaml written before the roster still declares it — and a leftover entry
    would silently force both roles back onto one endpoint.
    """
    out = _run(
        monkeypatch,
        tmp_path,
        {
            "bundle_target": "line_manager_dev",
            "model_specialist_endpoint": "spec-ep",
            "model_orchestrator_endpoint": "SET_ME",  # placeholder → unset
        },
    )
    assert "MODEL_ORCHESTRATOR" not in out
    # The rest of the file survives the removal intact.
    assert "value: 'spec-ep'" in out
    assert "MODEL_SPECIALIST" in out


def test_removal_takes_the_value_line_with_it(monkeypatch, tmp_path):
    # A dangling `value:` orphaned from its `- name:` would be invalid YAML.
    out = _run(
        monkeypatch,
        tmp_path,
        {"bundle_target": "line_manager_dev", "model_specialist_endpoint": "spec-ep"},
    )
    import yaml

    parsed = yaml.safe_load(out)
    names = [e["name"] for e in parsed["env"]]
    assert "MODEL_ORCHESTRATOR" not in names
    assert "MODEL_SPECIALIST" in names


def test_orchestrator_explicit_override_wins(monkeypatch, tmp_path):
    out = _run(
        monkeypatch,
        tmp_path,
        {
            "bundle_target": "line_manager_dev",
            "model_specialist_endpoint": "spec-ep",
            "model_orchestrator_endpoint": "orch-ep",
        },
    )
    lines = out.splitlines()
    idx = next(i for i, ln in enumerate(lines) if "MODEL_ORCHESTRATOR" in ln)
    assert lines[idx + 1].strip() == "value: 'orch-ep'"


# --------------------------------------------------------------------------- #
# model_roles — per-role overrides from settings
# --------------------------------------------------------------------------- #
def test_model_roles_become_per_role_env_vars(monkeypatch, tmp_path):
    out = _run(
        monkeypatch,
        tmp_path,
        {
            "bundle_target": "line_manager_dev",
            "model_specialist_endpoint": "spec-ep",
            "model_roles": {
                "downtime_analyst": "vendor-a-model",
                "intent_router": "vendor-b-model",
            },
        },
    )
    import yaml

    env = {e["name"]: e["value"] for e in yaml.safe_load(out)["env"]}
    assert env["MODEL_DOWNTIME_ANALYST"] == "vendor-a-model"
    assert env["MODEL_INTENT_ROUTER"] == "vendor-b-model"
    # Roles left out of the map get no entry, so the roster still picks for them.
    assert "MODEL_OEE_COACH" not in env


def test_unknown_role_is_rejected(monkeypatch, tmp_path):
    """A typo'd role would render an env var nothing reads — fail, don't warn."""
    with pytest.raises(SystemExit) as exc:
        _run(
            monkeypatch,
            tmp_path,
            {
                "bundle_target": "line_manager_dev",
                "model_roles": {"downtime_analyzer": "some-model"},
            },
        )
    assert "downtime_analyzer" in str(exc.value)


def test_placeholder_role_value_is_skipped(monkeypatch, tmp_path):
    out = _run(
        monkeypatch,
        tmp_path,
        {
            "bundle_target": "line_manager_dev",
            "model_specialist_endpoint": "spec-ep",
            "model_roles": {"oee_coach": "SET_ME", "general": "real-model"},
        },
    )
    import yaml

    env = {e["name"]: e["value"] for e in yaml.safe_load(out)["env"]}
    assert "MODEL_OEE_COACH" not in env
    assert env["MODEL_GENERAL"] == "real-model"


def test_role_dropped_from_settings_loses_its_env_entry(monkeypatch, tmp_path):
    """Removing an override from settings must un-pin the role in the app.

    Otherwise the previous render's env var survives in app.<env>.local.yaml and
    keeps forcing a model the operator has already deleted from config.
    """
    app_yaml = tmp_path / "app.dev.yaml"
    app_yaml.write_text(
        _APP_YAML + "  - name: MODEL_OEE_COACH\n    value: 'pinned-earlier'\n",
        encoding="utf-8",
    )
    settings = tmp_path / "settings.yaml"
    _write_settings(
        settings,
        {"bundle_target": "line_manager_dev", "model_specialist_endpoint": "spec-ep"},
    )
    monkeypatch.setattr(render, "ROOT", tmp_path)
    monkeypatch.setattr(
        render, "DEFAULT_APP_YAML", {"dev": app_yaml, "prod": tmp_path / "app.yaml"}
    )
    monkeypatch.setattr(sys, "argv", ["render", "dev", "--settings", str(settings)])
    render.main()

    import yaml

    env = {e["name"]: e["value"] for e in yaml.safe_load(app_yaml.read_text())["env"]}
    assert "MODEL_OEE_COACH" not in env


def test_model_roles_must_be_a_mapping(monkeypatch, tmp_path):
    with pytest.raises(SystemExit) as exc:
        _run(
            monkeypatch,
            tmp_path,
            {"bundle_target": "line_manager_dev", "model_roles": ["oee_coach"]},
        )
    assert "mapping" in str(exc.value)


def test_documented_roles_in_shipped_settings_are_all_valid():
    """The commented example in project-settings must name real roles.

    It is copy-paste material: a stale role name there would hand every user a
    config that fails their first render.
    """
    text = (ROOT / "config" / "project-settings.yaml").read_text(encoding="utf-8")
    known = set(render._known_roles())
    assert known, "roster import failed — the validation would be a no-op"

    documented = {
        line.split("#", 1)[1].strip().split(":", 1)[0].strip()
        for line in text.splitlines()
        if line.strip().startswith("#") and "databricks-" in line and ":" in line.split("#", 1)[1]
    }
    documented = {d for d in documented if d and " " not in d}
    assert documented, "no commented model_roles examples found to validate"
    assert documented <= known, f"unknown roles documented: {sorted(documented - known)}"


def test_nothing_to_patch_when_all_placeholders(monkeypatch, tmp_path, capsys):
    out = _run(
        monkeypatch,
        tmp_path,
        {
            "bundle_target": "line_manager_dev",
            "uc_catalog": "SET_ME",
            "uc_schema": "",
            "genie_space_id": "<fill-me>",
        },
    )
    assert "value: 'OLD'" in out  # unchanged
    assert "value: 'mycat'" not in out
    assert "nothing to patch" in capsys.readouterr().out.lower()


def test_missing_profile_raises(monkeypatch, tmp_path):
    app_yaml = tmp_path / "app.dev.yaml"
    app_yaml.write_text(_APP_YAML, encoding="utf-8")
    settings = tmp_path / "settings.yaml"
    _write_settings(settings, {"bundle_target": "some_other_target"})
    monkeypatch.setattr(render, "ROOT", tmp_path)
    monkeypatch.setattr(
        render, "DEFAULT_APP_YAML", {"dev": app_yaml, "prod": tmp_path / "app.yaml"}
    )
    monkeypatch.setattr(sys, "argv", ["render", "dev", "--settings", str(settings)])
    with pytest.raises(SystemExit):
        render.main()


class TestGovernedModelOwnershipFlag:
    """MVM_SILVER_ENABLED tells the app whether it WRITES the governed model.

    The governed schemas are catalog-level with no environment suffix, so two
    environments sharing a catalog share one model. Without this flag the app
    cannot tell its own rows from the other environment's, and prod shipped a
    Reconciliation panel scoring itself against dev's OEE.
    """

    def _profile(self, **over):
        base = {
            "bundle_target": "line_manager_dev",
            "uc_catalog": "mycat",
            "uc_schema": "mysch",
        }
        base.update(over)
        return base

    def test_true_renders_as_the_1_the_runtime_actually_checks(self, monkeypatch, tmp_path):
        """Both the app and the streaming job test `== "1"`, so a YAML True
        rendering as the string "True" would read as DISABLED."""
        out = _run(monkeypatch, tmp_path, self._profile(mvm_silver_enabled=True))
        assert "value: '1'" in out.split("MVM_SILVER_ENABLED")[1][:40]

    def test_false_renders_as_0_not_the_truthy_string_False(self, monkeypatch, tmp_path):
        out = _run(monkeypatch, tmp_path, self._profile(mvm_silver_enabled=False))
        block = out.split("MVM_SILVER_ENABLED")[1][:40]
        assert "value: '0'" in block
        assert "False" not in block

    def test_an_absent_setting_leaves_the_app_default_alone(self, monkeypatch, tmp_path):
        out = _run(monkeypatch, tmp_path, self._profile())
        assert "value: 'OLD'" in out.split("MVM_SILVER_ENABLED")[1][:40]


def test_both_app_templates_declare_every_rendered_env_var():
    """`_patch_yaml` only REPLACES values for keys already present.

    A field mapped in FIELD_TO_ENV but missing from the template renders as a
    silent no-op — the app then falls back to a default that may be wrong,
    with nothing in the deploy output to say so.
    """
    for name in ("app.yaml", "app.dev.yaml"):
        text = (ROOT / name).read_text(encoding="utf-8")
        for env_name in render.FIELD_TO_ENV.values():
            assert f"- name: {env_name}" in text, f"{name} is missing {env_name}"


class TestANewEnvVarReachesExistingLocalFiles:
    """`_patch_yaml` only replaces keys already present in the file.

    Users deploy from a gitignored app.<env>.local.yaml copied from the template
    once. Every env var added afterwards was therefore a silent no-op for them:
    the deploy printed the intended value, the app never got it. Dev hit exactly
    this — streaming silver ON, but the app never received MVM_SILVER_ENABLED,
    so Reconciliation disowned the model dev was writing.
    """

    _STALE = "env:\n  - name: UC_CATALOG\n    value: 'OLD'\n"

    def _render(self, monkeypatch, tmp_path, text, profile):
        app_yaml = tmp_path / "app.dev.yaml"
        app_yaml.write_text(text, encoding="utf-8")
        settings = tmp_path / "settings.yaml"
        _write_settings(settings, profile)
        monkeypatch.setattr(render, "ROOT", tmp_path)
        monkeypatch.setattr(
            render, "DEFAULT_APP_YAML", {"dev": app_yaml, "prod": tmp_path / "app.yaml"}
        )
        monkeypatch.setattr(sys, "argv", ["render", "dev", "--settings", str(settings)])
        render.main()
        return app_yaml.read_text(encoding="utf-8")

    def test_a_var_absent_from_the_file_is_inserted(self, monkeypatch, tmp_path):
        out = self._render(
            monkeypatch, tmp_path, self._STALE,
            {"bundle_target": "line_manager_dev", "uc_catalog": "c",
             "mvm_silver_enabled": True},
        )
        assert "- name: MVM_SILVER_ENABLED" in out
        assert "value: '1'" in out.split("MVM_SILVER_ENABLED")[1][:40]

    def test_the_inserted_block_is_valid_yaml_with_the_right_shape(self, monkeypatch, tmp_path):
        import yaml

        out = self._render(
            monkeypatch, tmp_path, self._STALE,
            {"bundle_target": "line_manager_dev", "uc_catalog": "c",
             "mvm_silver_enabled": False, "uc_schema": "s"},
        )
        parsed = yaml.safe_load(out)
        by_name = {e["name"]: e["value"] for e in parsed["env"]}
        assert by_name["MVM_SILVER_ENABLED"] == "0"
        assert by_name["UC_CATALOG"] == "c"
        assert by_name["UC_SCHEMA"] == "s"

    def test_existing_vars_are_not_duplicated(self, monkeypatch, tmp_path):
        out = self._render(
            monkeypatch, tmp_path, _APP_YAML,
            {"bundle_target": "line_manager_dev", "uc_catalog": "c",
             "mvm_silver_enabled": True},
        )
        assert out.count("- name: MVM_SILVER_ENABLED") == 1
        assert out.count("- name: UC_CATALOG") == 1

    def test_trailing_content_after_the_env_block_survives(self, monkeypatch, tmp_path):
        import yaml

        text = self._STALE + "command:\n  - ./run.sh\n"
        out = self._render(
            monkeypatch, tmp_path, text,
            {"bundle_target": "line_manager_dev", "uc_catalog": "c",
             "mvm_silver_enabled": True},
        )
        parsed = yaml.safe_load(out)
        assert parsed["command"] == ["./run.sh"]
        assert {"name": "MVM_SILVER_ENABLED", "value": "1"} in parsed["env"]


class TestTheEnvBlockIsParsedAtAll:
    """Structure this function cannot read must fail loudly, not quietly.

    Its whole reason to exist is that a silently dropped variable is worse than
    a broken deploy: the log prints the intended value, the app never gets it,
    and the symptom surfaces days later as a feature "not working". Two parsing
    gaps put that exact no-op back one level up.
    """

    def _write(self, tmp_path, text):
        path = tmp_path / "app.yaml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_a_comment_on_the_env_line_does_not_swallow_every_variable(self, tmp_path):
        path = self._write(tmp_path, "env:  # app env vars\n  - name: UC_CATALOG\n    value: 'OLD'\n")
        assert render._insert_missing_env(path, {"MVM_SILVER_ENABLED": "1"}) == ["MVM_SILVER_ENABLED"]
        assert "MVM_SILVER_ENABLED" in path.read_text(encoding="utf-8")

    def test_a_comment_on_a_name_line_does_not_produce_a_duplicate(self, tmp_path):
        # The key IS declared; failing to see it appended a second entry for the
        # same name, and which one the platform honours is not defined.
        path = self._write(tmp_path, "env:\n  - name: PGHOST  # prod host\n    value: 'OLD'\n")
        assert render._insert_missing_env(path, {"PGHOST": "newhost"}) == []
        assert path.read_text(encoding="utf-8").count("- name: PGHOST") == 1

    def test_no_env_block_at_all_stops_the_deploy(self, tmp_path):
        # Returning [] was indistinguishable from "nothing was missing".
        path = self._write(tmp_path, "resources: []\n")
        with pytest.raises(SystemExit) as excinfo:
            render._insert_missing_env(path, {"MVM_SILVER_ENABLED": "1"})
        assert "MVM_SILVER_ENABLED" in str(excinfo.value)

    def test_nothing_missing_needs_no_env_block(self, tmp_path):
        # Only complain when there is something we would have dropped.
        path = self._write(tmp_path, "resources: []\n")
        assert render._insert_missing_env(path, {}) == []


class TestVectorSearchIsDerivedFromSettings:
    """VS_ENDPOINT is a billing switch, so it follows a different rule.

    Everywhere else an empty setting means "not filled in yet" and is skipped,
    so a half-written settings file cannot blank out a working app. For Vector
    Search that rule inverts: clearing the endpoint is precisely how an
    operator turns the feature — and an hourly bill — off, and skipping the
    empty would leave the app pointed at an endpoint the settings no longer
    name.
    """

    def test_naming_an_endpoint_turns_the_feature_and_its_billing_line_on(
        self, monkeypatch, tmp_path
    ):
        out = _run(monkeypatch, tmp_path, {"bundle_target": "line_manager_dev", "vector_search_endpoint": "prodline-copilot-vs"})
        assert "prodline-copilot-vs" in out
        # Derived, never hand-set: the two drifting apart is how the cost panel
        # came to bill for an endpoint nobody had provisioned.
        assert "OPS_VS_ENDPOINT_ALWAYS_ON" in out
        assert "'true'" in out.split("OPS_VS_ENDPOINT_ALWAYS_ON")[1][:40]

    def test_clearing_the_setting_turns_it_off_rather_than_being_ignored(
        self, monkeypatch, tmp_path
    ):
        out = _run(monkeypatch, tmp_path, {"bundle_target": "line_manager_dev", "vector_search_endpoint": ""})
        block = out.split("VS_ENDPOINT")[1][:40]
        assert "''" in block, "an explicit empty must reach the app, not be skipped"
        assert "'false'" in out.split("OPS_VS_ENDPOINT_ALWAYS_ON")[1][:40]

    def test_a_profile_that_never_mentions_it_is_left_alone(self, monkeypatch, tmp_path):
        """Older local settings predate the key; do not touch what they omit."""
        out = _run(monkeypatch, tmp_path, {"bundle_target": "line_manager_dev", "uc_catalog": "cat"})
        assert "VS_ENDPOINT" not in out

    def test_an_unfilled_placeholder_does_not_provision_anything(self, monkeypatch, tmp_path):
        out = _run(monkeypatch, tmp_path, {"bundle_target": "line_manager_dev", "vector_search_endpoint": "SET_ME"})
        assert "SET_ME" not in out.split("VS_ENDPOINT")[1][:40]
        assert "'false'" in out.split("OPS_VS_ENDPOINT_ALWAYS_ON")[1][:40]
