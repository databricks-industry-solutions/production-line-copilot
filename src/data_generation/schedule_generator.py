"""Generate production_schedule table (~14 rows, 1-2 per shift)."""

from __future__ import annotations

import pandas as pd

from .config import SCENARIOS, SKUS


def _sku_by_id(sku_id: str) -> tuple[str, float]:
    """Return (sku_name, ideal_rate_bpm) for a given sku_id."""
    for s in SKUS:
        if s.sku_id == sku_id:
            return s.sku_name, s.ideal_rate_bpm
    raise KeyError(f"Unknown sku_id: {sku_id}")


def _shift_date(shift_id: str) -> str:
    """Return ISO date for shift start based on shift_id."""
    dates = {
        "shift_1": "2025-01-01",
        "shift_2": "2025-01-01",
        "shift_3": "2025-01-01",
        "shift_4": "2025-01-02",
        "shift_5": "2025-01-02",
        "shift_6": "2025-01-02",
        "shift_7": "2025-01-03",
    }
    return dates[shift_id]


def _shift_times(shift_id: str, segment_start: str | None = None, segment_end: str | None = None) -> tuple[str, str]:
    """Return (start_time, end_time) as ISO timestamps for a shift or segment."""
    base_dates = {
        "shift_1": ("2025-01-01", "2025-01-01"),
        "shift_2": ("2025-01-01", "2025-01-01"),
        "shift_3": ("2025-01-01", "2025-01-02"),
        "shift_4": ("2025-01-02", "2025-01-02"),
        "shift_5": ("2025-01-02", "2025-01-02"),
        "shift_6": ("2025-01-02", "2025-01-03"),
        "shift_7": ("2025-01-03", "2025-01-03"),
    }
    start_date, end_date = base_dates[shift_id]
    scenario = next(s for s in SCENARIOS if s.shift_id == shift_id)
    if segment_start is not None and segment_end is not None:
        return f"{start_date}T{segment_start}", f"{start_date}T{segment_end}"
    return f"{start_date}T{scenario.shift_start}", f"{end_date}T{scenario.shift_end}"


def _batch_id(sku: str, date: str, shift_num: int, seq: int = 1) -> str:
    """Generate batch ID like LAGER-20250101-S1-001."""
    prefix = sku.split("_")[0]
    date_compact = date.replace("-", "")
    return f"{prefix}-{date_compact}-S{shift_num}-{seq:03d}"


def generate_production_schedule() -> pd.DataFrame:
    """Generate production_schedule table (~14 rows, 1-2 per shift)."""
    rows: list[dict[str, object]] = []

    # shift_1: Baseline Smooth, 08:00-16:00 (OEE ~85%)
    start, end = _shift_times("shift_1")
    sku_name, ideal_rate = _sku_by_id("LAGER_330_GLASS_24")
    rows.append({
        "shift_id": "shift_1",
        "date": _shift_date("shift_1"),
        "shift_number": 1,
        "start_time": start,
        "end_time": end,
        "sku": "LAGER_330_GLASS_24",
        "sku_name": sku_name,
        "planned_cases": 8400,
        "actual_cases": 7140,
        "ideal_rate_bpm": ideal_rate,
        "status": "completed",
        "batch_id": _batch_id("LAGER_330_GLASS_24", "2025-01-01", 1),
    })

    # shift_2: Downtime Triage, 14:00-22:00 (OEE ~72%)
    start, end = _shift_times("shift_2")
    sku_name, ideal_rate = _sku_by_id("LAGER_330_GLASS_24")
    rows.append({
        "shift_id": "shift_2",
        "date": _shift_date("shift_2"),
        "shift_number": 2,
        "start_time": start,
        "end_time": end,
        "sku": "LAGER_330_GLASS_24",
        "sku_name": sku_name,
        "planned_cases": 8400,
        "actual_cases": 6030,
        "ideal_rate_bpm": ideal_rate,
        "status": "completed",
        "batch_id": _batch_id("LAGER_330_GLASS_24", "2025-01-01", 2),
    })

    # shift_3: Normal Operation, 22:00-06:00 (OEE ~82%)
    start, end = _shift_times("shift_3")
    sku_name, ideal_rate = _sku_by_id("PILSNER_500_GLASS_12")
    rows.append({
        "shift_id": "shift_3",
        "date": _shift_date("shift_3"),
        "shift_number": 3,
        "start_time": start,
        "end_time": end,
        "sku": "PILSNER_500_GLASS_12",
        "sku_name": sku_name,
        "planned_cases": 8400,
        "actual_cases": 6890,
        "ideal_rate_bpm": ideal_rate,
        "status": "completed",
        "batch_id": _batch_id("PILSNER_500_GLASS_12", "2025-01-01", 3),
    })

    # shift_4: Schedule Recovery — Segment 1 (changeover 06:00-06:45)
    start, end = _shift_times("shift_4", "06:00", "06:45")
    sku_name, ideal_rate = _sku_by_id("PILSNER_500_GLASS_12")
    rows.append({
        "shift_id": "shift_4",
        "date": _shift_date("shift_4"),
        "shift_number": 4,
        "start_time": start,
        "end_time": end,
        "sku": "PILSNER_500_GLASS_12",
        "sku_name": sku_name,
        "planned_cases": 0,
        "actual_cases": 0,
        "ideal_rate_bpm": ideal_rate,
        "status": "changeover",
        "batch_id": _batch_id("PILSNER_500_GLASS_12", "2025-01-02", 4),
    })

    # shift_4: Schedule Recovery — Segment 2 (WHEAT 06:45-14:00, OEE ~63%)
    start, end = _shift_times("shift_4", "06:45", "14:00")
    sku_name, ideal_rate = _sku_by_id("WHEAT_330_GLASS_24")
    rows.append({
        "shift_id": "shift_4",
        "date": _shift_date("shift_4"),
        "shift_number": 4,
        "start_time": start,
        "end_time": end,
        "sku": "WHEAT_330_GLASS_24",
        "sku_name": sku_name,
        "planned_cases": 7600,
        "actual_cases": 4800,
        "ideal_rate_bpm": ideal_rate,
        "status": "disrupted",
        "batch_id": _batch_id("WHEAT_330_GLASS_24", "2025-01-02", 4, 2),
    })

    # shift_5: Material Shortage, 14:00-22:00 (OEE ~54%)
    start, end = _shift_times("shift_5")
    sku_name, ideal_rate = _sku_by_id("WHEAT_330_GLASS_24")
    rows.append({
        "shift_id": "shift_5",
        "date": _shift_date("shift_5"),
        "shift_number": 5,
        "start_time": start,
        "end_time": end,
        "sku": "WHEAT_330_GLASS_24",
        "sku_name": sku_name,
        "planned_cases": 8000,
        "actual_cases": 4350,
        "ideal_rate_bpm": ideal_rate,
        "status": "disrupted",
        "batch_id": _batch_id("WHEAT_330_GLASS_24", "2025-01-02", 5),
    })

    # shift_6: Quality Deviation, 22:00-06:00 (OEE ~68%)
    start, end = _shift_times("shift_6")
    sku_name, ideal_rate = _sku_by_id("LAGER_330_GLASS_24")
    rows.append({
        "shift_id": "shift_6",
        "date": _shift_date("shift_6"),
        "shift_number": 6,
        "start_time": start,
        "end_time": end,
        "sku": "LAGER_330_GLASS_24",
        "sku_name": sku_name,
        "planned_cases": 8000,
        "actual_cases": 5450,
        "ideal_rate_bpm": ideal_rate,
        "status": "disrupted",
        "batch_id": _batch_id("LAGER_330_GLASS_24", "2025-01-02", 6),
    })

    # shift_7: Upstream Delay, 06:00-14:00 (OEE ~65%)
    start, end = _shift_times("shift_7")
    sku_name, ideal_rate = _sku_by_id("IPA_330_GLASS_24")
    rows.append({
        "shift_id": "shift_7",
        "date": _shift_date("shift_7"),
        "shift_number": 7,
        "start_time": start,
        "end_time": end,
        "sku": "IPA_330_GLASS_24",
        "sku_name": sku_name,
        "planned_cases": 8200,
        "actual_cases": 5340,
        "ideal_rate_bpm": ideal_rate,
        "status": "disrupted",
        "batch_id": _batch_id("IPA_330_GLASS_24", "2025-01-03", 7),
    })

    return pd.DataFrame(rows)


if __name__ == "__main__":
    df = generate_production_schedule()
    print(df.to_string())
