/**
 * Displays and locally progresses suggested follow-up actions from AI responses.
 * Used by `ChatPanel` for assistant messages that include executable action lists.
 * Maintains optimistic local status transitions so users get immediate feedback before backend wiring.
 */
import { useState, useEffect } from 'react';
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

export default function ActionPanel({ actions, onActionClick }: Props) {
  const [localActions, setLocalActions] = useState<ActionItem[]>(actions);

  useEffect(() => {
    setLocalActions(actions);
  }, [actions]);

  const handleClick = (action: ActionItem) => {
    if (action.status === 'done') return;

    setLocalActions((prev) =>
      prev.map((a) => (a.id === action.id ? { ...a, status: a.status === 'pending' ? 'in_progress' : 'done' } : a))
    );
    onActionClick?.(action);
  };

  if (!localActions.length) return null;

  const pending = localActions.filter((a) => a.status !== 'done').length;

  return (
    <div className="rounded-lg border border-border bg-card mt-3">
      <div className="flex items-center justify-between px-3 py-2 border-b border-border">
        <div className="flex items-center gap-2">
          <h4 className="text-xs font-semibold text-foreground">Recommended Actions</h4>
          {pending > 0 && (
            <span className="rounded-full bg-primary/10 text-primary px-1.5 py-px text-[10px] font-semibold">
              {pending} pending
            </span>
          )}
        </div>
        <span className="text-[10px] text-muted-foreground">Click to execute</span>
      </div>
      <div className="p-2 grid gap-1.5">
        {localActions.map((action) => (
          <button
            key={action.id}
            type="button"
            onClick={() => handleClick(action)}
            className={cn(
              'flex items-start gap-2.5 rounded-md border px-3 py-2 text-left transition-all',
              STATUS_STYLE[action.status],
              action.status === 'done' ? 'cursor-default' : 'cursor-pointer'
            )}
          >
            <span className="text-base mt-0.5 shrink-0">
              {action.status === 'done' ? '✅' : action.status === 'in_progress' ? '⏳' : ICON_MAP[action.icon] || '▶'}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="text-xs font-medium text-foreground">{action.title}</span>
                <span className="rounded bg-muted px-1 py-px text-[9px] font-medium text-muted-foreground shrink-0">
                  {action.system}
                </span>
              </div>
              <p className="text-[11px] text-muted-foreground mt-0.5 leading-tight">{action.description}</p>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
