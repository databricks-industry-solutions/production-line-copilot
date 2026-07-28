"""Unit tests for the optimizer engines — the OR/ML "brains" behind the
specialists. These import the real modules and exercise the deterministic
logic. Anything that queries a warehouse is monkeypatched; Monte Carlo paths
are seeded for reproducibility.
"""
from __future__ import annotations

import sys
import types

import pytest

# Stub langchain_core.tools only if langchain is unavailable (see note in
# test_optimizers.py). When it's installed we use the real package.
try:
    import langchain_core  # noqa: F401
    import langchain_core.tools  # noqa: F401
except Exception:  # pragma: no cover - minimal envs only
    _fake_lc = types.ModuleType("langchain_core")
    _fake_tools = types.ModuleType("langchain_core.tools")
    _fake_tools.tool = lambda f: f
    _fake_lc.tools = _fake_tools
    sys.modules.setdefault("langchain_core", _fake_lc)
    sys.modules.setdefault("langchain_core.tools", _fake_tools)

from agent_server.optimizers import constraint_loader as cl
from agent_server.optimizers import data_calibration as dc
from agent_server.optimizers import production_forecaster as pf_mod
from agent_server.optimizers.multi_shift_planner import MultiShiftPlanner
from agent_server.optimizers.production_forecaster import ProductionForecaster
from agent_server.optimizers.quality_causal_model import (
    QualityCausalModel,
    _bayesian_update,
)
from agent_server.optimizers.rul_estimator import RULEstimator, _linear_regression
from agent_server.optimizers.stochastic_schedule import (
    StochasticScheduleOptimizer,
    _generate_scenarios,
)

HAS_ORTOOLS = True
try:
    from ortools.linear_solver import pywraplp  # noqa: F401
except ImportError:
    HAS_ORTOOLS = False


@pytest.fixture(autouse=True)
def _reset_caches():
    """Optimizer caches are module-global; reset before every test so a
    monkeypatched query in one test can't bleed into another."""
    cl._cache = {}
    cl._cache_ts = 0.0
    dc._fc_cache = None
    dc._fc_cache_ts = 0.0
    dc._cpt_cache = None
    dc._cpt_cache_ts = 0.0
    yield
    cl._cache = {}
    cl._cache_ts = 0.0
    dc._fc_cache = None
    dc._fc_cache_ts = 0.0
    dc._cpt_cache = None
    dc._cpt_cache_ts = 0.0


# --------------------------------------------------------------------------- #
# constraint_loader
# --------------------------------------------------------------------------- #
class TestConstraintLoader:
    def _seed(self, mapping):
        import time
        cl._cache = dict(mapping)
        cl._cache_ts = time.monotonic()

    def test_get_float_from_cache(self):
        self._seed({"min_speed_bpm": "250"})
        assert cl.get_float("min_speed_bpm", 200.0) == 250.0

    def test_get_float_missing_key_uses_default(self):
        self._seed({"other": "1"})
        assert cl.get_float("nope", 199.0) == 199.0

    def test_get_float_bad_value_uses_default(self):
        self._seed({"x": "not-a-number"})
        assert cl.get_float("x", 5.0) == 5.0

    def test_get_bool_true_false_default(self):
        self._seed({"a": "true", "b": "no", "c": "maybe"})
        assert cl.get_bool("a", False) is True
        assert cl.get_bool("b", True) is False
        assert cl.get_bool("c", True) is True  # unrecognized -> default

    def test_refresh_cache_skips_error_rows(self, monkeypatch):
        from agent_server.tools import sql_query as sq
        rows = [
            {"constraint_key": "min_speed_bpm", "value": "210"},
            {"error": "boom"},  # must be ignored
        ]

        class _FakeSQL:
            def invoke(self, _q):
                return rows

        # ``sql_query`` is a frozen pydantic StructuredTool, so replace the
        # whole module-level name (imported fresh inside _refresh_cache).
        monkeypatch.setattr(sq, "sql_query", _FakeSQL())
        result = cl._refresh_cache()
        assert result == {"min_speed_bpm": "210"}


# --------------------------------------------------------------------------- #
# data_calibration
# --------------------------------------------------------------------------- #
class TestDataCalibration:
    def test_forecaster_defaults_when_no_data(self, monkeypatch):
        monkeypatch.setattr(dc, "_sql", lambda q: [])
        params = dc.calibrate_forecaster_params()
        assert params["ideal_rate_bpm"] == 420.0
        assert params["oee_std"] == 0.05
        assert params["historical_mttr_minutes"] == 15.0

    def test_forecaster_calibrates_from_rows(self, monkeypatch):
        def fake_sql(q):
            if "avg_rate" in q:
                return [{"avg_rate": 400.0}]
            if "oee_std" in q:
                return [{"oee_std": 0.08}]
            if "avg_mttr" in q:
                return [{"avg_mttr": 20.0}]
            return []
        monkeypatch.setattr(dc, "_sql", fake_sql)
        params = dc.calibrate_forecaster_params()
        assert params["ideal_rate_bpm"] == 400.0
        assert params["oee_std"] == 0.08
        assert params["historical_mttr_minutes"] == 20.0

    def test_forecaster_ignores_out_of_range(self, monkeypatch):
        def fake_sql(q):
            if "avg_rate" in q:
                return [{"avg_rate": 5000.0}]   # > 1000 -> rejected
            if "oee_std" in q:
                return [{"oee_std": 0.9}]        # > 0.5 -> rejected
            if "avg_mttr" in q:
                return [{"avg_mttr": 500.0}]     # > 120 -> rejected
            return []
        monkeypatch.setattr(dc, "_sql", fake_sql)
        params = dc.calibrate_forecaster_params()
        assert params["ideal_rate_bpm"] == 420.0
        assert params["oee_std"] == 0.05
        assert params["historical_mttr_minutes"] == 15.0

    def test_quality_cpt_empty_when_no_data(self, monkeypatch):
        monkeypatch.setattr(dc, "_sql", lambda q: [])
        assert dc.calibrate_quality_cpt() == {}

    def test_quality_cpt_from_rows(self, monkeypatch):
        rows = [{"machine_id": "FILLER", "root_cause": "temperature_deviation",
                 "total": 10, "fail_count": 7}]
        monkeypatch.setattr(dc, "_sql", lambda q: rows)
        cpt = dc.calibrate_quality_cpt()
        assert cpt["FILLER"]["temperature_deviation"] == 0.7
        assert cpt["FILLER"]["normal"] == 0.02


# --------------------------------------------------------------------------- #
# rul_estimator
# --------------------------------------------------------------------------- #
class TestLinearRegression:
    def test_perfect_line(self):
        slope, intercept, r2 = _linear_regression([0, 1, 2, 3], [0, 2, 4, 6])
        assert slope == pytest.approx(2.0)
        assert intercept == pytest.approx(0.0)
        assert r2 == pytest.approx(1.0)

    def test_single_point(self):
        slope, intercept, r2 = _linear_regression([1.0], [5.0])
        assert slope == 0.0
        assert intercept == 5.0


class TestRULEstimator:
    def setup_method(self):
        self.est = RULEstimator()

    def test_insufficient_data(self):
        r = self.est.estimate("M1", [{"timestamp_minute": 0, "value": 1.0}])
        assert r["confidence"] == "insufficient_data"
        assert r["rul_minutes"] is None

    def test_stable_or_improving(self):
        readings = [{"timestamp_minute": t, "value": v}
                    for t, v in [(0, 5.0), (50, 4.0), (100, 3.0)]]
        r = self.est.estimate("M1", readings, sensor_type="vibration")
        assert r["rul_status"] == "stable_or_improving"
        assert r["risk_level"] == "low"

    def test_critical_exceeded(self):
        readings = [{"timestamp_minute": t, "value": v}
                    for t, v in [(0, 4.0), (50, 5.5), (100, 7.0)]]
        r = self.est.estimate("M1", readings, sensor_type="vibration")
        assert r["rul_status"] == "critical_exceeded"
        assert r["risk_level"] == "critical"
        assert "CRITICAL" in r["recommendation"]

    def test_warning_exceeded(self):
        readings = [{"timestamp_minute": t, "value": v}
                    for t, v in [(0, 3.0), (50, 4.0), (100, 5.0)]]
        r = self.est.estimate("M1", readings, sensor_type="vibration")
        assert r["rul_status"] == "warning_exceeded"
        assert r["risk_level"] == "high"


# --------------------------------------------------------------------------- #
# production_forecaster
# --------------------------------------------------------------------------- #
class TestProductionForecaster:
    def setup_method(self):
        self.fc = ProductionForecaster()

    def test_zero_remaining_returns_current(self):
        r = self.fc.forecast(current_cases=500, remaining_minutes=0, current_oee=0.8)
        assert r["p10"] == r["p50"] == r["p90"] == 500

    def test_percentiles_ordered(self, monkeypatch):
        monkeypatch.setattr(pf_mod, "RANDOM_SEED", 42)  # deterministic
        r = self.fc.forecast(current_cases=100, remaining_minutes=120,
                             current_oee=0.75, ideal_rate_bpm=420.0)
        assert r["p10"] <= r["p50"] <= r["p90"]
        assert r["forecast_cases"] > 100  # produced more over the window
        assert r["simulations"] == pf_mod.N_SIMULATIONS


# --------------------------------------------------------------------------- #
# quality_causal_model
# --------------------------------------------------------------------------- #
class TestBayesianUpdate:
    def test_high_likelihood_raises_posterior(self):
        assert _bayesian_update(0.05, [0.9]) > 0.05

    def test_clamped_to_unit_interval(self):
        assert 0.0 <= _bayesian_update(0.5, [0.99, 0.99, 0.99]) <= 1.0


class TestQualityCausalModel:
    def setup_method(self):
        self.model = QualityCausalModel()

    def test_unknown_machine(self, monkeypatch):
        monkeypatch.setattr(dc, "calibrate_quality_cpt", lambda: {})
        r = self.model.analyze("NOT_A_MACHINE", ["something"])
        assert r["risk_level"] == "unknown"

    def test_normal_condition_low_risk(self, monkeypatch):
        monkeypatch.setattr(dc, "calibrate_quality_cpt", lambda: {})
        r = self.model.analyze("FILLER", ["normal"])
        assert r["risk_level"] == "low"
        assert r["quality_risk_score"] < 0.05
        assert r["cpt_source"] == "expert_prior"

    def test_multiple_conditions_raise_risk(self, monkeypatch):
        monkeypatch.setattr(dc, "calibrate_quality_cpt", lambda: {})
        r = self.model.analyze(
            "FILLER",
            ["fill_level_out_of_spec", "co2_low"],
            sensor_anomaly_count=3,
        )
        assert r["quality_risk_score"] > 0.05
        assert r["risk_level"] in ("moderate", "high", "critical")
        # 2 conditions + 1 anomaly factor, ranked by probability desc
        assert len(r["contributing_factors"]) == 3
        probs = [f["probability"] for f in r["contributing_factors"]]
        assert probs == sorted(probs, reverse=True)


# --------------------------------------------------------------------------- #
# multi_shift_planner
# --------------------------------------------------------------------------- #
class TestMultiShiftPlanner:
    def setup_method(self):
        self.planner = MultiShiftPlanner()

    def test_empty(self):
        r = self.planner.plan([])
        assert r["feasible"] is False

    def test_sequential_fallback(self):
        shifts = [
            {"shift_id": "s1", "planned_cases": 100, "remaining_minutes": 240,
             "current_speed_bpm": 380, "min_speed_bpm": 200, "max_speed_bpm": 450,
             "allow_overtime": False},
            {"shift_id": "s2", "planned_cases": 120, "remaining_minutes": 240,
             "current_speed_bpm": 380, "min_speed_bpm": 200, "max_speed_bpm": 450,
             "allow_overtime": False},
        ]
        r = self.planner._sequential_fallback(shifts)
        assert r["method"] == "sequential_fallback"
        assert len(r["shifts"]) == 2
        assert "total_projected_cases" in r

    def test_plan_returns_feasible(self):
        # plan() returns a usable plan whether OR-Tools is present or not: the
        # bilinear solver path is guarded and falls back to the sequential
        # planner on error.
        shifts = [
            {"shift_id": "s1", "planned_cases": 500, "remaining_minutes": 480,
             "current_speed_bpm": 380, "min_speed_bpm": 200, "max_speed_bpm": 450,
             "allow_overtime": True, "max_overtime_min": 60},
            {"shift_id": "s2", "planned_cases": 500, "remaining_minutes": 480,
             "current_speed_bpm": 380, "min_speed_bpm": 200, "max_speed_bpm": 450,
             "allow_overtime": True, "max_overtime_min": 60},
        ]
        r = self.planner.plan(shifts)
        assert r["feasible"] is True
        assert len(r["shifts"]) == 2


# --------------------------------------------------------------------------- #
# stochastic_schedule
# --------------------------------------------------------------------------- #
class TestStochasticSchedule:
    def setup_method(self):
        self.opt = StochasticScheduleOptimizer()

    def test_generate_scenarios_shape(self):
        scenarios = _generate_scenarios(480, 0.75, 0.05, 0, 15.0, n=20)
        assert len(scenarios) == 20
        assert all({"effective_minutes", "oee", "micro_stop_factor"} <= s.keys()
                   for s in scenarios)

    def test_trivial_zero_cases(self):
        r = self.opt.optimize(
            remaining_cases=0, remaining_minutes=120, min_speed_bpm=200,
            max_speed_bpm=450, current_speed_bpm=380, current_oee=0.8,
        )
        assert r["feasible"] is True
        assert r["method"] == "stochastic_trivial"

    def test_fallback_grid_search(self):
        scenarios = _generate_scenarios(470, 0.75, 0.05, 0, 15.0, n=20)
        r = self.opt._fallback(
            remaining_cases=800, effective_base=470, min_speed=200, max_speed=450,
            current_speed=380, allow_ot=False, max_ot=0, scenarios=scenarios,
        )
        assert r["feasible"] is True
        assert r["method"] == "stochastic_grid_search"
        assert "p50_cases" in r

    @pytest.mark.skipif(not HAS_ORTOOLS, reason="ortools not installed")
    def test_solver_optimize_feasible(self):
        r = self.opt.optimize(
            remaining_cases=500, remaining_minutes=120, min_speed_bpm=200,
            max_speed_bpm=450, current_speed_bpm=380, current_oee=0.8,
            allow_overtime=True, max_overtime_minutes=60,
        )
        assert r["feasible"] is True
        assert "recommended_speed_bpm" in r
