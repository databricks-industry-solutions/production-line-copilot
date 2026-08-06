"""Two-stage stochastic schedule optimisation.

Stage 1 (here-and-now):  Choose speed and overtime *before* knowing future disruptions.
Stage 2 (recourse):      For each Monte Carlo scenario, compute shortfall penalty.

The objective minimises:
    E[overtime_cost + speed_change + quality_risk + energy + shortfall_penalty]

This module uses sample-average approximation (SAA) — generate N scenarios from
the Monte Carlo forecaster, then solve a single deterministic MILP equivalent.
"""
import logging
import random

from langchain_core.tools import tool

from agent_server.optimizers._units import BOTTLES_PER_CASE, as_fraction, cases_from_bpm

logger = logging.getLogger(__name__)

try:
    from ortools.linear_solver import pywraplp
    _HAS_ORTOOLS = True
except ImportError:
    _HAS_ORTOOLS = False

N_SCENARIOS = 50
SHORTFALL_PENALTY_PER_CASE = 5.0
OVERTIME_COST_WEIGHT = 2.0
SPEED_DELTA_WEIGHT = 1.0
QUALITY_RISK_WEIGHT = 0.5
ENERGY_COST_WEIGHT = 0.3
QUALITY_SPEED_THRESHOLD = 420.0
ENERGY_BASE_SPEED = 350.0


def _generate_scenarios(
    remaining_minutes: float,
    current_oee: float,
    oee_std: float,
    active_stops: int,
    mttr: float,
    n: int = N_SCENARIOS,
) -> list[dict]:
    """Generate N scenarios of effective production minutes and OEE."""
    rng = random.Random()
    current_oee = as_fraction(current_oee)

    scenarios = []
    for _ in range(n):
        sim_remaining = remaining_minutes
        if active_stops > 0:
            repair = sum(rng.expovariate(1.0 / mttr) for _ in range(active_stops))
            sim_remaining = max(0, sim_remaining - repair)
        sim_oee = max(0.1, min(1.0, rng.gauss(current_oee, oee_std)))
        micro = rng.uniform(0.95, 1.0)
        scenarios.append({
            "effective_minutes": round(sim_remaining, 1),
            "oee": round(sim_oee, 4),
            "micro_stop_factor": round(micro, 4),
        })
    return scenarios


class StochasticScheduleOptimizer:
    """Two-stage stochastic schedule recovery using SAA."""

    def optimize(
        self,
        remaining_cases: int,
        remaining_minutes: float,
        min_speed_bpm: float,
        max_speed_bpm: float,
        current_speed_bpm: float,
        current_oee: float,
        allow_overtime: bool = False,
        max_overtime_minutes: float = 60.0,
        ramp_up_minutes: float = 10.0,
        cip_overlap_minutes: float = 0.0,
        active_stops: int = 0,
        oee_std: float = 0.05,
        mttr_minutes: float = 15.0,
    ) -> dict:
        if remaining_cases <= 0:
            return {"feasible": True, "method": "stochastic_trivial",
                    "recommended_speed_bpm": current_speed_bpm, "overtime_minutes": 0}

        effective_base = max(0, remaining_minutes - ramp_up_minutes - cip_overlap_minutes)
        scenarios = _generate_scenarios(
            effective_base, current_oee, oee_std, active_stops, mttr_minutes, N_SCENARIOS,
        )

        if not _HAS_ORTOOLS:
            return self._fallback(
                remaining_cases, effective_base, min_speed_bpm, max_speed_bpm,
                current_speed_bpm, allow_overtime, max_overtime_minutes, scenarios,
            )

        solver = pywraplp.Solver.CreateSolver("SCIP")
        if not solver:
            return self._fallback(
                remaining_cases, effective_base, min_speed_bpm, max_speed_bpm,
                current_speed_bpm, allow_overtime, max_overtime_minutes, scenarios,
            )

        solver.SetTimeLimit(10000)

        candidate_speeds = list(range(int(min_speed_bpm), int(max_speed_bpm) + 1, 10))
        if not candidate_speeds:
            candidate_speeds = [int(min_speed_bpm)]

        best_result = None
        best_obj = float("inf")
        S = len(scenarios)

        for cand_speed in candidate_speeds:
            solver2 = pywraplp.Solver.CreateSolver("SCIP")
            if not solver2:
                continue
            solver2.SetTimeLimit(5000)

            ot = solver2.NumVar(0, max_overtime_minutes if allow_overtime else 0, "ot")

            shortfall_vars = []
            for i, sc in enumerate(scenarios):
                sf = solver2.NumVar(0, remaining_cases, f"sf_{i}")
                rate_const = cand_speed * sc["oee"] * sc["micro_stop_factor"]
                solver2.Add(sf >= remaining_cases - rate_const * (sc["effective_minutes"] + ot) / BOTTLES_PER_CASE)
                shortfall_vars.append(sf)

            expected_shortfall = sum(shortfall_vars) / S
            speed_delta = abs(cand_speed - current_speed_bpm)
            q_pen = max(0, cand_speed - QUALITY_SPEED_THRESHOLD)
            e_pen = max(0, cand_speed - ENERGY_BASE_SPEED)

            objective = (
                OVERTIME_COST_WEIGHT * ot
                + SPEED_DELTA_WEIGHT * speed_delta
                + QUALITY_RISK_WEIGHT * q_pen
                + ENERGY_COST_WEIGHT * e_pen
                + SHORTFALL_PENALTY_PER_CASE * expected_shortfall
            )
            solver2.Minimize(objective)

            status = solver2.Solve()

            if status in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE):
                chosen_ot = ot.solution_value()
                avg_shortfall = sum(sf.solution_value() for sf in shortfall_vars) / S
                obj_val = solver2.Objective().Value()

                if obj_val < best_obj:
                    best_obj = obj_val
                    prod_estimates = []
                    for sc in scenarios:
                        avail = sc["effective_minutes"] + chosen_ot
                        eff_rate = cand_speed * sc["oee"] * sc["micro_stop_factor"]
                        prod_estimates.append(int(cases_from_bpm(eff_rate, avail)))
                    prod_estimates.sort()

                    best_result = {
                        "feasible": True,
                        "method": "two_stage_stochastic",
                        "recommended_speed_bpm": round(float(cand_speed), 1),
                        "overtime_minutes": round(chosen_ot, 0),
                        "scenarios_evaluated": S,
                        "expected_shortfall_cases": round(avg_shortfall, 0),
                        "p10_cases": prod_estimates[int(S * 0.1)],
                        "p50_cases": prod_estimates[int(S * 0.5)],
                        "p90_cases": prod_estimates[int(S * 0.9)],
                        "solver_status": "optimal" if status == pywraplp.Solver.OPTIMAL else "feasible",
                        "objective_value": round(obj_val, 2),
                    }

        if best_result:
            return best_result

        return {"feasible": False, "method": "two_stage_stochastic", "reason": "Infeasible under stochastic scenarios"}

    def _fallback(
        self, remaining_cases, effective_base, min_speed, max_speed,
        current_speed, allow_ot, max_ot, scenarios,
    ) -> dict:
        """Evaluate candidate solutions against all scenarios without solver."""
        best = None
        best_cost = float("inf")
        S = len(scenarios)

        for ot_min in range(0, int(max_ot if allow_ot else 0) + 1, 5):
            for s_bpm in range(int(min_speed), int(max_speed) + 1, 10):
                total_shortfall = 0
                prod_list = []
                for sc in scenarios:
                    avail = sc["effective_minutes"] + ot_min
                    eff_rate = s_bpm * sc["oee"] * sc["micro_stop_factor"]
                    produced = int(cases_from_bpm(eff_rate, avail))
                    prod_list.append(produced)
                    total_shortfall += max(0, remaining_cases - produced)

                avg_sf = total_shortfall / S
                q_pen = max(0, s_bpm - QUALITY_SPEED_THRESHOLD)
                e_pen = max(0, s_bpm - ENERGY_BASE_SPEED)
                cost = (OVERTIME_COST_WEIGHT * ot_min
                        + SPEED_DELTA_WEIGHT * abs(s_bpm - current_speed)
                        + QUALITY_RISK_WEIGHT * q_pen
                        + ENERGY_COST_WEIGHT * e_pen
                        + SHORTFALL_PENALTY_PER_CASE * avg_sf)

                if cost < best_cost:
                    best_cost = cost
                    prod_list.sort()
                    best = {
                        "feasible": True,
                        "method": "stochastic_grid_search",
                        "recommended_speed_bpm": float(s_bpm),
                        "overtime_minutes": ot_min,
                        "scenarios_evaluated": S,
                        "expected_shortfall_cases": round(avg_sf, 0),
                        "p10_cases": prod_list[int(S * 0.1)],
                        "p50_cases": prod_list[int(S * 0.5)],
                        "p90_cases": prod_list[int(S * 0.9)],
                    }

        return best or {"feasible": False, "method": "stochastic_grid_search", "reason": "No candidate found"}


_stochastic_optimizer = StochasticScheduleOptimizer()


@tool
def stochastic_schedule_recovery(
    remaining_cases: int,
    remaining_minutes: float,
    current_speed_bpm: float,
    current_oee: float,
    min_speed_bpm: float = -1.0,
    max_speed_bpm: float = -1.0,
    allow_overtime: bool | None = None,
    max_overtime_minutes: float = 60.0,
    ramp_up_minutes: float = 10.0,
    cip_overlap_minutes: float = 0.0,
    active_stops: int = 0,
) -> dict:
    """Run two-stage stochastic schedule recovery optimisation.

    Stage 1 decides speed and overtime before uncertainty is resolved.
    Stage 2 evaluates shortfall across Monte Carlo scenarios.
    Returns robust speed/overtime plan with P10/P50/P90 production estimates.

    Speed bounds auto-loaded from line_constraints if not provided."""
    from agent_server.optimizers.constraint_loader import get_bool, get_float
    from agent_server.optimizers.data_calibration import calibrate_forecaster_params

    if min_speed_bpm < 0:
        min_speed_bpm = get_float("min_speed_bpm", 200.0)
    if max_speed_bpm < 0:
        max_speed_bpm = get_float("max_speed_bpm", 450.0)
    if allow_overtime is None:
        allow_overtime = get_bool("allow_ot", False)

    cal = calibrate_forecaster_params()

    return _stochastic_optimizer.optimize(
        remaining_cases=remaining_cases,
        remaining_minutes=remaining_minutes,
        min_speed_bpm=min_speed_bpm,
        max_speed_bpm=max_speed_bpm,
        current_speed_bpm=current_speed_bpm,
        current_oee=current_oee,
        allow_overtime=allow_overtime,
        max_overtime_minutes=max_overtime_minutes,
        ramp_up_minutes=ramp_up_minutes,
        cip_overlap_minutes=cip_overlap_minutes,
        active_stops=active_stops,
        oee_std=cal.get("oee_std", 0.05),
        mttr_minutes=cal.get("historical_mttr_minutes", 15.0),
    )
