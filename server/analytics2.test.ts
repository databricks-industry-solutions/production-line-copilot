import { describe, it, expect, vi } from 'vitest';

vi.mock('node:fs', () => {
  return {
    default: {
      existsSync: vi.fn(),
      readFileSync: vi.fn(),
    },
    existsSync: vi.fn(),
    readFileSync: vi.fn(),
  };
});

import fs from 'node:fs';
import { executeAnalytics2Query } from './analytics2';

describe('executeAnalytics2Query contract', () => {
  it('returns {data: []} and 400 when queryKey missing', async () => {
    const res = await executeAnalytics2Query({
      queryKey: '',
      parameters: {},
      analyticsQuery: () => Promise.resolve([{ ok: true }]),
    });
    expect(res.status).toBe(400);
    expect(Array.isArray(res.body.data)).toBe(true);
    expect(res.body.data).toEqual([]);
    expect(res.body.error).toBeTruthy();
  });

  it('returns {data: []} and 404 when file missing', async () => {
    vi.mocked(fs.existsSync).mockReturnValue(false);
    const res = await executeAnalytics2Query({
      queryKey: 'machine_state',
      parameters: {},
      analyticsQuery: () => Promise.resolve([{ ok: true }]),
      cwd: '/tmp/project',
    });
    expect(res.status).toBe(404);
    expect(res.body.data).toEqual([]);
    expect(res.body.error).toMatch(/not found/i);
  });

  it('returns {data: []} and 200 when analytics returns non-array', async () => {
    vi.mocked(fs.existsSync).mockReturnValue(true);
    vi.mocked(fs.readFileSync).mockReturnValue('SELECT 1');
    const res = await executeAnalytics2Query({
      queryKey: 'machine_state',
      parameters: {},
      analyticsQuery: () => Promise.resolve({ not: 'an array' }),
      cwd: '/tmp/project',
    });
    expect(res.status).toBe(200);
    expect(Array.isArray(res.body.data)).toBe(true);
    expect(res.body.data).toEqual([]);
  });

  it('returns {data: [...]} and 200 on success', async () => {
    vi.mocked(fs.existsSync).mockReturnValue(true);
    vi.mocked(fs.readFileSync).mockReturnValue('SELECT * FROM main.line_manager_ai_kit.event_log');
    const rows = [{ a: 1 }, { a: 2 }];
    const res = await executeAnalytics2Query({
      queryKey: 'events',
      parameters: { shift_id: 'shift_1' },
      analyticsQuery: () => Promise.resolve(rows),
      cwd: '/tmp/project',
    });
    expect(res.status).toBe(200);
    expect(res.body.data).toEqual(rows);
  });

  it('returns {data: []} and 500 on analytics error', async () => {
    vi.mocked(fs.existsSync).mockReturnValue(true);
    vi.mocked(fs.readFileSync).mockReturnValue('SELECT 1');
    const res = await executeAnalytics2Query({
      queryKey: 'events',
      parameters: {},
      analyticsQuery: () => Promise.reject(new Error('boom')),
      cwd: '/tmp/project',
    });
    expect(res.status).toBe(500);
    expect(res.body.data).toEqual([]);
    expect(res.body.error).toBe('boom');
  });
});
