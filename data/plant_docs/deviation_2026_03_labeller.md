# Deviation report DEV-2026-03-011 — LABELLER vision rejects, night shift

Status: closed. Raised 2026-03-14. Closed 2026-03-19. Owner: quality.

## 1. What happened

On the night shift of 2026-03-14 the LABELLER vision reject rate rose from a
normal 0.4 per minute to a sustained 6 to 9 per minute over about 90 minutes.
Rejected bottles carried labels that were skewed by 3 to 5 degrees. 2,180
bottles were rejected. No product left the site with a defective label.

The line was not stopped. The LABELLER stayed at 420 BPM and the rejects were
diverted, so the loss shows in the quality figures rather than as downtime.

## 2. Investigation

- Glue temperature was logged at 158 to 161 °C for the whole period. The
  specification in the process narrative is 165 to 175 °C.
- The glue pot had been refilled at the start of the shift. Refilling with cold
  adhesive pulls the pot below setpoint for roughly 40 minutes, and the
  temperature trend shows exactly that shape, twice.
- Label roll remaining was 34 % and the roll was within date. The roll was
  eliminated as a cause.
- No mechanical change to the label station in the preceding two weeks.
- The CODER downstream showed no rise in rejects, which confirms the defect was
  label application and not bottle handling.

## 3. Root cause

Cold adhesive added to the glue pot mid-shift dropped glue temperature below the
specified window. Below about 162 °C the adhesive does not wet the label edge
evenly and the label rotates as the bottle turns.

## 4. Corrective action

1. Refill the glue pot only when the machine is stopped, and allow 20 minutes to
   recover setpoint before running. Completed 2026-03-15.
2. Add a glue temperature low alarm at 163 °C. Completed 2026-03-18.
3. SOP-LAB-008 updated with a pre-refill temperature check. Completed
   2026-03-19.

## 5. Effectiveness check

Reviewed 2026-04-19. Vision reject rate returned to 0.3 to 0.5 per minute. No
repeat of the pattern in the following month. Deviation closed.
