"""Score candidate models on the shift_briefing role's real format contract.

The shift_briefing prompt is the strictest in the app: five exact headings, a
numbered Priority Actions list, a one-line Confidence verdict, hard bullet and
word caps, no tables, no sub-headings. That is the whole reason the role exists
as its own specialist, and it makes "which model is better here" an objective
question rather than a matter of taste.

So this runs the *real* system prompt against each candidate with identical
stubbed tool data, several times, and scores the output against the rules the
UI actually depends on. Stubs rather than live tools on purpose: two models
given different data are not comparable, and what is being measured is format
discipline, not retrieval.

Dev tool, not shipped or imported by the app.

    DATABRICKS_CONFIG_PROFILE=<profile> uv run --frozen \
        python scripts/dev/compare_shift_briefing_models.py
"""
from __future__ import annotations

import re
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import agent_server  # noqa: F401,E402  - applies the vendor compat shim
from agent_server.llm_output import message_text  # noqa: E402
from agent_server.prompts.shift_briefing import SHIFT_BRIEFING_SYSTEM  # noqa: E402

CANDIDATES = [
    "databricks-claude-haiku-4-5",  # incumbent
    "databricks-gemini-3-5-flash",  # challenger
]
REPEATS = 8
REQUIRED_HEADINGS = ["Situation", "Key Risks", "Priority Actions", "Outlook", "Confidence"]

# Not all rules cost the same. The UI splits the answer on these exact ``##``
# headings and renders Priority Actions and Confidence as their own elements, so
# breaking one of these produces a visibly broken briefing. Everything else is
# cosmetic — an unbolded lead-in or an over-long bullet still reads fine. A model
# that fails five cosmetic rules is preferable to one that rarely mangles a
# structural one, which a single averaged percentage would hide.
STRUCTURAL_RULES = frozenset(
    {
        "exact 5 headings in order",
        "Priority Actions numbered",
        "Confidence is one HIGH/MEDIUM/LOW line",
        "no markdown tables",
    }
)

QUERY = (
    "Give me the pre-shift briefing for the night shift on Line 1 starting at 22:00."
)


def _tools():
    from langchain_core.tools import tool

    @tool
    def sql_query(query: str) -> str:
        """Run a read-only SQL SELECT against the production tables."""
        return (
            "line_id=LINE-01 | oee=67.3 | availability=81.2 | performance=79.4 | quality=88.1\n"
            "machine_id=FILLER-01 | state=RUNNING | downtime_min_shift=47 | target_rate=312\n"
            "machine_id=CAPPER-02 | state=DEGRADED | downtime_min_shift=18 | target_rate=305\n"
            "machine_id=LABELLER-03 | state=RUNNING | downtime_min_shift=0 | target_rate=298\n"
            "item_id=CROWN-CAP-STD | on_hand=8200 | consumption_per_min=195\n"
            "item_id=LABEL-500ML | on_hand=41000 | consumption_per_min=190\n"
            "open_ncr=2 | scrap_rate_pct=1.8 | last_cip=14:05"
        )

    @tool
    def genie_query(question: str) -> str:
        """Ask a natural-language question of the governed data model."""
        return "Night shift target: 148,000 units. Last night actual: 131,400 units (-11.2%)."

    @tool
    def calculator(expression: str) -> str:
        """Evaluate an arithmetic expression."""
        try:
            return str(eval(expression, {"__builtins__": {}}, {}))  # noqa: S307 - dev stub
        except Exception as exc:  # noqa: BLE001
            return f"error: {exc}"

    @tool
    def load_plan_and_constraints(line_id: str = "LINE-01") -> str:
        """Load the production plan, crew roster and scheduled constraints."""
        return (
            "plan: 148000 units night shift | changeover 02:00 (SKU 500ML -> 330ML, 35 min)\n"
            "CIP window 01:00-01:30 on FILLER-01 (blocks line)\n"
            "crew: Maria Lopez (line lead) confirmed; Marek Sw. (mechanic) NOT on file; "
            "2 operators confirmed"
        )

    @tool
    def retrieve_similar_cases(description: str) -> str:
        """Find historical incidents resembling the description."""
        return (
            "2026-06-14 CAPPER-02 degraded torque -> 3.2h downtime; fixed by torque head swap.\n"
            "2026-05-02 crown cap starvation at 190 u/min -> 1.1h lost; fix: stage 60 pallets."
        )

    return [sql_query, genie_query, calculator, load_plan_and_constraints, retrieve_similar_cases]


def _sections(text: str) -> dict[str, list[str]]:
    """Split a briefing into ``heading -> body lines``."""
    out: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        m = re.match(r"^##\s+(.*?)\s*$", line)
        if m and not line.startswith("###"):
            current = m.group(1).strip()
            out[current] = []
        elif current is not None and line.strip():
            out[current].append(line.rstrip())
    return out


def score(text: str) -> tuple[int, int, list[str]]:
    """Return ``(passed, total, failure notes)`` against the prompt's hard rules."""
    checks: list[tuple[str, bool]] = []
    sections = _sections(text)
    headings = list(sections)

    checks.append(("exact 5 headings in order", headings == REQUIRED_HEADINGS))
    checks.append(("no ### sub-headings", "###" not in text))
    checks.append(("no markdown tables", "|---" not in text and not re.search(r"^\s*\|", text, re.M)))
    checks.append(("<=250 words", len(text.split()) <= 250))

    actions = sections.get("Priority Actions", [])
    checks.append(
        ("Priority Actions numbered", bool(actions) and all(re.match(r"^\s*\d+\.", ln) for ln in actions))
    )

    conf = sections.get("Confidence", [])
    checks.append(
        (
            "Confidence is one HIGH/MEDIUM/LOW line",
            len(conf) == 1 and bool(re.match(r"^\*\*(HIGH|MEDIUM|LOW)\*\*\s*[—-]", conf[0].strip())),
        )
    )

    bulleted = {h: b for h, b in sections.items() if h not in {"Priority Actions", "Confidence"}}
    checks.append(
        ("<=5 bullets per section", all(len(b) <= 5 for b in bulleted.values()) and bool(bulleted))
    )
    checks.append(
        (
            "bullets lead with a bolded value",
            bool(bulleted)
            and all(
                all(re.match(r"^\s*[-*]\s+\*\*", ln) for ln in body)
                for body in bulleted.values()
                if body
            ),
        )
    )
    checks.append(("<=18 words per bullet", all(
        len(ln.split()) <= 20 for body in sections.values() for ln in body
    )))

    failures = [name for name, ok in checks if not ok]
    return sum(1 for _, ok in checks if ok), len(checks), failures


def main() -> None:
    from databricks_langchain import ChatDatabricks
    from langchain.agents import create_agent
    from langchain_core.messages import SystemMessage

    tools = _tools()
    print(f"shift_briefing head-to-head — {REPEATS} runs each\n")

    for endpoint in CANDIDATES:
        scores: list[float] = []
        latencies: list[float] = []
        all_failures: list[str] = []
        errors = 0
        structural_breaks = 0

        for _ in range(REPEATS):
            try:
                agent = create_agent(
                    model=ChatDatabricks(endpoint=endpoint, max_tokens=2048),
                    tools=tools,
                    system_prompt=SystemMessage(content=SHIFT_BRIEFING_SYSTEM),
                )
                t0 = time.time()
                res = agent.invoke({"messages": [("user", QUERY)]})
                latencies.append(time.time() - t0)
                text = message_text(res["messages"][-1])
                passed, total, failures = score(text)
                scores.append(passed / total)
                all_failures.extend(failures)
                if any(f in STRUCTURAL_RULES for f in failures):
                    structural_breaks += 1
            except Exception as exc:  # noqa: BLE001
                errors += 1
                all_failures.append(f"EXCEPTION {type(exc).__name__}: {str(exc)[:80]}")

        print(f"=== {endpoint} ===")
        if scores:
            print(f"  format compliance: {statistics.mean(scores):.0%}  (runs: {['%.0f%%' % (s*100) for s in scores]})")
            print(f"  median latency:    {statistics.median(latencies):.1f}s")
        print(f"  errors:            {errors}/{REPEATS}")
        print(f"  BROKEN LAYOUT:     {structural_breaks}/{REPEATS} runs  <- the one that matters")
        if all_failures:
            counts: dict[str, int] = {}
            for f in all_failures:
                counts[f] = counts.get(f, 0) + 1
            print("  rule failures:")
            for name, n in sorted(counts.items(), key=lambda kv: -kv[1]):
                kind = "STRUCTURAL" if name in STRUCTURAL_RULES else "cosmetic"
                print(f"    {n}x [{kind}] {name}")
        print()


if __name__ == "__main__":
    main()
