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
import re
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
    # Per-domain governed Genie spaces. Each specialist queries only the domain
    # it works in — see agent_server/tools/governed_domain_query.py. A domain
    # left empty simply yields no tool, so the specialist falls back to
    # sql_query rather than being handed one that always errors.
    "genie_space_asset": "GENIE_SPACE_ASSET",
    "genie_space_quality": "GENIE_SPACE_QUALITY",
    "genie_space_inventory": "GENIE_SPACE_INVENTORY",
    "genie_space_production": "GENIE_SPACE_PRODUCTION",
    # Empty means retrieve_similar_cases uses keyword search. Opting in is one
    # line here rather than an edit to app.yaml, because the endpoint bills by
    # the hour and the first deploy that creates it takes far longer than the
    # rest of the deploy put together — see scripts/provision_vector_search.py.
    "vector_search_endpoint": "VS_ENDPOINT",
    # Tells the app whether THIS environment writes the governed MVM tables.
    # They are catalog-level with no environment suffix, so a shared catalog
    # would otherwise let a non-writing environment report another's rows.
    "mvm_silver_enabled": "MVM_SILVER_ENABLED",
    # Fallback tiers. Each role prefers a vendor-specific model (see
    # agent_server/model_roster.py); these are what a role lands on when its
    # preferred model isn't served in this workspace.
    "model_specialist_endpoint": "MODEL_SPECIALIST",
    "model_advanced_endpoint": "MODEL_ADVANCED",
    "model_multi_vendor": "MODEL_MULTI_VENDOR",
    # MODEL_ORCHESTRATOR is handled specially below — it is a legacy pin now,
    # only rendered when the operator sets it explicitly.
}


def _insert_missing_env(path: Path, env_to_val: dict[str, str]) -> list[str]:
    """Append env entries the file does not declare yet, returning their names.

    ``_patch_yaml`` only REPLACES values for keys already present. That makes
    every newly introduced env var a silent no-op for anyone whose gitignored
    ``app.<env>.local.yaml`` was copied from an older template: the deploy still
    prints the intended value, the app never receives it, and it quietly falls
    back to a default.

    Caught live — dev runs streaming silver ON, but its app never received
    MVM_SILVER_ENABLED, so the Reconciliation panel disowned the governed model
    dev was actually writing, while the deploy log said ``MVM_SILVER_ENABLED = 1``.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    # A trailing comment on the name line must not hide the key. It did, and the
    # variable was then appended a second time — two entries for one name in the
    # env list, resolved by whichever the platform reads last.
    name_re = re.compile(r"^\s*-\s+name:\s+([A-Z_][A-Z0-9_]*)\s*(?:#.*)?$")
    present = {m.group(1) for m in map(name_re.match, lines) if m}
    missing = [k for k in env_to_val if k not in present]
    if not missing:
        return []

    start = next((i for i, ln in enumerate(lines) if re.match(r"^env:\s*(?:#.*)?$", ln)), None)
    if start is None:
        # Returning [] here would be indistinguishable from "nothing was
        # missing" — the exact silent no-op this function exists to prevent,
        # reintroduced one level up. Better to stop the deploy than to ship an
        # app that never receives the variables the log says it got.
        raise SystemExit(
            f"render_app_yaml: cannot find the 'env:' block in {path}, so these variables "
            f"would be silently dropped: {', '.join(missing)}. Fix the file's structure — "
            "'env:' must be a top-level key on its own line."
        )

    # The env block runs until the next top-level (unindented, non-comment) key.
    end = len(lines)
    for i in range(start + 1, len(lines)):
        ln = lines[i]
        if ln.strip() and not ln.startswith((" ", "\t", "#")):
            end = i
            break

    indent = "  "
    for i in range(start + 1, end):
        m = re.match(r"^(\s*)-\s+name:", lines[i])
        if m:
            indent = m.group(1)
            break

    at = end
    while at > start + 1 and not lines[at - 1].strip():
        at -= 1

    block: list[str] = []
    for key in missing:
        block.append(f"{indent}- name: {key}")
        block.append(f"{indent}  value: '{env_to_val[key]}'")
    lines[at:at] = block
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return missing


def _known_roles() -> list[str]:
    """Agent role names the roster defines, for validating ``model_roles`` keys."""
    try:
        sys.path.insert(0, str(ROOT))
        from agent_server.model_roster import ROSTER

        return [a.role for a in ROSTER]
    except Exception:  # noqa: BLE001 - rendering must work without app deps
        return []


def _role_overrides(raw: object) -> dict[str, str]:
    """Turn the ``model_roles`` settings block into ``MODEL_<ROLE>`` env vars.

    Per-role picks belong in config, not just in raw env vars: choosing which
    vendor answers which question is a deployment decision, and a customer
    whose workspace serves a better model for one role should be able to say so
    in ``project-settings.local.yaml`` like every other setting.

    An unknown role name is a hard error rather than a warning. The env var it
    would produce is read by nobody, so a typo like ``downtime_analyzer`` would
    otherwise look configured, deploy clean, and quietly do nothing.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise SystemExit("render_app_yaml: model_roles must be a mapping of role -> endpoint")

    known = _known_roles()
    out: dict[str, str] = {}
    for role, endpoint in raw.items():
        if _is_placeholder(endpoint):
            continue
        role_name = str(role).strip()
        if known and role_name not in known:
            raise SystemExit(
                f"render_app_yaml: unknown role {role_name!r} in model_roles. "
                f"Valid roles: {', '.join(sorted(known))}"
            )
        out[f"MODEL_{role_name.upper()}"] = str(endpoint).strip()
    return out


def _remove_env(path: Path, names: set[str]) -> list[str]:
    """Delete env entries named in ``names``, returning the ones removed.

    The mirror image of ``_insert_missing_env``, and needed for the same
    reason: a gitignored ``app.<env>.local.yaml`` copied from an older template
    keeps declaring vars the current settings no longer set. Leaving a stale
    entry in place is not neutral — MODEL_ORCHESTRATOR pins routing *and*
    synthesis to one endpoint, so a leftover copy would quietly override the
    per-role roster on exactly the deployments that have been running longest.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    name_re = re.compile(r"^(\s*)-\s+name:\s+([A-Z_][A-Z0-9_]*)\s*$")
    kept: list[str] = []
    removed: list[str] = []
    i = 0
    while i < len(lines):
        m = name_re.match(lines[i])
        if m and m.group(2) in names:
            removed.append(m.group(2))
            i += 1
            # Drop the entry's indented body (its `value:` / `valueFrom:` line).
            while i < len(lines) and re.match(r"^\s+\S", lines[i]) and not name_re.match(lines[i]):
                i += 1
            continue
        kept.append(lines[i])
        i += 1
    if removed:
        path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return removed


def _is_placeholder(value: object) -> bool:
    """True for anything still carrying an unfilled template token.

    The token can sit anywhere in the value, not just wrap it: settings like
    ``projects/<your-lakebase-project>/branches/main`` are paths with the
    placeholder embedded. An endswith check missed those and rendered the
    literal text straight into the app's environment.
    """
    text = str(value).strip()
    return text == "" or text == "SET_ME" or bool(re.search(r"<[^<>]+>", text))


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
        # YAML booleans render as "True"/"False", but the runtime reads these
        # flags as == "1" (matching the streaming job), so False must become
        # "0" rather than a truthy-looking string.
        if isinstance(val, bool):
            env_to_val[env_name] = "1" if val else "0"
            continue
        env_to_val[env_name] = str(val).strip()

    # Legacy pin. Routing and synthesis now resolve through the roster, which
    # gives them different models — routing wants the cheapest fast one, since
    # every request waits on it, while synthesis output is read verbatim. An
    # operator who sets this explicitly still gets both forced onto one
    # endpoint; leaving it empty (the default) lets the roster choose. It is
    # deliberately NOT defaulted to the specialist endpoint any more, because
    # doing so would silently override the roster on every fresh install.
    orch = prof.get("model_orchestrator_endpoint")
    if orch is not None and not _is_placeholder(orch):
        env_to_val["MODEL_ORCHESTRATOR"] = str(orch).strip()

    # Vector Search is the one setting where "empty" is a real instruction
    # rather than "not filled in yet". The generic loop above skips empty values
    # so a half-finished settings file cannot blank out a working app, but that
    # rule inverted here: clearing the endpoint is how an operator turns the
    # feature — and its hourly bill — OFF, and skipping the empty would leave
    # the old endpoint name in app.yaml and the app still pointed at it.
    #
    # A profile that never mentions the key at all (older local settings) is
    # still left alone; only an explicit empty turns it off.
    if "vector_search_endpoint" in prof:
        vs = str(prof.get("vector_search_endpoint") or "").strip()
        vs = "" if _is_placeholder(vs) else vs
        env_to_val["VS_ENDPOINT"] = vs
        # Derived, never set by hand. This flag only tells the cost panel to
        # bill an idle endpoint; the two drifting apart is how the panel came
        # to invent ~$58/month for an endpoint nobody had provisioned.
        env_to_val["OPS_VS_ENDPOINT_ALWAYS_ON"] = "true" if vs else "false"

    role_env = _role_overrides(prof.get("model_roles"))
    env_to_val.update(role_env)

    if not env_to_val:
        print(f"render_app_yaml: nothing to patch (no filled values for {target!r}).")
        return

    # Roles dropped from model_roles since the last render must lose their env
    # entry, or the app keeps running an override the settings no longer name.
    stale_roles = {f"MODEL_{r.upper()}" for r in _known_roles()} - set(role_env)
    stale = _remove_env(app_yaml, ({"MODEL_ORCHESTRATOR"} | stale_roles) - set(env_to_val))
    n = _patch_yaml(app_yaml, env_to_val)
    added = _insert_missing_env(app_yaml, env_to_val)
    for env_name in stale:
        print(f"render_app_yaml: removed stale {env_name} (roster now picks per role)")
    print(
        f"render_app_yaml: patched {n} env var(s) in {app_yaml.relative_to(ROOT)} "
        f"from {settings_path.name} (bundle_target={target}):"
    )
    for env_name, val in env_to_val.items():
        # Flag the added ones: an env var the file never declared is exactly the
        # case that used to vanish silently.
        suffix = "  (added — was not declared)" if env_name in added else ""
        print(f"  {env_name} = {val}{suffix}")


if __name__ == "__main__":
    main()
