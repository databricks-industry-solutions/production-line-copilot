# Line 1 process narrative (piping and instrumentation diagram DRW-L1-002)

Revision 4. Owner: process engineering. Companion text to the drawing set; the
drawing governs where the two disagree.

## 1. Flow order

Bottles move through the line in this order:

DEPALLETIZER, CONVEYOR_1, UNCASER, BOTTLE_WASHER, EMPTY_INSPECTOR, CONVEYOR_2,
FILLER, CAPPER, PASTEURIZER, LABELLER, CODER, CONVEYOR_3, CASE_PACKER,
SHRINK_WRAPPER, CASE_SEALER, CHECKWEIGHER, CONVEYOR_4, PALLETIZER,
STRETCH_WRAPPER.

The FILLER is the line pacemaker at a nominal 420 BPM. The CAPPER and the
LABELLER are rated to the same 420 BPM. The CASE_PACKER runs at 17.5 cases per
minute, which is the same throughput in 24-packs.

## 2. Buffers and what a stop does

Each accumulation conveyor holds roughly four minutes of production at nominal
rate. That is the whole reason a short stop is not a line stop:

- A machine that stops drops its output to zero immediately.
- Downstream machines drain their infeed buffer and then starve.
- Upstream machines fill their outfeed buffer and then block.
- Below 60 % buffer level the neighbouring machine's effective capacity starts
  to fall, so the loss spreads one machine at a time rather than all at once.

A stop shorter than about four minutes is usually absorbed by the buffers and
shows as a rate dip, not lost cases. Anything longer propagates.

## 3. Instrumentation of interest

| Location | Measurement | Nominal |
|---|---|---|
| BOTTLE_WASHER | caustic temperature | 62 to 66 °C |
| BOTTLE_WASHER | tunnel speed | 25 m/min |
| EMPTY_INSPECTOR | vision reject rate | below 2 per minute |
| FILLER | bowl level | 55 to 70 % in steady state |
| FILLER | average fill volume | format target, see SOP-FILL-014 |
| CAPPER | application torque | 20 to 24 Nm |
| PASTEURIZER | zone 2 temperature | see the PU hold specification |
| LABELLER | glue temperature | 165 to 175 °C |
| CHECKWEIGHER | case weight | format target, reject band +/- 1.5 % |
| CONVEYOR_1 to CONVEYOR_4 | belt speed | 25 m/min |

## 4. Utilities

Caustic supply and the hot water loop serve the BOTTLE_WASHER and the
PASTEURIZER from the same plant room. A caustic dosing fault therefore shows up
at both machines, which is worth remembering before chasing two unrelated
faults. Compressed air at 6 bar serves the FILLER, the CAPPER and the
CASE_PACKER on a shared header.
