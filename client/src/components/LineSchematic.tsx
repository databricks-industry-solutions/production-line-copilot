/**
 * Visualizes machine nodes, flow edges, and cascade-risk hints for the production line.
 * Used by `App` as the clickable machine map that drives the detail drawer selection.
 * Layout rows are fixed to mirror the physical lane topology expected in operator walkthroughs.
 */
import { memo, useMemo, useCallback } from 'react';
import { Skeleton } from '@databricks/appkit-ui/react';
import { MACHINE_LAYOUT, STATUS_COLORS, STATUS_LABELS, type MachineStatus } from '../lib/constants';
import { cn } from '../lib/utils';
import DataSourceBadge from './DataSourceBadge';

export interface LineSchematicProps {
  machineStates: Map<
    string,
    {
      status: string;
      throughput_per_min: number;
      speed_pct: number | null;
      alarm_count: number;
      down_since_min?: number;
      cases_lost?: number;
    }
  >;
  selectedMachine: string | null;
  highlightMachines: string[];
  onMachineClick: (machineId: string) => void;
  loading?: boolean;
}

const STATUS_BG: Record<string, string> = {
  running: 'bg-green-500',
  stopped: 'bg-red-500',
  degraded: 'bg-yellow-500',
  starved: 'bg-gray-400',
  idle: 'bg-blue-500',
};

const STATUS_RING: Record<string, string> = {
  running: 'ring-green-500/30',
  stopped: 'ring-red-500/30',
  degraded: 'ring-yellow-500/30',
  starved: 'ring-gray-400/30',
  idle: 'ring-blue-500/30',
};

const ROWS = [
  MACHINE_LAYOUT.filter((m) => m.y === 3),
  MACHINE_LAYOUT.filter((m) => m.y === 2),
  MACHINE_LAYOUT.filter((m) => m.y === 1),
];

const SCHEMATIC_SOURCE_DETAILS: Record<string, string> = {
  SCADA: 'Primary machine state, speed, and alarm values used for node colors.',
  PLC: 'Discrete equipment status/commands reflected in live machine tiles.',
  Historian: 'Short-window time-series used for trend/risk propagation hints.',
};

function LineSchematic({
  machineStates,
  selectedMachine,
  highlightMachines,
  onMachineClick,
  loading = false,
}: LineSchematicProps) {
  const cascadeRisk = useMemo(() => {
    const stoppedIdxSet = new Set<number>();
    MACHINE_LAYOUT.forEach((m, idx) => {
      const state = machineStates.get(m.machineId);
      if (state?.status === 'stopped') stoppedIdxSet.add(idx);
    });
    const risk = new Set<string>();
    for (const idx of stoppedIdxSet) {
      for (let i = idx + 1; i < Math.min(idx + 4, MACHINE_LAYOUT.length); i++) {
        const ds = machineStates.get(MACHINE_LAYOUT[i].machineId);
        if (ds?.status !== 'stopped') risk.add(MACHINE_LAYOUT[i].machineId);
      }
    }
    return risk;
  }, [machineStates]);

  const handleClick = useCallback((id: string) => onMachineClick(id), [onMachineClick]);

  if (loading) {
    return (
      <div
        className="flex items-center justify-center rounded-lg border bg-muted/30 p-4"
        style={{ minHeight: '200px' }}
        aria-label="Production line schematic loading"
      >
        <Skeleton className="h-[180px] w-full max-w-4xl rounded" />
      </div>
    );
  }

  return (
    <div
      className="rounded-lg border border-border bg-card p-3 space-y-1 overflow-x-auto"
      role="group"
      aria-label="Production line schematic — select a machine to view details"
    >
      <div className="flex items-center justify-between mb-1">
        <span className="text-[11px] text-muted-foreground font-medium">Production Line</span>
        <DataSourceBadge sources={['SCADA', 'PLC', 'Historian']} details={SCHEMATIC_SOURCE_DETAILS} />
      </div>
      {ROWS.map((row) => (
        <div key={row.map((m) => m.machineId).join('-')} className="flex items-center gap-1">
          {row.map((machine, mi) => {
            const state = machineStates.get(machine.machineId);
            const status = (state?.status ?? 'idle') as MachineStatus;
            const isSelected = selectedMachine === machine.machineId;
            const isHighlighted = highlightMachines.includes(machine.machineId);
            const isCascade = cascadeRisk.has(machine.machineId);
            const alarms = state?.alarm_count ?? 0;
            const speed = state?.speed_pct;
            const throughput = state?.throughput_per_min ?? 0;

            return (
              <div key={machine.machineId} className="flex items-center">
                {/* Edge connector */}
                {mi > 0 && (
                  <div className="flex items-center" aria-hidden>
                    <svg width="20" height="12" viewBox="0 0 20 12" className="shrink-0">
                      <line
                        x1="0"
                        y1="6"
                        x2="20"
                        y2="6"
                        stroke={status === 'stopped' || isCascade ? '#ef4444' : '#d1d5db'}
                        strokeWidth="2"
                        strokeDasharray={isCascade ? '4 2' : undefined}
                      />
                      <polygon
                        points="16,3 20,6 16,9"
                        fill={status === 'stopped' || isCascade ? '#ef4444' : '#d1d5db'}
                      />
                    </svg>
                  </div>
                )}

                {/* Machine node */}
                <button
                  type="button"
                  onClick={() => handleClick(machine.machineId)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      handleClick(machine.machineId);
                    }
                  }}
                  title={`${machine.label} — ${STATUS_LABELS[status] ?? status}. Speed is % of nominal (not OEE). Click for details.`}
                  aria-label={`${machine.label} — ${STATUS_LABELS[status] ?? status}${alarms > 0 ? `, ${alarms} alarm${alarms > 1 ? 's' : ''}` : ''}. Speed percent is relative to nominal asset speed.`}
                  aria-pressed={isSelected}
                  className={cn(
                    'relative flex flex-col items-center justify-center rounded-lg border-2 px-2 py-1.5 min-w-[70px] transition-all',
                    'hover:scale-105 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                    isSelected && 'ring-2 ring-offset-1 ring-sky-500 border-sky-500 scale-105 shadow-md',
                    !isSelected && isHighlighted && 'ring-2 ring-offset-1 ring-amber-400 border-amber-400',
                    !isSelected && !isHighlighted && isCascade && 'border-amber-400 border-dashed',
                    !isSelected && !isHighlighted && !isCascade && 'border-border',
                    STATUS_RING[status]
                  )}
                  style={{ backgroundColor: `${STATUS_COLORS[status] ?? STATUS_COLORS.idle}10` }}
                >
                  {/* Status dot */}
                  <span
                    className={cn(
                      'absolute -top-1 -right-1 h-2.5 w-2.5 rounded-full border border-card',
                      STATUS_BG[status] ?? 'bg-blue-500'
                    )}
                    aria-hidden
                  />
                  {alarms > 0 && (
                    <span
                      className="absolute -top-1 -left-1 flex h-3.5 w-3.5 items-center justify-center rounded-full bg-red-500 text-[8px] font-bold text-white"
                      aria-hidden
                    >
                      {alarms}
                    </span>
                  )}
                  <span className="text-[11px] font-semibold leading-tight truncate max-w-[60px]">{machine.label}</span>
                  <span
                    className="text-[10px] text-muted-foreground leading-tight"
                    title="Speed % of nominal design rate (not line availability)"
                  >
                    {speed != null ? `${speed.toFixed(0)}% spd` : '—'}
                  </span>
                  <span className="text-[10px] text-muted-foreground leading-tight" title="Model output per minute">
                    {throughput > 0 ? `${throughput.toFixed(0)}/m` : '—'}
                  </span>
                </button>
              </div>
            );
          })}
        </div>
      ))}

      {/* Legend */}
      <div className="flex items-center gap-3 pt-1 text-[11px] text-muted-foreground border-t border-border mt-1">
        {(Object.keys(STATUS_COLORS) as MachineStatus[]).map((key) => (
          <span key={key} className="flex items-center gap-1">
            <span className={cn('h-2 w-2 rounded-full', STATUS_BG[key])} />
            {STATUS_LABELS[key]}
          </span>
        ))}
        <span className="flex items-center gap-1 ml-2">
          <span className="h-2 w-2 rounded-full border-2 border-dashed border-amber-400" />
          Cascade risk
        </span>
        <span className="ml-auto text-[10px] max-w-[220px] text-right leading-tight">
          <strong>spd</strong> = speed vs nominal · <strong>/m</strong> = output/min · not availability
        </span>
      </div>
    </div>
  );
}

export default memo(LineSchematic);
