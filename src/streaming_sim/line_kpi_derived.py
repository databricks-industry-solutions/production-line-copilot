"""Tier-1 line KPI derived metrics (single source of truth for producers + UI contract).

Uses the same structural-loss baselines as the dashboard (beverage packaging benchmarks).
"""

from __future__ import annotations

from typing import Any

# Align with ``client/src/App.tsx`` — structural losses not modeled per tick.
_BASELINE_AVAIL = 0.88
_PERF_MULTIPLIER = 0.95
_BASELINE_QUALITY_FRAC = 0.985


def compute_tier1_line_kpi_row(
    *,
    shift_id: str,
    ts: Any,
    machines_running: int,
    machines_stopped: int,
    machines_degraded: int,
    machines_starved: int,
    total_throughput: float,
    avg_speed_pct: float,
    cumulative_cases: float = 0.0,
) -> dict[str, Any]:
    """Return a full ``line_kpi_bronze`` / Gold ``line_kpi`` row including A/P/Q/OEE."""
    running = int(machines_running)
    stopped = int(machines_stopped)
    degraded = int(machines_degraded)
    starved = int(machines_starved)
    total = running + stopped + degraded + starved

    sim_avail = (running + degraded * 0.5) / total if total > 0 else 0.0
    availability_to_now = round(sim_avail * _BASELINE_AVAIL * 100.0, 1)
    performance_to_now = round(min(float(avg_speed_pct), 100.0) * _PERF_MULTIPLIER, 1)
    quality_to_now = round(_BASELINE_QUALITY_FRAC * 100.0, 1)
    oee_to_now = round(
        (availability_to_now / 100.0)
        * (performance_to_now / 100.0)
        * (quality_to_now / 100.0)
        * 100.0,
        1,
    )

    return {
        "shift_id": shift_id,
        "ts": ts,
        "machines_running": running,
        "machines_stopped": stopped,
        "machines_degraded": degraded,
        "machines_starved": starved,
        "total_throughput": round(float(total_throughput), 2),
        "avg_speed_pct": round(float(avg_speed_pct), 1),
        "availability_to_now": availability_to_now,
        "performance_to_now": performance_to_now,
        "quality_to_now": quality_to_now,
        "oee_to_now": oee_to_now,
        # Cases are a discrete count: floor the float accumulator to a whole
        # number on emit so the Cases tile never shows fractional cases (schema
        # is DOUBLE, so we keep the float type but pin it to an integer value).
        "cumulative_cases": float(int(cumulative_cases)),
    }
