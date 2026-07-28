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
