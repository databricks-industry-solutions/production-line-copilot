"""Tests for the shift-briefing additions to scripts/genie_space_config.py.

The Genie space carries three types of briefing artifacts that we want to lock
down so they don't silently disappear if someone refactors the script:

  * 3 sample_questions (surfaced to Genie mobile users as autosuggest chips)
  * 4 example_question_sqls (curated SQL bodies the LLM can graft onto)
  * 2 benchmark Q&A pairs (used for Genie evaluation runs)
  * 1 dedicated text_instructions block ("SHIFT BRIEFING / HANDOVER QUERIES")

These tests load the script's ``serialized_space`` dict directly — no network,
no Databricks CLI needed — so they run in any environment.
"""
from __future__ import annotations

import importlib
import json

import pytest


@pytest.fixture(scope="module")
def space() -> dict:
    """Import scripts/genie_space_config.py and return its serialized_space dict."""
    mod = importlib.import_module("scripts.genie_space_config")
    ss = getattr(mod, "serialized_space")
    assert isinstance(ss, dict)
    return ss


def test_briefing_sample_questions_present(space):
    """Four shift-arc chips must lead the Genie mobile autosuggest list.

    The intended UX is: line manager walks to the line, opens the Databricks
    mobile app, and the FIRST chips they see are the four shift-arc briefings
    (pre / mid / post / carryover). Concretely:

      #1  'Brief me for my shift'
      #2  'How are we doing right now?'
      #3  'Wrap up this shift'
      #4  "What's carrying over?"

    Genie sorts sample questions by id alphanumerically, so the IDs for these
    four must come strictly before any operational drill-down question.
    """
    samples = space["config"]["sample_questions"]
    assert len(samples) >= 8

    leading = [" ".join(s["question"]).lower() for s in samples[:4]]

    # Pre-shift chip
    assert any("brief me" in q or "pre-shift" in q for q in leading), leading
    # Mid-shift chip — added so the line manager can ask mid-walk
    assert any("how are we doing" in q or "right now" in q or "mid-shift" in q
               for q in leading), leading
    # Post-shift chip
    assert any("wrap up" in q or "post-shift" in q for q in leading), leading
    # Carryover chip
    assert any("carrying over" in q or "carryover" in q for q in leading), leading


def test_briefing_text_instruction_present(space):
    """The text_instructions block must explain pre/post/carryover modes.

    Genie's API enforces at most ONE text_instructions block per space, so the
    briefing guidance must be folded into the single domain-rules block.
    """
    blocks = space["instructions"]["text_instructions"]
    assert len(blocks) == 1, (
        "Genie API rejects payloads with multiple text_instructions blocks "
        "(`instructions.text_instructions must contain at most one item`); "
        "merge briefing guidance into the single block instead."
    )
    joined = "".join(blocks[0]["content"]).lower()
    assert "shift briefing" in joined or "shift_briefing" in joined or "pre-shift" in joined
    # Each handover mode must be explicitly covered.
    for mode in ("pre-shift", "post-shift", "carryover"):
        assert mode in joined, f"text_instructions missing handover mode: {mode}"
    # The instructions must reference the actual tables a briefing needs.
    for table in ("production_schedule", "shift_crew", "maintenance_schedule",
                  "cip_schedule", "inventory_status", "event_log", "oee_gold"):
        assert table in joined, f"text_instructions missing table reference: {table}"


def test_briefing_example_sqls_present_and_valid(space):
    """Four example_question_sqls cover pre, post, carryover, and live-open modes."""
    examples = space["instructions"]["example_question_sqls"]
    by_question = {" ".join(e["question"]).lower(): e for e in examples}

    pre = next((e for q, e in by_question.items() if "pre-shift briefing" in q), None)
    post = next((e for q, e in by_question.items() if "post-shift summary" in q), None)
    carryover = next((e for q, e in by_question.items() if "carryover" in q), None)
    live = next((e for q, e in by_question.items() if "still open" in q or "right now" in q), None)

    assert pre is not None, "missing pre-shift briefing example SQL"
    assert post is not None, "missing post-shift summary example SQL"
    assert carryover is not None, "missing carryover-risks example SQL"
    assert live is not None, "missing live/mid-shift open-events example SQL"

    # Pre, post, and carryover must be shift-scoped via a :shift_id parameter
    # so Genie mobile can fill it in via the question phrase.
    for label, ex in (("pre-shift", pre), ("post-shift", post), ("carryover", carryover)):
        params = ex.get("parameters", [])
        param_names = [p["name"] for p in params]
        assert "shift_id" in param_names, f"{label} SQL missing :shift_id parameter"

    # The SQL bodies must reference the tables the briefing needs.
    pre_sql = "".join(pre["sql"])
    for tbl in ("production_schedule", "shift_crew", "inventory_status",
                "maintenance_schedule", "cip_schedule"):
        assert tbl in pre_sql, f"pre-shift SQL missing {tbl}"
    post_sql = "".join(post["sql"])
    for tbl in ("oee_gold", "event_log"):
        assert tbl in post_sql, f"post-shift SQL missing {tbl}"
    carryover_sql = "".join(carryover["sql"])
    for tbl in ("event_log", "inventory_status", "maintenance_schedule"):
        assert tbl in carryover_sql, f"carryover SQL missing {tbl}"


def test_briefing_benchmarks_present(space):
    """Two benchmarks let MLflow eval target briefing answers specifically."""
    bms = space["benchmarks"]["questions"]
    qs = [" ".join(b["question"]).lower() for b in bms]
    assert any("post-shift summary" in q for q in qs), qs
    assert any("carrying over" in q or "carryover" in q for q in qs), qs


def test_full_payload_is_json_round_trippable(space):
    """The serialized payload must round-trip through JSON exactly (catches stray refs)."""
    payload = json.dumps(space)
    reloaded = json.loads(payload)
    assert reloaded == space
