"""Tests for the shared inventory engine — depletion + auto-replenishment.

These pin the behaviour that fixes the "warehouse shows CRITICAL / all
depleted but the line keeps running" bug: stock is now modelled with a
reorder → lead-time → delivery cycle so it recovers instead of sitting at
zero forever. The standalone Zerobus producer is the sole consumer of this
module, so these tests guard the single source of truth.
"""

from __future__ import annotations

from src.streaming_sim.inventory_engine import (
    REPLENISH_LEAD_MAX_SECONDS,
    REPLENISH_LEAD_MIN_SECONDS,
    InventoryState,
    init_inventory,
    replenishment_lead_seconds,
    restock_for_machines,
    snapshot_inventory,
    tick_inventory,
)

MACHINE = "CAPPER"


def _mk(
    on_hand: float,
    *,
    mid: str = "CROWN_STD_26",
    safety: int = 5000,
    reorder: int = 8000,
    rate: float = 25200.0,
    machine: str = MACHINE,
) -> InventoryState:
    return InventoryState(
        material_id=mid,
        on_hand_qty=on_hand,
        safety_stock=safety,
        reorder_point=reorder,
        consumption_rate_per_hour=rate,
        machine_id=machine,
    )


def _running(machine: str = MACHINE, speed: float = 100.0):
    return {machine: "running"}, {machine: speed}


# ── Depletion basics ───────────────────────────────────────────────────────


def test_running_machine_depletes_stock():
    inv = _mk(252000.0)
    status_for, speed_for = _running()
    tick_inventory(
        {inv.material_id: inv},
        machine_status_for=status_for,
        machine_speed_for=speed_for,
        tick_seconds=3600.0,  # exactly 1 hour → drains one full hourly rate
    )
    assert inv.on_hand_qty == 252000.0 - 25200.0


def test_stopped_machine_does_not_deplete():
    inv = _mk(252000.0)
    tick_inventory(
        {inv.material_id: inv},
        machine_status_for={MACHINE: "stopped"},
        machine_speed_for={MACHINE: 0.0},
        tick_seconds=3600.0,
    )
    assert inv.on_hand_qty == 252000.0


def test_on_hand_floored_at_zero_when_replenish_disabled():
    inv = _mk(100.0)
    status_for, speed_for = _running()
    tick_inventory(
        {inv.material_id: inv},
        machine_status_for=status_for,
        machine_speed_for=speed_for,
        tick_seconds=3600.0,
        replenish=False,
    )
    assert inv.on_hand_qty == 0.0
    assert inv.status == "CRITICAL"


# ── Reorder placement ──────────────────────────────────────────────────────


def test_reorder_placed_when_crossing_reorder_point():
    inv = _mk(8000.0)  # exactly at reorder point → next drain trips it
    status_for, speed_for = _running()
    deliveries = tick_inventory(
        {inv.material_id: inv},
        machine_status_for=status_for,
        machine_speed_for=speed_for,
        tick_seconds=1.0,
    )
    assert deliveries == []
    assert inv.incoming_qty > 0.0
    # Order-up-to the ~10h buffer (max(rate*10, reorder*3)).
    assert inv.incoming_qty == 252000.0 - inv.on_hand_qty
    assert inv.lead_seconds_remaining == replenishment_lead_seconds(inv)


def test_no_double_order_while_one_is_in_transit():
    inv = _mk(8000.0)
    status_for, speed_for = _running()
    d1 = tick_inventory({inv.material_id: inv}, machine_status_for=status_for,
                        machine_speed_for=speed_for, tick_seconds=1.0)
    first_order = inv.incoming_qty
    lead_after_first = inv.lead_seconds_remaining
    d2 = tick_inventory({inv.material_id: inv}, machine_status_for=status_for,
                        machine_speed_for=speed_for, tick_seconds=1.0)
    assert d1 == [] and d2 == []
    # incoming_qty is unchanged (no new order); lead counts down by one tick.
    assert inv.incoming_qty == first_order
    assert inv.lead_seconds_remaining == lead_after_first - 1.0


def test_no_reorder_during_material_shortage():
    inv = _mk(6000.0)  # already below reorder point
    status_for, speed_for = _running()
    tick_inventory(
        {inv.material_id: inv},
        machine_status_for=status_for,
        machine_speed_for=speed_for,
        tick_seconds=1.0,
        shortage_machines={MACHINE},
    )
    assert inv.incoming_qty == 0.0  # no PO placed during the incident
    # 10× accelerated drain during shortage.
    assert inv.on_hand_qty < 6000.0


# ── Delivery mechanics ─────────────────────────────────────────────────────


def test_delivery_lands_after_lead_time_and_refills():
    inv = _mk(4000.0)  # below safety
    inv.incoming_qty = 1000.0
    inv.lead_seconds_remaining = 100.0
    # Stopped machine isolates delivery mechanics from drain.
    deliveries = tick_inventory(
        {inv.material_id: inv},
        machine_status_for={MACHINE: "stopped"},
        machine_speed_for={MACHINE: 0.0},
        tick_seconds=100.0,  # exhausts the lead time exactly
    )
    assert inv.on_hand_qty == 5000.0
    assert inv.incoming_qty == 0.0
    assert inv.lead_seconds_remaining == 0.0
    assert len(deliveries) == 1
    d = deliveries[0]
    assert d["material_id"] == "CROWN_STD_26"
    assert d["feeds_machine_id"] == MACHINE
    assert d["delivered_qty"] == 1000.0
    assert d["on_hand_qty"] == 5000.0


def test_full_reorder_cycle_recovers_to_ok():
    """Drive one material through drain → reorder → delivery → refill and
    assert it recovers (never stuck depleted)."""
    inv = _mk(8500.0)  # just above reorder so the cycle starts quickly
    status_for, speed_for = _running()
    statuses: set[str] = set()
    min_on_hand = inv.on_hand_qty
    total_deliveries = 0
    for _ in range(1000):
        deliveries = tick_inventory(
            {inv.material_id: inv},
            machine_status_for=status_for,
            machine_speed_for=speed_for,
            tick_seconds=60.0,
        )
        total_deliveries += len(deliveries)
        statuses.add(inv.status)
        min_on_hand = min(min_on_hand, inv.on_hand_qty)
        if total_deliveries and inv.status == "OK":
            break
    assert total_deliveries >= 1              # a PO was delivered
    assert inv.status == "OK"                 # recovered after delivery
    assert min_on_hand > 0.0                  # never fully depleted
    assert "LOW" in statuses or "CRITICAL" in statuses  # dipped before refill


# ── Lead-time formula ──────────────────────────────────────────────────────


def test_lead_seconds_uses_reorder_minus_safety_over_rate():
    inv = _mk(8000.0, safety=5000, reorder=8000, rate=25200.0)
    expected = (8000 - 5000) / 25200.0 * 3600.0  # ≈ 428.6s, within band
    assert abs(replenishment_lead_seconds(inv) - expected) < 1e-6
    assert REPLENISH_LEAD_MIN_SECONDS <= replenishment_lead_seconds(inv) <= REPLENISH_LEAD_MAX_SECONDS


def test_lead_seconds_clamped_to_max_for_slow_burn():
    inv = _mk(50.0, safety=10, reorder=30, rate=3.0)  # raw ≈ 24000s
    assert replenishment_lead_seconds(inv) == REPLENISH_LEAD_MAX_SECONDS


def test_lead_seconds_clamped_to_min_for_fast_burn():
    inv = _mk(100.0, safety=99, reorder=100, rate=100000.0)  # raw ≈ 0.036s
    assert replenishment_lead_seconds(inv) == REPLENISH_LEAD_MIN_SECONDS


def test_lead_seconds_zero_rate_returns_max():
    inv = _mk(100.0, safety=10, reorder=30, rate=0.0)
    assert replenishment_lead_seconds(inv) == REPLENISH_LEAD_MAX_SECONDS


# ── restock_for_machines (incident-resolve path) ───────────────────────────


def test_restock_refills_and_clears_pending_order():
    inv = _mk(2000.0)
    inv.incoming_qty = 999.0
    inv.lead_seconds_remaining = 500.0
    by_machine = {MACHINE: [inv.material_id]}
    restock_for_machines({inv.material_id: inv}, by_machine, [MACHINE])
    assert inv.on_hand_qty == 252000.0
    assert inv.incoming_qty == 0.0
    assert inv.lead_seconds_remaining == 0.0
    assert inv.status == "OK"


# ── init + snapshot ────────────────────────────────────────────────────────


def test_init_inventory_starts_full_and_ok():
    inventory, by_machine = init_inventory()
    assert inventory  # non-empty
    assert by_machine
    for inv in inventory.values():
        assert inv.status == "OK"
        assert inv.incoming_qty == 0.0
        assert inv.on_hand_qty > inv.reorder_point


def test_snapshot_schema_excludes_runtime_replenishment_fields():
    """The emitted snapshot must NOT gain new keys — Lakebase/Zerobus schemas
    depend on this exact shape."""
    inv = _mk(8000.0)
    inv.incoming_qty = 1234.0  # runtime-only, must not leak into snapshot
    inv.lead_seconds_remaining = 42.0
    rows = snapshot_inventory({inv.material_id: inv}, "shift_1")
    assert len(rows) == 1
    assert set(rows[0].keys()) == {
        "shift_id", "material_id", "on_hand_qty", "safety_stock",
        "reorder_point", "status", "consumption_rate_per_hour", "feeds_machine_id",
    }
