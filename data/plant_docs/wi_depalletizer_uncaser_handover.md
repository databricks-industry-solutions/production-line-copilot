# WI-DEP-003 — work instruction, DEPALLETIZER and UNCASER dry end feed

Revision 3. Owner: production. For operator use at the line start.

## 1. Purpose

Keeps glass moving into the BOTTLE_WASHER without starving it and without
crushing cases at the UNCASER. The dry end has no buffer worth the name before
CONVEYOR_1, so a two-minute gap here is a two-minute gap at the FILLER twelve
minutes later.

## 2. Pallet patterns

| Format | Bottles per layer | Layers per pallet | Interlayer sheet |
|---|---|---|---|
| 0.5 L | 189 | 5 | yes, after each layer |
| 1.0 L | 108 | 4 | yes, after each layer |
| 1.5 L | 84 | 4 | no |

An interlayer sheet left on the stack is the most common DEPALLETIZER fault. The
sheet lifts with the layer, the vacuum head loses grip on one corner and the
layer drops. Check the sheet count against the layer count as the pallet empties.

## 3. Sequence at line start

1. Confirm the BOTTLE_WASHER is at temperature. Do not feed glass into a cold
   washer; the bottles pass uncleaned and the EMPTY_INSPECTOR rejects them all.
2. Stage two pallets at the DEPALLETIZER and one at the UNCASER infeed.
3. Start CONVEYOR_1 and confirm 25 m/min at the display.
4. Start the DEPALLETIZER in single-layer mode for the first two layers and watch
   the transfer, then switch to continuous.
5. Confirm accumulation on CONVEYOR_1 reaches 30 % before the washer is asked for
   full rate.

## 4. Faults and first actions

| Symptom | First action |
|---|---|
| Layer dropped at the transfer | stop, clear glass, check for a lifted interlayer sheet |
| Cases jamming at the UNCASER infeed | check case flap condition, damp cases lose stiffness |
| CONVEYOR_1 accumulation below 20 % with pallets available | DEPALLETIZER cycle rate, not the conveyor |
| Bottles falling between UNCASER and CONVEYOR_1 | guide rail alignment at the transfer plate |

Damp cases are worth naming: cases stored against an outside wall overnight lose
flap stiffness, jam the UNCASER, and the fault looks like a machine problem for
the first hour of a shift.

## 5. Records

Pallet count and any dropped layers noted in the shift handover log, with the
delivery reference for the glass if a pallet was rejected.
