"""Pure-helper coverage for start_server.py.

start_server is mostly FastAPI/SSE/LLM glue (best covered by the smoke tests +
live deploy), but it carries a layer of pure helpers — numeric coercion, cost
bucketing, ops audit/role logic, shift-id resolution — that are cheap to pin
down and easy to regress. This file covers those without standing up the app.
"""
from __future__ import annotations

import types

import pytest

from agent_server import start_server as ss


# --------------------------------------------------------------------------- #
# numeric coercion
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "value,expected",
    [(3, 3.0), ("2.5", 2.5), (None, 0.0), ("nope", 0.0), (float("inf"), 0.0), (float("nan"), 0.0)],
)
def test_to_float(value, expected):
    assert ss._to_float(value) == expected


def test_to_float_custom_default():
    assert ss._to_float(None, default=9.0) == 9.0


def test_safe_num():
    assert ss._safe_num(None) == 0.0
    assert ss._safe_num("4") == 4.0


# --------------------------------------------------------------------------- #
# KPI helpers
# --------------------------------------------------------------------------- #
def test_kpi_is_empty():
    assert ss._kpi_is_empty(None) is True
    assert ss._kpi_is_empty({}) is True
    assert ss._kpi_is_empty({"oee_to_now": 0, "availability_to_now": 0,
                             "performance_to_now": 0, "quality_to_now": 0}) is True
    assert ss._kpi_is_empty({"oee_to_now": 0.7}) is False


def test_machine_rows_show_activity():
    assert ss._machine_rows_show_activity([{"status": "running"}]) is True
    assert ss._machine_rows_show_activity([{"status": "stopped", "throughput_per_min": 5}]) is True
    assert ss._machine_rows_show_activity([{"status": "stopped", "speed_pct": 20}]) is True
    assert ss._machine_rows_show_activity([{"status": "stopped"}]) is False
    assert ss._machine_rows_show_activity([]) is False


def test_derive_kpi_from_machine_rows_empty_is_none():
    assert ss._derive_kpi_from_machine_rows("shift_1", []) is None


def test_derive_kpi_from_machine_rows_returns_dict():
    machines = [
        {"machine_id": "FILLER", "status": "running", "throughput_per_min": 100, "speed_pct": 90, "ts": "2026-01-01T09:00:00"},
        {"machine_id": "CAPPER", "status": "stopped", "throughput_per_min": 0, "speed_pct": 0, "ts": "2026-01-01T09:01:00"},
        {"machine_id": "CONVEYOR_1", "status": "running", "throughput_per_min": 50, "speed_pct": 100, "ts": "2026-01-01T09:02:00"},
    ]
    row = ss._derive_kpi_from_machine_rows("shift_1", machines)
    assert isinstance(row, dict)
    assert row  # non-empty


def test_iso_from_payload_picks_latest():
    machines = [{"ts": "2026-01-01T09:00:00"}, {"ts": "2026-01-01T09:05:00"}]
    kpi = {"ts": "2026-01-01T08:00:00"}
    events = [{"timestamp_start": "2026-01-01T09:10:00"}]
    assert ss._iso_from_payload(machines, kpi, events) == "2026-01-01T09:10:00"


def test_iso_from_payload_none_when_empty():
    assert ss._iso_from_payload([], None, []) is None


# --------------------------------------------------------------------------- #
# env / cost helpers
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value,expected", [
    ("1", True), ("true", True), ("YES", True), ("on", True),
    ("0", False), ("", False), (None, False), ("nope", False),
])
def test_truthy(value, expected):
    assert ss._truthy(value) is expected


@pytest.mark.parametrize("unit,qty,expected", [
    ("BYTES", 10, 10),
    ("KB", 1, 1024),
    ("MB", 1, 1024 ** 2),
    ("GB", 1, 1024 ** 3),
    ("TB", 1, 1024 ** 4),
    ("GB", 0, 0.0),
    ("PARSECS", 5, 0.0),
])
def test_unit_to_bytes(unit, qty, expected):
    assert ss._unit_to_bytes(unit, qty) == expected


@pytest.mark.parametrize("origin,sku,expected", [
    ("", "ZEROBUS_INGEST", "Zerobus"),
    ("", "LAKEBASE_OLTP", "Lakebase"),
    ("", "LAKEFLOW_PIPELINE", "Pipelines"),
    ("", "MODEL_SERVING", "Model Serving"),
    ("", "SQL_WAREHOUSE", "SQL Warehouse"),
    ("", "JOBS_COMPUTE", "Jobs"),
    ("", "APP_COMPUTE", "Apps"),
    ("", "MYSTERY_SKU", "Other"),
])
def test_usage_category(origin, sku, expected):
    assert ss._usage_category(origin, sku) == expected


def test_estimate_usd_known_and_unknown():
    assert ss._estimate_usd("Jobs", 10) == round(10 * 0.55, 4)
    assert ss._estimate_usd("NopeComponent", 10) == round(10 * ss._DBU_USD_RATES["Other"], 4)
    assert ss._estimate_usd("Jobs", -5) == 0.0  # negative clamped


def test_estimate_tokens():
    assert ss._estimate_tokens("") == 0
    assert ss._estimate_tokens(None) == 0
    assert ss._estimate_tokens("abcd" * 4) == 4  # 16 chars / 4


@pytest.mark.parametrize("n,expected", [
    (0, "0 B"),
    (-5, "0 B"),
    (512, "512.00 B"),
    (1024, "1.00 KB"),
    (1024 ** 2, "1.00 MB"),
    (1024 ** 3, "1.00 GB"),
])
def test_human_bytes(n, expected):
    assert ss._human_bytes(n) == expected


# --------------------------------------------------------------------------- #
# in-app live counters
# --------------------------------------------------------------------------- #
def test_live_record_and_aggregate():
    metric = "test_metric_xyz"  # unique key → deterministic aggregate
    before = ss._live_aggregate(5).get(metric, 0.0)
    ss._live_record(metric, 2.0)
    ss._live_record(metric, 1.0)
    after = ss._live_aggregate(5)[metric]
    assert after - before == 3.0


# --------------------------------------------------------------------------- #
# ops helpers
# --------------------------------------------------------------------------- #
def test_ops_now_iso():
    assert "T" in ss._ops_now_iso()


def test_ops_to_dict():
    assert ss._ops_to_dict(None) == {}
    assert ss._ops_to_dict({"a": 1}) == {"a": 1}
    assert ss._ops_to_dict(types.SimpleNamespace(as_dict=lambda: {"b": 2})) == {"b": 2}
    assert ss._ops_to_dict(object()) == {}


def test_ops_user_header_priority():
    req = types.SimpleNamespace(headers={"x-ops-user": "a@x.com", "x-user-email": "b@x.com"})
    assert ss._ops_user(req) == "a@x.com"


def test_ops_user_env_fallback(monkeypatch):
    monkeypatch.setenv("OPS_DEFAULT_USER", "default@x.com")
    req = types.SimpleNamespace(headers={})
    assert ss._ops_user(req) == "default@x.com"


def test_ops_user_unknown(monkeypatch):
    monkeypatch.delenv("OPS_DEFAULT_USER", raising=False)
    req = types.SimpleNamespace(headers={})
    assert ss._ops_user(req) == "unknown"


def test_ops_role(monkeypatch):
    monkeypatch.setenv("OPS_ADMIN_EMAILS", "boss@x.com")
    monkeypatch.setenv("OPS_OPERATOR_EMAILS", "op@x.com")
    monkeypatch.setenv("OPS_DEFAULT_ROLE", "viewer")
    assert ss._ops_role("boss@x.com") == "admin"
    assert ss._ops_role("op@x.com") == "operator"
    assert ss._ops_role("stranger@x.com") == "viewer"


def test_ops_guard_cooldown(monkeypatch):
    monkeypatch.setattr(ss, "_OPS_COOLDOWN_S", 60.0, raising=False)
    key = "test-cooldown-key-abc"
    ss._OPS_LAST_CALLS.pop(key, None)
    assert ss._ops_guard_cooldown(key) is None  # first call allowed
    msg = ss._ops_guard_cooldown(key)  # immediate retry blocked
    assert msg is not None and "Cooldown" in msg


# --------------------------------------------------------------------------- #
# shift-id resolution
# --------------------------------------------------------------------------- #
def test_server_shift_id_prefers_env(monkeypatch):
    monkeypatch.setenv("SIM_SHIFT_ID", "shift_1_dev")
    assert ss._server_shift_id("live") == "shift_1_dev"


def test_server_shift_id_falls_back_to_client(monkeypatch):
    monkeypatch.setenv("SIM_SHIFT_ID", "")
    assert ss._server_shift_id("shift_9") == "shift_9"


def test_server_shift_id_default(monkeypatch):
    monkeypatch.setenv("SIM_SHIFT_ID", "")
    assert ss._server_shift_id(None) == "shift_1"
