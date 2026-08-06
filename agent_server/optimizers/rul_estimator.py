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

        Each reading: {"timestamp_minute": float, "value": float}. The time
        origin does not matter — the slope, and therefore the extrapolation, is
        invariant to it — so minutes from the first reading are as good as
        minutes from shift start.
        """
        if not sensor_readings or len(sensor_readings) < 3:
            return {
                "machine_id": machine_id,
                "sensor_type": sensor_type,
                "rul_minutes": None,
                "confidence": "insufficient_data",
                "message": f"Need at least 3 readings, got {len(sensor_readings or [])}",
            }

        # An unknown sensor used to fall back to {"warning": 0, "critical": 0},
        # and `current_value >= critical_threshold` is true for any positive
        # reading against a threshold of zero — so every unrecognised sensor
        # came back CRITICAL, "immediate maintenance required", stated with
        # full confidence. The prompt makes this the normal case, not the edge:
        # it tells the model to pick a real channel from `machine_sensors`,
        # whose names are machine-specific and will not be one of the five
        # below. Refusing is the only honest answer without a threshold.
        thresholds = custom_thresholds or DEFAULT_ALARM_THRESHOLDS.get(sensor_type)
        if not thresholds:
            return {
                "machine_id": machine_id,
                "sensor_type": sensor_type,
                "rul_minutes": None,
                "confidence": "unknown_sensor_type",
                "message": (
                    f"No alarm thresholds known for sensor_type '{sensor_type}'. "
                    "Pass custom_thresholds={'warning': <alarm_low>, 'critical': "
                    "<alarm_high>} from the machine_sensors row for this sensor, "
                    f"or use one of: {', '.join(sorted(DEFAULT_ALARM_THRESHOLDS))}."
                ),
            }
        # Defaulting a missing threshold to 0 made every positive reading "past
        # critical", which reads as a confident emergency rather than as the
        # missing input it is. A half-specified threshold is not usable.
        try:
            warning_threshold = float(thresholds["warning"])
            critical_threshold = float(thresholds["critical"])
        except (KeyError, TypeError, ValueError):
            return {
                "machine_id": machine_id,
                "sensor_type": sensor_type,
                "rul_minutes": None,
                "confidence": "incomplete_thresholds",
                "message": (
                    "custom_thresholds needs both 'warning' and 'critical' as "
                    f"numbers; got {thresholds!r}. Take them from the "
                    "machine_sensors row (alarm_low and alarm_high) for this sensor."
                ),
            }

        try:
            # `ts` is accepted because the maintenance_predictor prompt asked for
            # it for months while this read `timestamp_minute` as a bare
            # subscript, so every correctly-formed call raised KeyError.
            times = [float(r.get("timestamp_minute", r.get("ts"))) for r in sensor_readings]
            values = [float(r["value"]) for r in sensor_readings]
        except (KeyError, TypeError, ValueError):
            return {
                "machine_id": machine_id,
                "sensor_type": sensor_type,
                "rul_minutes": None,
                "confidence": "bad_input",
                "message": (
                    "Each reading needs a numeric 'timestamp_minute' (minutes, "
                    "any origin) and a numeric 'value'."
                ),
            }

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
        planned_cost = PLANNED_MAINTENANCE_COST_PER_MIN * PLANNED_MAINTENANCE_DURATION_MIN
        unplanned_cost = UNPLANNED_DOWNTIME_COST_PER_MIN * 60

        # Cost per minute of service life, not cost per intervention.
        #
        # Comparing expected costs alone has no opposing term: the expected
        # cost is planned_cost + p.(unplanned_cost - planned_cost), strictly
        # increasing in p whenever an unplanned stop costs more than a planned
        # one, and p never falls as you wait. The minimum was therefore always
        # the first candidate, so the loop returned 10% of critical RUL for
        # every input and the advertised trade-off was not modelled at all.
        #
        # What waiting actually buys is service life: maintain at 10% of RUL
        # and you throw away the other 90% of the component. Dividing by the
        # life obtained gives the standard age-replacement objective, where
        # waiting lowers the denominator faster than risk raises the numerator
        # until the failure curve turns over. The optimum is now interior and
        # it moves with the economics — make planned maintenance dearer than
        # an unplanned stop and it correctly says run to the end.
        best = None
        for step in range(1, 21):  # 5% steps, finer than the old decile grid
            frac = step / 20
            wait_min = frac * rul_critical_min
            if frac <= 0.5:
                p_fail = failure_prob_at_half * (frac / 0.5)
            else:
                p_fail = failure_prob_at_half + (failure_prob_at_full - failure_prob_at_half) * (frac - 0.5) / 0.5
            expected_cost = (1 - p_fail) * planned_cost + p_fail * unplanned_cost
            cost_rate = expected_cost / wait_min
            if best is None or cost_rate < best[0]:
                best = (cost_rate, wait_min, expected_cost, p_fail)

        _rate, best_time, best_cost, best_p = best
        return {
            "recommended_in_minutes": round(best_time, 0),
            "reasoning": (
                f"Minimises cost per minute of service life: waiting longer spreads the "
                f"{planned_cost:.0f}€ planned stop over more runtime, until the failure risk "
                f"({best_p:.0%} here) outweighs it at {UNPLANNED_DOWNTIME_COST_PER_MIN}€/min"
            ),
            "expected_cost_eur": round(best_cost, 0),
            "failure_probability": round(best_p, 3),
        }


_estimator = RULEstimator()


@tool
def estimate_rul(
    machine_id: str,
    sensor_readings: list[dict],
    sensor_type: str = "vibration",
    custom_thresholds: dict | None = None,
) -> dict:
    """Estimate Remaining Useful Life (RUL) for a machine based on sensor trend data.

    Input sensor_readings: list of {timestamp_minute: float, value: float}.
    sensor_type: one of 'vibration', 'temperature', 'motor_current', 'speed_drift',
    'pressure' — or any sensor name, if you also pass custom_thresholds.

    custom_thresholds: {"warning": <alarm_low>, "critical": <alarm_high>} taken
    from the machine_sensors row for the sensor. Required for any sensor outside
    the five built-in types; both keys must be present.

    Returns RUL estimate in minutes (to warning and critical thresholds),
    risk level, degradation trend, and optimal maintenance timing recommendation
    with cost analysis."""
    # This parameter was missing from the signature while the prompt instructed
    # the model to pass it. LangChain drops arguments a tool does not declare,
    # so every machine-specific sensor came back "unknown_sensor_type" — and the
    # message told the model to do the very thing it had just done.
    return _estimator.estimate(
        machine_id=machine_id,
        sensor_readings=sensor_readings,
        sensor_type=sensor_type,
        custom_thresholds=custom_thresholds,
    )
