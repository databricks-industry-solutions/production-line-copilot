/**
 * Renders the top-line KPI strip and hero operational status summary.
 * Used in `App` tower view as the primary shift-level health indicator.
 * Reconciles resolved markers from append-only events so status badges match EventFeed semantics.
 */
import { memo, useMemo, useState, useEffect } from 'react';
import { Card, CardContent, Skeleton } from '@databricks/appkit-ui/react';
import { formatPct, toNumber, isoToEpoch, cn } from '../lib/utils';
import { formatDurationMin } from '../lib/shiftWindow';
import DataSourceBadge from './DataSourceBadge';

export interface KPIStripProps {
  kpi: {
    oee_to_now: number;
    availability_to_now: number;
    performance_to_now: number;
    quality_to_now: number;
    cumulative_cases: number;
    total_throughput: number;
    machines_running: number;
    machines_stopped: number;
    machines_degraded: number;
    machines_starved: number;
  } | null;
  plannedCases?: number;
  /** Minutes elapsed / remaining in the current shift window (UTC-aligned 8h).
   *  When provided, the strip renders a shift-time progress indicator. */
  shiftElapsedMin?: number;
  shiftRemainingMin?: number;
  loading?: boolean;
  events?: Array<{
    severity: string;
    timestamp_start: string;
    event_type: string;
    machine_id: string;
    /** Producer is Zerobus-append-only — when an incident is resolved a
     *  separate ``event_type='resolved'`` row is appended for the same
     *  machine instead of updating ``timestamp_end`` on the original.
     *  We reconcile both signals here. */
    timestamp_end?: string | null;
  }>;
  inventoryAlerts?: number;
  qualityFails?: number;
  currentTimestamp?: number;
  onNavQuality?: () => void;
}

type StatusLevel = 'good' | 'warning' | 'critical';

function getOeeColor(val: number): string {
  if (val >= 70) return 'text-green-600';
  if (val >= 50) return 'text-yellow-600';
  return 'text-red-600';
}

function getOeeIcon(val: number): string {
  if (val >= 70) return '✓';
  if (val >= 50) return '△';
  return '!';
}

const STATUS_CONFIG: Record<StatusLevel, { label: string; icon: string; bg: string; badge: string }> = {
  good: { label: 'Normal', icon: '✓', bg: 'border-green-500/30 bg-green-500/5', badge: 'bg-green-500 text-white' },
  warning: { label: 'Watch', icon: '△', bg: 'border-yellow-500/30 bg-yellow-500/5', badge: 'bg-yellow-500 text-black' },
  critical: { label: 'Critical', icon: '!', bg: 'border-red-500/30 bg-red-500/5', badge: 'bg-red-500 text-white' },
};

// why: demo target reflects the line's "good shift" benchmark used across coaching prompts.
const OEE_TARGET = 75;
const KPI_SOURCE_DETAILS: Record<string, string> = {
  MES: 'Shift context and machine-state aggregation inputs.',
  SCADA: 'Live throughput/speed/alarm telemetry feeding A/P/Q/OEE.',
  'SAP PP': 'Production plan baseline (target vs actual cases).',
};

function TrendArrow({ current, previous }: { current: number; previous: number | null }) {
  if (previous === null || !Number.isFinite(previous)) return null;
  const delta = current - previous;
  if (Math.abs(delta) < 0.3) return <span className="text-[11px] text-muted-foreground ml-1">→</span>;
  const up = delta > 0;
  return (
    <span className={cn('text-[11px] font-medium ml-1', up ? 'text-green-600' : 'text-red-600')}>
      {up ? '↑' : '↓'}
      {Math.abs(delta).toFixed(1)}
    </span>
  );
}

function KPIStrip({
  kpi,
  plannedCases = 0,
  shiftElapsedMin,
  shiftRemainingMin,
  loading = false,
  events = [],
  inventoryAlerts = 0,
  qualityFails = 0,
  currentTimestamp = 0,
  onNavQuality,
}: KPIStripProps) {
  const [prevKpi, setPrevKpi] = useState<{ oee: number; avail: number; perf: number; quality: number } | null>(null);

  const oee = kpi ? toNumber(kpi.oee_to_now) : 0;
  const avail = kpi ? toNumber(kpi.availability_to_now) : 0;
  const perf = kpi ? toNumber(kpi.performance_to_now) : 0;
  const qual = kpi ? toNumber(kpi.quality_to_now) : 0;

  useEffect(() => {
    if (!kpi) return;
    const timer = setTimeout(() => {
      setPrevKpi({ oee, avail, perf, quality: qual });
    }, 5000);
    return () => clearTimeout(timer);
  }, [kpi, oee, avail, perf, qual]);

  const summary = useMemo(() => {
    if (!kpi) return null;
    const cases = toNumber(kpi.cumulative_cases);
    const stopped = toNumber(kpi.machines_stopped);

    // Build {machine_id -> latest 'resolved' ts} so we can close out
    // incidents whose original Bronze row never got an UPDATE on
    // ``timestamp_end`` (Zerobus is append-only — resolve emits a NEW
    // ``event_type='resolved'`` row for the same machine). Same reconciliation
    // EventFeed uses. Without this, the hero strip kept showing
    // ``⚠ FILLER: machine_breakdown`` indefinitely after resolve.
    const resolvedTsByMachine = new Map<string, number>();
    for (const e of events) {
      if (e.event_type === 'resolved' && e.machine_id) {
        const ts = isoToEpoch(String(e.timestamp_start));
        const prevResolvedTs = resolvedTsByMachine.get(e.machine_id) ?? 0;
        if (ts > prevResolvedTs) resolvedTsByMachine.set(e.machine_id, ts);
      }
    }

    const isActive = (e: {
      machine_id: string;
      timestamp_start: string;
      timestamp_end?: string | null;
      event_type: string;
    }): boolean => {
      // Resolved-row itself is not an "open issue".
      if (e.event_type === 'resolved') return false;
      if (e.timestamp_end) {
        return isoToEpoch(String(e.timestamp_end)) > currentTimestamp;
      }
      const resolvedTs = resolvedTsByMachine.get(e.machine_id) ?? 0;
      if (resolvedTs > 0 && resolvedTs >= isoToEpoch(String(e.timestamp_start))) {
        return false;
      }
      return true;
    };

    const activeEvents = events.filter((e) => isoToEpoch(String(e.timestamp_start)) <= currentTimestamp && isActive(e));
    const majorEvents = activeEvents.filter((e) => e.severity === 'major' || e.severity === 'critical');

    let status: StatusLevel = 'good';
    if (stopped >= 2 || oee < 50 || majorEvents.length >= 2) status = 'critical';
    else if (stopped >= 1 || oee < 70 || qualityFails > 0 || inventoryAlerts > 0) status = 'warning';

    const topIssue =
      majorEvents.length > 0
        ? `${majorEvents[0].machine_id}: ${majorEvents[0].event_type}`
        : stopped > 0
          ? `${stopped} machine(s) stopped`
          : null;

    return { status, topIssue, majorCount: majorEvents.length, cases };
  }, [kpi, events, inventoryAlerts, qualityFails, currentTimestamp, oee]);

  if (loading) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-8 w-full rounded" />
        <div className="grid grid-cols-4 md:grid-cols-7 gap-2">
          {Array.from({ length: 7 }, (_, i) => `kpi-skeleton-${i}`).map((key) => (
            <Card key={key} className="p-3">
              <CardContent className="p-0">
                <Skeleton className="h-4 w-16 mb-2" />
                <Skeleton className="h-8 w-20" />
              </CardContent>
            </Card>
          ))}
        </div>
      </div>
    );
  }

  if (!kpi) return null;

  const casesProgress = plannedCases > 0 ? toNumber(kpi.cumulative_cases) / plannedCases : 0;
  // cumulative_cases is a per-shift tally (producer resets every 8h window). If
  // it dwarfs a single-shift plan the producer is on an older build that never
  // reset — showing "% of plan" would be nonsense (e.g. 6233%), so hide the
  // ratio and just show the count in that case.
  const casesRatioValid = plannedCases > 0 && casesProgress <= 1.5;
  const throughputRate = toNumber(kpi.total_throughput);
  const hasShiftClock = shiftElapsedMin != null && shiftRemainingMin != null;
  const shiftTotalMin = hasShiftClock ? shiftElapsedMin + shiftRemainingMin : 0;
  const shiftFraction = hasShiftClock && shiftTotalMin > 0 ? shiftElapsedMin / shiftTotalMin : 0;
  const statusCfg = summary ? STATUS_CONFIG[summary.status] : STATUS_CONFIG.good;

  return (
    <div className="space-y-2">
      {/* Hero status bar (merged from InstantSummary) */}
      {summary && (
        <div
          className={cn('flex items-center gap-3 flex-wrap rounded-lg border px-3 py-1.5 text-[12px]', statusCfg.bg)}
        >
          <span className={cn('font-bold text-[11px] px-2 py-0.5 rounded', statusCfg.badge)}>{statusCfg.label}</span>
          <span>
            OEE <strong className={getOeeColor(oee)}>{formatPct(oee)}</strong>
          </span>
          <span>
            Cases <strong>{summary.cases.toLocaleString()}</strong>
            {casesRatioValid ? (
              <>/{plannedCases.toLocaleString()} ({formatPct(casesProgress * 100, 0)})</>
            ) : (
              plannedCases > 0 && <> (plan {plannedCases.toLocaleString()}/shift)</>
            )}
          </span>
          <span>
            Throughput <strong>{throughputRate.toFixed(0)}</strong>/min
          </span>
          {hasShiftClock && (
            <span className="flex items-center gap-1.5">
              Shift{' '}
              <strong>{formatDurationMin(shiftElapsedMin)}</strong>
              <span className="text-muted-foreground">/ {formatDurationMin(shiftTotalMin)}</span>
              <span
                className="inline-block h-1.5 w-14 rounded-full bg-muted overflow-hidden align-middle"
                title={`${formatDurationMin(shiftRemainingMin)} left in shift`}
              >
                <span
                  className="block h-full rounded-full bg-primary"
                  style={{ width: `${Math.min(shiftFraction * 100, 100)}%` }}
                />
              </span>
              <span className="text-muted-foreground">{formatDurationMin(shiftRemainingMin)} left</span>
            </span>
          )}
          {summary.topIssue && <span className="font-medium text-red-600 dark:text-red-400">⚠ {summary.topIssue}</span>}
          {qualityFails > 0 && (
            <button onClick={onNavQuality} className="text-purple-600 hover:underline font-medium">
              {qualityFails} QC fails
            </button>
          )}
          {inventoryAlerts > 0 && (
            <button onClick={onNavQuality} className="text-yellow-600 hover:underline font-medium">
              {inventoryAlerts} material alerts
            </button>
          )}
        </div>
      )}

      <DataSourceBadge sources={['MES', 'SCADA', 'SAP PP']} details={KPI_SOURCE_DETAILS} className="justify-end" />

      {/* KPI cards */}
      <div className="grid grid-cols-4 md:grid-cols-7 gap-2">
        <Card className="p-2">
          <CardContent className="p-0">
            <p className="text-[11px] text-muted-foreground mb-0.5">OEE</p>
            <div className="flex items-baseline">
              <p className={cn('text-lg font-bold', getOeeColor(oee))}>
                <span className="mr-0.5">{getOeeIcon(oee)}</span>
                {formatPct(oee)}
              </p>
              <TrendArrow current={oee} previous={prevKpi?.oee ?? null} />
            </div>
            <p className="text-[11px] text-muted-foreground mt-0.5">
              Target {OEE_TARGET}%{' '}
              <span className={oee >= OEE_TARGET ? 'text-green-600' : 'text-red-600'}>
                {oee >= OEE_TARGET ? '+' : ''}
                {(oee - OEE_TARGET).toFixed(0)}%
              </span>
            </p>
          </CardContent>
        </Card>

        <Card className="p-2">
          <CardContent className="p-0">
            <p className="text-[11px] text-muted-foreground mb-0.5">Availability</p>
            <div className="flex items-baseline">
              <p className="text-lg font-bold">{formatPct(avail)}</p>
              <TrendArrow current={avail} previous={prevKpi?.avail ?? null} />
            </div>
          </CardContent>
        </Card>

        <Card className="p-2">
          <CardContent className="p-0">
            <p className="text-[11px] text-muted-foreground mb-0.5">Performance</p>
            <div className="flex items-baseline">
              <p className="text-lg font-bold">{formatPct(perf)}</p>
              <TrendArrow current={perf} previous={prevKpi?.perf ?? null} />
            </div>
          </CardContent>
        </Card>

        <Card className="p-2">
          <CardContent className="p-0">
            <p className="text-[11px] text-muted-foreground mb-0.5">Quality</p>
            <div className="flex items-baseline">
              <p className="text-lg font-bold">{formatPct(qual)}</p>
              <TrendArrow current={qual} previous={prevKpi?.quality ?? null} />
            </div>
          </CardContent>
        </Card>

        <Card className="p-2">
          <CardContent className="p-0">
            <p className="text-[11px] text-muted-foreground mb-0.5">Cases</p>
            <p className="text-lg font-bold">
              {toNumber(kpi.cumulative_cases).toLocaleString()}
              {plannedCases > 0 && (
                <span className="text-xs font-normal text-muted-foreground ml-1">
                  {casesRatioValid
                    ? `/ ${plannedCases.toLocaleString()}`
                    : `(plan ${plannedCases.toLocaleString()}/shift)`}
                </span>
              )}
            </p>
            {casesRatioValid && (
              <div className="mt-1 h-1.5 w-full rounded-full bg-muted overflow-hidden">
                <div
                  className="h-full rounded-full bg-primary transition-all"
                  style={{ width: `${Math.min(casesProgress * 100, 100)}%` }}
                />
              </div>
            )}
            {casesRatioValid && (
              <p className="text-[11px] text-muted-foreground mt-0.5">{formatPct(casesProgress * 100, 0)} of plan</p>
            )}
          </CardContent>
        </Card>

        <Card className="p-2">
          <CardContent className="p-0">
            <p className="text-[11px] text-muted-foreground mb-0.5">Throughput</p>
            <p className="text-lg font-bold">
              {throughputRate.toFixed(0)}
              <span className="text-xs font-normal text-muted-foreground ml-0.5">/min</span>
            </p>
          </CardContent>
        </Card>

        <Card className="p-2">
          <CardContent className="p-0">
            <p className="text-[11px] text-muted-foreground mb-0.5">Line Status</p>
            <div className="flex items-center gap-1 flex-wrap text-[11px]">
              <span className="flex items-center gap-0.5">
                <span className="h-2 w-2 rounded-full bg-green-500 shrink-0" />
                <span className="font-medium">{toNumber(kpi.machines_running)}</span>
              </span>
              {toNumber(kpi.machines_stopped) > 0 && (
                <span className="flex items-center gap-0.5">
                  <span className="h-2 w-2 rounded-full bg-red-500 shrink-0" />
                  <span className="font-medium text-red-600">{toNumber(kpi.machines_stopped)}</span>
                </span>
              )}
              {toNumber(kpi.machines_degraded) > 0 && (
                <span className="flex items-center gap-0.5">
                  <span className="h-2 w-2 rounded-full bg-yellow-500 shrink-0" />
                  <span className="font-medium">{toNumber(kpi.machines_degraded)}</span>
                </span>
              )}
              {toNumber(kpi.machines_starved) > 0 && (
                <span className="flex items-center gap-0.5">
                  <span className="h-2 w-2 rounded-full bg-gray-400 shrink-0" />
                  <span className="font-medium">{toNumber(kpi.machines_starved)}</span>
                </span>
              )}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

export default memo(KPIStrip, (prev, next) => {
  if (prev.kpi !== next.kpi) return false;
  if (prev.plannedCases !== next.plannedCases) return false;
  // Re-render when the shift clock advances a whole minute.
  if (Math.floor(prev.shiftElapsedMin ?? -1) !== Math.floor(next.shiftElapsedMin ?? -1)) return false;
  if (prev.loading !== next.loading) return false;
  if (prev.events !== next.events) return false;
  if (prev.inventoryAlerts !== next.inventoryAlerts) return false;
  if (prev.qualityFails !== next.qualityFails) return false;
  if (prev.onNavQuality !== next.onNavQuality) return false;
  // Only re-render for currentTimestamp when it crosses a minute boundary
  const prevMin = Math.floor((prev.currentTimestamp ?? 0) / 60000);
  const nextMin = Math.floor((next.currentTimestamp ?? 0) / 60000);
  return prevMin === nextMin;
});
