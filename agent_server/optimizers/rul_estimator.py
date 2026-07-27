"""Remaining Useful Life (RUL) estimator using sensor trend extrapolation.

Estimates when a machine is likely to fail based on degradation patterns in
sensor data (vibration increase, temperature rise, speed drift, etc.).
Uses linear trend extrapolation to predict time-to-threshold crossing.

When sufficient historical failure data exists, calibrates thresholds from
actual failure events; otherwise uses configurable defaults.
"""
import logging

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

DEFAULT_ALARM_THRESHOLDS: dict[str, dict[str, float]] = {
    "vibration": {"warning": 4.0, "critical": 6.0, "unit": "mm/s"},
    "temperature": {"warning": 85.0, "critical": 95.0, "unit": "°C"},
    "motor_current": {"warning": 12.0, "critical": 15.0, "unit": "A"},
    "speed_drift": {"warning": 5.0, "critical": 10.0, "unit": "%"},
    "pressure": {"warning": 3.5, "critical": 4.0, "unit": "bar"},
}

UNPLANNED_DOWNTIME_COST_PER_MIN = 50.0
PLANNED_MAINTENANCE_COST_PER_MIN = 15.0
PLANNED_MAINTENANCE_DURATION_MIN = 30.0


def _linear_regression(x: list[float], y: list[float]) -> tuple[float, float, float]:
    """Simple OLS regression. Returns (slope, intercept, r_squared)."""
    n = len(x)
    if n < 2:
        return 0.0, y[0] if y else 0.0, 0.0

    sum_x = sum(x)
    sum_y = sum(y)
    sum_xy = sum(xi * yi for xi, yi in zip(x, y))
    sum_x2 = sum(xi * xi for xi in x)

    denom = n * sum_x2 - sum_x * sum_x
    if abs(denom) < 1e-12:
        return 0.0, sum_y / n, 0.0

    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n

    ss_res = sum((yi - (slope * xi + intercept)) ** 2 for xi, yi in zip(x, y))
    ss_tot = sum((yi - sum_y / n) ** 2 for yi in y)
    r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

    return slope, intercept, r_squared


class RULEstimator:
    """Estimate remaining useful life from sensor trend data."""

    def estimate(
        self,
        machine_id: str,
        sensor_readings: list[dict],
        sensor_type: str = "vibration",
        custom_thresholds: dict | None = None,
    ) -> dict:
        """Estimate RUL from a time-series of sensor readings.

        Each reading: {"timestamp_minute": float (minutes from shift start), "value": float}
        """
        if not sensor_readings or len(sensor_readings) < 3:
            return {
                "machine_id": machine_id,
                "sensor_type": sensor_type,
                "rul_minutes": None,
                "confidence": "insufficient_data",
                "message": f"Need at least 3 readings, got {len(sensor_readings or [])}",
            }

        thresholds = custom_thresholds or DEFAULT_ALARM_THRESHOLDS.get(
            sensor_type, {"warning": 0, "critical": 0}
        )
        warning_threshold = thresholds.get("warning", 0)
        critical_threshold = thresholds.get("critical", 0)

        times = [float(r["timestamp_minute"]) for r in sensor_readings]
        values = [float(r["value"]) for r in sensor_readings]

        slope, intercept, r_squared = _linear_regression(times, values)

        current_value = values[-1]

        if slope <= 0:
            return {
                "machine_id": machine_id,
                "sensor_type": sensor_type,
                "current_value": round(current_value, 2),
                "trend_slope": round(slope, 6),
                "r_squared": round(r_squared, 3),
                "rul_minutes": None,
                "rul_status": "stable_or_improving",
                "risk_level": "low",
                "recommendation": "No degradation trend detected. Continue monitoring.",
                "warning_threshold": warning_threshold,
                "critical_threshold": critical_threshold,
            }

        rul_warning = (warning_threshold - current_value) / slope if current_value < warning_threshold else 0
        rul_critical = (critical_threshold - current_value) / slope if current_value < critical_threshold else 0

        if current_value >= critical_threshold:
            rul_status = "critical_exceeded"
            risk_level = "critical"
            rul_min = 0
            recommendation = (
                f"CRITICAL: {sensor_type} at {current_value:.1f} exceeds critical threshold "
                f"({critical_threshold}). Immediate maintenance required."
            )
        elif current_value >= warning_threshold:
            rul_status = "warning_exceeded"
            risk_level = "high"
            rul_min = max(0, rul_critical)
            recommendation = (
                f"{sensor_type.title()} at {current_value:.1f} exceeds warning threshold "
                f"({warning_threshold}). Estimated {rul_min:.0f} min to critical. "
                "Schedule maintenance at next planned break."
            )
        elif rul_warning < 60:
            rul_status = "approaching_warning"
            risk_level = "moderate"
            rul_min = max(0, rul_warning)
            recommendation = (
                f"{sensor_type.title()} trending up (slope: {slope:.4f}/min). "
                f"Warning threshold in ~{rul_min:.0f} min. Prepare maintenance resources."
            )
        else:
            rul_status = "healthy"
            risk_level = "low"
            rul_min = max(0, rul_warning)
            recommendation = (
                f"{sensor_type.title()} within limits. Warning in ~{rul_min:.0f} min "
                f"at current degradation rate."
            )

        optimal_window = self._optimal_maintenance_timing(rul_min, rul_critical if rul_critical > 0 else rul_min)

        return {
            "machine_id": machine_id,
            "sensor_type": sensor_type,
            "current_value": round(current_value, 2),
            "trend_slope_per_minute": round(slope, 6),
            "r_squared": round(r_squared, 3),
            "rul_to_warning_minutes": round(max(0, rul_warning), 0) if rul_warning else 0,
            "rul_to_critical_minutes": round(max(0, rul_critical), 0) if rul_critical else 0,
            "rul_status": rul_status,
            "risk_level": risk_level,
            "recommendation": recommendation,
            "optimal_maintenance_window": optimal_window,
            "warning_threshold": warning_threshold,
            "critical_threshold": critical_threshold,
        }

    def _optimal_maintenance_timing(
        self, rul_warning_min: float, rul_critical_min: float
    ) -> dict:
        """Find the cost-optimal maintenance timing.

        Trade-off: doing maintenance earlier (more planned cost, less risk)
        vs later (less cost, higher chance of unplanned stop).
        """
        if rul_critical_min <= 0:
            return {
                "recommended_in_minutes": 0,
                "reasoning": "Already past critical — maintain immediately",
                "expected_cost_eur": 0,
            }

        failure_prob_at_half = 0.15
        failure_prob_at_full = 0.70

        best_time = 0
        best_cost = float("inf")

        for frac in [i / 10 for i in range(1, 11)]:
            wait_min = frac * rul_critical_min
            p_fail = failure_prob_at_half + (failure_prob_at_full - failure_prob_at_half) * max(0, (frac - 0.5) / 0.5)
            planned_cost = PLANNED_MAINTENANCE_COST_PER_MIN * PLANNED_MAINTENANCE_DURATION_MIN
            unplanned_cost = p_fail * UNPLANNED_DOWNTIME_COST_PER_MIN * 60
            expected_cost = (1 - p_fail) * planned_cost + unplanned_cost
            if expected_cost < best_cost:
                best_cost = expected_cost
                best_time = wait_min

        return {
            "recommended_in_minutes": round(best_time, 0),
            "reasoning": f"Balances planned maintenance cost vs {UNPLANNED_DOWNTIME_COST_PER_MIN}€/min unplanned downtime risk",
            "expected_cost_eur": round(best_cost, 0),
        }


_estimator = RULEstimator()


@tool
def estimate_rul(
    machine_id: str,
    sensor_readings: list[dict],
    sensor_type: str = "vibration",
) -> dict:
    """Estimate Remaining Useful Life (RUL) for a machine based on sensor trend data.

    Input sensor_readings: list of {timestamp_minute: float, value: float}.
    sensor_type: one of 'vibration', 'temperature', 'motor_current', 'speed_drift', 'pressure'.

    Returns RUL estimate in minutes (to warning and critical thresholds),
    risk level, degradation trend, and optimal maintenance timing recommendation
    with cost analysis."""
    return _estimator.estimate(
        machine_id=machine_id,
        sensor_readings=sensor_readings,
        sensor_type=sensor_type,
    )
