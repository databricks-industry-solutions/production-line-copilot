"""Unit reconciliation for values that reach the optimizers from an LLM.

OEE is carried as a 0-100 percentage everywhere the model can see it —
``line_kpi.oee_to_now`` is a percentage (``serving/silver_mapper.py``), the
context bundle passes it through unchanged, and ``prompts/oee_coach.py`` tells
the model in as many words that "all values are percentages (0-100)".

The Monte Carlo simulators need a 0-1 fraction, and they used to enforce that
with ``min(1.0, ...)``. A model that followed its instructions and passed 75.0
therefore had every one of its 1000 simulations clamped to a perfect OEE of
1.0, producing P10 == P50 == P90 at the theoretical maximum — a confident
forecast of a flawless shift, with nothing logged and no way for the caller to
tell. Rejecting the input would have been survivable; silently agreeing with it
was not.

Accepting both scales at the boundary is the pragmatic fix: the model cannot
get it wrong, and neither can a future caller.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# An OEE of 1.5% is not a line anyone is running; 1.5 as a fraction is
# impossible. Anything above this is a percentage that needs scaling, and
# genuine fractions (0.0-1.0) pass through untouched.
_PERCENT_THRESHOLD = 1.5


def as_fraction(oee: float, *, field: str = "current_oee") -> float:
    """Return ``oee`` as a 0-1 fraction, accepting a 0-100 percentage too."""
    value = float(oee)
    if value > _PERCENT_THRESHOLD:
        logger.debug("%s=%.3f read as a percentage; scaling to %.4f", field, value, value / 100.0)
        return value / 100.0
    return value


# Bottles in a physical case (24-pack). The line runs in bottles/min end to end
# and the plan is written in cases, so this is the only constant that converts
# between what the optimizers are given and what they are asked for. It matches
# ``src/streaming_sim/producer.py``'s BOTTLES_PER_CASE, which is what actually
# produces the cases the plan is measured against; a test pins them together.
BOTTLES_PER_CASE = 24


def cases_from_bpm(bpm: float, minutes: float) -> float:
    """Cases produced by running at ``bpm`` bottles/minute for ``minutes``.

    Every optimizer here takes a ``*_speed_bpm`` and reports ``projected_cases``,
    and every one of them used to divide by 60 — the minutes-to-hours conversion
    for a *per-hour* rate, applied to a per-minute one. At the nominal 420 BPM
    over a 480-minute shift that returned 3,360 cases against a plan of 8,400,
    understating the line by a factor of 2.5 (60/24) and never raising.

    The consequence was not a wrong number in a corner of a report. Recovery
    advice is a feasibility test, so a line that could comfortably make its plan
    was told it could not, and the suggested remedy was to raise the line speed
    past its own maximum. Going through one function keeps the five call sites
    from drifting apart again.
    """
    return bpm * minutes / BOTTLES_PER_CASE


def bpm_for_cases(cases: float, minutes: float) -> float:
    """Bottles/minute needed to make ``cases`` in ``minutes``. Inverse of above.

    Returns ``inf`` when there is no time left: no speed makes the plan, and
    saying so is better than a division error or a plausible finite answer.
    """
    if minutes <= 0:
        return float("inf")
    return cases * BOTTLES_PER_CASE / minutes
