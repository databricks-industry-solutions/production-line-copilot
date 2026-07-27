"""Generate consumption_log table (~105 rows = 7 shifts × 15 materials).

Consumption = rate × shift_hours × (1 + waste/100), adjusted for machine stops.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from .config import MATERIALS, SCENARIOS, MaterialDef

# Key materials to track (same 15 as inventory)
MATERIALS_TRACKED: list[str] = [
    "CROWN_STD_26",
    "CROWN_WHEAT_26",
    "LABEL_PAPER_FB_LAGER",
    "CARTON_24PACK",
    "FILM_SHRINK_500",
    "INK_THERMAL_BLK",
    "PALLET_EUR_1200",
    "STRETCH_FILM_23",
    "CAUSTIC_SODA",
    "CO2_FILLER",
    "WATER_FILLER",
    "PRODUCT_BEER_LAGER",
    "GLUE_HOT_MELT",
    "CIP_CHEMICAL",
    "RINSE_WATER",
    "TAPE_CARTON",
]

SHIFT_HOURS = 8.0

# Shift start timestamps (ISO)
SHIFT_START_ISO: dict[str, str] = {
    "shift_1": "2025-01-01T08:00:00",
    "shift_2": "2025-01-01T14:00:00",
    "shift_3": "2025-01-01T22:00:00",
    "shift_4": "2025-01-02T06:00:00",
    "shift_5": "2025-01-02T14:00:00",
    "shift_6": "2025-01-02T22:00:00",
    "shift_7": "2025-01-03T06:00:00",
}

# S2: LABELLER stopped 16:15-16:23 (8 min) -> reduce label consumption by 8/480 = ~1.7%
S2_LABELLER_STOP_MINUTES = 8.0

# S4: Changeover 06:00-06:45 (45 min idle) -> no consumption during that period
S4_IDLE_MINUTES = 45.0

# S5: CAPPER stops at 19:00 -> CROWN_STD_26 consumption only 14:00-19:00 = 5 hours
S5_CAPPER_RUN_HOURS = 5.0


def _material_by_id(material_id: str) -> MaterialDef | None:
    """Get MaterialDef for material_id."""
    for m in MATERIALS:
        if m.material_id == material_id:
            return m
    return None


def _consumption_factor(shift_id: str, material_id: str, machine_id: str) -> float:
    """Return fraction of normal consumption (0-1) based on scenario adjustments."""
    # S2: LABELLER stopped 8 min -> reduce LABELLER material consumption by ~1.7%
    if shift_id == "shift_2" and machine_id == "LABELLER":
        return 1.0 - (S2_LABELLER_STOP_MINUTES / 480.0)  # ~0.983

    # S4: Changeover 06:00-06:45 (45 min idle) -> 45/480 = 9.4% less
    if shift_id == "shift_4":
        return 1.0 - (S4_IDLE_MINUTES / 480.0)  # ~0.906

    # S5: CAPPER stop at 19:00 -> CROWN_WHEAT_26 only 5/8 hours
    if shift_id == "shift_5" and material_id == "CROWN_WHEAT_26" and machine_id == "CAPPER":
        return S5_CAPPER_RUN_HOURS / SHIFT_HOURS  # 0.625

    return 1.0


INVENTORY_ON_HAND: dict[str, dict[str, int]] = {
    "shift_5": {"CROWN_WHEAT_26": 6000},
}


def generate_consumption_log() -> pd.DataFrame:
    """Generate consumption_log table (~105 rows = 7 shifts × 15 materials).
    Consumption is capped at available inventory to prevent impossible depletion."""
    rows: list[dict[str, Any]] = []
    cumulative: dict[str, float] = {}

    for scenario in SCENARIOS:
        shift_id = scenario.shift_id
        timestamp = SHIFT_START_ISO[shift_id]

        for material_id in MATERIALS_TRACKED:
            mat = _material_by_id(material_id)
            if not mat:
                continue

            machine_id = mat.machine_id
            rate = mat.consumption_rate_per_hour
            waste_pct = mat.waste_factor_pct

            factor = _consumption_factor(shift_id, material_id, machine_id)
            effective_hours = SHIFT_HOURS * factor
            quantity_consumed = rate * effective_hours * (1.0 + waste_pct / 100.0)
            quantity_consumed = round(quantity_consumed, 2)

            inv_cap = INVENTORY_ON_HAND.get(shift_id, {}).get(material_id)
            if inv_cap is not None:
                quantity_consumed = min(quantity_consumed, float(inv_cap))

            cumulative[material_id] = cumulative.get(material_id, 0.0) + quantity_consumed

            rows.append({
                "shift_id": shift_id,
                "machine_id": machine_id,
                "material_id": material_id,
                "timestamp": timestamp,
                "quantity_consumed": quantity_consumed,
                "cumulative_consumed": round(cumulative[material_id], 2),
            })

    return pd.DataFrame(rows)


if __name__ == "__main__":
    df = generate_consumption_log()
    print(f"consumption_log: {len(df)} rows")
    crowns = df[df["material_id"] == "CROWN_STD_26"]
    print("\nCROWN_STD_26 consumption by shift:")
    print(crowns[["shift_id", "quantity_consumed", "cumulative_consumed"]].to_string())
    labels = df[df["material_id"] == "LABEL_PAPER_FB_LAGER"]
    print("\nLABEL_PAPER_FB_LAGER (S2 reduced):")
    print(labels[labels["shift_id"] == "shift_2"][["shift_id", "quantity_consumed"]].to_string())
