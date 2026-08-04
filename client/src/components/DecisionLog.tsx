/**
 * Stores and renders a local decision audit trail for assistant responses.
 * Used by chat workflows that call `addDecision` after receiving final specialist answers.
 * Persists to browser storage only, intentionally capped to keep payload size bounded.
 */
import { useState, useEffect, useCallback } from 'react';
import { cn } from '../lib/utils';
import { loadLog, type DecisionEntry } from '../lib/decisionLogStorage';

const STORAGE_KEY = 'line_manager_decision_log';

const SPECIALIST_LABELS: Record<string, string> = {
  oee_coach: 'OEE Coach',
  downtime_analyst: 'Downtime Analyst',
  quality_specialist: 'Quality Specialist',
  schedule_optimizer: 'Schedule Optimizer',
  maintenance_predictor: 'Maintenance Predictor',
  supply_chain_advisor: 'Supply Chain Advisor',
  strategic_advisor: 'Strategic Advisor',
  general: 'General',
};

export default function DecisionLog() {
  const [entries, setEntries] = useState<DecisionEntry[]>(() => loadLog());
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    const interval = setInterval(() => setEntries(loadLog()), 5000);
    return () => clearInterval(interval);
  }, []);

  const clearLog = useCallback(() => {
    localStorage.removeItem(STORAGE_KEY);
    setEntries([]);
  }, []);

  if (entries.length === 0) return null;

  return (
    <div className="rounded-lg border border-border bg-card">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between px-3 py-2 hover:bg-muted/30 transition-colors"
      >
        <div className="flex items-center gap-2">
          <h3 className="text-xs font-semibold">Decision Log</h3>
          <span className="rounded-full bg-muted px-1.5 py-px text-[11px] font-medium">{entries.length}</span>
        </div>
        <div className="flex items-center gap-2">
          {expanded && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                clearLog();
              }}
              className="text-[11px] text-destructive hover:underline"
            >
              Clear
            </button>
          )}
          <span className="text-muted-foreground text-[11px]">{expanded ? '\u25BC' : '\u25B6'}</span>
        </div>
      </button>
      {expanded && (
        <div className="max-h-72 overflow-y-auto border-t border-border">
          {[...entries].reverse().map((e) => (
            <DecisionEntryRow key={e.id} entry={e} />
          ))}
        </div>
      )}
    </div>
  );
}

function DecisionEntryRow({ entry }: { entry: DecisionEntry }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="border-b border-border last:border-b-0">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-muted/20 transition-colors"
      >
        <span className="text-[11px] tabular-nums text-muted-foreground shrink-0">
          {new Date(entry.timestamp).toLocaleTimeString('en-GB', {
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
          })}
        </span>
        <span
          className={cn(
            'rounded px-1 py-px text-[11px] font-semibold shrink-0',
            entry.specialist.includes('downtime')
              ? 'bg-red-500/20 text-red-700 dark:text-red-300'
              : entry.specialist.includes('quality')
                ? 'bg-purple-500/20 text-purple-700 dark:text-purple-300'
                : entry.specialist.includes('oee')
                  ? 'bg-green-500/20 text-green-700 dark:text-green-300'
                  : entry.specialist.includes('schedule')
                    ? 'bg-yellow-500/20 text-yellow-700 dark:text-yellow-300'
                    : entry.specialist.includes('supply')
                      ? 'bg-cyan-500/20 text-cyan-700 dark:text-cyan-300'
                      : entry.specialist.includes('maintenance')
                        ? 'bg-orange-500/20 text-orange-700 dark:text-orange-300'
                        : 'bg-muted text-muted-foreground'
          )}
        >
          {SPECIALIST_LABELS[entry.specialist] ?? entry.specialist}
        </span>
        {entry.responseTimeMs != null && (
          <span className="text-[11px] text-muted-foreground shrink-0">
            {(entry.responseTimeMs / 1000).toFixed(1)}s
          </span>
        )}
        <span className="text-[11px] text-foreground truncate flex-1">{entry.query}</span>
        <span className="text-muted-foreground text-[11px] shrink-0">{open ? '\u25BC' : '\u25B6'}</span>
      </button>
      {open && (
        <div className="px-3 pb-2 space-y-2 text-[11px]">
          {entry.reasoningSteps && entry.reasoningSteps.length > 0 && (
            <div className="rounded-md border border-border bg-muted/20 p-2">
              <div className="font-semibold text-muted-foreground mb-1">Reasoning trace</div>
              <div className="space-y-0.5">
                {entry.reasoningSteps.map((step) => (
                  <div key={`${step.label}-${step.detail}`} className="flex items-start gap-1.5">
                    <span
                      className={cn(
                        'mt-px shrink-0',
                        step.label.includes('LLM') ? 'text-violet-500' : 'text-green-500'
                      )}
                    >
                      {step.label.includes('LLM') ? '◆' : '✓'}
                    </span>
                    <div>
                      <span className="font-semibold">{step.label}</span>
                      <span className="ml-1 text-muted-foreground/70">— {step.detail}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="text-muted-foreground">
            <span className="font-semibold text-foreground">Summary: </span>
            {entry.recommendation}
          </div>
        </div>
      )}
    </div>
  );
}
