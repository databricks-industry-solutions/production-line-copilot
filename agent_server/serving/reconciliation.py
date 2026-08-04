"""Cross-path reconciliation between the live plane and the analytics plane.

The producer dual-writes every tick, so the same events travel two independent
routes:

    Tier-1 (live)      producer → Lakebase ``ai_serving.*_history``   ~200-500 ms
    Tier-2 (analytics) producer → Zerobus → ``*_bronze`` → QC gate
                                → ``gold_history_*`` → governed MVM    ~5-30 s +
                                                                       one cycle

Two routes carrying the same truth is only credible if you can show they agree.
This module compares them and, crucially, splits any difference into
**explained** and **unexplained**:

  * explained   — rows the QC gate deliberately rejected (``quality_rejects``).
                  A non-zero value here is the system working as designed.
  * unexplained — everything else. This is the only number that should ever be
                  red; it means rows went missing (or appeared) with no recorded
                  reason.

DQX warnings are reported alongside but never change a count: DQX flags rows
into an audit table without dropping them.

Why a *settled* window
----------------------
Tier-1 is seconds ahead of Tier-2 by design, so comparing "up to now" would
always show a large bogus deficit. Every count is therefore taken over a window
that ends ``lag_guard_seconds`` in the past, long enough for the Zerobus
materialization SLA plus one streaming cycle to land. The still-moving slice
between the window end and now is reported separately as "settling" so the UI
can show it without counting it as drift.

Both engines are given the SAME absolute, offset-qualified bounds, computed once
here. Letting each apply an interval to its own ``now()`` looks simpler but is
subtly wrong: the two queries run at different instants, so a row sitting on the
boundary falls inside one window and outside the other and shows up as a
one-tick phantom deviation forever. Pinning the bounds makes the comparison
exact, which is what lets the default tolerance stay at zero and a single missing
row still mean something.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Sequence

# Defaults: 15 min of settled traffic, ending 2 min ago. The guard must exceed
# the Zerobus SLA (~5-30 s) plus one availableNow cycle (~60 s) or the analytics
# side will look permanently behind.
DEFAULT_WINDOW_MINUTES = 15
DEFAULT_LAG_GUARD_SECONDS = 120

# A row carries the SAME event timestamp on both paths (the producer stamps it
# once per tick), so an exact match is achievable and the default tolerance is
# zero. The fraction exists only for operators who want to ignore de-minimis
# boundary noise on very high-volume lines.
DEFAULT_TOLERANCE_FRACTION = 0.0


@dataclass(frozen=True)
class Entity:
    """One logical stream that exists on BOTH paths, so it can be reconciled."""

    key: str
    label: str
    lakebase_table: str
    lakebase_ts: str
    bronze_table: str
    bronze_ts: str
    gold_table: str


# Only these four are dual-written by the producer, so only these reconcile
# count-for-count. telemetry and quality_checks are bronze-only (no Lakebase
# twin) and are deliberately NOT faked into the matrix.
ENTITIES: tuple[Entity, ...] = (
    Entity(
        "machine_state", "Machine state",
        "ai_serving.machine_state_history", "ts",
        "machine_state_bronze", "ts", "gold_history_machine_state",
    ),
    Entity(
        "line_kpi", "Line KPI",
        "ai_serving.line_kpi_history", "ts",
        "line_kpi_bronze", "ts", "gold_history_line_kpi",
    ),
    Entity(
        "inventory_status", "Inventory",
        "ai_serving.inventory_status_history", "ts",
        "inventory_status_bronze", "ts", "gold_history_inventory_status",
    ),
    Entity(
        "event_log", "Events",
        "ai_serving.event_log_history", "timestamp_start",
        "event_log_bronze", "timestamp_start", "gold_history_event_log",
    ),
)


@dataclass(frozen=True)
class GovernedTable:
    """A governed MVM table surfaced for freshness / population, not count parity.

    ``kind`` matters for honesty: the streaming silver layer MERGE-upserts, so
    dimension-like targets hold one row per entity (a line, a material) and their
    row count must NOT be compared against an append-only event count. Only
    ``append`` targets grow one row per source record.

    ``incident_driven`` targets are empty at steady state by design and fill only
    while an incident is active — zero is the correct answer, not a failure.
    """

    key: str
    label: str
    schema: str
    table: str
    ts_column: str
    kind: str  # "upsert" | "append"
    incident_driven: bool = False


GOVERNED_TABLES: tuple[GovernedTable, ...] = (
    GovernedTable("production_line", "Production line", "production",
                  "production_line", "created_timestamp", "upsert"),
    GovernedTable("stock_balance", "Stock balance", "inventory",
                  "stock_balance", "last_updated_timestamp", "upsert"),
    GovernedTable("failure_record", "Failure records", "asset",
                  "failure_record", "created_timestamp", "append", True),
    GovernedTable("inspection_result", "Inspection results", "quality",
                  "inspection_result", "created_timestamp", "append", True),
    GovernedTable("ncr", "NCRs", "quality",
                  "ncr", "created_timestamp", "append", True),
)


def window_bounds(
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
    lag_guard_seconds: int = DEFAULT_LAG_GUARD_SECONDS,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Return the absolute ``(start, end)`` of the settled window, in UTC.

    ``end`` sits ``lag_guard_seconds`` in the past so in-flight rows are excluded.
    Truncated to whole seconds because the producer ticks at 1 Hz; a sub-second
    bound would slice a tick in half on one side only.
    """
    ref = now or datetime.now(timezone.utc)
    if ref.tzinfo is None:
        # `astimezone` would read a naive value as SYSTEM LOCAL time and shift the
        # whole window by the host's UTC offset — silently, and differently per
        # deployment. `datetime.utcnow()` is naive, so this is an easy mistake.
        raise ValueError("`now` must be timezone-aware; pass datetime.now(timezone.utc)")
    ref = ref.astimezone(timezone.utc).replace(microsecond=0)
    end = ref - timedelta(seconds=max(0, int(lag_guard_seconds)))
    start = end - timedelta(minutes=max(1, int(window_minutes)))
    return start, end


def _ts(value: datetime) -> str:
    """Offset-qualified literal both Spark and Postgres parse unambiguously."""
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00:00")


def _q(catalog: str, schema: str, table: str) -> str:
    _check_ident(catalog, "catalog")
    _check_ident(schema, "schema")
    return f"`{catalog}`.`{schema}`.`{table}`"


_SHIFT_RE = re.compile(r"[A-Za-z0-9_.\-]+")
_IDENT_RE = re.compile(r"[A-Za-z0-9_]+")


def _check_ident(value: str, what: str) -> str:
    """Validate a catalog/schema name before it reaches SQL.

    These come from operator-set env vars rather than end users, so this is
    about failing loudly on a typo instead of emitting a malformed query — and
    about holding every interpolated identifier to the same standard
    ``shift_id`` is already held to.
    """
    if not _IDENT_RE.fullmatch(value or ""):
        raise ValueError(f"invalid {what}: {value!r}")
    return value


def shift_predicate(shift_id: str | None) -> str:
    """``AND shift_id = '...'`` for the configured shift, or empty string.

    Dev and prod share a single Lakebase instance and isolate themselves purely
    by ``sim_shift_id`` (``shift_1_dev`` vs ``shift_1``). Without this filter the
    live side counts BOTH environments' producers while the analytics side —
    isolated by UC schema — counts only one, and every stream reports a
    permanent phantom 2x deviation.

    Rejects anything that isn't a plain identifier rather than escaping it: a
    malformed shift would silently produce wrong numbers, and wrong numbers on a
    trust panel are worse than an error.
    """
    if not shift_id:
        return ""
    # fullmatch, not match: `$` would accept a trailing newline.
    if not _SHIFT_RE.fullmatch(shift_id):
        raise ValueError(f"invalid shift_id: {shift_id!r}")
    return f" AND shift_id = '{shift_id}'"


def build_existing_tables_sql(catalog: str, schemas: Sequence[str]) -> str:
    """List tables across one or more schemas in a single round-trip.

    A gold or MVM table only exists once something has written to it, and a
    missing table would otherwise fail the whole UNION. Takes all schemas at
    once because the governed model spans four of them and per-schema queries
    would quadruple the latency floor for no benefit.

    The explicit LIMIT matters: ``sql_query`` appends ``LIMIT 500`` to any
    statement that doesn't contain the word, which would silently truncate the
    listing and make real tables look absent.
    """
    _check_ident(catalog, "catalog")
    joined = ", ".join(f"'{_check_ident(s, 'schema').lower()}'" for s in schemas)
    return (
        "SELECT lower(table_schema) AS table_schema, lower(table_name) AS table_name "
        f"FROM `{catalog}`.information_schema.tables "
        f"WHERE lower(table_schema) IN ({joined}) LIMIT 5000"
    )


def build_uc_counts_sql(
    catalog: str,
    schema: str,
    start: datetime,
    end: datetime,
    existing: Iterable[str],
    entities: Sequence[Entity] = ENTITIES,
    shift_id: str | None = None,
) -> str:
    """One UNION ALL query for every bronze/gold count plus rejects and DQX.

    Skips tables absent from ``existing`` so a not-yet-written gold table
    degrades to "absent" instead of erroring the batch.

    The shift filter applies to bronze/gold only: ``quality_rejects`` and
    ``dqx_warnings`` carry no ``shift_id`` column, and they don't need one
    because they live in the environment's own UC schema.
    """
    have = {t.lower() for t in existing}
    shift = shift_predicate(shift_id)
    span = f"> TIMESTAMP '{_ts(start)}' AND {{col}} <= TIMESTAMP '{_ts(end)}'"
    parts: list[str] = []
    for e in entities:
        if e.bronze_table.lower() in have:
            w = span.format(col=e.bronze_ts)
            parts.append(
                f"SELECT '{e.key}' AS entity, 'bronze' AS layer, COUNT(*) AS n "
                f"FROM {_q(catalog, schema, e.bronze_table)} "
                f"WHERE {e.bronze_ts} {w}{shift}"
            )
        if e.gold_table.lower() in have:
            w = span.format(col=e.bronze_ts)
            parts.append(
                f"SELECT '{e.key}' AS entity, 'gold' AS layer, COUNT(*) AS n "
                f"FROM {_q(catalog, schema, e.gold_table)} "
                f"WHERE {e.bronze_ts} {w}{shift}"
            )
    if "quality_rejects" in have:
        # Rejects must be windowed on EVENT time to be subtractable from bronze
        # and gold, which are. `rejected_at` is processing time, offset by the
        # whole Zerobus + micro-batch latency, so using it only cancels out while
        # the reject rate is constant — and it stops being constant during
        # exactly the incident someone is demoing. The original row survives in
        # `payload` (a map of every source column cast to string), so the event
        # timestamp is recoverable; `rejected_at` remains the fallback.
        #
        # `bronze_table` holds the SHORT stream name ("machine_state"), not the
        # table name, so it joins directly against the entity keys.
        for e in entities:
            event_ts = (
                f"COALESCE(try_cast(payload['{e.bronze_ts}'] AS TIMESTAMP), rejected_at)"
            )
            parts.append(
                f"SELECT '{e.key}' AS entity, 'rejected' AS layer, COUNT(*) AS n "
                f"FROM {_q(catalog, schema, 'quality_rejects')} "
                f"WHERE bronze_table = '{e.key}' "
                f"AND {event_ts} {span.format(col=event_ts)}"
            )
    if "dqx_warnings" in have:
        w = span.format(col="flagged_at")
        parts.append(
            "SELECT bronze_table AS entity, 'dqx' AS layer, COUNT(*) AS n "
            f"FROM {_q(catalog, schema, 'dqx_warnings')} "
            f"WHERE flagged_at {w} GROUP BY bronze_table"
        )
    return "\nUNION ALL\n".join(parts)


def build_governed_sql(
    mvm_catalog: str,
    existing_by_schema: dict[str, set[str]],
    tables: Sequence[GovernedTable] = GOVERNED_TABLES,
) -> str:
    """Row count + last-write freshness for each governed MVM target."""
    parts: list[str] = []
    for g in tables:
        if g.table.lower() not in {t.lower() for t in existing_by_schema.get(g.schema, set())}:
            continue
        parts.append(
            f"SELECT '{g.key}' AS key, COUNT(*) AS n, "
            f"MAX({g.ts_column}) AS last_ts "
            f"FROM {_q(mvm_catalog, g.schema, g.table)}"
        )
    return "\nUNION ALL\n".join(parts)


def build_governed_oee_sql(mvm_catalog: str) -> str:
    """Governed OEE, for the value spot-check against the live path."""
    return (
        "SELECT MAX(actual_oee_percentage) AS oee "
        f"FROM {_q(mvm_catalog, 'production', 'production_line')}"
    )


def build_lakebase_counts_sql(
    start: datetime,
    end: datetime,
    entities: Sequence[Entity] = ENTITIES,
    shift_id: str | None = None,
) -> str:
    """Tier-1 counts over exactly the same absolute window as the UC side."""
    shift = shift_predicate(shift_id)
    parts = [
        f"SELECT '{e.key}' AS entity, COUNT(*) AS n FROM {e.lakebase_table} "
        f"WHERE {e.lakebase_ts} > TIMESTAMPTZ '{_ts(start)}' "
        f"AND {e.lakebase_ts} <= TIMESTAMPTZ '{_ts(end)}'{shift}"
        for e in entities
    ]
    return "\nUNION ALL\n".join(parts)


def build_lakebase_oee_sql(shift_id: str | None = None) -> str:
    """Most recent live OEE (0..100) for the value spot-check.

    Shift-scoped for the same reason as the counts: the newest row in a shared
    Lakebase may belong to the other environment's producer.
    """
    where = shift_predicate(shift_id).replace(" AND ", " WHERE ", 1)
    return (
        f"SELECT oee_to_now AS oee FROM ai_serving.line_kpi_history{where} "
        "ORDER BY ts DESC LIMIT 1"
    )


def classify_entity(
    *,
    lakebase: int | None,
    bronze: int | None,
    gold: int | None,
    rejected: int | None,
    tolerance_fraction: float = DEFAULT_TOLERANCE_FRACTION,
) -> dict:
    """Split the Tier-1 → Tier-2 difference into explained and unexplained.

    Two independent hops are checked:

      ingest  Tier-1 vs bronze. The producer writes both, so in a settled window
              these must match exactly; any gap is unexplained ingest loss.
      qc      bronze vs gold. Gold is bronze minus whatever the QC gate rejected,
              so ``bronze - gold - rejected`` must be zero; the rejects
              themselves are the explained part.

    Deltas are signed (a negative value means the downstream layer has MORE rows
    than it should, e.g. a duplicate replay), while ``unexplained`` is the
    magnitude, because drift in either direction is equally wrong.
    """
    ingest_delta = (
        lakebase - bronze if (lakebase is not None and bronze is not None) else None
    )
    # An unreadable reject count makes the QC hop unverifiable rather than clean:
    # treating it as zero would silently reclassify explained rows as drift.
    qc_delta = (
        bronze - gold - rejected
        if (bronze is not None and gold is not None and rejected is not None)
        else None
    )

    unexplained = abs(ingest_delta or 0) + abs(qc_delta or 0)
    baseline = max(lakebase or 0, bronze or 0)
    allowed = int(baseline * tolerance_fraction)

    # Order matters. Drift we actually detected always wins, even if the other
    # hop was unmeasurable. But when the live side is missing we must NOT report
    # "in agreement" off the QC hop alone — the cross-plane comparison, which is
    # the entire point of this panel, never happened.
    if unexplained > allowed:
        status = "error"
    elif lakebase == 0 and bronze == 0 and not rejected and not unexplained:
        # Nothing entered the window on either plane, so there is no drift to
        # find and no verdict to hedge. Distinct from "unknown": a stream that
        # is merely quiet (event_log at steady state, whose gold table may not
        # exist yet because no event has ever flowed) must not withhold a clean
        # verdict from the whole panel for the entire demo.
        status = "idle"
    elif ingest_delta is None or qc_delta is None:
        # Either hop being unmeasurable blocks a clean verdict, symmetrically:
        # "no drift detected" is not the same claim as "both hops verified".
        status = "unknown"
    elif unexplained > 0:
        status = "warn"
    else:
        status = "ok"

    return {
        "lakebase_rows": lakebase,
        "bronze_rows": bronze,
        "gold_rows": gold,
        "explained_rejected": rejected,
        "ingest_delta": ingest_delta,
        "qc_delta": qc_delta,
        "unexplained": unexplained,
        "status": status,
    }


OEE_MAX_PERCENT = 100.0


def compare_oee(
    live: float | None,
    governed: float | None,
    tolerance_points: float = 5.0,
) -> dict:
    """Spot-check a VALUE, not just a count, across the two paths.

    Row counts agreeing proves nothing about the correctness of the mapping —
    the governed OEE was once stored 100x inflated while every count matched.

    The scale bug is detected by RANGE, not by the ratio between the two sides.
    OEE is definitionally a 0..100 percentage, so ``governed > 100`` is
    impossible on its own terms and needs no second opinion. A ratio test is
    actively wrong here: ``oee_to_now`` is cumulative shift-to-date, so it
    starts near zero and climbs, meaning a healthy line a minute into a shift
    reads live≈1 against governed≈74 — a ratio of 74, which any "is it ~100x?"
    band flags as corruption. That would turn the headline verdict red during
    normal startup, which is exactly when someone is watching.
    """
    if live is None or governed is None:
        return {"live": live, "governed": governed, "delta": None, "status": "unknown"}

    delta = governed - live

    if governed > OEE_MAX_PERCENT or live > OEE_MAX_PERCENT:
        status = "error"
        note = (
            f"OEE outside the valid 0-{OEE_MAX_PERCENT:.0f}% range — "
            "the mapping is applying a scale factor"
        )
    elif abs(delta) <= tolerance_points:
        status = "ok"
        note = None
    else:
        status = "warn"
        note = (
            "outside tolerance — expected briefly after a shift start or restart, "
            "while the cumulative live figure catches up"
        )

    return {
        "live": round(live, 2),
        "governed": round(governed, 2),
        "delta": round(delta, 2),
        "status": status,
        "note": note,
    }


def _as_int(value) -> int | None:
    """Coerce a count, returning None (not 0) when it can't be read.

    Zero is the single worst fallback here: an unreadable bronze count would be
    indistinguishable from "every row is missing", and the panel would report
    the entire live count as unexplained drift. None flows through
    ``classify_entity`` as ``unknown``, which is the honest answer.
    """
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _as_float(value) -> float | None:
    """Coerce a metric value, returning None rather than raising."""
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None  # reject NaN


def _overall_status(entities: Sequence[dict], oee: dict) -> str:
    """Roll per-entity verdicts into the single headline the panel leads with.

    Severity wins over silence: one drifting stream makes the whole picture
    untrustworthy, and one unmeasurable stream blocks a clean "in agreement".
    Idle streams are the exception — they carried no rows, so they can neither
    hide drift nor confirm its absence, and are simply passed over unless every
    stream is idle.
    """
    statuses = {e["status"] for e in entities}
    if oee.get("status") == "error" or "error" in statuses:
        return "error"
    if "warn" in statuses or oee.get("status") == "warn":
        return "warn"
    if "unknown" in statuses:
        return "unknown"
    if statuses == {"idle"}:
        return "idle"
    return "ok"


def reconcile(
    *,
    uc_query: Callable[[str], list[dict]],
    pg_query: Callable[[str], list[dict]],
    catalog: str,
    schema: str,
    mvm_catalog: str | None = None,
    governed_owned: bool = True,
    shift_id: str | None = None,
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
    lag_guard_seconds: int = DEFAULT_LAG_GUARD_SECONDS,
    tolerance_fraction: float = DEFAULT_TOLERANCE_FRACTION,
    now: datetime | None = None,
) -> dict:
    """Assemble the full reconciliation payload.

    Both query callables are injected so this stays unit-testable without a
    warehouse or a Postgres endpoint. Each source is independently fault
    tolerant: if Lakebase is unreachable we still return the analytics-side
    counts (marked ``unknown``) rather than failing the whole panel.
    """
    start, end = window_bounds(window_minutes, lag_guard_seconds, now)
    warnings: list[str] = []

    existing: set[str] = set()
    try:
        rows = uc_query(build_existing_tables_sql(catalog, [schema]))
        existing = {str(r.get("table_name", "")).lower() for r in rows or []}
    except Exception as exc:  # noqa: BLE001 - degrade, never fail the panel
        warnings.append(f"table discovery failed: {exc}"[:200])

    # ── analytics side (bronze / gold / rejects / dqx) ────────────────────
    bronze: dict[str, int | None] = {}
    gold: dict[str, int | None] = {}
    rejected: dict[str, int | None] = {}
    dqx: dict[str, int | None] = {}
    sql = build_uc_counts_sql(catalog, schema, start, end, existing, shift_id=shift_id)
    if sql:
        try:
            for r in uc_query(sql) or []:
                bucket = {
                    "bronze": bronze, "gold": gold,
                    "rejected": rejected, "dqx": dqx,
                }.get(str(r.get("layer")))
                if bucket is not None:
                    bucket[str(r.get("entity"))] = _as_int(r.get("n"))
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"analytics counts failed: {exc}"[:200])

    # ── live side (Lakebase) ──────────────────────────────────────────────
    lakebase: dict[str, int | None] = {}
    lakebase_ok = True
    try:
        for r in pg_query(build_lakebase_counts_sql(start, end, shift_id=shift_id)) or []:
            lakebase[str(r.get("entity"))] = _as_int(r.get("n"))
    except Exception as exc:  # noqa: BLE001
        lakebase_ok = False
        warnings.append(f"live-path counts unavailable: {exc}"[:200])

    entities = []
    for e in ENTITIES:
        entities.append({
            "key": e.key,
            "label": e.label,
            **classify_entity(
                lakebase=lakebase.get(e.key) if lakebase_ok else None,
                bronze=bronze.get(e.key),
                gold=gold.get(e.key),
                rejected=rejected.get(e.key, 0),
                tolerance_fraction=tolerance_fraction,
            ),
            # DQX never changes a count, so an unreadable value degrades to 0
            # rather than to unknown — and the UI always renders a number.
            "dqx_flagged": dqx.get(e.key) or 0,
        })

    # ── governed model (freshness / population, not count parity) ─────────
    # Only report the governed model when THIS environment populates it. The MVM
    # schemas are catalog-level with no environment suffix, so two environments
    # sharing a catalog share one physical governed model — and an environment
    # whose streaming-silver writer is off would otherwise present the OTHER
    # environment's rows as its own. That failed silently in practice: prod read
    # a governed OEE of 74.4 against its own live 74.3 and looked like a passing
    # cross-plane check, when prod writes no governed rows at all.
    governed: list[dict] = []
    oee = {"live": None, "governed": None, "delta": None, "status": "unknown"}
    if mvm_catalog and governed_owned:
        mvm_schemas = sorted({g.schema for g in GOVERNED_TABLES})
        by_schema: dict[str, set[str]] = {s: set() for s in mvm_schemas}
        try:
            for r in uc_query(build_existing_tables_sql(mvm_catalog, mvm_schemas)) or []:
                sch = str(r.get("table_schema", "")).lower()
                if sch in by_schema:
                    by_schema[sch].add(str(r.get("table_name", "")).lower())
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"governed model discovery failed: {exc}"[:200])

        gsql = build_governed_sql(mvm_catalog, by_schema)
        counts: dict[str, dict] = {}
        if gsql:
            try:
                for r in uc_query(gsql) or []:
                    counts[str(r.get("key"))] = r
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"governed model counts failed: {exc}"[:200])

        for g in GOVERNED_TABLES:
            row = counts.get(g.key)
            n = _as_int(row.get("n")) if row else None
            governed.append({
                "key": g.key,
                "label": g.label,
                "schema": g.schema,
                "table": g.table,
                "kind": g.kind,
                "incident_driven": g.incident_driven,
                "rows": n,
                "last_ts": str(row.get("last_ts")) if row and row.get("last_ts") else None,
                # An incident table at zero is correct at steady state, so it is
                # reported as expected-empty rather than as a problem.
                "status": (
                    "absent" if row is None
                    else "unknown" if n is None
                    else "expected_empty" if (n == 0 and g.incident_driven)
                    else "empty" if n == 0
                    else "populated"
                ),
            })

        live_oee = governed_oee = None
        try:
            rows = uc_query(build_governed_oee_sql(mvm_catalog))
            governed_oee = (rows or [{}])[0].get("oee")
        except Exception:  # noqa: BLE001
            pass
        if lakebase_ok:
            try:
                rows = pg_query(build_lakebase_oee_sql(shift_id))
                live_oee = (rows or [{}])[0].get("oee")
            except Exception:  # noqa: BLE001
                pass
        # _as_float rather than float(): a non-numeric value must not raise out of
        # reconcile and take down the counts, which are independently valid.
        oee = compare_oee(_as_float(live_oee), _as_float(governed_oee))
    elif mvm_catalog:
        # The governed tables exist in the catalog but belong to another
        # environment. Report the live figure we DO own and withhold the
        # comparison, rather than scoring ourselves against someone else's data.
        live_oee = None
        if lakebase_ok:
            try:
                rows = pg_query(build_lakebase_oee_sql(shift_id))
                live_oee = (rows or [{}])[0].get("oee")
            except Exception:  # noqa: BLE001
                pass
        live_val = _as_float(live_oee)
        oee = {
            "live": round(live_val, 2) if live_val is not None else None,
            "governed": None,
            "delta": None,
            "status": "not_enabled",
        }

    total_unexplained = sum(e["unexplained"] for e in entities)
    overall = _overall_status(entities, oee)

    return {
        "window": {
            "minutes": window_minutes,
            "lag_guard_seconds": lag_guard_seconds,
            # Absolute bounds, identical for both engines — echoed so the panel
            # can state exactly what was compared.
            "start": start.isoformat(),
            "end": end.isoformat(),
        },
        # Surfaced so the panel can show WHICH traffic it compared — the shared
        # Lakebase makes an unscoped reading meaningless.
        "shift_id": shift_id,
        "entities": entities,
        "governed": governed,
        # False when the governed model lives in a catalog this environment
        # reads but does not write, so the panel can say so instead of showing
        # another environment's rows as evidence of its own pipeline.
        "governed_owned": governed_owned,
        "oee_check": oee,
        "dqx_total": sum(dqx.values()),
        "total_unexplained": total_unexplained,
        "overall": overall,
        "live_path_available": lakebase_ok,
        "warnings": warnings,
    }
