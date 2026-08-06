"""Type-check every SQL string in the codebase against the real Unity Catalog.

Grep tells you a table name looks wrong. Only the engine tells you a *column*
is wrong, and a wrong column is the failure mode that has cost this project the
most: `similar_case_retriever` selected three columns that do not exist on the
table it queried, and nothing caught it until the tool returned an error at
runtime — where it was reported as an empty result, not a failure.

So this does not pattern-match. It pulls every SQL-shaped string literal out of
the Python AST, substitutes a placeholder for each interpolation, and asks the
warehouse to EXPLAIN it. Databricks resolves tables and columns during analysis,
so an unknown column fails EXPLAIN without executing anything or touching a row.

Placeholders are the limitation worth knowing about: a query whose table name is
itself interpolated cannot be validated, and one whose WHERE clause is built by
string concatenation gets a literal where a value belongs. Those are reported as
SKIPPED rather than passed, so the coverage number is honest.

Usage:
    python scripts/validate_sql_against_uc.py --catalog C --schema S --warehouse-id W
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState

ROOT = Path(__file__).resolve().parents[1]

# config/queries/*.sql are authored against this fixed prefix and rewritten to
# the deployment's real catalog by server/sqlRewrite.ts. Kept in sync by
# tests/agent_server/test_sql_template_prefix.py.
_SQL_TEMPLATE_PREFIX = "main.line_manager_ai_kit."

# A string worth checking mentions both, in that order. Fragments that only say
# "FROM x" are handled separately below.
_IS_QUERY = re.compile(r"\bSELECT\b.*\bFROM\b", re.IGNORECASE | re.DOTALL)
_IS_FRAGMENT = re.compile(r"^\s*(FROM|JOIN)\s+\w", re.IGNORECASE)

# pyformat params (`%(sid)s`) and psycopg/DBAPI markers the SQL engine won't parse.
_PYFORMAT = re.compile(r"%\(\w+\)s")
_QMARK = re.compile(r"(?<![\w:]):\w+")

# Lakebase/Postgres queries live in the same tree and must not be judged against
# Unity Catalog: `ai_serving.*` exists only in Postgres, and `%s` / `::` / `ON
# CONFLICT` are not Databricks SQL. Explaining them here would report a wall of
# table-not-found that says nothing about the UC path.
_POSTGRES_MARKERS = re.compile(
    r"\bai_serving\.|\bai_chatbot\.|ON CONFLICT|RETURNING\b|TIMESTAMPTZ",
    re.IGNORECASE,
)
# Case-sensitive on purpose. Matching `%s` case-insensitively also matches the
# `%S` inside `LIKE '%STORAGE%'`, which quietly excluded the billing queries
# from validation entirely — a coverage hole that looks identical to a pass.
_PSYCOPG_MARKERS = re.compile(r"(?<!%)%s(?![A-Za-z])|::\w")

# Errors that are artefacts of substituting a placeholder, not defects in the
# query. Distinguishing them matters: if a placeholder failure is reported as a
# bug, the real bugs are lost in the noise and the tool gets ignored.
_PLACEHOLDER_NOISE = re.compile(
    r"XPLACEHOLDERX|CAST_INVALID_INPUT|INVALID_FORMAT|CANNOT_PARSE", re.IGNORECASE
)


@dataclass
class Finding:
    path: str
    lineno: int
    status: str  # OK | FAIL | SKIP
    detail: str
    sql: str


def _flatten(node: ast.AST) -> str | None:
    """``node`` as SQL text with interpolations replaced by a placeholder.

    Returns None when the node is not a string expression at all. The
    placeholder is deliberately a bare token: dropped inside quotes it reads as
    a string literal, and dropped bare it reads as an identifier, which is
    enough for the analyzer in both cases.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                parts.append("XPLACEHOLDERX")
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _flatten(node.left), _flatten(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _candidates(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Constant, ast.JoinedStr, ast.BinOp)):
            continue
        text = _flatten(node)
        if not text:
            continue
        if _IS_QUERY.search(text) or _IS_FRAGMENT.match(text):
            out.append((node.lineno, text))
    # A BinOp and its Constant children both match; keep the longest per line.
    best: dict[int, str] = {}
    for lineno, text in out:
        if len(text) > len(best.get(lineno, "")):
            best[lineno] = text
    return sorted(best.items())


def _prepare(sql: str) -> str | None:
    """``sql`` as something the warehouse can analyze, or None if unusable."""
    text = sql.strip().rstrip(";")
    if _POSTGRES_MARKERS.search(text) or _PSYCOPG_MARKERS.search(text):
        return None
    text = _PYFORMAT.sub("'X'", text)
    text = _QMARK.sub("'X'", text)
    # `.format()` templates reach the AST with their braces intact. NULL is the
    # substitution that costs least: it is valid in any comparison, so the rest
    # of the query still gets analyzed instead of dying at a parse error and
    # taking its column references with it.
    text = re.sub(r"\{[A-Za-z_]\w*\}", "NULL", text)
    text = re.sub(r"\bLIMIT\s+NULL\b", "LIMIT 5", text, flags=re.IGNORECASE)
    if _IS_FRAGMENT.match(text):
        text = "SELECT * " + text
    if not re.match(r"^\s*(SELECT|WITH)\b", text, re.IGNORECASE):
        return None
    # An interpolated table name cannot be resolved, so do not pretend to check it.
    if re.search(r"\b(FROM|JOIN)\s+\S*XPLACEHOLDERX", text, re.IGNORECASE):
        return None
    # A placeholder standing in for a whole predicate parses as a bare column
    # reference. Give it a predicate so the rest of the query still gets checked
    # — that is where `WHERE {clause}` queries hide their column bugs.
    text = re.sub(r"\b(WHERE|AND|OR)\s+XPLACEHOLDERX\b", r"\1 1=1", text, flags=re.IGNORECASE)
    text = re.sub(r"\bLIMIT\s+XPLACEHOLDERX\b", "LIMIT 5", text, flags=re.IGNORECASE)
    return text


class Warehouse:
    def __init__(self, warehouse_id: str, catalog: str, schema: str, profile: str | None):
        self._w = WorkspaceClient(profile=profile) if profile else WorkspaceClient()
        self._id = warehouse_id
        self.catalog = catalog
        self.schema = schema

    def query(self, sql: str) -> list[list]:
        r = self._w.statement_execution.execute_statement(
            warehouse_id=self._id, statement=sql, wait_timeout="50s"
        )
        while r.status and r.status.state in (StatementState.PENDING, StatementState.RUNNING):
            time.sleep(1)
            r = self._w.statement_execution.get_statement(r.statement_id)
        if r.status and r.status.state != StatementState.SUCCEEDED:
            raise RuntimeError(r.status.error)
        return (r.result.data_array or []) if r.result else []

    def explain(self, sql: str) -> tuple[bool, str]:
        """(analyzed_cleanly, message). Runs EXPLAIN — no rows are read."""
        stmt = f"EXPLAIN {sql}"
        try:
            r = self._w.statement_execution.execute_statement(
                warehouse_id=self._id, statement=stmt, catalog=self.catalog,
                schema=self.schema, wait_timeout="50s",
            )
            while r.status and r.status.state in (StatementState.PENDING, StatementState.RUNNING):
                time.sleep(1)
                r = self._w.statement_execution.get_statement(r.statement_id)
            if r.status and r.status.state != StatementState.SUCCEEDED:
                return False, str(r.status.error)[:220]
            # Databricks reports analysis errors in the EXPLAIN *output*, not as
            # a failed statement — checking only the status would pass everything.
            rows = (r.result.data_array or []) if r.result else []
            body = " ".join(str(c) for row in rows for c in row)
            if re.search(r"AnalysisException|UNRESOLVED_COLUMN|TABLE_OR_VIEW_NOT_FOUND"
                         r"|cannot be resolved|Error occurred", body, re.IGNORECASE):
                return False, re.sub(r"\s+", " ", body)[:220]
            return True, ""
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            return False, str(exc)[:220]


def _live_schema(wh: "Warehouse") -> dict[str, dict]:
    """{table: {"rows": n, "columns": {name: type}}} for the target schema.

    Row counts are best-effort. Not everything that appears in
    information_schema.columns can be SELECTed: a Vector Search index in the
    same schema answers ``count(*)`` with DATA_SOURCE_NOT_FOUND, and because
    the counts were fetched as one UNION ALL, that single object took down the
    whole validator — which is why this script stopped being run at all.
    """
    cols = wh.query(
        "SELECT table_name, column_name FROM "
        f"{wh.catalog}.information_schema.columns WHERE table_schema = '{wh.schema}'"
    )
    tables: dict[str, dict] = {}
    for table, column in cols:
        tables.setdefault(table, {"rows": -1, "columns": set()})["columns"].add(column)
    if not tables:
        return tables

    countable = {
        t for t, in wh.query(
            "SELECT table_name FROM "
            f"{wh.catalog}.information_schema.tables WHERE table_schema = '{wh.schema}' "
            "AND table_type IN ('MANAGED', 'EXTERNAL', 'VIEW', 'MANAGED_SHALLOW_CLONE')"
        )
    } & set(tables)

    def _count(names: list[str]) -> None:
        counts = wh.query(" UNION ALL ".join(
            f"SELECT '{t}' t, count(*) c FROM {wh.catalog}.{wh.schema}.{t}" for t in names
        ))
        for table, n in counts:
            tables[table]["rows"] = int(n)

    try:
        if countable:
            _count(sorted(countable))
    except Exception:  # noqa: BLE001 - fall back to per-table so one object cannot blind us
        for name in sorted(countable):
            try:
                _count([name])
            except Exception:  # noqa: BLE001 - leave rows at -1, reported as unknown
                pass
    return tables


def check_prompt_claims(wh: "Warehouse") -> list[str]:
    """Every table and column a prompt names must exist and hold rows.

    The prompts are the schema documentation the model actually reads, and they
    are maintained by hand. A column that quietly stops existing turns into SQL
    the model writes confidently and the warehouse rejects — or worse, a table
    that still exists but no longer holds rows, which returns success.
    """
    sys.path.insert(0, str(ROOT))
    from agent_server.prompts._shared import LIVE_DATA_TABLES

    live = _live_schema(wh)
    problems: list[str] = []

    for line in LIVE_DATA_TABLES.splitlines():
        m = re.match(r"^- (\w+)\(([^)]*)\)", line.strip())
        if not m:
            continue
        table, declared = m.group(1), [c.strip() for c in m.group(2).split(",")]
        if table not in live:
            problems.append(f"LIVE_DATA_TABLES names {table}, which does not exist")
            continue
        if live[table]["rows"] == 0:
            problems.append(f"LIVE_DATA_TABLES offers {table} to the model, but it is empty")
        unknown = [c for c in declared if c not in live[table]["columns"]]
        if unknown:
            problems.append(f"{table} has no column(s) {unknown}")

    refs = re.search(r"Reference tables: (.+?)\.\n", LIVE_DATA_TABLES, re.S)
    for name in re.findall(r"\w+", refs.group(1)) if refs else []:
        if name not in live:
            problems.append(f"reference table {name} does not exist")
        elif live[name]["rows"] == 0:
            problems.append(f"reference table {name} is empty")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--catalog", default=os.environ.get("UC_CATALOG"))
    ap.add_argument("--schema", default=os.environ.get("UC_SCHEMA"))
    ap.add_argument("--warehouse-id", default=os.environ.get("DATABRICKS_WAREHOUSE_ID"))
    ap.add_argument("--profile", default=os.environ.get("DATABRICKS_CONFIG_PROFILE"))
    ap.add_argument("--paths", nargs="*", default=["agent_server"])
    args = ap.parse_args()

    if not all([args.catalog, args.schema, args.warehouse_id]):
        print("error: need --catalog, --schema and --warehouse-id")
        return 2

    wh = Warehouse(args.warehouse_id, args.catalog, args.schema, args.profile)
    findings: list[Finding] = []

    files = [p for root in args.paths for p in sorted((ROOT / root).rglob("*.py"))]
    for path in files:
        rel = str(path.relative_to(ROOT))
        for lineno, raw in _candidates(path):
            prepared = _prepare(raw)
            if prepared is None:
                findings.append(Finding(rel, lineno, "SKIP", "interpolated table or not a statement", raw))
                continue
            ok, message = wh.explain(prepared)
            if not ok and _PLACEHOLDER_NOISE.search(message):
                # The substitution broke it, not the query. Reporting this as a
                # pass would be a lie about coverage, so it is a skip.
                findings.append(Finding(rel, lineno, "SKIP", f"placeholder artefact: {message[:80]}", prepared))
                continue
            findings.append(Finding(rel, lineno, "OK" if ok else "FAIL", message, prepared))

    # Standalone .sql files were outside this script's reach entirely, which is
    # how config/queries/ kept 17 files pinned to a catalog nobody has and five
    # of them pointed at tables that no longer exist. They carry the template
    # prefix the Express layer rewrites at runtime, so apply the same rewrite
    # before asking the warehouse.
    for path in sorted((ROOT / "config" / "queries").glob("*.sql")):
        rel = str(path.relative_to(ROOT))
        raw = path.read_text(encoding="utf-8")
        rewritten = raw.replace(_SQL_TEMPLATE_PREFIX, f"{args.catalog}.{args.schema}.")
        prepared = _prepare(rewritten)
        if prepared is None:
            findings.append(Finding(rel, 1, "SKIP", "not a checkable statement", raw))
            continue
        ok, message = wh.explain(prepared)
        if not ok and _PLACEHOLDER_NOISE.search(message):
            findings.append(Finding(rel, 1, "SKIP", f"placeholder artefact: {message[:80]}", prepared))
            continue
        findings.append(Finding(rel, 1, "OK" if ok else "FAIL", message, prepared))

    # Lakeview dashboard datasets were the third blind spot: the QC failures
    # tile shipped querying `quality_checks` (the table is `quality_checks_bronze`)
    # with `result = 'FAIL'` (the values are lowercase), so the tile rendered an
    # error in every workspace it was ever deployed to. The queries carry the
    # same {{UC_CATALOG}}/{{UC_SCHEMA}} placeholders deploy.sh substitutes.
    for path in sorted((ROOT / "dashboards").glob("*.lvdash.json")):
        rel = str(path.relative_to(ROOT))
        try:
            spec = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            findings.append(Finding(rel, 1, "FAIL", f"unparseable dashboard JSON: {e}", ""))
            continue
        for dataset in spec.get("datasets", []):
            name = dataset.get("displayName") or dataset.get("name") or "?"
            raw = "".join(dataset.get("queryLines") or []) or (dataset.get("query") or "")
            if not raw.strip():
                continue
            rewritten = (
                raw.replace("{{UC_CATALOG}}", args.catalog)
                .replace("{{UC_SCHEMA}}", args.schema)
            )
            prepared = _prepare(rewritten)
            if prepared is None:
                findings.append(Finding(f"{rel}[{name}]", 1, "SKIP", "not a checkable statement", raw))
                continue
            ok, message = wh.explain(prepared)
            if not ok and _PLACEHOLDER_NOISE.search(message):
                findings.append(Finding(f"{rel}[{name}]", 1, "SKIP", f"placeholder artefact: {message[:80]}", prepared))
                continue
            findings.append(Finding(f"{rel}[{name}]", 1, "OK" if ok else "FAIL", message, prepared))

    prompt_problems = check_prompt_claims(wh)

    fails = [f for f in findings if f.status == "FAIL"]
    skips = [f for f in findings if f.status == "SKIP"]
    oks = [f for f in findings if f.status == "OK"]

    for f in fails:
        print(f"\nFAIL {f.path}:{f.lineno}")
        print(f"     {re.sub(chr(10), ' ', f.sql)[:190]}")
        print(f"  -> {f.detail}")
    if skips:
        print("\nSKIPPED (not checkable):")
        for f in skips:
            print(f"  {f.path}:{f.lineno}  {re.sub(chr(10), ' ', f.sql)[:110]}")

    if prompt_problems:
        print("\nPROMPT CLAIMS THAT ARE NOT TRUE:")
        for p in prompt_problems:
            print(f"  {p}")

    print(f"\nqueries: {len(oks)} ok, {len(fails)} failing, {len(skips)} skipped"
          f"  |  prompt claims: {len(prompt_problems)} wrong")
    return 1 if (fails or prompt_problems) else 0


if __name__ == "__main__":
    sys.exit(main())
