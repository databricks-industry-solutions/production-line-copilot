#!/usr/bin/env python3
"""Derive the shared ``app.<env>.yaml`` env values from the deploy target's
``project-settings`` profile.

This makes ``config/project-settings(.local).yaml`` the SINGLE SOURCE OF TRUTH
for the values that previously had to be kept in sync by hand in two files
(project-settings AND app.yaml): UC catalog/schema, the Lakebase host + endpoint,
the sim shift id, and the Genie space id.

``scripts/deploy.sh`` runs this before bundle deploy (after ensuring a gitignored
``app.<env>.local.yaml`` exists), so users only fill values in one place. It is
also safe to run standalone.

Usage:
  python3 scripts/render_app_yaml.py dev   [--settings <project-settings.yaml>]
  python3 scripts/render_app_yaml.py prod  [--settings <project-settings.yaml>]

Idempotent — only rewrites env blocks whose value actually changes, and never
overwrites an app env with a placeholder (SET_ME / <...>) source value.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
# Reuse the robust, comment-preserving ``- name:/value:`` block patcher and the
# ``prefer .local.yaml`` resolver from the sibling refresh script.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from refresh_app_yaml_ids import _patch_yaml, _pick_app_yaml  # noqa: E402

ENV_TO_BUNDLE_TARGET = {
    "dev": "line_manager_dev",
    "prod": "line_manager_prod",
}

DEFAULT_APP_YAML = {
    "dev": ROOT / "app.dev.yaml",
    "prod": ROOT / "app.yaml",
}

# project-settings profile field  →  app.yaml env var name
FIELD_TO_ENV = {
    "uc_catalog": "UC_CATALOG",
    "uc_schema": "UC_SCHEMA",
    "lakebase_pg_host": "PGHOST",
    "lakebase_endpoint": "LAKEBASE_ENDPOINT",
    "sim_shift_id": "SIM_SHIFT_ID",
    "genie_space_id": "GENIE_SPACE_ID",
    "model_specialist_endpoint": "MODEL_SPECIALIST",
    "model_advanced_endpoint": "MODEL_ADVANCED",
    # MODEL_ORCHESTRATOR is handled specially below (falls back to the
    # specialist endpoint when model_orchestrator_endpoint is empty).
}


def _is_placeholder(value: object) -> bool:
    text = str(value).strip()
    return text == "" or text == "SET_ME" or (text.startswith("<") and text.endswith(">"))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("env", choices=["dev", "prod"])
    p.add_argument("--settings", default=None, help="Path to project-settings yaml")
    args = p.parse_args()

    target = ENV_TO_BUNDLE_TARGET[args.env]

    if args.settings:
        settings_path = Path(args.settings)
    else:
        local = ROOT / "config" / "project-settings.local.yaml"
        settings_path = local if local.is_file() else ROOT / "config" / "project-settings.yaml"
    if not settings_path.is_file():
        raise SystemExit(f"Missing project-settings file: {settings_path}")

    with open(settings_path, encoding="utf-8") as f:
        settings = yaml.safe_load(f) or {}

    prof = None
    for candidate in (settings.get("profiles") or {}).values():
        if (candidate or {}).get("bundle_target") == target:
            prof = candidate
            break
    if not prof:
        raise SystemExit(f"No profile with bundle_target={target!r} in {settings_path}")

    app_yaml = _pick_app_yaml(DEFAULT_APP_YAML[args.env])
    if not app_yaml.is_file():
        raise SystemExit(f"Missing {app_yaml}")

    env_to_val: dict[str, str] = {}
    for field, env_name in FIELD_TO_ENV.items():
        val = prof.get(field)
        if val is None:
            continue
        # Never clobber the app env with an unfilled placeholder from settings.
        # genie_space_id is legitimately optional/empty — just skip it then.
        if _is_placeholder(val):
            continue
        env_to_val[env_name] = str(val).strip()

    # The router/orchestrator reuses the specialist (cheap) endpoint unless the
    # operator set an explicit override — so a user who only swaps
    # ``model_specialist_endpoint`` doesn't leave MODEL_ORCHESTRATOR pointing at
    # a non-existent default.
    orch = prof.get("model_orchestrator_endpoint")
    if orch is not None and not _is_placeholder(orch):
        env_to_val["MODEL_ORCHESTRATOR"] = str(orch).strip()
    elif "MODEL_SPECIALIST" in env_to_val:
        env_to_val["MODEL_ORCHESTRATOR"] = env_to_val["MODEL_SPECIALIST"]

    if not env_to_val:
        print(f"render_app_yaml: nothing to patch (no filled values for {target!r}).")
        return

    n = _patch_yaml(app_yaml, env_to_val)
    print(
        f"render_app_yaml: patched {n} env var(s) in {app_yaml.relative_to(ROOT)} "
        f"from {settings_path.name} (bundle_target={target}):"
    )
    for env_name, val in env_to_val.items():
        print(f"  {env_name} = {val}")


if __name__ == "__main__":
    main()
