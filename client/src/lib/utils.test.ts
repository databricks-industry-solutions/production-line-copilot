import { describe, it, expect } from 'vitest';
import { toNumber, formatPercent, formatPct, formatTimestamp, isoToEpoch } from './utils';

describe('toNumber', () => {
  it('returns number for number input', () => {
    expect(toNumber(42)).toBe(42);
    expect(toNumber(0)).toBe(0);
    expect(toNumber(-5.5)).toBe(-5.5);
  });

  it('parses string to number', () => {
    expect(toNumber('42')).toBe(42);
    expect(toNumber('3.14')).toBe(3.14);
    expect(toNumber('0')).toBe(0);
  });

  it('returns 0 for non-numeric string', () => {
    expect(toNumber('abc')).toBe(0);
    expect(toNumber('')).toBe(0);
  });

  it('returns 0 for null/undefined', () => {
    expect(toNumber(null)).toBe(0);
    expect(toNumber(undefined)).toBe(0);
  });
});

describe('formatPercent', () => {
  it('formats fraction as percentage', () => {
    expect(formatPercent(0.85)).toBe('85.0%');
    expect(formatPercent(1)).toBe('100.0%');
    expect(formatPercent(0)).toBe('0.0%');
  });

  it('respects decimal places', () => {
    expect(formatPercent(0.8567, 2)).toBe('85.67%');
    expect(formatPercent(0.85, 0)).toBe('85%');
  });
});

describe('formatPct', () => {
  it('formats 0-100 scale value', () => {
    expect(formatPct(76.5)).toBe('76.5%');
    expect(formatPct(100)).toBe('100.0%');
    expect(formatPct(0)).toBe('0.0%');
  });

  it('respects decimal places', () => {
    expect(formatPct(76.54, 2)).toBe('76.54%');
    expect(formatPct(76.5, 0)).toBe('77%');
  });
});

describe('isoToEpoch', () => {
  it('reads a zone-qualified backend timestamp', () => {
    // The exact shape the serving layer sends.
    expect(isoToEpoch('2026-08-06 13:57:24.899302+00:00'))
      .toBe(Date.UTC(2026, 7, 6, 13, 57, 24, 899));
  });

  it('agrees with shiftWindow, which does its arithmetic in UTC', () => {
    expect(isoToEpoch('2026-08-06T06:00:00Z')).toBe(Date.UTC(2026, 7, 6, 6, 0, 0));
  });

  it('yields NaN for junk rather than a plausible wrong instant', () => {
    expect(Number.isNaN(isoToEpoch('not a date'))).toBe(true);
  });
});

describe('formatTimestamp', () => {
  it('formats ISO to time string', () => {
    const result = formatTimestamp('2025-01-01T14:30:00.000Z');
    // Should contain hours and minutes
    expect(result).toMatch(/\d{2}:\d{2}/);
  });
});
