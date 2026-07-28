"""Shared inventory tick logic.

Used by the standalone ``src/streaming_sim/producer.py`` (Zerobus producer job)
— the sole writer of live inventory state. Kept as a standalone pure-Python
module (no Spark, no I/O) so it's unit-testable in isolation. Inventory state is
depleted per machine status each tick, auto-reordered when it hits the reorder
point, and snapshotted to dicts ready for upsert / Zerobus emission.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from src.data_generation.config import MATERIALS, MaterialDef
from src.data_generation.inventory_generator import (
    MATERIAL_SAFETY,
    MATERIALS_TRACKED,
)

# ── Auto-replenishment (reorder → lead time → delivery) ────────────────────
# Without this, warehouse stock is a one-way drain: it starts at a ~10h buffer,
# depletes as machines run, and — because nothing refills it and the line never
# checks stock — the dashboard eventually sits at CRITICAL/"Depleted" forever
# while the line keeps running. That's the confusing state operators reported.
#
# Real plants don't run a raw-material warehouse to zero and stop; a reorder
# point triggers a purchase order that arrives after a lead time and refills
# stock. We model exactly that here, so the panel shows a believable
# drain → reorder → delivery → refill sawtooth instead of a dead "Depleted".
#
# Lead time is derived per material from the classic reorder relationship
#   reorder_point ≈ safety_stock + consumption_rate × lead_time
# so a delivery lands roughly when stock reaches safety_stock — i.e. it dips
# into LOW (yellow) but essentially never gets stuck at CRITICAL. Clamped to a
# sane band so tiny/huge buffers still behave.
REPLENISH_LEAD_MIN_SECONDS = 120.0
REPLENISH_LEAD_MAX_SECONDS = 1800.0


@dataclass
class InventoryState:
    material_id: str
    on_hand_qty: float
    safety_stock: int
    reorder_point: int
    consumption_rate_per_hour: float
    machine_id: str
    status: str = "OK"
    # Runtime-only replenishment state (NOT part of the emitted snapshot schema).
    # ``incoming_qty`` > 0 means a purchase order is in transit; it is added to
    # ``on_hand_qty`` once ``lead_seconds_remaining`` counts down to <= 0.
    incoming_qty: float = 0.0
    lead_seconds_remaining: float = 0.0


def _full_stock_level(rate_per_hour: float, reorder_point: float) -> float:
    """Order-up-to / full-buffer level: ~10h of demand (or 3× reorder)."""
    return max(rate_per_hour * 10.0, reorder_point * 3.0)


def replenishment_lead_seconds(inv: InventoryState) -> float:
    """Per-material delivery lead time, from ``(reorder - safety) / rate``.

    Sized so a delivery lands ~when stock reaches ``safety_stock`` (a LOW dip
    that refills), clamped to [REPLENISH_LEAD_MIN_SECONDS, REPLENISH_LEAD_MAX_SECONDS].
    """
    if inv.consumption_rate_per_hour <= 0:
        return REPLENISH_LEAD_MAX_SECONDS
    span = max(0.0, float(inv.reorder_point) - float(inv.safety_stock))
    raw = span / inv.consumption_rate_per_hour * 3600.0
    return max(REPLENISH_LEAD_MIN_SECONDS, min(REPLENISH_LEAD_MAX_SECONDS, raw))


def _recompute_status(inv: InventoryState) -> None:
    if inv.on_hand_qty > inv.reorder_point:
        inv.status = "OK"
    elif inv.on_hand_qty > inv.safety_stock:
        inv.status = "LOW"
    else:
        inv.status = "CRITICAL"


def init_inventory() -> tuple[dict[str, InventoryState], dict[str, list[str]]]:
    """Build initial inventory state at full stock. Returns (inventory, machine→materials)."""
    mat_lookup: dict[str, MaterialDef] = {m.material_id: m for m in MATERIALS}
    inventory: dict[str, InventoryState] = {}
    by_machine: dict[str, list[str]] = {}
    for mid in MATERIALS_TRACKED:
        mdef = mat_lookup.get(mid)
        if not mdef:
            continue
        safety, reorder = MATERIAL_SAFETY.get(mid, (0, 0))
        # why: seed ~10h buffer so minor incidents do not immediately starve stock.
        on_hand = _full_stock_level(mdef.consumption_rate_per_hour, reorder)
        inventory[mid] = InventoryState(
            material_id=mid,
            on_hand_qty=on_hand,
            safety_stock=safety,
            reorder_point=reorder,
            consumption_rate_per_hour=mdef.consumption_rate_per_hour,
            machine_id=mdef.machine_id,
        )
        by_machine.setdefault(mdef.machine_id, []).append(mid)
    return inventory, by_machine


def _drain_factor(machine_status: str | None, in_shortage: bool, speed_pct: float) -> float:
    if in_shortage:
        return 10.0
    if machine_status == "stopped":
        return 0.0
    if machine_status in ("degraded", "starved", "blocked"):
        return 0.3
    return max(0.0, min(2.0, speed_pct / 100.0))


def tick_inventory(
    inventory: dict[str, InventoryState],
    *,
    machine_status_for: dict[str, str | None],
    machine_speed_for: dict[str, float],
    tick_seconds: float,
    shortage_machines: Iterable[str] = (),
    replenish: bool = True,
) -> list[dict[str, Any]]:
    """Mutate ``inventory`` in place: deplete by per-machine status & speed, and
    (when ``replenish``) run the reorder → lead-time → delivery cycle.

    Returns the list of deliveries that landed this tick, one dict per material:
    ``{"material_id", "feeds_machine_id", "delivered_qty", "on_hand_qty"}``.
    Callers may surface these (e.g. as feed events) or ignore them.
    """
    tick_hours = tick_seconds / 3600.0
    shortage = set(shortage_machines)
    deliveries: list[dict[str, Any]] = []
    for inv in inventory.values():
        status = machine_status_for.get(inv.machine_id)
        speed = float(machine_speed_for.get(inv.machine_id, 100.0))
        in_shortage = inv.machine_id in shortage
        rate = _drain_factor(status, in_shortage, speed)
        consumed = inv.consumption_rate_per_hour * tick_hours * rate
        inv.on_hand_qty = max(0.0, inv.on_hand_qty - consumed)

        if replenish:
            if inv.incoming_qty > 0.0:
                # A purchase order is in transit — count down its lead time.
                inv.lead_seconds_remaining -= tick_seconds
                if inv.lead_seconds_remaining <= 0.0:
                    delivered = inv.incoming_qty
                    inv.on_hand_qty += delivered
                    inv.incoming_qty = 0.0
                    inv.lead_seconds_remaining = 0.0
                    deliveries.append({
                        "material_id": inv.material_id,
                        "feeds_machine_id": inv.machine_id,
                        "delivered_qty": round(delivered, 1),
                        "on_hand_qty": round(inv.on_hand_qty, 1),
                    })
            elif not in_shortage and inv.on_hand_qty <= inv.reorder_point:
                # Hit the reorder point with no order outstanding → place one.
                # Don't auto-order during an active material_shortage incident:
                # that's the "real" event the agent reasons about, and it is
                # cleared explicitly via restock_for_machines on resolve.
                target = _full_stock_level(inv.consumption_rate_per_hour, inv.reorder_point)
                order_qty = target - inv.on_hand_qty
                if order_qty > 0.0:
                    inv.incoming_qty = order_qty
                    inv.lead_seconds_remaining = replenishment_lead_seconds(inv)

        _recompute_status(inv)
    return deliveries


def restock_for_machines(
    inventory: dict[str, InventoryState],
    by_machine: dict[str, list[str]],
    machine_ids: Iterable[str],
) -> None:
    """Refill all materials feeding the given machines (used after material_shortage resolves)."""
    for mid in machine_ids:
        for mat_id in by_machine.get(mid, []):
            inv = inventory.get(mat_id)
            if inv:
                inv.on_hand_qty = _full_stock_level(
                    inv.consumption_rate_per_hour, inv.reorder_point
                )
                # Clear any in-transit order so we don't double-refill later.
                inv.incoming_qty = 0.0
                inv.lead_seconds_remaining = 0.0
                _recompute_status(inv)


def snapshot_inventory(
    inventory: dict[str, InventoryState], shift_id: str
) -> list[dict[str, Any]]:
    """Convert to list of dicts ready for upsert / Zerobus emission."""
    out: list[dict[str, Any]] = []
    for inv in inventory.values():
        out.append({
            "shift_id": shift_id,
            "material_id": inv.material_id,
            "on_hand_qty": round(inv.on_hand_qty, 1),
            "safety_stock": int(inv.safety_stock),
            "reorder_point": int(inv.reorder_point),
            "status": inv.status,
            "consumption_rate_per_hour": round(inv.consumption_rate_per_hour, 2),
            "feeds_machine_id": inv.machine_id,
        })
    return out
