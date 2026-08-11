# SOP-INS-012 — EMPTY_INSPECTOR setup and daily challenge test

Revision 5. Owner: quality. Review cycle: 12 months.

## 1. What the machine rejects

The EMPTY_INSPECTOR inspects every washed bottle before it reaches the FILLER.
Five checks run in parallel:

| Check | Rejects when | Sensitivity setting |
|---|---|---|
| Residual liquid | more than 2 ml in the base | 3 of 5 |
| Foreign object in base | object above 2 mm in any dimension | 4 of 5 |
| Sidewall damage | crack or chip above 1 mm | 4 of 5 |
| Finish damage | any chip on the sealing surface | 5 of 5 |
| Residue film | light transmission below 85 % of reference | 3 of 5 |

Finish damage runs at maximum sensitivity because a chipped sealing surface
passes the CAPPER at correct torque and leaks after the PASTEURIZER, where it is
found as an under-weight case at the CHECKWEIGHER, hours later and a pallet
downstream.

## 2. Normal reject rate

Expect below 2 rejects per minute at 420 BPM. Rates above that are a signal about
upstream equipment, not about this machine:

- Rising residue-film rejects: BOTTLE_WASHER caustic temperature or concentration.
- Rising residual-liquid rejects: final rinse drainage, or tunnel speed above
  setpoint carrying bottles out before they drain.
- Rising sidewall rejects with no change in reject pattern by lane: incoming glass
  quality, check the pallet and the delivery note.
- Rejects concentrated in one lane: mechanical, check the lane guide and the
  transfer from CONVEYOR_1.

## 3. Daily challenge test

Run at each shift start, before production. The test kit holds five reference
bottles, one for each check.

1. Confirm the machine is running at production sensitivity.
2. Pass each reference bottle through in sequence.
3. All five must be rejected. A reference bottle that passes stops the line.
4. Pass five clean bottles. All five must be accepted; a clean bottle rejected on
   two consecutive attempts means the sensitivity has drifted high and the line
   will lose good glass all shift.
5. Record all ten results.

A failed challenge test is a line stop, not a note for later. Production run since
the last passed test is quarantined by lot.

## 4. Records

Ten challenge results per shift, kit bottle serial numbers, and any sensitivity
change with the reason. Retain for 24 months.
