import { isoToEpoch } from './utils';
import type { EventFeedEvent } from '../components/EventFeed';

/** How long an event with no end and no resolve marker stays "active".
 *
 * event_log_bronze is append-only Zerobus ingest, so the producer cannot close
 * the original row — it appends a synthetic ``event_type='resolved'`` row and
 * the reconciliation below pairs them up. Any event whose resolve marker never
 * arrives is therefore active forever.
 *
 * That is not hypothetical. The backfill seeds one deliberately open event as
 * shift carryover, and nothing ever resolves it: no producer command sits
 * behind a seeded row, so Recover reports nothing to clear and the operator is
 * left with a red incident from a previous day that cannot be cleared by any
 * means the UI offers. One shift is the bound because carryover is defined
 * against the current shift — an unresolved event older than that was either
 * resolved without a marker or is stale data, and in neither case is it
 * something to act on now. */
export const STALE_AFTER_MS = 8 * 60 * 60 * 1000;

export function isEventActive(
  e: EventFeedEvent,
  currentTs: number,
  resolvedTsByMachine?: Map<string, number>
): boolean {
  if (e.timestamp_end) {
    return isoToEpoch(String(e.timestamp_end)) > currentTs;
  }
  const startTs = isoToEpoch(String(e.timestamp_start));
  if (resolvedTsByMachine) {
    const resolvedTs = resolvedTsByMachine.get(e.machine_id) ?? 0;
    if (resolvedTs > 0 && resolvedTs >= startTs) return false;
  }
  // Guard against a resolve marker that never comes. isoToEpoch returns 0 for
  // an unparseable timestamp; ageing those out would hide live rows, so only
  // apply the bound to a start we could actually read.
  if (startTs > 0 && currentTs - startTs > STALE_AFTER_MS) return false;
  return true;
}

/** Build the machine_id → latest 'resolved' timestamp map used by
 *  isEventActive(). Exported for App.tsx badge reuse. */
export function buildResolvedTsByMachine(events: EventFeedEvent[]): Map<string, number> {
  const m = new Map<string, number>();
  for (const e of events) {
    if (e.event_type === 'resolved' && e.machine_id) {
      const ts = isoToEpoch(String(e.timestamp_start));
      const prev = m.get(e.machine_id) ?? 0;
      if (ts > prev) m.set(e.machine_id, ts);
    }
  }
  return m;
}
