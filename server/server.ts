/**
 * Node proxy for the Databricks App frontend.
 *
 * Invoked by `npm run dev` / app startup to expose `/api/*` routes that
 * forward to the local Python agent server (`AGENT_PORT`, default 8001).
 * Design choice: this layer keeps browser auth/cookies on the app side while
 * centralizing upstream timeouts and SSE backpressure handling in one place.
 * Side effect: process-level AbortError guards avoid Node 22 crashes on
 * disconnected SSE clients and aborted fetches.
 */
import { createApp, analytics, server } from '@databricks/appkit';
import express, { type Request, type Response } from 'express';

// Prevent AbortError from crashing the process — SSE client disconnects
// trigger fetch AbortErrors that can escape try/catch in Node.js 22.
// Use name-based check (not instanceof) because cross-realm DOMException fails instanceof.
process.on('unhandledRejection', (reason: unknown) => {
  if (reason && typeof reason === 'object' && (reason as { name?: string }).name === 'AbortError') return;
  console.error('Unhandled rejection:', reason);
});
process.on('uncaughtException', (err) => {
  if (err.name === 'AbortError') return;
  console.error('Uncaught exception:', err);
  process.exit(1);
});

const AGENT_PORT = process.env.AGENT_PORT || '8001';

type ProxyOptions = {
  method?: string;
  timeoutMs?: number;
  body?: unknown;
  headers?: Record<string, string>;
  /** When true (default), forward upstream HTTP status; otherwise always 200. */
  forwardStatus?: boolean;
  errorMessage?: string;
};

function getOpsUser(req: Request): string {
  return req.header('x-forwarded-email') ?? req.header('x-user-email') ?? req.header('x-databricks-user') ?? 'unknown';
}

type StreamChunkResult = {
  done: boolean;
  value?: Uint8Array;
};

async function proxyToAgent(res: Response, targetPath: string, opts: ProxyOptions = {}): Promise<void> {
  const {
    method = 'GET',
    timeoutMs = 30000,
    body,
    headers = {},
    forwardStatus = true,
    errorMessage = 'endpoint unavailable',
  } = opts;

  try {
    const fetchHeaders: Record<string, string> = { ...headers };
    const fetchOpts: RequestInit = {
      method,
      headers: fetchHeaders,
      signal: AbortSignal.timeout(timeoutMs),
    };
    if (body !== undefined) {
      fetchHeaders['Content-Type'] = 'application/json';
      fetchOpts.body = JSON.stringify(body);
    }

    const response = await fetch(`http://localhost:${AGENT_PORT}${targetPath}`, fetchOpts);

    // Read as text first and decide. Calling .json() unconditionally meant any
    // non-JSON upstream body — a uvicorn HTML 502, a truncated response, a
    // proxy error page — threw a parse error that the catch below turned into
    // "502: Unexpected token < in JSON", discarding the real status and the
    // real message. An operator debugging a 500 from /ops/control/action was
    // shown a 502 about JSON syntax.
    const text = await response.text();
    const contentType = response.headers.get('content-type') ?? '';
    const status = forwardStatus ? response.status : 200;

    if (contentType.includes('application/json')) {
      try {
        res.status(status).json(JSON.parse(text) as unknown);
        return;
      } catch {
        // Declared JSON, wasn't. Fall through and forward it verbatim.
      }
    }

    if (response.ok && !text) {
      res.status(status).json({});
      return;
    }
    // Forward the upstream status even when we cannot forward its shape, so
    // the caller sees 500 vs 404 vs 502 rather than a blanket gateway error.
    res.status(forwardStatus ? response.status : response.ok ? 200 : response.status)
      .json({ error: text || errorMessage, upstream_status: response.status });
  } catch (error: unknown) {
    const err = error as Error;
    res.status(502).json({ error: err?.message || errorMessage });
  }
}

createApp({
  plugins: [analytics(), server()],
  onPluginsReady(appkit) {
    appkit.server.extend((app) => {
      app.use(express.json());

      // ── Diagnostic: client lifecycle beacon ───────────────────────
      // Receives small JSON envelopes from client/src/main.tsx (page_load,
      // page_exit) and dumps them to stdout so `databricks apps logs` shows
      // them with the [client-beacon] prefix. Accepts both application/json
      // (default fetch path) and the navigator.sendBeacon Blob form via the
      // route-level text parser. No state, no behavior change. Remove once
      // the "blank page reload" root cause is identified.
      app.post('/api/_client_beacon', express.text({ type: '*/*', limit: '4kb' }), (req, res) => {
        let parsed: unknown = req.body;
        if (typeof parsed === 'string' && parsed.length > 0) {
          try {
            parsed = JSON.parse(parsed);
          } catch {
            /* keep raw string */
          }
        }
        const user =
          req.header('x-forwarded-email') ?? req.header('x-user-email') ?? req.header('x-databricks-user') ?? null;
        const enriched = parsed && typeof parsed === 'object' ? { user, ...parsed } : { user, raw: parsed };
        console.log(`[client-beacon] ${JSON.stringify(enriched)}`);
        res.status(204).end();
      });

      app.get('/api/debug/sql', async (_req, res) => {
        await proxyToAgent(res, '/debug/sql', {
          timeoutMs: 30000,
          forwardStatus: false,
          errorMessage: 'debug endpoint unavailable',
        });
      });

      app.get('/api/ops/cost-summary', async (req, res) => {
        const hoursRaw = Array.isArray(req.query.hours) ? req.query.hours[0] : req.query.hours;
        const hoursNum = Number(hoursRaw ?? 24);
        // why: cap to 14 days to bound warehouse query cost/latency.
        const hours = Number.isFinite(hoursNum) ? Math.max(1, Math.min(24 * 14, Math.trunc(hoursNum))) : 24;
        const includeDetailsRaw = Array.isArray(req.query.include_details)
          ? req.query.include_details[0]
          : req.query.include_details;
        const includeDetails =
          (typeof includeDetailsRaw === 'string' ? includeDetailsRaw : 'false').toLowerCase() === 'true';
        await proxyToAgent(res, `/ops/cost-summary?hours=${hours}&include_details=${includeDetails}`, {
          timeoutMs: 120000,
          errorMessage: 'ops endpoint unavailable',
        });
      });

      app.get('/api/ops/cost-live', async (req, res) => {
        const minutesRaw = Array.isArray(req.query.minutes) ? req.query.minutes[0] : req.query.minutes;
        const minutesNum = Number(minutesRaw ?? 60);
        // why: keep payloads small for near-real-time polling callers.
        const minutes = Number.isFinite(minutesNum) ? Math.max(1, Math.min(360, Math.trunc(minutesNum))) : 60;
        await proxyToAgent(res, `/ops/cost-live?minutes=${minutes}`, {
          timeoutMs: 15000,
          errorMessage: 'ops live endpoint unavailable',
        });
      });

      app.get('/api/ops/dashboard-info', async (_req, res) => {
        await proxyToAgent(res, '/ops/dashboard-info', {
          timeoutMs: 15000,
          errorMessage: 'dashboard-info unavailable',
        });
      });

      app.get('/api/ops/reconciliation', async (req, res) => {
        const windowRaw = req.query.window_minutes;
        const windowNum = Number(windowRaw ?? 15);
        // why: bound the scan so a stray query string can't launch a full-history
        // warehouse scan on every poll.
        const windowMinutes = Number.isFinite(windowNum)
          ? Math.max(1, Math.min(1440, Math.trunc(windowNum)))
          : 15;
        await proxyToAgent(res, `/ops/reconciliation?window_minutes=${windowMinutes}`, {
          // Fans out to several warehouse queries plus Lakebase; a cold warehouse
          // can take a while to wake.
          timeoutMs: 60000,
          errorMessage: 'reconciliation unavailable',
        });
      });

      app.get('/api/ops/models', async (_req, res) => {
        await proxyToAgent(res, '/ops/models', {
          // First call may list the workspace's serving endpoints; the agent
          // caches it for the life of the process, so only this one is slow.
          timeoutMs: 30000,
          errorMessage: 'model roster unavailable',
        });
      });

      app.get('/api/ops/topology', async (_req, res) => {
        await proxyToAgent(res, '/ops/topology', {
          // Reads env only — no workspace calls, so this stays fast.
          timeoutMs: 10000,
          errorMessage: 'system topology unavailable',
        });
      });

      app.get('/api/ops/control/state', async (req, res) => {
        await proxyToAgent(res, '/ops/control/state', {
          timeoutMs: 120000,
          headers: { 'x-ops-user': getOpsUser(req) },
          errorMessage: 'ops control state unavailable',
        });
      });

      app.post('/api/ops/control/action', async (req, res) => {
        await proxyToAgent(res, '/ops/control/action', {
          method: 'POST',
          timeoutMs: 120000,
          body: req.body ?? {},
          headers: { 'x-ops-user': getOpsUser(req) },
          errorMessage: 'ops control action unavailable',
        });
      });

      app.post('/api/agent', async (req, res) => {
        try {
          const agentUrl = `http://localhost:${AGENT_PORT}/invocations`;
          const payload = JSON.stringify(req.body ?? {});

          const response = await fetch(agentUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: payload,
            signal: AbortSignal.timeout(180000),
          });

          if (!response.ok) {
            const errorText = await response.text();
            console.error('[agent-proxy] Agent returned', response.status, errorText);
            res.status(response.status).json({ error: errorText });
            return;
          }

          const data: unknown = await response.json();
          res.json(data);
        } catch (error: unknown) {
          const err = error as Error;
          console.error('[agent-proxy] Error:', error);
          res.status(502).json({
            error: 'Agent server unavailable',
            detail: err?.message || 'Unknown error',
          });
        }
      });

      app.post('/api/agent/stream', async (req, res) => {
        req.setTimeout(0);
        res.setTimeout(0);
        try {
          const agentUrl = `http://localhost:${AGENT_PORT}/invocations/stream`;
          const payload = JSON.stringify(req.body ?? {});

          const response = await fetch(agentUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: payload,
            signal: AbortSignal.timeout(180000),
          });

          if (!response.ok) {
            const errorText = await response.text();
            console.error('[agent-stream] Agent returned', response.status, errorText);
            res.status(response.status).json({ error: errorText });
            return;
          }

          res.writeHead(200, {
            'Content-Type': 'text/event-stream',
            'Cache-Control': 'no-cache',
            Connection: 'keep-alive',
            'X-Accel-Buffering': 'no',
          });

          const reader = response.body?.getReader();
          if (!reader) {
            res.end();
            return;
          }

          const decoder = new TextDecoder();
          try {
            while (true) {
              const result = (await reader.read()) as StreamChunkResult;
              if (result.done) break;
              const chunk = result.value;
              if (chunk instanceof Uint8Array) {
                res.write(decoder.decode(chunk, { stream: true }));
              }
            }
          } catch {
            // AbortError or client disconnect — expected
          } finally {
            await reader.cancel().catch(() => {});
            if (!res.writableEnded) res.end();
          }
        } catch (error: unknown) {
          const err = error as Error;
          console.error('[agent-stream] Error:', error);
          if (!res.headersSent) {
            res.status(502).json({
              error: 'Agent server unavailable',
              detail: err?.message || 'Unknown error',
            });
          } else if (!res.writableEnded) {
            res.end();
          }
        }
      });
      // ── SSE serving stream proxy ──────────────────────────────────
      // No AbortController — client disconnect is detected via res.destroyed.
      // AbortController causes unhandled rejections in Node.js 22.
      app.get('/api/serving/stream', async (_req, res) => {
        _req.setTimeout(0);
        res.setTimeout(0);
        if (_req.socket) _req.socket.setKeepAlive(true, 30_000);

        try {
          const response = await fetch(`http://localhost:${AGENT_PORT}/serving/stream`);

          if (!response.ok) {
            res.status(502).json({ error: 'Serving stream unavailable' });
            return;
          }

          res.writeHead(200, {
            'Content-Type': 'text/event-stream',
            'Cache-Control': 'no-cache',
            Connection: 'keep-alive',
            'X-Accel-Buffering': 'no',
          });

          const reader = response.body?.getReader();
          if (!reader) {
            res.end();
            return;
          }

          const decoder = new TextDecoder();

          try {
            while (true) {
              const result = (await reader.read()) as StreamChunkResult;
              if (result.done || res.destroyed) break;
              const chunk = result.value;
              if (chunk instanceof Uint8Array && !res.write(decoder.decode(chunk, { stream: true }))) {
                await new Promise<void>((r) => res.once('drain', r));
              }
            }
          } catch {
            // stream read errors expected on upstream close
          } finally {
            await reader.cancel().catch(() => {});
            if (!res.writableEnded) res.end();
          }
        } catch {
          if (!res.headersSent) {
            res.status(502).json({ error: 'Serving stream unavailable' });
          } else if (!res.writableEnded) {
            res.end();
          }
        }
      });

      // ── Warmup proxy ─────────────────────────────────────────────
      // Side-effect-free cold-start primer used by the Admin page
      // before a demo. Forwards user identity so the agent can audit
      // the call and gate it on operator/admin role. Timeout matches
      // the worst case where the SQL warehouse needs to fully cold-
      // start from STOPPED (≈30s) plus Lakebase OAuth round-trip.
      app.post('/api/sim/warmup', async (req, res) => {
        await proxyToAgent(res, '/sim/warmup', {
          method: 'POST',
          timeoutMs: 120000,
          headers: { 'x-ops-user': getOpsUser(req) },
          errorMessage: 'warmup unavailable',
        });
      });

      // ── Incident management proxy ────────────────────────────────
      app.post('/api/sim/incident', async (req, res) => {
        await proxyToAgent(res, '/sim/incident', {
          method: 'POST',
          timeoutMs: 30000,
          body: req.body ?? {},
          errorMessage: 'sim endpoint unavailable',
        });
      });

      app.post('/api/sim/resolve', async (req, res) => {
        await proxyToAgent(res, '/sim/resolve', {
          method: 'POST',
          timeoutMs: 30000,
          body: req.body ?? {},
          errorMessage: 'sim endpoint unavailable',
        });
      });

      // Resolve every active incident on a machine — used by the EventFeed's
      // per-row Recover button when the operator doesn't have a command_id.
      app.post('/api/sim/recover_machine', async (req, res) => {
        await proxyToAgent(res, '/sim/recover_machine', {
          method: 'POST',
          timeoutMs: 30000,
          body: req.body ?? {},
          errorMessage: 'sim endpoint unavailable',
        });
      });

      // Restart / stop / resume now touch the Databricks Jobs API in
      // producer mode (cancel + run_now / pause / unpause the producer job)
      // — that path is non-blocking on the SDK side but a slow workspace
      // can still take 20-30 s. 60 s gives plenty of headroom and matches
      // the cost-summary timeout philosophy.
      app.post('/api/sim/restart', async (_req, res) => {
        await proxyToAgent(res, '/sim/restart', {
          method: 'POST',
          timeoutMs: 60000,
          forwardStatus: false,
          errorMessage: 'restart unavailable',
        });
      });

      app.post('/api/sim/stop', async (_req, res) => {
        await proxyToAgent(res, '/sim/stop', {
          method: 'POST',
          timeoutMs: 60000,
          forwardStatus: false,
          errorMessage: 'stop unavailable',
        });
      });

      app.post('/api/sim/resume', async (_req, res) => {
        await proxyToAgent(res, '/sim/resume', {
          method: 'POST',
          timeoutMs: 60000,
          forwardStatus: false,
          errorMessage: 'resume unavailable',
        });
      });

      app.get('/api/sim/status', async (_req, res) => {
        await proxyToAgent(res, '/sim/status', {
          timeoutMs: 10000,
          forwardStatus: false,
          errorMessage: 'sim status unavailable',
        });
      });

      app.get('/api/sim/commands', async (_req, res) => {
        await proxyToAgent(res, '/sim/commands', {
          timeoutMs: 30000,
          forwardStatus: false,
          errorMessage: 'sim endpoint unavailable',
        });
      });
    });
  },
}).catch(console.error);
