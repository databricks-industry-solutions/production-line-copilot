/**
 * Streams live line snapshots from `/api/serving/stream` into React state.
 * Used by `App` as the single source for machines, KPI, events, and commands.
 * Keeps the last good frame during transient SSE drops so the UI avoids false outage flicker.
 */
import { useState, useEffect, useRef, useCallback } from 'react';

// Each SSE payload from /api/serving/stream is a real per-second snapshot
// from the Lakebase playback cursor — no client-side smoothing required.
// Values change every poll because the backend advances its cursor by one
// distinct timestamp per SSE tick.

export interface MachineState {
  shift_id: string;
  machine_id: string;
  ts: string;
  status: string;
  throughput_per_min: number;
  speed_pct: number;
  alarm_count: number;
  /** Present when Gold / serving row includes OT enrichment columns */
  primary_sensor_1?: number | null;
  primary_sensor_2?: number | null;
  material_level_pct?: number | null;
}

export interface ActiveCommand {
  command_id: string;
  scenario_id: string;
  target_machine_id: string;
  severity: string;
  status: string;
  created_at: string;
}

export interface SSEEvent {
  event_id: string;
  machine_id: string;
  event_type: string;
  severity: string;
  timestamp_start: string;
  /** Set when the event has ended. Producer is Zerobus-append-only and
   *  cannot UPDATE the original row — instead it emits a separate
   *  ``event_type='resolved'`` row keyed by the same machine. UI code
   *  reconciles both signals (see ``EventFeed``/``KPIStrip``). */
  timestamp_end?: string | null;
  description: string;
}

export interface InventoryItem {
  material_id: string;
  on_hand_qty: number;
  safety_stock: number;
  reorder_point: number;
  status: string;
  consumption_rate_per_hour?: number;
  /** When set (sim SSE), material is consumed primarily at this machine */
  feeds_machine_id?: string;
}

interface SSEPayload {
  machines?: MachineState[];
  kpi?: Record<string, unknown>;
  events?: SSEEvent[];
  commands?: ActiveCommand[];
  inventory?: InventoryItem[];
  tick?: number;
  source_mode?: 'lakebase';
  stream_last_update_ts?: string;
  source_last_event_ts?: string;
  ingestion_latency_ms?: number;
  cursor_lag_seconds?: number;
  /** "live" when cursor advanced this poll, "waiting" when no new row was
   *  available (the buffer underran or producer is stalled). */
  status?: 'live' | 'waiting';
  /** Convenience flag mirroring status === "waiting". */
  waiting?: boolean;
  error?: string;
}

export interface UseSSELiveDataResult {
  machines: MachineState[];
  kpi: Record<string, unknown> | null;
  events: SSEEvent[];
  commands: ActiveCommand[];
  inventory: InventoryItem[];
  connected: boolean;
  /** True once the stream has opened successfully at least once, and stays
   *  true across subsequent reconnects. Lets the UI distinguish a genuine
   *  first-load (show skeletons) from a brief reconnect blip (keep the last
   *  frame visible instead of blanking the whole dashboard). */
  hasConnected: boolean;
  error: string | null;
  tick: number;
  sourceMode: 'lakebase' | null;
  streamLastUpdateTs: string | null;
  sourceLastEventTs: string | null;
  ingestionLatencyMs: number | null;
  cursorLagSeconds: number | null;
  /** True when the latest SSE tick reported the cursor had no newer row
   *  to emit (producer paused, buffer underrun, or fresh restart). UI uses
   *  this to render a "Live · waiting for new value" indicator while still
   *  showing the last known frame. */
  waiting: boolean;
}

// why: brief reconnect blips are common during restarts; wait before surfacing an error banner.
const GRACE_MS = 4000;

/**
 * Subscribe to `/api/serving/stream` SSE endpoint.
 *
 * Backend advances a per-shift cursor over `ai_serving.*_history` tables and
 * emits one snapshot per second. When no new row exists yet the payload
 * carries `status: "waiting"` (we surface that via the `waiting` field) but
 * the previous frame stays visible.
 */
export function useSSELiveData(enabled = true): UseSSELiveDataResult {
  const [machines, setMachines] = useState<MachineState[]>([]);
  const [kpi, setKpi] = useState<Record<string, unknown> | null>(null);
  const [events, setEvents] = useState<SSEEvent[]>([]);
  const [commands, setCommands] = useState<ActiveCommand[]>([]);
  const [inventory, setInventory] = useState<InventoryItem[]>([]);
  const [connected, setConnected] = useState(false);
  const [hasConnected, setHasConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const [sourceMode, setSourceMode] = useState<'lakebase' | null>(null);
  const [streamLastUpdateTs, setStreamLastUpdateTs] = useState<string | null>(null);
  const [sourceLastEventTs, setSourceLastEventTs] = useState<string | null>(null);
  const [ingestionLatencyMs, setIngestionLatencyMs] = useState<number | null>(null);
  const [cursorLagSeconds, setCursorLagSeconds] = useState<number | null>(null);
  const [waiting, setWaiting] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const graceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const everConnectedRef = useRef(false);

  const clearGrace = useCallback(() => {
    if (graceTimerRef.current) {
      clearTimeout(graceTimerRef.current);
      graceTimerRef.current = null;
    }
  }, []);

  useEffect(() => {
    if (!enabled) {
      esRef.current?.close();
      esRef.current = null;
      clearGrace();
      const timer = window.setTimeout(() => {
        setConnected(false);
      }, 0);
      return () => window.clearTimeout(timer);
    }

    const es = new EventSource('/api/serving/stream');
    esRef.current = es;

    es.onopen = () => {
      everConnectedRef.current = true;
      clearGrace();
      setConnected(true);
      setHasConnected(true);
      setError(null);
    };

    es.onmessage = (ev: MessageEvent<string>) => {
      try {
        const payload = JSON.parse(ev.data) as SSEPayload;
        // Backend dedups + advances the cursor; the client just renders.
        if (payload.machines) setMachines(payload.machines);
        if (payload.kpi !== undefined) setKpi(payload.kpi);
        if (payload.events) setEvents(payload.events);
        if (payload.commands) setCommands(payload.commands);
        if (payload.inventory) setInventory(payload.inventory);
        if (payload.tick != null) setTick(payload.tick);
        if (payload.source_mode) setSourceMode(payload.source_mode);
        if (payload.stream_last_update_ts != null) setStreamLastUpdateTs(payload.stream_last_update_ts);
        if (payload.source_last_event_ts != null) setSourceLastEventTs(payload.source_last_event_ts);
        if (payload.ingestion_latency_ms != null) setIngestionLatencyMs(payload.ingestion_latency_ms);
        if (payload.cursor_lag_seconds != null) setCursorLagSeconds(payload.cursor_lag_seconds);
        // ``waiting`` semantics: if the backend explicitly reports it, use
        // that; otherwise fall back to "did this payload bring any new
        // machine/kpi rows?" so legacy backends still light up the indicator.
        if (typeof payload.waiting === 'boolean') {
          setWaiting(payload.waiting);
        } else if (payload.status === 'waiting') {
          setWaiting(true);
        } else if (payload.machines || payload.kpi || payload.inventory) {
          setWaiting(false);
        }
        if (payload.error) setError(payload.error);
        else setError(null);
      } catch {
        // heartbeat or malformed — ignore
      }
    };

    es.onerror = () => {
      setConnected(false);
      if (!everConnectedRef.current) return;
      clearGrace();
      graceTimerRef.current = setTimeout(() => {
        setError('SSE connection lost — reconnecting...');
      }, GRACE_MS);
    };

    return () => {
      es.close();
      esRef.current = null;
      clearGrace();
    };
  }, [enabled, clearGrace]);

  return {
    machines,
    kpi,
    events,
    commands,
    inventory,
    connected,
    hasConnected,
    error,
    tick,
    sourceMode,
    streamLastUpdateTs,
    sourceLastEventTs,
    ingestionLatencyMs,
    cursorLagSeconds,
    waiting,
  };
}
