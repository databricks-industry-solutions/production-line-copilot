import { describe, it, expect } from 'vitest';
import {
  asFlowStatus,
  dashDurationSeconds,
  flowBetween,
  flowThrough,
  supplyFeedsByMachine,
  FLOW_STATE_COLORS,
  FLOW_STATE_LABELS,
  FLOW_STATE_HINTS,
  type LiveStation,
} from './processFlowState';
import { STATUS_COLORS } from './constants';
import type { InventoryItem } from './useSSELiveData';

const station = (over: Partial<LiveStation> = {}): LiveStation => ({
  status: 'running',
  throughput_per_min: 400,
  speed_pct: 100,
  alarm_count: 0,
  ...over,
});

describe('flow states', () => {
  it('keeps the shared status colours and adds the one the app was missing', () => {
    for (const [key, value] of Object.entries(STATUS_COLORS)) {
      expect(FLOW_STATE_COLORS[key as keyof typeof STATUS_COLORS]).toBe(value);
    }
    // `blocked` is emitted by the cascade but absent from STATUS_COLORS, where
    // it silently became "idle". A flow view has to tell them apart.
    expect(FLOW_STATE_COLORS.blocked).toBeDefined();
    expect(FLOW_STATE_COLORS.blocked).not.toBe(FLOW_STATE_COLORS.idle);
  });

  it('explains starved and blocked as opposite directions, not as synonyms', () => {
    expect(FLOW_STATE_HINTS.starved).toMatch(/upstream/i);
    expect(FLOW_STATE_HINTS.blocked).toMatch(/downstream/i);
    expect(FLOW_STATE_LABELS.starved).not.toBe(FLOW_STATE_LABELS.blocked);
  });

  it('treats an unrecognised status as idle rather than crashing on it', () => {
    expect(asFlowStatus('running')).toBe('running');
    expect(asFlowStatus('blocked')).toBe('blocked');
    expect(asFlowStatus('something_new')).toBe('idle');
    expect(asFlowStatus(undefined)).toBe('idle');
  });
});

describe('what crosses the gap between two stations', () => {
  it('moves when both ends are running', () => {
    const state = flowBetween(station(), station());
    expect(state.moving).toBe(true);
    expect(state.tone).toBe('flowing');
    expect(state.speedPct).toBe(100);
  });

  it('stops when either end is stopped, and says which end', () => {
    const upstreamDown = flowBetween(station({ status: 'stopped', throughput_per_min: 0 }), station());
    expect(upstreamDown.moving).toBe(false);
    expect(upstreamDown.tone).toBe('halted');
    expect(upstreamDown.reason).toMatch(/upstream/i);

    const downstreamDown = flowBetween(station(), station({ status: 'stopped', throughput_per_min: 0 }));
    expect(downstreamDown.moving).toBe(false);
    expect(downstreamDown.reason).toMatch(/downstream/i);
  });

  it('shows a backed-up section as blocked, not as merely stopped', () => {
    const state = flowBetween(station({ status: 'blocked' }), station({ status: 'degraded' }));
    expect(state.tone).toBe('blocked');
    expect(state.moving).toBe(false);
  });

  it('shows a starving section as starved from either end', () => {
    expect(flowBetween(station({ status: 'starved' }), station()).tone).toBe('starved');
    expect(flowBetween(station(), station({ status: 'starved' })).tone).toBe('starved');
  });

  it('runs at the pace of the slower end, because a conveyor cannot do better', () => {
    const state = flowBetween(station({ speed_pct: 100 }), station({ speed_pct: 40, status: 'degraded' }));
    expect(state.speedPct).toBe(40);
    expect(state.tone).toBe('slowed');
    expect(state.moving).toBe(true);
  });

  it('carries the smaller of the two measured rates', () => {
    const state = flowBetween(station({ throughput_per_min: 400 }), station({ throughput_per_min: 120 }));
    expect(state.ratePerMin).toBe(120);
  });

  it('does not animate a section with no measured output', () => {
    const state = flowBetween(station({ throughput_per_min: 0 }), station({ throughput_per_min: 0 }));
    expect(state.moving).toBe(false);
  });

  it('says so plainly when there is no live state at all', () => {
    expect(flowBetween(undefined, station()).tone).toBe('unknown');
    expect(flowBetween(station(), undefined).moving).toBe(false);
  });
});

describe('dash pace', () => {
  it('runs a faster line with a shorter cycle', () => {
    expect(dashDurationSeconds(100)).toBeLessThan(dashDurationSeconds(50));
    expect(dashDurationSeconds(50)).toBeLessThan(dashDurationSeconds(10));
  });

  it('clamps at both ends so nothing strobes or appears frozen', () => {
    expect(dashDurationSeconds(100000)).toBeGreaterThanOrEqual(0.5);
    expect(dashDurationSeconds(0)).toBeLessThanOrEqual(6);
    expect(dashDurationSeconds(-5)).toBeLessThanOrEqual(6);
  });
});

const material = (over: Partial<InventoryItem> = {}): InventoryItem => ({
  material_id: 'CROWNS',
  on_hand_qty: 1000,
  safety_stock: 100,
  reorder_point: 200,
  status: 'OK',
  consumption_rate_per_hour: 100,
  feeds_machine_id: 'CAPPER',
  ...over,
});

describe('supply feeds', () => {
  const stations = new Map<string, LiveStation>([['CAPPER', station()]]);

  it('groups materials by the station they feed', () => {
    const feeds = supplyFeedsByMachine([material()], stations);
    expect(feeds.get('CAPPER')?.[0].materialId).toBe('CROWNS');
  });

  it('drops materials with no station rather than guessing one', () => {
    const feeds = supplyFeedsByMachine([material({ feeds_machine_id: undefined })], stations);
    expect(feeds.size).toBe(0);
  });

  it('computes hours of cover from the live draw', () => {
    const feeds = supplyFeedsByMachine([material({ on_hand_qty: 250, consumption_rate_per_hour: 100 })], stations);
    expect(feeds.get('CAPPER')?.[0].hoursLeft).toBe(2.5);
  });

  it('leaves cover unknown when nothing is being drawn', () => {
    const feeds = supplyFeedsByMachine([material({ consumption_rate_per_hour: 0 })], stations);
    expect(feeds.get('CAPPER')?.[0].hoursLeft).toBeNull();
    expect(feeds.get('CAPPER')?.[0].moving).toBe(false);
  });

  it('stops the feed when the station it feeds is not consuming', () => {
    const stopped = new Map<string, LiveStation>([['CAPPER', station({ status: 'stopped' })]]);
    expect(supplyFeedsByMachine([material()], stopped).get('CAPPER')?.[0].moving).toBe(false);
  });

  it('puts the material that will stop the station soonest first', () => {
    const feeds = supplyFeedsByMachine(
      [
        material({ material_id: 'FINE', status: 'OK' }),
        material({ material_id: 'GONE', status: 'CRITICAL' }),
        material({ material_id: 'LOWISH', status: 'LOW' }),
      ],
      stations,
    );
    expect(feeds.get('CAPPER')?.map((f) => f.materialId)).toEqual(['GONE', 'LOWISH', 'FINE']);
  });

  it('reads the status case-insensitively, since it arrives uppercase from SQL', () => {
    const feeds = supplyFeedsByMachine([material({ status: 'critical' })], stations);
    expect(feeds.get('CAPPER')?.[0].urgency).toBe('critical');
  });
});

describe('a section carried by a conveyor', () => {
  it('is the plain two-station case when there is no conveyor on it', () => {
    const a = station();
    const b = station();
    expect(flowThrough(a, undefined, b)).toEqual(flowBetween(a, b));
  });

  it('stops when the belt stops, however well the machines at each end run', () => {
    // The failure this exists for: a conveyor is a machine that breaks, and a
    // section drawn only from the stations either side would keep animating
    // product across a belt that is not turning.
    const flow = flowThrough(station(), station({ status: 'stopped' }), station());
    expect(flow.moving).toBe(false);
    expect(flow.tone).toBe('halted');
  });

  it('runs at the belt speed when the belt is the slowest part', () => {
    const flow = flowThrough(
      station({ speed_pct: 100, throughput_per_min: 400 }),
      station({ status: 'degraded', speed_pct: 40, throughput_per_min: 160 }),
      station({ speed_pct: 100, throughput_per_min: 400 }),
    );
    expect(flow.moving).toBe(true);
    expect(flow.speedPct).toBe(40);
    expect(flow.ratePerMin).toBe(160);
    expect(flow.tone).toBe('slowed');
  });

  it('still reports the worst end when the belt itself is fine', () => {
    const flow = flowThrough(station(), station(), station({ status: 'stopped' }));
    expect(flow.tone).toBe('halted');
    expect(flow.moving).toBe(false);
  });

  it('keeps flowing when all three are healthy', () => {
    const flow = flowThrough(station(), station(), station());
    expect(flow.moving).toBe(true);
    expect(flow.tone).toBe('flowing');
  });
});
