"""The live stream must not hold the event loop hostage.

The SSE generator reads Lakebase with blocking psycopg2 calls. FastAPI runs
async endpoints on a single event loop thread, so a read that blocks there
freezes every other request the app is serving. That is exactly the shape of a
Lakebase outage: each read waits out a connect timeout, and a dashboard tab
open in the corner of the room takes the whole app down with it.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest


@pytest.fixture
def app_with_slow_lakebase(monkeypatch):
    """The agent server, with every Lakebase read stalled like a bad connect."""
    from agent_server import start_server as ss
    from agent_server.serving import realtime_store

    stall_s = 0.4

    def slow_rows(*_a, **_k):
        time.sleep(stall_s)
        return []

    def slow_pair(*_a, **_k):
        time.sleep(stall_s)
        return [], None

    def slow_kpi(*_a, **_k):
        time.sleep(stall_s)
        return None, None

    def slow_ts(*_a, **_k):
        time.sleep(stall_s)
        return None

    monkeypatch.setattr(realtime_store, "read_machine_state_after", slow_pair)
    monkeypatch.setattr(realtime_store, "read_inventory_after", slow_pair)
    monkeypatch.setattr(realtime_store, "read_line_kpi_after", slow_kpi)
    monkeypatch.setattr(realtime_store, "read_max_history_ts", slow_ts)
    monkeypatch.setattr(realtime_store, "read_event_log_recent", slow_rows)
    monkeypatch.setattr(realtime_store, "read_commands_active", slow_rows)
    monkeypatch.setattr(ss, "_startup_sql_probe", lambda: None, raising=False)
    return ss.app, stall_s


async def _max_loop_lag(duration_s: float, step_s: float = 0.02) -> float:
    """Largest gap between when a sleep should have woken and when it did.

    This is the direct measure of "something blocked the loop": an awaited
    ``sleep(0.02)`` can only overrun if no other coroutine yielded control.
    Measuring a request's latency instead is misleading, because a request
    issued after the block simply completes fast.
    """
    worst = 0.0
    deadline = time.monotonic() + duration_s
    while time.monotonic() < deadline:
        before = time.monotonic()
        await asyncio.sleep(step_s)
        worst = max(worst, time.monotonic() - before - step_s)
    return worst


async def test_the_stream_does_not_block_the_loop_while_reading_lakebase(
    app_with_slow_lakebase,
):
    app, stall_s = app_with_slow_lakebase
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:

        async def drain_stream():
            try:
                async with client.stream("GET", "/serving/stream", timeout=10.0) as r:
                    async for _ in r.aiter_lines():
                        pass
            except Exception:
                pass

        streamer = asyncio.create_task(drain_stream())
        try:
            # Long enough to cover the cursor-anchor read plus a full tick.
            lag = await _max_loop_lag(3.0)
        finally:
            streamer.cancel()
            try:
                await streamer
            except BaseException:
                pass

        resp = await client.get("/health", timeout=10.0)

    assert resp.status_code == 200
    # A tick makes six of these reads. On the loop, lag lands at ~stall_s or
    # worse; dispatched to a worker thread it stays in the milliseconds.
    assert lag < stall_s, (
        f"event loop stalled for {lag:.2f}s while the SSE stream read Lakebase — "
        "the blocking reads are back on the event loop"
    )
