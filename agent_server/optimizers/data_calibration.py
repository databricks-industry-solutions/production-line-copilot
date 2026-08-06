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
    - production_schedule for ideal_rate_bpm (the planned line rate per SKU)
    - gold_history_line_kpi for oee_std (STDDEV of oee_to_now across recent shifts)
    - gold_history_event_log for historical MTTR (AVG duration of resolved stops)
    """
    global _fc_cache, _fc_cache_ts
    with _cache_lock:
        if _fc_cache and (time.monotonic() - _fc_cache_ts) < _CACHE_TTL:
            return dict(_fc_cache)

    params = dict(_DEFAULTS_FORECASTER)

    try:
        # `ideal_rate_bpm` is the quantity itself, so it is read rather than
        # derived. The previous expression divided by `planned_hours`, a column
        # that does not exist — and even had it existed, cases-per-hour × 60 is
        # not bottles per minute: it lands around 63,000 and the sanity guard
        # below would have rejected it. This calibration has never once fired.
        rate_rows = _sql(
            "SELECT AVG(ideal_rate_bpm) AS avg_rate "
            "FROM production_schedule WHERE ideal_rate_bpm > 0"
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
            "FROM gold_history_line_kpi WHERE oee_to_now IS NOT NULL AND oee_to_now > 0"
        )
        if std_rows and std_rows[0].get("oee_std"):
            val = float(std_rows[0]["oee_std"])
            if 0.001 < val < 0.5:
                params["oee_std"] = round(val, 4)
                logger.info("Calibrated oee_std = %.4f", val)
    except Exception:
        logger.debug("Could not calibrate oee_std", exc_info=True)

    try:
        # `major_stop` matched nothing: it belongs to the legacy seed generator,
        # and the events that actually land in gold_history come from the
        # producer and the backfill, which write micro_stop / minor_stop /
        # machine_breakdown / sensor_failure / quality_hold / changeover /
        # cip_cycle. The query never raised — it just averaged zero rows — so
        # MTTR silently stayed at the 15-minute default for every forecast.
        #
        # The three classes below are the unplanned stops that need a repair.
        # micro_stop is excluded deliberately: at three to four minutes it
        # dominates by count and would drag MTTR toward "clear a jam", which is
        # not what a caller asking how long a breakdown will last is after.
        # changeover and cip_cycle are planned and are not repairs at all.
        mttr_rows = _sql(
            "SELECT AVG(duration_minutes) AS avg_mttr "
            "FROM gold_history_event_log "
            "WHERE event_type IN ('machine_breakdown', 'minor_stop', 'sensor_failure') "
            "AND duration_minutes IS NOT NULL "
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
    """Learn CPT values from historical quality checks + events.

    For each machine, computes the empirical probability that a quality check
    failed while an event with a given root_cause was open.
    Falls back to a uniform prior if insufficient data.

    Returns: {machine_id: {condition: probability, ...}, ...}
    """
    global _cpt_cache, _cpt_cache_ts
    with _cache_lock:
        if _cpt_cache and (time.monotonic() - _cpt_cache_ts) < _CACHE_TTL:
            return dict(_cpt_cache)

    MIN_SAMPLES = 5

    # A failure is a check whose `result` is 'fail'. The previous version
    # compared `q.reject_rate` against a threshold; the quality table has no
    # such column — it holds one row per individual check, so a rate has to be
    # counted rather than read. Counting `q.check_id` rather than `*` matters
    # too: with a LEFT JOIN, an event with no checks in its window would
    # otherwise contribute a row and be scored as a clean sample.
    rows = _sql(
        "SELECT e.machine_id, e.root_cause, "
        "  COUNT(q.check_id) AS total, "
        "  SUM(CASE WHEN q.result = 'fail' THEN 1 ELSE 0 END) AS fail_count "
        "FROM gold_history_event_log e "
        "LEFT JOIN quality_checks_bronze q "
        "  ON e.machine_id = q.machine_id "
        "  AND q.timestamp BETWEEN e.timestamp_start AND COALESCE(e.timestamp_end, e.timestamp_start) "
        "WHERE e.root_cause IS NOT NULL AND e.root_cause != '' "
        "GROUP BY e.machine_id, e.root_cause "
        "HAVING COUNT(q.check_id) >= {min_s}".format(min_s=MIN_SAMPLES)
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
