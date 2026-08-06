"""``machines_starved`` carries two different faults, and must say so.

A machine can stop taking part in the line for opposite reasons: nothing is
arriving from upstream (starved), or what it makes has nowhere to go because
downstream is full (blocked). ``compute_line_kpi`` folds both into the single
``machines_starved`` counter, because the KPI row has no ``machines_blocked``
column to hold the other one.

That fold is survivable. What was not is the label: Genie was told the column
was the "count of machines starved by upstream stoppage", so a line backed up
behind a jam was reported to the operator as short of input — and the two point
at opposite ends of the line. Telling someone to chase a stalled upstream
machine while the real problem is a full downstream buffer costs a shift the
time it takes to work that out.

These tests pin the fold and the honesty of the two descriptions together, so
the counter cannot quietly go back to claiming one cause. Splitting the counter
later is fine; doing it without rewriting both descriptions is what fails here.
"""

from __future__ import annotations

from pathlib import Path

from src.streaming_sim.cascade import MachineSpec, build_line, compute_line_kpi

_ROOT = Path(__file__).resolve().parents[2]


def _kpi_for(statuses: dict[str, str]):
    specs = [
        MachineSpec(id="A", line_position=1, nominal_capacity=100.0),
        MachineSpec(id="B", line_position=2, nominal_capacity=100.0),
        MachineSpec(id="C", line_position=3, nominal_capacity=100.0),
    ]
    state = build_line(specs, buffer_seconds=5.0)
    for mid, st in statuses.items():
        state.machines[mid].status = st
    return compute_line_kpi(state, "shift_1", "2026-04-23T12:00:00")


def test_blocked_machines_are_counted_as_starved():
    """The fold this whole file exists to document."""
    kpi = _kpi_for({"A": "running", "B": "blocked", "C": "starved"})
    assert kpi["machines_starved"] == 2, (
        "blocked and starved share the counter; if this is now 1, the counter was "
        "split and both descriptions below need rewriting"
    )
    assert kpi["machines_running"] == 1


def test_a_blocked_line_is_not_reported_as_zero_starved():
    """Blocked alone still registers — it must not vanish from the KPI row."""
    kpi = _kpi_for({"A": "running", "B": "blocked", "C": "blocked"})
    assert kpi["machines_starved"] == 2


def test_there_is_still_no_machines_blocked_column():
    kpi = _kpi_for({"A": "blocked"})
    assert "machines_blocked" not in kpi


def test_the_prompt_does_not_blame_upstream_alone():
    text = (_ROOT / "agent_server/prompts/_shared.py").read_text()
    line = next(ln for ln in text.splitlines() if "machines_starved counts" in ln)
    assert "blocked" in line, "the prompt must name the second state in the counter"
    assert "machine_state.status" in line, "and point at the way to tell them apart"


def test_genie_does_not_blame_upstream_alone():
    text = (_ROOT / "scripts/genie_space_config_streaming.py").read_text()
    start = text.index('"column_name": "machines_starved"')
    desc = text[start:start + 700]
    assert "blocked" in desc, "Genie must not attribute the counter to upstream alone"
    # The old text was "Count of machines starved by upstream stoppage." The
    # replacement mentions upstream stoppage too, to warn against blaming it, so
    # match the warning rather than the phrase it warns about.
    assert "do not attribute" in desc.lower(), "the single-cause reading is unguarded"
    assert "machine_state.status" in desc, "and Genie needs the way to tell them apart"
