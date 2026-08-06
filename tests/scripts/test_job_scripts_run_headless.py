"""Scripts that run as job tasks cannot rely on ``__file__``.

Databricks runs a ``python_file`` task by reading the source and handing it to
``exec(compile(...))``. That leaves no ``__file__`` global, so a module-level
``os.path.abspath(__file__)`` raises ``NameError`` before the first line of
real work — and only in the workspace, never locally.

``backfill_quality_and_events.py`` shipped with exactly that line. Every unit
test passed, the script ran fine from a terminal, and it failed on the first
fresh deploy that reached it, taking the whole setup job and every downstream
task with it. Its siblings had already solved this with a documented fallback
to ``sys.argv[0]``; the new task simply did not copy it.

This reads the task list out of the generator that writes the bundle YAML, so a
task added later is covered without anyone remembering to come back here.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SYNC = (REPO / "scripts" / "sync_project_settings.py").read_text(encoding="utf-8")

# The generator emits `python_file: ../scripts/<name>.py` lines for each task.
JOB_SCRIPTS = sorted(set(re.findall(r"python_file: \.\./scripts/([\w.]+\.py)", SYNC)))


def test_the_task_list_was_found():
    """A refactor of the generator must not turn this suite into a no-op."""
    assert len(JOB_SCRIPTS) >= 5, (
        f"only found {JOB_SCRIPTS} — the python_file lines in "
        "sync_project_settings.py changed shape and this guard is now blind"
    )


def _module_level_file_refs(tree: ast.AST) -> list[int]:
    """Line numbers of ``__file__`` reads outside any function or class.

    Inside a function the name is resolved at call time, and a caller that
    knows it is running headless can avoid it. At module level there is no
    such escape: importing the file is enough to raise.
    """
    lines: list[int] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and child.id == "__file__":
                lines.append(child.lineno)
    return lines


@pytest.mark.parametrize("script", JOB_SCRIPTS)
def test_no_bare_module_level_file_reference(script: str):
    path = REPO / "scripts" / script
    assert path.exists(), f"{script} is wired into a job but does not exist"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    bare = _module_level_file_refs(tree)
    if not bare:
        return

    # `globals().get("__file__") or sys.argv[0]` is the sanctioned form: the
    # name appears as a string, not as an identifier, so it never resolves.
    guarded = 'globals().get("__file__")' in source or "globals().get('__file__')" in source
    assert guarded, (
        f"{script} reads __file__ at module level (line {bare[0]}). Databricks "
        "exec's job scripts without that global, so this raises NameError in "
        "the workspace while passing every local test. Use "
        'os.path.abspath(globals().get("__file__") or sys.argv[0]).'
    )


@pytest.mark.parametrize("script", JOB_SCRIPTS)
def test_a_clean_run_does_not_raise_system_exit(script: str):
    """``SystemExit(0)`` fails a serverless task even though the code is zero.

    On an IPython-wrapped ``spark_python_task`` the kernel surfaces any raised
    SystemExit as INTERNAL_ERROR. ``raise SystemExit(main())`` therefore fails
    the job on success — and does it *after* the work has been done, so the
    tables are written and the task is red, which is the confusing way round.

    A clean run has to fall off the end of the module instead.
    """
    source = (REPO / "scripts" / script).read_text(encoding="utf-8")
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or node.exc is None:
            continue
        exc = node.exc
        if not (isinstance(exc, ast.Call) and getattr(exc.func, "id", None) == "SystemExit"):
            continue
        # Unconditional `raise SystemExit(main())` is the broken form; raising
        # inside `if rc:` is fine because a clean run never reaches it.
        arg = exc.args[0] if exc.args else None
        unconditional = isinstance(arg, ast.Call)
        assert not unconditional, (
            f"{script} line {node.lineno}: `raise SystemExit(main())` fails the "
            "task on a serverless runtime even when main() returns 0. Assign the "
            "code first and raise only when it is non-zero."
        )
