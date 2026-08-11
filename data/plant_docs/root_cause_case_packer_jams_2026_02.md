# Root cause analysis RCA-2026-02-004 — repeated CASE_PACKER jams

Raised 2026-02-09. Closed 2026-03-02. Owner: maintenance with production.

## 1. Problem statement

Between 2026-01-26 and 2026-02-08 the CASE_PACKER stopped 47 times on carton
feed jams, against a normal 3 to 6 stops per fortnight. Stops averaged 4.5
minutes. Total downtime 3 hours 32 minutes, and because the average stop sat just
above the four-minute buffer, most of them also starved the SHRINK_WRAPPER and the
CASE_SEALER rather than being absorbed.

## 2. What was checked and eliminated

| Hypothesis | Test | Result |
|---|---|---|
| Carton supplier change | checked delivery records and carton certificates | no change, same supplier and grade |
| Carton magazine setup | measured against the machine manual | within specification |
| Lane guide wear | inspected and measured | wear present but inside limits |
| Vacuum on the pick head | measured at the head | at specification |
| Ambient humidity in the store | reviewed store logs | within normal range |

## 3. Root cause

Wet lubricant had been applied at the CONVEYOR_3 discharge into the CASE_PACKER
during a night shift on 2026-01-25. The maintenance manual for the accumulation
conveyors permits dry lubricant at the transfer plates only, and names this exact
consequence: wet lubricant migrates onto the carton blanks.

Contaminated blanks slip in the magazine and the pick head takes two blanks or
none. The jam presents at the CASE_PACKER, three machines away from the cause, and
every hypothesis in section 2 is a CASE_PACKER hypothesis. That is why it took two
weeks.

## 4. Contributing factors

- The lubricant type was not stated on the conveyor's local maintenance card, only
  in the manual extract held in the maintenance system.
- The night shift technician was covering line 1 for the first time.
- The first six jams were cleared and not recorded as a trend, so the start date
  was reconstructed afterwards from the event log rather than known.

## 5. Corrective actions

1. Clean the CONVEYOR_3 discharge and the magazine, discard blanks in the
   magazine and the top layer of the pallet. Completed 2026-02-09.
2. State the permitted lubricant on the local maintenance card at every conveyor
   transfer. Completed 2026-02-16.
3. Add lubricant type to the line induction checklist for cover technicians.
   Completed 2026-02-24.
4. Any machine stopping more than twice in a shift on the same fault is recorded
   as a trend the same shift, not cleared silently. Completed 2026-03-02.

## 6. Effectiveness check

Reviewed 2026-04-02. CASE_PACKER carton feed stops returned to 4 per fortnight. No
recurrence. Closed.
