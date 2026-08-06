"""Backfill routine quality checks and shift events for the live shift.

Why this is needed
------------------
The producer emits both tables only during an injected incident: QC rows require
an active ``quality_deviation``/``contamination`` scenario on a degraded machine,
and event rows are written on incident start and resolve. In steady state the
producer writes neither, so a workspace that has never had an incident injected
has an empty ``quality_checks_bronze`` and three rows in ``event_log_bronze``.

That is defensible for the live demo — a real line does not break every minute —
but it leaves two specialists with nothing to reason over. The Quality
Specialist cannot cite a reject rate that does not exist, and
``retrieve_similar_cases`` cannot find a precedent among three events. Both then
answer honestly and vaguely, and the evaluation reads it as low confidence.

What this writes
----------------
Routine QC sampling (every ``--qc-interval-minutes`` per checked parameter) and a
plausible spread of shift events across the trailing window, both timestamped
inside the live shift so they line up with the telemetry and machine-state rows
the agent reads alongside them. Events carry the outcome columns
(``resolution_minutes``, ``outcome_oee_impact``, ``outcome_description``) that
``event_log_bronze`` has and the gold table drops, because "what fixed it and how
long it took" is the whole point of retrieving a similar case.

This is demo data and says so: every generated row carries a ``BF`` id prefix, so
it can be told apart from producer-written rows and counted for idempotency.

Usage
-----
    python scripts/backfill_quality_and_events.py --dry-run
    python scripts/backfill_quality_and_events.py --hours 8

Re-running is a no-op unless ``--force`` (append) or ``--replace`` (regenerate)
is passed. Event ids are positional, so appending a second run duplicates every
one of them — prefer ``--replace`` when the templates have changed.

``--replace`` issues a delete-only commit against the ``*_bronze`` streaming
source, which the quality gate tolerates because it reads with
``ignoreDeletes`` (see ``pipelines/streaming_quality_sync.py``). Rows already
promoted to gold stay there; they are not retracted.
"""
from __future__ import annotations

import argparse
import os
import random
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# Databricks exec's a job's script through ``compile()``, which leaves no
# ``__file__`` global, so fall back to ``sys.argv[0]`` to locate scripts/.
# Same pattern as init_realtime_uc.py and bootstrap_reference_data.py.
_script_dir = os.path.dirname(os.path.abspath(globals().get("__file__") or sys.argv[0]))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

from _uc_sql import StmtCursor  # noqa: E402

BACKFILL_PREFIX = "BF"

# (machine_id, parameter, spec_min, spec_max, nominal, unit)
# Mirrors src/data_generation/quality_generator.QUALITY_CHECKS_DEF, extended to
# the machines a bottling line actually gates on. Every machine_id here exists
# in machine_registry — a QC row for a machine the agent cannot find in the
# registry reads as corrupt data rather than as a quality signal.
QC_PARAMETERS: list[tuple[str, str, float, float, float]] = [
    ("FILLER", "fill_volume_ml", 325.0, 335.0, 330.0),
    ("CAPPER", "cap_torque_nm", 1.5, 2.5, 2.0),
    ("LABELLER", "label_alignment_mm", 0.2, 2.0, 0.5),
    ("CHECKWEIGHER", "case_weight_g", 7800.0, 8100.0, 7920.0),
    ("PASTEURIZER", "pu_value", 15.0, 25.0, 20.0),
    ("BOTTLE_WASHER", "caustic_concentration_pct", 1.8, 2.6, 2.2),
]

# (machines, event_type, severity, root_cause, description, corrective_action,
#  duration_minutes, oee_impact_points)
#
# `machines` is the set the event can plausibly occur on, and it is not
# decoration. Assigning a machine at random put "Cap chute misfeed stopped the
# capper" on the PASTEURIZER, and since retrieve_similar_cases matches on this
# text, an operator asking about the capper was shown a capper fault recorded
# against a pasteuriser. Incoherent history is worse than no history: it reads
# as authoritative precedent.
#
# Weighted toward the short micro-stops that dominate a real shift; the long
# breakdowns are what a similar-case lookup is actually reaching for.
EVENT_TEMPLATES: list[tuple[tuple[str, ...], str, str, str, str, str, float, float]] = [
    (("CONVEYOR_1", "CONVEYOR_2", "EMPTY_INSPECTOR"),
     "micro_stop", "low", "bottle_jam",
     "Bottle jam cleared at infeed guide rail",
     "Cleared jam, adjusted guide rail spacing by 2mm", 3.0, 0.4),
    (("CAPPER",),
     "micro_stop", "low", "cap_misfeed",
     "Cap chute misfeed stopped the capper",
     "Cleared chute, verified cap orientation sensor", 4.0, 0.5),
    (("LABELLER",),
     "minor_stop", "medium", "label_web_break",
     "Label web break during reel changeover",
     "Spliced web, reduced tension setpoint to 38N", 11.0, 1.6),
    (("FILLER",),
     "minor_stop", "medium", "starvation_upstream",
     "Filler starved by depalletiser slowdown",
     "Restarted depalletiser, cleared pallet skew fault", 9.0, 1.3),
    (("FILLER", "CAPPER"),
     "machine_breakdown", "high", "bearing_wear",
     "Carousel drive bearing overheated and tripped",
     "Replaced bearing, regreased drive, ran 20min proving", 47.0, 6.8),
    (("CONVEYOR_1", "CONVEYOR_2", "CONVEYOR_3", "CONVEYOR_4"),
     "machine_breakdown", "high", "drive_fault",
     "VFD overcurrent trip on conveyor drive",
     "Reset drive, tightened terminal, logged for PM review", 26.0, 3.9),
    (("FILLER",),
     "quality_hold", "high", "fill_volume_drift",
     "Fill volume drifted low, batch held pending QA",
     "Recalibrated flowmeter, released after 3 in-spec checks", 33.0, 4.6),
    (("FILLER", "LABELLER"),
     "changeover", "low", "sku_change",
     "Planned SKU changeover to 500ml variant",
     "Completed changeover, first-article check passed", 22.0, 3.1),
    (("CHECKWEIGHER",),
     "sensor_failure", "medium", "sensor_drift",
     "Checkweigher load cell reading unstable",
     "Re-zeroed load cell, scheduled replacement in next PM", 14.0, 2.0),
    (("FILLER",),
     "cip_cycle", "low", "scheduled_cip",
     "Scheduled CIP cycle on filler bowl",
     "CIP completed, rinse conductivity within limits", 45.0, 6.2),
    # The templates above cover 9 of the line's 19 machines. retrieve_similar_cases
    # filters to resolved events with a recorded outcome, so for the other ten it
    # had nothing to return at all — including DEPALLETIZER, where a diagnosis
    # request fell back to the current shift's own open incidents and the analyst
    # reported LOW confidence for want of a precedent. Each of these names one
    # machine rather than a set, because rng.choice over a set leaves coverage to
    # chance and the point is that every machine has a case on record.
    (("DEPALLETIZER",),
     "machine_breakdown", "high", "pallet_skew",
     "Pallet skew tripped the depalletiser head safety interlock",
     "Squared pallet, re-taught layer height, resumed after interlock reset", 31.0, 4.4),
    (("DEPALLETIZER",),
     "minor_stop", "medium", "layer_sheet_jam",
     "Slip sheet lifted with the layer and jammed the sweep",
     "Removed sheet, increased vacuum dwell by 0.3s", 8.0, 1.1),
    (("UNCASER",),
     "minor_stop", "medium", "case_jam",
     "Case jammed at uncaser infeed dead plate",
     "Cleared case, adjusted dead plate gap to 4mm", 10.0, 1.4),
    (("BOTTLE_WASHER",),
     "machine_breakdown", "high", "caustic_temp_low",
     "Caustic tank fell below wash temperature and the cycle aborted",
     "Restored steam supply, reheated tank, revalidated first 20 bottles", 38.0, 5.4),
    (("PASTEURIZER",),
     "quality_hold", "high", "pu_deviation",
     "PU value tracked below target, batch held pending QA",
     "Corrected zone 2 setpoint, released after PU back in range", 29.0, 4.1),
    (("CASE_PACKER",),
     "minor_stop", "medium", "carton_misfeed",
     "Carton magazine misfeed halted the packer",
     "Refilled magazine, squared stack, adjusted sucker timing", 12.0, 1.7),
    (("CASE_SEALER",),
     "micro_stop", "low", "tape_break",
     "Tape head break on the case sealer",
     "Reloaded tape roll, cleaned blade, verified seal quality", 5.0, 0.7),
    (("CODER",),
     "sensor_failure", "medium", "print_registration",
     "Coder registration sensor missed bottles intermittently",
     "Cleaned lens, re-taught trigger threshold", 13.0, 1.8),
    (("SHRINK_WRAPPER",),
     "machine_breakdown", "high", "heater_element",
     "Shrink tunnel heater element failed open",
     "Replaced element, ramped tunnel, checked pack integrity", 42.0, 6.0),
    (("STRETCH_WRAPPER",),
     "minor_stop", "medium", "film_break",
     "Stretch film web broke mid-pallet",
     "Re-threaded film, lowered pre-stretch ratio to 220%", 9.0, 1.2),
    (("PALLETIZER",),
     "machine_breakdown", "high", "gripper_fault",
     "Palletiser gripper lost pneumatic pressure and dropped a layer",
     "Replaced solenoid valve, leak-tested circuit, re-ran layer", 35.0, 5.0),
]


@dataclass
class Target:
    catalog: str
    schema: str
    shift_id: str
    warehouse_id: str

    @property
    def fqn(self) -> str:
        return f"{self.catalog}.{self.schema}"


def _q(value: str) -> str:
    """Single-quote a string for SQL, escaping embedded quotes."""
    return "'" + str(value).replace("'", "''") + "'"


def generate_quality_rows(
    shift_id: str, start: datetime, end: datetime, interval_min: int, rng: random.Random
) -> list[dict]:
    """One QC row per parameter per interval, with a drift excursion seeded in.

    A uniform ~3% failure rate produces data that is technically populated and
    analytically inert — nothing to find, so nothing to say. The excursion gives
    the Quality Specialist a real signal: one parameter walks out of spec for a
    stretch in the back half of the window, which is the shape of the question
    it is actually asked ("is fill volume drifting?").
    """
    drift_param = rng.choice(QC_PARAMETERS)
    drift_start = start + (end - start) * 0.62
    drift_end = drift_start + timedelta(minutes=75)

    rows: list[dict] = []
    seq = 0
    t = start
    while t <= end:
        for machine_id, parameter, spec_min, spec_max, nominal in QC_PARAMETERS:
            span = spec_max - spec_min
            drifting = (
                (machine_id, parameter) == (drift_param[0], drift_param[1])
                and drift_start <= t <= drift_end
            )
            if drifting:
                # Walk past the upper limit gradually, so the trend is visible
                # in the values and not only in the pass/fail flag.
                progress = (t - drift_start) / (drift_end - drift_start)
                value = nominal + span * (0.35 + 0.55 * progress) + rng.gauss(0, span * 0.03)
            elif rng.random() < 0.03:
                value = nominal + rng.choice([-1, 1]) * span * rng.uniform(0.55, 0.75)
            else:
                value = nominal + rng.gauss(0, span * 0.12)

            seq += 1
            rows.append({
                "check_id": f"{BACKFILL_PREFIX}QC{seq:05d}",
                "shift_id": shift_id,
                "machine_id": machine_id,
                "timestamp": t,
                "parameter": parameter,
                "value": round(value, 3),
                "spec_min": spec_min,
                "spec_max": spec_max,
                "result": "fail" if value < spec_min or value > spec_max else "pass",
            })
        t += timedelta(minutes=interval_min)
    return rows


def generate_event_rows(
    shift_id: str, start: datetime, end: datetime, count: int, rng: random.Random
) -> list[dict]:
    """``count`` events spread over the window, all closed except the last one.

    One open event (no ``timestamp_end``) is deliberate: the shift-briefing
    specialist is asked to surface carryover, and carryover is precisely an
    event with no end timestamp. With every row closed there is nothing to hand
    over and the briefing has to say so.

    Nothing ever resolves it, though. Seeded rows have no producer command
    behind them, so the resolve marker the UI pairs a start row with never
    arrives and Recover has nothing to clear. Left unbounded it becomes a red
    incident from a previous day that no control can clear — which is what the
    operator board showed. The event feed therefore ages an unresolved event
    out after one shift (``STALE_AFTER_MS`` in client/src/lib/eventFeedUtils.ts):
    carryover is defined against the current shift, so this row stops counting
    as active once its shift is over. Keep the two in step if either changes.
    """
    rows: list[dict] = []
    for i in range(count):
        template = EVENT_TEMPLATES[i % len(EVENT_TEMPLATES)]
        machines, etype, severity, root_cause, desc, action, dur, oee_impact = template
        # Spread across the window with jitter, so events do not land on a
        # suspiciously regular cadence.
        frac = (i + 0.5) / count
        t_start = start + (end - start) * frac + timedelta(minutes=rng.uniform(-6, 6))
        duration = round(dur * rng.uniform(0.7, 1.4), 1)
        is_open = i == count - 1
        rows.append({
            "event_id": f"{BACKFILL_PREFIX}EV{i + 1:04d}",
            "shift_id": shift_id,
            "machine_id": rng.choice(machines),
            "event_type": etype,
            "severity": severity,
            "timestamp_start": t_start,
            "timestamp_end": None if is_open else t_start + timedelta(minutes=duration),
            "duration_minutes": None if is_open else duration,
            "root_cause": root_cause,
            "description": desc,
            "corrective_action": "" if is_open else action,
            "resolution_minutes": None if is_open else round(duration * rng.uniform(0.8, 1.1), 1),
            "outcome_oee_impact": None if is_open else round(oee_impact * rng.uniform(0.8, 1.2), 2),
            "outcome_description": "" if is_open else "Line returned to nominal rate after intervention",
        })
    return rows


def _sql_value(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, datetime):
        return f"TIMESTAMP '{value.strftime('%Y-%m-%d %H:%M:%S')}'"
    if isinstance(value, (int, float)):
        return str(value)
    return _q(value)


def _insert(cur: StmtCursor, table: str, rows: list[dict], batch: int = 200) -> int:
    """Multi-row INSERTs in batches. Returns rows written."""
    if not rows:
        return 0
    columns = list(rows[0])
    written = 0
    for i in range(0, len(rows), batch):
        chunk = rows[i:i + batch]
        values = ", ".join(
            "(" + ", ".join(_sql_value(r[c]) for c in columns) + ")" for r in chunk
        )
        cur.execute(f"INSERT INTO {table} ({', '.join(columns)}) VALUES {values}")
        written += len(chunk)
        print(f"    {written}/{len(rows)}")
    return written


def _existing_backfill_rows(cur: StmtCursor, table: str, id_col: str, shift_id: str) -> int:
    cur.execute(
        f"SELECT count(*) FROM {table} WHERE shift_id = {_q(shift_id)} "
        f"AND {id_col} LIKE '{BACKFILL_PREFIX}%'"
    )
    row = cur.fetchone()
    return int(row[0]) if row else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--catalog", default=os.environ.get("UC_CATALOG"))
    ap.add_argument("--schema", default=os.environ.get("UC_SCHEMA"))
    ap.add_argument("--shift-id", default=os.environ.get("SIM_SHIFT_ID", "shift_1"))
    ap.add_argument("--warehouse-id", default=os.environ.get("DATABRICKS_WAREHOUSE_ID"))
    ap.add_argument("--hours", type=float, default=8.0, help="trailing window to fill")
    ap.add_argument("--qc-interval-minutes", type=int, default=15)
    ap.add_argument("--events", type=int, default=24)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="append even if a backfill exists")
    ap.add_argument(
        "--replace", action="store_true",
        help="delete this shift's existing backfill rows first, then write fresh ones",
    )
    args = ap.parse_args()

    missing = [n for n in ("catalog", "schema", "warehouse_id") if not getattr(args, n)]
    if missing:
        print(f"error: missing {', '.join(missing)} (pass --{missing[0]} or set the env var)")
        return 2

    target = Target(args.catalog, args.schema, args.shift_id, args.warehouse_id)
    end = datetime.now(timezone.utc).replace(tzinfo=None)
    start = end - timedelta(hours=args.hours)
    rng = random.Random(args.seed)

    qc_rows = generate_quality_rows(
        target.shift_id, start, end, args.qc_interval_minutes, rng
    )
    event_rows = generate_event_rows(target.shift_id, start, end, args.events, rng)

    fails = sum(1 for r in qc_rows if r["result"] == "fail")
    print(f"target      {target.fqn}  shift={target.shift_id}")
    print(f"window      {start:%Y-%m-%d %H:%M} .. {end:%Y-%m-%d %H:%M} UTC ({args.hours}h)")
    print(f"quality     {len(qc_rows)} rows, {fails} fail ({fails / max(len(qc_rows), 1):.1%})")
    print(f"events      {len(event_rows)} rows, 1 left open as carryover")

    if args.dry_run:
        print("\ndry run — nothing written")
        return 0

    cur = StmtCursor(target.warehouse_id)
    qc_table = f"{target.fqn}.quality_checks_bronze"
    ev_table = f"{target.fqn}.event_log_bronze"

    if args.replace:
        # Event ids are positional (BFEV0001…), so appending a second run
        # duplicates every id. Without a replace path a change to
        # EVENT_TEMPLATES could only ever reach a brand-new environment —
        # which is how ten machines stayed without a single case on record
        # after the templates that cover them were added.
        print(f"\nreplacing existing backfill for {target.shift_id}")
        for table, id_col in ((qc_table, "check_id"), (ev_table, "event_id")):
            removed = _existing_backfill_rows(cur, table, id_col, target.shift_id)
            cur.execute(
                f"DELETE FROM {table} WHERE shift_id = {_q(target.shift_id)} "
                f"AND {id_col} LIKE '{BACKFILL_PREFIX}%'"
            )
            print(f"  {table}: removed {removed}")
    elif not args.force:
        existing = (
            _existing_backfill_rows(cur, qc_table, "check_id", target.shift_id)
            + _existing_backfill_rows(cur, ev_table, "event_id", target.shift_id)
        )
        if existing:
            print(
                f"\n{existing} backfilled rows already present — nothing to do "
                "(--replace to regenerate, --force to append)"
            )
            return 0

    print(f"\nwriting {qc_table}")
    _insert(cur, qc_table, qc_rows)
    print(f"writing {ev_table}")
    _insert(cur, ev_table, event_rows)
    cur.close()
    print("\ndone")
    return 0


if __name__ == "__main__":
    # Only raise SystemExit on a NON-ZERO code. On serverless spark_python_task
    # (IPython-wrapped, environment_version 2) a raised ``SystemExit(0)`` is
    # surfaced as INTERNAL_ERROR / workload failure even though the work
    # succeeded — so a clean run must fall off the end without raising.
    _rc = main()
    if _rc:
        raise SystemExit(_rc)
