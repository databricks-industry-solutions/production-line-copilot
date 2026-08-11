"""The optimizers each encoded a trade-off their maths did not implement.

None of these failed loudly. They returned plausible numbers, which is why
they survived: a Pareto that sums past 100%, a "cost-optimal" search whose
answer never varied, and a branded MILP result that no caller could ever
receive. These tests pin the properties that make each output meaningful.
"""

from __future__ import annotations

import pytest

from agent_server.optimizers import rul_estimator as rul_mod
from agent_server.optimizers._units import cases_from_bpm
from agent_server.optimizers.multi_shift_planner import MultiShiftPlanner, _speed_levels
from agent_server.optimizers.oee_loss_prioritizer import OEELossPrioritizer
from agent_server.optimizers.rul_estimator import RULEstimator

# ---------------------------------------------------------------------------
# Pareto contributions
# ---------------------------------------------------------------------------

class TestAParetoCannotExceedOneHundred:
    def setup_method(self):
        self.p = OEELossPrioritizer()

    def _shift(self, a, p, q, **kw):
        return {"shift_id": "s1", "availability": a, "performance": p,
                "quality": q, "oee": a * p * q / 10_000, **kw}

    def test_the_case_that_gave_one_hundred_and_ten_percent(self):
        """A = P = Q = 90 divided each 10-point loss by the 27.1-point OEE gap."""
        out = self.p.prioritize([self._shift(90, 90, 90)])
        assert out["pareto"][-1]["cumulative_pct"] == 100.0

    def test_the_shares_sum_to_a_whole(self):
        out = self.p.prioritize([self._shift(90, 90, 90)])
        assert sum(c["contribution_pct"] for c in out["pareto"]) == pytest.approx(100.0, abs=0.3)

    @pytest.mark.parametrize("a,p,q", [
        (90, 90, 90), (99, 99, 99), (50, 60, 70), (80, 95, 99), (30, 40, 50),
    ])
    def test_no_input_pushes_it_over(self, a, p, q):
        out = self.p.prioritize([self._shift(a, p, q)])
        assert out["pareto"][-1]["cumulative_pct"] <= 100.0
        assert all(0 <= c["contribution_pct"] <= 100 for c in out["pareto"])

    def test_the_cumulative_never_decreases(self):
        out = self.p.prioritize([self._shift(70, 85, 95)])
        seen = [c["cumulative_pct"] for c in out["pareto"]]
        assert seen == sorted(seen)

    def test_the_biggest_loss_still_ranks_first(self):
        """Renormalising must not disturb the ordering, which is the point."""
        out = self.p.prioritize([self._shift(60, 95, 99)])
        assert out["pareto"][0]["category"] == "Availability"

    def test_the_headline_gap_is_still_the_multiplicative_one(self):
        """Only the split is additive; the gap to close is real OEE."""
        out = self.p.prioritize([self._shift(90, 90, 90)])
        assert out["total_oee_gap"] == pytest.approx(100 - 72.9, abs=0.1)

    def test_a_perfect_shift_does_not_divide_by_zero(self):
        out = self.p.prioritize([self._shift(100, 100, 100)])
        assert all(c["contribution_pct"] == 0 for c in out["pareto"])

    def test_no_shifts_is_still_empty(self):
        assert self.p.prioritize([])["pareto"] == []


# ---------------------------------------------------------------------------
# Maintenance timing
# ---------------------------------------------------------------------------

class TestMaintenanceTimingIsActuallyOptimised:
    def setup_method(self):
        self.e = RULEstimator()

    def test_it_is_not_a_fixed_fraction_of_rul(self):
        """The old objective was monotonic, so it always picked the first
        candidate: exactly 10% of critical RUL for every input."""
        got = {
            self.e._optimal_maintenance_timing(r * 0.5, r)["recommended_in_minutes"] / r
            for r in (100.0, 600.0, 1440.0)
        }
        assert got != {0.1}

    def test_the_optimum_is_interior(self):
        """Not the earliest candidate and not the latest — a real trade-off."""
        rul = 600.0
        rec = self.e._optimal_maintenance_timing(300.0, rul)["recommended_in_minutes"]
        assert 0 < rec < rul

    def test_a_dearer_planned_stop_is_worth_deferring(self, monkeypatch):
        """When a planned stop costs more than an unplanned one, run it out."""
        cheap = self.e._optimal_maintenance_timing(300.0, 600.0)["recommended_in_minutes"]
        monkeypatch.setattr(rul_mod, "PLANNED_MAINTENANCE_COST_PER_MIN", 200.0)
        dear = self.e._optimal_maintenance_timing(300.0, 600.0)["recommended_in_minutes"]
        assert dear > cheap

    def test_it_scales_with_the_life_left(self):
        short = self.e._optimal_maintenance_timing(50.0, 100.0)["recommended_in_minutes"]
        long = self.e._optimal_maintenance_timing(500.0, 1000.0)["recommended_in_minutes"]
        assert long > short

    def test_past_critical_means_now(self):
        out = self.e._optimal_maintenance_timing(0.0, 0.0)
        assert out["recommended_in_minutes"] == 0

    def test_it_reports_the_risk_it_accepted(self):
        out = self.e._optimal_maintenance_timing(300.0, 600.0)
        assert 0.0 <= out["failure_probability"] <= 1.0
        assert out["expected_cost_eur"] > 0


# ---------------------------------------------------------------------------
# Multi-shift MILP
# ---------------------------------------------------------------------------

def _shifts(n=2, planned=3000):
    return [
        {"shift_id": f"s{i+1}", "remaining_minutes": 480, "planned_cases": planned,
         "allow_overtime": True, "max_overtime_min": 60,
         "min_speed_bpm": 200, "max_speed_bpm": 450,
         "needs_cip": i == 0, "needs_maintenance": i == 1}
        for i in range(n)
    ]


class TestTheSolverPathIsReachable:
    def setup_method(self):
        self.p = MultiShiftPlanner()

    def test_it_no_longer_falls_back_on_every_call(self):
        """The bilinear constraint made construction raise every time, so the
        branded result at the end of the solver path was dead code."""
        assert self.p.plan(_shifts())["method"] == "milp"

    def test_it_claims_optimal_only_when_it_solved(self):
        out = self.p.plan(_shifts())
        assert out["solver_status"] in ("optimal", "feasible")
        assert "objective_value" in out

    def test_it_does_not_promise_cases_the_line_cannot_make(self):
        """A McCormick relaxation would bound production from above and let
        the solver understate the shortfall. The discretisation is exact."""
        out = self.p.plan(_shifts(planned=9000))
        for s in out["shifts"]:
            physical = cases_from_bpm(
                s["recommended_speed_bpm"], s["effective_production_minutes"]
            )
            assert s["projected_cases"] <= physical + 1

    def test_it_honours_inter_shift_speed_continuity(self):
        out = self.p.plan(_shifts(n=3))
        speeds = [s["recommended_speed_bpm"] for s in out["shifts"]]
        for a, b in zip(speeds, speeds[1:]):
            assert abs(a - b) <= 50 + 1e-6

    def test_it_places_the_cip_and_the_maintenance(self):
        out = self.p.plan(_shifts())
        assert any(s["cip_scheduled"] for s in out["shifts"])
        assert any(s["maintenance_scheduled"] for s in out["shifts"])

    def test_a_shift_never_takes_both_stops(self):
        out = self.p.plan(_shifts(n=3))
        assert all(not (s["cip_scheduled"] and s["maintenance_scheduled"]) for s in out["shifts"])

    def test_the_stops_cost_the_time_they_take(self):
        out = self.p.plan(_shifts())
        for s in out["shifts"]:
            expected = 480 - (45 if s["cip_scheduled"] else 0) \
                           - (30 if s["maintenance_scheduled"] else 0) \
                           + s["overtime_minutes"]
            assert s["effective_production_minutes"] == pytest.approx(expected, abs=1)

    def test_an_impossible_target_reports_a_shortfall_rather_than_lying(self):
        out = self.p.plan(_shifts(planned=99_000))
        assert out["total_shortfall"] > 0

    def test_no_shifts_is_still_handled(self):
        assert self.p.plan([])["feasible"] is False

    def test_a_single_shift_solves(self):
        assert self.p.plan(_shifts(n=1))["method"] == "milp"


class TestTheSpeedGrid:
    def test_it_spans_the_allowed_range(self):
        levels = _speed_levels(200.0, 450.0)
        assert levels[0] == 200.0 and levels[-1] == 450.0

    def test_a_fixed_speed_collapses_to_one_level(self):
        assert _speed_levels(300.0, 300.0) == [300.0]

    def test_the_levels_are_ordered_and_distinct(self):
        levels = _speed_levels(200.0, 450.0)
        assert levels == sorted(levels)
        assert len(set(levels)) == len(levels)


# ---------------------------------------------------------------------------
# What the planner tells the model about itself
# ---------------------------------------------------------------------------

class TestThePlannerDescribesTheAlgorithmItRuns:
    """A tool description is prompt text, and the model quotes it to operators.

    Three artefacts described this planner and no two agreed: the class
    docstring said Benders decomposition, the `@tool` description said
    "sequential optimization ... calls schedule_recovery per shift", and the
    result was branded "benders_master". What runs is one monolithic SCIP MILP,
    so an operator asking how the plan was produced could be told, in fluent
    prose, about a decomposition that has never executed.
    """

    def test_the_result_is_labelled_for_what_it_is(self):
        assert MultiShiftPlanner().plan(_shifts())["method"] == "milp"

    def test_no_artefact_claims_an_algorithm_the_code_does_not_run(self):
        from agent_server.optimizers import multi_shift_planner as mod

        surfaces = {
            "class docstring": MultiShiftPlanner.__doc__ or "",
            "tool description": mod.multi_shift_plan.description or "",
        }
        for where, text in surfaces.items():
            body = text.lower()
            # Named only where the module explains what it stopped doing, which
            # is prose about the past, not a claim about the present. The class
            # docstring earns that; the tool description has no room for it.
            if where == "tool description":
                assert "benders" not in body, f"{where} still names Benders"
            assert "calls schedule_recovery per shift" not in body, (
                f"{where} says it delegates per shift; the solver path decides "
                "every shift in one model and only the fallback delegates"
            )
