/**
 * Shared fetch for the read-only `/api/ops/*` endpoints the context tabs use.
 *
 * The error handling is the reason this is shared rather than inlined. An
 * unproxied path returns the Node server's own 404 HTML, and calling
 * `res.json()` on it throws "Unexpected token '<'" — technically true and
 * useless to whoever has to fix it. Naming the real problem once means every
 * ops panel reports it the same way.
 */

export interface OpsResult<T> {
  data: T | null;
  error: string | null;
}

export async function fetchOpsJson<T>(path: string): Promise<OpsResult<T>> {
  const res = await fetch(path);
  const body = await res.text();

  let json: (T & { error?: string }) | null = null;
  try {
    json = JSON.parse(body) as T & { error?: string };
  } catch {
    return {
      data: null,
      error:
        res.status === 404
          ? `the ${path} route is not registered on the server`
          : `the server returned ${res.status} with a non-JSON body`,
    };
  }

  // `JSON.parse("null")` succeeds, and so does parsing a bare string or number.
  // Reading `.error` off any of those throws a TypeError from inside the very
  // helper that exists to stop callers seeing parser noise.
  if (json === null || typeof json !== 'object') {
    return { data: null, error: `the server returned ${res.status} with a JSON body that is not an object` };
  }

  if (!res.ok || json.error) {
    return {
      data: null,
      error: typeof json.error === 'string' ? json.error : `HTTP ${res.status}`,
    };
  }
  return { data: json, error: null };
}
