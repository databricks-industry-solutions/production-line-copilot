#!/usr/bin/env python3
"""Explain why an environment's MLflow traces are empty.

The failure this diagnoses looks like success from every angle: the deploy
reports complete, the app serves answers, the experiment exists and collects
traces — and every one of them holds no spans, so the MLflow UI shows a
"Enable UC trace storage" banner and "Failed to load chart data".

The cause is always one of a short list, and they are hard to tell apart from
the UI:

* the app is pointed at the bundle's classic experiment rather than the
  purpose-made ``-traces`` one, because provisioning failed and left the
  template's ``valueFrom: experiment`` in place;
* the ``-traces`` experiment exists but was linked without a telemetry
  destination, so it accepts traces and discards the spans;
* it is linked correctly but the app's service principal cannot write to the
  Delta tables or the experiment.

This reports which, and what to do about it. Read-only.

Usage:
  python3 scripts/diagnose_trace_storage.py dev
  python3 scripts/diagnose_trace_storage.py prod --profile <p>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from provision_trace_storage import (  # noqa: E402
    DESTINATION_TAG,
    ENV_TO_APP_YAML,
    LINK_TAG,
    TABLE_TAGS,
    _experiment_name,
    _profile,
)
from refresh_app_yaml_ids import _pick_app_yaml  # noqa: E402

OK, BAD, INFO = "  ok  ", " FAIL ", "      "


def _app_experiment_setting(env: str) -> tuple[str, str]:
    """What the deployed app.yaml actually tells the app to use."""
    path = _pick_app_yaml(ENV_TO_APP_YAML[env])
    lines = path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "- name: MLFLOW_EXPERIMENT_ID":
            for nxt in lines[i + 1 : i + 4]:
                stripped = nxt.strip()
                if stripped.startswith("valueFrom:"):
                    return path.name, "valueFrom:" + stripped.split(":", 1)[1].strip()
                if stripped.startswith("value:"):
                    return path.name, stripped.split(":", 1)[1].strip().strip("'\"")
    return path.name, ""


def diagnose(env: str, settings: Path) -> int:
    import mlflow

    profile = _profile(settings, env)
    name = _experiment_name(profile)
    yaml_name, configured = _app_experiment_setting(env)
    problems: list[str] = []

    print(f"Environment:      {env}")
    print(f"Trace experiment: {name}")
    print(f"App config:       {yaml_name} -> MLFLOW_EXPERIMENT_ID = {configured or '(unset)'}")
    print()

    if configured.startswith("valueFrom:"):
        print(f"{BAD} the app takes its experiment from the bundle, not from trace storage")
        print(f"{INFO} that is the classic experiment; its spans upload to a host the app")
        print(f"{INFO} cannot reach, so traces arrive with nothing in them")
        problems.append(
            "run scripts/provision_trace_storage.py (deploy.sh does this) and redeploy the app"
        )
    elif not configured:
        print(f"{BAD} MLFLOW_EXPERIMENT_ID is not set at all")
        problems.append("run scripts/provision_trace_storage.py and redeploy the app")

    experiment = mlflow.get_experiment_by_name(name)
    if experiment is None:
        print(f"{BAD} {name} does not exist — trace storage was never provisioned")
        problems.append("re-run the deploy; check uc_catalog / sql_warehouse_id are not SET_ME")
        return _report(problems)

    tags = dict(experiment.tags or {})
    print(f"{OK} {name} exists ({experiment.experiment_id})")

    if not tags.get(LINK_TAG):
        print(f"{BAD} it is not linked to Unity Catalog storage")
        problems.append(f"delete experiment {experiment.experiment_id} and re-run the deploy")
    elif not tags.get(DESTINATION_TAG):
        # The dangerous middle state: reports as linked, discards every span.
        print(f"{BAD} linked, but with no telemetry destination — every span is discarded")
        print(f"{INFO} a UC link cannot be undone, so this experiment is scrap")
        problems.append(
            f"delete experiment {experiment.experiment_id}, then re-run the deploy "
            "(it will otherwise fall through to the -2 name)"
        )
    else:
        print(f"{OK} linked to {tags[LINK_TAG]} with a telemetry destination")

    if configured and not configured.startswith("valueFrom:"):
        if configured == experiment.experiment_id:
            print(f"{OK} the app points at this experiment")
        else:
            print(f"{BAD} the app points at {configured}, not {experiment.experiment_id}")
            problems.append("re-run the deploy so app.yaml picks up the right experiment id")

    tables = [tags[t] for t in TABLE_TAGS if tags.get(t)]
    print(f"{OK if tables else BAD} {len(tables)} trace table(s) recorded")
    for table in tables:
        print(f"{INFO} {table}")
    if not tables:
        problems.append("no trace tables named in the experiment tags — the link did not complete")

    spans = _count_spans(settings, env, tables)
    return _report(problems, spans)


def _count_spans(settings: Path, env: str, tables: list[str]) -> tuple[int, str] | None:
    """How many spans are actually stored, and when the newest arrived.

    Provisioning being correct and traces existing are different questions, and
    the first was all this script used to answer — so it could report everything
    healthy about an experiment holding nothing, and then blame the service
    principal's write access when the real answer was that the reader had not
    named a warehouse. Counting settles it.
    """
    span_tables = [t for t in tables if t.endswith("_otel_spans")]
    if not span_tables:
        return None
    try:
        from databricks.sdk import WorkspaceClient

        warehouse = str(_profile(settings, env).get("sql_warehouse_id") or "")
        if not warehouse or warehouse.startswith("SET_ME"):
            return None
        # Auth comes from the environment, the same way the rest of the script
        # reaches the workspace.
        resp = WorkspaceClient().statement_execution.execute_statement(
            warehouse_id=warehouse,
            statement=f"SELECT count(*), max(time) FROM {span_tables[0]}",  # noqa: S608 - tag-sourced name
            wait_timeout="50s",
        )
        row = (resp.result.data_array or [["0", None]])[0] if resp.result else ["0", None]
        return int(row[0]), str(row[1] or "never")
    except Exception as exc:  # noqa: BLE001 - a diagnostic must not fail on its own diagnosis
        print(f"{INFO} could not count spans ({type(exc).__name__}); skipping that check")
        return None


def _report(problems: list[str], spans: tuple[int, str] | None = None) -> int:
    print()
    if not problems:
        print("Trace storage looks correctly provisioned.")
        if spans is not None:
            count, newest = spans
            print(f"{OK} {count} span(s) stored, newest {newest}")
            if count:
                print()
                print("The spans are there, so this is a reading problem, not a writing one.")
                print("Traces in Unity Catalog tables are served by a SQL warehouse rather")
                print("than by the tracking server, and neither the UI nor the SDK picks one")
                print("for you — so an experiment full of traces reads as empty until you do:")
                print()
                print("  * in the MLflow UI, choose a warehouse in the Traces tab;")
                print("  * from the SDK, export MLFLOW_TRACING_SQL_WAREHOUSE_ID=<id> before")
                print("    calling mlflow.search_traces, or it raises rather than returning [].")
                return 0
        print("If traces are still empty, the app SP may lack write access — re-run the")
        print("deploy, which re-grants it, and check the app's logs for export errors.")
        return 0
    print("What to do:")
    for n, problem in enumerate(problems, 1):
        print(f"  {n}. {problem}")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("env", choices=["dev", "prod"])
    ap.add_argument("--settings", default=str(ROOT / "config" / "project-settings.local.yaml"))
    args = ap.parse_args()

    settings = Path(args.settings)
    if not settings.is_file():
        settings = ROOT / "config" / "project-settings.yaml"
    return diagnose(args.env, settings)


if __name__ == "__main__":
    raise SystemExit(main())
