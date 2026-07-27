"""Generate OEE gold table (7 rows, one per shift)."""

from __future__ import annotations

import random
from typing import Any

import pandas as pd

from .config import SCENARIOS, SKUS

# OEE specs per shift (from design doc §5.1)
_SHIFT_OEE_SPECS: dict[str, dict[str, Any]] = {
    "shift_1": {  # S1 — Baseline Smooth: OEE ~85%
        "sku_id": "LAGER_330_GLASS_24",
        "availability": 98.0,
        "performance": 92.0,
        "quality": 94.0,
        "downtime_minutes": 10.0,
        "planned_cases": 8400,
        "changeover_minutes": 0.0,
        "speed_loss_minutes": 25.0,
    },
    "shift_2": {  # S2 — Downtime Triage: OEE ~72%
        "sku_id": "LAGER_330_GLASS_24",
        "availability": 85.0,
        "performance": 88.0,
        "quality": 96.0,
        "downtime_minutes": 35.0,
        "planned_cases": 8400,
        "changeover_minutes": 0.0,
        "speed_loss_minutes": 35.0,
    },
    "shift_3": {  # S3 — Normal Operation: OEE ~82%
        "sku_id": "PILSNER_500_GLASS_12",
        "availability": 97.0,
        "performance": 90.0,
        "quality": 94.0,
        "downtime_minutes": 15.0,
        "planned_cases": 8400,
        "changeover_minutes": 0.0,
        "speed_loss_minutes": 30.0,
    },
    "shift_4": {  # S4 — Schedule Recovery: OEE ~63%
        "sku_id": "WHEAT_330_GLASS_24",
        "availability": 80.0,
        "performance": 85.0,
        "quality": 93.0,
        "downtime_minutes": 75.0,
        "planned_cases": 7600,
        "changeover_minutes": 45.0,
        "speed_loss_minutes": 40.0,
    },
    "shift_5": {  # S5 — Material Shortage (MVP): OEE ~54%
        "sku_id": "WHEAT_330_GLASS_24",
        "availability": 72.0,
        "performance": 82.0,
        "quality": 92.0,
        "downtime_minutes": 135.0,
        "planned_cases": 8000,
        "changeover_minutes": 0.0,
        "speed_loss_minutes": 55.0,
    },
    "shift_6": {  # S6 — Quality Deviation: OEE ~68%
        "sku_id": "LAGER_330_GLASS_24",
        "availability": 85.0,
        "performance": 88.0,
        "quality": 91.0,
        "downtime_minutes": 45.0,
        "planned_cases": 8000,
        "changeover_minutes": 0.0,
        "speed_loss_minutes": 35.0,
    },
    "shift_7": {  # S7 — Upstream Delay: OEE ~65%
        "sku_id": "IPA_330_GLASS_24",
        "availability": 78.0,
        "performance": 87.0,
        "quality": 96.0,
        "downtime_minutes": 55.0,
        "planned_cases": 8200,
        "changeover_minutes": 0.0,
        "speed_loss_minutes": 40.0,
    },
}


def _validate_sku(sku_id: str) -> None:
    """Ensure sku_id exists in config."""
    valid = {s.sku_id for s in SKUS}
    if sku_id not in valid:
        raise ValueError(f"Unknown sku_id: {sku_id}; valid: {valid}")


def generate_oee_gold() -> pd.DataFrame:
    """Generate OEE gold table (7 rows, one per shift)."""
    # Deterministic seed for reproducible ±2% noise
    rng = random.Random(42)

    rows: list[dict[str, Any]] = []

    for scenario in SCENARIOS:
        shift_id = scenario.shift_id
        spec = _SHIFT_OEE_SPECS[shift_id]
        _validate_sku(spec["sku_id"])
        avail = spec["availability"]
        perf = spec["performance"]
        qual = spec["quality"]
        oee = avail * perf * qual / 10000.0

        # total_cases ≈ planned_cases * oee / 100 with ±2% rounding noise
        base_cases = spec["planned_cases"] * oee / 100.0
        noise = rng.uniform(-0.02, 0.02)
        total_cases = int(round(base_cases * (1 + noise)))

        # reject_count: quality = 100 * good / (good + reject) => reject = good * (100 - quality) / quality
        good_cases = total_cases
        reject_count = int(round(good_cases * (100 - qual) / qual)) if qual < 100 else 0

        rows.append({
            "shift_id": shift_id,
            "sku_id": spec["sku_id"],
            "availability": avail,
            "performance": perf,
            "quality": qual,
            "oee": round(oee, 2),
            "total_cases": total_cases,
            "planned_cases": spec["planned_cases"],
            "downtime_minutes": spec["downtime_minutes"],
            "reject_count": reject_count,
            "speed_loss_minutes": spec["speed_loss_minutes"],
            "changeover_minutes": spec["changeover_minutes"],
        })

    df = pd.DataFrame(rows)
    # Ensure column order matches output schema
    return df[[
        "shift_id", "sku_id", "availability", "performance", "quality", "oee",
        "total_cases", "planned_cases", "downtime_minutes", "reject_count",
        "speed_loss_minutes", "changeover_minutes",
    ]]


if __name__ == "__main__":
    df = generate_oee_gold()
    print("OEE Gold Table Summary")
    print("=" * 80)
    print(df.to_string(index=False))
    print()
    print("OEE by shift:")
    for _, row in df.iterrows():
        print(f"  {row['shift_id']}: OEE={row['oee']:.1f}% | "
              f"Avail={row['availability']:.0f}% Perf={row['performance']:.0f}% Qual={row['quality']:.0f}% | "
              f"Cases {row['total_cases']}/{row['planned_cases']}")
