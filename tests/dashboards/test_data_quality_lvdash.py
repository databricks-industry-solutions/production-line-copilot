"""Guards for dashboards/data_quality.lvdash.json.

The dashboard previously broke after deploy because Lakeview joined the
``queryLines`` array entries with an empty separator instead of newlines,
producing invalid SQL like ``... AS rowsFROM table``. To make the format
robust to whichever join strategy Lakeview uses, every dataset is required
to be a single self-contained query string (joined → still valid).

The committed file carries ``{{UC_CATALOG}}`` / ``{{UC_SCHEMA}}``
placeholders that ``scripts/sync_project_settings.py --target <bt>``
substitutes per bundle target at deploy time. Tests below tolerate the
placeholders by stubbing them with a safe identifier before checking the
keyword-glue regex.

These tests do not hit Lakeview/SQL warehouse — they parse the JSON only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_DASHBOARD = Path(__file__).resolve().parents[2] / "dashboards" / "data_quality.lvdash.json"

_PLACEHOLDER_STUBS = {
    "{{UC_CATALOG}}": "cat",
    "{{UC_SCHEMA}}": "sch",
}


def _load() -> dict:
    text = _DASHBOARD.read_text()
    for token, stub in _PLACEHOLDER_STUBS.items():
        text = text.replace(token, stub)
    return json.loads(text)


def test_dashboard_file_exists() -> None:
    assert _DASHBOARD.is_file(), f"missing {_DASHBOARD}"


def test_every_dataset_is_a_single_query_line() -> None:
    """Lakeview joins ``queryLines`` with empty string when persisting; multiple
    entries get concatenated without whitespace, breaking SQL keywords like
    ``AS rowsFROM``. Keeping each query as a SINGLE line is robust to either
    join strategy (newline or empty)."""
    d = _load()
    for ds in d["datasets"]:
        lines = ds.get("queryLines", [])
        assert len(lines) == 1, (
            f"dataset {ds['name']} must have exactly 1 queryLines entry "
            f"(found {len(lines)}); multi-line queries break after deploy."
        )


_KEYWORDS_NEED_LEADING_SPACE = (
    "FROM", "WHERE", "GROUP BY", "ORDER BY", "JOIN", "LEFT JOIN", "RIGHT JOIN",
    "UNION ALL", "LIMIT", "HAVING",
)


def test_no_keyword_glued_to_identifier() -> None:
    """Catches regressions like ``... AS rowsFROM table`` (no space before FROM)."""
    d = _load()
    for ds in d["datasets"]:
        q = ds["queryLines"][0]
        for kw in _KEYWORDS_NEED_LEADING_SPACE:
            assert not re.search(rf"[A-Za-z0-9_`]\)?{kw}\b", q), (
                f"dataset {ds['name']} has '{kw}' glued to preceding token: "
                f"{q[:200]}"
            )


def test_widget_field_references_match_dataset_aliases() -> None:
    """When a query renames a column (e.g. ``rows`` → ``row_count``), every
    widget that references it must be updated too — otherwise the widget
    silently shows ``Unable to render visualization`` after deploy.
    """
    d = _load()
    dataset_columns: dict[str, set[str]] = {}
    for ds in d["datasets"]:
        q = ds["queryLines"][0]
        # Pull out top-level aliases via a coarse but pragmatic regex.
        # Catches:  ... AS pass_rate, ... AS dropped_rows_24h, ... AS table_name
        aliases = set(re.findall(r"\bAS\s+([A-Za-z_][A-Za-z0-9_]*)", q, flags=re.I))
        # Plus simple "SELECT col1, col2" (no AS) — too noisy to parse fully,
        # so we conservatively allow any name. Test focuses on alias regressions.
        dataset_columns[ds["name"]] = {a.lower() for a in aliases}

    for page in d.get("pages", []):
        for widget in page.get("layout", []):
            w = widget.get("widget", {})
            for q in w.get("queries", []) or []:
                qq = q.get("query", {})
                ds_name = qq.get("datasetName")
                if not ds_name or ds_name not in dataset_columns:
                    continue
                cols = dataset_columns[ds_name]
                if not cols:
                    continue
                for f in qq.get("fields", []) or []:
                    field = str(f.get("name", "")).lower()
                    # Skip implicit/derived (no field name).
                    if not field:
                        continue
                    # If a dataset has any aliases, widget fields should match
                    # one of them (otherwise it's referencing a renamed column).
                    # We only assert when the alias set looks comprehensive
                    # (i.e. the field name is *almost* in the set under common
                    # transformations like backticks/quotes).
                    if field not in cols and any(
                        c.endswith(field) or field.endswith(c) for c in cols
                    ):
                        assert False, (
                            f"widget {w.get('name')} references field "
                            f"'{field}' which doesn't exactly match any "
                            f"alias in dataset {ds_name}: {sorted(cols)}"
                        )


_REPO = Path(__file__).resolve().parents[2]

# Tables the deploy creates from a variable/f-string rather than a literal
# ``CREATE TABLE <name>``, so the scan below cannot see them. Keep this list
# short and justified — every entry is a hole in the check.
_CREATED_DYNAMICALLY = {
    # Written by the generated streaming_quality_sync RTM job
    # (see scripts/sync_project_settings.py), whose DDL is built from the
    # target schema at render time.
    "quality_rejects",
}


def _tables_the_repo_creates() -> set[str]:
    names: set[str] = set(_CREATED_DYNAMICALLY)
    pattern = re.compile(
        r"CREATE\s+(?:OR\s+REPLACE\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
        r"[^\s(]*?([a-z][a-z0-9_]*)\s*[(\n]",
        flags=re.I,
    )
    for sub in ("scripts", "src", "agent_server"):
        for path in (_REPO / sub).rglob("*"):
            if path.suffix not in (".py", ".sql") or not path.is_file():
                continue
            names.update(m.lower() for m in pattern.findall(path.read_text(encoding="utf-8")))
    return names


def test_every_table_the_dashboard_queries_is_one_the_deploy_creates() -> None:
    """The QC failures tile shipped querying ``quality_checks``; the table the
    producer writes is ``quality_checks_bronze``. Nothing failed at build or
    deploy time — the tile just rendered "Unable to render visualization" in
    every workspace, because dashboard SQL was never checked against the
    schema the deploy actually creates.
    """
    created = _tables_the_repo_creates()
    referenced = set(
        re.findall(r"\{\{UC_CATALOG\}\}\.\{\{UC_SCHEMA\}\}\.([a-z_][a-z0-9_]*)", _DASHBOARD.read_text())
    )
    assert referenced, "dashboard references no UC tables — the regex or the file changed"
    unknown = sorted(referenced - created)
    assert not unknown, (
        f"dashboard queries table(s) the deploy never creates: {unknown}. "
        f"Known tables: {sorted(created)}"
    )


def test_result_comparisons_match_the_values_the_producer_writes() -> None:
    """``quality_checks_bronze.result`` holds lowercase 'pass'/'fail'. A
    ``result = 'FAIL'`` predicate parses, runs, and returns zero rows forever —
    the silent half of the same bug."""
    text = _DASHBOARD.read_text()
    assert "'FAIL'" not in text and "'PASS'" not in text, (
        "dashboard compares `result` against uppercase literals; the producer "
        "writes lowercase 'pass'/'fail'. Use lower(result) = 'fail'."
    )


def test_source_uses_uc_placeholders_not_hardcoded_catalog() -> None:
    """The committed dashboard must reference UC tables via the
    ``{{UC_CATALOG}}`` / ``{{UC_SCHEMA}}`` placeholders so the same file
    works across every bundle target. A hardcoded catalog (like a previous
    workspace's name) ships fine in tests but renders the dashboard inert
    on any target whose UC namespace differs.
    """
    text = _DASHBOARD.read_text()
    assert "{{UC_CATALOG}}" in text, (
        "Templated dashboard must keep {{UC_CATALOG}} placeholder so "
        "sync_project_settings.py --target <bt> can render it."
    )
    assert "{{UC_SCHEMA}}" in text, (
        "Templated dashboard must keep {{UC_SCHEMA}} placeholder."
    )
    # Hardcoded catalog references from prior deploys must not slip back
    # into the committed copy. Append a literal here when we discover one
    # leaking back in.
    for forbidden in ("main.line_manager_streaming",):
        assert forbidden not in text, (
            f"Committed dashboard contains hardcoded {forbidden!r}; replace "
            "with {{UC_CATALOG}}.{{UC_SCHEMA}} so the render step handles it."
        )
    # Catch generic personal-catalog naming leaks: any `..._<word>_catalog.`
    # qualifier means a workspace-specific name slipped past the placeholder
    # substitution. {{UC_CATALOG}} doesn't end in `_catalog.` so it won't trip.
    import re
    matches = re.findall(r"\b[a-z][a-z0-9_]*_catalog\.", text)
    assert not matches, (
        f"Dashboard contains hardcoded catalog qualifiers: {sorted(set(matches))}. "
        "Use {{UC_CATALOG}}.{{UC_SCHEMA}} placeholders instead."
    )
