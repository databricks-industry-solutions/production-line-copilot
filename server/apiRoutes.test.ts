/**
 * Every `/api/...` path the client fetches must be registered on the Express
 * server.
 *
 * There is no type link between the two sides: the client fetches a string
 * literal, and if no route matches, Express answers its own 404 HTML. Callers
 * that treat a failed fetch as "no data yet" then render an empty state
 * forever, which looks like a slow backend rather than a missing route. That is
 * exactly how `/api/ops/models` shipped unproxied — the panel returned null on
 * error, so a 404 was indistinguishable from a roster that hadn't loaded.
 *
 * This walks the client source for fetch targets and asserts each one is
 * declared in the server, so the next unproxied path fails here instead of in
 * front of a customer.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

const CLIENT_SRC = join(__dirname, '..', 'client', 'src');
const SERVER_FILE = join(__dirname, 'server.ts');

function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      out.push(...sourceFiles(full));
    } else if (/\.tsx?$/.test(entry) && !entry.endsWith('.test.ts') && !entry.endsWith('.test.tsx')) {
      out.push(full);
    }
  }
  return out;
}

/**
 * Paths the client asks for, stripped of query strings and interpolation.
 *
 * Any `/api/...` string literal counts, not only one sitting directly inside
 * `fetch(`. Matching on `fetch(` was how this test came to miss the very path
 * it names in its own docstring: `/api/ops/models` and `/api/ops/topology` go
 * through the shared `fetchOpsJson()` helper, and `/api/serving/stream` is
 * opened with `new EventSource()`. All three were invisible here, so the guard
 * quietly stopped covering every endpoint added this release.
 *
 * The cost of the wider net is the odd path mentioned in a comment. That is
 * the right trade: a documented endpoint should be routed too, and a false
 * positive fails loudly where a false negative fails silently.
 */
function clientApiPaths(): Map<string, string[]> {
  const found = new Map<string, string[]>();
  for (const file of sourceFiles(CLIENT_SRC)) {
    const text = readFileSync(file, 'utf8');
    for (const match of text.matchAll(/['"`](\/api\/[^'"`?\s)]*)/g)) {
      const path = match[1].replace(/\$\{[^}]*\}/g, '').replace(/\/+$/, '');
      // A prose wildcard (`/api/ops/*`) names a family, not an endpoint.
      if (path.includes('*')) continue;
      if (!found.has(path)) found.set(path, []);
      found.get(path)!.push(file.slice(CLIENT_SRC.length + 1));
    }
  }
  return found;
}

function serverRoutes(): Set<string> {
  const text = readFileSync(SERVER_FILE, 'utf8');
  const routes = new Set<string>();
  for (const match of text.matchAll(/app\.(get|post|put|delete)\(\s*['"`](\/api\/[^'"`]*)/g)) {
    routes.add(match[2]);
  }
  return routes;
}

describe('client fetch targets are served', () => {
  const paths = clientApiPaths();
  const routes = serverRoutes();

  it('finds the routes it is meant to be checking', () => {
    // Guards the regexes themselves: if either stops matching, every assertion
    // below would pass vacuously.
    expect(paths.size).toBeGreaterThan(5);
    expect(routes.size).toBeGreaterThan(5);
  });

  it.each([
    // Named individually because a count cannot tell you which path fell out.
    // Each of these reaches the server through something other than a bare
    // `fetch(` call, which is how all three escaped this test once already.
    ['/api/ops/models', 'fetchOpsJson helper'],
    ['/api/ops/topology', 'fetchOpsJson helper'],
    ['/api/serving/stream', 'EventSource'],
    ['/api/agent/stream', 'fetch'],
  ])('still sees %s (reached via %s)', (path) => {
    expect(
      paths.has(path),
      `the extractor no longer finds ${path}. It is still called from the client, so ` +
        'this test has stopped covering it — widen the regex rather than deleting the case.',
    ).toBe(true);
  });

  it.each([...clientApiPaths().keys()].sort())('%s is registered on the server', (path) => {
    expect(routes.has(path), `no Express route for ${path} (fetched by ${paths.get(path)?.join(', ')})`).toBe(
      true,
    );
  });
});
