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
    """Return the serving endpoints that MUST exist, or the deploy is broken.

    Only the fallback tiers qualify. Every agent role prefers a vendor-specific
    model, but a missing one degrades to its tier rather than failing, so the
    tiers are the real hard requirement — see ``_roster_endpoints`` for the
    soft ones.
    """
    configured = {
        str((prof or {}).get(k) or "").strip()
        for k in ("model_specialist_endpoint", "model_advanced_endpoint", "model_orchestrator_endpoint")
    }
    configured = {n for n in configured if n and not _is_placeholder(n)}

    # Union, not either/or. This used to return the configured names alone when
    # there were any, so filling in one field switched off checking of the
    # others: with only the specialist tier set, the advanced roles still
    # resolved to `databricks-gpt-5` at runtime and preflight never asked
    # whether it existed. The deploy passed clean and every advanced role then
    # failed on its first model call.
    defaults = {_runtime_tier_endpoint(tier) for tier in ("specialist", "advanced")}
    return sorted(configured | {d for d in defaults if d})


def _runtime_tier_endpoint(tier: str) -> str:
    """What the app itself would fall back to for ``tier``, asked of the app.

    Importing the roster rather than restating its defaults here: two copies of
    the same constant is how preflight ends up verifying an endpoint the runtime
    stopped using.
    """
    try:
        from agent_server import model_roster

        return str(model_roster._tier_endpoint(tier) or "").strip()
    except Exception:  # noqa: BLE001 - preflight must run without the app importable
        return {"specialist": "databricks-gpt-5-mini", "advanced": "databricks-gpt-5"}.get(tier, "")


def _roster_endpoints() -> list[tuple[str, str]]:
    """Return ``(endpoint, role)`` for each per-role preferred model.

    Absence is a WARN, not a FAIL: the app falls back to the tier endpoint for
    that one role. Reporting them individually is still worth it — a workspace
    that serves none of them runs the whole roster on one vendor, and the
    operator should learn that here rather than from the About tab.
    """
    try:
        sys.path.insert(0, str(ROOT))
        from agent_server.model_roster import ROSTER
    except Exception:  # noqa: BLE001 - preflight must run without app deps
        return []
    return [(a.endpoint, a.role) for a in ROSTER]


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

    # Per-role models. Missing ones only cost that role its preferred vendor.
    required = set(serving_endpoints)
    degraded: list[str] = []
    for endpoint, role in _roster_endpoints():
        if endpoint in required:
            continue
        try:
            w.serving_endpoints.get(endpoint)
        except Exception:  # noqa: BLE001 - NotFound or no CAN_VIEW, same outcome
            degraded.append(f"{role} → {endpoint}")
    if degraded:
        results.append(
            CheckResult(
                "per-role model roster",
                Status.WARN,
                f"{len(degraded)} role(s) will fall back to the tier endpoints: "
                + "; ".join(degraded),
                "Harmless — those roles use model_specialist_endpoint / "
                "model_advanced_endpoint instead. Enable the models in Serving → "
                "Endpoints to run the full multi-vendor roster.",
            )
        )
    else:
        results.append(
            CheckResult("per-role model roster", Status.PASS, "all preferred models served")
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

    # Per-domain governed spaces. Reported as one line rather than four so a
    # fully wired deployment stays quiet, and a partial one names what is
    # missing — those specialists silently drop to sql_query otherwise.
    #
    # Whether "none wired" is news depends entirely on mvm_silver_enabled. With
    # it off there is no governed model to point a space at, so seven
    # SQL-only specialists are the design and warning would be noise. With it on,
    # none wired is the loudest case there is and the earlier version of this
    # check was silent for it — `if wired` skipped the empty list, so an
    # environment where provisioning had never run looked identical to one where
    # it had succeeded.
    domains = ["asset", "quality", "inventory", "production"]
    wired = [
        d for d in domains
        if (lambda v: v and v != "SET_ME" and not _is_placeholder(v))(
            str(prof.get(f"genie_space_{d}") or "").strip()
        )
    ]
    mvm_on = str(prof.get("mvm_silver_enabled", "")).strip().lower() in {"true", "1", "yes"}
    if len(wired) == len(domains):
        results.append(
            CheckResult(
                "governed domain spaces",
                Status.PASS,
                f"all {len(domains)} domain Genie spaces configured",
            )
        )
    elif not mvm_on and not wired:
        results.append(
            CheckResult(
                "governed domain spaces",
                Status.PASS,
                "none — mvm_silver_enabled is off for this environment",
                "By design: no governed model here, so all specialists use "
                "sql_query and the System Map draws them SQL-only. Set "
                "mvm_silver_enabled: true and mvm_catalog to turn it on.",
            )
        )
    else:
        missing = [d for d in domains if d not in wired]
        results.append(
            CheckResult(
                "governed domain spaces",
                Status.WARN,
                f"{len(wired)}/{len(domains)} wired — missing: {', '.join(missing)}",
                "Those specialists fall back to sql_query. Deploy re-runs "
                "provisioning; leave the field as SET_ME to have it recaptured.",
            )
        )

    return results, True


def report_model_coverage(profile: str, prof: dict | None, tiers: list[str]) -> None:
    """Print which model answers for each role here, and whether it is served.

    Choosing a model per role only helps if you can see what your workspace
    actually has. Six vendors is the default roster; a workspace serving four
    of them still works, but the operator should learn that here — before
    deploying — rather than from a fallback notice in the About tab.

    Deliberately never exits non-zero. Missing per-role models are a
    degradation, not a failure, and this is also useful as a read-only "what
    would run?" command.
    """
    roster = _roster_endpoints()
    if not roster:
        print("model roster: unavailable (could not import agent_server.model_roster)")
        return

    overrides = (prof or {}).get("model_roles") or {}
    served: set[str] | None = None
    try:
        from databricks.sdk import WorkspaceClient

        w = WorkspaceClient(profile=profile)
        served = {e.name for e in w.serving_endpoints.list() if e.name}
    except Exception as exc:  # noqa: BLE001
        print(f"    (could not list serving endpoints: {type(exc).__name__}: {exc})")

    print(f"=== model roster: profile={profile} ===")
    print(f"    fallback tiers: {', '.join(tiers)}")
    print()
    print(f"    {'ROLE':<22} {'MODEL':<34} {'SOURCE':<9} STATUS")

    missing: list[str] = []
    vendors: set[str] = set()
    for endpoint, role in roster:
        override = str(overrides.get(role) or "").strip()
        target = override or endpoint
        source = "config" if override else "default"
        if served is None:
            status = "unknown"
        elif target in served:
            status = "served"
            vendors.add(_vendor_of(target))
        else:
            status = "MISSING → falls back to tier"
            missing.append(role)
        print(f"    {role:<22} {target:<34} {source:<9} {status}")

    print()
    if served is None:
        print("    Coverage unknown — could not reach the workspace.")
    elif missing:
        print(
            f"    {len(missing)} of {len(roster)} roles will fall back to the tier "
            f"endpoints: {', '.join(missing)}"
        )
        print("    Harmless, but those roles lose their preferred vendor. Enable the")
        print("    models under Serving → Endpoints, or pin alternatives via model_roles")
        print("    in project-settings to keep the roster multi-vendor.")
    else:
        print(f"    All {len(roster)} roles served — {len(vendors)} vendors active.")


def _vendor_of(endpoint: str) -> str:
    try:
        sys.path.insert(0, str(ROOT))
        from agent_server.model_roster import describe

        return describe(endpoint)[1]
    except Exception:  # noqa: BLE001
        return "unknown"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("env", choices=sorted(ENV_TO_BUNDLE_TARGET))
    parser.add_argument(
        "--profile",
        default=None,
        help="Databricks CLI profile (default: $DATABRICKS_PROFILE or DEFAULT)",
    )
    parser.add_argument(
        "--models",
        action="store_true",
        help="Only report which model each agent role will use, and whether this "
        "workspace serves it. Never fails; run it to see provider coverage.",
    )
    args = parser.parse_args()

    import os

    profile = args.profile or os.environ.get("DATABRICKS_PROFILE", "DEFAULT")
    bundle_target = ENV_TO_BUNDLE_TARGET[args.env]
    settings_path, data = _load_yaml()
    profile_name, prof = _find_profile_for_target(data, bundle_target)
    serving_endpoints = _discover_serving_endpoints(prof)

    if args.models:
        report_model_coverage(profile, prof, serving_endpoints)
        raise SystemExit(0)

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
