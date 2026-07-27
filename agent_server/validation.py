"""Pure validation logic for context builder. No external dependencies."""
from __future__ import annotations

import re

_SAFE_ID = re.compile(r"^[a-zA-Z0-9_-]+$")
_SAFE_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z?$")


def validate_shift_id(shift_id: str) -> str:
    """Validate shift_id format. Raises ValueError if invalid."""
    if not _SAFE_ID.match(shift_id):
        raise ValueError(f"Invalid shift_id: {shift_id!r}")
    return shift_id


def validate_machine_id(machine_id: str | None) -> str | None:
    """Validate optional machine_id. Returns None if empty, raises ValueError if unsafe."""
    if not machine_id:
        return None
    if not _SAFE_ID.match(machine_id):
        raise ValueError(f"Invalid machine_id: {machine_id!r}")
    return machine_id


def validate_timestamp(ts: str) -> str:
    """Validate and normalize timestamp to plain ISO (no Z, no milliseconds)."""
    if not _SAFE_TS.match(ts):
        raise ValueError(f"Invalid timestamp: {ts!r}")
    # Strip trailing Z and milliseconds to match data format
    cleaned = ts.rstrip("Z")
    if "." in cleaned:
        cleaned = cleaned.split(".")[0]
    return cleaned
