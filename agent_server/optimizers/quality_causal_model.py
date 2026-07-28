"""Causal model for quality prediction using a simplified Bayesian network.

Models the conditional probability of quality failures given sensor states,
machine conditions, and material quality. Uses a lightweight CPT (conditional
probability table) approach. CPT values are first learned from historical data;
hardcoded expert priors serve as a fallback when insufficient data is available.
"""
import logging

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# Expert-prior CPTs used as fallback when historical data is insufficient
_FALLBACK_CPT_FILLER: dict[str, float] = {
    "fill_level_out_of_spec": 0.85,
    "temperature_deviation": 0.60,
    "pressure_high": 0.45,
    "co2_low": 0.70,
    "normal": 0.02,
}

_FALLBACK_CPT_LABELLER: dict[str, float] = {
    "adhesive_failure": 0.90,
    "tension_out_of_range": 0.65,
    "speed_mismatch": 0.50,
    "normal": 0.03,
}

_FALLBACK_CPT_CAPPER: dict[str, float] = {
    "torque_out_of_spec": 0.80,
    "head_wear": 0.55,
    "misalignment": 0.75,
    "normal": 0.01,
}

_FALLBACK_CPT_CHECKWEIGHER: dict[str, float] = {
    "weight_drift": 0.70,
    "calibration_needed": 0.60,
    "ambient_temp_change": 0.30,
    "normal": 0.02,
}

_FALLBACK_CPTS: dict[str, dict[str, float]] = {
    "FILLER": _FALLBACK_CPT_FILLER,
    "LABELLER": _FALLBACK_CPT_LABELLER,
    "CAPPER": _FALLBACK_CPT_CAPPER,
    "CHECKWEIGHER": _FALLBACK_CPT_CHECKWEIGHER,
}

PRIOR_QUALITY_ISSUE = 0.05


def _get_machine_cpts() -> dict[str, dict[str, float]]:
    """Load CPTs from historical data, falling back to expert priors."""
    try:
        from agent_server.optimizers.data_calibration import calibrate_quality_cpt
        learned = calibrate_quality_cpt()
        if learned:
            merged: dict[str, dict[str, float]] = {}
            for mid in set(list(_FALLBACK_CPTS.keys()) + list(learned.keys())):
                fallback = _FALLBACK_CPTS.get(mid, {"normal": 0.02})
                data_cpt = learned.get(mid, {})
                merged[mid] = {**fallback, **data_cpt}
            return merged
    except Exception:
        logger.debug("Could not load learned CPTs, using fallback", exc_info=True)
    return dict(_FALLBACK_CPTS)


def _bayesian_update(prior: float, likelihoods: list[float]) -> float:
    """Apply Bayesian updates for multiple independent evidence factors."""
    posterior = prior
    for likelihood in likelihoods:
        posterior = (likelihood * posterior) / (
            likelihood * posterior + (1 - likelihood) * (1 - posterior)
        )
    return min(max(posterior, 0.0), 1.0)


class QualityCausalModel:
    """Simplified Bayesian network for quality failure prediction."""

    def analyze(
        self,
        machine_id: str,
        observed_conditions: list[str],
        sensor_anomaly_count: int = 0,
        recent_quality_fail_rate: float = 0.0,
    ) -> dict:
        all_cpts = _get_machine_cpts()
        cpt = all_cpts.get(machine_id, {})
        if not cpt:
            return {
                "machine_id": machine_id,
                "quality_risk_score": PRIOR_QUALITY_ISSUE,
                "risk_level": "unknown",
                "message": f"No causal model available for {machine_id}",
                "contributing_factors": [],
            }

        likelihoods = []
        factors = []

        for condition in observed_conditions:
            prob = cpt.get(condition, cpt.get("normal", 0.02))
            likelihoods.append(prob)
            factors.append({
                "condition": condition,
                "probability": round(prob, 3),
                "source": "machine_cpt",
            })

        if sensor_anomaly_count > 0:
            anomaly_likelihood = min(0.3 + 0.1 * sensor_anomaly_count, 0.9)
            likelihoods.append(anomaly_likelihood)
            factors.append({
                "condition": f"{sensor_anomaly_count} sensor anomalies",
                "probability": round(anomaly_likelihood, 3),
                "source": "anomaly_detection",
            })

        if recent_quality_fail_rate > 0.05:
            hist_likelihood = min(recent_quality_fail_rate * 2, 0.8)
            likelihoods.append(hist_likelihood)
            factors.append({
                "condition": f"Recent fail rate {recent_quality_fail_rate:.1%}",
                "probability": round(hist_likelihood, 3),
                "source": "historical_trend",
            })

        risk_score = _bayesian_update(PRIOR_QUALITY_ISSUE, likelihoods) if likelihoods else PRIOR_QUALITY_ISSUE

        if risk_score > 0.7:
            risk_level = "critical"
            recommendation = "Immediate quality hold recommended. Inspect last 30 minutes of production."
        elif risk_score > 0.4:
            risk_level = "high"
            recommendation = "Increase inspection frequency. Review sensor calibration."
        elif risk_score > 0.15:
            risk_level = "moderate"
            recommendation = "Monitor closely. Schedule preventive check at next break."
        else:
            risk_level = "low"
            recommendation = "Within normal parameters. Continue standard QC."

        try:
            from agent_server.optimizers.data_calibration import calibrate_quality_cpt
            learned = calibrate_quality_cpt()
            cpt_source = "data_driven" if machine_id in learned else "expert_prior"
        except Exception:
            cpt_source = "expert_prior"

        return {
            "machine_id": machine_id,
            "quality_risk_score": round(risk_score, 3),
            "risk_level": risk_level,
            "recommendation": recommendation,
            "contributing_factors": sorted(factors, key=lambda f: f["probability"], reverse=True),
            "prior_probability": PRIOR_QUALITY_ISSUE,
            "cpt_source": cpt_source,
        }


_model = QualityCausalModel()


@tool
def quality_risk_assessment(
    machine_id: str,
    observed_conditions: list[str],
    sensor_anomaly_count: int = 0,
    recent_quality_fail_rate: float = 0.0,
) -> dict:
    """Assess quality failure risk using a Bayesian causal model.
    Takes observed conditions (e.g., 'fill_level_out_of_spec', 'adhesive_failure'),
    sensor anomaly counts, and recent quality fail rate.
    Returns risk score (0-1), risk level, and ranked contributing factors."""
    return _model.analyze(
        machine_id=machine_id,
        observed_conditions=observed_conditions,
        sensor_anomaly_count=sensor_anomaly_count,
        recent_quality_fail_rate=recent_quality_fail_rate,
    )
