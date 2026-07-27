/**
 * Shows per-machine diagnostics, contextual KPIs, and incident history in the right-side modal.
 * Used by `App` when a node is selected from `LineSchematic`.
 * Combines live values with metadata/fallback estimates so operators still get coherent context during sparse payloads.
 */
import { Card, CardContent, CardHeader, CardTitle, Skeleton } from '@databricks/appkit-ui/react';
import { STATUS_COLORS, type MachineStatus } from '../lib/constants';
import { toNumber, formatTimestamp, cn } from '../lib/utils';
import type { MachineMeta } from '../lib/machineMeta';
import StatusBadge from './StatusBadge';
import { severityToVariant } from '../lib/statusBadgeUtils';

export interface LineKpiSummary {
  oee_to_now: number;
  availability_to_now: number;
  performance_to_now: number;
  quality_to_now: number;
  total_throughput: number;
}

export interface MachineDetailCardProps {
  machineId: string;
  /** Registry row — display name, role, nominal rate, sensor labels */
  meta: MachineMeta | null;
  /** Upstream / downstream — short labels with id in parentheses */
  neighbors: { upstream: string | null; downstream: string | null };
  /** Shift-level KPIs (same as KPI strip) for context while viewing one asset */
  lineKpi?: LineKpiSummary | null;
  /** Materials whose BOM ties them to this machine (sim path includes feeds_machine_id) */
  materialsHere?: Array<{
    material_id: string;
    on_hand_qty: number;
    status: string;
    consumption_rate_per_hour?: number;
  }>;
  state: {
    status: string;
    throughput_per_min: number;
    speed_pct: number | null;
    primary_sensor_1: number | null;
    primary_sensor_2: number | null;
    material_level_pct: number | null;
    alarm_count: number;
    ts: string;
  } | null;
  /** When true, primary_sensor_* values are model-estimated for the demo UI */
  readingsEstimated?: boolean;
  events: Array<{
    event_id: string;
    event_type: string;
    severity: string;
    description: string;
    timestamp_start: string;
    timestamp_end?: string | null;
  }>;
  onClose: () => void;
  onAskAgent?: (query: string, contextTimestamp?: string) => void;
  loading?: boolean;
  downSinceMin?: number;
  casesLost?: number;
}

function MachineDetailCard({
  machineId,
  meta,
  neighbors,
  lineKpi,
  materialsHere = [],
  state,
  readingsEstimated = false,
  events,
  onClose,
  onAskAgent,
  loading = false,
  downSinceMin,
  casesLost,
}: MachineDetailCardProps) {
  const status = (state?.status ?? 'idle') as MachineStatus;
  const statusColor = STATUS_COLORS[status] ?? STATUS_COLORS.idle;
  const isDegraded = status === 'stopped' || status === 'degraded' || status === 'starved';
  const title = meta?.displayName ?? machineId;
  const nom = meta?.nominalRate;
  const rateUnit = meta?.rateUnit ?? '';
  const speedPct = state?.speed_pct != null ? Math.max(0, Math.min(100, toNumber(state.speed_pct))) : null;
  const throughput = state?.throughput_per_min != null ? toNumber(state.throughput_per_min) : null;
  const utilizationPct =
    throughput != null && nom != null && nom > 0 ? Math.min(150, Math.max(0, (throughput / nom) * 100)) : null;
  const alarmDensity =
    throughput != null && throughput > 0 && state != null ? (toNumber(state.alarm_count) * 60) / throughput : null;
  const activeEvents = events.filter((ev) => ev.event_type !== 'resolved' && !ev.timestamp_end);
  const resolvedEvents = events.filter((ev) => ev.event_type === 'resolved' || !!ev.timestamp_end);

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <div>
          <CardTitle id="machine-detail-title" className="text-base font-medium">
            {title}
          </CardTitle>
          <p className="text-[11px] text-muted-foreground font-mono mt-0.5">{machineId}</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded-md p-1 hover:bg-muted transition-colors"
          aria-label="Close machine detail"
        >
          <span className="text-lg leading-none">×</span>
        </button>
      </CardHeader>
      <CardContent className="space-y-4">
        {loading ? (
          <div className="space-y-4">
            <Skeleton className="h-5 w-24" />
            <div className="grid grid-cols-2 gap-3">
              {Array.from({ length: 6 }, (_, i) => `machine-detail-skeleton-${i}`).map((key) => (
                <Skeleton key={key} className="h-12 w-full" />
              ))}
            </div>
            <Skeleton className="h-20 w-full" />
          </div>
        ) : (
          <>
            {meta && (
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-xs text-muted-foreground leading-snug flex-1 min-w-0">{meta.role}</p>
                <span className="text-[10px] uppercase tracking-wide rounded border border-border px-1.5 py-0.5 text-muted-foreground shrink-0">
                  {meta.type.replace(/_/g, ' ')}
                </span>
              </div>
            )}
            <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
              {neighbors.upstream && (
                <span>
                  Upstream: <span className="text-foreground">{neighbors.upstream}</span>
                </span>
              )}
              {neighbors.downstream && (
                <span>
                  Downstream: <span className="text-foreground">{neighbors.downstream}</span>
                </span>
              )}
            </div>

            {lineKpi && (
              <div className="rounded-md border border-border bg-muted/30 px-2.5 py-2">
                <p className="text-[10px] font-medium text-muted-foreground mb-1.5">Line snapshot (shift aggregate)</p>
                <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
                  <div>
                    <span className="text-muted-foreground">OEE </span>
                    <span className="font-medium tabular-nums">{toNumber(lineKpi.oee_to_now).toFixed(1)}%</span>
                  </div>
                  <div>
                    <span className="text-muted-foreground">Avail. </span>
                    <span className="font-medium tabular-nums">
                      {toNumber(lineKpi.availability_to_now).toFixed(1)}%
                    </span>
                  </div>
                  <div>
                    <span className="text-muted-foreground">Perf. </span>
                    <span className="font-medium tabular-nums">{toNumber(lineKpi.performance_to_now).toFixed(1)}%</span>
                  </div>
                  <div>
                    <span className="text-muted-foreground">Quality </span>
                    <span className="font-medium tabular-nums">{toNumber(lineKpi.quality_to_now).toFixed(1)}%</span>
                  </div>
                  <div className="col-span-2 pt-0.5 border-t border-border/60 mt-0.5">
                    <span className="text-muted-foreground">Total throughput </span>
                    <span className="font-medium tabular-nums">
                      {toNumber(lineKpi.total_throughput).toLocaleString()}
                    </span>
                    <span className="text-muted-foreground"> /min</span>
                  </div>
                </div>
              </div>
            )}

            {/* Status badge */}
            <div className="flex items-center gap-2">
              <span className="h-2.5 w-2.5 rounded-full shrink-0" style={{ backgroundColor: statusColor }} />
              <span className="text-sm font-medium capitalize">{status}</span>
            </div>

            <div className="rounded-md bg-muted/60 border border-border px-2.5 py-2 text-[11px] text-muted-foreground leading-relaxed">
              <span className="font-medium text-foreground">Speed %</span> on the schematic is{' '}
              <strong>commanded speed vs nominal design rate</strong> for this asset (bounded 0–100%). Line{' '}
              <strong>availability / OEE</strong> is shown in the KPI strip above — it is a shift-level aggregate, not
              this per-machine percentage.
            </div>

            {nom != null && (
              <div className="text-xs">
                <span className="text-muted-foreground">Nominal design rate: </span>
                <span className="font-medium tabular-nums">
                  {nom} {rateUnit}
                </span>
              </div>
            )}

            {downSinceMin != null && (
              <div className="rounded-md border border-red-500/30 bg-red-500/10 px-2.5 py-1.5 text-xs font-medium text-red-700 dark:text-red-400">
                Down {downSinceMin} min &middot; ~{(casesLost ?? 0).toLocaleString()} cases lost
              </div>
            )}

            {/* Inline AI action for degraded/stopped machines */}
            {isDegraded && onAskAgent && (
              <button
                type="button"
                onClick={() =>
                  onAskAgent(
                    `Diagnose ${machineId}: status is "${status}". What is the root cause, impact on the line, and recommended recovery actions?`
                  )
                }
                className={cn(
                  'w-full rounded-lg border-2 border-dashed border-primary/40 bg-primary/5 px-3 py-2',
                  'text-sm font-medium text-primary hover:bg-primary/10 hover:border-primary/60 transition-colors',
                  'flex items-center justify-center gap-2'
                )}
              >
                <span className="text-base">🤖</span>
                Diagnose with AI
              </button>
            )}

            {/* Metrics grid */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <p className="text-xs text-muted-foreground">Speed (% of nominal)</p>
                <p className="text-sm font-medium tabular-nums">{speedPct != null ? `${speedPct.toFixed(1)}%` : '—'}</p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Throughput (model output / min)</p>
                <p className="text-sm font-medium">
                  {state ? toNumber(state.throughput_per_min).toLocaleString() : '—'}
                  {state && meta?.nominalRate != null && (
                    <span className="text-muted-foreground font-normal text-xs">
                      {' '}
                      &nbsp;· nominal {nom} {rateUnit}
                    </span>
                  )}
                </p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">
                  {meta?.primary1?.label ?? 'Primary metric 1'}
                  {meta?.primary1?.unit ? ` (${meta.primary1.unit})` : ''}
                </p>
                <p className="text-sm font-medium">
                  {state?.primary_sensor_1 != null ? toNumber(state.primary_sensor_1).toLocaleString() : '—'}
                </p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">
                  {meta?.primary2?.label ?? 'Primary metric 2'}
                  {meta?.primary2?.unit ? ` (${meta.primary2.unit})` : ''}
                </p>
                <p className="text-sm font-medium">
                  {state?.primary_sensor_2 != null ? toNumber(state.primary_sensor_2).toLocaleString() : '—'}
                </p>
              </div>
              <div className="col-span-2">
                <p className="text-xs text-muted-foreground mb-1">
                  {meta?.material?.label ?? 'Material / level'}
                  {meta?.material?.unit ? ` (${meta.material.unit})` : ''}
                </p>
                {state?.material_level_pct != null ? (
                  <div className="space-y-1">
                    <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
                      <div
                        className="h-full rounded-full bg-primary transition-all"
                        style={{
                          width: `${Math.min(100, Math.max(0, toNumber(state.material_level_pct)))}%`,
                        }}
                      />
                    </div>
                    <p className="text-sm font-medium tabular-nums">
                      {`${toNumber(state.material_level_pct).toFixed(1)}%`}
                    </p>
                  </div>
                ) : (
                  <p className="text-sm font-medium">—</p>
                )}
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Active Alarms</p>
                <p className={cn('text-sm font-medium', (state?.alarm_count ?? 0) > 0 && 'text-destructive')}>
                  {state ? toNumber(state.alarm_count) : '—'}
                </p>
              </div>
              {state?.ts != null && state.ts !== '' && (
                <div className="col-span-2">
                  <p className="text-xs text-muted-foreground">Last update</p>
                  <p className="text-sm font-medium">{formatTimestamp(state.ts)}</p>
                </div>
              )}
            </div>

            <div>
              <h4 className="text-sm font-medium mb-1.5">Live Sensors</h4>
              <div className="rounded-md border border-border bg-muted/30 px-2.5 py-2">
                <div className="grid grid-cols-[auto_1fr_auto] gap-x-2 gap-y-1.5 text-[11px]">
                  <span className="font-mono text-muted-foreground">SP-001</span>
                  <span className="text-muted-foreground">Commanded speed</span>
                  <span className="font-medium tabular-nums">{speedPct != null ? `${speedPct.toFixed(1)}%` : '—'}</span>

                  <span className="font-mono text-muted-foreground">TP-002</span>
                  <span className="text-muted-foreground">Model throughput</span>
                  <span className="font-medium tabular-nums">
                    {throughput != null ? `${throughput.toFixed(1)}/min` : '—'}
                  </span>

                  <span className="font-mono text-muted-foreground">UT-003</span>
                  <span className="text-muted-foreground">Utilization vs nominal</span>
                  <span className="font-medium tabular-nums">
                    {utilizationPct != null ? `${utilizationPct.toFixed(1)}%` : '—'}
                  </span>

                  <span className="font-mono text-muted-foreground">AL-004</span>
                  <span className="text-muted-foreground">Active alarms</span>
                  <span className={cn('font-medium tabular-nums', (state?.alarm_count ?? 0) > 0 && 'text-destructive')}>
                    {state != null ? toNumber(state.alarm_count) : '—'}
                  </span>

                  <span className="font-mono text-muted-foreground">AD-005</span>
                  <span className="text-muted-foreground">Alarm density</span>
                  <span className="font-medium tabular-nums">
                    {alarmDensity != null ? `${alarmDensity.toFixed(2)}/hr` : '—'}
                  </span>

                  <span className="font-mono text-muted-foreground">ML-006</span>
                  <span className="text-muted-foreground">Material level</span>
                  <span className="font-medium tabular-nums">
                    {state?.material_level_pct != null ? `${toNumber(state.material_level_pct).toFixed(1)}%` : '—'}
                  </span>
                </div>
              </div>
            </div>

            {readingsEstimated ? (
              <p className="text-[10px] text-muted-foreground border-t border-border pt-2">
                Primary metrics are <strong>client-side estimates</strong> when detailed OT columns are not in the SSE
                payload.
              </p>
            ) : (
              <p className="text-[10px] text-muted-foreground border-t border-border pt-2">
                Primary metrics are <strong>aligned with the live simulation model</strong> (same formulas as
                SCADA-style tags in the demo).
              </p>
            )}

            {materialsHere.length > 0 && (
              <div>
                <h4 className="text-sm font-medium mb-1.5">Materials at this station</h4>
                <ul className="space-y-1.5 max-h-36 overflow-y-auto text-[11px]">
                  {materialsHere.map((m) => (
                    <li
                      key={m.material_id}
                      className="grid grid-cols-[1fr_auto] gap-x-2 gap-y-0.5 rounded border border-border bg-muted/40 px-2 py-1.5 text-[11px]"
                    >
                      <span className="font-mono truncate" title={m.material_id}>
                        {m.material_id}
                      </span>
                      <span
                        className={cn(
                          'justify-self-end rounded px-1 text-[10px] font-medium',
                          m.status === 'CRITICAL' && 'bg-red-500/15 text-red-700 dark:text-red-400',
                          m.status === 'LOW' && 'bg-amber-500/15 text-amber-800 dark:text-amber-400',
                          (m.status === 'OK' || !m.status) && 'bg-green-500/10 text-green-800 dark:text-green-400'
                        )}
                      >
                        {m.status}
                      </span>
                      <span className="col-span-2 text-muted-foreground">
                        {toNumber(m.on_hand_qty).toLocaleString(undefined, {
                          minimumFractionDigits: 1,
                          maximumFractionDigits: 1,
                        })}{' '}
                        on hand
                        {m.consumption_rate_per_hour != null && (
                          <span className="text-foreground">
                            {' '}
                            · {toNumber(m.consumption_rate_per_hour).toFixed(1)}/h burn
                          </span>
                        )}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* Active events */}
            {activeEvents.length > 0 && (
              <div>
                <h4 className="text-sm font-medium mb-2">Active Events</h4>
                <ul className="space-y-2 max-h-32 overflow-y-auto">
                  {activeEvents.map((ev) => (
                    <li key={ev.event_id} className="text-xs p-2 rounded-md bg-muted/50 border border-border">
                      <div className="flex items-center gap-2">
                        <StatusBadge variant={severityToVariant(ev.severity)} dot>
                          {ev.severity}
                        </StatusBadge>
                        <span className="text-muted-foreground">{formatTimestamp(ev.timestamp_start)}</span>
                      </div>
                      <p className="mt-0.5">{ev.description}</p>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {resolvedEvents.length > 0 && (
              <div>
                <h4 className="text-sm font-medium mb-2 text-muted-foreground">Resolved Incidents</h4>
                <ul className="space-y-2 max-h-24 overflow-y-auto">
                  {resolvedEvents.map((ev) => (
                    <li
                      key={ev.event_id}
                      className="text-xs p-2 rounded-md bg-muted/20 border border-border/70 opacity-80"
                    >
                      <div className="flex items-center gap-2">
                        <StatusBadge variant="neutral" dot>
                          RESOLVED
                        </StatusBadge>
                        <span className="text-muted-foreground">
                          {formatTimestamp(ev.timestamp_end || ev.timestamp_start)}
                        </span>
                      </div>
                      <p className="mt-0.5">{ev.description}</p>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

export default MachineDetailCard;
