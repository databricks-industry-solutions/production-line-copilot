export type BadgeVariant = 'critical' | 'warning' | 'ok' | 'info' | 'neutral';

export function severityToVariant(severity: string): BadgeVariant {
  const s = severity?.toLowerCase();
  if (s === 'critical' || s === 'major') return 'critical';
  if (s === 'minor' || s === 'warning') return 'warning';
  if (s === 'info') return 'info';
  return 'neutral';
}
