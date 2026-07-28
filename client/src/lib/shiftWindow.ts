/**
 * Client-side shift-window math, kept in lock-step with the streaming producer
 * (`src/streaming_sim/producer.py:_shift_window_start`): fixed 8-hour shifts
 * aligned to UTC 00:00 / 08:00 / 16:00. Because the boundaries are
 * deterministic we can derive "where are we in the shift" purely from the
 * wall-clock — no extra server round-trip — and it matches the boundary at
 * which the producer resets `cumulative_cases`, so the Cases tile and the shift
 * clock stay consistent.
 */

/** Shift length in minutes — matches producer SHIFT_WINDOW_MINUTES. */
export const SHIFT_WINDOW_MINUTES = 480;

/** Line ideal output: CASE_PACKER runs at 420 bottles/min ÷ 24 per case. */
export const IDEAL_CASES_PER_MIN = 17.5;

/** Per-shift plan target = ideal rate × shift length = 8,400 cases.
 *  Equals `production_schedule.planned_cases` for the live LAGER shift. */
export const SHIFT_PLANNED_CASES = Math.round(IDEAL_CASES_PER_MIN * SHIFT_WINDOW_MINUTES);

export interface ShiftProgress {
  startMs: number;
  endMs: number;
  elapsedMin: number;
  remainingMin: number;
  /** 0..1 fraction of the shift elapsed. */
  fraction: number;
}

/** Current position within the UTC-aligned 8h shift window. */
export function shiftProgress(nowMs: number = Date.now()): ShiftProgress {
  const d = new Date(nowMs);
  const startHour = Math.floor(d.getUTCHours() / 8) * 8;
  const startMs = Date.UTC(
    d.getUTCFullYear(),
    d.getUTCMonth(),
    d.getUTCDate(),
    startHour,
  );
  const endMs = startMs + SHIFT_WINDOW_MINUTES * 60_000;
  const elapsedMin = Math.min(SHIFT_WINDOW_MINUTES, Math.max(0, (nowMs - startMs) / 60_000));
  const remainingMin = Math.min(SHIFT_WINDOW_MINUTES, Math.max(0, (endMs - nowMs) / 60_000));
  return {
    startMs,
    endMs,
    elapsedMin,
    remainingMin,
    fraction: elapsedMin / SHIFT_WINDOW_MINUTES,
  };
}

/** Compact duration label, e.g. 200 → "3h 20m", 45 → "45m". */
export function formatDurationMin(totalMin: number): string {
  const m = Math.max(0, Math.round(totalMin));
  const h = Math.floor(m / 60);
  const rem = m % 60;
  if (h === 0) return `${rem}m`;
  return `${h}h ${rem.toString().padStart(2, '0')}m`;
}
