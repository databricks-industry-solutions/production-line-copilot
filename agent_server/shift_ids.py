"""The one place that knows shift ids come in two flavours.

The producer writes live rows keyed by the env-partitioned ``SIM_SHIFT_ID`` —
``shift_1_dev`` in dev, ``shift_1`` in prod — so dev traffic never lands in
prod's cursor. The reference tables that frame a shift (``production_schedule``,
``shift_crew``, ``maintenance_schedule``, ``cip_schedule``, ``scenario_index``,
``scenario_moments``) are seeded once with the *base* id and shared by both.

The two sets are therefore disjoint: in dev, ``gold_history_line_kpi`` holds
only ``shift_1_dev`` and ``shift_crew`` holds only ``shift_1``. Query either
with the other's id and you get zero rows and no error, which is the worst
possible failure — a specialist writes a confident answer over an empty result.

``context_builder`` got this right from the start. Nothing else did, which is
why this now lives in a module of its own instead of being a private helper.
"""
from __future__ import annotations

ENV_SHIFT_SUFFIXES = ("_dev", "_prod")


def reference_shift_id(shift_id: str) -> str:
    """Map an env-partitioned live shift id to the base id reference tables use."""
    for suffix in ENV_SHIFT_SUFFIXES:
        if shift_id.endswith(suffix):
            return shift_id[: -len(suffix)]
    return shift_id
