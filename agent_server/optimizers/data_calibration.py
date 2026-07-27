"""Data-driven parameter calibration for optimiser modules.

Queries actual production data to derive realistic parameters instead of
relying on hardcoded values. Results are cached for 5 minutes.
"""
import logging
import threading
import time

logger = logging.getLogger(__name__)

_cache_lock = threading.Lock()
_fc_cache: dict | None = None
_fc_cache_ts: float = 0.0
_cpt_cache: dict | None = None
_cpt_cache_ts: float = 0.0
_CACHE_TTL = 300.0

_DEFAULTS_FORECASTER = {
    "ideal_rate_bpm": 420.0,
    "oee_std": 0.05,
    "historical_mttr_minutes": 15.0,
}

_DEFAULTS_CPT: dict[str, dict[str, float]] = {}


def _sql(query: str) -> list[dict]:
    """Execute a SQL query via the shared tool and return safe rows."""
    try:
        from agent_server.tools.sql_query import sql_query
        rows = sql_query.invoke(query)
        return [r for r in (rows or []) if isinstance(r, dict) and "error" not in r]
    except Exception:
        logger.exception("Calibration query failed: %s", query[:80])
        return []


def calibrate_forecaster_params() -> dict:
    """Return calibrated Monte Carlo parameters from live data.

    Queries:
    - production_schedule for actual ideal_rate (planned_cases / planned_hours * 60)
    - line_kpi for oee_std (STDDEV of oee_to_now across recent shifts)
    - event_log for historical MTTR (AVG duration of resolved major_stop events)
    """
    global _fc_cache, _fc_cache_ts
    with _cache_lock:
        if _fc_cache and (time.monotonic() - _fc_cache_ts) < _CACHE_TTL:
            return dict(_fc_cache)

    params = dict(_DEFAULTS_FORECASTER)

    try:
        rate_rows = _sql(
            "SELECT AVG(planned_cases / NULLIF(planned_hours, 0) * 60) AS avg_rate "
            "FROM production_schedule WHERE planned_cases > 0"
        )
        if rate_rows and rate_rows[0].get("avg_rate"):
            val = float(rate_rows[0]["avg_rate"])
            if 100 < val < 1000:
                params["ideal_rate_bpm"] = round(val, 1)
                logger.info("Calibrated ideal_rate_bpm = %.1f", val)
    except Exception:
        logger.debug("Could not calibrate ideal_rate_bpm", exc_info=True)

    try:
        std_rows = _sql(
            "SELECT STDDEV(oee_to_now / 100.0) AS oee_std "
            "FROM line_kpi WHERE oee_to_now IS NOT NULL AND oee_to_now > 0"
        )
        if std_rows and std_rows[0].get("oee_std"):
            val = float(std_rows[0]["oee_std"])
            if 0.001 < val < 0.5:
                params["oee_std"] = round(val, 4)
                logger.info("Calibrated oee_std = %.4f", val)
    except Exception:
        logger.debug("Could not calibrate oee_std", exc_info=True)

    try:
        mttr_rows = _sql(
            "SELECT AVG(duration_minutes) AS avg_mttr "
            "FROM event_log "
            "WHERE event_type = 'major_stop' AND duration_minutes IS NOT NULL "
            "AND duration_minutes > 0 AND duration_minutes < 480"
        )
        if mttr_rows and mttr_rows[0].get("avg_mttr"):
            val = float(mttr_rows[0]["avg_mttr"])
            if 1 < val < 120:
                params["historical_mttr_minutes"] = round(val, 1)
                logger.info("Calibrated historical_mttr_minutes = %.1f", val)
    except Exception:
        logger.debug("Could not calibrate historical_mttr_minutes", exc_info=True)

    with _cache_lock:
        _fc_cache = dict(params)
        _fc_cache_ts = time.monotonic()

    return params


def calibrate_quality_cpt() -> dict[str, dict[str, float]]:
    """Learn CPT values from historical quality_checks + event_log data.

    For each machine, computes the empirical probability of a quality failure
    (reject_rate > threshold) co-occurring with each event root_cause.
    Falls back to a uniform prior if insufficient data.

    Returns: {machine_id: {condition: probability, ...}, ...}
    """
    global _cpt_cache, _cpt_cache_ts
    with _cache_lock:
        if _cpt_cache and (time.monotonic() - _cpt_cache_ts) < _CACHE_TTL:
            return dict(_cpt_cache)

    QUALITY_FAIL_THRESHOLD = 0.03
    MIN_SAMPLES = 5

    rows = _sql(
        "SELECT e.machine_id, e.root_cause, "
        "  COUNT(*) AS total, "
        "  SUM(CASE WHEN q.reject_rate > {thr} THEN 1 ELSE 0 END) AS fail_count "
        "FROM event_log e "
        "LEFT JOIN quality_checks q "
        "  ON e.machine_id = q.machine_id "
        "  AND q.timestamp BETWEEN e.timestamp_start AND COALESCE(e.timestamp_end, e.timestamp_start) "
        "WHERE e.root_cause IS NOT NULL AND e.root_cause != '' "
        "GROUP BY e.machine_id, e.root_cause "
        "HAVING COUNT(*) >= {min_s}".format(thr=QUALITY_FAIL_THRESHOLD, min_s=MIN_SAMPLES)
    )

    cpt: dict[str, dict[str, float]] = {}
    for r in rows:
        mid = r.get("machine_id", "")
        cause = r.get("root_cause", "")
        total = int(r.get("total", 0))
        fail_count = int(r.get("fail_count", 0))
        if not mid or not cause or total < MIN_SAMPLES:
            continue
        prob = min(max(fail_count / total, 0.01), 0.99)
        cpt.setdefault(mid, {})
        cpt[mid][cause] = round(prob, 3)

    for mid in cpt:
        cpt[mid]["normal"] = 0.02

    if cpt:
        logger.info("Calibrated quality CPTs for %d machines from data", len(cpt))

    with _cache_lock:
        _cpt_cache = dict(cpt) if cpt else None
        _cpt_cache_ts = time.monotonic()

    return cpt
