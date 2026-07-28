"""Tests for the TTL + single-flight cache around build_context().

Covers:
    - cache HIT inside TTL: identical args return the cached bundle and
      don't redo the SQL fan-out.
    - cache MISS after TTL expiry: a fresh build runs.
    - cache MISS for different args (shift_id / timestamp / machine_id).
    - shallow-copy semantics: mutating the returned dict's top-level keys
      doesn't corrupt the cached bundle (this matches how
      agent.py:_overlay_live_state reassigns operational slices).
    - cache_clear(): forces a rebuild.
    - CONTEXT_CACHE_TTL_SECONDS=0 disables caching.
    - Single-flight: concurrent first-callers for the same key collapse
      into ONE underlying build.

The tests monkeypatch the inner `_build_context_uncached` so we don't
hit a real SQL warehouse — these stay pure unit tests.
"""

from __future__ import annotations

import threading
import time

import pytest

from agent_server import context_builder


@pytest.fixture(autouse=True)
def _clear_cache():
    """Every test starts with an empty cache and the production-default TTL."""
    context_builder.build_context.cache_clear()
    original_ttl = context_builder._CONTEXT_CACHE_TTL_S
    yield
    context_builder._CONTEXT_CACHE_TTL_S = original_ttl
    context_builder.build_context.cache_clear()


def _make_counter_builder(monkeypatch, delay_s: float = 0.0):
    """Replace the inner builder with a counter that returns a fresh bundle.

    Returns the counter list (so tests can inspect call count) and the
    bundle factory.
    """
    calls: list[tuple] = []

    def fake_build(shift_id, timestamp, machine_id=None):
        calls.append((shift_id, timestamp, machine_id))
        if delay_s:
            time.sleep(delay_s)
        return {
            "scenario": {"shift_id": shift_id, "current_timestamp": timestamp},
            "machine_id": machine_id,
            "line_state": [{"machine_id": "M1"}],
            "build_seq": len(calls),
        }

    monkeypatch.setattr(context_builder, "_build_context_uncached", fake_build)
    return calls


def test_cache_hit_within_ttl_reuses_bundle(monkeypatch):
    """Two calls in <TTL should produce one underlying build."""
    calls = _make_counter_builder(monkeypatch)
    context_builder._CONTEXT_CACHE_TTL_S = 5.0

    a = context_builder.build_context("shift_1", "2026-06-26T08:00:00")
    b = context_builder.build_context("shift_1", "2026-06-26T08:00:00")

    assert len(calls) == 1, "second call should HIT the cache"
    assert a["build_seq"] == 1
    assert b["build_seq"] == 1, "cached bundle should be returned unchanged"


def test_cache_miss_after_ttl_expiry(monkeypatch):
    """A call after TTL elapses should rebuild."""
    calls = _make_counter_builder(monkeypatch)
    context_builder._CONTEXT_CACHE_TTL_S = 0.05  # 50ms

    context_builder.build_context("shift_1", "2026-06-26T08:00:00")
    time.sleep(0.10)
    context_builder.build_context("shift_1", "2026-06-26T08:00:00")

    assert len(calls) == 2, "second call should rebuild after TTL expiry"


def test_cache_miss_on_different_keys(monkeypatch):
    """Distinct (shift_id, timestamp, machine_id) tuples are independent keys."""
    calls = _make_counter_builder(monkeypatch)
    context_builder._CONTEXT_CACHE_TTL_S = 5.0

    context_builder.build_context("shift_1", "2026-06-26T08:00:00")
    context_builder.build_context("shift_2", "2026-06-26T08:00:00")  # diff shift
    context_builder.build_context("shift_1", "2026-06-26T08:00:01")  # diff ts
    context_builder.build_context("shift_1", "2026-06-26T08:00:00", "M1")  # diff machine

    assert len(calls) == 4, "every distinct key should miss the cache"


def test_returned_bundle_is_shallow_copy(monkeypatch):
    """Reassigning a top-level key on the returned dict must not poison cache.

    This mirrors how `agent.py:_overlay_live_state` overwrites operational
    slices (`context['line_state'] = [...]`).
    """
    _make_counter_builder(monkeypatch)
    context_builder._CONTEXT_CACHE_TTL_S = 5.0

    a = context_builder.build_context("shift_1", "2026-06-26T08:00:00")
    a["line_state"] = "MUTATED"

    b = context_builder.build_context("shift_1", "2026-06-26T08:00:00")
    assert b["line_state"] == [{"machine_id": "M1"}], (
        "cache must not be corrupted by caller mutations of the returned dict"
    )


def test_cache_clear_forces_rebuild(monkeypatch):
    """build_context.cache_clear() drops all entries; the next call rebuilds."""
    calls = _make_counter_builder(monkeypatch)
    context_builder._CONTEXT_CACHE_TTL_S = 5.0

    context_builder.build_context("shift_1", "2026-06-26T08:00:00")
    context_builder.build_context.cache_clear()
    context_builder.build_context("shift_1", "2026-06-26T08:00:00")

    assert len(calls) == 2


def test_ttl_zero_disables_cache(monkeypatch):
    """CONTEXT_CACHE_TTL_SECONDS=0 makes every call a real build."""
    calls = _make_counter_builder(monkeypatch)
    context_builder._CONTEXT_CACHE_TTL_S = 0.0

    for _ in range(3):
        context_builder.build_context("shift_1", "2026-06-26T08:00:00")

    assert len(calls) == 3, "cache should be disabled when TTL <= 0"


def test_single_flight_collapses_concurrent_first_callers(monkeypatch):
    """N concurrent threads on a cold key should trigger exactly ONE build.

    Without the per-key build lock, a thundering-herd of identical requests
    would each kick off the full warehouse fan-out and hammer connection
    establishment. With single-flight, the first caller builds and the
    other N-1 callers wait, then read the cached bundle.
    """
    calls = _make_counter_builder(monkeypatch, delay_s=0.10)
    context_builder._CONTEXT_CACHE_TTL_S = 5.0

    barrier = threading.Barrier(8)
    results: list[dict] = []
    results_lock = threading.Lock()

    def worker():
        barrier.wait()
        bundle = context_builder.build_context("shift_1", "2026-06-26T08:00:00")
        with results_lock:
            results.append(bundle)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(calls) == 1, (
        f"expected single-flight to collapse 8 concurrent callers into 1 "
        f"build; got {len(calls)} builds"
    )
    assert len(results) == 8
    assert all(r["build_seq"] == 1 for r in results)
