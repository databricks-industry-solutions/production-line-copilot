import { describe, it, expect } from 'vitest';
import { deviationTone } from './reconciliationUtils';

// The whole point of the reconciliation panel is to distinguish "we checked
// and the paths agree" from "we could not check". Both arrive as zero
// unexplained rows, so the colour is the only thing that separates them —
// these tests pin that it does.

describe('deviationTone', () => {
  it('greens a zero that was actually measured', () => {
    expect(deviationTone('ok', 0)).toBe('good');
  });

  it('greens a quiet stream — no traffic is a measured result, not a gap', () => {
    expect(deviationTone('idle', 0)).toBe('good');
  });

  it('withholds green when a hop could not be counted', () => {
    // The backend sends unexplained=0 alongside status=unknown because it has
    // no deviation to report — not because the paths agreed.
    expect(deviationTone('unknown', 0)).toBe('unknown');
  });

  it('reds any non-zero deviation, measured or not', () => {
    expect(deviationTone('warn', 3)).toBe('bad');
    expect(deviationTone('error', 900)).toBe('bad');
    expect(deviationTone('unknown', 5)).toBe('bad');
  });
});
