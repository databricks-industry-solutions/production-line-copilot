"""The payload a prompt tells the model to build must be the payload the tool reads.

Every bug pinned here was invisible in the way that matters: nothing raised
where anyone could see it, and the specialist wrote a confident answer over the
result.

  * `estimate_rul` read `r["timestamp_minute"]` as a bare subscript while the
    maintenance_predictor prompt told the model to send `{"ts": ...}`. Every
    correctly-formed call raised KeyError, on the only tool that specialist has
    which no other specialist does.
  * An unrecognised sensor fell back to thresholds of zero, and any positive
    reading is above zero, so the tool answered CRITICAL — "immediate
    maintenance required" — for a healthy machine. The prompt makes that the
    normal case: it tells the model to pick a real channel from
    `machine_sensors`, whose names are machine-specific.
  * The Monte Carlo simulators clamp OEE with `min(1.0, ...)`, but everything
    the model can see carries OEE as a 0-100 percentage. A model that followed
    its instructions had all 1000 simulations pinned to a perfect shift.
  * `shift_briefing` asked for `current_qty` and quality checks with `NOK`.
    Neither exists; both silently matched nothing, so the briefing reported no
    stock risk and no quality holds on a shift that had them.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("langchain_core")

REPO = Path(__file__).resolve().parents[2]


class _StubTool:
    """Stands in for the `sql_query` StructuredTool, which pydantic seals."""

    def __init__(self, rows):
        self._rows = rows

    def invoke(self, *_args, **_kwargs):
        return self._rows


class TestEstimateRulAcceptsWhatThePromptSends:
    def _readings(self, key: str) -> list[dict]:
        return [{key: float(i), "value": 3.0 + i * 0.5} for i in range(4)]

    def test_the_prompt_example_key_is_one_the_tool_reads(self):
        prompt = (REPO / "agent_server" / "prompts" / "maintenance_predictor.py").read_text()
        example = re.search(r'Pass as: \[\{\{"(\w+)":', prompt)
        assert example, "the estimate_rul worked example disappeared from the prompt"
        source = (REPO / "agent_server" / "optimizers" / "rul_estimator.py").read_text()
        assert f'"{example.group(1)}"' in source, (
            f"the prompt tells the model to send {example.group(1)!r}, which the estimator never reads"
        )

    @pytest.mark.parametrize("key", ["timestamp_minute", "ts"])
    def test_both_spellings_of_the_time_key_work(self, key):
        from agent_server.optimizers.rul_estimator import RULEstimator

        out = RULEstimator().estimate("FILLER", self._readings(key), sensor_type="vibration")
        assert out.get("confidence") not in {"bad_input", "unknown_sensor_type"}
        assert out.get("rul_status"), out

    def test_a_reading_with_no_usable_time_is_refused_not_crashed(self):
        from agent_server.optimizers.rul_estimator import RULEstimator

        out = RULEstimator().estimate("FILLER", [{"value": 1.0}] * 4, sensor_type="vibration")
        assert out["confidence"] == "bad_input"

    def test_an_unknown_sensor_does_not_come_back_critical(self):
        """The prompt sends machine-specific sensor names by design."""
        from agent_server.optimizers.rul_estimator import RULEstimator

        out = RULEstimator().estimate(
            "WASHER", self._readings("timestamp_minute"), sensor_type="washer_caustic_temp"
        )
        assert out.get("risk_level") != "critical", (
            "an unknown sensor fell back to a zero threshold, so any positive "
            "reading reads as past critical"
        )
        assert out["confidence"] == "unknown_sensor_type"

    def test_thresholds_passed_by_the_caller_make_an_unknown_sensor_work(self):
        from agent_server.optimizers.rul_estimator import RULEstimator

        out = RULEstimator().estimate(
            "WASHER",
            self._readings("timestamp_minute"),
            sensor_type="washer_caustic_temp",
            custom_thresholds={"warning": 80.0, "critical": 95.0},
        )
        assert out.get("confidence") != "unknown_sensor_type"
        assert out.get("rul_status") == "healthy", out


class TestOeeIsUnderstoodOnEitherScale:
    """0-100 is what the context and the prompts carry; 0-1 is what the math needs."""

    def test_the_forecast_is_the_same_either_way(self):
        from agent_server.optimizers.production_forecaster import ProductionForecaster

        f = ProductionForecaster()
        pct = f.forecast(current_cases=1000, remaining_minutes=240, current_oee=75.0)
        frac = f.forecast(current_cases=1000, remaining_minutes=240, current_oee=0.75)
        assert abs(pct["p50"] - frac["p50"]) / frac["p50"] < 0.05

    def test_a_percentage_does_not_forecast_a_perfect_shift(self):
        from agent_server.optimizers.production_forecaster import ProductionForecaster

        pct = ProductionForecaster().forecast(
            current_cases=0, remaining_minutes=60, current_oee=75.0, ideal_rate_bpm=100.0
        )
        perfect = 100.0 * 60  # rate x minutes at OEE 1.0
        assert pct["p50"] < perfect * 0.9, "the percentage input was clamped to a perfect OEE"

    def test_the_calculator_agrees_with_itself(self):
        from agent_server.tools.calculator import calculator

        pct = calculator.invoke(
            {"operation": "oee", "params": {"availability": 85, "performance": 90, "quality": 98}}
        )
        frac = calculator.invoke(
            {"operation": "oee", "params": {"availability": 0.85, "performance": 0.9, "quality": 0.98}}
        )
        assert pct == frac
        assert 0 <= pct["oee_pct"] <= 100


class TestAToolFailureIsNotDressedAsData:
    def test_a_failed_similar_case_search_is_not_returned_as_a_case(self, monkeypatch):
        from agent_server.tools import similar_case_retriever as scr
        from agent_server.tools import sql_query as sql_query_module

        monkeypatch.setattr(
            sql_query_module, "sql_query",
            _StubTool([{"error": "[TABLE_OR_VIEW_NOT_FOUND] gold_history_event_log"}]),
        )
        out = scr._keyword_search("bearing seizure", None, None, 3)
        assert len(out) == 1 and out[0].get("error"), out
        assert "corrective_action" not in out[0]

    def test_plan_and_constraints_says_so_when_both_queries_failed(self, monkeypatch):
        from agent_server.tools import plan_constraints_loader as pcl

        monkeypatch.setattr(pcl, "sql_query", _StubTool([{"error": "warehouse is down"}]))
        out = pcl.load_plan_and_constraints.invoke({"shift_id": "shift_1"})
        assert out.get("error"), "a warehouse outage was reported as 'no plan on file'"

    def test_an_empty_shift_is_still_not_an_error(self, monkeypatch):
        from agent_server.tools import plan_constraints_loader as pcl

        monkeypatch.setattr(pcl, "sql_query", _StubTool([]))
        out = pcl.load_plan_and_constraints.invoke({"shift_id": "shift_1"})
        assert "error" not in out
        assert out == {"plan": {}, "constraints": []}


class TestThePromptsNameRealColumnsAndValues:
    BRIEFING = (REPO / "agent_server" / "prompts" / "shift_briefing.py").read_text()

    @pytest.mark.parametrize("ghost", ["current_qty", "item_id", "NOK"])
    def test_the_briefing_does_not_name_a_field_that_does_not_exist(self, ghost):
        # NOK survives only in the sentence that tells the model it is not a value.
        occurrences = [
            line for line in self.BRIEFING.splitlines()
            if ghost in line and "there is no OK/NOK" not in line
        ]
        assert not occurrences, occurrences

    def test_the_briefing_names_the_real_inventory_column(self):
        assert "on_hand_qty" in self.BRIEFING
        assert "material_id" in self.BRIEFING

    def test_the_citation_example_is_not_a_dead_table(self):
        shared = (REPO / "agent_server" / "prompts" / "_shared.py").read_text()
        rule = next(line for line in shared.splitlines() if "Reference the data source" in line)
        assert "machine_state data" not in rule, (
            "the example source name is the empty `machine_state`, shown to all "
            "seven specialists 14 lines before the real name"
        )


class TestCalibrationQueriesMatchTheDataThatExists:
    def test_the_mttr_query_uses_event_types_the_producer_writes(self):
        calib = (REPO / "agent_server" / "optimizers" / "data_calibration.py").read_text()
        backfill = (REPO / "scripts" / "backfill_quality_and_events.py").read_text()
        wanted = set(re.findall(r"'(\w+_\w+)'", calib.split("avg_mttr")[1].split(")")[0]))
        wanted -= {"duration_minutes", "gold_history_event_log", "event_type"}
        assert wanted, "could not find the event types in the MTTR query"
        missing = [w for w in wanted if f'"{w}"' not in backfill]
        assert not missing, f"MTTR averages over event types nothing writes: {missing}"
