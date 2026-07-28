"""Pure helpers for the /sim/recover_machine endpoint.

Kept dependency-free so it can be unit-tested without importing the FastAPI
app, Lakebase, or any DBSQL client.
"""
from __future__ import annotations

from typing import Any, Iterable


def _tokens(target: Any) -> set[str]:
    """Split a (possibly comma-joined) target_machine_id into a token set.

    The engine joins multiple targets with ", " when an incident hits more
    than one machine (cascade). Producer rows store a single id. We accept
    either form. Empty/None returns an empty set, never raises.
    """
    if not target:
        return set()
    s = str(target)
    return {t.strip() for t in s.split(",") if t.strip()}


def commands_targeting_machine(
    commands: Iterable[dict[str, Any]],
    machine_id: str,
) -> list[dict[str, Any]]:
    """Filter ``commands`` to those whose target_machine_id includes ``machine_id``.

    Match is **exact** on a token, not a substring — so requesting ``FILLER``
    does not accidentally match ``FILLER_INFEED``. Order is preserved.
    """
    target = (machine_id or "").strip()
    if not target:
        return []
    return [c for c in commands if target in _tokens(c.get("target_machine_id"))]
