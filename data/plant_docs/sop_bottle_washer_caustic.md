# SOP-WSH-005 — BOTTLE_WASHER caustic control and carryover checks

Revision 6. Owner: hygiene. Review cycle: 12 months.

## 1. Operating window

| Parameter | Setpoint | Working window | Action outside |
|---|---|---|---|
| Caustic concentration | 2.0 % | 1.8 to 2.2 % | dose or dilute, re-test after 10 min |
| Caustic temperature | 64 °C | 62 to 66 °C | hold the line, do not run bottles |
| Tunnel speed | 25 m/min | 24 to 26 m/min | correct to setpoint, verify with tachometer |
| Final rinse conductivity | below 40 microsiemens per cm | — | investigate carryover |

Below 62 °C the caustic does not remove dried product residue and the
EMPTY_INSPECTOR downstream starts rejecting on residue rather than on damage.
That is the useful early signal: a rising EMPTY_INSPECTOR reject rate with no
change in incoming glass is nearly always a washer temperature problem.

## 2. Why washer faults look like PASTEURIZER faults

The caustic heat exchanger and the PASTEURIZER hot water loop are fed from the
same plant room, described in the line process narrative. A steam supply problem
therefore shows at both machines within a few minutes of each other. Before
treating a low zone 2 temperature at the PASTEURIZER as a tunnel fault, check
whether caustic temperature fell at the same time. If both fell, the fault is
upstream of both machines.

## 3. Carryover checks

Caustic carried out of the washer on bottle surfaces reaches the FILLER and shows
as taste complaints, not as an alarm. Check every four hours:

1. Draw five bottles from the EMPTY_INSPECTOR outfeed.
2. Rinse each with 50 ml of treated water and test the rinsate pH.
3. Rinsate pH must be between 6.5 and 8.0. Above 8.0 indicates carryover.
4. On a failure, stop the line at the FILLER, raise final rinse pressure, and
   re-test before restarting.

## 4. Concentration testing

Titration every two hours during production and after any dosing intervention.
Concentration drifts down through a shift as bottles carry caustic out; a
concentration that drifts *up* means the rinse is not returning and the dosing
control is chasing a false low reading.

## 5. Records

Concentration and temperature readings every two hours, four-hourly carryover
results, and any dosing interventions. Retain for 24 months.
