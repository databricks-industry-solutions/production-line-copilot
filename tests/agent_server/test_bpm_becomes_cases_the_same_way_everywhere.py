"""The line runs in bottles; the plan is written in cases. One conversion.

Every optimizer takes a ``*_speed_bpm`` and reports ``projected_cases``, and
every one of them divided by 60 — the minutes-to-hours conversion for a per-hour
rate, applied to a per-minute one. Nothing raised. At the nominal 420 BPM over a
480-minute shift they returned 3,360 cases against a plan of 8,400, so the line
looked 2.5x slower than it is (60/24).

That is not a cosmetic error in a report. Recovery advice is a feasibility test:
a line that would comfortably make its plan was told it could not, and the
suggested remedy was to run faster than its own stated maximum. Two of these
tests failed against the old code by asserting exactly that.

So the conversion lives in one function, and these tests hold every caller to it
and to the producer's constant — the producer is what actually turns bottles
into the cases the plan is measured against, so if the two ever disagree the
optimizers are wrong by definition.
"""

from __future__ import annotations

import pytest

from agent_server.optimizers._units import (
    BOTTLES_PER_CASE,
    bpm_for_cases,
    cases_from_bpm,
)
from agent_server.tools.calculator import calculator

# The line's nameplate: 420 bottles/min, an 8-hour shift, and the plan that
# follows from them. Straight out of src/streaming_sim/producer.py.
IDEAL_BPM = 420.0
SHIFT_MINUTES = 480.0
SHIFT_PLANNED_CASES = 8400


def _invoke(tool, **kwargs):
    return tool.invoke(kwargs)


class TestTheConstantMatchesTheProducer:
    def test_a_case_is_twenty_four_bottles(self):
        assert BOTTLES_PER_CASE == 24

    def test_it_agrees_with_the_producer_that_makes_the_cases(self):
        """If these drift, the plan and the optimizers measure different things."""
        from src.streaming_sim.producer import BOTTLES_PER_CASE as PRODUCER_CONSTANT

        assert BOTTLES_PER_CASE == PRODUCER_CONSTANT

    def test_the_nameplate_line_makes_exactly_its_plan(self):
        """420 BPM for a full shift is 8,400 cases — the planned figure."""
        assert cases_from_bpm(IDEAL_BPM, SHIFT_MINUTES) == pytest.approx(
            SHIFT_PLANNED_CASES
        )

    def test_it_is_not_the_old_hourly_conversion(self):
        """The regression, stated as a number: /60 gave 3,360 for a full shift."""
        assert cases_from_bpm(IDEAL_BPM, SHIFT_MINUTES) != pytest.approx(3360)


class TestTheConversionIsSelfConsistent:
    @pytest.mark.parametrize("bpm", [0.0, 1.0, 120.0, 420.0, 450.0])
    @pytest.mark.parametrize("minutes", [0.0, 15.0, 240.0, 480.0])
    def test_cases_and_speed_invert_each_other(self, bpm, minutes):
        cases = cases_from_bpm(bpm, minutes)
        if minutes <= 0 or bpm <= 0:
            return
        assert bpm_for_cases(cases, minutes) == pytest.approx(bpm)

    def test_no_time_means_no_speed_is_enough(self):
        assert bpm_for_cases(100, 0) == float("inf")

    def test_no_time_makes_nothing(self):
        assert cases_from_bpm(420, 0) == 0

    def test_output_scales_with_both_inputs(self):
        base = cases_from_bpm(200, 100)
        assert cases_from_bpm(400, 100) == pytest.approx(base * 2)
        assert cases_from_bpm(200, 200) == pytest.approx(base * 2)


class TestTheCalculatorAgrees:
    def test_recovery_uses_the_shared_conversion(self):
        r = _invoke(
            calculator,
            operation="recovery_estimate",
            params={
                "remaining_cases": 1000,
                "speed_bpm": 420,
                "remaining_minutes": 100,
                "ramp_up_minutes": 0,
            },
        )
        assert r["projected_cases"] == int(cases_from_bpm(420, 100))

    def test_a_line_that_can_make_its_plan_is_told_so(self):
        """The bug's real cost: a feasible recovery reported as impossible.

        4,200 cases in the 240 minutes left, at the nominal 420 BPM, is exactly
        achievable. The /60 version projected 1,680 and said no.
        """
        r = _invoke(
            calculator,
            operation="recovery_estimate",
            params={
                "remaining_cases": 4200,
                "speed_bpm": 420,
                "remaining_minutes": 240,
                "ramp_up_minutes": 0,
            },
        )
        assert r["feasible"] is True
        assert r["completion_pct"] == pytest.approx(100.0)

    def test_cost_estimate_still_uses_an_hourly_rate(self):
        """Not every /60 was wrong: this one is given cases *per hour*."""
        r = _invoke(
            calculator,
            operation="cost_estimate",
            params={"downtime_minutes": 120, "line_rate_cases_per_hour": 1050},
        )
        assert r["lost_cases"] == 2100


class TestEveryOptimizerUsesIt:
    """Each of these reported ``projected_cases`` from a BPM and got it wrong."""

    def test_schedule_recovery_projects_physically(self):
        from agent_server.optimizers.schedule_recovery import ScheduleRecoveryOptimizer

        out = ScheduleRecoveryOptimizer().optimize(
            remaining_cases=4200,
            remaining_minutes=240,
            min_speed_bpm=300,
            max_speed_bpm=450,
            current_speed_bpm=420,
            allow_overtime=False,
        )
        # The old /60 made this infeasible and advised raising the line's
        # maximum speed; 4,200 cases in the 230 effective minutes needs
        # 4200*24/230 = 438 BPM, which is inside the 450 limit.
        assert out["feasible"] is True
        assert out["recommended_speed_bpm"] <= 450
        speed = out["recommended_speed_bpm"]
        minutes = out["effective_production_minutes"] + out["overtime_minutes"]
        assert out["projected_cases"] <= cases_from_bpm(speed, minutes) + 1
        assert out["projected_cases"] >= 4200

    def test_the_forecaster_predicts_a_full_shift_near_plan(self):
        from agent_server.optimizers.production_forecaster import ProductionForecaster

        out = ProductionForecaster().forecast(
            current_cases=0,
            remaining_minutes=SHIFT_MINUTES,
            current_oee=1.0,
            ideal_rate_bpm=IDEAL_BPM,
            oee_std=0.0,
        )
        # A perfect shift cannot beat the plan, and should land just under it
        # (micro-stop loss averages ~2.5%). The /60 version forecast ~3,280 for
        # a flawless shift — a 61% shortfall invented by a unit conversion.
        assert out["p50"] <= SHIFT_PLANNED_CASES + 1
        assert out["p50"] > SHIFT_PLANNED_CASES * 0.9


def test_no_optimizer_still_divides_bpm_by_sixty():
    """A grep-level guard: the mistake was uniform, so its absence should be too."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2]
    offenders = []
    for path in (root / "agent_server" / "optimizers").glob("*.py"):
        if path.name == "_units.py":
            continue  # its docstring names the old formula on purpose
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"(speed|bpm|rate)\w*\s*\*[^#\n]*/\s*60(\.0)?\b", line, re.I):
                offenders.append(f"{path.name}:{i}: {line.strip()}")
    assert not offenders, "bottles/min divided by 60 again:\n" + "\n".join(offenders)
