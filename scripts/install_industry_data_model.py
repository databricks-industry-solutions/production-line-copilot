"""Install a Databricks Industry Data Model (MVM) into Unity Catalog.

This is a compact, self-contained installer for the manufacturing (or any other)
industry data model published in
``databricks-industry-solutions/lakehouse-industry-data-models``.

Why a local installer (vs. the upstream ``data-model-installer.ipynb``)?
  - It fits this repo's serverless setup pattern: like ``init_realtime_uc.py`` and
    ``bootstrap_reference_data.py`` it drives the **SDK Statement Execution API**
    (``_uc_sql.StmtCursor``) against a SQL warehouse — no Spark session, no
    ``pyarrow`` import, safe on serverless ``spark_python_task`` compute.
  - The model definitions are still pulled **live** from the upstream repo, so the
    schema stays authoritative; we only own the (thin) apply loop.

What it does (mirrors the upstream flow, minus the multi-catalog styles we don't
use):
  1. Resolve the latest ``vN`` version folder for the industry.
  2. Download ``schemas/*.sql`` (+ ``metrics/*.sql`` when metric views are on).
  3. Detect the source catalog token and rewrite it to the target catalog.
  4. Split into statements and bucket them into dependency-ordered phases:
     ``catalog -> schema -> table -> fk -> tag -> metric``.
  5. Apply each phase (threaded) with transient-error backoff; ``already exists``
     is ignored so re-runs are idempotent. Failures are retried serially at the
     end. Structural failures are fatal; metric-view source defects only warn.

Usage:
  MVM_CATALOG=line_manager_silver_mvm INDUSTRY=manufacturing MODEL_SIZE=mvm \
  DATABRICKS_HOST=... DATABRICKS_WAREHOUSE_ID=... \
  python scripts/install_industry_data_model.py
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Defaults ───────────────────────────────────────────────────────────────
DEFAULT_INDUSTRY = "manufacturing"
DEFAULT_MODEL_SIZE = "mvm"
DEFAULT_CATALOG = "line_manager_silver_mvm"
DEFAULT_SOURCE_REPO = "databricks-industry-solutions/lakehouse-industry-data-models"
# Pinned to a commit, not a branch. The installer downloads and executes ~114
# tables' worth of third-party DDL at setup time, so tracking `main` would make
# two users installing on different days get different schemas — and an upstream
# column rename would break the silver mapper's expectations with no warning.
# Bump deliberately (and re-run the mapper tests); override with --source-ref.
DEFAULT_SOURCE_REF = "b51150dbeccd61e90c258603fd03c2e7ac78e240"  # 2026-07-24
DEFAULT_THREADS = 16
DEFAULT_DDL_THREADS = 8  # UC throttles ADD CONSTRAINT / SET TAGS at high concurrency
MAX_RETRIES = 6

_IGNORABLE = ("already exists", "already_exists")
_TRANSIENT = (
    "uc_client_exception", "failed to contact the unity catalog",
    "gateway time", "504", "503", "502", "429", "too many requests",
    "deadline exceeded", "temporarily unavailable", "request timed out",
    "service unavailable", "connection reset", "concurrent",
)
# Deterministic source-model defects — never self-heal on retry.
_DETERMINISTIC = (
    "unresolved_column", "unresolved_routine", "unresolved_field",
    "unresolved_using_column", "parse_syntax_error", "invalid_parameter_value",
    "datatype_mismatch", "ambiguous_reference", "invalid_column",
    "cannot_resolve", "wrong_num_args", "unsupported_call",
)


# ── Pure SQL helpers (unit-tested; no network / Spark) ──────────────────────
def split_sql(text: str) -> list[str]:
    """Split a SQL script into statements on top-level ``;``.

    Comment-, string-, backtick- and ``$$``-aware so semicolons inside those do
    not split a statement. Ported from the upstream installer.
    """
    stmts: list[str] = []
    buf: list[str] = []
    i, n = 0, len(text)
    in_line = in_block = in_squote = in_btick = in_dollar = False
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if in_line:
            # Drop line-comment TEXT (mirror block-comment handling below) but
            # keep the terminating newline as a separator. Retaining the comment
            # text here would leave statements prefixed with ``-- header`` lines,
            # so categorize()'s ``startswith("CREATE …")`` checks misfile them
            # into the ``other`` phase — which runs last, after tables/FKs — and
            # every dependent DDL then fails (schema/table not found).
            if c == "\n":
                in_line = False
                buf.append(c)
            i += 1; continue
        if in_block:
            if c == "*" and nxt == "/":
                in_block = False; i += 2; continue
            i += 1; continue
        if in_dollar:
            if c == "$" and nxt == "$":
                buf.append("$$"); in_dollar = False; i += 2; continue
            buf.append(c); i += 1; continue
        if in_squote:
            buf.append(c)
            if c == "'":
                if nxt == "'":
                    buf.append(nxt); i += 2; continue
                in_squote = False
            i += 1; continue
        if in_btick:
            buf.append(c)
            if c == "`":
                in_btick = False
            i += 1; continue
        if c == "-" and nxt == "-":
            in_line = True; i += 2; continue
        if c == "/" and nxt == "*":
            in_block = True; i += 2; continue
        if c == "$" and nxt == "$":
            in_dollar = True; buf.append("$$"); i += 2; continue
        if c == "'":
            in_squote = True; buf.append(c); i += 1; continue
        if c == "`":
            in_btick = True; buf.append(c); i += 1; continue
        if c == ";":
            s = "".join(buf).strip()
            if s:
                stmts.append(s)
            buf = []; i += 1; continue
        buf.append(c); i += 1
    tail = "".join(buf).strip()
    if tail:
        stmts.append(tail)
    return stmts


def categorize(stmt: str) -> str:
    """Bucket a statement into an install phase."""
    u = re.sub(r"\s+", " ", stmt).strip().upper()
    if u.startswith("CREATE CATALOG"):
        return "catalog"
    if u.startswith("CREATE DATABASE") or u.startswith("CREATE SCHEMA"):
        return "schema"
    if (u.startswith("CREATE OR REPLACE TABLE") or u.startswith("CREATE TABLE")
            or u.startswith("CREATE EXTERNAL TABLE")):
        return "table"
    if "ADD CONSTRAINT" in u and "FOREIGN KEY" in u:
        return "fk"
    if "SET TAGS" in u or "UNSET TAGS" in u:
        return "tag"
    if ("CREATE OR REPLACE VIEW" in u or u.startswith("CREATE VIEW")
            or "CREATE MATERIALIZED VIEW" in u):
        return "metric"
    return "other"


def detect_catalog_token(sql_text: str) -> str | None:
    """First ``CREATE CATALOG `x`` token in the source SQL (the model's catalog)."""
    m = re.search(r"CREATE\s+CATALOG\s+(?:IF\s+NOT\s+EXISTS\s+)?`([^`]+)`",
                  sql_text, re.IGNORECASE)
    return m.group(1) if m else None


def rewrite_catalog(text: str, src_token: str | None, dst_token: str) -> str:
    """Rewrite the source catalog token to the target catalog everywhere."""
    if not src_token or src_token == dst_token:
        return text
    return text.replace("`%s`" % src_token, "`%s`" % dst_token)


def build_plan(raw_by_name: dict[str, str], target_catalog: str,
               include_metrics: bool = True) -> "OrderedDict[str, list[str]]":
    """Rewrite + split + categorize all model SQL into ordered phases.

    ``raw_by_name`` maps file name -> raw SQL. Files whose name contains
    ``catalog`` are scanned first for the source catalog token. The original
    ``CREATE CATALOG`` statements are dropped (we create the target catalog
    explicitly, robustly, in ``install``).
    """
    src_token = None
    for name, raw in raw_by_name.items():
        if "catalog" in name.lower():
            src_token = detect_catalog_token(raw)
            if src_token:
                break
    if src_token is None:
        for raw in raw_by_name.values():
            src_token = detect_catalog_token(raw)
            if src_token:
                break

    plan: "OrderedDict[str, list[str]]" = OrderedDict(
        (k, []) for k in ("catalog", "schema", "table", "fk", "tag", "metric", "other")
    )
    for name, raw in raw_by_name.items():
        if not include_metrics and _is_metric_file(name):
            continue
        rewritten = rewrite_catalog(raw, src_token, target_catalog)
        for stmt in split_sql(rewritten):
            cat = categorize(stmt)
            if cat == "catalog":
                continue  # created explicitly by install()
            plan[cat].append(stmt)
    plan["_src_token"] = src_token  # type: ignore[index]
    return plan


def _is_metric_file(name: str) -> bool:
    return "metric" in name.lower()


def _ignorable(msg: str) -> bool:
    m = (msg or "").lower()
    return any(tok in m for tok in _IGNORABLE)


def _transient(msg: str) -> bool:
    m = (msg or "").lower()
    return any(tok in m for tok in _TRANSIENT)


def _deterministic(msg: str) -> bool:
    m = (msg or "").lower()
    return any(tok in m for tok in _DETERMINISTIC)


# ── Model download (GitHub) ─────────────────────────────────────────────────
def _http_get(url: str, token: str = "") -> str:
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "line-manager-data-model-installer")
    if token:
        req.add_header("Authorization", "Bearer %s" % token)
    with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 (fixed https host)
        return r.read().decode("utf-8")


def _gh_contents(repo: str, ref: str, path: str, token: str = "") -> list[dict]:
    url = "https://api.github.com/repos/%s/contents/%s?ref=%s" % (repo, path, ref)
    return json.loads(_http_get(url, token))


def _latest_version(repo: str, ref: str, industry: str, token: str = "") -> str:
    items = _gh_contents(repo, ref, "data-models/%s" % industry, token)
    versions = sorted(
        (it["name"] for it in items
         if it.get("type") == "dir" and re.match(r"^v\d+$", it["name"])),
        key=lambda v: int(v[1:]),
    )
    if not versions:
        raise RuntimeError("No version folders (vN) for industry %s" % industry)
    return versions[-1]


def _list_sql(repo: str, ref: str, folder: str, token: str = "") -> list[tuple[str, str]]:
    try:
        items = _gh_contents(repo, ref, folder, token)
    except Exception:
        return []
    return [(it["name"], it["download_url"]) for it in items
            if it.get("type") == "file" and it["name"].endswith(".sql")]


def fetch_model_sql(repo: str, ref: str, industry: str, model_size: str,
                    include_metrics: bool, token: str = "") -> tuple[dict[str, str], str]:
    """Download all schema (+ optional metric) SQL for the model. Returns
    (raw_by_name, resolved_version)."""
    version = _latest_version(repo, ref, industry, token)
    base = "data-models/%s/%s/%s" % (industry, version, model_size)
    files = _list_sql(repo, ref, base + "/schemas", token)
    if include_metrics:
        files += _list_sql(repo, ref, base + "/metrics", token)
    if not files:
        raise RuntimeError("No SQL files under %s/schemas" % base)
    raw_by_name: dict[str, str] = {}
    for name, url in files:
        raw_by_name[name] = _http_get(url, token)
    return raw_by_name, version


# ── Apply (SDK Statement Execution API) ─────────────────────────────────────
def _exec_with_backoff(exec_one, stmt: str) -> tuple[bool, str | None]:
    """Run one statement, retrying transient UC errors with exp backoff+jitter."""
    delay = 0.5
    last = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            exec_one(stmt)
            return True, None
        except Exception as e:  # noqa: BLE001 — classify below
            last = str(e)
            if _ignorable(last) or not _transient(last) or attempt == MAX_RETRIES:
                return False, last
            time.sleep(delay + random.uniform(0, delay))
            delay = min(delay * 2, 16.0)
    return False, last


def run_phase(name: str, statements: list[str], exec_one, workers: int) -> list[tuple[str, str]]:
    """Run one phase across ``workers`` threads. Returns [(stmt, err)] failures."""
    if not statements:
        print("[%s] nothing to do" % name)
        return []
    failures: list[tuple[str, str]] = []
    t0 = time.time()
    print("[%s] START %d statements, %d workers" % (name, len(statements), workers))

    def _do(stmt: str) -> tuple[str, str] | None:
        ok, err = _exec_with_backoff(exec_one, stmt)
        if ok or _ignorable(err or ""):
            return None
        return (stmt, err or "unknown")

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for fut in as_completed([ex.submit(_do, s) for s in statements]):
            res = fut.result()
            if res:
                failures.append(res)
    print("[%s] DONE in %.1fs (%d failures)" % (name, time.time() - t0, len(failures)))
    return failures


def retry_failed(failures: list[tuple[str, str, str]], exec_one, passes: int = 3):
    """Re-run failed statements serially; transient/ordering errors self-heal.

    Deterministic source defects are skipped (they never succeed on retry)."""
    deterministic = [f for f in failures if _deterministic(f[2])]
    remaining = [f for f in failures if not _deterministic(f[2])]
    if deterministic:
        print("Skipping retry for %d deterministic source defect(s)" % len(deterministic))
    for p in range(1, passes + 1):
        if not remaining:
            break
        print("=== retry pass %d: %d statements ===" % (p, len(remaining)))
        still = []
        for phase, stmt, _ in remaining:
            ok, err = _exec_with_backoff(exec_one, stmt)
            if ok or _ignorable(err or ""):
                continue
            still.append((phase, stmt, err or "unknown"))
        if len(still) == len(remaining):
            remaining = still
            break
        remaining = still
    return deterministic + remaining


def _ensure_catalog(exec_one, catalog: str) -> None:
    """Ensure the target catalog exists + is usable (idempotent).

    Tries ``CREATE CATALOG IF NOT EXISTS``. Many workspaces (e.g. FEVM
    serverless) deny metastore-level ``CREATE CATALOG`` to non-admins, so if
    creation fails we fall back to verifying the catalog already exists and is
    usable (``USE CATALOG``). Only raise when the catalog truly isn't there —
    otherwise every downstream schema/table/FK would fail with a confusing
    ``NO_SUCH_CATALOG`` after churning through thousands of statements.
    """
    ok, err = _exec_with_backoff(exec_one, "CREATE CATALOG IF NOT EXISTS `%s`" % catalog)
    if ok or _ignorable(err or ""):
        return
    try:
        exec_one("USE CATALOG `%s`" % catalog)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            "Target catalog `%s` does not exist and could not be created (%s). "
            "Point --catalog at an existing catalog you own, or ask an admin "
            "for CREATE CATALOG on the metastore." % (catalog, err)
        ) from e
    print("[catalog] `%s` already exists (no CREATE CATALOG privilege — reusing)" % catalog)


def install(plan, exec_one, catalog: str, threads: int, ddl_threads: int,
            include_metrics: bool) -> list[tuple[str, str, str]]:
    """Apply the plan phase-by-phase, retry failures, return unrecoverable ones."""
    failures: list[tuple[str, str, str]] = []

    def do(phase: str, workers: int) -> None:
        for stmt, err in run_phase(phase, plan.get(phase, []), exec_one, workers):
            failures.append((phase, stmt, err))

    print("-" * 60)
    _ensure_catalog(exec_one, catalog)
    do("schema", threads)
    do("table", threads)
    do("fk", ddl_threads)
    do("tag", ddl_threads)
    if include_metrics:
        do("metric", threads)
    if plan.get("other"):
        do("other", threads)

    print("First-pass failures (pre-retry): %d" % len(failures))
    return retry_failed(failures, exec_one)


# ── CLI ─────────────────────────────────────────────────────────────────────
def _env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, default)
    if v is None or v == "":
        raise ValueError("Missing required env var: %s" % name)
    return v


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--catalog", default=os.environ.get("MVM_CATALOG", DEFAULT_CATALOG),
                   help="Target UC catalog for the model (created if absent).")
    p.add_argument("--industry", default=os.environ.get("INDUSTRY", DEFAULT_INDUSTRY))
    p.add_argument("--model-size", default=os.environ.get("MODEL_SIZE", DEFAULT_MODEL_SIZE),
                   choices=["mvm", "ecm"])
    p.add_argument("--source-repo", default=os.environ.get("SOURCE_REPO", DEFAULT_SOURCE_REPO))
    p.add_argument("--source-ref", default=os.environ.get("SOURCE_REF", DEFAULT_SOURCE_REF))
    p.add_argument("--github-token", default=os.environ.get("GITHUB_TOKEN", ""))
    p.add_argument("--no-metrics", action="store_true",
                   help="Skip metric views (schemas + tables + FKs + tags only).")
    p.add_argument("--threads", type=int, default=int(os.environ.get("THREADS", DEFAULT_THREADS)))
    p.add_argument("--host", default=os.environ.get("DATABRICKS_HOST"))
    p.add_argument("--warehouse-id", default=os.environ.get("DATABRICKS_WAREHOUSE_ID"))
    p.add_argument("--app-client-id", default=os.environ.get("APP_CLIENT_ID", ""),
                   help="App service principal granted read access to the model.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    include_metrics = not args.no_metrics
    host = args.host or _env("DATABRICKS_HOST")
    warehouse_id = args.warehouse_id or _env("DATABRICKS_WAREHOUSE_ID")

    print("Installing industry data model")
    print("  industry     : %s" % args.industry)
    print("  model_size   : %s" % args.model_size)
    print("  catalog      : %s" % args.catalog)
    print("  metric views : %s" % include_metrics)
    print("  source       : %s@%s" % (args.source_repo, args.source_ref))

    raw_by_name, version = fetch_model_sql(
        args.source_repo, args.source_ref, args.industry, args.model_size,
        include_metrics, args.github_token,
    )
    print("  version      : %s (%d SQL files)" % (version, len(raw_by_name)))

    plan = build_plan(raw_by_name, args.catalog, include_metrics)
    src_token = plan.pop("_src_token", None)  # type: ignore[arg-type]
    print("  src catalog  : %s -> %s" % (src_token, args.catalog))
    for k in ("schema", "table", "fk", "tag", "metric", "other"):
        if plan.get(k):
            print("    %-8s %d" % (k, len(plan[k])))

    # Serverless-safe SQL exec (SDK Statement Execution API — no pyarrow/Spark).
    _script_dir = os.path.dirname(os.path.abspath(globals().get("__file__") or sys.argv[0]))
    if _script_dir not in sys.path:
        sys.path.insert(0, _script_dir)
    from _uc_sql import StmtCursor

    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service.sql import StatementState

    if host and not os.environ.get("DATABRICKS_HOST"):
        os.environ["DATABRICKS_HOST"] = host
    w = WorkspaceClient()
    _terminal = {StatementState.SUCCEEDED, StatementState.FAILED,
                 StatementState.CANCELED, StatementState.CLOSED}

    def exec_one(sql: str) -> None:
        """Thread-safe single-statement exec against the warehouse."""
        resp = w.statement_execution.execute_statement(
            warehouse_id=warehouse_id, statement=sql, wait_timeout="30s")
        while resp.status and resp.status.state not in _terminal:
            time.sleep(1.0)
            resp = w.statement_execution.get_statement(resp.statement_id)
        state = resp.status.state if resp.status else None
        if state != StatementState.SUCCEEDED:
            err = resp.status.error if resp.status else None
            msg = getattr(err, "message", None) or err or "unknown error"
            raise RuntimeError("SQL failed (%s): %s" % (state, msg))

    ddl_threads = max(4, min(args.threads, DEFAULT_DDL_THREADS))
    final = install(plan, exec_one, args.catalog, args.threads, ddl_threads, include_metrics)

    structural = [f for f in final if f[0] != "metric"]
    print("=" * 64)
    total = sum(len(v) for v in plan.values())
    print("INSTALL SUMMARY %s/%s -> `%s` (%d statements)"
          % (args.industry, args.model_size, args.catalog, total))
    if final:
        print("UNRECOVERABLE: %d (structural=%d, metric=%d)"
              % (len(final), len(structural), len(final) - len(structural)))
        for phase, stmt, err in final:
            print("  [%s] %s" % (phase, " ".join(stmt.split())[:110]))
            print("    -> %s" % (" ".join((err or "").split())[:180]))
    if structural:
        print("INSTALL FAILED: %d structural failure(s) in `%s`"
              % (len(structural), args.catalog))
        return 1
    grant_app_sp_read(exec_one, args.catalog, (args.app_client_id or "").strip())

    if final:
        print("INSTALLED WITH WARNINGS: %d metric-view source defect(s), 0 structural"
              % len(final))
    else:
        print("SUCCESS: %s/%s -> `%s`" % (args.industry, args.model_size, args.catalog))
    return 0


# Schemas the silver mapper actually populates. Only these are granted — the
# model ships 13 schemas, and the app has no reason to read the 9 it never fills.
POPULATED_SCHEMAS = ("asset", "production", "quality", "inventory")


def grant_app_sp_read(exec_one, catalog: str, app_sp: str) -> None:
    """Let the app service principal read the governed model it just installed.

    Granted here rather than in ``init_realtime_uc.py`` because that task runs
    before this one — the schemas don't exist yet at that point. Without these
    grants the governed Genie space and the SQL specialists run as the app SP
    and hit PERMISSION_DENIED, or return an empty result that reads as "the
    governed model has no data". Non-fatal: the model itself is installed and
    usable by humans, so a grant failure is a warning, not a failed install.
    """
    if not app_sp:
        print("NOTE: APP_CLIENT_ID not set — skipping governed-model grants for the app SP.")
        return
    try:
        exec_one(f"GRANT USE CATALOG ON CATALOG `{catalog}` TO `{app_sp}`")
        for schema in POPULATED_SCHEMAS:
            exec_one(f"GRANT USE SCHEMA ON SCHEMA `{catalog}`.`{schema}` TO `{app_sp}`")
            exec_one(f"GRANT SELECT ON SCHEMA `{catalog}`.`{schema}` TO `{app_sp}`")
        print("OK: granted app SP %s… USE SCHEMA + SELECT on %s"
              % (app_sp[:8], ", ".join(POPULATED_SCHEMAS)))
    except Exception as exc:  # noqa: BLE001
        print("WARN: could not grant governed-model read to the app SP (%s). "
              "Governed Genie/SQL answers will come back empty until granted."
              % str(exc)[:160])


if __name__ == "__main__":
    # Only raise SystemExit on a NON-ZERO code. On serverless spark_python_task
    # (IPython-wrapped, environment_version 2) a raised ``SystemExit(0)`` is
    # surfaced as INTERNAL_ERROR / workload failure even though the work
    # succeeded — so a clean run must fall off the end without raising.
    _rc = main()
    if _rc:
        raise SystemExit(_rc)
