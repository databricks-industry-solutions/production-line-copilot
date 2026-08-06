import { describe, expect, it } from 'vitest';
import {
  emptyKpiHoldState,
  MAX_HELD_TICKS,
  resolveKpiFrame,
  type KpiHoldState,
  type KpiTriple,
} from './kpiHold';

const healthy: KpiTriple = {
  oee_to_now: 74.2,
  availability_to_now: 86.0,
  performance_to_now: 87.6,
};

const zeroed: KpiTriple = {
  oee_to_now: 0,
  availability_to_now: 0,
  performance_to_now: 0,
};

/** Feed a sequence of ticks and return what the tiles would have shown. */
function play(
  ticks: Array<{ kpi: KpiTriple; throughput: number }>,
): { shown: KpiTriple[]; state: KpiHoldState } {
  let state = emptyKpiHoldState;
  const shown: KpiTriple[] = [];
  for (const tick of ticks) {
    const out = resolveKpiFrame(tick.kpi, tick.throughput, state);
    shown.push(out.value);
    state = out.state;
  }
  return { shown, state };
}

describe('resolveKpiFrame', () => {
  it('passes a healthy frame straight through', () => {
    const out = resolveKpiFrame(healthy, 420, emptyKpiHoldState);
    expect(out.value).toEqual(healthy);
    expect(out.held).toBe(false);
    expect(out.state.lastGood).toEqual(healthy);
  });

  it('carries the previous frame across a single mid-cascade blip', () => {
    // The tile should not flash 0 -> 75% when the producer emits one bad tick.
    const { shown } = play([
      { kpi: healthy, throughput: 420 },
      { kpi: zeroed, throughput: 380 },
      { kpi: healthy, throughput: 415 },
    ]);
    expect(shown[1]).toEqual(healthy);
  });

  it('stops holding once the zero outlasts its grace tick', () => {
    // This is the bug: a stopped line drains its buffers, so throughput stays
    // above zero and the hold condition was satisfied on every single tick.
    const { shown } = play([
      { kpi: healthy, throughput: 420 },
      { kpi: zeroed, throughput: 12 },
      { kpi: zeroed, throughput: 8 },
      { kpi: zeroed, throughput: 1.2 },
      { kpi: zeroed, throughput: 1.2 },
    ]);
    expect(shown[1]).toEqual(healthy); // the one grace tick
    expect(shown.slice(2)).toEqual([zeroed, zeroed, zeroed]);
  });

  it('never shows a stale frame for longer than MAX_HELD_TICKS', () => {
    const ticks = [{ kpi: healthy, throughput: 420 }].concat(
      Array.from({ length: 40 }, () => ({ kpi: zeroed, throughput: 1.2 })),
    );
    const { shown } = play(ticks);
    const stale = shown.slice(1).filter((frame) => frame.oee_to_now !== 0).length;
    expect(stale).toBe(MAX_HELD_TICKS);
  });

  it('reports a real zero immediately when nothing is moving', () => {
    // No throughput at all was never ambiguous.
    const { shown } = play([
      { kpi: healthy, throughput: 420 },
      { kpi: zeroed, throughput: 0 },
    ]);
    expect(shown[1]).toEqual(zeroed);
  });

  it('does not invent a frame before the first healthy one', () => {
    // A cold start that opens on a stopped line has nothing to fall back to.
    const out = resolveKpiFrame(zeroed, 5, emptyKpiHoldState);
    expect(out.value).toEqual(zeroed);
    expect(out.held).toBe(false);
  });

  it('grants a fresh grace tick after the line recovers', () => {
    const { shown } = play([
      { kpi: healthy, throughput: 420 },
      { kpi: zeroed, throughput: 10 },
      { kpi: zeroed, throughput: 10 }, // real zero, hold exhausted
      { kpi: healthy, throughput: 400 }, // recovered, budget resets
      { kpi: zeroed, throughput: 10 },
    ]);
    expect(shown[2]).toEqual(zeroed);
    expect(shown[4]).toEqual(healthy);
  });

  it('holds the whole triple together, not just OEE', () => {
    // Availability was the number that disagreed with the assistant.
    const { shown } = play([
      { kpi: healthy, throughput: 420 },
      { kpi: zeroed, throughput: 300 },
      { kpi: zeroed, throughput: 300 },
    ]);
    expect(shown[1].availability_to_now).toBe(healthy.availability_to_now);
    expect(shown[2].availability_to_now).toBe(0);
  });
});
