"""Anomaly detection on machine telemetry using Z-score and IQR methods.

Queries `machine_telemetry` (raw sensor time-series) and returns
anomalous readings grouped by sensor.
"""
import logging
import math
import re
import statistics
from datetime import datetime, timezone

from langchain_core.tools import tool

from agent_server.tools.sql_query import sql_query

logger = logging.getLogger(__name__)

ZSCORE_THRESHOLD = 2.5
IQR_MULTIPLIER = 1.5

_SAFE_ID = re.compile(r"^[a-zA-Z0-9_\-]+$")
# The offset group is not decoration. The context hands the model
# `scenario.current_timestamp`, which is `datetime.now(timezone.utc).isoformat()`
# and therefore ends in `+00:00`. Rejecting that rejected the one timestamp the
# model had every reason to trust, so every call built from context failed
# validation before it reached the warehouse.
_SAFE_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$"
)


def _normalize_timestamp(value: str) -> str:
    """``value`` as a naive UTC ``YYYY-MM-DD HH:MM:SS`` literal.

    The offset is resolved here rather than passed through: the column is a
    plain TIMESTAMP, so comparing it against an offset-bearing string literal
    leaves the interpretation to the engine's session timezone. An hour of
    silent skew in an anomaly window is worse than an outright rejection.
    """
    text = value.strip().replace("Z", "+00:00").replace(" ", "T", 1)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return value
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def _zscore_anomalies(values: list[float], timestamps: list[str], sensor: str) -> list[dict]:
    """Detect anomalies using Z-score method."""
    if len(values) < 5:
        return []

    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    std = math.sqrt(variance) if variance > 0 else 0

    if std == 0:
        return []

    anomalies = []
    for i, v in enumerate(values):
        z = abs(v - mean) / std
        if z > ZSCORE_THRESHOLD:
            anomalies.append({
                "sensor": sensor,
                "timestamp": timestamps[i],
                "value": round(v, 2),
                "z_score": round(z, 2),
                "mean": round(mean, 2),
                "std": round(std, 2),
                "method": "z_score",
                "severity": "critical" if z > 3.5 else "warning",
            })
    return anomalies


def _iqr_anomalies(values: list[float], timestamps: list[str], sensor: str) -> list[dict]:
    """Detect anomalies using IQR method (more robust to non-normal distributions)."""
    if len(values) < 10:
        return []

    # Interpolate between ranks. Integer-division indexing (sorted[n//4],
    # sorted[3*n//4]) is a nearest-rank quartile that skews toward the upper
    # tail and never lands between samples, so the fences it produces are
    # biased and flag the wrong readings near the boundary. The error is
    # largest at n = 10, which is exactly where the guard above admits data.
    q1, _median, q3 = statistics.quantiles(values, n=4, method="inclusive")
    iqr = q3 - q1

    if iqr == 0:
        return []

    lower = q1 - IQR_MULTIPLIER * iqr
    upper = q3 + IQR_MULTIPLIER * iqr

    anomalies = []
    for i, v in enumerate(values):
        if v < lower or v > upper:
            anomalies.append({
                "sensor": sensor,
                "timestamp": timestamps[i],
                "value": round(v, 2),
                "lower_bound": round(lower, 2),
                "upper_bound": round(upper, 2),
                "method": "iqr",
                "severity": "critical" if v < (q1 - 3 * iqr) or v > (q3 + 3 * iqr) else "warning",
            })
    return anomalies


@tool
def detect_anomalies(
    machine_id: str,
    shift_id: str,
    timestamp_start: str,
    timestamp_end: str,
    method: str = "both",
) -> dict:
    """Detect anomalous sensor readings for a machine in a time window.
    Methods: 'zscore', 'iqr', or 'both'. Returns anomalies grouped by sensor
    with severity levels (warning/critical)."""
    for name, val in [("machine_id", machine_id), ("shift_id", shift_id)]:
        if not _SAFE_ID.match(val):
            return {"anomalies": [], "error": f"Invalid {name}: {val!r}", "total_points": 0}

    for name, val in [("timestamp_start", timestamp_start), ("timestamp_end", timestamp_end)]:
        if not _SAFE_TIMESTAMP.match(val):
            return {"anomalies": [], "error": f"Invalid {name} format: {val!r}", "total_points": 0}

    timestamp_start = _normalize_timestamp(timestamp_start)
    timestamp_end = _normalize_timestamp(timestamp_end)

    # DESC, because the 500-row cap has to fall somewhere and the end of the
    # window is the part worth testing. Ascending took the oldest 500 readings,
    # which on a busy machine is the first few seconds of the window — the
    # statistics then described a moment nobody asked about.
    rows = sql_query.invoke(
        f"SELECT timestamp, sensor_name, value "
        f"FROM telemetry_bronze "
        f"WHERE machine_id = '{machine_id}' AND shift_id = '{shift_id}' "
        f"AND timestamp >= '{timestamp_start}' AND timestamp <= '{timestamp_end}' "
        f"ORDER BY timestamp DESC LIMIT 500"
    )

    # Three different things used to arrive here as one message. The model
    # cannot recover from "No telemetry data found" — it reads like a finding
    # about the line. It can recover from being told the filter looks wrong.
    if rows and isinstance(rows[0], dict) and "error" in rows[0]:
        return {"anomalies": [], "error": f"Telemetry query failed: {rows[0]['error']}",
                "total_points": 0}
    if not rows:
        return {
            "anomalies": [],
            "error": (
                f"No telemetry rows for machine_id={machine_id!r} shift_id={shift_id!r} "
                f"between {timestamp_start} and {timestamp_end}. This is a filter "
                f"mismatch far more often than an idle machine: telemetry is keyed by "
                f"the environment-suffixed shift id and stamped with real wall-clock "
                f"time, so a window taken from the production schedule will never "
                f"match. Retry with a window relative to now."
            ),
            "total_points": 0,
        }

    sensor_data: dict[str, tuple[list[float], list[str]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        sensor = row.get("sensor_name")
        ts = row.get("timestamp")
        val = row.get("value")
        if not sensor or ts is None or val is None:
            continue
        try:
            fval = float(val)
            if not math.isfinite(fval):
                continue
        except (ValueError, TypeError):
            continue
        if sensor not in sensor_data:
            sensor_data[sensor] = ([], [])
        sensor_data[sensor][0].append(fval)
        sensor_data[sensor][1].append(str(ts))

    # Rows arrive newest-first from the query above; the anomaly reports read as
    # a time series, so put each sensor back in chronological order.
    for values, timestamps in sensor_data.values():
        values.reverse()
        timestamps.reverse()

    all_anomalies: list[dict] = []
    for sensor, (values, timestamps) in sensor_data.items():
        if method in ("zscore", "both"):
            all_anomalies.extend(_zscore_anomalies(values, timestamps, sensor))
        if method in ("iqr", "both"):
            all_anomalies.extend(_iqr_anomalies(values, timestamps, sensor))

    all_anomalies.sort(key=lambda a: a.get("severity", "") == "critical", reverse=True)

    return {
        "machine_id": machine_id,
        "shift_id": shift_id,
        "window": f"{timestamp_start} to {timestamp_end}",
        "total_sensors_analyzed": len(sensor_data),
        "total_data_points": sum(len(v) for v, _ in sensor_data.values()),
        "anomalies": all_anomalies[:20],
        "total_anomalies": len(all_anomalies),
        "method": method,
    }
