"""Probabilistic production forecaster using Monte Carlo simulation.

Generates confidence intervals for end-of-shift production based on
current OEE trajectory and historical variability.
"""
import logging
import math
import random

from langchain_core.tools import tool

from agent_server.optimizers._units import as_fraction, cases_from_bpm

logger = logging.getLogger(__name__)

N_SIMULATIONS = 1000
RANDOM_SEED = None  # None = non-deterministic; set to int for reproducible tests


class ProductionForecaster:
    """Monte Carlo forecaster for end-of-shift production."""

    def forecast(
        self,
        current_cases: int,
        remaining_minutes: float,
        current_oee: float,
        ideal_rate_bpm: float = 420.0,
        oee_std: float = 0.05,
        active_stops: int = 0,
        historical_mttr_minutes: float = 15.0,
    ) -> dict:
        if remaining_minutes <= 0:
            return {
                "forecast_cases": current_cases,
                "p10": current_cases,
                "p50": current_cases,
                "p90": current_cases,
                "confidence_interval_90": [current_cases, current_cases],
                "remaining_minutes": 0,
            }

        current_oee = as_fraction(current_oee)

        rng = random.Random(RANDOM_SEED)
        results = []

        for _ in range(N_SIMULATIONS):
            sim_remaining = remaining_minutes

            if active_stops > 0:
                repair_time = sum(
                    rng.expovariate(1.0 / historical_mttr_minutes)
                    for _ in range(active_stops)
                )
                sim_remaining = max(0, sim_remaining - repair_time)

            sim_oee = max(0.1, min(1.0, rng.gauss(current_oee, oee_std)))

            micro_stop_loss = rng.uniform(0.95, 1.0)

            effective_rate = ideal_rate_bpm * sim_oee * micro_stop_loss
            sim_cases = current_cases + int(cases_from_bpm(effective_rate, sim_remaining))
            results.append(sim_cases)

        results.sort()
        n = len(results)
        p10 = results[int(n * 0.10)]
        p50 = results[int(n * 0.50)]
        p90 = results[int(n * 0.90)]
        mean = sum(results) / n
        std = math.sqrt(sum((x - mean) ** 2 for x in results) / n)

        return {
            "forecast_cases": int(mean),
            "forecast_std": int(std),
            "p10": p10,
            "p50": p50,
            "p90": p90,
            "confidence_interval_90": [p10, p90],
            "remaining_minutes": remaining_minutes,
            "simulations": N_SIMULATIONS,
            "inputs": {
                "current_cases": current_cases,
                "current_oee": current_oee,
                "ideal_rate_bpm": ideal_rate_bpm,
                "active_stops": active_stops,
            },
        }


_forecaster = ProductionForecaster()


@tool
def production_forecast(
    current_cases: int,
    remaining_minutes: float,
    current_oee: float,
    ideal_rate_bpm: float = -1.0,
    oee_std: float = -1.0,
    active_stops: int = 0,
    historical_mttr_minutes: float = -1.0,
) -> dict:
    """Forecast end-of-shift production using Monte Carlo simulation.
    Returns P10/P50/P90 confidence intervals for total cases produced.
    Accounts for active stops (with exponential repair time distribution),
    OEE variability, and micro-stop losses.

    Parameters ideal_rate_bpm, oee_std, and historical_mttr_minutes are
    auto-calibrated from actual data when not explicitly provided (pass -1)."""
    from agent_server.optimizers.data_calibration import calibrate_forecaster_params

    cal = calibrate_forecaster_params()

    if ideal_rate_bpm < 0:
        ideal_rate_bpm = cal.get("ideal_rate_bpm", 420.0)
    if oee_std < 0:
        oee_std = cal.get("oee_std", 0.05)
    if historical_mttr_minutes < 0:
        historical_mttr_minutes = cal.get("historical_mttr_minutes", 15.0)

    return _forecaster.forecast(
        current_cases=current_cases,
        remaining_minutes=remaining_minutes,
        current_oee=current_oee,
        ideal_rate_bpm=ideal_rate_bpm,
        oee_std=oee_std,
        active_stops=active_stops,
        historical_mttr_minutes=historical_mttr_minutes,
    )
