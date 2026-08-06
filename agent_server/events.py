"""Which events are actually open, server-side.

The event log is append-only — it shadows a Zerobus bronze ingest, so the
producer cannot close a row it already wrote. It appends a synthetic
``event_type='resolved'`` row for the machine instead, and a reader pairs the
two up. ``timestamp_end IS NULL`` therefore does not mean "still happening"; it
means "this row was written as an opener", which every incident row is.

The browser has known this since the event feed was built
(``client/src/lib/eventFeedUtils.ts``). The server never did. So a question
about one machine could be answered against another machine's incidents that
had been resolved an hour and a half earlier — the snapshot listed three open
DEPALLETIZER breakdowns while the line state, read from a different source,
correctly reported the only issue as FILLER. Specialists saw the same list
under the name ``active_events`` and reasoned from it.

This is the Python side of that rule, deliberately mirroring the TypeScript so
the two surfaces cannot drift: same closure semantics, same eight-hour bound on
an event whose resolve marker never arrives.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Iterable

logger = logging.getLogger(__name__)

# Mirrors STALE_AFTER_MS in client/src/lib/eventFeedUtils.ts. One shift, because
# carryover is defined against the current shift: an unresolved event older than
# that was either resolved without a marker or is stale data, and neither is
# something to act on now.
STALE_AFTER_SECONDS = 8 * 60 * 60

RESOLVED_EVENT_TYPE = "resolved"


def to_epoch(value: Any) -> float:
    """Seconds since the epoch, or 0.0 when the value cannot be read.

    Rows reach here from two places with two shapes: Lakebase hands back
    ``datetime`` objects, Unity Catalog hands back ISO strings (sometimes
    Z-suffixed, which ``fromisoformat`` rejects before 3.11). 0.0 is the
    "unreadable" signal and callers must not age those out — see
    ``is_event_active``.
    """
    if value is None or value == "":
        return 0.0
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    if isinstance(value, (int, float)):
        return float(value)
    try:
        text = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()
    except (ValueError, TypeError):
        return 0.0


def resolved_ts_by_machine(events: Iterable[dict]) -> dict[str, float]:
    """machine_id → the latest resolve marker written for it."""
    latest: dict[str, float] = {}
    for e in events:
        if e.get("event_type") != RESOLVED_EVENT_TYPE:
            continue
        machine = e.get("machine_id")
        if not machine:
            continue
        ts = to_epoch(e.get("timestamp_start"))
        if ts > latest.get(machine, 0.0):
            latest[machine] = ts
    return latest


def is_event_active(
    event: dict,
    now_ts: float,
    resolved: dict[str, float] | None = None,
) -> bool:
    """Whether one event is still open as of ``now_ts``."""
    if event.get("event_type") == RESOLVED_EVENT_TYPE:
        return False

    end = event.get("timestamp_end")
    if end:
        return to_epoch(end) > now_ts

    start_ts = to_epoch(event.get("timestamp_start"))
    if resolved:
        marker = resolved.get(event.get("machine_id") or "", 0.0)
        if marker > 0.0 and marker >= start_ts:
            return False

    # Only age out a start we could actually read; ageing out an unparseable one
    # would hide live rows.
    if start_ts > 0 and now_ts - start_ts > STALE_AFTER_SECONDS:
        return False
    return True


def active_events(events: Iterable[dict], now_ts: float | None = None) -> list[dict]:
    """The subset of ``events`` still open, in the order given.

    Resolve markers have to be collected across the whole list before any single
    event can be judged, so this takes the list rather than being a predicate
    callers can filter with.
    """
    rows = list(events)
    if now_ts is None:
        now_ts = datetime.now(timezone.utc).timestamp()
    resolved = resolved_ts_by_machine(rows)
    return [e for e in rows if is_event_active(e, now_ts, resolved)]


SHIFT_WINDOW_MINUTES = 480


def shift_progress(now: datetime | None = None) -> tuple[float, float]:
    """Minutes elapsed and remaining in the UTC-aligned 8h shift containing ``now``.

    Mirrors ``src/streaming_sim/producer._shift_window_start`` and
    ``client/src/lib/shiftWindow.ts``: boundaries at 00:00, 08:00 and 16:00 UTC.
    Because they are deterministic, the position in the shift follows from the
    clock alone and cannot drift from what the producer resets against or what
    the header shows.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    start = now.replace(hour=(now.hour // 8) * 8, minute=0, second=0, microsecond=0)
    elapsed = (now - start).total_seconds() / 60.0
    elapsed = min(SHIFT_WINDOW_MINUTES, max(0.0, elapsed))
    return round(elapsed, 1), round(SHIFT_WINDOW_MINUTES - elapsed, 1)


def context_now_ts(context: dict) -> float:
    """The clock to judge events against: the scenario's, falling back to ours.

    The simulated line carries its own timestamp, and judging its events against
    wall-clock time would age them wrongly whenever the two diverge.
    """
    scenario = context.get("scenario") or {}
    ts = to_epoch(scenario.get("current_timestamp"))
    return ts if ts > 0 else datetime.now(timezone.utc).timestamp()
