/**
 * Embeds the data-quality AI/BI dashboard with lightweight load/error handling.
 * Used by `App` in the Data Quality tab for streaming quality-gate freshness and validation checks.
 * Treats dashboard metadata lookup as optional so the app remains usable when bundle resources are missing.
 */
import { useEffect, useState, useCallback } from 'react';

type DashboardInfo = {
  id: string;
  display_name: string;
  host: string | null;
  embed_url: string | null;
};

type DashboardInfoResponse = DashboardInfo & {
  error?: string;
};

type LoadState = 'loading' | 'ready' | 'error';

export default function DataQualityTab() {
  const [info, setInfo] = useState<DashboardInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [state, setState] = useState<LoadState>('loading');

  const load = useCallback(async (options?: { showLoading?: boolean }) => {
    const showLoading = options?.showLoading ?? true;
    if (showLoading) {
      setState('loading');
    }
    setError(null);
    try {
      const res = await fetch('/api/ops/dashboard-info');
      const json = (await res.json()) as DashboardInfoResponse;
      if (!res.ok || json.error) {
        setError(typeof json.error === 'string' ? json.error : `HTTP ${res.status}`);
        setInfo(null);
        setState('error');
        return;
      }
      setInfo(json);
      setState('ready');
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Unknown error';
      setError(msg);
      setInfo(null);
      setState('error');
    }
  }, []);

  useEffect(() => {
    let cancelled = false;

    void (async () => {
      setError(null);
      try {
        const res = await fetch('/api/ops/dashboard-info');
        const json = (await res.json()) as DashboardInfoResponse;
        if (cancelled) return;
        if (!res.ok || json.error) {
          setError(typeof json.error === 'string' ? json.error : `HTTP ${res.status}`);
          setInfo(null);
          setState('error');
          return;
        }
        setInfo(json);
        setState('ready');
      } catch (e: unknown) {
        if (cancelled) return;
        const msg = e instanceof Error ? e.message : 'Unknown error';
        setError(msg);
        setInfo(null);
        setState('error');
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="space-y-3" style={{ height: 'calc(100vh - 140px)' }}>
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">Data Quality</h2>
          <p className="text-xs text-muted-foreground">
            Quality-gate pass rate (validated rows vs rejected rows), per-table freshness, and reject reasons. Embedded
            AI/BI dashboard — opens in Databricks for filter / export.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {info?.embed_url && (
            <a
              className="text-xs underline text-muted-foreground hover:text-foreground"
              href={info.embed_url.replace('/embed/dashboardsv3/', '/dashboardsv3/')}
              target="_blank"
              rel="noreferrer"
            >
              Open in Databricks
            </a>
          )}
          <button
            type="button"
            className="rounded border border-border bg-background px-2 py-1 text-xs hover:bg-accent"
            onClick={() => {
              void load();
            }}
          >
            Reload
          </button>
        </div>
      </div>

      {state === 'loading' && (
        <div className="flex items-center justify-center h-full text-sm text-muted-foreground">Loading dashboard…</div>
      )}

      {state === 'error' && (
        <div className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-xs text-destructive">
          <div className="font-medium">Dashboard not available.</div>
          <div className="mt-1 opacity-80 break-words">{error}</div>
          <div className="mt-2 text-foreground opacity-70">
            <div className="mb-1">
              Most common cause after a fresh deploy: the app SP lacks CAN_READ on the Lakeview dashboard (or its parent
              folder), or the dashboard was not published with <code>embed_credentials=true</code>. Both are non-DAB
              steps run by <code>scripts/grant_app_jobs_view.sh</code>.
            </div>
            <div>
              Fix: re-run <code>./scripts/deploy.sh prod</code> (it now invokes the grant script post-deploy), or run{' '}
              <code>scripts/grant_app_jobs_view.sh</code> manually with <code>DASHBOARD_ID</code> +{' '}
              <code>WAREHOUSE_ID</code> set, then click <strong>Reload</strong>.
            </div>
          </div>
        </div>
      )}

      {state === 'ready' && info?.embed_url && (
        <div
          className="flex-1 min-h-0 rounded-md overflow-hidden border border-border"
          style={{ height: 'calc(100% - 70px)' }}
        >
          {/* If widgets render "Unable to render visualization." inside this iframe,
              the dashboard isn't published with embed_credentials=true. Run
              scripts/grant_app_jobs_view.sh (or ./scripts/deploy.sh prod which now
              invokes it post-deploy) to publish + grant CAN_READ to the app SP. */}
          <iframe
            title="Data Quality dashboard"
            src={info.embed_url}
            className="w-full h-full"
            allow="clipboard-write"
          />
        </div>
      )}

      {state === 'ready' && !info?.embed_url && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-3 text-xs">
          Dashboard found (id <code>{info?.id}</code>) but no host configured. Set
          <code className="ml-1">DATABRICKS_HOST</code> on the app and reload.
        </div>
      )}
    </div>
  );
}
