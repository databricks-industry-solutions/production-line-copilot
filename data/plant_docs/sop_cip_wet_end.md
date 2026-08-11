# SOP-CIP-002 — clean in place, wet end (FILLER and PASTEURIZER)

Revision 8. Owner: hygiene with process engineering. Review cycle: 12 months.

## 1. Scope and frequency

Covers the CIP circuit serving the FILLER bowl and filling valves and the
PASTEURIZER tunnel spray system. Runs at the end of every production day, and
before any product changeover that crosses the groups in the changeover cleaning
matrix.

The BOTTLE_WASHER is not on this circuit. Its caustic system is separate and is
covered by SOP-WSH-005, though both draw from the same hot water loop, so a CIP
run and a washer heat-up at the same time will starve each other.

## 2. Phase sequence

| Phase | Medium | Temperature | Concentration | Duration |
|---|---|---|---|---|
| 1 pre-rinse | recovered water | ambient | none | 8 min |
| 2 caustic | caustic | 75 to 80 °C | 1.8 to 2.2 % | 20 min |
| 3 intermediate rinse | fresh water | ambient | none | 6 min |
| 4 acid | nitric acid blend | 60 to 65 °C | 0.8 to 1.2 % | 12 min |
| 5 final rinse | treated water | ambient | none | 10 min |
| 6 sanitise | peracetic blend | ambient | 0.15 to 0.25 % | 8 min |

Total circuit time is about 64 minutes plus heat-up. Phase 2 is the phase that
does the work; shortening it to recover production time is the most common cause
of a failed hygiene swab the following morning.

## 3. Release criteria

- Final rinse conductivity within 20 microsiemens per cm of incoming water.
- Final rinse pH between 6.5 and 7.5.
- Two hygiene swabs per run: one at the FILLER bowl outlet, one at the
  PASTEURIZER zone 2 spray header. Both below 150 relative light units on the
  ATP reader.
- A swab between 150 and 300 requires a repeat sanitise phase and a re-swab. Above
  300, repeat the full sequence from phase 2.

## 4. Interaction with the PASTEURIZER

The tunnel must be empty of product before phase 2. Bottles left in the tunnel
during a caustic phase are destroyed, not reworked. Drop the zones to ambient
before starting; returning zone 2 to its 68 °C setpoint from cold takes about 25
minutes, and that time belongs to the CIP window, not to the production start.

## 5. Records

Phase temperatures and concentrations logged automatically. Swab results, rinse
conductivity and pH recorded by the hygiene operator in the QMS. Retain for 24
months.
