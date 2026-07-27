#!/usr/bin/env python3
"""Refresh OPS_* resource IDs in ``app.yaml`` / ``app.dev.yaml`` from the
current bundle deployment.

Typical use: after ``databricks bundle deploy --target line_manager_dev`` the
job + dashboard IDs change. The Databricks App reads them
from env vars at runtime, so we have to write the new IDs back into the
matching ``app.<env>.yaml`` before the next ``databricks apps deploy``.

Usage:
  python3 scripts/refresh_app_yaml_ids.py dev    # patches app.dev.yaml
  python3 scripts/refresh_app_yaml_ids.py prod   # patches app.yaml

The script reads ``databricks bundle summary --target <bundle-target>
--output json`` and updates these env vars in place (preserving comments
and ordering):

  OPS_PRODUCER_JOB_ID            ← jobs.streaming_producer.id
  OPS_SETUP_JOB_ID               ← jobs.solution_accelerator_setup.id
  OPS_LAKEBASE_SYNCER_JOB_ID     ← jobs.streaming_quality_sync.id
  OPS_TTL_JOB_ID                 ← jobs.lakebase_history_ttl.id
  OPS_DATA_QUALITY_DASHBOARD_ID  ← dashboards.line_manager_data_quality.id

Idempotent. Safe to re-run after every bundle deploy.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# NOTE: quality validation runs in the streaming_quality_sync job, so there is no
# separate pipeline resource to refresh here.
ENV_TO_BUNDLE_KEY = {
    "OPS_PRODUCER_JOB_ID":           ("jobs", "streaming_producer"),
    "OPS_SETUP_JOB_ID":              ("jobs", "solution_accelerator_setup"),
    "OPS_LAKEBASE_SYNCER_JOB_ID":    ("jobs", "streaming_quality_sync"),
    "OPS_TTL_JOB_ID":                ("jobs", "lakebase_history_ttl"),
    "OPS_DATA_QUALITY_DASHBOARD_ID": ("dashboards", "line_manager_data_quality"),
}

def _pick_app_yaml(default_path: Path) -> Path:
    """Prefer the gitignored ``*.local.yaml`` sibling when present.

    The committed ``app.yaml`` / ``app.dev.yaml`` ship as SET_ME templates;
    operator-local values (including the refreshed OPS_* IDs we write here)
    belong in the .local.yaml sibling so they never land in git.
    """
    local = default_path.with_suffix(".local.yaml")
    return local if local.is_file() else default_path


ENV_TARGETS = {
    "dev":  ("line_manager_dev",  _pick_app_yaml(ROOT / "app.dev.yaml")),
    "prod": ("line_manager_prod", _pick_app_yaml(ROOT / "app.yaml")),
}


def _bundle_summary(target: str, profile: str | None) -> dict:
    cmd = ["databricks", "bundle", "summary", "--target", target, "--output", "json"]
    if profile:
        cmd += ["--profile", profile]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"bundle summary failed (exit={proc.returncode})")
    return json.loads(proc.stdout)


def _extract_id(summary: dict, kind: str, key: str) -> str | None:
    """Pull the resource ``id`` field from a bundle summary tree.

    Bundle summary JSON shape: ``{"resources": {"jobs": {<key>: {"id": ...}}}}``.
    Some ID fields are ints (jobs) and some are GUID strings (dashboards).
    """
    res = summary.get("resources") or {}
    bucket = res.get(kind) or {}
    item = bucket.get(key) or {}
    val = item.get("id")
    if val is None or val == "":
        return None
    return str(val)


def _patch_yaml(path: Path, env_to_id: dict[str, str]) -> int:
    """Replace ``value: '<old>'`` for known OPS_* env vars in ``path``.

    Block format we target (PyYAML-compatible but parsed line-by-line so
    comments + ordering survive):

        - name: OPS_PRODUCER_JOB_ID
          value: '1234567890'
    """
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    out: list[str] = []
    changes = 0

    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        m = re.match(r"^(\s*)-\s+name:\s+([A-Z_][A-Z0-9_]*)\s*$", line)
        if m and m.group(2) in env_to_id:
            env_name = m.group(2)
            new_val = env_to_id[env_name]
            # Look for the next ``value:`` line (must be the matching one — same
            # block depth + 2 spaces). Don't peek further than 3 lines ahead.
            for j in range(i + 1, min(i + 4, len(lines))):
                vmatch = re.match(r"^(\s+)value:\s+(.*)$", lines[j])
                if vmatch:
                    cur = vmatch.group(2).strip().strip("'\"")
                    if cur != new_val:
                        out[-1:] = [line]
                        # write all intermediate lines, replace the value line
                        for k in range(i + 1, j):
                            out.append(lines[k])
                        out.append(f"{vmatch.group(1)}value: '{new_val}'")
                        i = j  # advance past the value line
                        changes += 1
                    else:
                        for k in range(i + 1, j + 1):
                            out.append(lines[k])
                        i = j
                    break
            else:
                # No value: line within range — bail, treat block as opaque.
                pass
        i += 1

    if changes:
        path.write_text("\n".join(out) + ("\n" if text.endswith("\n") else ""), encoding="utf-8")
    return changes


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("env", choices=["dev", "prod"])
    p.add_argument("--profile", help="Databricks CLI profile (defaults to DEFAULT; override with --profile or DATABRICKS_PROFILE)")
    args = p.parse_args()

    bundle_target, app_yaml_path = ENV_TARGETS[args.env]
    if not app_yaml_path.is_file():
        raise SystemExit(f"Missing {app_yaml_path}")

    profile = args.profile or os.environ.get("DATABRICKS_PROFILE", "DEFAULT")
    summary = _bundle_summary(bundle_target, profile)

    env_to_id: dict[str, str] = {}
    missing: list[str] = []
    for env_name, (kind, key) in ENV_TO_BUNDLE_KEY.items():
        val = _extract_id(summary, kind, key)
        if val is None:
            missing.append(f"{kind}.{key}")
        else:
            env_to_id[env_name] = val

    if missing:
        sys.stderr.write(
            f"WARN: bundle summary missing resources: {', '.join(missing)} — "
            "skipping those (run `databricks bundle deploy --target "
            f"{bundle_target}` first if this is the initial rollout).\n"
        )

    if not env_to_id:
        raise SystemExit("Nothing to patch (no resource IDs resolved).")

    n = _patch_yaml(app_yaml_path, env_to_id)
    print(f"Patched {n} env var(s) in {app_yaml_path.relative_to(ROOT)}:")
    for env_name, val in env_to_id.items():
        print(f"  {env_name} = {val}")


if __name__ == "__main__":
    main()
