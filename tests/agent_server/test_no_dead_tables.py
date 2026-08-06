"""No query may target a table that exists but is never written to.

The dev and prod schemas still carry `machine_state`, `line_kpi`, `event_log`
and `telemetry` from an earlier layering. Nothing has ever written a row to any
of them — their Delta history is one version long, the CREATE. The pipeline
lands in `*_bronze` and the quality gate publishes `gold_history_*`.

An empty table is a worse failure than a missing one. `TABLE_OR_VIEW_NOT_FOUND`
surfaces in a trace and someone fixes it; zero rows comes back as a clean
result, the specialist answers from an empty context, and the only symptom is
that confidence drifts down. A whole evaluation run went by before anyone read
far enough into the spans to see it.

`machine_telemetry` never existed at all, which is the tell that these names get
written from memory rather than looked up: it is the name the schema *would*
have used, so it reads correct in review.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
AGENT_SERVER = REPO / "agent_server"

# The Genie space is a second, independent place where table names are handed to
# a model — and it was missed entirely by the first pass of this check. Every
# table it named, every example query it taught, and its join rules were all
# written against the dead layer, so the tool could not return data at all while
# still costing ~15 s per call.
GENIE_CONFIG = REPO / "scripts" / "genie_space_config_streaming.py"

# Real, queryable, and permanently empty — plus one that was never real.
DEAD_TABLES = {
    "machine_telemetry",
    "machine_state_enriched",
    "telemetry",
    "machine_state",
    "line_kpi",
    "event_log",
    "quality_checks",
}

# The table token after FROM/JOIN, unqualified or fully qualified. Matching the
# last segment is deliberate: `main.line_manager_ai_kit.event_log` is just as
# dead as `event_log`.
_TABLE_REF = re.compile(
    r"\b(?:FROM|JOIN)\s+(?:[a-zA-Z_][\w]*\.)*([a-zA-Z_][\w]*)", re.IGNORECASE
)


def _sql_string_literals(path: Path) -> list[tuple[int, str]]:
    """(lineno, text) for every string constant in ``path``.

    Read via ``ast`` so prose in comments cannot trip the check — only text the
    program can actually hand to a database counts.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


class TestNoQueryTargetsADeadTable:
    def test_no_sql_in_agent_server_selects_from_an_empty_table(self):
        offenders = []
        for path in sorted(AGENT_SERVER.rglob("*.py")):
            for lineno, text in _sql_string_literals(path):
                for table in _TABLE_REF.findall(text):
                    if table.lower() in DEAD_TABLES:
                        rel = path.relative_to(AGENT_SERVER.parent)
                        offenders.append(f"{rel}:{lineno} -> {table}")

        assert not offenders, (
            "these queries target tables that are never written to, so they "
            "return zero rows instead of failing — use the `*_bronze` or "
            f"`gold_history_*` table instead: {offenders}"
        )

    def test_prompts_do_not_name_a_dead_table_to_the_model(self):
        """A prompt naming a dead table is a query we never see in the diff.

        The model writes the SQL, so a stale name in prompt prose produces the
        same silent-empty result as a stale name in code — except it is invisible
        to the check above, because the offending query never appears in the
        repository at all.
        """
        prompts = AGENT_SERVER / "prompts"
        offenders = []
        for path in sorted(prompts.glob("*.py")):
            for lineno, text in _sql_string_literals(path):
                for table in _TABLE_REF.findall(text):
                    if table.lower() in DEAD_TABLES:
                        offenders.append(f"prompts/{path.name}:{lineno} -> {table}")

        assert not offenders, (
            "prompt text tells the model to query a table that is always "
            f"empty: {offenders}"
        )

    def test_every_prompt_carrying_sql_query_also_carries_the_table_list(self):
        """Naming no table is as harmful as naming the wrong one.

        `shift_briefing` told the model to use `sql_query` for "last 3 events on
        a specific machine" and then listed no tables at all, because it inlines
        its own output template rather than importing the shared block. The
        model does the reasonable thing with no schema in front of it and writes
        `FROM event_log` — zero rows, no error, and a handover that reports a
        clean shift. The check above cannot see it: the dead name is in neither
        the prompt nor the repository, only in the model's guess.
        """
        prompts = AGENT_SERVER / "prompts"
        offenders = []
        for path in sorted(prompts.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            mentions_sql_query = "`sql_query`" in source or "sql_query(" in source
            if not mentions_sql_query:
                continue
            # Either import the shared block or spell the table names out.
            if "LIVE_DATA_TABLES" in source:
                continue
            if "gold_history_" in source or "_bronze" in source:
                continue
            offenders.append(path.name)

        assert not offenders, (
            "these prompts hand the model `sql_query` without telling it which "
            "tables exist, so it falls back to the short names — which are the "
            f"empty ones: {offenders}. Import LIVE_DATA_TABLES from _shared."
        )

    def test_the_genie_space_is_not_built_on_dead_tables(self):
        offenders = []
        for lineno, text in _sql_string_literals(GENIE_CONFIG):
            for table in _TABLE_REF.findall(text):
                if table.lower() in DEAD_TABLES:
                    offenders.append(f"{GENIE_CONFIG.name}:{lineno} -> {table}")
            # Table identifiers are declared, not queried, so FROM/JOIN misses them.
            for match in re.finditer(r"\.(\w+)\"", text):
                if match.group(1).lower() in DEAD_TABLES:
                    offenders.append(f"{GENIE_CONFIG.name}:{lineno} -> {match.group(1)}")

        assert not offenders, (
            "the Genie space is configured against tables that hold no rows, so "
            f"every question it answers is answered from nothing: {offenders}"
        )


def test_the_setup_job_does_not_recreate_the_dead_tables():
    """The durable fix is upstream: stop creating tables nothing writes.

    Every other guard in this file polices the places a dead name might be
    *used*. This one polices the place they came from. While the setup job
    still ran `CREATE TABLE IF NOT EXISTS ...quality_checks`, any query that
    slipped past review found a real, empty, permanently-queryable table and
    returned zero rows. With the DDL gone the same mistake raises
    TABLE_OR_VIEW_NOT_FOUND, which a specialist reports and a human can see.
    """
    ddl = (REPO / "scripts" / "init_realtime_uc.py").read_text()
    created = set(re.findall(r"CREATE TABLE IF NOT EXISTS \{+fqn\}+\.(\w+)", ddl))
    resurrected = sorted(created & DEAD_TABLES)
    assert not resurrected, (
        f"init_realtime_uc.py creates dead table(s) {resurrected}. They are never "
        "written, so any query against them returns zero rows instead of failing. "
        "Write to *_bronze and read from gold_history_* instead."
    )


def test_the_synced_tables_job_does_not_mirror_the_dead_tables():
    """Syncing an empty Delta table provisions a billed pipeline over nothing."""
    src = (REPO / "scripts" / "setup_synced_tables.py").read_text()
    listed = set(re.findall(r'"name":\s*"(\w+)"', src))
    dead = sorted(listed & DEAD_TABLES)
    assert not dead, (
        f"setup_synced_tables.py mirrors dead table(s) {dead} into Lakebase. "
        "Nothing writes them and nothing reads the mirror — the live UI reads "
        "ai_serving.*, which the quality gate upserts directly."
    )
