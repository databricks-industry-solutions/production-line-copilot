/**
 * Provides guarded admin controls for the producer and setup/maintenance jobs.
 * Used by the `/admin` route in `App` for privileged operational actions.
 * Keeps destructive actions behind an explicit confirmation modal to reduce accidental clicks.
 */
import { useCallback, useEffect, useState } from 'react';

interface OpsResourceState {
  exists?: boolean;
  state?: string;
  pause_status?: string;
  active_runs?: number;
  job_id?: number;
  pipeline_id?: string;
  error?: string;
}

interface OpsControlStateResponse {
  user?: string;
  role?: string;
  can_control?: boolean;
  resources?: {
    producer?: OpsResourceState;
    setup_job?: OpsResourceState;
  };
  last_actions?: Record<
    string,
    { timestamp?: string; action?: string; user?: string; status?: string; message?: string }
  >;
  error?: string;
}

interface WarmupStep {
  name: string;
  ok: boolean;
  ms: number;
  error?: string;
}

interface WarmupResponse {
  ok?: boolean;
  total_ms?: number;
  steps?: WarmupStep[];
  error?: string;
}

function canAct(canControl: boolean | undefined, exists: boolean | undefined): boolean {
  return Boolean(canControl) && exists === true;
}

export default function AdminJobControls({ onBack }: { onBack: () => void }) {
  const [opsState, setOpsState] = useState<OpsControlStateResponse | null>(null);
  const [opsLoading, setOpsLoading] = useState(false);
  const [opsError, setOpsError] = useState<string | null>(null);
  const [opsMessage, setOpsMessage] = useState<string | null>(null);
  const [pendingConfirm, setPendingConfirm] = useState<{ target: string; action: string; label: string } | null>(null);
  const [warmupRunning, setWarmupRunning] = useState(false);
  const [warmup, setWarmup] = useState<WarmupResponse | null>(null);

  const fetchOpsState = useCallback(async () => {
    setOpsLoading(true);
    try {
      const res = await fetch('/api/ops/control/state');
      const data = (await res.json()) as OpsControlStateResponse;
      if (!res.ok || data.error) {
        setOpsError(data.error ?? `HTTP ${res.status}`);
        setOpsState(null);
      } else {
        setOpsError(null);
        setOpsState(data);
      }
    } catch (e) {
      setOpsError(e instanceof Error ? e.message : 'Ops control state unavailable');
      setOpsState(null);
    } finally {
      setOpsLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchOpsState();
  }, [fetchOpsState]);

  const runWarmup = useCallback(async () => {
    setWarmupRunning(true);
    setWarmup(null);
    try {
      const res = await fetch('/api/sim/warmup', { method: 'POST' });
      const data = (await res.json()) as WarmupResponse;
      setWarmup(data);
    } catch (e) {
      setWarmup({ ok: false, error: e instanceof Error ? e.message : 'warmup failed' });
    } finally {
      setWarmupRunning(false);
    }
  }, []);

  const runOpsAction = useCallback(
    async (target: string, action: string) => {
      setOpsMessage(null);
      try {
        const res = await fetch('/api/ops/control/action', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ target, action, confirm: true }),
        });
        const data = (await res.json()) as { error?: string; message?: string; status?: string };
        if (!res.ok || data.error) {
          setOpsMessage(`Action failed: ${data.error ?? `HTTP ${res.status}`}`);
        } else {
          setOpsMessage(data.message ?? `${target}:${action} submitted`);
        }
        await fetchOpsState();
      } catch (e) {
        setOpsMessage(`Action failed: ${e instanceof Error ? e.message : 'unknown error'}`);
      }
    },
    [fetchOpsState]
  );

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">Admin Controls</h2>
          <p className="text-xs text-muted-foreground">Operational controls are isolated to `/admin`.</p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => {
              void fetchOpsState();
            }}
            className="rounded border border-border px-2 py-1 text-[11px] font-medium hover:bg-muted"
          >
            Refresh state
          </button>
          <button
            type="button"
            onClick={onBack}
            className="rounded border border-border px-2 py-1 text-[11px] font-medium hover:bg-muted"
          >
            Back to app
          </button>
        </div>
      </div>

      <div className="rounded-lg border border-border bg-card p-4 space-y-2">
        <p className="text-xs text-muted-foreground">
          Role: {opsState?.role ?? 'unknown'} · User: {opsState?.user ?? 'unknown'} · Controls enabled:{' '}
          {opsState?.can_control ? 'yes' : 'no'}
        </p>
        {opsLoading && <p className="text-xs text-muted-foreground">Loading control state...</p>}
        {opsError && <p className="text-xs text-destructive">{opsError}</p>}
        {opsMessage && <p className="text-xs text-muted-foreground">{opsMessage}</p>}

        <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-xs">
          <div className="rounded border border-border p-2 space-y-1">
            <p className="font-medium">Producer</p>
            <p className="text-muted-foreground">
              state: {opsState?.resources?.producer?.state ?? '—'} · pause:{' '}
              {opsState?.resources?.producer?.pause_status ?? '—'}
            </p>
            {opsState?.resources?.producer?.error && (
              <p className="text-[11px] text-amber-600 dark:text-amber-400">{opsState.resources.producer.error}</p>
            )}
            <div className="flex gap-1">
              <button
                type="button"
                disabled={!canAct(opsState?.can_control, opsState?.resources?.producer?.exists)}
                onClick={() =>
                  setPendingConfirm({ target: 'producer', action: 'pause', label: 'Pause producer schedule' })
                }
                className="rounded border px-2 py-1 disabled:opacity-50"
              >
                Pause
              </button>
              <button
                type="button"
                disabled={!canAct(opsState?.can_control, opsState?.resources?.producer?.exists)}
                onClick={() =>
                  setPendingConfirm({ target: 'producer', action: 'resume', label: 'Resume producer schedule' })
                }
                className="rounded border px-2 py-1 disabled:opacity-50"
              >
                Resume
              </button>
              <button
                type="button"
                disabled={!canAct(opsState?.can_control, opsState?.resources?.producer?.exists)}
                onClick={() =>
                  setPendingConfirm({ target: 'producer', action: 'restart', label: 'Restart producer job now' })
                }
                className="rounded border px-2 py-1 disabled:opacity-50"
              >
                Restart
              </button>
            </div>
          </div>

          <div className="rounded border border-border p-2 space-y-1">
            <p className="font-medium">Setup / Maintenance</p>
            <p className="text-muted-foreground">state: {opsState?.resources?.setup_job?.state ?? '—'}</p>
            {opsState?.resources?.setup_job?.error && (
              <p className="text-[11px] text-amber-600 dark:text-amber-400">{opsState.resources.setup_job.error}</p>
            )}
            <div className="flex gap-1">
              <button
                type="button"
                disabled={!canAct(opsState?.can_control, opsState?.resources?.setup_job?.exists)}
                onClick={() =>
                  setPendingConfirm({ target: 'setup_job', action: 'run_now', label: 'Run setup/maintenance job now' })
                }
                className="rounded border px-2 py-1 disabled:opacity-50"
              >
                Run now
              </button>
            </div>
          </div>
        </div>
      </div>

      <div className="rounded-lg border border-border bg-card p-4 space-y-2">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium">Pre-demo warm-up</p>
            <p className="text-[11px] text-muted-foreground">
              Side-effect-free probe of SQL warehouse, UC scenario_commands, Lakebase, and engine. Run ~1 min before a
              demo so the first incident click feels instant. Repeat if the warehouse goes idle.
            </p>
          </div>
          <button
            type="button"
            onClick={() => {
              void runWarmup();
            }}
            disabled={warmupRunning || !opsState?.can_control}
            className="rounded border border-border bg-background px-3 py-1 text-xs font-medium hover:bg-muted disabled:opacity-50"
          >
            {warmupRunning ? 'Warming up…' : 'Warm up demo'}
          </button>
        </div>

        {warmup && (
          <div className="space-y-1 pt-1 text-[11px]">
            {warmup.error && <p className="text-destructive">Failed: {warmup.error}</p>}
            {warmup.steps && (
              <>
                <div className="flex items-center gap-2">
                  <span
                    className={`inline-block h-2 w-2 rounded-full ${warmup.ok ? 'bg-emerald-500' : 'bg-amber-500'}`}
                  />
                  <span className="text-muted-foreground">
                    {warmup.ok ? 'Warm' : 'Partial'} · total {warmup.total_ms ?? 0} ms
                  </span>
                </div>
                <ul className="ml-3 space-y-0.5">
                  {warmup.steps.map((s) => (
                    <li key={s.name} className="flex items-baseline gap-2">
                      <span className={s.ok ? 'text-foreground' : 'text-amber-600 dark:text-amber-400'}>
                        [{s.ok ? 'ok' : 'err'}] {s.name}
                      </span>
                      <span className="text-muted-foreground">{s.ms} ms</span>
                      {s.error && (
                        <span className="text-amber-600 dark:text-amber-400 truncate" title={s.error}>
                          {s.error}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        )}
      </div>

      {pendingConfirm && (
        <div className="fixed inset-0 z-[120] flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-md rounded-lg border border-border bg-card p-4 space-y-3 shadow-xl">
            <h4 className="text-sm font-semibold">Confirm action</h4>
            <p className="text-xs text-muted-foreground">{pendingConfirm.label}</p>
            <div className="flex justify-end gap-2">
              <button
                type="button"
                className="rounded border border-border px-3 py-1 text-xs"
                onClick={() => setPendingConfirm(null)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="rounded bg-destructive px-3 py-1 text-xs text-destructive-foreground"
                onClick={() => {
                  const next = pendingConfirm;
                  setPendingConfirm(null);
                  void runOpsAction(next.target, next.action);
                }}
              >
                Confirm
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
