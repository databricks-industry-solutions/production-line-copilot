import logging
import os

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

OEE_VALUE_EUR_PER_PCT = float(os.environ.get("OEE_VALUE_EUR_PER_PCT", "5000"))


class OEELossPrioritizer:
    """Ranks OEE losses by contribution and estimates ROI per improvement area."""

    def prioritize(self, shifts: list[dict]) -> dict:
        """Input: list of {shift_id, availability, performance, quality, oee,
                          downtime_minutes, speed_loss_minutes, reject_count}
        All values as percentages (0-100) or counts."""
        if not shifts:
            return {"total_oee_gap": 0, "pareto": []}

        n = len(shifts)
        total_gap = sum(100 - s.get("oee", 100) for s in shifts)
        availability_loss = sum(100 - s.get("availability", 100) for s in shifts)
        performance_loss = sum(100 - s.get("performance", 100) for s in shifts)
        quality_loss = sum(100 - s.get("quality", 100) for s in shifts)

        categories = [
            {
                "category": "Availability",
                "total_loss_pct": round(availability_loss / n, 1),
                "contribution_pct": round(availability_loss / total_gap * 100, 1) if total_gap else 0,
                "top_drivers": self._availability_drivers(shifts),
            },
            {
                "category": "Performance",
                "total_loss_pct": round(performance_loss / n, 1),
                "contribution_pct": round(performance_loss / total_gap * 100, 1) if total_gap else 0,
                "top_drivers": self._performance_drivers(shifts),
            },
            {
                "category": "Quality",
                "total_loss_pct": round(quality_loss / n, 1),
                "contribution_pct": round(quality_loss / total_gap * 100, 1) if total_gap else 0,
                "top_drivers": self._quality_drivers(shifts),
            },
        ]
        categories.sort(key=lambda c: c["contribution_pct"], reverse=True)

        cumulative = 0.0
        for cat in categories:
            cumulative += cat["contribution_pct"]
            cat["cumulative_pct"] = round(cumulative, 1)
            cat["roi_estimate"] = self._estimate_roi(cat)

        return {"total_oee_gap": round(total_gap / n, 1), "pareto": categories}

    def _availability_drivers(self, shifts: list[dict]) -> list[str]:
        total_dt = sum(s.get("downtime_minutes", 0) for s in shifts)
        drivers = []
        if total_dt > 60:
            drivers.append(f"Total unplanned downtime: {total_dt:.0f} min across {len(shifts)} shifts")
        low_avail = [s for s in shifts if s.get("availability", 100) < 80]
        if low_avail:
            ids = ", ".join(str(s.get("shift_id", "?")) for s in low_avail)
            drivers.append(f"Shifts with <80% availability: {ids}")
        if not drivers:
            drivers.append("Availability is within target range")
        return drivers

    def _performance_drivers(self, shifts: list[dict]) -> list[str]:
        total_speed_loss = sum(s.get("speed_loss_minutes", 0) for s in shifts)
        drivers = []
        if total_speed_loss > 30:
            drivers.append(f"Speed loss: {total_speed_loss:.0f} min equivalent across shifts")
        low_perf = [s for s in shifts if s.get("performance", 100) < 85]
        if low_perf:
            ids = ", ".join(str(s.get("shift_id", "?")) for s in low_perf)
            drivers.append(f"Shifts with <85% performance: {ids}")
        if not drivers:
            drivers.append("Performance is within target range")
        return drivers

    def _quality_drivers(self, shifts: list[dict]) -> list[str]:
        total_rejects = sum(s.get("reject_count", 0) for s in shifts)
        drivers = []
        if total_rejects > 50:
            drivers.append(f"Total rejects: {total_rejects} across shifts")
        low_quality = [s for s in shifts if s.get("quality", 100) < 95]
        if low_quality:
            ids = ", ".join(str(s.get("shift_id", "?")) for s in low_quality)
            drivers.append(f"Shifts with <95% quality: {ids}")
        if not drivers:
            drivers.append("Quality is within target range")
        return drivers

    def _estimate_roi(self, category: dict) -> dict:
        loss = category["total_loss_pct"]
        improvement_potential = loss * 0.5
        annual_value = improvement_potential * OEE_VALUE_EUR_PER_PCT
        return {
            "improvement_potential_pct": round(improvement_potential, 1),
            "estimated_annual_value_eur": round(annual_value, 0),
            "oee_value_per_pct_eur": OEE_VALUE_EUR_PER_PCT,
            "difficulty": "medium" if loss < 10 else "high",
        }


_prioritizer = OEELossPrioritizer()


@tool
def oee_loss_analysis(shifts_data: list[dict]) -> dict:
    """Analyze OEE losses across shifts using Pareto analysis.
    Input: list of shift dicts with keys: shift_id, availability, performance, quality, oee,
    downtime_minutes, speed_loss_minutes, reject_count (all as percentages 0-100 or counts).
    Returns prioritized loss categories with ROI estimates."""
    return _prioritizer.prioritize(shifts_data)
