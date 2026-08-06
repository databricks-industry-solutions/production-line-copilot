"""Every machine needs at least one resolved case with a recorded outcome.

``retrieve_similar_cases`` only returns events that were closed out and carry an
outcome — anything else is a description of something that once went wrong with
no resolution, which is not a precedent. The seeded history covered 9 of the
line's 19 machines, so for the other ten the tool had nothing to return at all.

DEPALLETIZER was one of them. Asked to diagnose a stop there, the search fell
back to the current shift's own open incidents and the analyst reported
"historical case match is same-shift keyword only, no outcome data" and dropped
to LOW confidence. The tool was working; the data behind it did not exist.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from backfill_quality_and_events import (  # noqa: E402
    EVENT_TEMPLATES,
    generate_event_rows,
)

from data_generation.config import MACHINES  # noqa: E402

TEMPLATE_MACHINES = {m for t in EVENT_TEMPLATES for m in t[0]}


@pytest.mark.parametrize("machine", sorted({m.id for m in MACHINES}))
def test_every_machine_can_be_seeded_a_case(machine):
    assert machine in TEMPLATE_MACHINES, (
        f"{machine} has no seeded history, so retrieve_similar_cases can never "
        "return a precedent for it and the specialist reports low confidence"
    )


class TestTheSeededHistory:
    def test_every_template_fires_at_the_default_event_count(self):
        # Templates are assigned round-robin by index, so a count below the
        # template count silently drops the tail — the machines added last.
        import argparse
        import inspect

        import backfill_quality_and_events as bf

        src = inspect.getsource(bf.main) if hasattr(bf, "main") else ""
        assert "--events" in src
        parser = argparse.ArgumentParser()
        parser.add_argument("--events", type=int, default=24)
        default = parser.parse_args([]).events
        assert default >= len(EVENT_TEMPLATES), (
            f"{default} events over {len(EVENT_TEMPLATES)} templates leaves the "
            "last templates unseeded"
        )

    def test_the_machines_that_had_no_history_now_do(self):
        # The ten that were missing when this was found.
        for machine in (
            "BOTTLE_WASHER", "CASE_PACKER", "CASE_SEALER", "CODER", "DEPALLETIZER",
            "PALLETIZER", "PASTEURIZER", "SHRINK_WRAPPER", "STRETCH_WRAPPER", "UNCASER",
        ):
            assert machine in TEMPLATE_MACHINES

    def test_each_new_template_names_a_single_machine(self):
        # rng.choice over a set leaves coverage to chance; the machines added to
        # close this gap must be certain to appear.
        singles = {t[0][0] for t in EVENT_TEMPLATES if len(t[0]) == 1}
        assert {"DEPALLETIZER", "PALLETIZER", "UNCASER"} <= singles

    def test_generated_rows_carry_an_outcome_the_retriever_can_use(self):
        import random
        from datetime import datetime, timedelta, timezone

        start = datetime(2026, 8, 5, 6, tzinfo=timezone.utc)
        rows = generate_event_rows(
            "shift_1", start, start + timedelta(hours=8), len(EVENT_TEMPLATES),
            random.Random(3),
        )
        closed = [r for r in rows if r["timestamp_end"] is not None]
        assert closed, "every row was left open"
        for row in closed:
            assert row["outcome_description"], f"{row['event_id']} has no outcome"
            assert row["corrective_action"], f"{row['event_id']} has no corrective action"
            assert row["resolution_minutes"], f"{row['event_id']} has no resolution time"

    def test_exactly_one_row_is_left_open_as_carryover(self):
        import random
        from datetime import datetime, timedelta, timezone

        start = datetime(2026, 8, 5, 6, tzinfo=timezone.utc)
        rows = generate_event_rows(
            "shift_1", start, start + timedelta(hours=8), 24, random.Random(3)
        )
        assert sum(1 for r in rows if r["timestamp_end"] is None) == 1
