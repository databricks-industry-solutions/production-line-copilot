"""The Quality Specialist's copy of the ``quality_checks`` schema must be true.

Writing a table's columns into a prompt buys a saved ReAct round and takes on a
debt: the prompt is now a second copy of the DDL, and a wrong copy is worse than
none — it sends the model confidently at a column that isn't there. These tests
read the real DDL out of ``scripts/init_realtime_uc.py`` and compare.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from agent_server.prompts.quality_specialist import (
    LIVE_QUALITY_SCHEMA,
    QUALITY_SPECIALIST_SYSTEM,
)

DDL_SOURCE = Path(__file__).resolve().parents[2] / "scripts" / "init_realtime_uc.py"

# Columns the model was observed inventing against the live app.
HALLUCINATED = ("defect_type", "reject_count", "total_checked", "reject_rate")


def _ddl_columns() -> list[str]:
    """Column names from the CREATE TABLE for the quality checks table.

    Reads ``quality_checks_bronze``, not ``quality_checks``. The unsuffixed
    table was an empty shell nothing ever wrote, and it is no longer created at
    all — bronze is where the producer and the backfill land rows, so bronze is
    the schema the prompt has to be true about.
    """
    source = DDL_SOURCE.read_text()
    # The DDL sits in an f-string, so the file text carries doubled braces.
    match = re.search(
        r"CREATE TABLE IF NOT EXISTS \{+fqn\}+\.quality_checks_bronze \((.*?)\)\s*USING DELTA",
        source,
        re.DOTALL,
    )
    assert match, (
        f"could not find the quality_checks_bronze DDL in {DDL_SOURCE.name}. If "
        "the table moved, this guard is no longer checking anything."
    )
    columns = []
    for line in match.group(1).strip().splitlines():
        name = line.strip().split()
        if name:
            columns.append(name[0].rstrip(","))
    return columns


def test_the_ddl_is_still_where_we_think_it_is():
    assert _ddl_columns(), "parsed an empty column list out of the DDL"


@pytest.mark.parametrize("column", _ddl_columns())
def test_every_real_column_is_named_in_the_prompt(column):
    assert column in LIVE_QUALITY_SCHEMA, (
        f"quality_checks.{column} exists but the Quality Specialist's prompt "
        "does not mention it, so the model cannot know it can be queried."
    )


def test_the_prompt_invents_no_columns():
    """Anything the prompt presents as a column must exist in the DDL."""
    real = set(_ddl_columns())
    listed = re.search(r"- Columns: (.*?)\.\n", LIVE_QUALITY_SCHEMA, re.DOTALL)
    assert listed, "the prompt no longer carries a '- Columns:' line"
    claimed = {c.strip() for c in listed.group(1).replace("\n", " ").split(",") if c.strip()}
    assert claimed <= real, (
        f"the prompt claims columns that quality_checks does not have: "
        f"{sorted(claimed - real)}"
    )
    assert claimed == real, f"the prompt omits real columns: {sorted(real - claimed)}"


@pytest.mark.parametrize("column", HALLUCINATED)
def test_the_columns_the_model_invented_are_called_out(column):
    """Naming them explicitly is what stops the guess, so it has to stay."""
    assert column in LIVE_QUALITY_SCHEMA, (
        f"the prompt no longer tells the model that {column!r} does not exist. "
        "That guess cost a full ReAct round against the deployed app."
    )
    assert column not in set(_ddl_columns()), (
        f"{column} now exists in quality_checks — the prompt says it does not. "
        "Update both."
    )


def test_the_schema_reaches_the_system_prompt():
    assert LIVE_QUALITY_SCHEMA in QUALITY_SPECIALIST_SYSTEM, (
        "LIVE_QUALITY_SCHEMA is defined but never interpolated into the prompt"
    )
