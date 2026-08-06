"""Schedule recovery optimizer using OR-Tools MILP solver.

Finds the optimal speed profile and overtime allocation to recover planned
production after a disruption, respecting ramp-up time, CIP windows, and
speed-quality trade-offs.
"""
import logging

from langchain_core.tools import tool

from agent_server.optimizers._units import BOTTLES_PER_CASE, bpm_for_cases, cases_from_bpm

logger = logging.getLogger(__name__)

try:
    from ortools.linear_solver import pywraplp
    _HAS_ORTOOLS = True
except ImportError:
    _HAS_ORTOOLS = False
    logger.warning("OR-Tools not available; schedule_recovery will use heuristic fallback")

OVERTIME_COST_WEIGHT = 2.0
SPEED_DELTA_WEIGHT = 1.0
QUALITY_RISK_WEIGHT = 0.5
ENERGY_COST_WEIGHT = 0.3
QUALITY_RISK_SPEED_THRESHOLD = 420.0
ENERGY_BASE_SPEED = 350.0  # energy cost is proportional to speed above this


class ScheduleRecoveryOptimizer:
    """MILP optimizer for schedule recovery after disruptions.

    Uses OR-Tools SCIP solver to find optimal speed + overtime that minimises
    a weighted cost of overtime, speed change, and quality risk at high speed.
    Falls back to grid search if OR-Tools is unavailable.
    """

    def optimize(
        self,
        remaining_cases: int,
        remaining_minutes: float,
        min_speed_bpm: float,
        max_speed_bpm: float,
        current_speed_bpm: float,
        allow_overtime: bool,
        max_overtime_minutes: float = 60,
        ramp_up_minutes: float = 10,
        cip_overlap_minutes: float = 0,
        changeover_minutes: float = 0,
    ) -> dict:
        if remaining_cases <= 0:
            return {"feasible": True, "recommended_speed_bpm": current_speed_bpm,
                    "overtime_minutes": 0, "projected_cases": 0, "completion_pct": 100.0,
                    "method": "trivial"}

        if min_speed_bpm <= 0 or max_speed_bpm <= 0 or remaining_minutes <= 0:
            return {"feasible": False, "reason": "Invalid input parameters"}

        effective_base = max(0.0, remaining_minutes - ramp_up_minutes - cip_overlap_minutes - changeover_minutes)

        if _HAS_ORTOOLS:
            result = self._milp_solve(
                remaining_cases, effective_base, min_speed_bpm, max_speed_bpm,
                current_speed_bpm, allow_overtime, max_overtime_minutes,
            )
        else:
            result = self._grid_search(
                remaining_cases, effective_base, min_speed_bpm, max_speed_bpm,
                current_speed_bpm, allow_overtime, max_overtime_minutes,
            )

        if result and result.get("feasible"):
            result["ramp_up_minutes"] = ramp_up_minutes
            result["effective_production_minutes"] = effective_base + result.get("overtime_minutes", 0)
            if changeover_minutes > 0:
                result["changeover_minutes"] = changeover_minutes
            if cip_overlap_minutes > 0:
                result["cip_overlap_minutes"] = cip_overlap_minutes
                result["cip_warning"] = "Recovery window overlaps with planned CIP"
            margin = result.get("constraint_margin", {})
            if margin:
                result["constraint_margin"] = margin
        return result

    def _milp_solve(
        self,
        remaining_cases: int,
        effective_base: float,
        min_speed_bpm: float,
        max_speed_bpm: float,
        current_speed_bpm: float,
        allow_overtime: bool,
        max_overtime_minutes: float,
    ) -> dict:
        """Solve with OR-Tools SCIP MILP solver.

        Linearises the bilinear speed*overtime term by discretising overtime
        into 5-min slots and introducing binary selector variables.
        """
        solver = pywraplp.Solver.CreateSolver("SCIP")
        if not solver:
            logger.warning("SCIP solver not available, falling back to grid search")
            return self._grid_search(
                remaining_cases, effective_base, min_speed_bpm, max_speed_bpm,
                current_speed_bpm, allow_overtime, max_overtime_minutes,
            )

        solver.SetTimeLimit(5000)

        OT_STEP = 5
        ot_max = int(max_overtime_minutes) if allow_overtime else 0
        ot_slots = list(range(0, ot_max + 1, OT_STEP))
        if not ot_slots:
            ot_slots = [0]
        K = len(ot_slots)

        y = [solver.BoolVar(f"y_{k}") for k in range(K)]
        solver.Add(sum(y) == 1)

        # Lower bound must be 0.0, not min_speed_bpm: the `<= max_speed_bpm * y[k]`
        # constraint forces speed[k] to 0 for non-selected slots (y[k]=0). A
        # min_speed_bpm floor there makes `min_speed_bpm <= 0` unsatisfiable and
        # renders the whole model infeasible. The `>= min_speed_bpm * y[k]`
        # constraint below already enforces the minimum for the chosen slot.
        speed = [solver.NumVar(0.0, max_speed_bpm, f"s_{k}") for k in range(K)]

        for k in range(K):
            solver.Add(speed[k] >= min_speed_bpm * y[k])
            solver.Add(speed[k] <= max_speed_bpm * y[k])

        production_terms = []
        for k in range(K):
            avail_k = effective_base + ot_slots[k]
            if avail_k <= 0:
                solver.Add(y[k] == 0)
                continue
            production_terms.append(speed[k] * (avail_k / BOTTLES_PER_CASE))

        if production_terms:
            solver.Add(sum(production_terms) >= remaining_cases)

        speed_up = solver.NumVar(0, max_speed_bpm, "speed_up")
        speed_down = solver.NumVar(0, max_speed_bpm, "speed_down")
        for k in range(K):
            solver.Add(speed[k] - current_speed_bpm * y[k] <= speed_up)
            solver.Add(current_speed_bpm * y[k] - speed[k] <= speed_down)

        quality_penalty = [solver.NumVar(0, max_speed_bpm, f"qp_{k}") for k in range(K)]
        energy_penalty = [solver.NumVar(0, max_speed_bpm, f"ep_{k}") for k in range(K)]
        for k in range(K):
            solver.Add(quality_penalty[k] >= speed[k] - QUALITY_RISK_SPEED_THRESHOLD * y[k])
            solver.Add(quality_penalty[k] >= 0)
            solver.Add(energy_penalty[k] >= speed[k] - ENERGY_BASE_SPEED * y[k])
            solver.Add(energy_penalty[k] >= 0)

        overtime_cost = sum(ot_slots[k] * y[k] for k in range(K))

        objective = (
            OVERTIME_COST_WEIGHT * overtime_cost
            + SPEED_DELTA_WEIGHT * (speed_up + speed_down)
            + QUALITY_RISK_WEIGHT * sum(quality_penalty)
            + ENERGY_COST_WEIGHT * sum(energy_penalty)
        )
        solver.Minimize(objective)

        status = solver.Solve()

        if status in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE):
            chosen_k = next(k for k in range(K) if y[k].solution_value() > 0.5)
            chosen_speed = speed[chosen_k].solution_value()
            chosen_ot = ot_slots[chosen_k]
            avail = effective_base + chosen_ot
            projected = int(cases_from_bpm(chosen_speed, avail))

            return {
                "feasible": True,
                "recommended_speed_bpm": round(chosen_speed, 1),
                "overtime_minutes": chosen_ot,
                "projected_cases": projected,
                "completion_pct": round(min(projected / remaining_cases * 100, 100), 1),
                "method": "milp",
                "solver_status": "optimal" if status == pywraplp.Solver.OPTIMAL else "feasible",
                "objective_value": round(solver.Objective().Value(), 2),
                "alternatives_evaluated": K,
                "quality_risk_penalty": round(quality_penalty[chosen_k].solution_value(), 1),
                "energy_cost_penalty": round(energy_penalty[chosen_k].solution_value(), 1),
                "cost_breakdown": {
                    "overtime": round(OVERTIME_COST_WEIGHT * chosen_ot, 1),
                    "speed_change": round(SPEED_DELTA_WEIGHT * abs(chosen_speed - current_speed_bpm), 1),
                    "quality_risk": round(QUALITY_RISK_WEIGHT * quality_penalty[chosen_k].solution_value(), 1),
                    "energy": round(ENERGY_COST_WEIGHT * energy_penalty[chosen_k].solution_value(), 1),
                },
                "constraint_margin": {
                    "speed_headroom_bpm": round(max_speed_bpm - chosen_speed, 1),
                    "overtime_headroom_min": round(max_overtime_minutes - chosen_ot, 0),
                },
            }

        if status == pywraplp.Solver.INFEASIBLE:
            relaxation = self._suggest_relaxation(
                remaining_cases, effective_base, min_speed_bpm, max_speed_bpm,
                max_overtime_minutes, allow_overtime,
            )
            return {
                "feasible": False,
                "reason": "No feasible solution within current constraints",
                "method": "milp",
                "solver_status": "infeasible",
                "relaxation_suggestions": relaxation,
            }

        logger.warning("MILP solver returned status %s, falling back to grid search", status)
        return self._grid_search(
            remaining_cases, effective_base, min_speed_bpm, max_speed_bpm,
            current_speed_bpm, allow_overtime, max_overtime_minutes,
        )

    def _suggest_relaxation(
        self, remaining_cases, effective_base, min_speed, max_speed, max_ot, allow_ot,
    ) -> list[str]:
        suggestions = []
        max_avail = effective_base + (max_ot if allow_ot else 0)
        if max_avail > 0:
            needed = bpm_for_cases(remaining_cases, max_avail)
            if needed > max_speed:
                gap = needed - max_speed
                suggestions.append(f"Increase max speed by {gap:.0f} BPM or reduce target by {int(cases_from_bpm(gap, max_avail))} cases")
        if not allow_ot:
            at_max_speed = cases_from_bpm(max_speed, effective_base)
            if at_max_speed < remaining_cases:
                needed_ot = (remaining_cases - at_max_speed) * BOTTLES_PER_CASE / max_speed
                suggestions.append(f"Enable overtime (~{needed_ot:.0f} min at max speed)")
        if not suggestions:
            suggestions.append("Consider splitting the recovery across multiple shifts")
        return suggestions

    def _grid_search(
        self,
        remaining_cases: int,
        effective_base: float,
        min_speed_bpm: float,
        max_speed_bpm: float,
        current_speed_bpm: float,
        allow_overtime: bool,
        max_overtime_minutes: float,
    ) -> dict:
        """Fallback grid search with 5-min overtime steps and 10-BPM speed steps."""
        best = None
        best_cost = float("inf")

        ot_range = range(0, int(max_overtime_minutes if allow_overtime else 0) + 1, 5)
        speed_range_step = 10

        for ot_min in ot_range:
            avail_time = effective_base + ot_min
            if avail_time <= 0:
                continue
            needed_speed = bpm_for_cases(remaining_cases, avail_time)
            if needed_speed < min_speed_bpm:
                needed_speed = min_speed_bpm
            if needed_speed > max_speed_bpm:
                continue

            for s_offset in range(0, int(max_speed_bpm - needed_speed) + 1, speed_range_step):
                test_speed = needed_speed + s_offset
                if test_speed > max_speed_bpm:
                    break
                projected = int(cases_from_bpm(test_speed, avail_time))
                speed_delta = abs(test_speed - current_speed_bpm)
                quality_pen = max(0, test_speed - QUALITY_RISK_SPEED_THRESHOLD)
                energy_pen = max(0, test_speed - ENERGY_BASE_SPEED)
                cost = (OVERTIME_COST_WEIGHT * ot_min
                        + SPEED_DELTA_WEIGHT * speed_delta
                        + QUALITY_RISK_WEIGHT * quality_pen
                        + ENERGY_COST_WEIGHT * energy_pen)

                if cost < best_cost:
                    best_cost = cost
                    best = {
                        "feasible": True,
                        "recommended_speed_bpm": round(test_speed, 1),
                        "overtime_minutes": ot_min,
                        "projected_cases": projected,
                        "completion_pct": round(min(projected / remaining_cases * 100, 100), 1) if remaining_cases > 0 else 100.0,
                        "method": "grid_search",
                        "quality_risk_penalty": round(quality_pen, 1),
                        "energy_cost_penalty": round(energy_pen, 1),
                    }
                if projected >= remaining_cases:
                    break

        if best:
            return best
        return {"feasible": False, "reason": "No solution within constraints", "method": "grid_search"}


_optimizer = ScheduleRecoveryOptimizer()


@tool
def schedule_recovery(
    remaining_cases: int,
    remaining_minutes: float,
    min_speed_bpm: float = -1.0,
    max_speed_bpm: float = -1.0,
    current_speed_bpm: float = 380.0,
    allow_overtime: bool | None = None,
    max_overtime_minutes: float = 60.0,
    ramp_up_minutes: float = 10.0,
    cip_overlap_minutes: float = 0.0,
    changeover_minutes: float = 0.0,
) -> dict:
    """Run schedule recovery optimization to find the best speed/overtime plan
    after a production disruption. Uses MILP (OR-Tools SCIP) when available, with
    grid search fallback. Minimises a weighted cost of overtime, speed change, and
    quality risk at high speed. Accounts for ramp-up time, CIP overlaps, and changeovers.

    Speed bounds and overtime permission are auto-loaded from line_constraints if
    not explicitly provided (pass -1 or None to use DB values).
    Returns recommended speed, overtime, projected cases, constraint margins, and
    infeasibility relaxation suggestions if no solution exists."""
    from agent_server.optimizers.constraint_loader import get_bool, get_float

    if min_speed_bpm < 0:
        min_speed_bpm = get_float("min_speed_bpm", 200.0)
    if max_speed_bpm < 0:
        max_speed_bpm = get_float("max_speed_bpm", 450.0)
    if allow_overtime is None:
        allow_overtime = get_bool("allow_ot", False)

    return _optimizer.optimize(
        remaining_cases=remaining_cases,
        remaining_minutes=remaining_minutes,
        min_speed_bpm=min_speed_bpm,
        max_speed_bpm=max_speed_bpm,
        current_speed_bpm=current_speed_bpm,
        allow_overtime=allow_overtime,
        max_overtime_minutes=max_overtime_minutes,
        ramp_up_minutes=ramp_up_minutes,
        cip_overlap_minutes=cip_overlap_minutes,
        changeover_minutes=changeover_minutes,
    )
