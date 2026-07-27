#!/usr/bin/env python3
"""Read-only prerequisite checker — run before ``./scripts/deploy.sh``.

Usage:
  python3 scripts/preflight.py <dev|prod> [--profile <cli-profile>]

Exits 0 when no FAIL results; 1 when any check FAILs. WARNs never fail the run.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SETTINGS_LOCAL = ROOT / "config" / "project-settings.local.yaml"
_SETTINGS_DEFAULT = ROOT / "config" / "project-settings.yaml"
AGENT_PY = ROOT / "agent_server" / "agent.py"

ENV_TO_BUNDLE_TARGET = {
    "dev": "line_manager_dev",
    "prod": "line_manager_prod",
}

# Mirrors deploy.sh SET_ME guard (app_client_id intentionally omitted).
REQUIRED_CONFIG_KEYS = frozenset(
    {
        "workspace_host",
        "sql_warehouse_id",
        "uc_catalog",
        "uc_schema",
        "producer_client_id",
        "lakebase_pg_host",
        "lakebase_endpoint",
        "zerobus_server_endpoint",
    }
)

MIN_NODE_MAJOR = 22


class Status(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass
class CheckResult:
    name: str
    status: Status
    message: str
    remediation: str = ""


def _load_yaml():
    try:
        import yaml  # type: ignore
    except ImportError:
        print("FAIL  PyYAML not installed — pip install pyyaml", file=sys.stderr)
        raise SystemExit(1)

    path = _SETTINGS_LOCAL if _SETTINGS_LOCAL.is_file() else _SETTINGS_DEFAULT
    if not path.is_file():
        print(f"FAIL  Missing {path}", file=sys.stderr)
        raise SystemExit(1)

    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return path, data


def _find_profile_for_target(data: dict, bundle_target: str) -> tuple[str | None, dict | None]:
    for name, prof in (data.get("profiles") or {}).items():
        if isinstance(prof, dict) and prof.get("bundle_target") == bundle_target:
            return name, prof
    return None, None


def _is_placeholder(value: object) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    if not text:
        return True
    if text == "SET_ME":
        return True
    return text.startswith("<") and text.endswith(">")


def _discover_serving_endpoints(prof: dict | None = None) -> list[str]:
    """Return the model serving endpoint names to validate.

    If the deploy profile configures ``model_*_endpoint`` overrides, validate
    exactly those (so a workspace that serves the models under different names
    doesn't fail on the gpt-5 defaults). Otherwise fall back to the endpoint
    names baked into agent.py.
    """
    if prof:
        configured = {
            str(prof.get(k) or "").strip()
            for k in (
                "model_specialist_endpoint",
                "model_advanced_endpoint",
                "model_orchestrator_endpoint",
            )
        }
        configured = {n for n in configured if n and n != "SET_ME"}
        if configured:
            return sorted(configured)

    if not AGENT_PY.is_file():
        return ["databricks-gpt-5-mini", "databricks-gpt-5"]

    text = AGENT_PY.read_text(encoding="utf-8")
    names: set[str] = set()
    for match in re.finditer(
        r'os\.environ\.get\(\s*["\']MODEL_[^"\']+["\']\s*,\s*["\']([^"\']+)["\']\s*\)',
        text,
    ):
        names.add(match.group(1))
    for match in re.finditer(r'["\'](databricks-gpt-[^"\']+)["\']', text):
        names.add(match.group(1))
    return sorted(names)


def _parse_secret_ref(ref: str) -> tuple[str | None, str | None]:
    """Parse ``{{secrets/<scope>/<key>}}`` into (scope, key)."""
    m = re.fullmatch(r"\{\{secrets/([^/]+)/([^}]+)\}\}", (ref or "").strip())
    if not m:
        return None, None
    return m.group(1), m.group(2)


def _run_cmd(argv: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, check=False)
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode, out.strip()
    except OSError as exc:
        return 127, str(exc)


def _print_result(result: CheckResult) -> None:
    line = f"{result.status.value:<4}  {result.name}: {result.message}"
    print(line)
    if result.status == Status.FAIL and result.remediation:
        print(f"       → {result.remediation}")


def check_local_tooling() -> list[CheckResult]:
    results: list[CheckResult] = []

    if shutil.which("databricks"):
        results.append(CheckResult("databricks CLI", Status.PASS, "found on PATH"))
    else:
        results.append(
            CheckResult(
                "databricks CLI",
                Status.FAIL,
                "not found on PATH",
                "Install the Databricks CLI: https://docs.databricks.com/dev-tools/cli/",
            )
        )

    code, out = _run_cmd(["node", "--version"])
    if code != 0:
        results.append(
            CheckResult(
                "node",
                Status.FAIL,
                "node not found or failed",
                "Install Node.js >= 22 (see package.json engines.node).",
            )
        )
    else:
        version = out.lstrip("v").split(".")[0]
        try:
            major = int(version)
            if major >= MIN_NODE_MAJOR:
                results.append(CheckResult("node", Status.PASS, f"{out} (>= {MIN_NODE_MAJOR})"))
            else:
                results.append(
                    CheckResult(
                        "node",
                        Status.FAIL,
                        f"{out} — need >= {MIN_NODE_MAJOR}",
                        f"Upgrade Node.js to v{MIN_NODE_MAJOR}+ (nvm install {MIN_NODE_MAJOR}).",
                    )
                )
        except ValueError:
            results.append(
                CheckResult(
                    "node",
                    Status.WARN,
                    f"could not parse version from {out!r}",
                )
            )

    if shutil.which("uv"):
        results.append(CheckResult("uv", Status.PASS, "found on PATH"))
    else:
        results.append(
            CheckResult(
                "uv",
                Status.FAIL,
                "not found on PATH",
                "Install uv: https://docs.astral.sh/uv/getting-started/installation/",
            )
        )

    try:
        import psycopg2  # noqa: F401

        results.append(CheckResult("psycopg2", Status.PASS, "importable"))
    except ImportError:
        results.append(
            CheckResult(
                "psycopg2",
                Status.FAIL,
                "not importable",
                "Run: uv sync (or pip install psycopg2-binary) — needed by init_lakebase_role.py.",
            )
        )

    try:
        import databricks.sql  # noqa: F401

        results.append(CheckResult("databricks.sql", Status.PASS, "importable"))
    except ImportError:
        results.append(
            CheckResult(
                "databricks.sql",
                Status.FAIL,
                "not importable",
                "Run: uv sync (or pip install databricks-sql-connector).",
            )
        )

    return results


def check_required_config(profile_name: str | None, prof: dict | None) -> list[CheckResult]:
    if prof is None:
        return [
            CheckResult(
                "project-settings profile",
                Status.FAIL,
                "no profile for bundle target",
                "Add a profile with the matching bundle_target in config/project-settings.local.yaml.",
            )
        ]

    unfilled: list[str] = []
    for key in sorted(REQUIRED_CONFIG_KEYS):
        value = prof.get(key)
        if value is None:
            unfilled.append(f"{profile_name}.{key} (missing)")
            continue
        if _is_placeholder(value):
            unfilled.append(f"{profile_name}.{key} = {value!r}")

    if unfilled:
        return [
            CheckResult(
                "required config",
                Status.FAIL,
                f"{len(unfilled)} placeholder field(s): {', '.join(unfilled[:3])}"
                + (" …" if len(unfilled) > 3 else ""),
                "Copy config/project-settings.yaml → config/project-settings.local.yaml and fill real values.",
            )
        ]
    return [CheckResult("required config", Status.PASS, f"all {len(REQUIRED_CONFIG_KEYS)} required fields filled")]


def _normalize_host(host: str) -> str:
    return host.rstrip("/").lower()


def run_api_checks(
    profile: str,
    prof: dict,
    serving_endpoints: list[str],
) -> tuple[list[CheckResult], bool]:
    """Return (results, sdk_available)."""
    try:
        from databricks.sdk import WorkspaceClient
        from databricks.sdk.errors import NotFound, PermissionDenied
    except ImportError:
        return [
            CheckResult(
                "Databricks SDK",
                Status.WARN,
                "databricks-sdk not importable — skipping workspace API checks",
                "Run: uv sync (or pip install databricks-sdk>=0.81.0).",
            )
        ], False

    results: list[CheckResult] = []
    try:
        w = WorkspaceClient(profile=profile)
    except Exception as exc:
        results.append(
            CheckResult(
                "WorkspaceClient",
                Status.FAIL,
                f"could not create client for profile {profile!r}: {exc}",
                f"Run: databricks auth login --profile {profile}",
            )
        )
        return results, True

    # CLI auth
    try:
        me = w.current_user.me()
        user = getattr(me, "user_name", None) or getattr(me, "display_name", None) or "authenticated"
        results.append(CheckResult("CLI auth", Status.PASS, f"current_user.me() OK ({user})"))
    except Exception as exc:
        results.append(
            CheckResult(
                "CLI auth",
                Status.FAIL,
                f"current_user.me() failed: {exc}",
                f"Run: databricks auth login --profile {profile}",
            )
        )

    expected_host = _normalize_host(str(prof.get("workspace_host") or ""))
    if expected_host:
        try:
            cfg = w.config
            actual_host = _normalize_host(str(getattr(cfg, "host", "") or ""))
            if actual_host and actual_host == expected_host:
                results.append(CheckResult("profile host", Status.PASS, f"matches {expected_host}"))
            elif actual_host:
                results.append(
                    CheckResult(
                        "profile host",
                        Status.WARN,
                        f"CLI profile host {actual_host!r} != project-settings {expected_host!r}",
                        f"Align ~/.databrickscfg [{profile}] host with workspace_host in project-settings.",
                    )
                )
            else:
                results.append(
                    CheckResult(
                        "profile host",
                        Status.WARN,
                        "could not read host from WorkspaceClient config",
                    )
                )
        except Exception as exc:
            results.append(CheckResult("profile host", Status.WARN, f"host check skipped: {exc}"))

    warehouse_id = str(prof.get("sql_warehouse_id") or "").strip()
    if warehouse_id and not _is_placeholder(warehouse_id):
        try:
            wh = w.warehouses.get(warehouse_id)
            state = str(getattr(wh, "state", None) or getattr(wh, "warehouse_state", None) or "unknown")
            if state.upper() == "RUNNING":
                results.append(CheckResult("SQL warehouse", Status.PASS, f"{warehouse_id} exists ({state})"))
            else:
                results.append(
                    CheckResult(
                        "SQL warehouse",
                        Status.WARN,
                        f"{warehouse_id} exists but state={state}",
                        "Deploy can start the warehouse; expect a short delay on first query.",
                    )
                )
        except NotFound:
            results.append(
                CheckResult(
                    "SQL warehouse",
                    Status.FAIL,
                    f"warehouse {warehouse_id!r} not found",
                    "Create a SQL warehouse in the workspace or fix sql_warehouse_id in project-settings.",
                )
            )
        except Exception as exc:
            results.append(
                CheckResult(
                    "SQL warehouse",
                    Status.FAIL,
                    f"warehouse check failed: {exc}",
                    "Verify sql_warehouse_id and your workspace permissions.",
                )
            )

    catalog = str(prof.get("uc_catalog") or "").strip()
    if catalog and not _is_placeholder(catalog):
        try:
            w.catalogs.get(catalog)
            results.append(CheckResult("UC catalog", Status.PASS, f"{catalog!r} accessible"))
        except NotFound:
            results.append(
                CheckResult(
                    "UC catalog",
                    Status.FAIL,
                    f"catalog {catalog!r} not found",
                    f"Create catalog {catalog!r} or request USE CATALOG / ownership grants.",
                )
            )
        except PermissionDenied:
            results.append(
                CheckResult(
                    "UC catalog",
                    Status.FAIL,
                    f"no access to catalog {catalog!r}",
                    f"Request USE CATALOG on {catalog!r} from your workspace admin.",
                )
            )
        except Exception as exc:
            results.append(
                CheckResult(
                    "UC catalog",
                    Status.FAIL,
                    f"catalog check failed: {exc}",
                    "Verify uc_catalog and Unity Catalog permissions.",
                )
            )

    for endpoint in serving_endpoints:
        try:
            w.serving_endpoints.get(endpoint)
            results.append(CheckResult(f"serving endpoint {endpoint}", Status.PASS, "exists"))
        except NotFound:
            results.append(
                CheckResult(
                    f"serving endpoint {endpoint}",
                    Status.FAIL,
                    "not found",
                    f"Provision a model serving endpoint named {endpoint!r} in the workspace.",
                )
            )
        except Exception as exc:
            results.append(
                CheckResult(
                    f"serving endpoint {endpoint}",
                    Status.FAIL,
                    f"check failed: {exc}",
                    f"Verify endpoint {endpoint!r} exists and you have CAN_VIEW.",
                )
            )

    secret_ref = str(prof.get("producer_client_secret_ref") or "").strip()
    scope, key = _parse_secret_ref(secret_ref)
    if not scope:
        results.append(
            CheckResult(
                "producer secret scope",
                Status.WARN,
                f"could not parse producer_client_secret_ref={secret_ref!r}",
                "Expected format: {{secrets/<scope>/<key>}}",
            )
        )
    else:
        try:
            scopes = {s.name for s in w.secrets.list_scopes()}
            if scope in scopes:
                results.append(
                    CheckResult(
                        "producer secret scope",
                        Status.PASS,
                        f"scope {scope!r} exists (key {key!r} not verified)",
                    )
                )
            else:
                results.append(
                    CheckResult(
                        "producer secret scope",
                        Status.WARN,
                        f"scope {scope!r} not in list_scopes()",
                        f"Create secret scope {scope!r} and add key {key!r}, or confirm list permissions.",
                    )
                )
        except Exception as exc:
            results.append(
                CheckResult(
                    "producer secret scope",
                    Status.WARN,
                    f"could not list secret scopes: {exc}",
                    f"Manually confirm scope {scope!r} and key {key!r} exist.",
                )
            )

    genie_id = str(prof.get("genie_space_id") or "").strip()
    if genie_id and genie_id != "SET_ME" and not _is_placeholder(genie_id):
        results.append(
            CheckResult(
                "genie space",
                Status.WARN,
                f"genie_space_id={genie_id!r} configured (existence not verified)",
                "Optional — Genie briefing features need a valid space ID.",
            )
        )

    return results, True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("env", choices=sorted(ENV_TO_BUNDLE_TARGET))
    parser.add_argument(
        "--profile",
        default=None,
        help="Databricks CLI profile (default: $DATABRICKS_PROFILE or DEFAULT)",
    )
    args = parser.parse_args()

    import os

    profile = args.profile or os.environ.get("DATABRICKS_PROFILE", "DEFAULT")
    bundle_target = ENV_TO_BUNDLE_TARGET[args.env]
    settings_path, data = _load_yaml()
    profile_name, prof = _find_profile_for_target(data, bundle_target)
    serving_endpoints = _discover_serving_endpoints(prof)

    print(f"=== preflight: env={args.env} target={bundle_target} profile={profile} ===")
    print(f"    settings: {settings_path.relative_to(ROOT)}")
    if profile_name:
        print(f"    profile block: {profile_name}")
    print(f"    serving endpoints: {', '.join(serving_endpoints)}")
    print()

    all_results: list[CheckResult] = []
    all_results.extend(check_local_tooling())
    all_results.extend(check_required_config(profile_name, prof))

    if prof is not None:
        api_results, _ = run_api_checks(profile, prof, serving_endpoints)
        all_results.extend(api_results)

    print()
    for result in all_results:
        _print_result(result)

    counts = {s: 0 for s in Status}
    for result in all_results:
        counts[result.status] += 1

    print()
    print(
        f"Summary: {counts[Status.PASS]} PASS, {counts[Status.WARN]} WARN, {counts[Status.FAIL]} FAIL"
    )

    if counts[Status.FAIL] > 0:
        print("Preflight FAILED — fix FAIL items before running ./scripts/deploy.sh", file=sys.stderr)
        raise SystemExit(1)

    print("Preflight OK (no FAILs).")
    raise SystemExit(0)


if __name__ == "__main__":
    main()
