/**
 * Shared semantic badge primitive for severity and status labels.
 * Used by event, incident, and machine-detail components for consistent color/icon language.
 * Keeps visual variants centralized so severity mapping changes stay non-breaking for consumers.
 */
import { cn } from '../lib/utils';
import type { BadgeVariant } from '../lib/statusBadgeUtils';

const VARIANT_STYLES: Record<BadgeVariant, string> = {
  critical: 'bg-red-500/15 text-red-700 dark:text-red-400 border-red-500/30',
  warning: 'bg-yellow-500/15 text-yellow-700 dark:text-yellow-400 border-yellow-500/30',
  ok: 'bg-green-500/15 text-green-700 dark:text-green-400 border-green-500/30',
  info: 'bg-blue-500/15 text-blue-700 dark:text-blue-400 border-blue-500/30',
  neutral: 'bg-muted text-muted-foreground border-border',
};

const DOT_COLORS: Record<BadgeVariant, string> = {
  critical: 'bg-red-500',
  warning: 'bg-yellow-500',
  ok: 'bg-green-500',
  info: 'bg-blue-500',
  neutral: 'bg-muted-foreground',
};

const VARIANT_ICONS: Record<BadgeVariant, string> = {
  critical: '✕',
  warning: '⚠',
  ok: '✓',
  info: 'ℹ',
  neutral: '—',
};

interface StatusBadgeProps {
  variant: BadgeVariant;
  children: React.ReactNode;
  dot?: boolean;
  pulse?: boolean;
  className?: string;
}

export default function StatusBadge({ variant, children, dot = false, pulse = false, className }: StatusBadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium',
        VARIANT_STYLES[variant],
        className
      )}
    >
      <span className="shrink-0" aria-hidden>
        {VARIANT_ICONS[variant]}
      </span>
      {dot && (
        <span className={cn('h-1.5 w-1.5 rounded-full shrink-0', DOT_COLORS[variant], pulse && 'animate-pulse')} />
      )}
      {children}
    </span>
  );
}
