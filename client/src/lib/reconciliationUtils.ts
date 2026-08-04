/** Pure display logic for the reconciliation panel (see components/Reconciliation.tsx). */

/**
 * Whether an unexplained-row count has earned green.
 *
 * A zero can mean two different things: both hops were counted and agreed, or
 * a hop could not be counted at all — the backend reports the latter as
 * ``unknown`` and still sends 0, since there is no deviation it can name.
 * Colouring that green would claim a check that never ran, which is precisely
 * the failure this panel exists to catch.
 */
export function deviationTone(status: string, unexplained: number): 'good' | 'bad' | 'unknown' {
  if (unexplained > 0) return 'bad';
  return status === 'unknown' ? 'unknown' : 'good';
}
