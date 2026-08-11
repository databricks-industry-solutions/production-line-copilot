# Plant document corpus

Twenty-two synthetic plant documents plus `examples.json`. This is the knowledge
source for the opt-in Knowledge Assistant (`scripts/provision_knowledge_assistant.py`
uploads this directory to a Unity Catalog volume and points the assistant at it).

They are **markdown, not PDF**, on purpose. Knowledge Assistant ingests
`txt, pdf, md, ppt/pptx, doc/docx`, and markdown stays reviewable and diffable
in git where a binary would not. A real deployment points the same config at its
own volume of PDFs and deletes nothing here.

## What is in here, and why each kind

| Kind | Documents | What it demonstrates |
|---|---|---|
| Procedures | filler changeover, capper isolation, clean in place, washer caustic, empty inspector setup, depalletizer feed | ordered steps, where order is itself the safety control |
| Specifications | fill volumes by format, capper torque by closure, pasteurisation units | tables of setpoints and tolerances, quoted exactly |
| Matrices | changeover times, product changeover cleaning | a lookup across two axes, where the answer is a cell |
| Records | batch record, calibration certificate, shift handover log | dated evidence, hourly readings, what actually happened |
| Investigations | labeller deviation, case packer root cause | a conclusion reached over time, with eliminated hypotheses |
| Governance | HACCP critical control points, planned maintenance schedule | limits and intervals that other documents defer to |
| Reference | line process narrative, conveyor maintenance manual, closure certificate | the shared context the rest of the corpus cites |
| Bulletin | pasteurisation window proposal | a document that **disagrees** with a specification, on purpose |

## The disagreement is deliberate

`bulletin_2026_05_pasteurizer_pu_window.md` proposes a pasteurisation window and a
zone 2 setpoint that `spec_pasteurizer_pu_hold.md` does not carry, and says
plainly that it is not approved and that the specification governs. A corpus where
every question has one obvious source demonstrates retrieval; this pair asks for
judgement, and a wrong answer here is the kind that ships product.

`tests/data/test_the_plant_doc_corpus_is_answerable.py` keeps the pair honest: the
bulletin must keep stating its status, and the proposed numbers must not leak into
any other document.

## Conventions the tests enforce

Every document is written against the machine vocabulary in
`client/src/lib/machineMeta.ts` — the same ids the tiles, the specialists and the
event history use. A document naming equipment this line does not have produces a
fluent, cited answer about a machine that does not exist, so unknown upper-case
terms fail the suite. New acronyms are added to an allowlist deliberately, one at
a time, because the usual cause of an unknown token is a typo in a machine name.

The numbers are invented but internally consistent, and they cross-reference:
the torque window in the capper procedure is the window in the closure
certificate, the critical limit in the HACCP plan and the shift-start check in the
batch record. Answers can therefore be checked against the corpus rather than
merely sounding plausible.

`examples.json` carries the question / guideline pairs registered as assistant
examples at provision time. Each entry names the document that answers it.
