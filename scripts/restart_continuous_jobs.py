"""Restart the continuous streaming jobs so they pick up newly deployed code.

``databricks bundle deploy`` replaces the workspace files a job runs from, but a
run already in flight keeps executing the code it started with. The continuous
producer and quality-sync jobs never terminate on their own, so on every deploy
after the first they carry on running whatever was current when they last
started — indefinitely.

That is not a theoretical gap. A fix to the producer's command polling was
deployed to both environments and changed nothing, because both producers were
still running a build from the previous day; the symptom was the bug persisting
through a clean deploy, which sends you looking in the wrong place.

Cancelling the run is the restart: the jobs are configured ``continuous``, so
Databricks starts a fresh run — on the new code — within a minute or two.

Idempotent, and safe to run when nothing is going: a job with no active run is
reported and skipped.
"""
from __future__ import annotations

import argparse

# Bundle resource names, minus the environment suffix deploy.sh passes in.
CONTINUOUS_JOBS = (
    "line-manager-streaming-producer",
    "line-manager-streaming-quality-sync",
)

ACTIVE = {"PENDING", "RUNNING", "QUEUED", "BLOCKED", "WAITING_FOR_RETRY"}


def restart(profile: str, suffix: str = "") -> int:
    from databricks.sdk import WorkspaceClient

    w = WorkspaceClient(profile=profile) if profile else WorkspaceClient()
    wanted = {f"{name}{suffix}" for name in CONTINUOUS_JOBS}

    by_name = {}
    for job in w.jobs.list():
        name = (job.settings.name or "") if job.settings else ""
        if name in wanted:
            by_name[name] = job.job_id

    missing = wanted - set(by_name)
    for name in sorted(missing):
        # Not an error: a fresh deploy creates these unpaused and they start on
        # their own, and a workshop target may not define them at all.
        print(f"  {name}: not found — nothing to restart")

    failures = 0
    for name in sorted(by_name):
        job_id = by_name[name]
        try:
            runs = [
                r for r in w.jobs.list_runs(job_id=job_id, limit=5)
                if r.state and r.state.life_cycle_state
                and r.state.life_cycle_state.value in ACTIVE
            ]
        except Exception as exc:  # noqa: BLE001
            print(f"  {name}: could not list runs ({str(exc)[:120]})")
            failures += 1
            continue

        if not runs:
            print(f"  {name}: no active run — will start on the new code by itself")
            continue

        for run in runs:
            try:
                w.jobs.cancel_run(run_id=run.run_id)
                print(f"  {name}: cancelled run {run.run_id} — continuous job restarts on new code")
            except Exception as exc:  # noqa: BLE001
                print(f"  {name}: cancel failed for run {run.run_id} ({str(exc)[:120]})")
                failures += 1

    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="", help="Databricks CLI profile")
    ap.add_argument("--suffix", default="", help="environment suffix, e.g. -dev")
    ns = ap.parse_args()
    return restart(ns.profile, ns.suffix)


if __name__ == "__main__":
    _rc = main()
    # Databricks treats SystemExit(0) from a job task as a workload failure, and
    # this module is importable from one. Only signal a real failure.
    if _rc:
        raise SystemExit(_rc)
