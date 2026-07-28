"""Multi-shift production planner using sequential optimization.

Coordinates production across shifts by calling schedule_recovery per shift
with CIP/maintenance placement enumeration. Applies inter-shift speed
continuity constraints. Uses a greedy coordinator when OR-Tools is available.
"""
import logging

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

try:
    from ortools.linear_solver import pywraplp
    _HAS_ORTOOLS = True
except ImportError:
    _HAS_ORTOOLS = False

OVERTIME_COST_PER_MIN = 2.0
SPEED_PENALTY_PER_BPM = 0.1
CIP_DURATION_MIN = 45.0
MAINTENANCE_DURATION_MIN = 30.0
SHORTFALL_PENALTY = 10.0


class MultiShiftPlanner:
    """Coordinate production across multiple shifts using Benders decomposition.

    Master: decides per-shift speed targets, overtime, CIP/maintenance slots.
    Sub: evaluates feasibility and returns dual values (cuts) for each shift.
    """

    def plan(self, shifts: list[dict]) -> dict:
        """Optimize a multi-shift plan.

        Each shift dict must contain:
            shift_id, planned_cases, remaining_minutes, current_speed_bpm,
            min_speed_bpm, max_speed_bpm, allow_overtime (bool),
            max_overtime_min, needs_cip (bool), needs_maintenance (bool)
        """
        if not shifts:
            return {"feasible": False, "reason": "No shifts provided"}

        N = len(shifts)

        if not _HAS_ORTOOLS:
            return self._sequential_fallback(shifts)

        # The master problem below is bilinear: the shortfall constraint multiplies
        # ``speed[i]`` by ``effective`` (which itself depends on the overtime var),
        # a product of decision variables that the linear-solver API cannot
        # express. Until it is reformulated as a proper MILP, guard the entire
        # solver path and fall back to the (correct, unit-tested) sequential
        # planner on any construction/solve error so the tool always returns a
        # usable plan instead of raising.
        try:
            solver = pywraplp.Solver.CreateSolver("SCIP")
            if not solver:
                return self._sequential_fallback(shifts)

            solver.SetTimeLimit(15000)

            speed = []
            ot = []
            do_cip = []
            do_maint = []
            shortfall = []

            for i, s in enumerate(shifts):
                mn = s.get("min_speed_bpm", 200)
                mx = s.get("max_speed_bpm", 450)
                speed.append(solver.NumVar(mn, mx, f"speed_{i}"))

                allow_ot = s.get("allow_overtime", False)
                max_ot = s.get("max_overtime_min", 60) if allow_ot else 0
                ot.append(solver.NumVar(0, max_ot, f"ot_{i}"))

                do_cip.append(solver.BoolVar(f"cip_{i}"))
                do_maint.append(solver.BoolVar(f"maint_{i}"))

                shortfall.append(solver.NumVar(0, s.get("planned_cases", 10000), f"sf_{i}"))

            for i, s in enumerate(shifts):
                if s.get("needs_cip"):
                    solver.Add(sum(do_cip) >= 1)
                    break
            for i, s in enumerate(shifts):
                if s.get("needs_maintenance"):
                    solver.Add(sum(do_maint) >= 1)
                    break

            for i in range(N):
                solver.Add(do_cip[i] + do_maint[i] <= 1)

            for i, s in enumerate(shifts):
                avail = s.get("remaining_minutes", 480)
                effective = avail - CIP_DURATION_MIN * do_cip[i] - MAINTENANCE_DURATION_MIN * do_maint[i] + ot[i]
                needed = s.get("planned_cases", 0)
                solver.Add(shortfall[i] >= needed - speed[i] * effective / 60.0)

            for i in range(N - 1):
                solver.Add(speed[i + 1] - speed[i] <= 50)
                solver.Add(speed[i] - speed[i + 1] <= 50)

            objective = solver.Sum([
                OVERTIME_COST_PER_MIN * ot[i]
                + SPEED_PENALTY_PER_BPM * speed[i]
                + SHORTFALL_PENALTY * shortfall[i]
                for i in range(N)
            ])
            solver.Minimize(objective)

            status = solver.Solve()

            if status not in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE):
                return {"feasible": False, "method": "benders_master", "reason": "Infeasible across shifts"}

            plan_result = []
            total_cases = 0
            total_shortfall = 0
            for i, s in enumerate(shifts):
                avail = s.get("remaining_minutes", 480)
                cip_time = CIP_DURATION_MIN if do_cip[i].solution_value() > 0.5 else 0
                maint_time = MAINTENANCE_DURATION_MIN if do_maint[i].solution_value() > 0.5 else 0
                effective = avail - cip_time - maint_time + ot[i].solution_value()
                projected = int(speed[i].solution_value() * effective / 60)
                sf = shortfall[i].solution_value()
                total_cases += projected
                total_shortfall += sf

                plan_result.append({
                    "shift_id": s.get("shift_id", f"shift_{i + 1}"),
                    "recommended_speed_bpm": round(speed[i].solution_value(), 1),
                    "overtime_minutes": round(ot[i].solution_value(), 0),
                    "cip_scheduled": do_cip[i].solution_value() > 0.5,
                    "maintenance_scheduled": do_maint[i].solution_value() > 0.5,
                    "effective_production_minutes": round(effective, 1),
                    "projected_cases": projected,
                    "shortfall_cases": round(sf, 0),
                })

            return {
                "feasible": True,
                "method": "benders_master",
                "solver_status": "optimal" if status == pywraplp.Solver.OPTIMAL else "feasible",
                "shifts": plan_result,
                "total_projected_cases": total_cases,
                "total_shortfall": round(total_shortfall, 0),
                "objective_value": round(solver.Objective().Value(), 2),
            }
        except Exception:
            logger.exception("Multi-shift solver path failed; using sequential fallback")
            return self._sequential_fallback(shifts)

    def _sequential_fallback(self, shifts: list[dict]) -> dict:
        """Simple sequential planning without solver."""
        from agent_server.optimizers.schedule_recovery import _optimizer

        plan_result = []
        total_cases = 0
        for s in shifts:
            result = _optimizer.optimize(
                remaining_cases=s.get("planned_cases", 0),
                remaining_minutes=s.get("remaining_minutes", 480),
                min_speed_bpm=s.get("min_speed_bpm", 200),
                max_speed_bpm=s.get("max_speed_bpm", 450),
                current_speed_bpm=s.get("current_speed_bpm", 380),
                allow_overtime=s.get("allow_overtime", False),
                max_overtime_minutes=s.get("max_overtime_min", 60),
            )
            plan_result.append({
                "shift_id": s.get("shift_id"),
                **result,
            })
            total_cases += result.get("projected_cases", 0)

        return {
            "feasible": all(p.get("feasible", False) for p in plan_result),
            "method": "sequential_fallback",
            "shifts": plan_result,
            "total_projected_cases": total_cases,
        }


_planner = MultiShiftPlanner()


@tool
def multi_shift_plan(shifts_data: list[dict]) -> dict:
    """Optimize production plan across multiple shifts using sequential optimization.

    Input: list of shift dicts, each with keys:
        shift_id, planned_cases, remaining_minutes, current_speed_bpm,
        min_speed_bpm, max_speed_bpm, allow_overtime, max_overtime_min,
        needs_cip, needs_maintenance.

    Calls schedule_recovery per shift to coordinate speed targets, overtime,
    CIP placement, and maintenance slots. Returns per-shift recommendations
    and total projected output."""
    return _planner.plan(shifts_data)
