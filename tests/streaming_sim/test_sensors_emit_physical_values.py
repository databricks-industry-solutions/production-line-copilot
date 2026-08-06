"""A synthesized reading has to be physically possible.

``_process_value`` jittered around ``nominal_value`` and fell back to 0 when a
sensor had none. The noise band is 2 % of nominal, so a nominal of 0 took the
absolute fallback of ±1 and the sensor emitted ``uniform(-1, 1)``.

Exactly one sensor in the catalog hit that path: ``depal_cycle_time`` — a cycle
time, in seconds, going negative, and the sensor DEPALLETIZER is
primary-mapped to. Asked to diagnose a stop on that machine the downtime
analyst found no usable precursor and reported LOW confidence, which was an
accurate read of three million rows of noise.

Nothing failed. The pipeline ingested it, the quality gate passed it, and the
dashboards charted it.
"""
from __future__ import annotations

import pytest

from src.data_generation.config import SENSORS
from src.streaming_sim.cascade import MachineState
from src.streaming_sim.sensor_synthesizer import SensorSynthesizer, _is_rate

NON_COUNTER = ("process", "speed", "level")


def _state(machine_id: str, status: str = "running", speed_pct: float = 100.0) -> MachineState:
    # machine_id is the caller's bookkeeping; MachineState itself is anonymous.
    return MachineState(status=status, throughput=420.0, speed_pct=speed_pct, alarm_count=0)


class TestTheCatalogIsComplete:
    @pytest.mark.parametrize(
        "sensor", [s for s in SENSORS if s.sensor_type in NON_COUNTER], ids=lambda s: s.sensor_name
    )
    def test_every_non_counter_sensor_has_a_nominal(self, sensor):
        assert sensor.nominal_value is not None, (
            f"{sensor.sensor_name} synthesizes around 0 and emits noise, not a measurement"
        )

    @pytest.mark.parametrize(
        "sensor", [s for s in SENSORS if s.sensor_type in NON_COUNTER], ids=lambda s: s.sensor_name
    )
    def test_every_non_counter_sensor_has_a_threshold(self, sensor):
        # estimate_rul projects a trend against alarm_low/alarm_high. With
        # neither, the maintenance specialist's only tool cannot answer.
        assert sensor.alarm_low is not None or sensor.alarm_high is not None, (
            f"{sensor.sensor_name} gives estimate_rul no limit to project against"
        )

    def test_the_depalletizer_cycle_time_is_a_plausible_duration(self):
        s = next(x for x in SENSORS if x.sensor_name == "depal_cycle_time")
        assert s.nominal_value is not None and s.nominal_value > 0
        assert s.alarm_low is not None and s.alarm_low > 0, "a cycle time cannot be negative"


class TestSynthesizedReadings:
    @pytest.fixture
    def synth(self):
        return SensorSynthesizer(seed=7)

    @pytest.mark.parametrize("status", ["running", "degraded", "stopped"])
    def test_no_duration_or_rate_sensor_goes_negative(self, synth, status):
        for sensor in SENSORS:
            if sensor.sensor_type not in NON_COUNTER:
                continue
            ms = _state(sensor.machine_id, status, 40.0 if status == "degraded" else 100.0)
            for _ in range(50):
                value = synth._process_value(sensor, ms)
                assert value >= 0, f"{sensor.sensor_name} emitted {value} while {status}"

    def test_a_running_sensor_stays_near_its_nominal(self, synth):
        sensor = next(s for s in SENSORS if s.sensor_name == "depal_cycle_time")
        ms = _state("DEPALLETIZER")
        for _ in range(50):
            value = synth._process_value(sensor, ms)
            assert abs(value - sensor.nominal_value) <= sensor.nominal_value * 0.05

    def test_a_reading_sits_inside_its_own_alarm_band_when_healthy(self, synth):
        for sensor in SENSORS:
            if sensor.sensor_type not in ("process", "speed") or sensor.alarm_low is None:
                continue
            if sensor.alarm_high is None:
                continue
            ms = _state(sensor.machine_id)
            value = synth._process_value(sensor, ms)
            assert sensor.alarm_low <= value <= sensor.alarm_high, (
                f"{sensor.sensor_name} alarms at its own nominal — the band is wrong"
            )

    def test_a_missing_nominal_is_loud_rather_than_noisy(self, synth):
        from src.data_generation.config import SensorDef

        broken = SensorDef(
            machine_id="X", sensor_name="x_cycle_time", sensor_type="process", unit="sec",
            nominal_value=None,
        )
        with pytest.raises(ValueError, match="nominal_value"):
            synth._process_value(broken, _state("X"))


LEVELS = [s for s in SENSORS if s.sensor_type == "level"]
RATES = [s for s in SENSORS if s.sensor_type in ("counter", "quality") and _is_rate(s)]
TOTALS = [s for s in SENSORS if s.sensor_type in ("counter", "quality") and not _is_rate(s)]


class TestLevels:
    """A level that is nearly always in alarm is not reporting anything."""

    @pytest.fixture
    def synth(self):
        return SensorSynthesizer(seed=11)

    @pytest.mark.parametrize("sensor", LEVELS, ids=lambda s: s.sensor_name)
    def test_a_level_never_reads_below_empty(self, synth, sensor):
        # The floor was applied to the stored value and the jitter added after,
        # so shrink_film_remaining reported -0.09 % of a roll.
        ms = _state(sensor.machine_id)
        values = [synth._level_value(sensor, ms, 1.0) for _ in range(400)]
        assert min(values) >= 0.0, f"{sensor.sensor_name} reported {min(values)}"

    @pytest.mark.parametrize(
        "sensor", [s for s in LEVELS if s.alarm_low is not None], ids=lambda s: s.sensor_name
    )
    def test_a_level_spends_most_of_its_time_above_its_own_alarm(self, synth, sensor):
        """Refill used to trigger at a flat 20 %, whatever the alarm said.

        washer_caustic_level alarms at 60 and was refilled below 20, so it sat
        under its own threshold for most of a shift — 586 breaches in fifteen
        minutes while the machine reported `running`.
        """
        ms = _state(sensor.machine_id)
        values = [synth._level_value(sensor, ms, 1.0) for _ in range(600)]
        below = sum(1 for v in values if v < sensor.alarm_low)
        assert below / len(values) < 0.35, (
            f"{sensor.sensor_name} is under its low alarm {100 * below // len(values)}% "
            "of the time — the alarm carries no information"
        )

    @pytest.mark.parametrize(
        "sensor", [s for s in LEVELS if s.alarm_low is not None], ids=lambda s: s.sensor_name
    )
    def test_a_running_machine_is_never_out_of_material(self, synth, sensor):
        """0 % remaining on a machine that reports `running` is a contradiction.

        The refill is probabilistic to model operator reaction time, and the
        dice occasionally left label_roll_remaining at 0 while the LABELLER
        kept running. A specialist reading both cannot resolve that.
        """
        ms = _state(sensor.machine_id)
        values = [synth._level_value(sensor, ms, 1.0) for _ in range(2000)]
        assert min(values) > 0.0, f"{sensor.sensor_name} ran dry while running"

    @pytest.mark.parametrize(
        "sensor", [s for s in LEVELS if s.alarm_low is not None], ids=lambda s: s.sensor_name
    )
    def test_a_level_does_dip_into_alarm_sometimes(self, synth, sensor):
        # The opposite failure: a level that never alarms is equally uninformative.
        ms = _state(sensor.machine_id)
        values = [synth._level_value(sensor, ms, 1.0) for _ in range(600)]
        assert min(values) < sensor.alarm_low, f"{sensor.sensor_name} never dips into alarm"


class TestRatesAreNotAccumulated:
    """A per-minute unit is a rate, and a rate does not climb forever."""

    @pytest.fixture
    def synth(self):
        return SensorSynthesizer(seed=13)

    def test_the_rate_sensors_are_the_ones_declared_per_minute(self):
        assert {s.sensor_name for s in RATES} == {
            "insp_vision_nok_count", "label_vision_nok_count",
        }

    @pytest.mark.parametrize("sensor", RATES, ids=lambda s: s.sensor_name)
    def test_a_healthy_machine_stays_under_the_alarm(self, synth, sensor):
        """It used to cross the alarm minutes in and never come back down."""
        ms = _state(sensor.machine_id)
        for _ in range(200):
            value = synth._counter_value(sensor, ms, 1.0)
            assert value <= sensor.alarm_high, f"{sensor.sensor_name} alarmed while healthy"

    @pytest.mark.parametrize("sensor", RATES, ids=lambda s: s.sensor_name)
    def test_a_rate_does_not_climb_without_bound(self, synth, sensor):
        ms = _state(sensor.machine_id)
        early = [synth._counter_value(sensor, ms, 1.0) for _ in range(30)]
        for _ in range(500):
            synth._counter_value(sensor, ms, 1.0)
        late = [synth._counter_value(sensor, ms, 1.0) for _ in range(30)]
        assert max(late) <= max(early) * 3, "the rate is accumulating, not reporting"

    @pytest.mark.parametrize("sensor", RATES, ids=lambda s: s.sensor_name)
    def test_a_fault_pushes_it_over_the_alarm(self, synth, sensor):
        ms = _state(sensor.machine_id, "degraded", 45.0)
        values = [synth._counter_value(sensor, ms, 1.0) for _ in range(20)]
        assert min(values) >= sensor.alarm_high, "a defect spike has to read as one"

    @pytest.mark.parametrize("sensor", RATES, ids=lambda s: s.sensor_name)
    def test_a_stopped_machine_inspects_nothing(self, synth, sensor):
        assert synth._counter_value(sensor, _state(sensor.machine_id, "stopped"), 1.0) == 0.0

    @pytest.mark.parametrize("sensor", TOTALS[:6], ids=lambda s: s.sensor_name)
    def test_a_running_total_still_accumulates(self, synth, sensor):
        ms = _state(sensor.machine_id)
        values = [synth._counter_value(sensor, ms, 1.0) for _ in range(20)]
        assert values == sorted(values), f"{sensor.sensor_name} stopped being monotonic"
        assert values[-1] > values[0]
