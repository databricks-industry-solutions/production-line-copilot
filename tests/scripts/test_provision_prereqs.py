"""Tests for scripts/provision_prereqs.py (the optional prerequisite provisioner).

These are pure-logic / mock-SDK tests — they never touch a real workspace and
never create billable infra. They pin the behaviours that matter for a safe
provisioner:

  * ``_is_set`` / ``_parse_secret_ref``  — input hygiene
  * ``_write_profile_fields``            — profile-scoped, comment-preserving writeback
  * ``_load_profile``                    — bundle_target resolution
  * idempotency skips ("already set", "reuse existing by name")
  * dry-run never returns create side effects
  * Zerobus URL derivation (never "created", only derived)
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.provision_prereqs import (  # noqa: E402
    _is_set,
    _load_profile,
    _parse_secret_ref,
    _write_profile_fields,
    derive_zerobus_url,
    provision_lakebase,
    provision_service_principal,
    provision_warehouse,
)


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #
def _obj(**kw):
    return types.SimpleNamespace(**kw)


class _FakeList:
    def __init__(self, items):
        self._items = items

    def list(self, *a, **k):
        return list(self._items)


class _FakeOp:
    def wait(self):
        return None


class _FakePostgres:
    """Stand-in for w.postgres: project missing -> create; endpoint has a host."""

    def __init__(self, host="ep-x.database.us-east-1.cloud.databricks.com", project_exists=False):
        self._host = host
        self._project_exists = project_exists
        self.created = False

    def get_project(self, name):
        if self._project_exists:
            return _obj(name=name)
        raise RuntimeError("not found")

    def create_project(self, project, project_id):
        self.created = True
        self._project_exists = True
        return _FakeOp()

    def get_endpoint(self, name):
        hosts = _obj(host=self._host) if self._host else None
        return _obj(status=_obj(hosts=hosts))


class FakeW:
    """Minimal read-only stand-in for WorkspaceClient (dry-run paths only)."""

    def __init__(self, *, warehouses=(), sps=(), scopes=(), workspace_id=999, postgres=None):
        self.warehouses = _FakeList(warehouses)
        self.service_principals = _FakeList(sps)
        self.secrets = types.SimpleNamespace(
            list_scopes=lambda: [_obj(name=s) for s in scopes]
        )
        self.postgres = postgres
        self._wsid = workspace_id

    def get_workspace_id(self):
        return self._wsid


# --------------------------------------------------------------------------- #
# _is_set
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", ["", "   ", "SET_ME", "<your-id>", None])
def test_is_set_rejects_placeholders(value):
    assert _is_set(value) is False


@pytest.mark.parametrize("value", ["abc123", "2f59", "ep-x.database.us-east-1.cloud.databricks.com"])
def test_is_set_accepts_real_values(value):
    assert _is_set(value) is True


# --------------------------------------------------------------------------- #
# _parse_secret_ref
# --------------------------------------------------------------------------- #
def test_parse_secret_ref_valid():
    assert _parse_secret_ref("{{secrets/my-scope/my-key}}") == ("my-scope", "my-key")


@pytest.mark.parametrize("ref", ["", "not-a-ref", "{{secrets/onlyscope}}", None])
def test_parse_secret_ref_falls_back_to_defaults(ref):
    assert _parse_secret_ref(ref) == ("line-manager", "zerobus-client-secret")


# --------------------------------------------------------------------------- #
# _load_profile
# --------------------------------------------------------------------------- #
_SETTINGS = """\
profiles:
  line_manager_dev:
    bundle_target: line_manager_dev
    sql_warehouse_id: "SET_ME"
    producer_client_id: "SET_ME"
    # keep this comment
    producer_client_secret_ref: "{{secrets/line-manager/zerobus-client-secret}}"
    lakebase_pg_host: "SET_ME"
    lakebase_endpoint: "SET_ME"
    zerobus_server_endpoint: "SET_ME"
  line_manager_prod:
    bundle_target: line_manager_prod
    sql_warehouse_id: "PROD_WH_KEEP"
"""


def _settings_file(tmp_path: Path) -> Path:
    p = tmp_path / "project-settings.local.yaml"
    p.write_text(_SETTINGS, encoding="utf-8")
    return p


def test_load_profile_by_bundle_target(tmp_path):
    name, prof = _load_profile(_settings_file(tmp_path), "line_manager_dev")
    assert name == "line_manager_dev"
    assert prof["sql_warehouse_id"] == "SET_ME"


def test_load_profile_missing_raises(tmp_path):
    with pytest.raises(SystemExit):
        _load_profile(_settings_file(tmp_path), "nope")


# --------------------------------------------------------------------------- #
# _write_profile_fields
# --------------------------------------------------------------------------- #
def test_write_profile_fields_scoped_and_comment_preserving(tmp_path):
    path = _settings_file(tmp_path)
    _write_profile_fields(path, "line_manager_dev", {
        "sql_warehouse_id": "wh-abc",
        "producer_client_id": "cid-xyz",
    })
    text = path.read_text(encoding="utf-8")

    # dev block updated
    assert 'sql_warehouse_id: "wh-abc"' in text
    assert 'producer_client_id: "cid-xyz"' in text
    # comment preserved
    assert "# keep this comment" in text
    # prod block untouched
    assert 'sql_warehouse_id: "PROD_WH_KEEP"' in text
    # still valid YAML with the expected values
    import yaml
    doc = yaml.safe_load(text)
    assert doc["profiles"]["line_manager_dev"]["sql_warehouse_id"] == "wh-abc"
    assert doc["profiles"]["line_manager_prod"]["sql_warehouse_id"] == "PROD_WH_KEEP"


def test_write_profile_fields_warns_on_unknown_key(tmp_path, capsys):
    path = _settings_file(tmp_path)
    _write_profile_fields(path, "line_manager_dev", {"nonexistent_key": "x"})
    out = capsys.readouterr().out
    assert "not found" in out and "nonexistent_key" in out


# --------------------------------------------------------------------------- #
# provision_warehouse
# --------------------------------------------------------------------------- #
def test_warehouse_skips_when_already_set():
    prof = {"sql_warehouse_id": "existing-id"}
    assert provision_warehouse(FakeW(), prof, apply=False) == {}


def test_warehouse_reuses_existing_by_name():
    w = FakeW(warehouses=[_obj(name="line-manager-serverless", id="wh-reused")])
    prof = {"sql_warehouse_id": "SET_ME"}
    assert provision_warehouse(w, prof, apply=False) == {"sql_warehouse_id": "wh-reused"}


def test_warehouse_dry_run_creates_nothing():
    w = FakeW(warehouses=[])
    prof = {"sql_warehouse_id": "SET_ME"}
    assert provision_warehouse(w, prof, apply=False) == {}


# --------------------------------------------------------------------------- #
# provision_service_principal
# --------------------------------------------------------------------------- #
def test_sp_reuses_when_client_id_set():
    w = FakeW(
        sps=[_obj(application_id="cid-set", id="42", display_name="line-manager-producer")],
        scopes=["line-manager"],
    )
    prof = {
        "producer_client_id": "cid-set",
        "producer_client_secret_ref": "{{secrets/line-manager/zerobus-client-secret}}",
    }
    out = provision_service_principal(w, prof, apply=False)
    assert out == {"producer_client_id": "cid-set"}


def test_sp_dry_run_does_not_mint_secret():
    # No SP exists yet, dry run -> nothing created, no proxy call available on FakeW.
    w = FakeW(sps=[], scopes=[])
    prof = {
        "producer_client_id": "SET_ME",
        "producer_client_secret_ref": "{{secrets/line-manager/zerobus-client-secret}}",
    }
    out = provision_service_principal(w, prof, apply=False)
    assert out == {}


# --------------------------------------------------------------------------- #
# provision_lakebase
# --------------------------------------------------------------------------- #
def test_lakebase_skips_when_already_set():
    prof = {"lakebase_pg_host": "ep-x.database.us-east-1.cloud.databricks.com",
            "lakebase_endpoint": "projects/p/branches/production/endpoints/primary"}
    assert provision_lakebase(FakeW(), prof, apply=False) == {}


def test_lakebase_dry_run_creates_nothing():
    prof = {"lakebase_pg_host": "SET_ME", "lakebase_endpoint": "SET_ME"}
    assert provision_lakebase(FakeW(), prof, apply=False) == {}


def test_lakebase_apply_creates_project_and_reads_autocreated_host():
    """create_project auto-provisions branch+endpoint; we only create the project
    then read the endpoint host (verified against the live API)."""
    pg = _FakePostgres(host="ep-live.database.us-east-1.cloud.databricks.com")
    prof = {"lakebase_pg_host": "SET_ME", "lakebase_endpoint": "SET_ME"}
    out = provision_lakebase(FakeW(postgres=pg), prof, apply=True)
    assert pg.created is True
    assert out == {
        "lakebase_endpoint": "projects/prodline-copilot/branches/production/endpoints/primary",
        "lakebase_pg_host": "ep-live.database.us-east-1.cloud.databricks.com",
    }


def test_lakebase_apply_idempotent_when_project_exists():
    pg = _FakePostgres(project_exists=True)
    prof = {"lakebase_pg_host": "SET_ME", "lakebase_endpoint": "SET_ME"}
    out = provision_lakebase(FakeW(postgres=pg), prof, apply=True)
    assert pg.created is False  # did not re-create an existing project
    assert out["lakebase_endpoint"].endswith("endpoints/primary")


# --------------------------------------------------------------------------- #
# derive_zerobus_url  (derived only — never "created")
# --------------------------------------------------------------------------- #
def test_zerobus_skips_when_already_set():
    prof = {"zerobus_server_endpoint": "https://1.zerobus.us-east-1.cloud.databricks.com"}
    assert derive_zerobus_url(FakeW(), prof, region=None, created_lakebase={}) == {}


def test_zerobus_uses_explicit_region():
    w = FakeW(workspace_id=123456789012345)
    out = derive_zerobus_url(w, {"zerobus_server_endpoint": "SET_ME"},
                             region="us-east-1", created_lakebase={})
    assert out == {
        "zerobus_server_endpoint": "https://123456789012345.zerobus.us-east-1.cloud.databricks.com"
    }


def test_zerobus_derives_region_from_lakebase_host():
    w = FakeW(workspace_id=123)
    out = derive_zerobus_url(
        w, {"zerobus_server_endpoint": "SET_ME"}, region=None,
        created_lakebase={"lakebase_pg_host": "ep-a.database.eu-west-1.cloud.databricks.com"},
    )
    assert out == {"zerobus_server_endpoint": "https://123.zerobus.eu-west-1.cloud.databricks.com"}


def test_zerobus_skips_when_region_unknown():
    w = FakeW(workspace_id=123)
    out = derive_zerobus_url(w, {"zerobus_server_endpoint": "SET_ME"},
                             region=None, created_lakebase={})
    assert out == {}
