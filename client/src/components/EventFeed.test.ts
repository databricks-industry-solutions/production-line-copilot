import { describe, it, expect } from 'vitest';
// Imported from lib rather than through EventFeed.tsx, which re-exports these
// but also drags in the AppKit chart components. Those resolve a nested copy of
// echarts-for-react that is not installed, so the whole file failed to load and
// every assertion in it silently stopped running.
import {
  buildResolvedTsByMachine,
  isEventActive,
  STALE_AFTER_MS,
} from '../lib/eventFeedUtils';
import type { EventFeedEvent } from './EventFeed';

// Producer is Zerobus-append-only, so a resolved incident is represented
// by TWO rows in event_log_history:
//   1. an incident_start row (severity=critical/major/minor, timestamp_end=NULL)
//   2. a synthetic event_type='resolved' row (severity=info)
// These tests pin the badge/filter semantics that App.tsx + EventFeed
// share so we don't regress to double-counting the same incident.

function mkEvent(over: Partial<EventFeedEvent> = {}): EventFeedEvent {
  return {
    event_id: 'e1',
    machine_id: 'FILLER',
    event_type: 'machine_breakdown',
    severity: 'critical',
    timestamp_start: '2026-05-11T07:00:00Z',
    timestamp_end: null,
    duration_minutes: 0,
    root_cause: 'machine_breakdown',
    description: 'Filler down',
    ...over,
  };
}

describe('buildResolvedTsByMachine', () => {
  it('captures the latest resolved timestamp per machine', () => {
    const events: EventFeedEvent[] = [
      mkEvent({ event_id: 's', event_type: 'machine_breakdown' }),
      mkEvent({
        event_id: 'r1',
        event_type: 'resolved',
        severity: 'info',
        timestamp_start: '2026-05-11T07:01:00Z',
      }),
      mkEvent({
        event_id: 'r2',
        event_type: 'resolved',
        severity: 'info',
        timestamp_start: '2026-05-11T07:05:00Z',
      }),
    ];
    const m = buildResolvedTsByMachine(events);
    expect(m.get('FILLER')).toBe(Date.UTC(2026, 4, 11, 7, 5, 0));
  });

  it('ignores non-resolved events', () => {
    const events: EventFeedEvent[] = [
      mkEvent({ event_type: 'machine_breakdown' }),
      mkEvent({ event_type: 'speed_loss', severity: 'minor' }),
    ];
    expect(buildResolvedTsByMachine(events).size).toBe(0);
  });
});

describe('isEventActive', () => {
  const now = Date.UTC(2026, 4, 11, 8, 0, 0); // 1h after start

  it('treats own timestamp_end in the future as still active', () => {
    const e = mkEvent({
      timestamp_end: '2026-05-11T09:00:00Z',
    });
    expect(isEventActive(e, now)).toBe(true);
  });

  it('treats own timestamp_end in the past as resolved', () => {
    const e = mkEvent({
      timestamp_end: '2026-05-11T07:30:00Z',
    });
    expect(isEventActive(e, now)).toBe(false);
  });

  it('treats start row with NULL timestamp_end as active without a resolve marker', () => {
    expect(isEventActive(mkEvent(), now)).toBe(true);
  });

  it('treats start row as resolved when a later resolve marker exists for the same machine', () => {
    const resolvedMap = new Map<string, number>([['FILLER', Date.UTC(2026, 4, 11, 7, 5, 0)]]);
    expect(isEventActive(mkEvent(), now, resolvedMap)).toBe(false);
  });

  it('does NOT treat as resolved when the resolve marker pre-dates this start', () => {
    // start at 07:00, "resolve" at 06:00 → unrelated older event, ignore.
    const resolvedMap = new Map<string, number>([['FILLER', Date.UTC(2026, 4, 11, 6, 0, 0)]]);
    expect(isEventActive(mkEvent(), now, resolvedMap)).toBe(true);
  });
});

describe('an open event whose resolve marker never arrives', () => {
  // The backfill seeds one deliberately open event as shift carryover, and
  // nothing ever resolves it: no producer command sits behind a seeded row, so
  // Recover reports nothing to clear. Without a bound the operator keeps a red
  // incident from a previous day that no control in the UI can clear — seen in
  // prod as a FILLER minor_stop from the day before, still listed as active.
  const start = '2026-05-11T07:00:00Z';
  const startMs = Date.UTC(2026, 4, 11, 7, 0, 0);

  it('is still active inside the shift it belongs to', () => {
    expect(isEventActive(mkEvent({ timestamp_start: start }), startMs + STALE_AFTER_MS - 1000)).toBe(
      true
    );
  });

  it('ages out once it is older than a shift', () => {
    expect(isEventActive(mkEvent({ timestamp_start: start }), startMs + STALE_AFTER_MS + 1000)).toBe(
      false
    );
  });

  it('ages out the day-old carryover the seed leaves behind', () => {
    const dayLater = startMs + 24 * 60 * 60 * 1000;
    expect(isEventActive(mkEvent({ timestamp_start: start }), dayLater, new Map())).toBe(false);
  });

  it('does not age out an event that carries its own future end', () => {
    // An explicit timestamp_end is authoritative; the bound must not override it.
    const e = mkEvent({ timestamp_start: start, timestamp_end: '2026-05-13T09:00:00Z' });
    expect(isEventActive(e, startMs + 25 * 60 * 60 * 1000)).toBe(true);
  });

  it('leaves an unparseable start alone rather than hiding it', () => {
    // isoToEpoch yields 0 for junk; ageing that out would hide live rows.
    expect(isEventActive(mkEvent({ timestamp_start: 'not-a-date' }), Date.now())).toBe(true);
  });
});

describe('badge semantics (active-only counting)', () => {
  // This mirrors the activeIncidentBadge logic in App.tsx so a refactor
  // there can't silently start double-counting resolution markers.
  function countActive(events: EventFeedEvent[], at: number): number {
    const resolved = buildResolvedTsByMachine(events);
    let n = 0;
    for (const e of events) {
      if (e.event_type === 'resolved') continue;
      if ((e.severity ?? '').toLowerCase() === 'info') continue;
      if (isEventActive(e, at, resolved)) n++;
    }
    return n;
  }

  it('counts an open critical incident as 1', () => {
    const events = [mkEvent()];
    expect(countActive(events, Date.UTC(2026, 4, 11, 8, 0, 0))).toBe(1);
  });

  it('drops the count to 0 when the matching resolve marker arrives', () => {
    const events: EventFeedEvent[] = [
      mkEvent({ event_id: 'start' }),
      mkEvent({
        event_id: 'res',
        event_type: 'resolved',
        severity: 'info',
        timestamp_start: '2026-05-11T07:05:00Z',
      }),
    ];
    expect(countActive(events, Date.UTC(2026, 4, 11, 8, 0, 0))).toBe(0);
  });

  it('never counts an info-severity / event_type=resolved row by itself', () => {
    const events: EventFeedEvent[] = [
      mkEvent({
        event_id: 'r-only',
        event_type: 'resolved',
        severity: 'info',
        timestamp_start: '2026-05-11T07:05:00Z',
      }),
    ];
    expect(countActive(events, Date.UTC(2026, 4, 11, 8, 0, 0))).toBe(0);
  });

  it('counts open + ignores closed when both exist on the same machine', () => {
    const events: EventFeedEvent[] = [
      mkEvent({ event_id: 's1', timestamp_start: '2026-05-11T07:00:00Z' }),
      mkEvent({
        event_id: 'r1',
        event_type: 'resolved',
        severity: 'info',
        timestamp_start: '2026-05-11T07:10:00Z',
      }),
      // Second flare-up — newer than the resolve, so still active.
      mkEvent({
        event_id: 's2',
        event_type: 'speed_loss',
        severity: 'major',
        timestamp_start: '2026-05-11T07:30:00Z',
      }),
    ];
    expect(countActive(events, Date.UTC(2026, 4, 11, 8, 0, 0))).toBe(1);
  });
});
