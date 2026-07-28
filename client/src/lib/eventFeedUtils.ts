import { isoToEpoch } from './utils';
import type { EventFeedEvent } from '../components/EventFeed';

export function isEventActive(
  e: EventFeedEvent,
  currentTs: number,
  resolvedTsByMachine?: Map<string, number>
): boolean {
  if (e.timestamp_end) {
    return isoToEpoch(String(e.timestamp_end)) > currentTs;
  }
  if (resolvedTsByMachine) {
    const resolvedTs = resolvedTsByMachine.get(e.machine_id) ?? 0;
    if (resolvedTs > 0) {
      const startTs = isoToEpoch(String(e.timestamp_start));
      if (resolvedTs >= startTs) return false;
    }
  }
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
