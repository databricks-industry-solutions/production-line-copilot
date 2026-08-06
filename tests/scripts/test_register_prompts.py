"""Tests for ``scripts/register_prompts.py``.

The property under test is idempotency. ``scripts/deploy.sh`` runs this script
on every deploy, so a guard that fails open mints a fresh version of all eleven
prompts each time and buries the versions where the wording actually changed.

That is not a hypothetical: the first implementation looked the latest version
up with ``prompts:/<name>@latest``, which the ``load_prompt`` docstring
advertises but which resolves to None against Unity Catalog even when versions
exist. Every prompt was therefore re-registered on every run. The fakes below
model the real backend closely enough to have caught it — in particular
``search_prompt_versions`` raises rather than returning empty for a prompt that
was never registered, which is what the first run of a fresh environment hits.

No network: MLflow's entry points are monkeypatched on the module objects, the
same way ``tests/agent_server/test_span_outputs.py`` does it.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("mlflow")

from scripts.register_prompts import (  # noqa: E402
    UNREADABLE,
    _is_missing,
    _latest,
    _tags,
    register_all,
)

CAT, SCH = "cat", "sch"


@dataclass
class FakeVersion:
    version: int
    template: str


class FakeRestError(Exception):
    """Shaped like the RestException a missing prompt raises."""


class FakeClient:
    """Enough of MlflowClient to exercise the lookup path.

    ``search_prompt_versions`` raises for an unknown name rather than returning
    an empty list, matching the observed backend.
    """

    def __init__(self, store: dict[str, list[FakeVersion]]):
        self.store = store

    def search_prompt_versions(self, name: str):
        if name not in self.store:
            raise FakeRestError(f"NOT_FOUND: Prompt with name {name} does not exist.")
        return list(self.store[name])


class Recorder:
    """Captures register/alias calls so a test can assert what was written."""

    def __init__(self, store: dict[str, list[FakeVersion]]):
        self.store = store
        self.registered: list[tuple[str, str]] = []
        self.aliased: list[tuple[str, str, int]] = []

    def register_prompt(self, name, template, commit_message=None, tags=None):
        versions = self.store.setdefault(name, [])
        new = FakeVersion(version=len(versions) + 1, template=template)
        versions.append(new)
        self.registered.append((name, template))
        return new

    def set_prompt_alias(self, name, alias, version):
        self.aliased.append((name, alias, int(version)))


@pytest.fixture
def wired(monkeypatch):
    """Patch MLflow so ``register_all`` runs against in-memory fakes."""

    def _wire(store: dict[str, list[FakeVersion]]) -> Recorder:
        import mlflow
        import mlflow.genai as genai

        rec = Recorder(store)
        monkeypatch.setattr(mlflow, "set_registry_uri", lambda _uri: None)
        monkeypatch.setattr(mlflow, "MlflowClient", lambda *a, **k: FakeClient(store))
        monkeypatch.setattr(genai, "register_prompt", rec.register_prompt)
        monkeypatch.setattr(genai, "set_prompt_alias", rec.set_prompt_alias)
        return rec

    return _wire


class TestLatest:
    def test_unregistered_prompt_is_not_an_error(self):
        """The first run of a fresh environment must not look like a failure."""
        assert _latest(FakeClient({}), "cat.sch.nope") is None

    def test_picks_the_highest_version_regardless_of_order(self):
        client = FakeClient({"p": [FakeVersion(1, "a"), FakeVersion(3, "c"), FakeVersion(2, "b")]})
        assert _latest(client, "p").version == 3

    def test_a_real_read_failure_is_unreadable_not_missing(self):
        """An unreadable registry must not be reported as an empty one."""

        class Broken:
            def search_prompt_versions(self, name):
                raise RuntimeError("network on fire")

        assert _latest(Broken(), "p") is UNREADABLE

    def test_missing_is_distinguished_from_broken(self):
        assert _is_missing(FakeRestError("NOT_FOUND: Prompt with name x does not exist."))
        assert not _is_missing(RuntimeError("network on fire"))

    def test_a_credentials_failure_is_not_a_missing_prompt(self):
        """The bug this guards against cost eleven redundant versions.

        MLflow's credential error explains the DATABRICKS_CONFIG_PROFILE
        fallback with the words "does not exist". Read as a missing prompt,
        every prompt in the catalogue gets re-registered on a deploy whose
        auth is merely misconfigured.
        """
        auth_error = FakeRestError(
            "Reading Databricks credential configuration failed with MLflow "
            "registry URI 'databricks-uc'. Please ensure that the "
            "'databricks-sdk' PyPI library is installed, the tracking URI is "
            "set correctly, and Databricks authentication is properly "
            "configured. The registry URI can be either 'databricks-uc' (using "
            "profile name specified by 'DATABRICKS_CONFIG_PROFILE' environment "
            "variable or using 'DEFAULT' authentication profile if "
            "'DATABRICKS_CONFIG_PROFILE' environment variable does not exist)."
        )
        assert not _is_missing(auth_error)


class TestIdempotency:
    def test_first_run_registers_every_prompt(self, wired):
        store: dict[str, list[FakeVersion]] = {}
        rec = wired(store)
        created = register_all(CAT, SCH)
        assert created == len(store) > 0
        assert len(rec.registered) == created

    def test_second_run_registers_nothing(self, wired):
        """The regression. Unchanged text must not produce a new version."""
        store: dict[str, list[FakeVersion]] = {}
        wired(store)
        register_all(CAT, SCH)
        before = {name: len(v) for name, v in store.items()}

        rec2 = wired(store)
        created = register_all(CAT, SCH)

        assert created == 0
        assert rec2.registered == []
        assert {name: len(v) for name, v in store.items()} == before

    def test_the_alias_is_repointed_even_when_nothing_changed(self, wired):
        """So a restored environment is healed by re-running, not by clicking."""
        store: dict[str, list[FakeVersion]] = {}
        wired(store)
        register_all(CAT, SCH)

        rec2 = wired(store)
        register_all(CAT, SCH)
        assert len(rec2.aliased) == len(store)
        assert {alias for _, alias, _ in rec2.aliased} == {"production"}

    def test_changed_text_registers_a_new_version(self, wired):
        store: dict[str, list[FakeVersion]] = {}
        wired(store)
        register_all(CAT, SCH)

        edited = next(iter(store))
        store[edited].append(FakeVersion(len(store[edited]) + 1, "someone edited this"))

        rec2 = wired(store)
        created = register_all(CAT, SCH)
        assert created == 1
        assert rec2.registered[0][0] == edited

    def test_dry_run_writes_nothing(self, wired):
        store: dict[str, list[FakeVersion]] = {}
        rec = wired(store)
        assert register_all(CAT, SCH, dry_run=True) == 0
        assert rec.registered == [] and rec.aliased == [] and store == {}

    def test_dry_run_reads_the_registry_so_it_can_report_drift(self, wired, capsys):
        """A plan that skips the read can only ever say "new".

        That is worse than useless in a deploy log: it reports an in-sync
        registry as eleven pending registrations.
        """
        store: dict[str, list[FakeVersion]] = {}
        wired(store)
        register_all(CAT, SCH)

        wired(store)
        capsys.readouterr()
        register_all(CAT, SCH, dry_run=True)

        out = capsys.readouterr().out
        assert "unchanged, still v1" in out
        assert "would register" not in out

    def test_an_unreadable_registry_mints_nothing(self, wired, capsys):
        """Skipping loses a deploy's worth of registration; guessing "new"
        loses the version history."""
        store: dict[str, list[FakeVersion]] = {}
        rec = wired(store)

        def broken(self, name):
            raise RuntimeError("Reading Databricks credential configuration failed")

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(FakeClient, "search_prompt_versions", broken)
        try:
            created = register_all(CAT, SCH)
        finally:
            monkeypatch.undo()

        assert created == 0
        assert rec.registered == []
        assert "registry unreadable" in capsys.readouterr().out


class TestTags:
    def test_a_governed_specialist_records_its_runtime_suffix(self):
        """Registered text is not the whole system prompt for these, and the
        tag is the only place that says so."""
        from agent_server.prompts.catalog import BY_SLUG

        tags = _tags(BY_SLUG["oee_coach_system"])
        assert tags["governed_domain"] == "production"
        assert "governed_domain_prompt" in tags["runtime_suffix"]

    def test_a_prompt_with_no_governed_domain_claims_no_suffix(self):
        from agent_server.prompts.catalog import BY_SLUG

        assert "runtime_suffix" not in _tags(BY_SLUG["shift_briefing_system"])
