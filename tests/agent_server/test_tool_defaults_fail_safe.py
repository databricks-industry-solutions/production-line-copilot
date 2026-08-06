"""Two tools whose numeric defaults answered a question nobody asked.

``depletion_estimate`` defaulted a missing horizon to 0 and then compared
against it, so any positive stock came back "will not deplete" — a false
negative on running out of material, on a tool bound by almost every
specialist and called by an LLM that will treat an optional-looking argument
as optional.

``_iqr_anomalies`` picked quartiles by integer division, which never lands
between samples and skews the fences that decide what gets flagged.
"""

from __future__ import annotations

import statistics

import pytest

from agent_server.tools.anomaly_detector import _iqr_anomalies
from agent_server.tools.calculator import calculator


def _depletion(**params):
    return calculator.invoke({"operation": "depletion_estimate", "params": params})


class TestAMissingHorizonIsNotAnAnswer:
    def test_it_does_not_claim_safety_it_cannot_know(self):
        """Two hours from empty, and the old code said will_deplete False."""
        out = _depletion(current_qty=100, consumption_rate_per_hour=50)
        assert out["will_deplete"] is None
        assert out["hours_until_empty"] == 2.0

    def test_it_says_why(self):
        assert "remaining_hours" in _depletion(current_qty=100, consumption_rate_per_hour=50)["note"]

    def test_a_real_horizon_still_answers(self):
        assert _depletion(current_qty=100, consumption_rate_per_hour=50, remaining_hours=8)["will_deplete"] is True

    def test_enough_stock_to_finish_the_shift(self):
        assert _depletion(current_qty=1000, consumption_rate_per_hour=50, remaining_hours=8)["will_deplete"] is False

    def test_an_explicit_zero_horizon_is_no_horizon(self):
        """Zero hours left is not a window in which anything can deplete."""
        assert _depletion(current_qty=100, consumption_rate_per_hour=50, remaining_hours=0)["will_deplete"] is None

    def test_an_empty_string_horizon_does_not_crash(self):
        assert _depletion(current_qty=100, consumption_rate_per_hour=50, remaining_hours="")["will_deplete"] is None

    def test_nothing_being_consumed_never_depletes(self):
        out = _depletion(current_qty=100, consumption_rate_per_hour=0, remaining_hours=8)
        assert out["will_deplete"] is False
        assert out["hours_until_empty"] == float("inf")

    def test_junk_still_reports_an_error(self):
        assert "error" in _depletion(current_qty="lots", consumption_rate_per_hour=50, remaining_hours=8)


class TestQuartilesLandBetweenSamples:
    def _ts(self, n):
        return [f"2026-08-06T12:{i:02d}:00" for i in range(n)]

    def test_it_matches_the_standard_definition(self):
        values = [float(v) for v in range(1, 21)] + [1000.0]
        q1, _m, q3 = statistics.quantiles(values, n=4, method="inclusive")
        # Fences derived from those quartiles decide what is flagged.
        found = _iqr_anomalies(values, self._ts(21), "vibration")
        assert found and found[0]["value"] == 1000.0
        assert found[0]["upper_bound"] == pytest.approx(round(q3 + 1.5 * (q3 - q1), 2))

    def test_the_smallest_admissible_sample_is_not_the_worst_case(self):
        """n = 10 is where integer division was furthest from the truth, and
        it is exactly where the guard above lets data through."""
        values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        q1, _m, q3 = statistics.quantiles(values, n=4, method="inclusive")
        assert q1 == pytest.approx(3.25) and q3 == pytest.approx(7.75)
        # Nearest-rank would have taken sorted[2] = 3 and sorted[7] = 8.
        assert (q1, q3) != (3.0, 8.0)

    def test_a_clean_run_flags_nothing(self):
        values = [10.0 + (i % 3) * 0.1 for i in range(30)]
        assert _iqr_anomalies(values, self._ts(30), "temp") == []

    def test_an_outlier_at_either_end_is_caught(self):
        values = [10.0] * 15 + [10.5] * 15
        low = _iqr_anomalies([-500.0] + values, self._ts(31), "temp")
        high = _iqr_anomalies(values + [500.0], self._ts(31), "temp")
        assert any(a["value"] == -500.0 for a in low)
        assert any(a["value"] == 500.0 for a in high)

    def test_too_few_readings_is_still_declined(self):
        assert _iqr_anomalies([1.0] * 9, self._ts(9), "temp") == []

    def test_a_flat_signal_has_no_spread_to_judge_by(self):
        assert _iqr_anomalies([7.0] * 20, self._ts(20), "temp") == []

    def test_the_reported_timestamp_belongs_to_the_reading(self):
        # Needs real spread: a flat signal has an IQR of zero and is declined.
        values = [10.0 + (i % 4) * 0.5 for i in range(20)] + [999.0]
        ts = self._ts(21)
        found = _iqr_anomalies(values, ts, "temp")
        assert found and found[0]["timestamp"] == ts[20]
