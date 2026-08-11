# SOP-FILL-014 — FILLER size changeover, 0.5 L to 1.0 L

Revision 6. Owner: packaging engineering. Review cycle: 12 months.

## 1. Scope

Applies to the FILLER on line 1 when changing bottle format between 0.5 L and
1.0 L. Covers the carousel, the bowl level control and the infeed handover from
CONVEYOR_2. Cap format changes are covered by SOP-CAP-021, not by this
procedure.

## 2. Preconditions

- Line stopped at the FILLER, upstream buffer on CONVEYOR_2 below 20 %.
- Product bowl drained to below 5 % level.
- Two operators. One works the carousel, one stays at the HMI.
- PPE: cut-resistant gloves and eye protection while carousel parts are handled.

## 3. Changeover steps

1. Stop the FILLER at the HMI, then apply the machine isolation described in
   SOP-CAP-021 section 4 before reaching into the carousel.
2. Drain the bowl and record the closing level. A bowl left above 5 % carries
   the previous format's product into the first bottles of the new run.
3. Remove the 0.5 L centring bells (24 off) and fit the 1.0 L set. Torque the
   bell retainers to 12 Nm.
4. Change the infeed starwheel and the guide rails. Set the rail gap to 74 mm
   for 1.0 L, from 62 mm for 0.5 L.
5. Reset the fill target at the HMI to 1000 ml and the low-fill alarm to 988 ml.
   The 0.5 L values are 500 ml and 492 ml.
6. Refill the bowl to 60 % before restarting, so bowl control has authority from
   the first bottle.

## 4. Restart and verification

- Restart at 180 BPM for the first five minutes. Do not go to the nominal
  420 BPM until the fill checks below pass.
- Take ten consecutive bottles and weigh them. Average fill volume must sit
  within 1000 ml +/- 6 ml, and no single bottle below 988 ml.
- Confirm the CAPPER is running before releasing filled bottles downstream. A
  stopped CAPPER with a running FILLER fills the buffer between them in about
  four minutes at 420 BPM.
- Record the verification bottles in the QMS changeover record.

## 5. Common faults after changeover

| Symptom | Likely cause | Action |
|---|---|---|
| Fill low alarms in the first minutes | bowl restarted below 60 % | raise bowl level, re-verify ten bottles |
| Bottles rocking in the carousel | wrong centring bells or rail gap | stop, re-check step 3 and step 4 |
| Repeated infeed jams | rail gap still at 62 mm | reset gap to 74 mm |

## 6. Records

Changeover record, ten verification weights, and the closing bowl level. Retain
for 24 months.
