/**
 * The shared chokepoint for every `/api/ops/*` read.
 *
 * It exists because a missing Express route returns the Node server's own 404
 * HTML, and `res.json()` on that throws "Unexpected token '<'" — a message that
 * describes the parser's problem rather than the deployment's. That is not
 * hypothetical: `/api/ops/models` shipped unproxied, and the opaque parse error
 * was what the browser console showed.
 *
 * So the contract worth pinning is not "it parses JSON" but "every failure
 * names something an operator can act on, and none of them throw".
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

import { fetchOpsJson } from './opsFetch';

function respondWith(body: string, status = 200) {
  vi.stubGlobal(
    'fetch',
    vi.fn(() =>
      Promise.resolve({
        ok: status >= 200 && status < 300,
        status,
        text: () => Promise.resolve(body),
      }),
    ),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('fetchOpsJson', () => {
  it('returns the parsed body when the route answers', async () => {
    respondWith(JSON.stringify({ configured: 4 }));
    const { data, error } = await fetchOpsJson<{ configured: number }>('/api/ops/topology');
    expect(error).toBeNull();
    expect(data).toEqual({ configured: 4 });
  });

  it('names the unregistered route on a 404 of HTML', async () => {
    // The original bug. The message has to say "route", because the fix is a
    // line in server.ts and nothing about a parse error suggests that.
    respondWith('<!DOCTYPE html><title>Error</title>', 404);
    const { data, error } = await fetchOpsJson('/api/ops/models');
    expect(data).toBeNull();
    expect(error).toContain('/api/ops/models');
    expect(error).toContain('not registered');
  });

  it('handles HTML served with a 200, which is what an SPA fallback does', async () => {
    // Databricks Apps serves index.html for unknown paths rather than a 404, so
    // the status alone cannot be trusted to tell us the route is missing.
    respondWith('<!DOCTYPE html><div id="root">', 200);
    const { data, error } = await fetchOpsJson('/api/ops/topology');
    expect(data).toBeNull();
    expect(error).toContain('non-JSON');
  });

  it('passes a server-reported error through verbatim', async () => {
    respondWith(JSON.stringify({ error: 'SQL warehouse is stopped' }));
    const { data, error } = await fetchOpsJson('/api/ops/models');
    expect(data).toBeNull();
    expect(error).toBe('SQL warehouse is stopped');
  });

  it('reports the status when a failure carries no message', async () => {
    respondWith(JSON.stringify({}), 503);
    expect((await fetchOpsJson('/api/ops/models')).error).toBe('HTTP 503');
  });

  it('does not throw on a JSON body of null', async () => {
    // `JSON.parse("null")` succeeds, so reading `.error` off the result was a
    // TypeError raised from inside the helper written to prevent exactly that
    // class of message reaching the user.
    respondWith('null');
    const { data, error } = await fetchOpsJson('/api/ops/topology');
    expect(data).toBeNull();
    expect(error).toContain('not an object');
  });

  it('does not throw on a bare JSON scalar', async () => {
    respondWith('"unexpected"');
    expect((await fetchOpsJson('/api/ops/topology')).error).toContain('not an object');
  });

  it('lets a network failure propagate to the caller', async () => {
    // The hooks catch this and render their own offline state; swallowing it
    // here would make a dead backend indistinguishable from an empty one.
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))),
    );
    await expect(fetchOpsJson('/api/ops/topology')).rejects.toThrow('Failed to fetch');
  });
});
