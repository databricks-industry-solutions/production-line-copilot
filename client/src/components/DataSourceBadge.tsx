/**
 * Renders compact provenance badges for datasets powering a panel.
 * Used across KPI, chat, events, incidents, and inventory components.
 * Tooltips stay optional so callers can show source names without verbose metadata.
 */
import { cn } from '../lib/utils';

const SOURCE_STYLES: Record<string, { bg: string; text: string }> = {
  SCADA: { bg: 'bg-blue-500/10 border-blue-500/20', text: 'text-blue-600 dark:text-blue-400' },
  MES: { bg: 'bg-emerald-500/10 border-emerald-500/20', text: 'text-emerald-600 dark:text-emerald-400' },
  'SAP MM': { bg: 'bg-purple-500/10 border-purple-500/20', text: 'text-purple-600 dark:text-purple-400' },
  'SAP PP': { bg: 'bg-indigo-500/10 border-indigo-500/20', text: 'text-indigo-600 dark:text-indigo-400' },
  LIMS: { bg: 'bg-orange-500/10 border-orange-500/20', text: 'text-orange-600 dark:text-orange-400' },
  Historian: { bg: 'bg-cyan-500/10 border-cyan-500/20', text: 'text-cyan-600 dark:text-cyan-400' },
  CMMS: { bg: 'bg-rose-500/10 border-rose-500/20', text: 'text-rose-600 dark:text-rose-400' },
  WMS: { bg: 'bg-amber-500/10 border-amber-500/20', text: 'text-amber-600 dark:text-amber-400' },
  PLC: { bg: 'bg-sky-500/10 border-sky-500/20', text: 'text-sky-600 dark:text-sky-400' },
};

const DEFAULT_STYLE = { bg: 'bg-muted border-border', text: 'text-muted-foreground' };

interface Props {
  sources: string[];
  className?: string;
  details?: Record<string, string>;
}

export default function DataSourceBadge({ sources, className, details }: Props) {
  const infoText = details
    ? sources
        .map((src) => (details[src] ? `${src}: ${details[src]}` : null))
        .filter(Boolean)
        .join('\n')
    : '';

  return (
    <div className={cn('flex items-center gap-1 flex-wrap', className)}>
      {sources.map((src) => {
        const s = SOURCE_STYLES[src] ?? DEFAULT_STYLE;
        const tip = details?.[src];
        return (
          <span
            key={src}
            className={cn(
              'inline-flex items-center rounded border px-1.5 py-px text-[9px] font-medium tracking-wide uppercase',
              s.bg,
              s.text
            )}
            title={tip}
            aria-label={tip ? `${src}: ${tip}` : src}
          >
            {src}
          </span>
        );
      })}
      {infoText && (
        <span
          className="inline-flex h-3.5 w-3.5 items-center justify-center rounded-full border border-border text-[9px] font-semibold text-muted-foreground"
          title={infoText}
          aria-label={infoText}
        >
          i
        </span>
      )}
    </div>
  );
}
