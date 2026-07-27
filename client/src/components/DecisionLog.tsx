/**
 * Stores and renders a local decision audit trail for assistant responses and trace spans.
 * Used by chat workflows that call `addDecision` after receiving final specialist answers.
 * Persists to browser storage only, intentionally capped to keep payload size bounded.
 */
import { useState, useEffect, useCallback } from 'react';
import { cn } from '../lib/utils';
import { loadLog, type DecisionEntry } from '../lib/decisionLogStorage';

interface TraceSpan {
  name: string;
  span_type: string;
  status: string;
  duration_ms: number;
  inputs?: string;
  outputs?: string;
}

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

const SPAN_TYPE_STYLES: Record<string, { icon: string; color: string }> = {
  LLM: { icon: '◆', color: 'text-violet-500' },
  TOOL: { icon: '⚙', color: 'text-cyan-500' },
  CHAIN: { icon: '⛓', color: 'text-amber-500' },
  RETRIEVER: { icon: '🔍', color: 'text-green-500' },
  AGENT: { icon: '▶', color: 'text-blue-500' },
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

          {entry.traceSpans && entry.traceSpans.length > 0 && <TraceSpanViewer spans={entry.traceSpans} />}

          <div className="text-muted-foreground">
            <span className="font-semibold text-foreground">Summary: </span>
            {entry.recommendation}
          </div>
        </div>
      )}
    </div>
  );
}

function TraceSpanViewer({ spans }: { spans: TraceSpan[] }) {
  const [expanded, setExpanded] = useState(false);
  const totalMs = spans.reduce((sum, s) => sum + s.duration_ms, 0);

  return (
    <details
      open={expanded}
      onToggle={(e) => setExpanded((e.target as HTMLDetailsElement).open)}
      className="rounded-md border border-border bg-muted/10"
    >
      <summary className="flex items-center gap-2 px-2 py-1.5 cursor-pointer hover:bg-muted/20 transition-colors select-none">
        <span className="font-semibold text-muted-foreground">MLflow Trace</span>
        <span className="text-muted-foreground/60">{spans.length} spans</span>
        <span className="text-muted-foreground/60">
          · {totalMs > 1000 ? `${(totalMs / 1000).toFixed(1)}s` : `${totalMs}ms`}
        </span>
      </summary>
      {expanded && (
        <div className="border-t border-border">
          {totalMs > 0 && (
            <div className="px-2 pt-1.5 pb-1">
              <div className="flex h-3 w-full rounded overflow-hidden bg-muted/30 gap-px">
                {spans
                  .filter((s) => s.duration_ms > 0)
                  .map((span) => {
                    const pct = Math.max((span.duration_ms / totalMs) * 100, 1);
                    const bgColor =
                      span.span_type === 'LLM'
                        ? 'bg-violet-500/60'
                        : span.span_type === 'TOOL'
                          ? 'bg-cyan-500/60'
                          : span.span_type === 'AGENT'
                            ? 'bg-blue-500/60'
                            : 'bg-amber-500/40';
                    return (
                      <div
                        key={`${span.name}-${span.span_type}-${span.duration_ms}`}
                        className={cn('h-full rounded-sm', bgColor)}
                        style={{ width: `${pct}%` }}
                        title={`${span.name} (${span.span_type}) — ${span.duration_ms}ms`}
                      />
                    );
                  })}
              </div>
              <div className="flex items-center gap-3 mt-0.5 text-[10px] text-muted-foreground/50">
                <span className="flex items-center gap-0.5">
                  <span className="h-1.5 w-1.5 rounded-sm bg-violet-500/60" /> LLM
                </span>
                <span className="flex items-center gap-0.5">
                  <span className="h-1.5 w-1.5 rounded-sm bg-cyan-500/60" /> Tool
                </span>
                <span className="flex items-center gap-0.5">
                  <span className="h-1.5 w-1.5 rounded-sm bg-blue-500/60" /> Agent
                </span>
                <span className="flex items-center gap-0.5">
                  <span className="h-1.5 w-1.5 rounded-sm bg-amber-500/40" /> Chain
                </span>
              </div>
            </div>
          )}

          <div className="divide-y divide-border">
            {spans.map((span) => (
              <SpanRow key={`${span.name}-${span.span_type}-${span.duration_ms}`} span={span} />
            ))}
          </div>
        </div>
      )}
    </details>
  );
}

function SpanRow({ span }: { span: TraceSpan }) {
  const [open, setOpen] = useState(false);
  const style = SPAN_TYPE_STYLES[span.span_type] ?? { icon: '○', color: 'text-muted-foreground' };
  const hasDetail = span.inputs || span.outputs;
  const statusOk = span.status === 'OK' || span.status === 'UNSET';

  return (
    <div>
      <button
        type="button"
        onClick={() => hasDetail && setOpen(!open)}
        className={cn(
          'flex w-full items-center gap-2 px-2 py-1 text-left',
          hasDetail && 'hover:bg-muted/20 cursor-pointer',
          !hasDetail && 'cursor-default'
        )}
      >
        <span className={cn('shrink-0', style.color)}>{style.icon}</span>
        <span className="font-medium truncate flex-1">{span.name}</span>
        <span
          className={cn(
            'rounded px-1 py-px text-[10px] font-mono shrink-0',
            span.span_type === 'LLM'
              ? 'bg-violet-500/10 text-violet-600 dark:text-violet-400'
              : span.span_type === 'TOOL'
                ? 'bg-cyan-500/10 text-cyan-600 dark:text-cyan-400'
                : 'bg-muted text-muted-foreground'
          )}
        >
          {span.span_type}
        </span>
        <span className="tabular-nums text-muted-foreground shrink-0 w-14 text-right">
          {span.duration_ms > 1000 ? `${(span.duration_ms / 1000).toFixed(1)}s` : `${span.duration_ms}ms`}
        </span>
        {!statusOk && (
          <span className="text-red-500 shrink-0" title={span.status}>
            ✕
          </span>
        )}
        {hasDetail && <span className="text-muted-foreground/40 shrink-0 text-[10px]">{open ? '▼' : '▶'}</span>}
      </button>
      {open && hasDetail && (
        <div className="px-2 pb-1.5 pl-7 space-y-1">
          {span.inputs && (
            <div>
              <span className="font-semibold text-muted-foreground">Input: </span>
              <code className="text-[10px] text-foreground/70 break-all">{span.inputs}</code>
            </div>
          )}
          {span.outputs && (
            <div>
              <span className="font-semibold text-muted-foreground">Output: </span>
              <code className="text-[10px] text-foreground/70 break-all">{span.outputs}</code>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
