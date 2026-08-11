# Planned maintenance schedule SCH-MNT-001 — line 1 intervals by machine

Revision 11. Owner: maintenance. Interval basis is running hours unless a
calendar interval is stated.

## 1. Interval table

| Machine | Weekly | Monthly | 6 monthly | Annual |
|---|---|---|---|---|
| DEPALLETIZER | vacuum head seal check | chain tension | head rebuild | structural inspection |
| CONVEYOR_1 | belt edge and debris | belt tension and speed | drive chain wear | — |
| UNCASER | gripper pad condition | cam timing check | gripper rebuild | — |
| BOTTLE_WASHER | spray nozzle blockage | caustic filter change | heat exchanger clean | tank internal inspection |
| EMPTY_INSPECTOR | lens clean | reference calibration | camera alignment | electronics service |
| CONVEYOR_2 | belt edge and debris | belt tension and speed | drive chain wear | transfer plate replacement |
| FILLER | valve seal inspection | bowl level probe check | valve seal replacement | carousel bearing service |
| CAPPER | torque head spread check | cap chute wear | torque head calibration | turret bearing service |
| PASTEURIZER | spray header check | zone probe verification | tunnel belt inspection | water treatment service |
| LABELLER | glue pot clean | glue temperature probe check | applicator rebuild | vision system service |
| CODER | print head clean | ink system service | — | electronics service |
| CONVEYOR_3 | belt edge and debris | belt tension and speed | drive chain wear | — |
| CASE_PACKER | carton magazine check | lane guide wear | head rebuild | — |
| SHRINK_WRAPPER | tunnel temperature check | element inspection | element replacement | — |
| CASE_SEALER | glue nozzle clean | glue system service | — | — |
| CHECKWEIGHER | reference weight check | full calibration | load cell verification | — |
| CONVEYOR_4 | belt edge and debris | belt tension and speed | drive chain wear | transfer plate replacement |
| PALLETIZER | gripper condition | axis lubrication | axis service | structural inspection |
| STRETCH_WRAPPER | film path check | film carriage service | — | — |

## 2. Tasks that must not be deferred

Three items on this schedule are the difference between a planned stop and an
unplanned one, and they are deferred more often than the rest because nothing
visible happens when they are skipped:

- CAPPER torque head calibration at six months. A drifting head is found as
  leaks at the CHECKWEIGHER, several machines and several hours away.
- EMPTY_INSPECTOR reference calibration monthly. Drift high loses good glass all
  shift; drift low passes chipped finishes to the CAPPER.
- FILLER valve seal replacement at six months. A weeping seal reads as a low
  average fill volume that the bowl control keeps correcting for, so the fill
  looks stable while product is being lost.

## 3. Scheduling against production

Weekly tasks are done at the day-end clean, inside the clean in place window
where the wet end is already stopped. Monthly and longer tasks need a planned
line stop; the standing slot is the first Tuesday of the month.

Anything requiring entry inside a guard follows the isolation sequence in
SOP-CAP-021 section 2, whichever machine it is on.

## 4. Records

Completion, findings and parts used against each interval, in the maintenance
system. Deferred tasks need a named approver and a new date, and the deferral is
reviewed at the monthly maintenance meeting.
