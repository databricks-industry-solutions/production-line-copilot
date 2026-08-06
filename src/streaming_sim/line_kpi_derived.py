"""Tier-1 line KPI derived metrics (single source of truth for producers + UI contract).

Uses the same structural-loss baselines as the dashboard (beverage packaging benchmarks).

Availability and performance come in two flavours here, and the distinction is
the whole point of this module:

* **instantaneous** — computed from the current tick's machine counts. This is
  what the line is doing *right now*.
* **shift-to-date** — the time-average since the shift began, which is what
  ``*_to_now`` means and what an operator reads an OEE tile as. Availability is
  a plain time-average; performance is averaged over run time rather than
  planned time, per the standard definition, so that a stop is charged to
  availability once instead of to both factors at once.

Only the second belongs in the ``*_to_now`` columns. They used to carry the
first, so the instant a line stopped the row claimed ``oee_to_now = 0.0``
alongside ``cumulative_cases = 1965`` — a shift that had made nearly two
thousand cases reporting zero OEE for that shift. Downstream, the dashboard and
the assistant then disagreed about the same number, and the UI grew a
workaround that read a legitimate zero as a compute glitch.

Callers own the accumulator (``ShiftKpiAccumulator``) rather than this module
holding global state, so the maths stays pure and testable. A caller with no
shift history — one aggregating a snapshot of rows it just read — passes none
and gets the instantaneous values, which is the honest answer to the only
question it can actually answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Align with ``client/src/App.tsx`` — structural losses not modeled per tick.
_BASELINE_AVAIL = 0.88
_PERF_MULTIPLIER = 0.95
_BASELINE_QUALITY_FRAC = 0.985


@dataclass
class ShiftKpiAccumulator:
    """Running shift-to-date mean of availability and performance.

    Ticks arrive at a fixed cadence, so an unweighted mean over ticks is the
    time-average.

    Two reset paths, because a shift ends in two different ways. ``observe``
    resets on a change of shift id, which covers a caller that tracks several
    shifts. The streaming producer is not that caller: its shift id comes from
    ``SIM_SHIFT_ID`` and never changes, while its shift *window* rolls over on
    the clock — so it calls ``reset`` explicitly at the same point it zeroes
    ``cumulative_cases``. Without that, one shift's OEE would inherit the
    previous one's downtime forever.
    """

    shift_id: str = ""
    ticks: int = 0
    availability_sum: float = 0.0
    # Performance is weighted by availability, because the two are not
    # independent: a stopped line has no availability AND no speed, so a plain
    # mean of performance charges the same downtime a second time and the
    # product of the two means squares the penalty. The standard definition
    # already says so — performance is output over RUN time, not over planned
    # time — and downtime is availability's to account for.
    #
    # This is not a rounding-level difference. Six hours into a dev shift that
    # had made 3,732 of a 6,283-case pace, the tile read 43.5% where the cases
    # on the floor say about 59%: sixteen points of OEE that no loss on the line
    # corresponds to. Weighting restores the identity that makes the split
    # meaningful, A x P = the time-average of instantaneous A x P, so the three
    # factors multiply back to what actually came off the line.
    weighted_performance_sum: float = 0.0
    availability_weight: float = 0.0

    def reset(self, shift_id: str = "") -> None:
        """Start a new shift's tally."""
        self.shift_id = shift_id or self.shift_id
        self.ticks = 0
        self.availability_sum = 0.0
        self.weighted_performance_sum = 0.0
        self.availability_weight = 0.0

    def observe(self, shift_id: str, availability: float, performance: float) -> tuple[float, float]:
        """Record one tick and return the shift-to-date (availability, performance)."""
        if shift_id != self.shift_id:
            self.reset(shift_id)
        self.ticks += 1
        self.availability_sum += availability
        self.weighted_performance_sum += availability * performance
        self.availability_weight += availability
        mean_availability = self.availability_sum / self.ticks
        if self.availability_weight <= 0.0:
            # Nothing has run yet, so there is no rate to report. Carrying this
            # tick's performance would describe a line that has not moved.
            return mean_availability, 0.0
        return (
            mean_availability,
            self.weighted_performance_sum / self.availability_weight,
        )


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
    accumulator: ShiftKpiAccumulator | None = None,
) -> dict[str, Any]:
    """Return a full ``line_kpi_bronze`` / Gold ``line_kpi`` row including A/P/Q/OEE.

    Pass ``accumulator`` to get true shift-to-date A/P/OEE. Without it the
    ``*_to_now`` fields carry this tick's values, because a caller with no shift
    history has nothing else to give.
    """
    running = int(machines_running)
    stopped = int(machines_stopped)
    degraded = int(machines_degraded)
    starved = int(machines_starved)
    total = running + stopped + degraded + starved

    sim_avail = (running + degraded * 0.5) / total if total > 0 else 0.0
    availability_now = round(sim_avail * _BASELINE_AVAIL * 100.0, 1)
    performance_now = round(min(float(avg_speed_pct), 100.0) * _PERF_MULTIPLIER, 1)

    if accumulator is not None:
        availability_to_now, performance_to_now = accumulator.observe(
            shift_id, availability_now, performance_now
        )
        availability_to_now = round(availability_to_now, 1)
        performance_to_now = round(performance_to_now, 1)
    else:
        availability_to_now, performance_to_now = availability_now, performance_now

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
