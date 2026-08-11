# Specification SPEC-FILL-021 — fill volumes and case configuration by format

Revision 5. Owner: packaging engineering with quality. Review cycle: 12 months.
Supersedes nothing; the changeover method itself is SOP-FILL-014.

## 1. Filled volume by format

| Format | Fill target | Working tolerance | Low-fill alarm | Reject below | Nominal rate |
|---|---|---|---|---|---|
| 0.5 L | 500 ml | +/- 4 ml | 492 ml | 490 ml | 420 BPM |
| 1.0 L | 1000 ml | +/- 6 ml | 988 ml | 985 ml | 420 BPM |
| 1.5 L | 1500 ml | +/- 9 ml | 1482 ml | 1478 ml | 360 BPM |

The 1.5 L format is the only one that does not run at the line nominal rate. The
FILLER carousel is the limit, not the CAPPER or the LABELLER, both of which are
rated to 420 BPM in any format.

Average fill volume is judged on ten consecutive bottles, never on one. A single
bottle inside the reject band is a handling artefact; two in ten is a fault.

## 2. Case configuration and case weight

| Format | Bottles per case | Cases per pallet | Target case weight | CHECKWEIGHER reject band |
|---|---|---|---|---|
| 0.5 L | 24 | 60 | 13.10 kg | +/- 1.5 % |
| 1.0 L | 12 | 60 | 12.85 kg | +/- 1.5 % |
| 1.5 L | 8 | 48 | 12.60 kg | +/- 1.5 % |

The CASE_PACKER runs 17.5 cases per minute in 24-packs, which matches 420 BPM.
In 12-packs at the same bottle rate it runs 35 cases per minute, and that is
above its rated 30, so the 1.0 L format is packed at 400 BPM at the FILLER or the
CASE_PACKER becomes the constraint. This is the one case on line 1 where the
pacemaker is deliberately slowed to protect a downstream machine.

## 3. Why the reject band is narrower than the tolerance

Working tolerance is what the process is expected to hold. The reject band is
where an individual bottle is removed. They are deliberately different: a bottle
at 986 ml in the 1.0 L format is outside tolerance and inside the reject band, so
it stays in the case and raises the alarm, and the fault gets corrected before
the average moves. Setting them equal makes the line reject bottles it should be
adjusting for.

## 4. Records

Ten verification weights per changeover and per shift start, held in the QMS for
24 months. CHECKWEIGHER rejects are counted per case, not per bottle.
