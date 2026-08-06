"""The curated Genie space must default to the shift its own line writes.

The config hardcoded ``shift_1`` as the default ``:shift_id`` in every curated
SQL. dev's producer writes ``shift_1_dev``, so the dev space answered "There are
currently 0 machines on the line" — a query that ran cleanly and returned
nothing, which reads as a dead line rather than a misconfigured space. The
schema was already templated per environment; the shift was not.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "genie_space_config_streaming.py"


def _render(shift: str | None = None) -> dict:
    args = [sys.executable, str(_SCRIPT), "--dry-run",
            "--catalog", "cat", "--schema", "sch", "--warehouse-id", "wh"]
    if shift is not None:
        args += ["--shift-id", shift]
    subprocess.run(args, cwd=_ROOT, check=True, capture_output=True)
    return json.loads(Path("/tmp/genie_curated_streaming.json").read_text())


@pytest.fixture(scope="module")
def dev_space() -> str:
    return _render("shift_1_dev")["serialized_space"]


class TestTheShiftIsTemplated:
    def test_the_source_holds_no_hardcoded_shift_default(self):
        source = _SCRIPT.read_text()
        assert '"default_value": {"values": ["shift_1"]}' not in source

    def test_every_shift_default_is_the_one_asked_for(self, dev_space):
        defaults = re.findall(r'"default_value": \{"values": \["([^"]+)"\]\}', dev_space)
        shifts = [d for d in defaults if d.startswith("shift_")]
        assert shifts, "no :shift_id defaults found — the fixture is not exercising anything"
        assert set(shifts) == {"shift_1_dev"}

    def test_no_placeholder_survives_into_the_payload(self, dev_space):
        for placeholder in ("__SHIFT__", "__CAT__", "__SCH__"):
            assert placeholder not in dev_space

    def test_prod_still_gets_the_unsuffixed_shift(self):
        blob = _render("shift_1")["serialized_space"]
        defaults = re.findall(r'"default_value": \{"values": \["(shift_[^"]+)"\]\}', blob)
        assert set(defaults) == {"shift_1"}

    def test_the_default_stays_prod_when_no_shift_is_given(self):
        # An operator running the script by hand should not silently retarget
        # a space at some other environment's shift.
        blob = _render(None)["serialized_space"]
        defaults = re.findall(r'"default_value": \{"values": \["(shift_[^"]+)"\]\}', blob)
        assert set(defaults) == {"shift_1"}


class TestTheInstructionsAgreeWithTheDefaults:
    def test_the_prose_names_the_same_shift_as_the_parameters(self, dev_space):
        # Genie reads the instruction text as well as the parameter defaults,
        # and a contradiction between them is how it ends up guessing.
        assert "shift_1_dev" in dev_space
        assert "matches the prod producer" not in dev_space

    def test_it_no_longer_advertises_a_live_shift(self, dev_space):
        # The config's own column note says there is no 'live' shift, yet six
        # parameters documented "Default: live."
        assert "Default: live." not in dev_space

    def test_reference_tables_keep_the_unsuffixed_id(self, dev_space):
        # This one is genuinely environment-independent: reference tables are
        # keyed by the base id, which is why joining them to live tables on
        # shift_id always returns empty.
        assert "REFERENCE tables are keyed by the base id" in dev_space
        assert "e.g. 'shift_1'" in dev_space
