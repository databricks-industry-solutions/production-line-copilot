"""Multi-shift production planner.

Solves one MILP across all shifts: per-shift speed and overtime, where the
single CIP and maintenance stops go, and the resulting shortfall, subject to
inter-shift speed continuity. Speed is discretised so that cases produced
(speed x time, both decisions) linearises exactly.

Falls back to sequential per-shift planning when OR-Tools is unavailable or
the model proves infeasible.
"""
import logging

from langchain_core.tools import tool

from agent_server.optimizers._units import BOTTLES_PER_CASE

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

# How finely speed is discretised. Speed x time is bilinear in the raw
# formulation; fixing speed to a finite set of levels makes the product
# exactly linearisable. Eleven levels put them 25 bpm apart over a typical
# 200-450 range, which is finer than an operator would set a line by hand and
# small enough that the model still solves in well under the time limit.
SPEED_LEVELS = 11


def _speed_levels(min_bpm: float, max_bpm: float) -> list[float]:
    """Evenly spaced speed choices spanning the allowed range, endpoints included."""
    if max_bpm <= min_bpm:
        return [min_bpm]
    step = (max_bpm - min_bpm) / (SPEED_LEVELS - 1)
    return [min_bpm + step * k for k in range(SPEED_LEVELS)]


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

        # Cases produced are speed x time, and both are decisions, so the
        # constraint is bilinear and pywraplp's linear API rejects it outright.
        # The whole block used to be written that way behind the try/except
        # below, which meant construction always raised and every caller
        # silently got _sequential_fallback: the "benders_master" result, the
        # inter-shift speed continuity and the joint CIP/maintenance placement
        # were unreachable code advertising an optimization that never ran.
        #
        # Discretising speed into a fixed set of levels makes it exact rather
        # than approximate. With one binary per level, speed x time becomes a
        # sum of (binary x continuous) terms, and each of those linearises
        # exactly with the standard big-M pair. A McCormick relaxation of the
        # continuous product would have been easier and wrong in the dangerous
        # direction: it bounds production from above, so the solver could claim
        # cases the line cannot make and report a shortfall smaller than
        # reality. The fallback stays for solver-absent and infeasible cases.
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

            level_choice = []   # binary per speed level, per shift
            level_speed = []    # the bpm each of those levels stands for
            produced = []       # cases the shift makes, linear in the above

            for i, s in enumerate(shifts):
                mn = float(s.get("min_speed_bpm", 200))
                mx = float(s.get("max_speed_bpm", 450))
                if mx < mn:
                    mn, mx = mx, mn
                levels = _speed_levels(mn, mx)
                level_speed.append(levels)

                y = [solver.BoolVar(f"lvl_{i}_{k}") for k in range(len(levels))]
                solver.Add(sum(y) == 1)
                level_choice.append(y)

                sp = solver.NumVar(mn, mx, f"speed_{i}")
                solver.Add(sp == sum(levels[k] * y[k] for k in range(len(levels))))
                speed.append(sp)

                allow_ot = s.get("allow_overtime", False)
                max_ot = float(s.get("max_overtime_min", 60)) if allow_ot else 0.0
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
                avail = float(s.get("remaining_minutes", 480))
                allow_ot = s.get("allow_overtime", False)
                max_ot = float(s.get("max_overtime_min", 60)) if allow_ot else 0.0

                eff = solver.NumVar(0, avail + max_ot, f"eff_{i}")
                solver.Add(eff == avail
                           - CIP_DURATION_MIN * do_cip[i]
                           - MAINTENANCE_DURATION_MIN * do_maint[i]
                           + ot[i])

                # z[k] carries eff when level k is chosen and 0 otherwise, so
                # sum_k level_bpm[k] * z[k] is exactly speed x time.
                big_m = avail + max_ot
                levels, y = level_speed[i], level_choice[i]
                z = [solver.NumVar(0, big_m, f"z_{i}_{k}") for k in range(len(levels))]
                for k in range(len(levels)):
                    solver.Add(z[k] <= big_m * y[k])
                    solver.Add(z[k] <= eff)
                    solver.Add(z[k] >= eff - big_m * (1 - y[k]))

                made = solver.NumVar(0, solver.infinity(), f"made_{i}")
                solver.Add(made == sum(levels[k] * z[k] for k in range(len(levels))) / BOTTLES_PER_CASE)
                produced.append(made)

                needed = s.get("planned_cases", 0)
                solver.Add(shortfall[i] >= needed - made)

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
                # Read the solver's own production variable rather than
                # recomputing it, so the plan reported is the plan optimised.
                projected = int(produced[i].solution_value())
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
