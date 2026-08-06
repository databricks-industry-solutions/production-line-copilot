"""Per-tick sensor synthesizer.

Takes the cascade ``LineState`` (the producer's physical truth) and the static
``SENSORS`` catalog from ``data_generation.config`` and emits one telemetry row
per (machine_id, sensor_name) per tick. Adds realistic jitter around
``nominal_value``, scales counters by throughput, applies degradation when a
machine is degraded/stopped, and keeps cumulative counters monotonic.

Used by:
  * ``src/streaming_sim/producer.py`` — to emit a richer telemetry stream
    (one row per defined sensor per tick instead of just 6 hand-picked rows).

Designed so the existing ``machine_state`` / ``line_kpi`` rows remain the
authoritative producer-side outputs; the streaming quality-gate job can
re-derive those metrics from this telemetry as a shadow path before any cutover.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from src.data_generation.config import SENSORS, SensorDef
from src.streaming_sim.cascade import MachineState


def _is_rate(sensor: SensorDef) -> bool:
    """Whether the sensor's unit describes a rate rather than a running total.

    Consulted only for counter/quality sensors, which are the ones that
    accumulate. Speed sensors read 'm/min' and match too, but they take the
    process path and are never accumulated in the first place.

    The unit is the only place the distinction is recorded — 'EA/min' against
    plain 'EA' — and nothing read it, so rates were accumulated.
    """
    return "/min" in sensor.unit or "/h" in sensor.unit


@dataclass
class SensorReading:
    machine_id: str
    sensor_name: str
    sensor_type: str
    unit: str
    value: float


class SensorSynthesizer:
    """Stateful per-machine cumulative-counter synthesizer.

    Stateless ``process`` / ``level`` / ``speed`` / ``quality`` sensors are
    derived directly from machine state + nominal value. ``counter`` sensors
    accumulate monotonically based on throughput, persisted across ticks.
    """

    def __init__(self, seed: int = 42) -> None:
        self._rng = random.Random(seed)
        self._sensors_by_machine: dict[str, list[SensorDef]] = {}
        for s in SENSORS:
            self._sensors_by_machine.setdefault(s.machine_id, []).append(s)
        self._counters: dict[tuple[str, str], float] = {}

    @property
    def sensor_count(self) -> int:
        return len(SENSORS)

    def reset(self) -> None:
        self._counters.clear()

    @staticmethod
    def _healthy_noise(sensor: SensorDef, nom: float) -> float:
        """Jitter for a healthy reading: 2 % of nominal, but inside the alarm band.

        A flat 2 % ignored the sensor's own spec. ``filler_fill_volume_avg`` is
        330 ml with limits at 325/335 — a 1.5 % band — so a perfectly healthy
        filler breached its fill spec at random, roughly a quarter of every
        tick. Anything reading that column sees real out-of-spec readings with
        no cause behind them.

        Staying at 40 % of the distance to the nearer limit keeps a healthy
        machine comfortably in spec and leaves the rest of the band to mean what
        it says: something is actually wrong.
        """
        noise = abs(nom) * 0.02
        margins = [
            abs(nom - limit)
            for limit in (sensor.alarm_low, sensor.alarm_high)
            if limit is not None
        ]
        if margins:
            noise = min(noise, min(margins) * 0.4)
        return noise

    def _process_value(self, sensor: SensorDef, ms: MachineState) -> float:  # noqa: D401

        """Process / level / speed sensors: jitter around nominal, degrade on fault.

        A sensor with no nominal used to fall back to 0, which made the noise
        band ±1 absolute and the reading ``uniform(-1, 1)``. That shipped for
        ``depal_cycle_time`` — a cycle time in seconds, going negative, on the
        one sensor DEPALLETIZER is primary-mapped to. Asked to diagnose a stop
        on that machine, the analyst found no usable precursor and reported low
        confidence, which was the correct read of the data it was given.

        The catalog now gives every non-counter sensor a nominal, so this is a
        guard rather than a live path: without one there is nothing meaningful
        to synthesize, and a plausible-looking number is worse than none.
        """
        if sensor.nominal_value is None:
            raise ValueError(
                f"{sensor.machine_id}.{sensor.sensor_name} ({sensor.sensor_type}) has no "
                "nominal_value; add one in data_generation.config.SENSORS. Synthesizing "
                "around 0 emits noise that reads as a real measurement."
            )
        nom = float(sensor.nominal_value)
        noise = self._healthy_noise(sensor, nom)
        val = nom + self._rng.uniform(-noise, noise)

        if ms.status == "stopped":
            # Speed/level/process collapse to ~0 for speed/throughput-tied sensors.
            if sensor.sensor_type in ("speed",):
                return 0.0
            # Level falls slowly, process drifts toward room temp / ambient.
            if sensor.sensor_type == "level":
                return max(0.0, val * 0.95)
            return round(val, 3)
        if ms.status == "degraded":
            # Process / speed sensors drop to throughput's share of nominal.
            if sensor.sensor_type in ("speed", "process"):
                ratio = max(0.1, ms.speed_pct / 100.0)
                val = nom * ratio + self._rng.uniform(-noise, noise)
        return round(val, 3)

    def _level_value(self, sensor: SensorDef, ms: MachineState, tick_seconds: float) -> float:
        """Level sensors: gradually deplete based on throughput, refill possible.

        The refill trigger follows the sensor's own low alarm rather than a
        flat 20 %. It used to be hardcoded, which left every level whose alarm
        sits higher in permanent alarm: washer_caustic_level alarms at 60 and
        was only refilled below 20, so it spent most of a shift under its own
        threshold — 586 breaches in fifteen minutes on a machine reporting
        `running`. An alarm that is almost always on carries no information,
        and anything downstream asking "is this in alarm?" gets a constant yes.
        """
        key = (sensor.machine_id, sensor.sensor_name)  # SensorDef carries its machine id
        full = float(sensor.nominal_value or 80.0)
        cur = self._counters.get(key)
        if cur is None:
            cur = full
        # Deplete when running, hold when stopped, refill randomly.
        if ms.status in ("running", "degraded"):
            depletion = (ms.throughput / 60.0) * tick_seconds * 0.05  # ~5 %/min at full throughput
            cur = max(0.0, cur - depletion)
        # Dip under the alarm, then get refilled — the alarm is the prompt for
        # the refill, so it should clear soon after it trips. The probability
        # stands in for how long the operator takes to react.
        refill_at = float(sensor.alarm_low) if sensor.alarm_low is not None else 20.0
        if cur < refill_at and self._rng.random() < 0.05:
            cur = full  # operator refilled
        # Reacting late is realistic; running on an empty roll is not. Without
        # this floor the dice occasionally left label_roll_remaining at 0 %
        # while the LABELLER still reported `running`, and a specialist reading
        # both has been handed a contradiction it cannot resolve.
        if ms.status in ("running", "degraded") and cur < refill_at * 0.25:
            cur = full
        self._counters[key] = cur
        # Clamped because the jitter is applied after the floor, not before: at
        # cur=0 an unclamped ±0.5 reported a tank holding less than nothing.
        return round(max(0.0, cur + self._rng.uniform(-0.5, 0.5)), 2)

    def _counter_value(self, sensor: SensorDef, ms: MachineState, tick_seconds: float) -> float:
        """Counter: monotonic accumulation tied to throughput.

        A sensor whose unit is per-minute is a rate and is not accumulated —
        see ``_defect_rate``. ``insp_vision_nok_count`` and
        ``label_vision_nok_count`` are declared EA/min with a nominal of 2 and
        an alarm at 10, and were accumulated anyway: they crossed the alarm a
        few minutes into every shift, climbed without bound, and dropped back
        to zero whenever the producer restarted, which reads as a defect rate
        that suddenly healed. Both are primary-mapped, so that was the number
        the inspector and labeller were judged by.
        """
        if _is_rate(sensor):
            return self._defect_rate(sensor, ms)
        key = (sensor.machine_id, sensor.sensor_name)
        cur = self._counters.get(key, 0.0)
        if ms.status in ("running", "degraded"):
            increment = (ms.throughput / 60.0) * tick_seconds
            # Quality counters accumulate slower (1-3 % of throughput).
            if sensor.sensor_type == "quality":
                increment = increment * self._rng.uniform(0.0, 0.03)
                # During fault, defects spike.
                if ms.status == "degraded":
                    increment += self._rng.uniform(0.5, 2.0)
            cur += increment
        self._counters[key] = cur
        return round(cur, 2)

    def _defect_rate(self, sensor: SensorDef, ms: MachineState) -> float:
        """A per-minute defect rate: jitter around nominal, spike on a fault.

        Bounded by the sensor's own alarm so a healthy machine reads normal and
        a degraded one reads as a genuine excursion, rather than the threshold
        being crossed once and never recovered.
        """
        nominal = float(sensor.nominal_value or 0.0)
        if ms.status == "stopped":
            return 0.0  # nothing is being inspected
        if ms.status == "degraded":
            ceiling = float(sensor.alarm_high) if sensor.alarm_high is not None else nominal * 5
            return round(self._rng.uniform(ceiling, ceiling * 1.6), 2)
        return round(max(0.0, self._rng.uniform(nominal * 0.4, nominal * 1.6)), 2)

    def emit_for_machine(
        self, machine_id: str, ms: MachineState, tick_seconds: float = 1.0
    ) -> list[SensorReading]:
        """Return one ``SensorReading`` per defined sensor on this machine."""
        defs = self._sensors_by_machine.get(machine_id, [])
        out: list[SensorReading] = []
        for sd in defs:
            if sd.sensor_type == "level":
                value = self._level_value(sd, ms, tick_seconds)
            elif sd.sensor_type in ("counter", "quality"):
                value = self._counter_value(sd, ms, tick_seconds)
            else:  # process | speed | other
                value = self._process_value(sd, ms)
            out.append(
                SensorReading(
                    machine_id=machine_id,
                    sensor_name=sd.sensor_name,
                    sensor_type=sd.sensor_type,
                    unit=sd.unit,
                    value=float(value),
                )
            )
        return out

    def emit_all(
        self, machines: dict[str, MachineState], tick_seconds: float = 1.0
    ) -> list[SensorReading]:
        out: list[SensorReading] = []
        for mid, ms in machines.items():
            out.extend(self.emit_for_machine(mid, ms, tick_seconds))
        return out
