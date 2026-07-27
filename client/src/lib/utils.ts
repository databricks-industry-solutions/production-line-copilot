import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

/**
 * Shared client-side utility helpers for class merging, numeric coercion, and time formatting.
 * Used broadly by UI components to normalize mixed SQL/SSE payload types.
 * Timestamp helpers intentionally emit plain local ISO strings to match backend SQL string predicates.
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Safely coerce SQL numeric values (which may arrive as strings) to number. */
export function toNumber(val: unknown): number {
  if (typeof val === 'number') return Number.isFinite(val) ? val : 0;
  if (typeof val === 'string') {
    const n = Number(val);
    return Number.isFinite(n) ? n : 0;
  }
  return 0;
}

/** Format a 0-1 fraction as percentage (e.g. 0.85 → "85.0%"). */
export function formatPercent(val: number, decimals = 1): string {
  if (!Number.isFinite(val)) return '—%';
  return `${(val * 100).toFixed(decimals)}%`;
}

/** Format a value that's already in 0-100 scale (e.g. 76.5 → "76.5%"). Clamped to [0, 100]. */
export function formatPct(val: number, decimals = 1): string {
  if (!Number.isFinite(val)) return '—%';
  const clamped = Math.max(0, Math.min(val, 100));
  return `${clamped.toFixed(decimals)}%`;
}

export function formatTimestamp(iso: string | number): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export function isoToEpoch(iso: string): number {
  return new Date(iso).getTime();
}

/**
 * Convert epoch ms to local-time ISO string WITHOUT 'Z' suffix.
 * Data in UC uses plain ISO strings (e.g. '2025-01-01T10:00:00'),
 * so we must match that format for SQL string comparisons.
 */
export function epochToIso(epoch: number): string {
  const d = new Date(epoch);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
