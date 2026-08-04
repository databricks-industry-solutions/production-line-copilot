"""Guard against blocking the event loop from an ``async def`` route.

FastAPI runs a ``def`` handler in its threadpool but an ``async def`` handler
directly on the event loop. A handler declared ``async`` that then makes a
blocking call — a warehouse round trip, an SDK HTTP call — stops the entire
process for its duration: every other request queues behind it and the
``/serving/stream`` SSE ticks stop arriving.

That is not hypothetical here. ``/ops/cost-summary`` was written this way and
measured against dev froze the live stream for 44.9 seconds against its normal
1.04 s tick, because six sequential billing queries plus a query-history
aggregate all ran inline. The fix is the pattern the rest of this module
already uses: keep the work in a plain ``def`` and ``await asyncio.to_thread``
it from a thin route wrapper.

This test reads the source rather than exercising the routes because the defect
is structural — it is visible in the shape of the function and invisible in a
unit test that stubs the slow call out.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

SERVER = pathlib.Path(__file__).resolve().parents[2] / "agent_server" / "start_server.py"

# Calls that go over the network and therefore must never run inline in a
# coroutine. Matched as substrings of the unparsed function body.
BLOCKING_MARKERS = (
    "_sql.invoke",
    "_query_uc(",
    "w.jobs.",
    "w.warehouses.",
)

# Known offenders, kept explicit so this test documents the debt instead of
# hiding it. `ops_control_action` issues a handful of Jobs API calls inline;
# far milder than the cost panel's tens of seconds, but the same defect.
# Removing an entry here should only ever happen by fixing the handler.
KNOWN_BLOCKING = {"ops_control_action"}


def _route_handlers() -> list[ast.AsyncFunctionDef]:
    tree = ast.parse(SERVER.read_text())
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        if any(ast.unparse(d).startswith("app.") for d in node.decorator_list):
            out.append(node)
    return out


def test_the_scan_finds_the_routes_at_all() -> None:
    """A guard whose matcher silently stops matching guards nothing."""
    handlers = _route_handlers()
    assert len(handlers) > 10
    assert any(h.name == "ops_cost_summary" for h in handlers)


@pytest.mark.parametrize("handler", _route_handlers(), ids=lambda h: h.name)
def test_async_routes_do_not_block_the_event_loop(handler: ast.AsyncFunctionDef) -> None:
    source = ast.unparse(handler)
    found = [m for m in BLOCKING_MARKERS if m in source]
    if not found:
        return
    if handler.name in KNOWN_BLOCKING:
        pytest.xfail(f"known pre-existing blocking handler: {handler.name}")

    # An inline blocking call is only safe if it sits inside a nested plain
    # `def` that gets handed to a thread, which is the established pattern.
    offloads = "asyncio.to_thread" in source or "run_in_threadpool" in source
    assert offloads, (
        f"{handler.name} is `async def` and calls {found} directly on the event loop. "
        "Every request and every SSE tick stops for as long as that takes. "
        "Move the work into a plain `def` and `await asyncio.to_thread(...)` it."
    )


def test_cost_summary_offloads_and_its_worker_stays_synchronous() -> None:
    """The specific regression that motivated this file."""
    tree = ast.parse(SERVER.read_text())
    route = next(n for n in ast.walk(tree)
                 if isinstance(n, ast.AsyncFunctionDef) and n.name == "ops_cost_summary")
    worker = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "_cost_summary_payload")

    assert "asyncio.to_thread(_cost_summary_payload" in ast.unparse(route)
    # The route must stay thin: if billing queries creep back into it they are
    # back on the event loop.
    assert not any(m in ast.unparse(route) for m in BLOCKING_MARKERS)
    # And the worker must stay a plain def — making it `async` would silently
    # undo the fix, since `to_thread` would then just hand back a coroutine.
    assert isinstance(worker, ast.FunctionDef)
