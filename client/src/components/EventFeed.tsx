/**
 * Renders live incident rows with active/history reconciliation and quick actions.
 * Used in `App` Events & AI tab and shared by tab-badge logic via exported helpers.
 * Reconciles append-only resolve markers so a single incident never appears as both open and resolved.
 */
import { useMemo, useState } from 'react';
import { Skeleton } from '@databricks/appkit-ui/react';
import { formatTimestamp, isoToEpoch, cn } from '../lib/utils';
import StatusBadge from './StatusBadge';
import { severityToVariant } from '../lib/statusBadgeUtils';
import { isEventActive, buildResolvedTsByMachine } from '../lib/eventFeedUtils';
import DataSourceBadge from './DataSourceBadge';

export interface EventFeedEvent {
  event_id: string;
  machine_id: string;
  event_type: string;
  severity: string;
  timestamp_start: string;
  timestamp_end: string | null;
  duration_minutes: number;
  root_cause: string;
  description: string;
  corrective_action?: string;
}

export interface HandledAlertItem {
  scenarioName: string;
  description: string;
  keyMachines: string;
  timestamp: number;
}

export interface EventFeedProps {
  events: EventFeedEvent[];
  currentTimestamp: number;
  loading?: boolean;
  onAskAbout?: (query: string, contextTimestamp?: string) => void;
  onJumpToTime?: (epochMs: number) => void;
  handledAlerts?: HandledAlertItem[];
  onResolveAlert?: (index: number) => void;
  /** Resolve every active incident on the given machine. When provided,
   *  active rows render a Recover button next to "Ask AI". Returns when
   *  the API call completes — the row stays visible until the next SSE
   *  payload reflects the resolved state (~30 s through producer/quality-gate). */
  onRecoverMachine?: (machineId: string) => Promise<unknown>;
}

type SeverityFilter = 'all' | 'critical' | 'major' | 'minor' | 'history';

const FILTER_PILLS: { value: SeverityFilter; label: string; color: string }[] = [
  { value: 'all', label: 'All', color: '' },
  { value: 'critical', label: 'Critical', color: 'bg-red-500' },
  { value: 'major', label: 'Major', color: 'bg-red-400' },
  { value: 'minor', label: 'Minor', color: 'bg-yellow-500' },
  // "History" replaces the old "Info" filter. The producer emits a
  // synthetic severity='info' / event_type='resolved' row each time an
  // incident closes (Zerobus is append-only — it can't UPDATE the
  // original start row's timestamp_end). The new design hides those
  // duplicate marker rows entirely and instead tags the original start
  // row as Resolved via resolvedTsByMachine, so the same incident no
  // longer appears under both Critical and Info.
  { value: 'history', label: 'History', color: 'bg-muted-foreground' },
];

const EVENT_SOURCE_DETAILS: Record<string, string> = {
  MES: 'Incident lifecycle and operator event classifications.',
  SCADA: 'Machine status/alarm transitions that trigger incident rows.',
  CMMS: 'Mapped maintenance work-order context for downtime events.',
};

// Re-export for EventFeed.test.ts (tests/ is eslint-ignored but imports this module path).
// eslint-disable-next-line react-refresh/only-export-components -- test compatibility re-exports
export { isEventActive, buildResolvedTsByMachine };

function matchesFilter(e: EventFeedEvent, active: boolean, filter: SeverityFilter): boolean {
  if (filter === 'all') return true;
  // "History" = resolved incidents (any original severity). Active rows
  // never appear here.
  if (filter === 'history') return !active;
  // Severity pills now show only currently-active incidents of that
  // severity. Past instances belong to History — this prevents the
  // same incident from showing under both "Critical" and "Info" (the
  // duplicate the user flagged).
  if (!active) return false;
  const s = e.severity?.toLowerCase() ?? '';
  return s === filter;
}

export default function EventFeed({
  events,
  currentTimestamp,
  loading = false,
  onAskAbout,
  onJumpToTime,
  handledAlerts = [],
  onResolveAlert,
  onRecoverMachine,
}: EventFeedProps) {
  const [filter, setFilter] = useState<SeverityFilter>('all');

  // Map machine_id -> latest 'resolved' event timestamp. Built from the
  // FULL events list (before we drop event_type='resolved' rows below)
  // so the start rows can still resolve themselves visually.
  const resolvedTsByMachine = useMemo(() => buildResolvedTsByMachine(events), [events]);

  const allVisible = useMemo(() => {
    return (
      [...events]
        // Hide the synthetic event_type='resolved' marker rows entirely —
        // they duplicate the original start row, which already renders
        // as "Resolved" via resolvedTsByMachine. Suppressing them removes
        // the "one incident in Critical AND Info" double-count.
        .filter((e) => e.event_type !== 'resolved')
        .filter((e) => isoToEpoch(String(e.timestamp_start)) <= currentTimestamp)
        .sort((a, b) => {
          const aActive = isEventActive(a, currentTimestamp, resolvedTsByMachine) ? 0 : 1;
          const bActive = isEventActive(b, currentTimestamp, resolvedTsByMachine) ? 0 : 1;
          if (aActive !== bActive) return aActive - bActive;
          return isoToEpoch(String(b.timestamp_start)) - isoToEpoch(String(a.timestamp_start));
        })
    );
  }, [events, currentTimestamp, resolvedTsByMachine]);

  const counts = useMemo(() => {
    const c: Record<SeverityFilter, number> = {
      all: 0,
      critical: 0,
      major: 0,
      minor: 0,
      history: 0,
    };
    for (const e of allVisible) {
      c.all++;
      const active = isEventActive(e, currentTimestamp, resolvedTsByMachine);
      if (!active) {
        c.history++;
        continue;
      }
      const s = e.severity?.toLowerCase() ?? '';
      if (s === 'critical') c.critical++;
      else if (s === 'major') c.major++;
      else if (s === 'minor') c.minor++;
    }
    return c;
  }, [allVisible, currentTimestamp, resolvedTsByMachine]);

  const filtered = useMemo(() => {
    return allVisible.filter((e) => {
      const active = isEventActive(e, currentTimestamp, resolvedTsByMachine);
      return matchesFilter(e, active, filter);
    });
  }, [allVisible, filter, currentTimestamp, resolvedTsByMachine]);
  const filteredActive = useMemo(
    () => filtered.filter((e) => isEventActive(e, currentTimestamp, resolvedTsByMachine)),
    [filtered, currentTimestamp, resolvedTsByMachine]
  );
  const filteredResolved = useMemo(
    () => filtered.filter((e) => !isEventActive(e, currentTimestamp, resolvedTsByMachine)),
    [filtered, currentTimestamp, resolvedTsByMachine]
  );

  if (loading) {
    return (
      <div className="flex h-full flex-col rounded-lg border border-border bg-card">
        <div className="flex items-center justify-between px-3 py-2 border-b border-border">
          <Skeleton className="h-4 w-20" />
          <Skeleton className="h-3 w-28" />
        </div>
        <div className="flex-1 p-3 space-y-1.5">
          {Array.from({ length: 4 }, (_, i) => `event-feed-skeleton-${i}`).map((key) => (
            <Skeleton key={key} className="h-10 w-full rounded" />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col rounded-lg border border-border bg-card overflow-hidden">
      {/* Header with filter pills */}
      <div className="flex shrink-0 items-center justify-between px-3 py-1.5 border-b border-border gap-2">
        <div className="flex items-center gap-1.5">
          <h2 className="text-xs font-semibold">Events</h2>
          <span className="rounded-full bg-muted px-1.5 py-px text-[11px] font-medium">{allVisible.length}</span>
          <DataSourceBadge sources={['MES', 'SCADA', 'CMMS']} details={EVENT_SOURCE_DETAILS} />
        </div>
        <div className="flex items-center gap-1" role="group" aria-label="Filter events by severity">
          {FILTER_PILLS.map((pill) => {
            const count = counts[pill.value] ?? 0;
            const isActive = filter === pill.value;
            return (
              <button
                key={pill.value}
                type="button"
                onClick={() => setFilter(pill.value)}
                aria-pressed={isActive}
                className={cn(
                  'flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium transition-colors',
                  isActive
                    ? 'bg-primary/10 text-primary border border-primary/30'
                    : 'text-muted-foreground hover:text-foreground hover:bg-muted border border-transparent'
                )}
              >
                {pill.color && <span className={cn('h-1.5 w-1.5 rounded-full', pill.color)} />}
                {pill.label}
                {count > 0 && <span className="text-[11px] opacity-70">{count}</span>}
              </button>
            );
          })}
        </div>
      </div>

      {/* Handled alerts — user chose "I'll handle it" */}
      {handledAlerts.length > 0 && (
        <div className="border-b border-amber-500/30 bg-amber-500/5 px-2 py-1.5 space-y-1">
          <div className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-amber-500 animate-pulse" />
            <span className="text-[11px] font-bold text-amber-600 dark:text-amber-400 uppercase tracking-wide">
              Handling manually ({handledAlerts.length})
            </span>
          </div>
          {handledAlerts.map((ha) => (
            <div
              key={ha.scenarioName + ha.timestamp}
              className="rounded-md border border-amber-500/30 bg-amber-500/10 px-2 py-1.5"
            >
              <div className="flex items-center gap-1.5 text-[11px]">
                <span className="font-semibold text-amber-700 dark:text-amber-300">{ha.scenarioName}</span>
                {ha.keyMachines && <span className="text-muted-foreground">({ha.keyMachines})</span>}
              </div>
              <p className="text-[11px] text-muted-foreground mt-0.5">{ha.description}</p>
              <div className="flex items-center gap-1.5 mt-1">
                {onAskAbout && (
                  <button
                    onClick={() => onAskAbout(`Recovery plan for ${ha.scenarioName}: ${ha.description}`)}
                    className="rounded-full bg-amber-500/20 border border-amber-500/30 px-2 py-0.5 text-[11px] font-semibold text-amber-700 dark:text-amber-300 hover:bg-amber-500/30 transition-colors"
                  >
                    Ask AI for Recovery
                  </button>
                )}
                {onResolveAlert && (
                  <button
                    onClick={() => onResolveAlert(handledAlerts.indexOf(ha))}
                    className="rounded-full bg-green-500/20 border border-green-500/30 px-2 py-0.5 text-[11px] font-semibold text-green-700 dark:text-green-300 hover:bg-green-500/30 transition-colors"
                  >
                    Mark Resolved
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Filterable active/resolved sections */}
      <div className="flex-1 overflow-y-auto">
        {filtered.length === 0 && handledAlerts.length === 0 ? (
          <p className="text-xs text-muted-foreground py-6 text-center">
            {allVisible.length === 0 ? 'No events at current time' : 'No events match this filter'}
          </p>
        ) : (
          <div>
            {filteredActive.length > 0 && (
              <div className="divide-y divide-border">
                {filteredActive.map((event) => (
                  <EventRow
                    key={event.event_id}
                    event={event}
                    onAskAbout={onAskAbout}
                    onJumpToTime={onJumpToTime}
                    onRecoverMachine={onRecoverMachine}
                    active
                  />
                ))}
              </div>
            )}

            {filteredResolved.length > 0 && (
              <>
                <div className="px-3 py-1.5 text-[11px] font-medium text-muted-foreground border-y border-border bg-muted/20">
                  Resolved incidents ({filteredResolved.length})
                </div>
                <div className="divide-y divide-border">
                  {filteredResolved.map((event) => (
                    <EventRow
                      key={event.event_id}
                      event={event}
                      onAskAbout={onAskAbout}
                      onJumpToTime={onJumpToTime}
                      active={false}
                    />
                  ))}
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function EventRow({
  event,
  onAskAbout,
  onJumpToTime,
  onRecoverMachine,
  active = true,
}: {
  event: EventFeedEvent;
  onAskAbout?: (query: string, contextTimestamp?: string) => void;
  onJumpToTime?: (epochMs: number) => void;
  onRecoverMachine?: (machineId: string) => Promise<unknown>;
  active?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [recovering, setRecovering] = useState(false);

  // The producer emits a synthetic "resolved" event when an incident clears;
  // those rows aren't actionable so we hide the Recover button on them.
  const canRecover =
    active &&
    !!onRecoverMachine &&
    !!event.machine_id &&
    event.event_type !== 'resolved' &&
    (event.severity?.toLowerCase() ?? '') !== 'info';

  const handleRecover = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!onRecoverMachine || recovering) return;
    setRecovering(true);
    try {
      await onRecoverMachine(event.machine_id);
    } catch {
      // surface remains unchanged; backend/state refresh will clarify final status
    } finally {
      setRecovering(false);
    }
  };

  return (
    <div className={cn(!active && 'opacity-50')}>
      <div
        className="flex flex-col gap-0.5 px-3 py-2 hover:bg-muted/20 transition-colors cursor-pointer text-[12px]"
        onClick={() => setOpen(!open)}
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            setOpen(!open);
          }
        }}
      >
        <div className="flex items-center gap-2">
          {active && (
            <span className="relative flex h-2 w-2 shrink-0">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-red-400 opacity-75" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-red-500" />
            </span>
          )}
          <span className="font-medium">{event.machine_id}</span>
          <StatusBadge variant={severityToVariant(event.severity)} dot>
            {event.severity}
          </StatusBadge>
          <span className="text-muted-foreground truncate">{event.event_type}</span>
          {!active && <StatusBadge variant="neutral">Resolved</StatusBadge>}
          <span className="ml-auto shrink-0 text-[11px] tabular-nums text-muted-foreground">
            {formatTimestamp(String(event.timestamp_start))}
          </span>
        </div>
        <div className="flex items-center gap-2 pl-4">
          <span className="text-muted-foreground truncate text-[11px]">
            {event.description.length > 60 ? event.description.slice(0, 60) + '...' : event.description}
          </span>
          <span className="ml-auto flex items-center gap-1.5 shrink-0">
            {event.duration_minutes > 0 && <StatusBadge variant="neutral">{event.duration_minutes}m</StatusBadge>}
            {onJumpToTime && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onJumpToTime(isoToEpoch(String(event.timestamp_start)));
                }}
                className="rounded-full border border-border px-2 py-0.5 text-[11px] font-medium text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
              >
                Jump to time
              </button>
            )}
            {onAskAbout && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onAskAbout(
                    `Analyze the ${event.event_type} on ${event.machine_id}: ${event.description}. What is the root cause, impact on the line, and what do you recommend?`,
                    event.timestamp_start
                  );
                }}
                className="rounded-full bg-primary/10 border border-primary/30 px-2 py-0.5 text-[11px] font-medium text-primary hover:bg-primary/20 transition-colors"
              >
                Ask AI
              </button>
            )}
            {canRecover && (
              <button
                onClick={(e) => {
                  void handleRecover(e);
                }}
                disabled={recovering}
                title={`Clear active fault on ${event.machine_id} — line will recover over ~30 s`}
                className="rounded-full bg-green-500/10 border border-green-500/30 px-2 py-0.5 text-[11px] font-medium text-green-700 dark:text-green-300 hover:bg-green-500/20 disabled:opacity-50 transition-colors"
              >
                {recovering ? 'Recovering…' : 'Recover'}
              </button>
            )}
          </span>
        </div>
      </div>
      {open && (
        <div className="px-3 pb-2 text-[11px] space-y-0.5 text-muted-foreground ml-4">
          {event.root_cause && (
            <p>
              <span className="font-medium text-foreground">Root cause:</span> {event.root_cause}
            </p>
          )}
          {event.corrective_action && (
            <p>
              <span className="font-medium text-foreground">Resolution:</span> {event.corrective_action}
            </p>
          )}
          {!event.root_cause && !event.corrective_action && <p className="italic">No details recorded.</p>}
        </div>
      )}
    </div>
  );
}
