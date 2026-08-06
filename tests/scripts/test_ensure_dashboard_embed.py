"""The Data Quality tab depends on three facts nobody was checking.

A dashboard embeds only if it exists, is published with embedded credentials,
and is readable by whoever opens the tab. All three were assumed: the bundle
declares `embed_credentials`, and the grants lived in a bash script that ran
under scripts/deploy.sh and warned-and-continued when it failed. A colleague
running `databricks bundle deploy` in their own workspace got a dashboard that
existed, resolved by name, and rendered Databricks' bare "The resource is not
available" page inside the iframe.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "ensure_dashboard_embed", ROOT / "scripts" / "ensure_dashboard_embed.py"
)
mod = importlib.util.module_from_spec(_spec)
sys.modules["ensure_dashboard_embed"] = mod
_spec.loader.exec_module(mod)


def _dashboard(name: str, did: str) -> MagicMock:
    d = MagicMock()
    d.display_name = name
    d.dashboard_id = did
    return d


def _published(embed: bool = True) -> MagicMock:
    p = MagicMock()
    p.embed_credentials = embed
    p.warehouse_id = "wh-1"
    return p


@pytest.fixture
def w() -> MagicMock:
    client = MagicMock()
    client.lakeview.list.return_value = [_dashboard("App — Data Quality", "dash-1")]
    client.current_user.me.return_value = MagicMock(user_name="someone@example.com")
    return client


class TestResolution:
    def test_an_explicit_id_is_used_directly(self, w):
        w.lakeview.get.return_value = _dashboard("Whatever", "dash-9")
        assert mod._resolve_dashboard_id(w, "dash-9", "App — Data Quality")[0] == "dash-9"
        w.lakeview.list.assert_not_called()

    @pytest.mark.parametrize("placeholder", ["SET_ME", "<your-dashboard-id>", "", "   "])
    def test_placeholders_fall_back_to_the_name(self, w, placeholder):
        """SET_ME is a non-empty string, and every naive check has taken it for an id.

        This is the exact case that produced the 404: refresh_app_yaml_ids.py
        had not filled the value, so the deploy skipped every dashboard step.
        """
        assert mod._resolve_dashboard_id(w, placeholder, "App — Data Quality")[0] == "dash-1"

    def test_a_missing_dashboard_is_a_clear_error_not_a_crash(self, w):
        w.lakeview.list.return_value = [_dashboard("Something Else", "x")]
        with pytest.raises(SystemExit) as e:
            mod._resolve_dashboard_id(w, "", "App — Data Quality")
        assert "no dashboard named" in str(e.value)


class TestPublishing:
    def _run(self, w, monkeypatch, extra: list[str] | None = None) -> int:
        argv = ["ensure_dashboard_embed.py", "--warehouse-id", "wh-1", "--app-name", "App"]
        monkeypatch.setattr(sys, "argv", argv + (extra or []))
        monkeypatch.setattr(mod, "WorkspaceClient", lambda **kw: w)
        return mod.main()

    def test_an_unpublished_dashboard_is_published(self, w, monkeypatch):
        w.lakeview.get_published.side_effect = [Exception("not found"), _published()]
        assert self._run(w, monkeypatch) == 0
        w.lakeview.publish.assert_called_once()
        assert w.lakeview.publish.call_args.kwargs["embed_credentials"] is True

    def test_publishing_is_skipped_when_already_correct(self, w, monkeypatch):
        w.lakeview.get_published.return_value = _published()
        assert self._run(w, monkeypatch) == 0
        w.lakeview.publish.assert_not_called()

    def test_published_without_embedded_credentials_is_republished(self, w, monkeypatch):
        """Otherwise every widget renders 'Unable to render visualization.'"""
        w.lakeview.get_published.side_effect = [_published(embed=False), _published(embed=True)]
        assert self._run(w, monkeypatch) == 0
        w.lakeview.publish.assert_called_once()

    def test_it_fails_when_the_dashboard_is_still_unpublished_afterwards(self, w, monkeypatch):
        """Publishing can report success and leave nothing embeddable."""
        w.lakeview.get_published.side_effect = [Exception("no"), Exception("still no")]
        assert self._run(w, monkeypatch) == 1

    def test_it_fails_when_credentials_did_not_stick(self, w, monkeypatch):
        w.lakeview.get_published.side_effect = [Exception("no"), _published(embed=False)]
        assert self._run(w, monkeypatch) == 1


class TestGrants:
    def _acl(self, w, monkeypatch, extra: list[str]) -> list:
        w.lakeview.get_published.return_value = _published()
        monkeypatch.setattr(
            sys, "argv",
            ["ensure_dashboard_embed.py", "--warehouse-id", "wh-1", "--app-name", "App"] + extra,
        )
        monkeypatch.setattr(mod, "WorkspaceClient", lambda **kw: w)
        assert mod.main() == 0
        return w.permissions.update.call_args.kwargs["access_control_list"]

    def test_humans_are_granted_not_just_the_service_principal(self, w, monkeypatch):
        """The old script granted the app SP only.

        So the tab worked for the person who deployed it and 404'd for everyone
        they showed it to — which is how this reached a colleague's workspace.
        """
        acl = self._acl(w, monkeypatch, ["--app-sp", "sp-123", "--viewer", "a@b.com"])
        assert "sp-123" in [a.service_principal_name for a in acl]
        assert "a@b.com" in [a.user_name for a in acl]

    def test_the_deploying_user_is_granted_by_default(self, w, monkeypatch):
        acl = self._acl(w, monkeypatch, [])
        assert "someone@example.com" in [a.user_name for a in acl]

    def test_groups_can_be_granted(self, w, monkeypatch):
        acl = self._acl(w, monkeypatch, ["--viewer-group", "plant-managers"])
        assert "plant-managers" in [a.group_name for a in acl]

    def test_a_placeholder_service_principal_is_not_granted(self, w, monkeypatch):
        """SET_ME as a principal name would make the grant call fail outright."""
        acl = self._acl(w, monkeypatch, ["--app-sp", "SET_ME"])
        assert "SET_ME" not in [a.service_principal_name for a in acl]
