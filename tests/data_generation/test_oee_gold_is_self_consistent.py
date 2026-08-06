"""The seeded OEE gold table has to mean what its column names say.

This table is the historical half of the story: seven finished shifts that the
dashboard charts and that the assistant reads when a question reaches back past
the live shift. Its numbers are designed rather than measured, which is exactly
why they need pinning — a designed number that quietly contradicts its own
components is indistinguishable from a computed one that is wrong, and the
recurring complaint in this project has been the dashboard and the assistant
disagreeing about OEE.

Two things are checked. First, that each row is internally coherent: the OEE is
the product of its own three factors, the case count follows from the OEE, and
the reject count follows from the quality. Second, that the definition of OEE
here is the same definition the live accumulator uses, so history and the
current shift are on one scale.
"""

from __future__ import annotations

import pytest

from src.data_generation.config import SCENARIOS, SKUS
from src.data_generation.oee_calculator import generate_oee_gold
from src.streaming_sim.line_kpi_derived import compute_tier1_line_kpi_row


@pytest.fixture(scope="module")
def gold():
    return generate_oee_gold()


class TestTheShapeOfTheTable:
    def test_there_is_one_row_per_scenario(self, gold):
        assert len(gold) == len(SCENARIOS)
        assert list(gold["shift_id"]) == [s.shift_id for s in SCENARIOS]

    def test_every_shift_is_represented_once(self, gold):
        assert gold["shift_id"].is_unique

    def test_every_sku_is_one_the_catalogue_knows(self, gold):
        known = {s.sku_id for s in SKUS}
        assert set(gold["sku_id"]) <= known

    def test_generation_is_deterministic(self):
        # The noise is seeded. Two runs must agree, or the gold table shifts
        # under the dashboard every time the data is regenerated.
        first = generate_oee_gold()
        second = generate_oee_gold()
        assert first.equals(second)


class TestEachRowAgreesWithItself:
    def test_oee_is_the_product_of_its_three_factors(self, gold):
        for row in gold.to_dict("records"):
            expected = row["availability"] * row["performance"] * row["quality"] / 10_000.0
            assert row["oee"] == pytest.approx(expected, abs=0.01), (
                f"{row['shift_id']}: OEE {row['oee']} is not A×P×Q "
                f"({row['availability']}×{row['performance']}×{row['quality']})"
            )

    def test_cases_produced_follow_from_the_oee(self, gold):
        # total_cases is planned_cases scaled by OEE, give or take the ±2%
        # rounding noise the generator adds on purpose.
        for row in gold.to_dict("records"):
            implied = row["planned_cases"] * row["oee"] / 100.0
            assert row["total_cases"] == pytest.approx(implied, rel=0.025), (
                f"{row['shift_id']}: {row['total_cases']} cases against an implied {implied:.0f}"
            )

    def test_rejects_follow_from_the_quality_figure(self, gold):
        # quality = good / (good + reject), so the reject count is recoverable
        # from the two numbers sitting next to it in the same row.
        for row in gold.to_dict("records"):
            good = row["total_cases"]
            implied_quality = 100.0 * good / (good + row["reject_count"])
            assert implied_quality == pytest.approx(row["quality"], abs=0.05), (
                f"{row['shift_id']}: {row['reject_count']} rejects against {good} good "
                f"implies quality {implied_quality:.2f}, row says {row['quality']}"
            )

    def test_nobody_produced_more_than_they_planned(self, gold):
        assert (gold["total_cases"] <= gold["planned_cases"]).all()

    def test_the_percentages_are_percentages(self, gold):
        for column in ("availability", "performance", "quality", "oee"):
            assert gold[column].between(0, 100).all(), f"{column} left the 0–100 range"

    def test_the_minute_counts_are_not_negative(self, gold):
        for column in ("downtime_minutes", "speed_loss_minutes", "changeover_minutes"):
            assert (gold[column] >= 0).all(), f"{column} went negative"

    def test_downtime_fits_inside_a_shift(self, gold):
        # Eight hours. Downtime longer than the shift would make availability
        # unreconstructable from the row.
        assert (gold["downtime_minutes"] <= 480).all()


class TestHistoryAndTheLiveShiftUseOneDefinition:
    """The seeded rows and the live accumulator must not drift apart.

    They compute from different inputs — one from designed factors, one from
    machine counts — but if they disagreed about what OEE *is*, the dashboard's
    history and the assistant's current-shift answer would sit on two scales and
    every comparison across them would be wrong.
    """

    def test_the_live_path_multiplies_the_same_way_the_gold_table_does(self):
        live = compute_tier1_line_kpi_row(
            shift_id="shift_1",
            ts="2026-01-01T00:00:00+00:00",
            machines_running=18,
            machines_stopped=1,
            machines_degraded=0,
            machines_starved=0,
            total_throughput=300.0,
            avg_speed_pct=90.0,
        )
        expected = (
            live["availability_to_now"] * live["performance_to_now"] * live["quality_to_now"] / 10_000.0
        )
        assert live["oee_to_now"] == pytest.approx(expected, abs=0.1)

    def test_both_paths_report_percentages_not_fractions(self, gold):
        live = compute_tier1_line_kpi_row(
            shift_id="shift_1",
            ts="2026-01-01T00:00:00+00:00",
            machines_running=19,
            machines_stopped=0,
            machines_degraded=0,
            machines_starved=0,
            total_throughput=300.0,
            avg_speed_pct=95.0,
        )
        # A healthy line reads as tens-of-percent in both, not 0.7-ish. Mixing
        # the two scales is the failure that makes a good shift look catastrophic.
        assert live["oee_to_now"] > 1.0
        assert gold["oee"].max() > 1.0
