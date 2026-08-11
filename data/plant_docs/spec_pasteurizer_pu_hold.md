# Specification SPEC-PAS-003 — PASTEURIZER pasteurisation units and zone holds

Revision 3. Owner: process engineering with quality. Review cycle: 24 months.

## 1. Target

Delivered pasteurisation units (PU) must fall between 18 and 26 PU for all
products on line 1. Below 18 PU the product is not commercially sterile and the
batch is held. Above 26 PU flavour degrades and the batch is a quality
deviation even though it is safe.

## 2. Zone setpoints

| Zone | Function | Setpoint | Tolerance |
|---|---|---|---|
| 1 | pre-heat | 42 °C | +/- 2 °C |
| 2 | holding | 68 °C | +/- 1 °C |
| 3 | holding | 68 °C | +/- 1 °C |
| 4 | pre-cool | 45 °C | +/- 3 °C |
| 5 | cool | 28 °C | +/- 3 °C |

Zone 2 is the controlling measurement and the one the line tiles display. A
zone 2 reading outside 67 to 69 °C puts the PU target at risk within minutes,
because holding time is fixed by belt speed.

## 3. Relationship to line rate

PU accumulate with time at temperature, so a slow line over-pasteurises and a
fast line under-pasteurises:

- At nominal rate the tunnel delivers about 22 PU with zone 2 at 68 °C.
- A stop upstream at the FILLER or the CAPPER leaves bottles standing in the
  tunnel. Bottles held longer than four minutes with the zones at setpoint
  exceed 26 PU and must be diverted.
- On any stop longer than four minutes, drop zone 2 to 55 °C and hold the
  bottles. Return to 68 °C before restarting the line.

## 4. Verification

- One PU logger run per week per product, recorded against the tunnel profile.
- Zone 2 recorded continuously. An excursion beyond 67 to 69 °C for more than
  two minutes raises a deviation under the QMS.
- Steam supply is shared with the BOTTLE_WASHER caustic loop, so investigate a
  zone 2 low excursion together with the washer temperature before treating it
  as a PASTEURIZER fault.

## 5. Held product

Product outside 18 to 26 PU is quarantined by lot and assessed by quality. Under
18 PU is destroyed. Over 26 PU may be released on a documented quality
assessment covering flavour and colour.
