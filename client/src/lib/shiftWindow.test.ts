import { describe, it, expect } from 'vitest';
import {
  SHIFT_WINDOW_MINUTES,
  SHIFT_PLANNED_CASES,
  IDEAL_CASES_PER_MIN,
  shiftProgress,
  formatDurationMin,
} from './shiftWindow';

describe('shift-window constants', () => {
  it('plan target = ideal rate x shift length (matches producer 8,400)', () => {
    expect(SHIFT_WINDOW_MINUTES).toBe(480);
    expect(IDEAL_CASES_PER_MIN).toBe(17.5);
    expect(SHIFT_PLANNED_CASES).toBe(8400);
  });
});

describe('shiftProgress', () => {
  it('aligns to UTC 00/08/16 boundaries', () => {
    // 2026-07-28T11:30:00Z → 08:00 window, 3h30m elapsed, 4h30m left.
    const now = Date.UTC(2026, 6, 28, 11, 30);
    const p = shiftProgress(now);
    expect(new Date(p.startMs).getUTCHours()).toBe(8);
    expect(new Date(p.endMs).getUTCHours()).toBe(16);
    expect(Math.round(p.elapsedMin)).toBe(210);
    expect(Math.round(p.remainingMin)).toBe(270);
    expect(p.fraction).toBeCloseTo(210 / 480, 5);
  });

  it('is 0 elapsed exactly at a boundary', () => {
    const p = shiftProgress(Date.UTC(2026, 6, 28, 16, 0));
    expect(p.elapsedMin).toBe(0);
    expect(p.remainingMin).toBe(480);
    expect(p.fraction).toBe(0);
  });

  it('clamps within the window', () => {
    const p = shiftProgress(Date.UTC(2026, 6, 28, 7, 59, 59));
    expect(p.elapsedMin).toBeGreaterThanOrEqual(0);
    expect(p.elapsedMin).toBeLessThanOrEqual(SHIFT_WINDOW_MINUTES);
    expect(p.remainingMin).toBeGreaterThanOrEqual(0);
  });
});

describe('formatDurationMin', () => {
  it('formats hours and minutes', () => {
    expect(formatDurationMin(210)).toBe('3h 30m');
    expect(formatDurationMin(45)).toBe('45m');
    expect(formatDurationMin(0)).toBe('0m');
    expect(formatDurationMin(480)).toBe('8h 00m');
  });
});
