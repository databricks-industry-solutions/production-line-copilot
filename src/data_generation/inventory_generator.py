"""Generate inventory_status table (~105 rows = 7 shifts × 15 materials).

Tracks on-hand quantities, safety stock, reorder point, and status (OK/LOW/CRITICAL).
Scenario-specific: S5 crowns deplete to CRITICAL (supply chain failure).
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from .config import SCENARIOS

# Key materials to track (subset of config.MATERIALS)
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

# Default inventory levels (OK) - comfortably above reorder_point
DEFAULT_ON_HAND: dict[str, int] = {
    "CROWN_STD_26": 25000,
    "CROWN_WHEAT_26": 24000,
    "LABEL_PAPER_FB_LAGER": 15000,
    "CARTON_24PACK": 5000,
    "FILM_SHRINK_500": 3000,
    "INK_THERMAL_BLK": 500,
    "PALLET_EUR_1200": 200,
    "STRETCH_FILM_23": 2000,
    "CAUSTIC_SODA": 100,
    "CO2_FILLER": 500,
    "WATER_FILLER": 2000,
    "PRODUCT_BEER_LAGER": 500,
    "GLUE_HOT_MELT": 200,
    "CIP_CHEMICAL": 50,
    "RINSE_WATER": 1000,
    "TAPE_CARTON": 500,
}

# Crown-specific: safety_stock 5000, reorder_point 8000 (from LINE_CONSTRAINTS C007, C008)
CROWN_SAFETY_STOCK = 5000
CROWN_REORDER_POINT = 8000

# Generic safety/reorder for other materials (simplified)
MATERIAL_SAFETY: dict[str, tuple[int, int]] = {
    "CROWN_STD_26": (5000, 8000),
    "CROWN_WHEAT_26": (5000, 8000),
    "LABEL_PAPER_FB_LAGER": (2000, 5000),
    "CARTON_24PACK": (2000, 4000),
    "FILM_SHRINK_500": (500, 1500),
    "INK_THERMAL_BLK": (50, 200),
    "PALLET_EUR_1200": (20, 80),
    "STRETCH_FILM_23": (300, 800),
    "CAUSTIC_SODA": (20, 50),
    "CO2_FILLER": (100, 250),
    "WATER_FILLER": (500, 1000),
    "PRODUCT_BEER_LAGER": (100, 300),
    "GLUE_HOT_MELT": (50, 150),
    "CIP_CHEMICAL": (10, 30),
    "RINSE_WATER": (200, 500),
    "TAPE_CARTON": (100, 300),
}


def _status(on_hand: int, safety_stock: int, reorder_point: int) -> str:
    """OK if on_hand > reorder_point, LOW if between safety and reorder, CRITICAL if below safety."""
    if on_hand > reorder_point:
        return "OK"
    if on_hand > safety_stock:
        return "LOW"
    return "CRITICAL"


def generate_inventory_status() -> pd.DataFrame:
    """Generate inventory_status table (~105 rows = 7 shifts × 15 materials)."""
    rows: list[dict[str, Any]] = []

    for scenario in SCENARIOS:
        shift_id = scenario.shift_id
        timestamp = SHIFT_START_ISO[shift_id]

        for material_id in MATERIALS_TRACKED:
            safety_stock, reorder_point = MATERIAL_SAFETY[material_id]

            # S5: CROWN_WHEAT_26 supply chain failure — only 6000 remain (below reorder 8000)
            # Consumption during shift_5 ~7650 caps (5h run × 1530/h) so stock will deplete
            if material_id == "CROWN_WHEAT_26" and shift_id == "shift_5":
                on_hand = 6000
            else:
                on_hand = DEFAULT_ON_HAND[material_id]

            status = _status(on_hand, safety_stock, reorder_point)

            rows.append({
                "shift_id": shift_id,
                "material_id": material_id,
                "timestamp": timestamp,
                "on_hand_qty": on_hand,
                "safety_stock": safety_stock,
                "reorder_point": reorder_point,
                "status": status,
            })

    return pd.DataFrame(rows)


if __name__ == "__main__":
    df = generate_inventory_status()
    print(f"inventory_status: {len(df)} rows")
    print(f"  OK: {(df['status'] == 'OK').sum()}")
    print(f"  LOW: {(df['status'] == 'LOW').sum()}")
    print(f"  CRITICAL: {(df['status'] == 'CRITICAL').sum()}")
    crowns = df[df["material_id"] == "CROWN_STD_26"]
    print("\nCROWN_STD_26 by shift:")
    print(crowns[["shift_id", "on_hand_qty", "status"]].to_string())
