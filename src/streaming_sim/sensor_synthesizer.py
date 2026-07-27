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

    def _process_value(self, sensor: SensorDef, ms: MachineState) -> float:  # noqa: D401

        """Process / level / speed sensors: jitter around nominal, degrade on fault."""
        nom = sensor.nominal_value if sensor.nominal_value is not None else 0.0
        # Base noise: ±2 % of nominal (or ±1 absolute when nominal=0).
        noise = (nom * 0.02) if nom else 1.0
        val = nom + self._rng.uniform(-noise, noise)

        if ms.status == "stopped":
            # Speed/level/process collapse to ~0 for speed/throughput-tied sensors.
            if sensor.sensor_type in ("speed",):
                return 0.0
            # Level falls slowly, process drifts toward room temp / ambient.
            if sensor.sensor_type == "level":
                return max(0.0, val * 0.95)
            return val
        if ms.status == "degraded":
            # Process / speed sensors drop to throughput's share of nominal.
            if sensor.sensor_type in ("speed", "process"):
                ratio = max(0.1, ms.speed_pct / 100.0)
                val = nom * ratio + self._rng.uniform(-noise, noise)
        return round(val, 3)

    def _level_value(self, sensor: SensorDef, ms: MachineState, tick_seconds: float) -> float:
        """Level sensors: gradually deplete based on throughput, refill possible."""
        key = (sensor.machine_id, sensor.sensor_name)  # SensorDef carries its machine id
        cur = self._counters.get(key)
        if cur is None:
            cur = float(sensor.nominal_value or 80.0)
        # Deplete when running, hold when stopped, refill randomly.
        if ms.status in ("running", "degraded"):
            depletion = (ms.throughput / 60.0) * tick_seconds * 0.05  # ~5 %/min at full throughput
            cur = max(0.0, cur - depletion)
        if cur < 20.0 and self._rng.random() < 0.05:
            cur = float(sensor.nominal_value or 80.0)  # operator refilled
        self._counters[key] = cur
        return round(cur + self._rng.uniform(-0.5, 0.5), 2)

    def _counter_value(self, sensor: SensorDef, ms: MachineState, tick_seconds: float) -> float:
        """Counter: monotonic accumulation tied to throughput."""
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
