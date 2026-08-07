"""Tests for the trace-viewer grants in scripts/provision_trace_storage.py.

The bug these pin: the app writes traces as its own service principal, but the
traces tab deep-links into the workspace MLflow UI and reads the trace tables as
whoever clicked. Sharing the app grants nothing in Unity Catalog, so a colleague
with CAN_MANAGE on the app used to open the traces tab to an empty list.

Four grants stand between a viewer and a span — catalog traversal, schema
traversal, SELECT on the tables, and CAN_USE on the warehouse that runs the
query — and missing any one produces that same blank page. So each is asserted
separately rather than as one lump.

Pure-logic / mock-SDK: no workspace is touched and nothing billable is created.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from scripts.provision_trace_storage import (  # noqa: E402
    DEFAULT_TRACE_VIEWERS,
    _grant_trace_viewers,
    _principal_field,
    _split_viewers,
)

CATALOG = "main"
SCHEMA = "line_manager_streaming"
TABLES = [
    "main.line_manager_streaming.prodline_traces_otel_spans",
    "main.line_manager_streaming.prodline_traces_otel_logs",
]
WAREHOUSE = "wh-123"
EXPERIMENT = "4242"


@pytest.fixture
def grants(monkeypatch):
    """Collect the SQL that would run, instead of running it."""
    statements: list[str] = []
    monkeypatch.setattr(
        "scripts.provision_trace_storage._grant",
        lambda _wh, statement: statements.append(statement),
    )
    return statements


@pytest.fixture
def no_sdk_calls(monkeypatch):
    """Stub the two SDK-backed grants; they have their own tests below."""
    calls: dict[str, object] = {}
    monkeypatch.setattr(
        "scripts.provision_trace_storage._grant_warehouse_use",
        lambda wh, viewers: calls.update(warehouse=(wh, list(viewers))),
    )
    monkeypatch.setattr(
        "scripts.provision_trace_storage._grant_experiment_read",
        lambda exp, viewers: calls.update(experiment=(exp, list(viewers))),
    )
    return calls


def _run(viewers, warehouse=WAREHOUSE):
    _grant_trace_viewers(warehouse, CATALOG, SCHEMA, TABLES, EXPERIMENT, viewers)


# --------------------------------------------------------------------------- #
# what a viewer needs
# --------------------------------------------------------------------------- #
class TestAViewerGetsEveryGrantAReadNeeds:
    def test_it_grants_catalog_traversal(self, grants, no_sdk_calls):
        _run(["data-team"])
        assert "GRANT USE CATALOG ON CATALOG `main` TO `data-team`" in grants

    def test_it_grants_schema_traversal(self, grants, no_sdk_calls):
        _run(["data-team"])
        expected = "GRANT USE SCHEMA ON SCHEMA `main`.`line_manager_streaming` TO `data-team`"
        assert expected in grants

    def test_it_grants_select_on_every_trace_table(self, grants, no_sdk_calls):
        _run(["data-team"])
        for table in TABLES:
            assert f"GRANT SELECT ON TABLE {table} TO `data-team`" in grants

    def test_it_grants_use_on_the_warehouse_that_runs_the_query(self, grants, no_sdk_calls):
        # Without this the UC grants are all in place and the traces page still
        # shows nothing, because it has no compute to read the tables with.
        _run(["data-team"])
        assert no_sdk_calls["warehouse"] == (WAREHOUSE, ["data-team"])

    def test_it_grants_read_on_the_experiment(self, grants, no_sdk_calls):
        _run(["data-team"])
        assert no_sdk_calls["experiment"] == (EXPERIMENT, ["data-team"])

    def test_it_grants_to_each_viewer_separately(self, grants, no_sdk_calls):
        _run(["data-team", "alice@example.com"])
        for viewer in ("data-team", "alice@example.com"):
            assert f"GRANT USE CATALOG ON CATALOG `main` TO `{viewer}`" in grants


# --------------------------------------------------------------------------- #
# opting out
# --------------------------------------------------------------------------- #
class TestGrantingNobodyIsReachable:
    def test_no_viewers_grants_nothing(self, grants, no_sdk_calls):
        _run([])
        assert grants == []
        assert no_sdk_calls == {}

    def test_the_word_none_means_nobody(self):
        # 'none' is spelled out because project-settings.yaml cannot express
        # "explicitly empty" — a blank value reads the same as an unset key.
        assert _split_viewers("none") == []

    def test_an_empty_value_means_nobody(self):
        assert _split_viewers("") == []
        assert _split_viewers("   ") == []

    def test_a_missing_warehouse_does_not_stop_the_uc_grants(self, grants, monkeypatch):
        # The UC grants are useless without compute, but failing here would cost
        # the deploy its experiment link, and this script is non-fatal by design.
        monkeypatch.setattr(
            "scripts.provision_trace_storage._grant_experiment_read", lambda *a: None
        )
        _run(["data-team"], warehouse="")
        assert any("GRANT SELECT ON TABLE" in statement for statement in grants)


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #
class TestTheViewerListIsParsedForgivingly:
    def test_it_defaults_to_everyone(self):
        # The default is open on purpose: an under-grant is a silent blank page.
        assert DEFAULT_TRACE_VIEWERS == "account users"
        assert _split_viewers(DEFAULT_TRACE_VIEWERS) == ["account users"]

    def test_it_splits_and_strips(self):
        assert _split_viewers(" a , b ,, c ") == ["a", "b", "c"]

    def test_an_email_is_a_user_and_a_bare_name_is_a_group(self):
        # The permissions API takes the principal in a different field per type.
        assert _principal_field("alice@example.com") == {"user_name": "alice@example.com"}
        assert _principal_field("data-team") == {"group_name": "data-team"}


class TestTheEveryoneGroupHasTwoNames:
    """UC is account-level and says 'account users'; workspace ACLs say 'users'.

    Each layer rejects the other's spelling, so getting this wrong grants
    nothing while looking like it worked — UC fails with PRINCIPAL_DOES_NOT_EXIST
    and the traces tab stays empty.
    """

    def test_unity_catalog_grants_use_the_account_spelling(self, grants, no_sdk_calls):
        _run(["account users"])
        assert "GRANT USE CATALOG ON CATALOG `main` TO `account users`" in grants

    def test_workspace_acls_use_the_workspace_spelling(self):
        assert _principal_field("account users") == {"group_name": "users"}

    def test_a_named_group_is_not_rewritten(self):
        assert _principal_field("data-team") == {"group_name": "data-team"}


# --------------------------------------------------------------------------- #
# the demotion trap
# --------------------------------------------------------------------------- #
def _fake_client(monkeypatch, existing_levels, sent):
    """A WorkspaceClient whose warehouse already has the given ACL."""

    def _entry(name, levels):
        return types.SimpleNamespace(
            group_name=name,
            user_name=None,
            service_principal_name=None,
            all_permissions=[
                types.SimpleNamespace(permission_level=types.SimpleNamespace(value=lvl))
                for lvl in levels
            ],
        )

    warehouses = types.SimpleNamespace(
        get_permissions=lambda _id: types.SimpleNamespace(
            access_control_list=[_entry(n, lv) for n, lv in existing_levels.items()]
        ),
        update_permissions=lambda _id, access_control_list: sent.extend(access_control_list),
    )
    # Patch the class on the real module rather than replacing the module, which
    # would make the databricks.sdk.service.* imports below it unresolvable.
    monkeypatch.setattr(
        "databricks.sdk.WorkspaceClient",
        lambda: types.SimpleNamespace(warehouses=warehouses),
    )


class TestItNeverTakesWarehouseAccessAway:
    def test_it_skips_someone_who_already_has_can_manage(self, monkeypatch):
        # update_permissions SETS a level rather than raising it, so granting
        # CAN_USE to a CAN_MANAGE holder would quietly demote them.
        from scripts import provision_trace_storage as mod

        sent: list = []
        _fake_client(monkeypatch, {"admins": ["CAN_MANAGE"]}, sent)
        mod._grant_warehouse_use(WAREHOUSE, ["admins"])
        assert sent == []

    def test_it_grants_to_someone_with_nothing(self, monkeypatch):
        from scripts import provision_trace_storage as mod

        sent: list = []
        _fake_client(monkeypatch, {"admins": ["CAN_MANAGE"]}, sent)
        mod._grant_warehouse_use(WAREHOUSE, ["data-team"])
        assert [req.group_name for req in sent] == ["data-team"]

    def test_it_grants_only_to_the_ones_missing_access(self, monkeypatch):
        from scripts import provision_trace_storage as mod

        sent: list = []
        _fake_client(monkeypatch, {"admins": ["CAN_MANAGE"], "users": ["CAN_USE"]}, sent)
        mod._grant_warehouse_use(WAREHOUSE, ["users", "data-team"])
        assert [req.group_name for req in sent] == ["data-team"]

    def test_it_recognises_the_everyone_group_under_its_workspace_name(self, monkeypatch):
        # The ACL says "users"; the viewer list says "account users". Comparing
        # the two spellings naively would re-grant CAN_USE over CAN_MANAGE.
        from scripts import provision_trace_storage as mod

        sent: list = []
        _fake_client(monkeypatch, {"users": ["CAN_MANAGE"]}, sent)
        mod._grant_warehouse_use(WAREHOUSE, ["account users"])
        assert sent == []
