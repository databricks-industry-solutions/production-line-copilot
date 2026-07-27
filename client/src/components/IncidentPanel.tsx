/**
 * Provides incident trigger controls and active-command resolution actions.
 * Used in `App` tower view sidebar for simulation demos and operator recovery flow.
 * Trigger controls can be visually hidden while keeping active incident actions always available.
 */
import { useState, useCallback } from 'react';
import { cn } from '../lib/utils';
import DataSourceBadge from './DataSourceBadge';

const INCIDENT_TYPES = [
  { id: 'machine_breakdown', label: 'Machine Breakdown', specialist: 'Downtime', color: 'bg-red-500' },
  { id: 'sensor_failure', label: 'Sensor Failure', specialist: 'Downtime', color: 'bg-red-400' },
  { id: 'speed_loss', label: 'Speed Loss', specialist: 'OEE', color: 'bg-amber-500' },
  { id: 'minor_stops', label: 'Minor Stops', specialist: 'OEE', color: 'bg-amber-400' },
  { id: 'quality_deviation', label: 'Quality Deviation', specialist: 'Quality', color: 'bg-orange-500' },
  { id: 'contamination', label: 'Contamination', specialist: 'Quality', color: 'bg-orange-600' },
  { id: 'material_shortage', label: 'Material Shortage', specialist: 'Supply', color: 'bg-purple-500' },
  { id: 'supply_delay', label: 'Supply Delay', specialist: 'Supply', color: 'bg-purple-400' },
  { id: 'vibration_anomaly', label: 'Vibration Anomaly', specialist: 'Maintenance', color: 'bg-blue-500' },
  { id: 'maintenance_overdue', label: 'Maintenance Overdue', specialist: 'Maintenance', color: 'bg-blue-400' },
  { id: 'safety_incident', label: 'Safety Incident', specialist: 'Safety', color: 'bg-red-700' },
  { id: 'zone_lockout', label: 'Zone Lockout', specialist: 'Safety', color: 'bg-red-600' },
  { id: 'changeover_delay', label: 'Changeover Delay', specialist: 'Scheduler', color: 'bg-teal-500' },
  { id: 'rush_order', label: 'Rush Order', specialist: 'Scheduler', color: 'bg-teal-400' },
] as const;

const SEVERITIES = ['low', 'medium', 'high', 'critical'] as const;

const TARGET_MACHINES = [
  'DEPALLETIZER',
  'UNCASER',
  'BOTTLE_WASHER',
  'EMPTY_INSPECTOR',
  'FILLER',
  'CAPPER',
  'PASTEURIZER',
  'LABELLER',
  'CODER',
  'CASE_PACKER',
  'SHRINK_WRAPPER',
  'CASE_SEALER',
  'CHECKWEIGHER',
  'PALLETIZER',
  'STRETCH_WRAPPER',
] as const;

const INCIDENT_SOURCE_DETAILS: Record<string, string> = {
  MES: 'Operator-triggered scenarios and shift command workflow.',
  CMMS: 'Maintenance incident taxonomy and resolution context.',
};

interface ActiveCommand {
  command_id: string;
  scenario_id: string;
  target_machine_id: string | null;
  severity: string;
  status: string;
}

interface IncidentPanelProps {
  shiftId: string;
  activeCommands: ActiveCommand[];
  onTrigger: (params: {
    shift_id: string;
    scenario_id: string;
    target_machine_id?: string;
    severity?: string;
  }) => Promise<unknown>;
  onResolve: (commandId: string) => Promise<unknown>;
  loading?: boolean;
  // When false, hide the simulator-only "Trigger Incident" card. The
  // operational "Active Incidents" card always renders so operators can
  // still resolve real faults. Defaults to true for back-compat.
  showTrigger?: boolean;
}

export default function IncidentPanel({
  shiftId,
  activeCommands,
  onTrigger,
  onResolve,
  loading,
  showTrigger = true,
}: IncidentPanelProps) {
  const [selectedIncident, setSelectedIncident] = useState<string>(INCIDENT_TYPES[0].id);
  const [selectedSeverity, setSelectedSeverity] = useState<(typeof SEVERITIES)[number]>('medium');
  const [selectedTarget, setSelectedTarget] = useState<string>(TARGET_MACHINES[0]);

  const handleTrigger = useCallback(() => {
    void onTrigger({
      shift_id: shiftId,
      scenario_id: selectedIncident,
      target_machine_id: selectedTarget,
      severity: selectedSeverity,
    });
  }, [shiftId, selectedIncident, selectedTarget, selectedSeverity, onTrigger]);

  return (
    <div className="space-y-3">
      {/* Trigger controls — simulator-only. Always mounted so the slide-in
          transition fires on toggle; gated visually with max-height +
          opacity + translate-x for a slide-from-right reveal. */}
      <div
        aria-hidden={!showTrigger}
        className={cn(
          'overflow-hidden transition-all duration-300 ease-out',
          showTrigger
            ? 'max-h-[600px] opacity-100 translate-x-0'
            : 'max-h-0 opacity-0 translate-x-6 pointer-events-none'
        )}
      >
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Trigger Incident</h3>
            <DataSourceBadge sources={['MES', 'CMMS']} details={INCIDENT_SOURCE_DETAILS} />
          </div>
          <div className="grid grid-cols-2 gap-2 mb-2">
            <select
              value={selectedIncident}
              onChange={(e) => setSelectedIncident(e.target.value)}
              className="rounded border border-input bg-background px-2 py-1.5 text-xs"
            >
              {INCIDENT_TYPES.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.label} ({t.specialist})
                </option>
              ))}
            </select>
            <select
              value={selectedTarget}
              onChange={(e) => setSelectedTarget(e.target.value)}
              className="rounded border border-input bg-background px-2 py-1.5 text-xs"
            >
              {TARGET_MACHINES.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </div>
          <div className="flex items-center gap-2">
            <div className="flex gap-1">
              {SEVERITIES.map((s) => (
                <button
                  key={s}
                  onClick={() => setSelectedSeverity(s)}
                  className={cn(
                    'rounded px-2 py-1 text-[10px] font-medium transition-colors',
                    selectedSeverity === s
                      ? s === 'critical'
                        ? 'bg-red-600 text-white'
                        : s === 'high'
                          ? 'bg-orange-500 text-white'
                          : s === 'medium'
                            ? 'bg-amber-500 text-white'
                            : 'bg-blue-500 text-white'
                      : 'bg-muted text-muted-foreground hover:bg-accent'
                  )}
                >
                  {s}
                </button>
              ))}
            </div>
            <button
              onClick={handleTrigger}
              disabled={loading}
              className="ml-auto rounded bg-destructive px-3 py-1.5 text-xs font-medium text-destructive-foreground hover:bg-destructive/90 disabled:opacity-50 transition-colors"
            >
              {loading ? 'Sending...' : 'Trigger'}
            </button>
          </div>
        </div>
      </div>

      {/* Active incidents */}
      {activeCommands.length > 0 && (
        <div className="rounded-lg border border-border bg-card p-3">
          <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">
            Active Incidents ({activeCommands.length})
          </h3>
          <div className="space-y-1.5">
            {activeCommands.map((cmd) => {
              const typeDef = INCIDENT_TYPES.find((t) => t.id === cmd.scenario_id);
              return (
                <div
                  key={cmd.command_id}
                  className="flex items-center justify-between rounded border border-border bg-background px-2 py-1.5"
                >
                  <div className="flex items-center gap-2">
                    <span className={cn('h-2 w-2 rounded-full', typeDef?.color ?? 'bg-gray-400')} />
                    <span className="text-xs font-medium">{typeDef?.label ?? cmd.scenario_id}</span>
                    {cmd.target_machine_id && (
                      <span className="text-[10px] text-muted-foreground">on {cmd.target_machine_id}</span>
                    )}
                    <span
                      className={cn(
                        'rounded px-1.5 py-0.5 text-[10px] font-medium',
                        cmd.status === 'active'
                          ? 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'
                          : 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400'
                      )}
                    >
                      {cmd.status}
                    </span>
                  </div>
                  <button
                    onClick={() => {
                      void onResolve(cmd.command_id);
                    }}
                    disabled={loading}
                    className="rounded bg-green-600 px-2 py-1 text-[10px] font-medium text-white hover:bg-green-700 disabled:opacity-50 transition-colors"
                  >
                    Resolve
                  </button>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
