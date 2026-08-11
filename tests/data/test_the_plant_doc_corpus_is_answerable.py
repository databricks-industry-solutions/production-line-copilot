"""The shipped plant-document corpus has to be answerable by the Knowledge Assistant.

The corpus is the only knowledge source the assistant gets, so a doc that names
a machine the app does not model produces a fluent answer about equipment that
does not exist on this line — the failure mode is a confident citation, not an
error. These tests hold the corpus to the machine vocabulary in
``client/src/lib/machineMeta.ts``, which is what the tiles, the specialists and
the event history all use.

Two ingestion rules from the Knowledge Assistant docs are asserted here too,
because both fail silently: files whose names start with ``_`` or ``.`` are
skipped during ingestion, and only txt/pdf/md/ppt(x)/doc(x) are read at all. A
skipped file looks exactly like a file with nothing relevant in it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_DIR = REPO_ROOT / "data" / "plant_docs"
MACHINE_META = REPO_ROOT / "client" / "src" / "lib" / "machineMeta.ts"

# Supported by Knowledge Assistant volume ingestion. The corpus ships markdown.
INGESTIBLE_SUFFIXES = {".txt", ".pdf", ".md", ".ppt", ".pptx", ".doc", ".docx"}

# Upper-case tokens in the corpus that are deliberately not machine ids. Kept
# tight on purpose: an unknown token failing this list is usually a typo in a
# machine name (LABELER for LABELLER), which is exactly what we want caught.
NON_MACHINE_TOKENS = {
    "ATP", "BPM", "CCP", "CIP", "COA", "CO2", "EA", "HACCP", "HMI", "ISO",
    "LOTO", "MES", "NOK", "OEE", "PPE", "PU", "QMS", "SKU", "SOP", "UTC", "WIP",
}

CAPS_TOKEN = re.compile(r"\b[A-Z][A-Z0-9_]{1,}\b")

# Document reference codes (SOP-CAP-021, DEV-2026-03-011, DRW-L1-002). Their
# fragments are not equipment names, so they are removed before tokenizing —
# otherwise every allowlist entry would be a piece of a filing convention and
# the check would stop being able to say anything about machine names.
DOC_REFERENCE = re.compile(r"\b[A-Z][A-Z0-9]{1,5}(?:-[A-Z0-9]+)+\b")


def equipment_tokens(text: str) -> set[str]:
    """Upper-case tokens in prose, with document reference codes removed."""
    return set(CAPS_TOKEN.findall(DOC_REFERENCE.sub(" ", text)))


def known_machine_ids() -> set[str]:
    """Machine ids the app models, read from the client's flow order."""
    source = MACHINE_META.read_text(encoding="utf-8")
    block = re.search(
        r"MACHINE_FLOW_ORDER:\s*string\[\]\s*=\s*\[(.*?)\]", source, re.DOTALL
    )
    assert block, "MACHINE_FLOW_ORDER not found in machineMeta.ts"
    return set(re.findall(r"'([A-Z0-9_]+)'", block.group(1)))


def corpus_docs() -> list[Path]:
    return sorted(p for p in CORPUS_DIR.glob("*.md") if p.name != "README.md")


def test_the_corpus_directory_ships_documents():
    assert CORPUS_DIR.is_dir(), f"{CORPUS_DIR} does not exist"
    assert len(corpus_docs()) >= 15, "too few documents to demonstrate document Q&A"


class TestTheDeliberateDisagreement:
    """One pair of documents disagrees on purpose.

    A corpus where every question has exactly one obvious source demonstrates
    retrieval, not judgement. The pasteurisation bulletin proposes numbers the
    specification does not carry, so the assistant has to say which one is in
    force — and that only works while the bulletin keeps saying it is not.
    """

    BULLETIN = "bulletin_2026_05_pasteurizer_pu_window.md"
    SPEC = "spec_pasteurizer_pu_hold.md"

    def test_the_bulletin_says_it_is_not_in_force(self):
        text = (CORPUS_DIR / self.BULLETIN).read_text(encoding="utf-8")
        assert "not approved for" in text.lower(), "the bulletin must state its status"
        assert "SPEC-PAS-003" in text, "the bulletin must name the document that governs"
        assert "does not change the specification" in text.lower()

    def test_only_the_bulletin_carries_the_proposed_window(self):
        """If the proposal leaks into another document there is no longer a
        right answer, only two documents saying different things."""
        leaked = [p.name for p in corpus_docs()
                  if p.name != self.BULLETIN and "20 to 30" in p.read_text(encoding="utf-8")]
        assert not leaked, f"the proposed PU window leaked into {leaked}"

    def test_the_specification_still_states_the_window_in_force(self):
        text = (CORPUS_DIR / self.SPEC).read_text(encoding="utf-8")
        assert "18 and 26 PU" in text, "the specification must still carry the live window"
        assert "68 °C" in text


@pytest.mark.parametrize("doc", corpus_docs(), ids=lambda p: p.name)
def test_every_document_would_be_ingested(doc: Path):
    assert doc.suffix in INGESTIBLE_SUFFIXES
    assert not doc.name.startswith(("_", ".")), "ingestion skips these silently"
    assert len(doc.read_text(encoding="utf-8").strip()) > 400, "too thin to answer from"


@pytest.mark.parametrize("doc", corpus_docs(), ids=lambda p: p.name)
def test_every_document_names_a_machine_the_app_models(doc: Path):
    machines = known_machine_ids()
    text = doc.read_text(encoding="utf-8")
    assert machines & equipment_tokens(text), (
        f"{doc.name} names no machine from MACHINE_FLOW_ORDER"
    )


@pytest.mark.parametrize("doc", corpus_docs(), ids=lambda p: p.name)
def test_no_document_invents_equipment(doc: Path):
    allowed = known_machine_ids() | NON_MACHINE_TOKENS
    unknown = equipment_tokens(doc.read_text(encoding="utf-8")) - allowed
    assert not unknown, f"{doc.name} uses unknown upper-case terms: {sorted(unknown)}"


def test_the_examples_reference_documents_that_exist():
    examples = json.loads((CORPUS_DIR / "examples.json").read_text(encoding="utf-8"))
    assert len(examples) >= 15
    names = {p.name for p in corpus_docs()}
    for ex in examples:
        assert ex["question"].strip(), "an example needs a question"
        assert isinstance(ex["guidelines"], list) and ex["guidelines"]
        assert ex["doc"] in names, f"example points at missing doc {ex['doc']}"
