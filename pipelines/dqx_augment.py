"""Optional DQX (Data Quality eXtended) augmentation for streaming_quality_sync.

Purpose
-------
The hand-rolled ``QC_RULES`` in ``streaming_quality_sync.py`` already covers
null / enum / range / non-negative checks on primary keys. DQX earns its keep
for the two categories that are painful in raw SQL:

- **Freshness** (row event-time vs processing time) — needs ``current_timestamp``
  arithmetic per row, cleaner as a declarative DQX check.
- **Cross-table referential integrity** — needs a broadcast join per row,
  expensive to hand-code cleanly. Reserved for after step 3 (Industry Data
  Model) lands ``equipment_register``.

Warning-only mode
-----------------
DQX runs BESIDES the existing QC pipeline, not in front of it. It flags
suspect rows and appends them to a separate audit table
(``<catalog>.<schema>.dqx_warnings``); the existing ``quality_rejects`` sink
and the valid-row path are untouched. This lets us:

- Deploy DQX without any risk to the working streaming path.
- Compare DQX signal against ``quality_rejects`` in the DQ dashboard.
- Promote a DQX check to a drop-rule (i.e. add it to ``QC_RULES``) only
  after we've seen a week of clean signal in dev.

Feature-flag
------------
``DQX_ENABLED=1`` env var opts the streaming job into calling
``apply_dqx_warnings``. Default OFF so the deploy is inert until we
explicitly turn it on.

Wiring into streaming_quality_sync
----------------------------------
Add ONE call inside ``_sink``, right after ``split_valid_invalid``:

    from pipelines.dqx_augment import apply_dqx_warnings, is_enabled
    if is_enabled():
        apply_dqx_warnings(spark, batch_df, name, dqx_sink=<catalog>.<schema>.dqx_warnings)

The wiring is intentionally NOT applied here — the streaming job stays
untouched until we validate DQX end-to-end in dev. See the follow-up task
in the docstring of ``streaming_quality_sync.py``.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CHECKS_YAML = Path(__file__).parent / "dqx_checks.yaml"


def is_enabled() -> bool:
    """Return True when DQX augmentation should run.

    Kept as a helper (not inlined at the call site) so the ``DQX_ENABLED``
    knob has exactly one authoritative reader — easier to swap for a bundle
    variable or feature-flag service later.
    """
    return os.environ.get("DQX_ENABLED", "0") == "1"


def load_checks(table_name: str | None = None) -> list[dict[str, Any]]:
    """Load DQX check definitions from ``dqx_checks.yaml``.

    If ``table_name`` is provided, returns only checks whose ``name`` starts
    with ``<table_name>__`` (the naming convention this file enforces).

    Returns an empty list — not None — when the YAML is missing or empty so
    callers can no-op cleanly. This lets ``apply_dqx_warnings`` be safe to
    call before the YAML has been fleshed out.
    """
    if not CHECKS_YAML.exists():
        logger.warning("DQX checks YAML missing at %s — no checks will run", CHECKS_YAML)
        return []
    import yaml  # deferred: pyyaml is a dev-only extra
    with CHECKS_YAML.open() as f:
        doc = yaml.safe_load(f) or {}
    checks = doc.get("checks", []) or []
    if table_name:
        prefix = f"{table_name}__"
        checks = [c for c in checks if c.get("name", "").startswith(prefix)]
    return checks


def apply_dqx_warnings(
    spark,
    batch_df,
    table_name: str,
    dqx_sink: str,
) -> int:
    """Run DQX checks on a batch DataFrame and append warning rows to the sink.

    Args:
        spark: Active SparkSession (from the driver — DQX needs the same
            session, so pass explicitly rather than creating one here).
        batch_df: The raw bronze batch DataFrame. NOT the pre-QC filtered
            slice — DQX and QC are independent judgements.
        table_name: One of the bronze table short names (``machine_state``,
            ``line_kpi``, etc.). Used to select checks from the YAML.
        dqx_sink: Fully-qualified Delta table for warning rows. Created on
            first write via ``.saveAsTable``; caller is responsible for
            grants + Lakeview widget wiring.

    Returns:
        The number of rows that failed at least one DQX check (for logging).

    Behaviour on error:
        DQX unavailable, YAML malformed, or DQEngine raising — logs and
        returns 0. Never raises. Rationale: DQX is an OBSERVABILITY layer
        that must not take the streaming job down. If DQX breaks, the
        streaming job continues with the hand-rolled QC as the sole gate,
        which is exactly the pre-DQX behaviour.
    """
    checks = load_checks(table_name)
    if not checks:
        return 0

    try:
        # Deferred import — DQX is optional at the pyproject level (see
        # ``[project.optional-dependencies].dqx``) so a base-image deploy
        # without the extra doesn't crash the module import.
        from databricks.labs.dqx.engine import DQEngine
    except ImportError:
        logger.warning(
            "databricks-labs-dqx not installed — skipping DQX augmentation. "
            "Add it to pyproject.toml [project.optional-dependencies].dqx and re-deploy."
        )
        return 0

    try:
        dq = DQEngine(spark)
        # apply_checks_by_metadata annotates each row with a ``_warnings``
        # (or ``_errors`` on older versions) column carrying the failed check
        # names. We filter for non-empty warnings to isolate the flagged rows.
        annotated = dq.apply_checks_by_metadata(batch_df, checks)
    except Exception:
        logger.exception("DQX apply_checks_by_metadata failed on %s — skipping", table_name)
        return 0

    # Handle both column names DQX has used across versions.
    from pyspark.sql import functions as F

    warn_col = "_warnings" if "_warnings" in annotated.columns else "_errors"
    if warn_col not in annotated.columns:
        logger.warning(
            "DQX did not add a _warnings/_errors column on %s — check DQX version",
            table_name,
        )
        return 0

    failed = (
        annotated.filter(F.size(F.col(warn_col)) > 0)
        .withColumn("bronze_table", F.lit(table_name))
        .withColumn("flagged_at", F.current_timestamp())
    )

    n_failed = failed.count()
    if n_failed == 0:
        return 0

    try:
        (failed
         .write
         .mode("append")
         .option("mergeSchema", "true")
         .saveAsTable(dqx_sink))
        logger.info("DQX flagged %d row(s) on %s → %s", n_failed, table_name, dqx_sink)
    except Exception:
        logger.exception("Failed to append DQX warnings to %s", dqx_sink)
        return 0

    return n_failed
