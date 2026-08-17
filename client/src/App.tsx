/**
 * Composes the full production-manager UI: live line view, incidents, chat, ops, and quality tabs.
 * Used as the root screen mounted from `main.tsx`, with data sourced from SSE + incident APIs.
 * Prioritizes operator-trust safeguards (status overrides, sticky reconciliation, pending banners) over UI minimalism.
 */
import { useState, useMemo, useCallback, useRef, useEffect, lazy, Suspense } from 'react';
import { cn, toNumber, isoToEpoch } from './lib/utils';
import { MACHINE_LAYOUT } from './lib/constants';
import { deriveMachineReadings, flowNeighbors, getMachineMeta } from './lib/machineMeta';
import { ErrorBoundary } from './ErrorBoundary';
import IncidentPanel from './components/IncidentPanel';
import { useIncidentApi } from './lib/useIncidentApi';
import { useSSELiveData } from './lib/useSSELiveData';
import { shiftProgress, SHIFT_PLANNED_CASES } from './lib/shiftWindow';
import ProcessFlowSchematic from './components/ProcessFlowSchematic';
import KPIStrip from './components/KPIStrip';
import InventoryPanel from './components/InventoryPanel';
import EventFeed from './components/EventFeed';
import { isEventActive, buildResolvedTsByMachine } from './lib/eventFeedUtils';
import { emptyKpiHoldState, resolveKpiFrame, type KpiHoldState } from './lib/kpiHold';
import ChatPanel from './components/ChatPanel';
import AboutTab from './components/AboutTab';
import DemoGuideTab from './components/DemoGuideTab';
import MultiAITab from './components/MultiAITab';
import MachineDetailCard from './components/MachineDetailCard';
import CostObservabilityTab from './components/CostObservabilityTab';
// Split out: this is the only tab that draws charts, and the charting engine
// behind them more than doubles the main bundle. An operator watching the line
// should not download it to do that.
const DataQualityTab = lazy(() => import('./components/DataQualityTab'));
import AdminJobControls from './components/AdminJobControls';
import { DEFAULT_TAB, searchWithTab, tabFromSearch, type TabId } from './lib/tabRouting';

// Mirrors ``apply_incident`` in ``src/streaming_sim/incidents.py`` — kept in
// sync manually because the client must override tile color the moment a
// command becomes active, before the actual machine_state row arrives via
// the bronze→gold→Lakebase pipeline. New scenarios server-side should be
// reflected here.
const SCENARIO_TO_STATUS: Record<string, 'stopped' | 'degraded'> = {
  machine_breakdown: 'stopped',
  supply_delay: 'stopped',
  changeover_delay: 'stopped',
  minor_stops: 'stopped',
  safety_incident: 'stopped',
  zone_lockout: 'stopped',
  sensor_failure: 'degraded',
  speed_loss: 'degraded',
  maintenance_overdue: 'degraded',
  vibration_anomaly: 'degraded',
  quality_deviation: 'degraded',
  contamination: 'degraded',
  material_shortage: 'degraded',
};

function inferStatusFromScenario(scenarioId: string | null | undefined): 'stopped' | 'degraded' | null {
  if (!scenarioId) return null;
  return SCENARIO_TO_STATUS[scenarioId] ?? null;
}

function App() {
  const [path, setPath] = useState<string>(() => window.location.pathname || '/');
  const [selectedMachine, setSelectedMachine] = useState<string | null>(null);
  const [chatQuery, setChatQuery] = useState<{ text: string; id: number; overrideTimestamp?: string }>({
    text: '',
    id: 0,
  });
  // Seeded from the URL so a shared link opens on the view it names — the Demo
  // Guide's walkthrough buttons are the main producer of those links.
  const [activeTab, setActiveTab] = useState<TabId>(() => tabFromSearch(window.location.search) ?? DEFAULT_TAB);
  const incidentApi = useIncidentApi();
  const [simRunning, setSimRunning] = useState(true);
  // Hide the "Trigger Incident" simulator card by default so the dashboard
  // reads as a real plant view. Real-time controls (▶ Start / ⏸ Pause /
  // ↺ Clean Restart) and operator surfaces (Active Incidents, Resolve,
  // Recover, status badge, Admin) stay visible regardless. Persisted
  // per-browser via localStorage.
  const [showTriggerSim, setShowTriggerSim] = useState<boolean>(() => {
    if (typeof window === 'undefined') return false;
    try {
      return window.localStorage.getItem('lmak.show_trigger_sim') === '1';
    } catch {
      return false;
    }
  });
  useEffect(() => {
    try {
      window.localStorage.setItem('lmak.show_trigger_sim', showTriggerSim ? '1' : '0');
    } catch {
      // ignore (private mode / quota)
    }
  }, [showTriggerSim]);
  const sseLive = useSSELiveData(simRunning);
  const busyRef = useRef(false);
  // Set when the user triggers a producer-side action (restart / incident).
  // Cleared automatically once the SSE source_last_event_ts crosses this
  // wall-clock value (proving the producer cycle has reached the app), or
  // after a 60 s safety timeout. Used to render a "waiting for fresh data"
  // banner so users understand the ~30 s e2e latency.
  const [pendingSinceMs, setPendingSinceMs] = useState<number | null>(null);
  const [pendingReason, setPendingReason] = useState<'restart' | 'incident' | null>(null);
  // Detail payload from the most recent /sim/restart so the banner can show
  // the user what actually happened (UC commands cleared, Lakebase wipe,
  // producer job cancel + run_now). The user complained that the old banner
  // only mentioned "Producer → Lakebase ai_serving.*_history" and hid the
  // fact that we also restart the whole producer job.
  const [restartDetails, setRestartDetails] = useState<RestartDetails | null>(null);

  useEffect(() => {
    if (pendingSinceMs == null) return;
    if (!sseLive.sourceLastEventTs) return;
    const t = Date.parse(sseLive.sourceLastEventTs);
    if (Number.isFinite(t) && t >= pendingSinceMs) {
      setPendingSinceMs(null);
      setPendingReason(null);
      setRestartDetails(null);
    }
  }, [sseLive.sourceLastEventTs, pendingSinceMs]);

  useEffect(() => {
    if (pendingSinceMs == null) return;
    // why: producer restart + first fresh row usually lands well under one minute; avoid stale "pending" UX.
    const timer = setTimeout(() => {
      setPendingSinceMs(null);
      setPendingReason(null);
      setRestartDetails(null);
    }, 60_000);
    return () => clearTimeout(timer);
  }, [pendingSinceMs]);

  const navigate = useCallback((nextPath: string) => {
    if (window.location.pathname !== nextPath) {
      window.history.pushState({}, '', nextPath);
    }
    setPath(nextPath);
  }, []);

  /**
   * Switch tabs and leave the URL saying so, so Back works and the address bar
   * is worth copying. pushState rather than replaceState because a walkthrough
   * that cannot be stepped back through is worse than one that never moved.
   */
  const selectTab = useCallback((tab: TabId) => {
    setActiveTab(tab);
    const nextSearch = searchWithTab(window.location.search, tab);
    if (window.location.search !== nextSearch) {
      window.history.pushState({}, '', `${window.location.pathname}${nextSearch}`);
    }
  }, []);

  useEffect(() => {
    const onPop = () => {
      setPath(window.location.pathname || '/');
      setActiveTab(tabFromSearch(window.location.search) ?? DEFAULT_TAB);
    };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  const handleSimRestart = useCallback(async () => {
    if (busyRef.current) return;
    busyRef.current = true;
    try {
      setSimRunning(false);
      const res = await fetch('/api/sim/restart', { method: 'POST' });
      if (!res.ok) console.error('Restart failed:', res.status);
      let details: RestartDetails | null = null;
      try {
        details = (await res.json()) as RestartDetails;
      } catch {
        details = null;
      }
      await new Promise((r) => setTimeout(r, 800));
      setPendingSinceMs(Date.now());
      setPendingReason('restart');
      setRestartDetails(details);
    } catch (err) {
      console.error('Restart error:', err);
    } finally {
      setSimRunning(true);
      busyRef.current = false;
    }
  }, []);

  const handleTriggerIncident = useCallback<typeof incidentApi.triggerIncident>(
    async (params) => {
      const result = await incidentApi.triggerIncident(params);
      setPendingSinceMs(Date.now());
      setPendingReason('incident');
      return result;
    },
    [incidentApi]
  );

  // Wraps the recover-machine API call with the same "waiting for fresh
  // data" banner used by trigger/restart, since the recovery cascade also
  // takes ~30 s to roll through producer → streaming quality-gate → Lakebase → SSE.
  const handleRecoverMachine = useCallback(
    async (machineId: string) => {
      const result = await incidentApi.recoverMachine(machineId);
      if (result.count > 0) {
        setPendingSinceMs(Date.now());
        setPendingReason('incident');
      }
      return result;
    },
    [incidentApi]
  );

  const handleSimStart = useCallback(async () => {
    if (busyRef.current) return;
    busyRef.current = true;
    try {
      await fetch('/api/sim/resume', { method: 'POST' });
      setSimRunning(true);
    } finally {
      busyRef.current = false;
    }
  }, []);

  const handleSimStop = useCallback(async () => {
    if (busyRef.current) return;
    busyRef.current = true;
    try {
      await fetch('/api/sim/stop', { method: 'POST' });
      setSimRunning(false);
    } finally {
      busyRef.current = false;
    }
  }, []);

  const handleAskAgent = useCallback(
    (query: string) => {
      setChatQuery((prev) => ({ text: query, id: prev.id + 1 }));
      selectTab('alerts');
    },
    [selectTab]
  );

  const handleMachineClick = useCallback((machineId: string) => {
    setSelectedMachine((prev) => (prev === machineId ? null : machineId));
  }, []);

  const selectedMachineDetail = useMemo(() => {
    if (!selectedMachine) return null;
    const raw = sseLive.machines.find((m) => m.machine_id === selectedMachine);
    const meta = getMachineMeta(selectedMachine);
    const neighbors = flowNeighbors(selectedMachine);
    if (!raw) {
      return {
        machineId: selectedMachine,
        meta,
        neighbors,
        state: null as null | {
          status: string;
          throughput_per_min: number;
          speed_pct: number | null;
          primary_sensor_1: number | null;
          primary_sensor_2: number | null;
          material_level_pct: number | null;
          alarm_count: number;
          ts: string;
        },
        readingsEstimated: false,
      };
    }
    const pickNum = (v: unknown): number | null => {
      if (v == null || v === '') return null;
      const n = toNumber(v);
      return Number.isFinite(n) ? n : null;
    };
    const derived = deriveMachineReadings(selectedMachine, {
      status: raw.status,
      throughput_per_min: toNumber(raw.throughput_per_min),
      speed_pct: raw.speed_pct != null ? toNumber(raw.speed_pct) : null,
      alarm_count: toNumber(raw.alarm_count),
    });
    const hasServerPrimary =
      pickNum(raw.primary_sensor_1) != null ||
      pickNum(raw.primary_sensor_2) != null ||
      pickNum(raw.material_level_pct) != null;
    const state = {
      status: raw.status,
      throughput_per_min: toNumber(raw.throughput_per_min),
      speed_pct: raw.speed_pct != null ? toNumber(raw.speed_pct) : null,
      primary_sensor_1: pickNum(raw.primary_sensor_1) ?? derived.primary_sensor_1,
      primary_sensor_2: pickNum(raw.primary_sensor_2) ?? derived.primary_sensor_2,
      material_level_pct: pickNum(raw.material_level_pct) ?? derived.material_level_pct,
      alarm_count: toNumber(raw.alarm_count),
      ts: raw.ts != null && raw.ts !== '' ? String(raw.ts) : '',
    };
    return {
      machineId: selectedMachine,
      meta,
      neighbors,
      state,
      readingsEstimated: !hasServerPrimary,
    };
  }, [selectedMachine, sseLive.machines]);

  // Build machine state map from SSE data (same shape as components expect).
  //
  // Why we override status from ``commands``: between "operator triggers
  // incident" and "FILLER tile turns red" data has to traverse
  //   trigger → UC scenario_commands → producer poll → bronze (Zerobus)
  //   → streaming_quality_sync (gold) → Lakebase → SSE cursor read
  // (typically 3–6 s). And during the incident, brief gaps in the producer
  // pipeline can let a stale ``running`` row leak through, making the tile
  // flicker green↔red. The IncidentPanel already shows the incident
  // immediately because ``commands`` is read fresh from UC every poll, so
  // tile color and incident card were diverging.
  //
  // Fix: once the producer has acknowledged the incident (status='active'
  // in scenario_commands), force the tile color to match the operator's
  // view. The override drops the moment the command leaves the active
  // list (resolve_requested or resolved), letting the real machine_state
  // take over again.
  //
  // Defense-in-depth: ``read_commands_active`` has a 1 s cache that falls
  // back to ``rows=[]`` on transient warehouse errors. A single failed poll
  // would unmask ``machines.status`` (which can lag the actual incident by
  // 3–6 s of pipeline latency) and flip the tile green→red→green. We
  // harden against this by:
  //   1. Cross-checking ``events`` for an unresolved incident_start row on
  //      the same machine (events are append-only Lakebase reads, no
  //      warehouse round-trip, so they survive warehouse blips).
  //   2. A ~5 s sticky cache in ``stickyOverridesRef`` so an override that
  //      WAS active in the last few ticks remains active even if both
  //      sources go briefly empty.
  const stickyOverridesRef = useRef<Map<string, { status: 'stopped' | 'degraded'; expiresAt: number }>>(new Map());
  // why: smooth over short warehouse/cache hiccups without masking real resolves for long.
  const STICKY_OVERRIDE_MS = 5000;

  const machineStatusOverrides = useMemo(() => {
    const overrides = new Map<string, 'stopped' | 'degraded'>();
    const now = Date.now();

    // ── Source 1: active scenario_commands (preferred path) ───────────
    for (const cmd of sseLive.commands) {
      if (!cmd.target_machine_id) continue;
      const expected = inferStatusFromScenario(cmd.scenario_id);
      if (expected) overrides.set(cmd.target_machine_id, expected);
    }

    // ── Source 2: events (incident_start without later resolved) ──────
    // Same reconciliation pattern as EventFeed/KPIStrip — Zerobus is
    // append-only so resolution is signalled by a separate row.
    const resolvedTsByMachine = new Map<string, number>();
    for (const e of sseLive.events) {
      if (e.event_type === 'resolved' && e.machine_id) {
        const ts = isoToEpoch(String(e.timestamp_start));
        const prev = resolvedTsByMachine.get(e.machine_id) ?? 0;
        if (ts > prev) resolvedTsByMachine.set(e.machine_id, ts);
      }
    }
    for (const e of sseLive.events) {
      if (!e.machine_id || overrides.has(e.machine_id)) continue;
      if (e.event_type === 'resolved') continue;
      const startTs = isoToEpoch(String(e.timestamp_start));
      if (e.timestamp_end && isoToEpoch(String(e.timestamp_end)) <= now) continue;
      const resolvedTs = resolvedTsByMachine.get(e.machine_id) ?? 0;
      if (resolvedTs > 0 && resolvedTs >= startTs) continue;
      const expected = inferStatusFromScenario(e.event_type);
      if (expected) overrides.set(e.machine_id, expected);
    }

    // ── Refresh sticky cache for everything we found this tick ────────
    for (const [machineId, status] of overrides) {
      stickyOverridesRef.current.set(machineId, {
        status,
        expiresAt: now + STICKY_OVERRIDE_MS,
      });
    }

    // ── Apply unexpired sticky entries the live sources missed ────────
    for (const [machineId, sticky] of stickyOverridesRef.current) {
      if (overrides.has(machineId)) continue;
      if (sticky.expiresAt > now) {
        overrides.set(machineId, sticky.status);
      } else {
        stickyOverridesRef.current.delete(machineId);
      }
    }

    return overrides;
  }, [sseLive.commands, sseLive.events]);

  const machineStatesMap = useMemo(() => {
    const map = new Map<
      string,
      {
        status: string;
        throughput_per_min: number;
        speed_pct: number | null;
        alarm_count: number;
      }
    >();
    for (const m of sseLive.machines) {
      const override = machineStatusOverrides.get(m.machine_id);
      map.set(m.machine_id, {
        status: override ?? m.status,
        throughput_per_min: m.throughput_per_min,
        speed_pct: m.speed_pct,
        alarm_count: m.alarm_count,
      });
    }
    return map;
  }, [sseLive.machines, machineStatusOverrides]);

  // Tier-1 KPI from SSE payload only (computed upstream: producers → Delta → Synced Tables → Lakebase).
  // The producer recomputes A/P/OEE per tick and can emit avg_speed_pct=0 for a
  // single tick mid-cascade, collapsing OEE to 0 before the line settles. The
  // decision of whether such a zero is that blip or the line's real state lives
  // in lib/kpiHold.ts, with the reasoning and its tests.
  const kpiHoldRef = useRef<KpiHoldState>(emptyKpiHoldState);
  const kpiAtT = useMemo(() => {
    const k = sseLive.kpi ?? {};
    const running = toNumber(k.machines_running);
    const stopped = toNumber(k.machines_stopped);
    const degraded = toNumber(k.machines_degraded);
    const starved = toNumber(k.machines_starved);
    const total = running + stopped + degraded + starved;
    if (total === 0 && sseLive.machines.length === 0 && Object.keys(k).length === 0) return null;

    const quality = toNumber(k.quality_to_now);
    const totalThroughput = toNumber(k.total_throughput);

    const resolved = resolveKpiFrame(
      {
        oee_to_now: toNumber(k.oee_to_now),
        availability_to_now: toNumber(k.availability_to_now),
        performance_to_now: toNumber(k.performance_to_now),
      },
      totalThroughput,
      kpiHoldRef.current
    );
    kpiHoldRef.current = resolved.state;
    const { oee_to_now: oee, availability_to_now: availability, performance_to_now: performance } = resolved.value;

    return {
      oee_to_now: oee,
      availability_to_now: availability,
      performance_to_now: performance,
      quality_to_now: quality,
      // Producer resets cumulative_cases each UTC-aligned 8h shift, so the raw
      // value already reflects cases produced this shift (0 at the boundary,
      // growing with elapsed time). No synthetic carry-over offset — that
      // would inflate "% of plan" against the shift clock at shift start.
      cumulative_cases: toNumber(k.cumulative_cases),
      total_throughput: totalThroughput,
      machines_running: running,
      machines_stopped: stopped,
      machines_degraded: degraded,
      machines_starved: starved,
    };
  }, [sseLive.kpi, sseLive.machines]);

  // Events from SSE — map to expected shape
  const liveEvents = useMemo(() => {
    return sseLive.events.map((e) => ({
      event_id: e.event_id,
      machine_id: e.machine_id,
      event_type: e.event_type,
      severity: e.severity,
      timestamp_start: e.timestamp_start,
      timestamp_end: (e as { timestamp_end?: string | null }).timestamp_end ?? null,
      duration_minutes: 0,
      root_cause: e.event_type,
      description: e.description,
    }));
  }, [sseLive.events]);

  const eventsForSelectedMachine = useMemo(() => {
    if (!selectedMachine) return [];
    return liveEvents.filter((e) => e.machine_id === selectedMachine);
  }, [liveEvents, selectedMachine]);

  // Tab badge: only currently-active incidents.
  //
  //  - severity='info' rows are resolution markers from the producer
  //    (event_type='resolved'); we ignore them outright.
  //  - For the remaining incident-start rows we apply the same
  //    isEventActive() check the feed uses, so a row that's been closed
  //    out (either via its own timestamp_end or a later 'resolved' event
  //    for the same machine) doesn't keep the red number alight.
  const activeIncidentBadge = useMemo(() => {
    const resolvedTs = buildResolvedTsByMachine(liveEvents);
    const now = Date.now();
    let n = 0;
    for (const e of liveEvents) {
      if (e.event_type === 'resolved') continue;
      if ((e.severity ?? '').toLowerCase() === 'info') continue;
      if (isEventActive(e, now, resolvedTs)) n++;
    }
    return n;
  }, [liveEvents]);

  const machineShortLabels = useMemo(() => {
    const m = new Map<string, string>();
    MACHINE_LAYOUT.forEach((row) => m.set(row.machineId, row.label));
    return m;
  }, []);

  const neighborsDisplay = useMemo(() => {
    if (!selectedMachine) return { upstream: null as string | null, downstream: null as string | null };
    const n = flowNeighbors(selectedMachine);
    const fmt = (id: string | null) => (id ? `${machineShortLabels.get(id) ?? id} (${id})` : null);
    return { upstream: fmt(n.upstream), downstream: fmt(n.downstream) };
  }, [selectedMachine, machineShortLabels]);

  const materialsAtSelectedMachine = useMemo(() => {
    if (!selectedMachine) return [];
    return sseLive.inventory
      .filter((inv) => (inv.feeds_machine_id || '') === selectedMachine)
      .map((inv) => ({
        material_id: inv.material_id,
        on_hand_qty: inv.on_hand_qty,
        status: inv.status,
        consumption_rate_per_hour: inv.consumption_rate_per_hour,
      }));
  }, [sseLive.inventory, selectedMachine]);

  const lineKpiForDetail = useMemo(() => {
    if (!kpiAtT) return null;
    return {
      oee_to_now: kpiAtT.oee_to_now,
      availability_to_now: kpiAtT.availability_to_now,
      performance_to_now: kpiAtT.performance_to_now,
      quality_to_now: kpiAtT.quality_to_now,
      total_throughput: kpiAtT.total_throughput,
    };
  }, [kpiAtT]);

  const liveStatusText = useMemo(() => {
    // Backend signals "waiting" when the playback cursor has caught up to
    // head and there's no new row to emit yet (producer paused, fresh
    // restart, or buffer underrun). We keep showing the LAST KNOWN frame
    // but flip the indicator so users know nothing new just landed.
    if (sseLive.waiting) return 'Live · waiting for new value';
    if (!sseLive.streamLastUpdateTs) return 'Live · Lakebase';
    const t = new Date(sseLive.streamLastUpdateTs).toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
    const lag = sseLive.cursorLagSeconds;
    const lagStr = lag != null && lag >= 1 ? ` · ${Math.round(lag)}s lag` : '';
    return `Live · updated ${t}${lagStr}`;
  }, [sseLive.streamLastUpdateTs, sseLive.waiting, sseLive.cursorLagSeconds]);

  // Where we are in the current shift (UTC-aligned 8h window — same boundary
  // the producer resets cumulative_cases on). Recomputed each render; App
  // re-renders every SSE tick so the clock stays fresh.
  const shift = shiftProgress();

  return (
    <div className="flex h-screen flex-col bg-background">
      {/* Header */}
      <header className="shrink-0 border-b border-border bg-card px-4 pt-2 pb-0">
        <div className="mx-auto max-w-[1600px] space-y-1.5">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-lg font-semibold text-foreground">ProdLine CoPilot</h1>
              <p className="text-[11px] text-muted-foreground -mt-0.5">
                Real-time line monitoring and AI-powered optimization
              </p>
            </div>
            <div className="flex items-center gap-2">
              {sseLive.connected ? (
                <div
                  className={
                    sseLive.waiting
                      ? 'flex items-center gap-1.5 text-[10px] text-amber-600 dark:text-amber-400'
                      : 'flex items-center gap-1.5 text-[10px] text-green-600 dark:text-green-400'
                  }
                >
                  <span
                    className={
                      sseLive.waiting
                        ? 'h-1.5 w-1.5 rounded-full bg-amber-500 animate-pulse'
                        : 'h-1.5 w-1.5 rounded-full bg-green-500 animate-pulse'
                    }
                  />
                  {liveStatusText}
                </div>
              ) : simRunning ? (
                <div className="flex items-center gap-1.5 text-[10px] text-amber-600 dark:text-amber-400">
                  <span className="h-1.5 w-1.5 rounded-full bg-amber-500 animate-pulse" />
                  {sseLive.hasConnected ? 'Reconnecting…' : 'Connecting...'}
                </div>
              ) : (
                <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
                  <span className="h-1.5 w-1.5 rounded-full bg-gray-400" />
                  Paused
                </div>
              )}
              <div className="flex items-center gap-1 border-l border-border pl-2">
                {simRunning ? (
                  <button
                    type="button"
                    onClick={() => {
                      void handleSimStop();
                    }}
                    className="rounded px-2 py-1 text-[10px] font-medium bg-amber-100 text-amber-700 hover:bg-amber-200 dark:bg-amber-900/30 dark:text-amber-400 transition-colors"
                  >
                    ⏸ Pause
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={() => {
                      void handleSimStart();
                    }}
                    className="rounded px-2 py-1 text-[10px] font-medium bg-green-100 text-green-700 hover:bg-green-200 dark:bg-green-900/30 dark:text-green-400 transition-colors"
                  >
                    ▶ Start
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => {
                    void handleSimRestart();
                  }}
                  className="rounded px-2 py-1 text-[10px] font-medium bg-blue-100 text-blue-700 hover:bg-blue-200 dark:bg-blue-900/30 dark:text-blue-400 transition-colors"
                  title="Full reset — clears all incidents, resets OEE, and starts fresh"
                >
                  ↺ Clean Restart
                </button>
                <button
                  type="button"
                  onClick={() => setShowTriggerSim((v) => !v)}
                  className={cn(
                    'rounded px-2 py-1 text-[10px] font-medium transition-colors',
                    showTriggerSim
                      ? 'bg-amber-500 text-white hover:bg-amber-600 dark:bg-amber-600 dark:hover:bg-amber-700'
                      : 'bg-muted text-muted-foreground hover:bg-accent'
                  )}
                  title={
                    showTriggerSim
                      ? 'Hide the Trigger Incident panel and let the dashboard read as a real plant view.'
                      : 'Slide in the Trigger Incident panel for demos / testing.'
                  }
                >
                  🧪 {showTriggerSim ? 'Hide Trigger' : 'Trigger Sim'}
                </button>
                <button
                  type="button"
                  onClick={() => navigate(path === '/admin' ? '/' : '/admin')}
                  className="rounded px-2 py-1 text-[10px] font-medium bg-slate-100 text-slate-700 hover:bg-slate-200 dark:bg-slate-800/40 dark:text-slate-300 transition-colors"
                  title="Open admin controls"
                >
                  ⚙ {path === '/admin' ? 'Exit Admin' : 'Admin'}
                </button>
              </div>
            </div>
          </div>

          {sseLive.error && (
            <div className="flex items-center justify-between gap-3 rounded-md border border-destructive/50 bg-destructive/10 p-2 text-xs text-destructive">
              <span>
                {sseLive.stalled
                  ? 'Live connection lost. Still retrying — reconnect now or refresh the page if it persists.'
                  : 'Live connection dropped — reconnecting automatically…'}
              </span>
              {sseLive.stalled && (
                <button
                  type="button"
                  onClick={sseLive.retryNow}
                  className="shrink-0 rounded border border-destructive/50 px-2 py-1 font-medium hover:bg-destructive/20"
                >
                  Reconnect now
                </button>
              )}
            </div>
          )}

          {pendingSinceMs != null && (
            <PendingPipelineBanner sinceMs={pendingSinceMs} reason={pendingReason} restartDetails={restartDetails} />
          )}

          {/* Tab bar */}
          {path !== '/admin' && (
            <nav className="flex gap-0 -mb-px" role="tablist">
              {/* First, so a presenter does not hunt for it. Not the default
                  tab, though: someone opening the app cold should see a plant,
                  not instructions about a plant. */}
              <TabButton active={activeTab === 'guide'} onClick={() => selectTab('guide')} label="Demo Guide" />
              <TabButton active={activeTab === 'tower'} onClick={() => selectTab('tower')} label="Line Status" />
              <TabButton
                active={activeTab === 'alerts'}
                onClick={() => selectTab('alerts')}
                label="Events & AI"
                /* Red number = currently-active incidents only. Resolved
                   rows still appear under the feed's History filter but
                   no longer light up the tab. See activeIncidentBadge
                   for the exact reconciliation (mirrors EventFeed). */
                badge={activeIncidentBadge}
              />
              <TabButton active={activeTab === 'ops'} onClick={() => selectTab('ops')} label="Cost & Obs" />
              <TabButton active={activeTab === 'quality'} onClick={() => selectTab('quality')} label="Data Integrity" />
              <TabButton active={activeTab === 'multiai'} onClick={() => selectTab('multiai')} label="Multi-AI" />
              <TabButton active={activeTab === 'about'} onClick={() => selectTab('about')} label="About" />
            </nav>
          )}
        </div>
      </header>

      {/* Main content */}
      <main className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-[1600px] px-4 py-4">
          {path === '/admin' ? (
            <AdminJobControls onBack={() => navigate('/')} />
          ) : (
            activeTab === 'tower' && (
              <div className="space-y-4">
                <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
                  {/* KPI + Schematic — main area. Expands to full width when the
                    right sidebar would be empty (sim controls hidden AND no
                    active incidents). */}
                  <div
                    className={cn(
                      'space-y-4',
                      showTriggerSim || sseLive.commands.length > 0 ? 'lg:col-span-3' : 'lg:col-span-4'
                    )}
                  >
                    <ErrorBoundary>
                      <KPIStrip
                        kpi={kpiAtT}
                        plannedCases={SHIFT_PLANNED_CASES}
                        shiftElapsedMin={shift.elapsedMin}
                        shiftRemainingMin={shift.remainingMin}
                        loading={!sseLive.hasConnected}
                        events={liveEvents}
                        inventoryAlerts={0}
                        qualityFails={0}
                        currentTimestamp={Date.now()}
                        onNavQuality={() => {}}
                      />
                    </ErrorBoundary>

                    <ErrorBoundary>
                      <ProcessFlowSchematic
                        machineStates={machineStatesMap}
                        inventory={sseLive.inventory}
                        kpi={kpiAtT}
                        selectedMachine={selectedMachine}
                        onMachineClick={handleMachineClick}
                        loading={!sseLive.hasConnected}
                        waiting={sseLive.waiting}
                      />
                    </ErrorBoundary>

                    <ErrorBoundary>
                      <InventoryPanel
                        items={sseLive.inventory}
                        loading={!sseLive.hasConnected}
                        elapsedHours={shift.elapsedMin / 60}
                        remainingHours={shift.remainingMin / 60}
                      />
                    </ErrorBoundary>
                  </div>

                  {/* Incident panel — right sidebar. Hidden entirely when the
                    sim trigger card is hidden AND there are no active
                    incidents to resolve. */}
                  {(showTriggerSim || sseLive.commands.length > 0) && (
                    <div className="lg:col-span-1">
                      <ErrorBoundary>
                        <IncidentPanel
                          shiftId="live"
                          activeCommands={sseLive.commands}
                          onTrigger={handleTriggerIncident}
                          onResolve={incidentApi.resolveIncident}
                          loading={incidentApi.loading}
                          showTrigger={showTriggerSim}
                        />
                      </ErrorBoundary>
                    </div>
                  )}
                </div>
              </div>
            )
          )}

          {/* Sits outside the tab body so the modal is not nested inside the
              scrolling column that opened it. */}
          {path !== '/admin' && activeTab === 'tower' && selectedMachine && selectedMachineDetail && (
            <div
              className="fixed inset-0 z-[100] flex items-end justify-center sm:items-center sm:justify-end p-3 sm:p-6"
              role="dialog"
              aria-modal="true"
              aria-labelledby="machine-detail-title"
            >
              <button
                type="button"
                className="absolute inset-0 bg-black/45 backdrop-blur-[1px]"
                aria-label="Close machine details"
                onClick={() => setSelectedMachine(null)}
              />
              <div className="relative z-10 w-full max-w-md max-h-[min(88vh,640px)] overflow-y-auto rounded-lg shadow-2xl">
                <MachineDetailCard
                  machineId={selectedMachineDetail.machineId}
                  meta={selectedMachineDetail.meta}
                  neighbors={neighborsDisplay}
                  lineKpi={lineKpiForDetail}
                  materialsHere={materialsAtSelectedMachine}
                  state={selectedMachineDetail.state}
                  readingsEstimated={selectedMachineDetail.readingsEstimated}
                  events={eventsForSelectedMachine}
                  onClose={() => setSelectedMachine(null)}
                  onAskAgent={handleAskAgent}
                  loading={!sseLive.hasConnected && selectedMachineDetail.state == null}
                />
              </div>
            </div>
          )}

          <div className={path !== '/admin' && activeTab === 'alerts' ? '' : 'hidden'}>
            <div className="flex flex-col gap-3" style={{ height: 'calc(100vh - 140px)' }}>
              <div className="flex-1 min-h-0 grid grid-cols-1 lg:grid-cols-3 gap-3">
                <div className="min-h-0 overflow-hidden lg:col-span-1">
                  <ErrorBoundary>
                    <EventFeed
                      events={liveEvents}
                      currentTimestamp={Date.now()}
                      loading={!sseLive.hasConnected}
                      onAskAbout={handleAskAgent}
                      onRecoverMachine={handleRecoverMachine}
                    />
                  </ErrorBoundary>
                </div>
                <div className="min-h-0 overflow-hidden lg:col-span-2">
                  <ErrorBoundary>
                    <ChatPanel
                      shiftId="shift_1"
                      timestamp="2025-01-01T12:00:00"
                      machineId={selectedMachine}
                      externalQuery={chatQuery}
                    />
                  </ErrorBoundary>
                </div>
              </div>
            </div>
          </div>

          <div className={path !== '/admin' && activeTab === 'ops' ? '' : 'hidden'}>
            <CostObservabilityTab
              connected={sseLive.connected}
              streamLastUpdateTs={sseLive.streamLastUpdateTs}
              sourceLastEventTs={sseLive.sourceLastEventTs}
              ingestionLatencyMs={sseLive.ingestionLatencyMs}
              machineCount={sseLive.machines.length}
              eventCount={liveEvents.length}
            />
          </div>

          {path !== '/admin' && activeTab === 'quality' && (
            <ErrorBoundary>
              <Suspense
                fallback={
                  <div className="p-6 text-sm text-muted-foreground">Loading Data Integrity…</div>
                }
              >
                <DataQualityTab />
              </Suspense>
            </ErrorBoundary>
          )}

          {/* Boundaries for the same reason every other tab has one: both render
              server JSON, and an unguarded throw here is caught only by the root
              boundary, which replaces the whole app — live line status included —
              rather than the one tab that failed. */}
          {path !== '/admin' && activeTab === 'multiai' && (
            <ErrorBoundary>
              <MultiAITab />
            </ErrorBoundary>
          )}

          {path !== '/admin' && activeTab === 'about' && (
            <ErrorBoundary>
              <AboutTab />
            </ErrorBoundary>
          )}

          {/* Static content, but boundaried like the rest: the one tab that
              must not break is the one someone is presenting from. */}
          {path !== '/admin' && activeTab === 'guide' && (
            <ErrorBoundary>
              <DemoGuideTab onOpenTab={selectTab} />
            </ErrorBoundary>
          )}
        </div>
      </main>
    </div>
  );
}

// Shape of /sim/restart's JSON response. Mirrors agent_server.start_server
// helpers (_abort_pending_uc_commands, _truncate_ai_serving_demo_state,
// _restart_producer_job_best_effort) so the banner can show specifically
// what step finished, not just "waiting for fresh data".
export type RestartDetails = {
  status?: string;
  mode?: 'producer';
  uc_commands_clear?: {
    resolve_requested?: number | string;
    aborted?: number | string;
    total?: number;
    error?: string;
  };
  lakebase_clear?: {
    shift_id?: string;
    rows_deleted?: number;
    tables_ok?: number;
    tables_total?: number;
    warning?: string;
    error?: string;
  };
  producer_restart?: {
    job_id?: number;
    cancelled_runs?: number[];
    new_run_id?: number;
    start_fresh?: boolean;
    skipped?: string;
    warning?: string;
    error?: string;
  };
};

function summarizeRestart(d: RestartDetails | null | undefined): string[] {
  if (!d) return [];
  const lines: string[] = [];

  // Step 1 — UC scenario_commands wind-down.
  const uc = d.uc_commands_clear;
  if (uc) {
    if (uc.error) {
      lines.push(`✗ UC commands wind-down failed: ${uc.error}`);
    } else {
      const resolved = Number(uc.resolve_requested ?? 0);
      const aborted = Number(uc.aborted ?? 0);
      const total = uc.total ?? resolved + aborted;
      lines.push(
        total === 0
          ? '✓ No active scenario_commands to clear'
          : `✓ Wound down ${total} scenario_commands (${resolved} resolve_requested, ${aborted} aborted)`
      );
    }
  }

  // Step 2 — Lakebase ai_serving.*_history wipe for this shift.
  const lb = d.lakebase_clear;
  if (lb) {
    if (lb.error) {
      lines.push(`✗ Lakebase wipe failed: ${lb.error}`);
    } else if (lb.warning) {
      lines.push(`⚠ Lakebase wipe skipped: ${lb.warning}`);
    } else {
      const tables = lb.tables_ok ?? 0;
      const totalTables = lb.tables_total ?? tables;
      const rows = lb.rows_deleted ?? 0;
      lines.push(
        `✓ Wiped Lakebase ai_serving.*_history for shift ${lb.shift_id ?? '?'} (${rows} rows across ${tables}/${totalTables} tables)`
      );
    }
  }

  // Step 3 — producer Databricks Job cancel + run_now.
  const pr = d.producer_restart;
  if (pr) {
    if (pr.error) {
      lines.push(`✗ Producer job restart failed: ${pr.error}`);
    } else if (pr.skipped) {
      lines.push(`⚠ Producer job restart skipped: ${pr.skipped}`);
    } else {
      const cancelled = pr.cancelled_runs?.length ?? 0;
      const newRun = pr.new_run_id ? `run_id=${pr.new_run_id}` : 'queued';
      const flag = pr.start_fresh ? ' --start-fresh' : '';
      const warn = pr.warning ? ` (warning: ${pr.warning})` : '';
      lines.push(
        `✓ Producer job: cancelled ${cancelled} active run${cancelled === 1 ? '' : 's'} → run_now${flag} (${newRun})${warn}`
      );
    }
  }

  return lines;
}

function PendingPipelineBanner({
  sinceMs,
  reason,
  restartDetails,
}: {
  sinceMs: number;
  reason: 'restart' | 'incident' | null;
  restartDetails?: RestartDetails | null;
}) {
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  const elapsed = Math.max(0, Math.floor((nowMs - sinceMs) / 1000));
  const label =
    reason === 'restart'
      ? 'Pipeline restart submitted'
      : reason === 'incident'
        ? 'Incident submitted to producer'
        : 'Producer action submitted';
  // why: incidents take the dual-write fast path (~5-10s end-to-end on a warm
  // producer); a Clean Restart cancels + re-runs the producer Job, so the
  // first fresh row only lands after the cluster spins back up — typically
  // 20-30s. Using the same 8s expected for both made the bar peg at 100%
  // and look stuck whenever the user hit Restart.
  const expected = reason === 'restart' ? 30 : 8;
  const pct = Math.min(100, Math.round((elapsed / expected) * 100));
  const detail =
    reason === 'restart'
      ? '· clearing UC commands, wiping Lakebase ai_serving.*_history, and restarting the producer job'
      : '· waiting for fresh data through Producer → Lakebase ai_serving.*_history';
  const steps = reason === 'restart' ? summarizeRestart(restartDetails) : [];
  return (
    <div
      role="status"
      aria-live="polite"
      className="rounded-md border border-blue-300/70 bg-blue-50 dark:border-blue-700/40 dark:bg-blue-900/20 p-2 text-[11px] text-blue-800 dark:text-blue-200"
    >
      <div className="flex items-center gap-2">
        <span className="inline-block h-2 w-2 rounded-full bg-blue-500 animate-pulse" />
        <span className="font-medium">{label}</span>
        <span>{detail}</span>
        <span className="ml-auto tabular-nums opacity-80">
          {elapsed}s / ~{expected}s
        </span>
      </div>
      <div className="mt-1 h-1 w-full overflow-hidden rounded bg-blue-200/60 dark:bg-blue-800/40">
        <div className="h-full bg-blue-500 transition-all duration-300 ease-out" style={{ width: `${pct}%` }} />
      </div>
      {steps.length > 0 && (
        <ul className="mt-2 space-y-0.5 font-mono text-[10.5px] leading-snug opacity-90">
          {steps.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  label,
  badge = 0,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  badge?: number;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={cn(
        'relative px-4 py-2 text-sm font-medium transition-colors border-b-2',
        active
          ? 'border-primary text-primary'
          : 'border-transparent text-muted-foreground hover:text-foreground hover:border-muted-foreground/30'
      )}
    >
      {label}
      {badge > 0 && (
        <span className="absolute -top-1 -right-1 flex h-5 min-w-5 items-center justify-center rounded-full bg-destructive px-1 text-[11px] font-bold text-destructive-foreground">
          {badge > 99 ? '99+' : badge}
        </span>
      )}
    </button>
  );
}

export default App;
