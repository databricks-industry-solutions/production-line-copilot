# Changeover matrix MTX-CO-001 — planned minutes by transition and machine

Revision 4. Owner: production planning. Reviewed each quarter against actual
changeover records.

## 1. How to read this

Times are planned minutes from last good bottle of the old format to first good
bottle of the new one. Machines change over in parallel where they can; the line
time is the critical path, not the sum of the column.

| Transition | FILLER | CAPPER | LABELLER | CASE_PACKER | Line critical path |
|---|---|---|---|---|---|
| 0.5 L to 1.0 L | 45 | 10 | 25 | 30 | 55 |
| 1.0 L to 0.5 L | 45 | 10 | 25 | 30 | 55 |
| 1.0 L to 1.5 L | 60 | 10 | 25 | 40 | 70 |
| 1.5 L to 1.0 L | 60 | 10 | 25 | 40 | 70 |
| 0.5 L to 1.5 L | 75 | 10 | 30 | 40 | 85 |
| Product change, same format | 0 | 0 | 20 | 0 | 85 |

The last row is the one that surprises people. No machine needs mechanical work
for a product change in the same bottle, but the wet end needs a full clean in
place under SOP-CIP-002 when the change crosses cleaning groups, and that is 64
minutes plus heat-up. The label change runs inside that window.

## 2. Critical path notes

- The FILLER is on the critical path for every format change. Its 45 to 75 minutes
  is carousel work: centring bells, starwheel, rail gap.
- The CAPPER is never on the critical path for a format change on line 1, because
  all three formats use the same 28 mm closure. It appears here at 10 minutes for
  the height adjustment only.
- The CASE_PACKER time is carton magazine and lane setup. It runs in parallel with
  the FILLER and only becomes critical if the FILLER finishes early.
- The PASTEURIZER needs no changeover work, but a format change alters bottle
  spacing in the tunnel, so the first bottles need a delivered pasteurisation
  check against SPEC-PAS-003 before the run is released.

## 3. Planning rules

1. Do not plan two format changes in one shift on the 1.5 L format. Two 85 minute
   changes plus start-up losses leaves under four productive hours.
2. Group product changes so that cleaning-group crossings happen once per day, at
   the day-end clean where the time is already committed.
3. A changeover that overruns its planned time by more than 20 minutes is recorded
   with a reason. The recorded reasons drive the quarterly review of this matrix.

## 4. Actual versus planned

Planned times assume two operators and no fault. The 0.5 L to 1.0 L transition
has averaged 61 minutes over the last quarter against 55 planned, and the recorded
reasons are dominated by rail gap re-adjustment after the first jams — which is
step 4 of SOP-FILL-014 being done to the old figure.
