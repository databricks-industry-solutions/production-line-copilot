"""A configured Genie space id is not the same as a live Genie space.

dev's streaming space was trashed by a teardown. project-settings correctly
reset ``genie_space_id`` to SET_ME so a deploy would rebuild it, but the deploy
never looked: the whole Genie provisioning block sat inside the ``RUN_SETUP=1``
branch, and RUN_SETUP is auto-detected by probing for ``machine_state_bronze``.
Once the schema existed again every deploy resolved to RUN_SETUP=0 and skipped
the step, so a deleted space could only be restored by deploying into a
genuinely empty schema.

Meanwhile the app went on advertising itself as Genie-enabled, because the only
check was that the env var was non-empty. Every ``genie_query`` from the shift
briefing, strategic advisor and OEE coach failed against a space in the trash,
and the System Map drew the connection anyway.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_server.tools import genie_query

_DEPLOY = Path(__file__).resolve().parents[2] / "scripts" / "deploy.sh"


class _Response:
    def __init__(self, status_code: int):
        self.status_code = status_code


@pytest.fixture(autouse=True)
def _clear_cache():
    genie_query._SPACE_RESOLVES_CACHE.clear()
    yield
    genie_query._SPACE_RESOLVES_CACHE.clear()


@pytest.fixture
def api(monkeypatch):
    def build(status_code: int = 200, raises: bool = False):
        calls: list[str] = []

        def fake_get(url, **_kw):
            calls.append(url)
            if raises:
                raise ConnectionError("network down")
            return _Response(status_code)

        monkeypatch.setattr(genie_query, "_make_headers", lambda: ({}, "https://host"))
        monkeypatch.setattr(genie_query.requests, "get", fake_get)
        return calls
    return build


class TestResolvingASpace:
    def test_a_live_space_resolves(self, api):
        api(status_code=200)
        assert genie_query.space_resolves("01f1live") is True

    def test_a_trashed_space_does_not(self, api):
        # The API answers 404 for a space in the trash.
        api(status_code=404)
        assert genie_query.space_resolves("01f1trashed") is False

    def test_no_space_configured_is_not_available(self, api, monkeypatch):
        api()
        monkeypatch.setattr(genie_query, "_SPACE_ID", "")
        assert genie_query.space_resolves("") is False

    def test_it_asks_about_the_space_it_was_given(self, api):
        calls = api()
        genie_query.space_resolves("01f1abc")
        assert calls and calls[0].endswith("/api/2.0/genie/spaces/01f1abc")


class TestItDoesNotCryWolf:
    def test_an_unreachable_workspace_is_not_reported_as_missing(self, api):
        # Absent and unreachable are different, and a transient network fault
        # reported as "not configured" is its own false alarm.
        api(raises=True)
        assert genie_query.space_resolves("01f1abc") is True

    def test_a_transient_failure_is_not_cached(self, api):
        api(raises=True)
        genie_query.space_resolves("01f1abc")
        assert "01f1abc" not in genie_query._SPACE_RESOLVES_CACHE


class TestItDoesNotHammerTheApi:
    def test_a_repeated_check_is_served_from_cache(self, api):
        calls = api(status_code=200)
        for _ in range(5):
            genie_query.space_resolves("01f1abc")
        assert len(calls) == 1

    def test_each_space_is_cached_separately(self, api):
        calls = api(status_code=200)
        genie_query.space_resolves("01f1abc")
        genie_query.space_resolves("01f1def")
        assert len(calls) == 2


class TestTheSystemMapReportsTheTruth:
    def test_it_checks_resolution_not_just_the_variable(self):
        source = (
            Path(__file__).resolve().parents[2] / "agent_server" / "start_server.py"
        ).read_text()
        assert '"streaming_space": _streaming_space_available()' in source
        assert '"streaming_space": bool(os.environ.get("GENIE_SPACE_ID"' not in source

    def test_an_unset_variable_short_circuits(self, monkeypatch):
        from agent_server.start_server import _streaming_space_available

        monkeypatch.delenv("GENIE_SPACE_ID", raising=False)
        assert _streaming_space_available() is False

    def test_the_check_never_takes_the_panel_down(self, monkeypatch):
        from agent_server import start_server

        monkeypatch.setenv("GENIE_SPACE_ID", "01f1abc")
        monkeypatch.setattr(
            genie_query, "space_resolves",
            lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        assert start_server._streaming_space_available() is True


class TestTheDeployCanRepairAMissingSpace:
    """The provisioning step has to run on an ordinary redeploy, not only on a
    deploy into an empty schema."""

    def _source(self) -> str:
        return _DEPLOY.read_text()

    def test_provisioning_is_not_inside_the_run_setup_branch(self):
        source = self._source()
        setup = source.index('if [[ "${RUN_SETUP:-0}" == "1" ]]; then')
        genie = source.index("# ── 7a. Genie space provisioning")
        closing_fi = source.index("\nfi\n", setup)
        assert genie > closing_fi, (
            "Genie provisioning is back inside the RUN_SETUP branch, so a "
            "deleted space can only be restored by deploying into an empty schema"
        )

    def test_a_dangling_id_is_treated_as_unset(self):
        source = self._source()
        assert "/api/2.0/genie/spaces/$GENIE_ID" in source
        assert "no longer resolves" in source

    def test_it_still_runs_after_the_setup_job(self):
        # The curated space references catalog.schema.<table>, so provisioning
        # before the tables exist would create a space over nothing.
        source = self._source()
        assert source.index("Setup job complete") < source.index(
            "# ── 7a. Genie space provisioning"
        )

    def test_it_tells_the_space_which_shift_this_environment_writes(self):
        source = self._source()
        assert "GENIE_SHIFT=$(_profile_field sim_shift_id)" in source
        assert "--shift-id" in source

    def test_the_two_environments_do_not_share_a_space_title(self):
        # Genie spaces are workspace files; a same-named space in the same
        # folder fails with "Node named '<title>.geniespace.json' already exists".
        source = self._source()
        assert '[[ "$ENV" != "prod" ]] && GENIE_TITLE=' in source
        assert '[[ "$ENV" != "prod" ]] && MVM_GENIE_ARGS+=(--title-suffix "$ENV")' in source

    def test_the_streaming_space_is_no_more_conditional_than_the_governed_one(self):
        # Step 7b was already unconditional; the asymmetry is what hid this.
        # Comments are stripped because the ones above the step explain the
        # RUN_SETUP history this test exists to keep out.
        source = self._source()
        streaming = source.index("# ── 7a. Genie space provisioning")
        governed = source.index("# ── 7b. Governed MVM Genie space")
        code = "\n".join(
            line for line in source[streaming:governed].splitlines()
            if not line.lstrip().startswith("#")
        )
        assert "RUN_SETUP" not in code
