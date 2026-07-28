"""Derived OT-style display metrics per machine (control tower UI).

Mirrors logic in ``client/src/lib/machineMeta.ts`` ``deriveMachineReadings`` —
keep both in sync when tuning demo formulas.
"""

from __future__ import annotations

from typing import Any

from src.data_generation.config import MACHINES, MachineDef


def _machine_def(machine_id: str) -> MachineDef | None:
    for m in MACHINES:
        if m.id == machine_id:
            return m
    return None


def enrich_machine_state_row(machine_id: str, row: dict[str, Any]) -> dict[str, Any]:
    """Return extra keys: primary_sensor_1, primary_sensor_2, material_level_pct (nullable)."""
    status = str(row.get("status") or "")
    sp_raw = row.get("speed_pct")
    sp = float(sp_raw) if sp_raw is not None else 100.0
    sp = max(0.0, min(100.0, sp))
    tput = max(0.0, float(row.get("throughput_per_min") or 0.0))
    alarms = int(row.get("alarm_count") or 0)
    degraded = status in ("degraded", "stopped")

    meta = _machine_def(machine_id)
    if meta is None:
        return {"primary_sensor_1": None, "primary_sensor_2": None, "material_level_pct": None}

    mtype = meta.type
    nom = meta.nominal_speed

    if mtype == "conveyor" and nom is not None:
        belt = (float(nom) * sp) / 100.0
        accum = min(95.0, 18.0 + (100.0 - sp) * 0.55 + (12.0 if degraded else 0.0))
        return {
            "primary_sensor_1": round(belt, 1),
            "primary_sensor_2": round(accum, 1),
            "material_level_pct": None,
        }

    if machine_id == "DEPALLETIZER":
        cyc = 14.0 / max(0.35, sp / 100.0)
        return {"primary_sensor_1": round(cyc, 1), "primary_sensor_2": None, "material_level_pct": None}

    if machine_id == "BOTTLE_WASHER":
        temp = 78.0 + (sp - 100.0) * 0.04 - (4.0 if degraded else 0.0)
        tunnel = (25.0 * sp) / 100.0
        level = max(55.0, 88.0 - (100.0 - sp) * 0.35 - alarms * 1.2)
        return {
            "primary_sensor_1": round(temp, 1),
            "primary_sensor_2": round(tunnel, 1),
            "material_level_pct": round(level, 1),
        }

    if machine_id == "EMPTY_INSPECTOR":
        nok = (4.0 + alarms * 0.8) if degraded else (0.8 + (100.0 - sp) * 0.03)
        rej = (2.0 + alarms) if degraded else 0.2
        return {"primary_sensor_1": round(nok, 1), "primary_sensor_2": round(rej, 1), "material_level_pct": None}

    if machine_id == "FILLER":
        base = 322.0 if degraded else 329.5 + (sp - 85.0) * 0.02
        fill = min(340.0, max(318.0, base - alarms * 0.4))
        lows = float(alarms)
        bowl = max(25.0, 72.0 + (sp / 100.0) * 22.0 - (18.0 if degraded else 0.0))
        return {
            "primary_sensor_1": round(fill, 1),
            "primary_sensor_2": lows,
            "material_level_pct": round(bowl, 1),
        }

    if machine_id == "CAPPER":
        torque = min(2.8, max(1.2, 2.0 * (sp / 100.0) - (0.35 if degraded else 0.0)))
        hop = max(20.0, 82.0 - (100.0 - sp) * 0.25 - alarms)
        return {"primary_sensor_1": round(torque, 2), "primary_sensor_2": None, "material_level_pct": round(hop, 1)}

    if machine_id == "PASTEURIZER":
        z2 = 72.0 + (sp - 100.0) * 0.02 - (3.0 if degraded else 0.0)
        pu = min(38.0, max(12.0, 26.0 * (sp / 100.0) - (6.0 if degraded else 0.0)))
        return {"primary_sensor_1": round(z2, 1), "primary_sensor_2": round(pu, 1), "material_level_pct": None}

    if machine_id == "LABELLER":
        nok = (5.0 + alarms) if degraded else (1.2 + (100.0 - sp) * 0.04)
        glue = 145.0 + (sp - 100.0) * 0.05 - (8.0 if degraded else 0.0)
        roll = max(18.0, 85.0 - (100.0 - sp) * 0.2 - alarms * 1.5)
        return {
            "primary_sensor_1": round(nok, 1),
            "primary_sensor_2": round(glue, 1),
            "material_level_pct": round(roll, 1),
        }

    if machine_id == "CODER":
        ink = max(15.0, 72.0 - alarms * 2.5 - (12.0 if degraded else 0.0))
        return {"primary_sensor_1": None, "primary_sensor_2": None, "material_level_pct": round(ink, 1)}

    if machine_id == "CASE_PACKER" and nom is not None:
        spd = (float(nom) * sp) / 100.0
        rej = float(alarms + (3 if degraded else 0))
        mag = max(22.0, 78.0 - (100.0 - sp) * 0.3 - alarms)
        return {"primary_sensor_1": rej, "primary_sensor_2": round(spd, 2), "material_level_pct": round(mag, 1)}

    if machine_id == "SHRINK_WRAPPER":
        film = max(20.0, 82.0 - (100.0 - sp) * 0.25 - alarms)
        return {"primary_sensor_1": None, "primary_sensor_2": None, "material_level_pct": round(film, 1)}

    if machine_id == "CHECKWEIGHER":
        w = 7920.0 + (sp - 100.0) * 4.0 - alarms * 12.0 - (40.0 if degraded else 0.0)
        rej = float(alarms + (2 if degraded else 0))
        return {"primary_sensor_1": round(w), "primary_sensor_2": rej, "material_level_pct": None}

    if machine_id == "PALLETIZER":
        mag = max(25.0, 80.0 - (100.0 - sp) * 0.22 - alarms * 1.2)
        return {"primary_sensor_1": None, "primary_sensor_2": None, "material_level_pct": round(mag, 1)}

    if nom is not None and mtype in ("filler", "capper", "labeller"):
        eff = (float(nom) * sp) / 100.0
        return {"primary_sensor_1": round(eff, 1), "primary_sensor_2": None, "material_level_pct": None}

    if nom is not None:
        eff = (float(nom) * sp) / 100.0
        return {"primary_sensor_1": round(eff, 1), "primary_sensor_2": None, "material_level_pct": None}

    return {
        "primary_sensor_1": round(tput, 1) if tput > 0 else None,
        "primary_sensor_2": None,
        "material_level_pct": None,
    }
