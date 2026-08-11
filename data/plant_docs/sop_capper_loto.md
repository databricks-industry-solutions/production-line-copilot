# SOP-CAP-021 — CAPPER isolation (LOTO) and torque head service

Revision 9. Owner: maintenance. Review cycle: 12 months.

## 1. Scope

Isolation of the CAPPER for any work inside the capping turret, including torque
head replacement and cap chute clearing. Also the reference isolation procedure
for the FILLER (SOP-FILL-014 step 1) and the LABELLER.

## 2. LOTO sequence

Perform in this order. Do not reorder steps 3 and 4.

1. Announce the stop to the line operator and stop the CAPPER at the HMI.
2. Wait for the turret to come to rest. Free rotation continues for up to eight
   seconds after the stop command.
3. Isolate electrical supply at the local disconnect and apply a personal lock
   and tag.
4. Isolate pneumatic supply, then vent the accumulator. The turret can index
   once on stored air if the accumulator is not vented — this is the step that
   most often gets skipped.
5. Isolate the cap hopper drive.
6. Verify zero energy: attempt a start at the HMI, confirm no motion, confirm
   the pressure gauge reads zero.
7. Only then open the turret guard.

Removal is the reverse order, with the guard closed before any lock is removed.

## 3. Torque head replacement

- Nominal application torque is 22 Nm. Working window is 20 to 24 Nm.
- Replace a head whose measured torque drifts more than 2 Nm from nominal
  across ten consecutive caps.
- After replacement, run 20 caps and record the average application torque.
  Release the line only when the average is within the working window.
- Torque below 20 Nm gives loose caps and leaks found at the CHECKWEIGHER as
  under-weight cases. Torque above 24 Nm cracks caps and shows as vision
  rejects at the LABELLER.

## 4. Cap chute clearing

Cap jams do not require full isolation if the chute can be cleared from outside
the guard with the machine stopped and the cap hopper drive isolated (step 5
only). Any work that puts a hand past the guard line requires the full sequence
in section 2.

## 5. Records

Lock register entry, the 20 post-service torque readings, and the reason for the
intervention in the QMS log. Retain for 24 months.
