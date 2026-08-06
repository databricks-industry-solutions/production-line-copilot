"""Cascade engine — buffer-based propagation of faults and recovery across a
bottling line.

The line is a sequence of machines connected by finite buffers.  When a machine
stops, its output drops to zero.  Downstream machines drain their input buffers
then starve; upstream machines fill their output buffers then block.  Recovery
reverses the process.

The engine is pure — no I/O, no randomness, fully deterministic for a given
input.  This makes it straightforward to unit-test.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from src.streaming_sim.line_kpi_derived import (
    ShiftKpiAccumulator,
    compute_tier1_line_kpi_row,
)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class MachineSpec:
    """Static definition of a machine on the line."""
    id: str
    line_position: int
    nominal_capacity: float  # items/min at 100 % speed
    # Conveyors carry product rather than process it, so they are given a
    # sentinel capacity to keep them from ever bottlenecking the line. That
    # sentinel then leaked into ``speed_pct`` as flow/9999, and a healthy belt
    # reported 2 %. The line average already excluded conveyors as meaningless,
    # but the tile and every specialist reading machine_state still saw it and
    # would reasonably call a running conveyor nearly stopped. A belt runs at
    # its set speed or it does not run, so we leave its speed to fault state.
    pass_through: bool = False

    def __hash__(self) -> int:
        return hash(self.id)


@dataclass
class BufferState:
    """Mutable state of the buffer between two adjacent machines."""
    capacity: float  # max items
    level: float     # current items

    def fill_ratio(self) -> float:
        return self.level / self.capacity if self.capacity > 0 else 1.0


@dataclass
class MachineState:
    """Mutable per-tick state of a single machine."""
    status: str = "running"           # running | stopped | degraded | starved | blocked
    throughput: float = 0.0           # items/min this tick
    speed_pct: float = 100.0          # 0-105
    alarm_count: int = 0


@dataclass
class LineState:
    """Complete snapshot of the line for one tick."""
    machines: dict[str, MachineState] = field(default_factory=dict)
    buffers: dict[tuple[str, str], BufferState] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Line builder
# ---------------------------------------------------------------------------

def build_line(specs: list[MachineSpec], buffer_seconds: float = 5.0) -> LineState:
    """Create a fresh LineState with all machines running and proportional buffers.

    Each buffer holds ``buffer_seconds`` worth of the faster neighbour's capacity,
    starting at 80 % full — comfortably above the drain threshold so every machine
    boots at 100 % speed.

    A pass-through neighbour is skipped when sizing. Conveyors carry a sentinel
    capacity so they never bottleneck, and taking the max included that
    sentinel: a buffer beside a conveyor came out at 833 units where its
    neighbours justify about 25. Faults then took roughly twenty-four times
    longer to cross a conveyor than any other pair, so a stop upstream of one
    looked contained for minutes after the line had really begun to starve.
    """
    specs_sorted = sorted(specs, key=lambda s: s.line_position)
    machines = {s.id: MachineState(throughput=s.nominal_capacity) for s in specs_sorted}
    buffers: dict[tuple[str, str], BufferState] = {}
    for a, b in zip(specs_sorted, specs_sorted[1:]):
        rated = [s.nominal_capacity for s in (a, b) if not s.pass_through]
        if not rated:  # conveyor to conveyor: neither has a real rate to size on
            rated = [a.nominal_capacity, b.nominal_capacity]
        cap = max(rated) * buffer_seconds / 60.0
        cap = max(cap, 10.0)
        buffers[(a.id, b.id)] = BufferState(capacity=cap, level=cap * 0.8)
    return LineState(machines=machines, buffers=buffers)


# ---------------------------------------------------------------------------
# Cascade tick
# ---------------------------------------------------------------------------

_DRAIN_THRESHOLD = 0.6   # buffer fill below this → downstream starts slowing
_FILL_THRESHOLD = 0.85   # buffer fill above this → upstream starts slowing


def cascade_tick(
    specs: list[MachineSpec],
    state: LineState,
    tick_seconds: float = 1.0,
) -> LineState:
    """Advance the line by one tick with **gradual** propagation.

    When a machine stops, its immediate neighbours keep running at full speed
    while their buffers still have headroom.  As buffers drain below
    ``_DRAIN_THRESHOLD`` (60 %), the machine's effective capacity drops
    proportionally — creating a smooth, visible decline that ripples outward
    one machine at a time.  Recovery reverses the process as buffers refill.

    Algorithm:
      1. Reset derived statuses (starved/blocked → running).
      2. Compute effective capacity with buffer-proportional attenuation.
      3. Forward pass — production rate per machine.
      4. Buffer update — net in/out per buffer.
      5. Status pass — derive speed_pct + starved/blocked from actual flow.
    """
    ordered = sorted(specs, key=lambda s: s.line_position)
    ids = [s.id for s in ordered]
    cap_map = {s.id: s.nominal_capacity for s in ordered}
    pass_through = {s.id: s.pass_through for s in ordered}
    tick_min = tick_seconds / 60.0

    # 1. Reset derived statuses — faulted (stopped/degraded) are preserved.
    #    Also reset speed_pct for non-faulted machines so the buffer attenuation
    #    is the sole determinant of actual speed (avoids compounding with stale pct).
    for mid in ids:
        ms = state.machines[mid]
        if ms.status in ("starved", "blocked"):
            ms.status = "running"
        if ms.status not in ("stopped", "degraded"):
            ms.speed_pct = 100.0

    # 2. Effective capacity with buffer-level attenuation.
    eff_cap: dict[str, float] = {}
    for i, mid in enumerate(ids):
        ms = state.machines[mid]
        if ms.status == "stopped":
            eff_cap[mid] = 0.0
            continue

        base = cap_map[mid] * (ms.speed_pct / 100.0)

        # Input starvation: proportional slowdown as input buffer drains
        if i > 0:
            prev = ids[i - 1]
            in_buf = state.buffers.get((prev, mid))
            if in_buf is not None and in_buf.fill_ratio() < _DRAIN_THRESHOLD:
                factor = max(0.0, in_buf.fill_ratio() / _DRAIN_THRESHOLD)
                base *= factor

        # Output blocking: proportional slowdown as output buffer fills
        if i + 1 < len(ids):
            nxt = ids[i + 1]
            out_buf = state.buffers.get((mid, nxt))
            if out_buf is not None and out_buf.fill_ratio() > _FILL_THRESHOLD:
                space = max(0.0, (1.0 - out_buf.fill_ratio()) / (1.0 - _FILL_THRESHOLD))
                base *= space

        eff_cap[mid] = base

    # 3. Forward pass — production rate per machine.
    flow: dict[str, float] = {}
    for i, mid in enumerate(ids):
        if i == 0:
            input_avail = eff_cap[mid]
        else:
            prev = ids[i - 1]
            in_buf = state.buffers.get((prev, mid))
            buf_rate = min(in_buf.level / max(tick_min, 1e-9), cap_map[mid]) if in_buf else 0.0
            input_avail = flow[prev] + buf_rate

        output_limit = eff_cap[mid]
        if i + 1 < len(ids):
            nxt = ids[i + 1]
            out_buf = state.buffers.get((mid, nxt))
            if out_buf is not None and eff_cap[nxt] < 1.0:
                remaining = max(0.0, out_buf.capacity - out_buf.level)
                output_limit = min(output_limit, remaining / max(tick_min, 1e-9))

        flow[mid] = min(eff_cap[mid], input_avail, output_limit)

    # 4. Buffer update.
    for a, b in zip(ids, ids[1:]):
        buf = state.buffers.get((a, b))
        if buf is not None:
            net = (flow[a] - flow[b]) * tick_min
            buf.level = max(0.0, min(buf.capacity, buf.level + net))

    # 5. Status pass — derive speed_pct from actual flow, set starved/blocked.
    for i, mid in enumerate(ids):
        ms = state.machines[mid]
        ms.throughput = flow[mid]

        if ms.status == "stopped":
            ms.throughput = 0.0
            continue
        if ms.status == "degraded":
            continue

        if cap_map[mid] > 0 and not pass_through.get(mid, False):
            ms.speed_pct = max(0.0, min(100.0, flow[mid] / cap_map[mid] * 100.0))

        # Starved: input buffer critically low and upstream not producing
        if i > 0:
            prev = ids[i - 1]
            in_buf = state.buffers.get((prev, mid))
            if in_buf is not None and in_buf.fill_ratio() < 0.1 and flow[prev] < 1.0:
                ms.status = "starved"
                continue

        # Blocked: output buffer nearly full and downstream effectively stopped
        if i + 1 < len(ids):
            nxt = ids[i + 1]
            out_buf = state.buffers.get((mid, nxt))
            if (out_buf is not None and out_buf.fill_ratio() > 0.95
                    and eff_cap[nxt] < 1.0 and ms.status != "starved"):
                ms.status = "blocked"

    return state


# ---------------------------------------------------------------------------
# Helpers for the producer
# ---------------------------------------------------------------------------

def apply_fault(state: LineState, machine_id: str, fault_status: str = "stopped",
                speed_pct: float = 0.0, extra_alarms: int = 0) -> None:
    """Apply a direct fault to a machine (e.g. from an incident command)."""
    ms = state.machines.get(machine_id)
    if ms is None:
        return
    ms.status = fault_status
    ms.speed_pct = speed_pct
    ms.alarm_count += extra_alarms
    if fault_status == "stopped":
        ms.throughput = 0.0


def clear_fault(state: LineState, machine_id: str) -> None:
    """Remove a fault — machine returns to running at 100 %."""
    ms = state.machines.get(machine_id)
    if ms is None:
        return
    ms.status = "running"
    ms.speed_pct = 100.0
    ms.alarm_count = 0


def snapshot_to_dicts(
    state: LineState,
    shift_id: str,
    timestamp: Any,
    *,
    rng: random.Random | None = None,
) -> list[dict[str, Any]]:
    """Convert the line state into a list of flat dicts ready for Zerobus ingest.

    Real factory sensors jitter ~±1-2 % around steady state even when nothing
    is changing; without that, the dashboard looks frozen because the cascade
    converges to a deterministic fixed point. We add the jitter HERE (not in
    ``cascade_tick``) so it doesn't perturb the cascade's flow accounting —
    the line-state machinery stays deterministic, only the *emitted* sensor
    snapshot wobbles.

    Faulted machines (stopped / degraded / starved / blocked) keep their exact
    fault values so incident behaviour stays clean.
    """
    rows: list[dict[str, Any]] = []
    for mid, ms in state.machines.items():
        if rng is not None and ms.status == "running":
            tput_jitter = rng.uniform(-0.015, 0.015) * ms.throughput
            speed_jitter = rng.uniform(-1.0, 1.0)
            tput = max(0.0, ms.throughput + tput_jitter)
            speed = max(0.0, min(100.0, ms.speed_pct + speed_jitter))
        else:
            tput = ms.throughput
            speed = ms.speed_pct
        rows.append({
            "shift_id": shift_id,
            "machine_id": mid,
            "ts": timestamp,
            "status": ms.status,
            "throughput_per_min": round(tput, 2),
            "speed_pct": round(speed, 1),
            "alarm_count": ms.alarm_count,
        })
    return rows


def _last_station(state: LineState) -> str | None:
    """The machine at the end of the line — the one nothing is fed from.

    Derived from the buffer adjacency rather than dict order, so it stays
    correct if a caller builds a LineState by hand. Falls back to insertion
    order, which ``build_line`` sorts by line position.
    """
    upstream = {a for a, _ in state.buffers}
    tail = [mid for mid in state.machines if mid not in upstream]
    if len(tail) == 1:
        return tail[0]
    return next(reversed(state.machines), None) if state.machines else None


def compute_line_kpi(
    state: LineState,
    shift_id: str,
    timestamp: Any,
    *,
    exclude_from_speed: set[str] | None = None,
    cumulative_cases: float = 0.0,
    machine_rows: list[dict[str, Any]] | None = None,
    accumulator: ShiftKpiAccumulator | None = None,
) -> dict[str, Any]:
    """Aggregate machine states into a single line-KPI row.

    ``exclude_from_speed`` – machine IDs to omit from ``avg_speed_pct``
    (e.g. conveyors whose throughput/capacity ratio is meaningless).
    ``cumulative_cases`` – the producer's running tally of cases produced
    since shift start; passed through to the gold row.
    ``accumulator`` – the caller's shift-to-date A/P tally. Supply it and the
    ``*_to_now`` fields mean what they say; omit it and they carry this tick's
    values, which is all a stateless caller can honestly report.
    ``machine_rows`` – optional list of jittered snapshot dicts (from
    ``snapshot_to_dicts(..., rng=...)``); when provided, throughput and
    speed are summed/averaged from these jittered values so the line KPI
    inherits realistic sensor noise. When ``None`` we fall back to the
    deterministic cascade state (matches the legacy / test contract).
    """
    running = stopped = degraded = starved = 0
    speed_sum = 0.0
    speed_count = 0
    _excl = exclude_from_speed or set()

    # Both flow-starved states land in `starved` below: the KPI row has no
    # machines_blocked column to put the other one in. Keep it that way only as
    # long as both descriptions say so — starved (no input) and blocked
    # (downstream full) point an operator in opposite directions, so a counter
    # labelled "starved by upstream stoppage" sends half its cases the wrong
    # way. Per-machine status keeps the two apart for anyone who needs them.

    # Line output is what leaves the last station, not the sum of all of them.
    # This is a series line: every bottle passes through all nineteen machines,
    # so adding their rates counts the same bottle nineteen times. The tile read
    # 5,680/min on a line whose real rate is ~300 — and the column is documented
    # to Genie as "cases per minute", which it was out by a factor of 450.
    tput_by_id = (
        {r.get("machine_id"): float(r.get("throughput_per_min") or 0.0) for r in machine_rows}
        if machine_rows is not None
        else {mid: ms.throughput for mid, ms in state.machines.items()}
    )
    last = _last_station(state)
    total_tput = tput_by_id.get(last, 0.0)

    if machine_rows is not None:
        for r in machine_rows:
            mid = r.get("machine_id")
            if mid not in _excl:
                speed_sum += float(r.get("speed_pct") or 0.0)
                speed_count += 1
            st = r.get("status")
            if st == "running":
                running += 1
            elif st == "stopped":
                stopped += 1
            elif st == "degraded":
                degraded += 1
            elif st in ("starved", "blocked"):
                starved += 1
    else:
        for mid, ms in state.machines.items():
            if mid not in _excl:
                speed_sum += ms.speed_pct
                speed_count += 1
            if ms.status == "running":
                running += 1
            elif ms.status == "stopped":
                stopped += 1
            elif ms.status == "degraded":
                degraded += 1
            elif ms.status in ("starved", "blocked"):
                starved += 1

    avg_sp = round(speed_sum / max(speed_count, 1), 1)

    return compute_tier1_line_kpi_row(
        shift_id=shift_id,
        ts=timestamp,
        machines_running=running,
        machines_stopped=stopped,
        machines_degraded=degraded,
        machines_starved=starved,
        total_throughput=round(total_tput, 2),
        avg_speed_pct=avg_sp,
        cumulative_cases=float(cumulative_cases),
        accumulator=accumulator,
    )
