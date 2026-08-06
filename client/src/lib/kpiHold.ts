/**
 * Deciding whether a zero OEE is a blip or the truth.
 *
 * The producer recomputes A/P/OEE from the current tick's machine counts. Mid
 * cascade — during a resolve, or the instant a fault lands — it can emit
 * `avg_speed_pct = 0` for a single tick before the line settles, which
 * collapses OEE to zero and makes the tiles flash 0 → 75 %. Carrying the
 * previous frame across that one tick is worth doing.
 *
 * Carrying it further is not. A stopped line satisfies the same condition on
 * every tick: it still drains its last buffer, so throughput sits just above
 * zero while OEE is genuinely zero. Held indefinitely, the tiles showed
 * minutes-old numbers for as long as the line was down — 27.8 % availability
 * against a real 0.0 — while the assistant read the same table and reported
 * zero. The dashboard and the AI gave different answers to the same question,
 * and the dashboard was the wrong one.
 *
 * Pure so the boundary between the two cases is testable; App.tsx keeps the
 * state in refs.
 */

export interface KpiTriple {
  oee_to_now: number;
  availability_to_now: number;
  performance_to_now: number;
}

export interface KpiHoldState {
  /** The most recent frame with a non-zero OEE, or null before the first one. */
  lastGood: KpiTriple | null;
  /** Consecutive ticks already served from `lastGood`. */
  heldTicks: number;
}

/** How many consecutive ticks may be served from the last good frame. */
export const MAX_HELD_TICKS = 1;

export const emptyKpiHoldState: KpiHoldState = { lastGood: null, heldTicks: 0 };

/**
 * Resolve the A/P/OEE to display for one tick.
 *
 * @param incoming    this tick's values as computed upstream
 * @param throughput  the line's current throughput; a stopped line still
 *                    drains its buffers, so this alone does not mean healthy
 * @param state       carried between ticks
 */
export function resolveKpiFrame(
  incoming: KpiTriple,
  throughput: number,
  state: KpiHoldState,
): { value: KpiTriple; state: KpiHoldState; held: boolean } {
  const looksTransient =
    incoming.oee_to_now === 0 && throughput > 0 && state.lastGood !== null;

  if (looksTransient && state.heldTicks < MAX_HELD_TICKS && state.lastGood) {
    return {
      value: state.lastGood,
      state: { lastGood: state.lastGood, heldTicks: state.heldTicks + 1 },
      held: true,
    };
  }

  if (incoming.oee_to_now > 0) {
    return {
      value: incoming,
      state: { lastGood: incoming, heldTicks: 0 },
      held: false,
    };
  }

  // A zero that has outlasted its grace tick, or one with no throughput behind
  // it: the line really is down, so say so.
  return { value: incoming, state: { ...state, heldTicks: state.heldTicks }, held: false };
}
