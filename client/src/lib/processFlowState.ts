/**
 * Turns live machine and material rows into what the process-flow view draws.
 * Used by `ProcessFlowSchematic` for station colours, connector motion, and supply feeds.
 * Pure so the rules about when product is moving can be asserted without a DOM.
 */
import { STATUS_COLORS } from './constants';
import type { InventoryItem } from './useSSELiveData';

/**
 * `blocked` is a real cascade status — output buffer full, downstream stalled —
 * but it is absent from the shared `STATUS_COLORS`, so everywhere else in the
 * app it lands on the "unknown" colour and reads as idle. On a flow diagram the
 * difference between starved and blocked is the diagnosis: starved means the
 * problem is upstream of you, blocked means it is downstream. They are given
 * opposite ends of the palette here for that reason.
 */
export const FLOW_STATE_COLORS = {
  ...STATUS_COLORS,
  blocked: '#ea580c',
} as const;

export type FlowMachineStatus = keyof typeof FLOW_STATE_COLORS;

export const FLOW_STATE_LABELS: Record<FlowMachineStatus, string> = {
  running: 'Running',
  stopped: 'Stopped',
  degraded: 'Degraded',
  starved: 'Starved — nothing arriving',
  blocked: 'Blocked — nowhere to put it',
  idle: 'Idle',
};

/** What each state means for the operator, shown on hover. */
export const FLOW_STATE_HINTS: Record<FlowMachineStatus, string> = {
  running: 'Producing at or near its design rate.',
  stopped: 'Not producing. Everything downstream will starve in turn.',
  degraded: 'Producing below its design rate.',
  starved: 'Waiting for product. The cause is upstream, not here.',
  blocked: 'Cannot discharge. The cause is downstream, not here.',
  idle: 'No live state for this station.',
};

export function asFlowStatus(status: string | undefined): FlowMachineStatus {
  return status != null && status in FLOW_STATE_COLORS ? (status as FlowMachineStatus) : 'idle';
}

export interface LiveStation {
  status: string;
  throughput_per_min: number;
  speed_pct: number | null;
  alarm_count: number;
}

export type FlowTone = 'flowing' | 'slowed' | 'starved' | 'blocked' | 'halted' | 'unknown';

export interface EdgeFlow {
  /** Whether to animate. False renders a still pipe, which is the honest look for a stopped line. */
  moving: boolean;
  /** Percent of design rate, the pace the dashes run at. */
  speedPct: number;
  /** Items per minute actually crossing, for the label. Null when unknown. */
  ratePerMin: number | null;
  tone: FlowTone;
  reason: string;
}

export const FLOW_TONE_COLORS: Record<FlowTone, string> = {
  flowing: '#22d3ee',
  slowed: '#eab308',
  starved: '#64748b',
  blocked: '#f97316',
  halted: '#ef4444',
  unknown: '#475569',
};

/**
 * What is crossing the gap between two adjacent stations.
 *
 * A conveyor moves only what the slower end allows, so both ends are consulted:
 * a running filler in front of a stopped capper is not filling anything for
 * long. The pace comes from `speed_pct` rather than `throughput_per_min`
 * because throughput is measured in different units along the line — bottles a
 * minute at the filler, cases a minute at the packer — and animating those
 * against each other would make the front of the line look twenty times faster
 * than the back for no physical reason. Percent of design rate is the one
 * quantity that means the same thing at every station.
 */
export function flowBetween(upstream: LiveStation | undefined, downstream: LiveStation | undefined): EdgeFlow {
  if (!upstream || !downstream) {
    return { moving: false, speedPct: 0, ratePerMin: null, tone: 'unknown', reason: 'No live state for this section.' };
  }

  const up = asFlowStatus(upstream.status);
  const down = asFlowStatus(downstream.status);
  const rate = Math.min(upstream.throughput_per_min ?? 0, downstream.throughput_per_min ?? 0);
  const pct = Math.max(0, Math.min(100, Math.min(upstream.speed_pct ?? 100, downstream.speed_pct ?? 100)));

  if (up === 'stopped' || down === 'stopped') {
    return {
      moving: false,
      speedPct: 0,
      ratePerMin: 0,
      tone: 'halted',
      reason: up === 'stopped' ? 'Upstream station is stopped.' : 'Downstream station is stopped.',
    };
  }
  if (up === 'blocked') {
    return {
      moving: false,
      speedPct: 0,
      ratePerMin: rate,
      tone: 'blocked',
      reason: 'Backed up — downstream cannot take it.',
    };
  }
  if (up === 'starved' || down === 'starved') {
    return { moving: false, speedPct: 0, ratePerMin: rate, tone: 'starved', reason: 'Nothing arriving from upstream.' };
  }
  if (rate <= 0) {
    return {
      moving: false,
      speedPct: 0,
      ratePerMin: rate,
      tone: 'unknown',
      reason: 'No measured output on this section.',
    };
  }

  const slowed = up === 'degraded' || down === 'degraded' || pct < 85;
  return {
    moving: true,
    speedPct: pct,
    ratePerMin: rate,
    tone: slowed ? 'slowed' : 'flowing',
    reason: slowed ? `Moving at ${Math.round(pct)}% of design rate.` : 'Moving at design rate.',
  };
}

/**
 * Worst first. Folding the two halves of a section has to keep the diagnosis
 * rather than average it away: a section whose conveyor has stopped is stopped,
 * however well the machines at each end are running.
 */
const TONE_SEVERITY: Record<FlowTone, number> = {
  halted: 0,
  blocked: 1,
  starved: 2,
  unknown: 3,
  slowed: 4,
  flowing: 5,
};

/**
 * What is crossing a section that has a conveyor in the middle of it.
 *
 * The conveyor is a machine like any other — it stops, it jams, it runs slow —
 * so a section carried by one is limited by three things rather than two. With
 * no conveyor this is just {@link flowBetween}, which is the common case on
 * this line.
 */
export function flowThrough(
  upstream: LiveStation | undefined,
  via: LiveStation | undefined,
  downstream: LiveStation | undefined
): EdgeFlow {
  if (!via) return flowBetween(upstream, downstream);

  const first = flowBetween(upstream, via);
  const second = flowBetween(via, downstream);
  const worst = TONE_SEVERITY[first.tone] <= TONE_SEVERITY[second.tone] ? first : second;

  // Only `slowed` and `flowing` move, and both rank below every stalled tone,
  // so reaching here with a moving `worst` means both halves are moving.
  if (!worst.moving) return worst;

  const rates = [first.ratePerMin, second.ratePerMin].filter((r): r is number => r != null);
  return {
    ...worst,
    speedPct: Math.min(first.speedPct, second.speedPct),
    ratePerMin: rates.length > 0 ? Math.min(...rates) : null,
  };
}

/** Seconds for one dash to travel one dash-period. Faster line, shorter cycle. */
const DASH_BASE_SECONDS = 0.9;
const DASH_MIN_SECONDS = 0.5;
const DASH_MAX_SECONDS = 6;

export function dashDurationSeconds(speedPct: number): number {
  if (speedPct <= 0) return DASH_MAX_SECONDS;
  const seconds = DASH_BASE_SECONDS / (speedPct / 100);
  return Math.min(DASH_MAX_SECONDS, Math.max(DASH_MIN_SECONDS, Math.round(seconds * 100) / 100));
}

export type SupplyUrgency = 'ok' | 'low' | 'critical';

/**
 * Material feeds, in violet so they read as a different kind of thing from the
 * machine states they hang over. One step darker than the tints the schematic
 * used while its canvas was near-black: the same hues on a near-white canvas
 * were washed out, and a chip saying a material is nearly out has to be read.
 */
export const SUPPLY_COLORS: Record<SupplyUrgency, string> = {
  ok: '#7c3aed',
  low: '#d97706',
  critical: '#dc2626',
};

export interface SupplyFeed {
  materialId: string;
  machineId: string;
  urgency: SupplyUrgency;
  /** Hours of stock left at the current draw. Null when nothing is being consumed. */
  hoursLeft: number | null;
  consumptionPerHour: number;
  onHand: number;
  moving: boolean;
}

function urgencyOf(item: InventoryItem): SupplyUrgency {
  const status = (item.status ?? '').toUpperCase();
  if (status === 'CRITICAL') return 'critical';
  if (status === 'LOW') return 'low';
  return 'ok';
}

/**
 * Group live materials by the station they feed.
 *
 * `feeds_machine_id` comes from the producer and is what makes these lines real
 * rather than decorative — without it the app knows a material is low but not
 * what stops when it runs out. Materials arriving without it are dropped rather
 * than guessed at.
 */
export function supplyFeedsByMachine(
  inventory: InventoryItem[],
  stations: Map<string, LiveStation>
): Map<string, SupplyFeed[]> {
  const byMachine = new Map<string, SupplyFeed[]>();

  for (const item of inventory) {
    const machineId = item.feeds_machine_id;
    if (!machineId) continue;

    const draw = item.consumption_rate_per_hour ?? 0;
    const station = stations.get(machineId);
    const status = asFlowStatus(station?.status);
    const feed: SupplyFeed = {
      materialId: item.material_id,
      machineId,
      urgency: urgencyOf(item),
      hoursLeft: draw > 0 ? Math.round((item.on_hand_qty / draw) * 10) / 10 : null,
      consumptionPerHour: draw,
      onHand: item.on_hand_qty,
      // A stopped station consumes nothing, whatever the last reported rate said.
      moving: draw > 0 && status !== 'stopped' && status !== 'starved',
    };

    const list = byMachine.get(machineId);
    if (list) list.push(feed);
    else byMachine.set(machineId, [feed]);
  }

  // Worst-off material first, so a station showing one chip shows the one that
  // will stop it soonest.
  const rank: Record<SupplyUrgency, number> = { critical: 0, low: 1, ok: 2 };
  for (const feeds of byMachine.values()) {
    feeds.sort((a, b) => rank[a.urgency] - rank[b.urgency] || a.materialId.localeCompare(b.materialId));
  }
  return byMachine;
}
