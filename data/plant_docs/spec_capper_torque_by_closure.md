# Specification SPEC-CAP-009 — CAPPER application torque by closure type

Revision 4. Owner: packaging engineering. Review cycle: 12 months. The isolation
and head-service procedure is SOP-CAP-021; this document governs the numbers.

## 1. Torque windows

| Closure | Liner | Application torque | Working window | Removal torque after 24 h |
|---|---|---|---|---|
| 28 mm screw, standard | 1.20 mm | 22 Nm | 20 to 24 Nm | 1.4 to 2.6 Nm |
| 28 mm screw, thin liner | 0.90 mm | 20 Nm | 18 to 22 Nm | 1.2 to 2.2 Nm |
| 38 mm screw | 1.40 mm | 26 Nm | 24 to 28 Nm | 1.8 to 3.2 Nm |

Line 1 runs the 28 mm standard closure in all three bottle formats. The other two
rows exist because both have been trialled and both leave torque heads set
wrongly if a trial is not reversed.

## 2. What a wrong torque looks like downstream

Torque faults are almost never found at the CAPPER. They are found two or three
machines later, which is why this table matters more than the machine's own
display:

| Condition | Where it shows | What is seen |
|---|---|---|
| Below the window | CHECKWEIGHER | under-weight cases from leakage after the PASTEURIZER |
| Below the window | PASTEURIZER | product in the tunnel water, cloudy zone 2 sight glass |
| Above the window | LABELLER | vision rejects from cracked closure skirts |
| Above the window | CAPPER | rising head-to-head torque spread across ten caps |

A single loose cap is a closure defect. A run of loose caps at a stable torque
reading is a head calibration problem, and a run of loose caps with a falling
torque reading is air pressure — the FILLER, the CAPPER and the CASE_PACKER share
a 6 bar header, so check the header before the machine.

## 3. Lot-specific guidance

Incoming closure lots are released with a certificate of analysis, and a
certificate may carry advice that differs from this table. The certificate wins
for that lot only, and only within the working window: it reflects measured liner
thickness on material that is already on site. Anything outside the working
window needs packaging engineering to agree it in writing before the line runs.

## 4. Verification

Twenty caps after any head service, ten caps at each shift start, recorded as an
average and a spread. Replace a head that drifts more than 2 Nm from nominal
across ten consecutive caps.
