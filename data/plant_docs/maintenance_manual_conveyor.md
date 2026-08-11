# Maintenance manual extract — accumulation conveyors CONVEYOR_1 to CONVEYOR_4

Revision 7. Owner: maintenance. Applies to all four accumulation conveyors on
line 1.

## 1. Duty

Each conveyor runs at a nominal 25 m/min belt speed and holds roughly four
minutes of production at line rate. Accumulation pressure is the measurement
that matters: it tells you whether the conveyor is buffering or fighting itself.

- Normal accumulation pressure: 30 to 70 %.
- Above 80 %: bottles are being pushed rather than carried. Expect scuffing and,
  at the CONVEYOR_2 handover into the FILLER, infeed jams.
- Below 20 %: the conveyor is starved and the downstream machine will follow
  within about four minutes.

## 2. Planned maintenance

| Interval | Task |
|---|---|
| Weekly | inspect belt edges for wear, clear debris from the drip trays |
| Weekly | check lubrication dosing at the transfer plates |
| Monthly | measure belt tension, correct to the drive manual figure |
| Monthly | verify belt speed against the 25 m/min setpoint with a tachometer |
| 6 monthly | inspect drive chain and sprockets, replace if wear exceeds 3 % pitch |
| Annually | replace transfer plates on CONVEYOR_2 and CONVEYOR_4 |

## 3. Fault finding

| Symptom | Check first | Then |
|---|---|---|
| Belt speed below setpoint at normal load | drive belt tension | motor current and drive fault log |
| Accumulation pressure high with the line running | downstream machine rate | transfer plate wear and lubrication |
| Accumulation pressure oscillating | lubrication dosing | photo-eye alignment at the discharge |
| Bottles falling at a transfer | guide rail alignment | plate step height, replace worn plate |

A conveyor reported as slow is usually a downstream problem. CONVEYOR_2 running
under pressure with the FILLER stopped is the buffer doing its job, not a
conveyor fault — check the FILLER before working on the conveyor.

## 4. Lubrication

Dry lubricant at the transfer plates only. Do not apply wet lubricant on the
CONVEYOR_3 discharge into the CASE_PACKER; it migrates onto the carton blanks
and shows as seal failures at the CASE_SEALER.

## 5. Isolation

Electrical isolation at the local disconnect with a personal lock and tag before
any work inside the guards. The full energy isolation sequence, including
pneumatic venting, is in SOP-CAP-021 section 2.
