/**
 * Shows the actions a specialist recommends for the current incident.
 *
 * These are PROPOSALS. Nothing here writes to a CMMS, SAP or a scheduling
 * system — see docs/design/write-back-and-human-approval.md, which specifies
 * the approval and relay path but is explicitly unimplemented. The action ids
 * the server emits (`create_ticket`, `dispatch_tech`, …) have no corresponding
 * endpoint; the one action route that exists, `/ops/control/action`, drives
 * demo infrastructure and shares no vocabulary with these.
 *
 * The panel used to claim otherwise. It said "Click to execute", and clicking
 * ran an optimistic status transition to a green ✅ "done" — a state that meant
 * nothing had happened, was lost on the next re-render, and told the operator a
 * ticket existed when none did. In a plant context that is worse than a missing
 * feature: someone stops chasing the work because the screen says it is handled.
 *
 * Removing the buttons fixed the lie and left a flat list nobody could act on,
 * which is its own failure: the panel told the operator to hand these to the
 * responsible system and then made them retype it. So the buttons are back and
 * they do the thing the panel actually asks for — one click copies a
 * ready-to-paste handoff for the target system. The claim and the effect now
 * match, and no row ever reports work as complete.
 *
 * Pass `onActionClick` and the execution path takes over instead, ready for the
 * connector when it is built.
 */
import { useState, useEffect, useRef } from 'react';
import { cn } from '../lib/utils';

export interface ActionItem {
  id: string;
  title: string;
  description: string;
  icon: string;
  system: string;
  status: 'pending' | 'in_progress' | 'done';
}

interface Props {
  actions: ActionItem[];
  onActionClick?: (action: ActionItem) => void;
  /** Specialist that proposed these, recorded in the copied handoff so the
   *  receiving system keeps the provenance. */
  source?: string;
}

const ICON_MAP: Record<string, string> = {
  ticket: '🎫',
  person: '👤',
  inventory: '📦',
  calendar: '📅',
  shield: '🛡️',
  science: '🔬',
  document: '📄',
  truck: '🚚',
  search: '🔍',
  swap: '🔄',
  chart: '📊',
  speed: '⚡',
  list: '📋',
  school: '🎓',
  wrench: '🔧',
  sensor: '📡',
  sort: '↕️',
  clock: '⏰',
  check: '✓',
};

const STATUS_STYLE: Record<string, string> = {
  pending: 'border-border bg-card hover:bg-accent/50 hover:border-primary/40',
  in_progress: 'border-blue-400 bg-blue-50 dark:bg-blue-900/20',
  done: 'border-green-400 bg-green-50 dark:bg-green-900/20 opacity-70',
};

/** The text one click puts on the clipboard.
 *
 * Everything the receiving system needs to raise the item without going back to
 * the chat, and an explicit note that a human still owns the decision. */
export function buildHandoff(action: ActionItem, source?: string, now: Date = new Date()): string {
  return [
    action.title,
    `System:      ${action.system}`,
    `Action:      ${action.description}`,
    `Proposed by: ProdLine CoPilot${source ? ` — ${source}` : ''}`,
    `Proposed at: ${now.toISOString().replace('T', ' ').slice(0, 16)} UTC`,
    'Status:      proposal — requires human approval, not sent to any system',
  ].join('\n');
}

export default function ActionPanel({ actions, onActionClick, source }: Props) {
  const [localActions, setLocalActions] = useState<ActionItem[]>(actions);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [failedId, setFailedId] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const interactive = Boolean(onActionClick);

  useEffect(() => {
    setLocalActions(actions);
  }, [actions]);

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  const flash = (id: string, ok: boolean) => {
    setCopiedId(ok ? id : null);
    setFailedId(ok ? null : id);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      setCopiedId(null);
      setFailedId(null);
    }, 2000);
  };

  const handleExecute = (action: ActionItem) => {
    if (!onActionClick || action.status === 'done') return;
    setLocalActions((prev) =>
      prev.map((a) =>
        a.id === action.id ? { ...a, status: a.status === 'pending' ? 'in_progress' : 'done' } : a
      )
    );
    onActionClick(action);
  };

  const handleCopy = async (action: ActionItem) => {
    try {
      await navigator.clipboard.writeText(buildHandoff(action, source));
      flash(action.id, true);
    } catch {
      // Clipboard is permission-gated and unavailable over plain http. Saying so
      // beats a button that silently does nothing.
      flash(action.id, false);
    }
  };

  if (!localActions.length) return null;

  const pending = localActions.filter((a) => a.status !== 'done').length;

  return (
    <div className="rounded-lg border border-border bg-card mt-3">
      <div className="flex items-center justify-between px-3 py-2 border-b border-border">
        <div className="flex items-center gap-2">
          <h4 className="text-xs font-semibold text-foreground">Recommended Actions</h4>
          <span className="rounded-full bg-primary/10 text-primary px-1.5 py-px text-[10px] font-semibold">
            {interactive ? `${pending} pending` : `${localActions.length} proposed`}
          </span>
        </div>
        <span className="text-[10px] text-muted-foreground">
          {interactive ? 'Click to execute' : 'Proposed — not sent to any system'}
        </span>
      </div>
      <div className="p-2 grid gap-1.5">
        {localActions.map((action, index) => {
          const copied = copiedId === action.id;
          const failed = failedId === action.id;
          return (
            <button
              key={action.id}
              type="button"
              onClick={() => (interactive ? handleExecute(action) : void handleCopy(action))}
              title={
                interactive
                  ? undefined
                  : `Copy a ready-to-paste handoff for ${action.system}`
              }
              className={cn(
                'group flex items-start gap-2.5 rounded-md border px-3 py-2 text-left transition-all',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40',
                interactive
                  ? STATUS_STYLE[action.status]
                  : 'border-border bg-card hover:bg-accent/50 hover:border-primary/40 active:scale-[0.995]',
                interactive && action.status === 'done' ? 'cursor-default' : 'cursor-pointer',
                copied && 'border-green-400 bg-green-50 dark:bg-green-900/20'
              )}
            >
              <span className="flex flex-col items-center gap-0.5 shrink-0 mt-0.5">
                <span className="text-base leading-none">
                  {interactive && action.status === 'done'
                    ? '✅'
                    : interactive && action.status === 'in_progress'
                      ? '⏳'
                      : ICON_MAP[action.icon] || '▶'}
                </span>
                {/* The specialist returns these in priority order. */}
                <span className="text-[9px] font-semibold text-muted-foreground leading-none">
                  {index + 1}
                </span>
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-medium text-foreground">{action.title}</span>
                  <span className="rounded bg-muted px-1 py-px text-[9px] font-medium text-muted-foreground shrink-0">
                    {action.system}
                  </span>
                </div>
                <p className="text-[11px] text-muted-foreground mt-0.5 leading-tight">
                  {action.description}
                </p>
              </div>
              {!interactive && (
                <span
                  className={cn(
                    'shrink-0 self-center rounded px-1.5 py-0.5 text-[10px] font-medium transition-colors',
                    copied
                      ? 'text-green-700 dark:text-green-300'
                      : failed
                        ? 'text-amber-700 dark:text-amber-300'
                        : 'text-muted-foreground opacity-0 group-hover:opacity-100 group-focus-visible:opacity-100'
                  )}
                >
                  {copied ? 'Copied ✓' : failed ? 'Copy blocked' : 'Copy handoff'}
                </span>
              )}
            </button>
          );
        })}
      </div>
      {!interactive && (
        <p className="px-3 pb-2 text-[10px] leading-tight text-muted-foreground">
          Copy hands the item to the responsible system with its provenance. Automated
          write-back is designed but not implemented — a write is treated as a proposal a
          human approves, never an action the model takes.
        </p>
      )}
    </div>
  );
}
