"""Tests for context_builder assembly + query plumbing.

The TTL/single-flight cache is covered in test_context_builder_cache.py; this
file covers the parts that turn raw SQL rows into the agent context bundle:

  * ``_safe_rows``               — drops {"error": ...} sentinel rows
  * ``_parameterized_query``     — happy path + failure resets the thread conn
  * ``_parallel_param_queries``  — name→rows mapping + per-query isolation
  * ``_build_context_uncached``  — bundle shape, line-position tagging + sort,
                                   KPI mapping, elapsed/remaining math
"""
from __future__ import annotations

from agent_server import context_builder as cb


# --------------------------------------------------------------------------- #
# _safe_rows
# --------------------------------------------------------------------------- #
def test_safe_rows_drops_error_and_non_dicts():
    rows = [{"a": 1}, {"error": "boom"}, None, "str", {"b": 2}]
    assert cb._safe_rows(rows) == [{"a": 1}, {"b": 2}]


def test_safe_rows_handles_none():
    assert cb._safe_rows(None) == []


# --------------------------------------------------------------------------- #
# _parameterized_query
# --------------------------------------------------------------------------- #
class _Cursor:
    def __init__(self, description, rows, raise_on_execute=False):
        self.description = description
        self._rows = rows
        self._raise = raise_on_execute

    def execute(self, sql, params=None):
        if self._raise:
            raise RuntimeError("warehouse down")

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class _Conn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.closed = False

    def cursor(self):
        return self._cursor

    def close(self):
        self.closed = True


def test_parameterized_query_happy(monkeypatch):
    cur = _Cursor(description=[("x",), ("y",)], rows=[(1, 2), (3, 4)])
    monkeypatch.setattr(cb, "_get_thread_connection", lambda: _Conn(cur))
    out = cb._parameterized_query("SELECT x, y FROM t", {}, "t")
    assert out == [{"x": 1, "y": 2}, {"x": 3, "y": 4}]


def test_parameterized_query_no_description_returns_empty(monkeypatch):
    cur = _Cursor(description=None, rows=[])
    monkeypatch.setattr(cb, "_get_thread_connection", lambda: _Conn(cur))
    assert cb._parameterized_query("SELECT 1", {}, "t") == []


def test_parameterized_query_failure_returns_empty_and_resets_conn(monkeypatch):
    cur = _Cursor(description=None, rows=[], raise_on_execute=True)
    conn = _Conn(cur)
    monkeypatch.setattr(cb, "_get_thread_connection", lambda: conn)
    cb._thread_local.conn = conn
    out = cb._parameterized_query("SELECT 1", {}, "t")
    assert out == []
    # thread-local connection is force-cleared so the next call reconnects
    assert getattr(cb._thread_local, "conn", None) is None


# --------------------------------------------------------------------------- #
# _parallel_param_queries
# --------------------------------------------------------------------------- #
def test_parallel_param_queries_maps_by_name(monkeypatch):
    def fake_pq(sql, params, label=""):
        return [{"label": label}]

    monkeypatch.setattr(cb, "_parameterized_query", fake_pq)
    out = cb._parallel_param_queries(
        {
            "one": ("SELECT 1", {}, "L1"),
            "two": ("SELECT 2", {}, "L2"),
        }
    )
    assert out["one"] == [{"label": "L1"}]
    assert out["two"] == [{"label": "L2"}]


def test_parallel_param_queries_isolates_failures(monkeypatch):
    def fake_pq(sql, params, label=""):
        if label == "bad":
            raise RuntimeError("kaboom")
        return [{"ok": True}]

    monkeypatch.setattr(cb, "_parameterized_query", fake_pq)
    out = cb._parallel_param_queries(
        {"good": ("q", {}, "good"), "bad": ("q", {}, "bad")}
    )
    assert out["good"] == [{"ok": True}]
    assert out["bad"] == []  # exception collapses to empty, not a crash


# --------------------------------------------------------------------------- #
# _build_context_uncached
# --------------------------------------------------------------------------- #
def test_build_context_assembles_bundle(monkeypatch):
    data = {
        "scenario_index": [
            {
                "scenario_name": "Steady State",
                "scenario_type": "normal",
                "shift_start": "2026-01-01T08:00:00",
                "shift_end": "2026-01-01T16:00:00",
            }
        ],
        # deliberately out of physical order + an error sentinel to filter
        "gold_history_machine_state": [
            {"machine_id": "FILLER", "ts": "2026-01-01T09:00:00"},
            {"machine_id": "DEPALLETIZER", "ts": "2026-01-01T09:00:00"},
            {"error": "transient"},
        ],
        "gold_history_line_kpi": [
            {
                "oee_to_now": 0.75,
                "availability_to_now": 0.9,
                "performance_to_now": 0.85,
                "quality_to_now": 0.98,
                "cumulative_cases": 1200,
                "total_throughput": 400,
                "machines_running": 15,
                "machines_stopped": 4,
            }
        ],
        "production_schedule": [{"planned_cases": 5000}],
        "line_constraints": [{"name": "max_speed"}],
        "gold_history_event_log": [{"event_id": "e1"}],
    }

    def fake_pq(sql, params, label=""):
        return list(data.get(label, []))

    monkeypatch.setattr(cb, "_parameterized_query", fake_pq)

    bundle = cb._build_context_uncached("shift_1", "2026-01-01T10:00:00")

    # scenario + elapsed/remaining math (10:00 is 120 min into an 8h shift)
    assert bundle["scenario"]["scenario_name"] == "Steady State"
    assert bundle["scenario"]["elapsed_minutes"] == 120.0
    assert bundle["scenario"]["remaining_minutes"] == 360.0

    # line_topology is the canonical order
    assert bundle["line_topology"] == cb.MACHINE_ORDER

    # line_state: error row dropped, sorted by physical position, position tagged
    ls = bundle["line_state"]
    assert [m["machine_id"] for m in ls] == ["DEPALLETIZER", "FILLER"]
    assert ls[0]["line_position"] == 1
    assert ls[1]["line_position"] == 7

    # KPI mapping
    assert bundle["current_kpi"]["oee_to_now"] == 0.75
    assert bundle["current_kpi"]["machines_running"] == 15

    # plan + reference data
    assert bundle["production_plan"] == {"planned_cases": 5000}
    assert bundle["constraints"] == [{"name": "max_speed"}]
    assert bundle["active_events"] == [{"event_id": "e1"}]


def test_build_context_defaults_when_scenario_missing(monkeypatch):
    monkeypatch.setattr(cb, "_parameterized_query", lambda *a, **k: [])
    bundle = cb._build_context_uncached("shift_1", "2026-01-01T10:00:00")
    # no scenario row → shift_start/end default to the query timestamp, so
    # elapsed/remaining collapse to 0 (not the malformed-row 480 fallback).
    assert bundle["scenario"]["scenario_name"] == "Unknown"
    assert bundle["scenario"]["remaining_minutes"] == 0.0
    assert bundle["current_kpi"]["oee_to_now"] is None
    assert bundle["production_plan"] == {}


def test_build_context_malformed_scenario_uses_8h_default(monkeypatch):
    data = {
        "scenario_index": [
            {"scenario_name": "Broken", "shift_start": "not-a-date", "shift_end": "nope"}
        ]
    }
    monkeypatch.setattr(cb, "_parameterized_query", lambda sql, params, label="": list(data.get(label, [])))
    bundle = cb._build_context_uncached("shift_1", "2026-01-01T10:00:00")
    # malformed shift bounds → default to a standard 8h shift
    assert bundle["scenario"]["elapsed_minutes"] == 0
    assert bundle["scenario"]["remaining_minutes"] == 480


# --------------------------------------------------------------------------- #
# _reference_shift_id — env-suffix stripping for framing/reference tables
# --------------------------------------------------------------------------- #
def test_reference_shift_id_strips_env_suffix():
    assert cb._reference_shift_id("shift_1_dev") == "shift_1"
    assert cb._reference_shift_id("shift_2_prod") == "shift_2"
    # no env suffix (prod live id / already-base) → unchanged
    assert cb._reference_shift_id("shift_1") == "shift_1"


def test_build_context_dev_shift_resolves_reference_tables(monkeypatch):
    """Regression: the dev live id (``shift_1_dev``) must resolve reference rows
    seeded under the base id (``shift_1``). Reference tables are bootstrap-seeded
    with the base id only; live tables are producer-written with the suffixed id.
    Before the fix the framing lookup missed → ``remaining_minutes`` collapsed to
    0 (the "Time remaining: 0 min" bug in the dev app)."""
    seen: dict[str, str | None] = {}

    def fake_pq(sql, params, label=""):
        sid = params.get("sid") if isinstance(params, dict) else None
        seen[label] = sid
        # Reference tables only have base-id rows; live tables only suffixed.
        if label == "scenario_index" and sid == "shift_1":
            return [{
                "scenario_name": "Steady State",
                "scenario_type": "normal",
                "shift_start": "2026-01-01T08:00:00",
                "shift_end": "2026-01-01T16:00:00",
            }]
        if label == "production_schedule" and sid == "shift_1":
            return [{"planned_cases": 5000}]
        return []

    monkeypatch.setattr(cb, "_parameterized_query", fake_pq)
    bundle = cb._build_context_uncached("shift_1_dev", "2026-01-01T10:00:00")

    # framing resolved via base id → real 8h window, not the 0-min fallback
    assert bundle["scenario"]["scenario_name"] == "Steady State"
    assert bundle["scenario"]["remaining_minutes"] == 360.0
    assert bundle["production_plan"] == {"planned_cases": 5000}
    # header still reflects the env-partitioned live id
    assert bundle["scenario"]["shift_id"] == "shift_1_dev"

    # reference/framing tables queried with the base id …
    for ref_label in (
        "scenario_index", "production_schedule", "shift_crew",
        "cip_schedule", "maintenance_schedule", "scenario_moments",
    ):
        assert seen[ref_label] == "shift_1", ref_label
    # … while producer-written live tables keep the suffixed id
    for live_label in (
        "gold_history_machine_state", "gold_history_event_log",
        "gold_history_line_kpi", "quality_checks_bronze", "inventory_status",
    ):
        assert seen[live_label] == "shift_1_dev", live_label
