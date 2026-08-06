/**
 * Data Integrity: the single "can I trust these numbers?" surface.
 * Used by `App`. Combines the two halves of that question — the quality-gate
 * dashboard (what the gate caught) and reconciliation (whether anything slipped
 * past unnoticed) — because either one alone answers it only partially.
 * Treats dashboard metadata lookup as optional so the tab remains useful when
 * bundle resources are missing.
 */
import { useEffect, useState, useCallback } from 'react';

import { useReconciliation } from '../lib/useReconciliation';
import { ReconciliationRail, ReconciliationVerdict } from './Reconciliation';

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
  const reconciliation = useReconciliation();

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
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold">Data Integrity</h2>
        <p className="mt-0.5 max-w-4xl text-xs text-muted-foreground">
          Two independent checks on the same question. The dashboard shows what the streaming quality gate caught —
          pass rate, per-table freshness, reject reasons. The panel beside it checks the live and analytics planes
          against each other, so anything that slipped past the gate unnoticed still shows up.
        </p>
      </div>

      <ReconciliationVerdict
        data={reconciliation.data}
        loading={reconciliation.loading}
        error={reconciliation.error}
        onReload={reconciliation.reload}
      />

      {/* The embedded Lakeview dashboard doesn't reflow into a narrow column —
          it keeps the wider side, and the reconciliation detail is laid out for
          the rail rather than being the full-width table squeezed in. Splitting
          only at xl: below ~1280px the dashboard's share drops under ~700px,
          where its own widgets start scrolling internally, so stacking at full
          width beats two cramped columns. */}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-5">
        <section className="xl:col-span-3">
          <div className="mb-2 flex items-center justify-between gap-2">
            <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              Quality gate — AI/BI dashboard
            </h3>
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
            <div className="flex h-[calc(100vh-365px)] min-h-[460px] items-center justify-center rounded-md border border-border text-sm text-muted-foreground">
              Loading dashboard…
            </div>
          )}

          {state === 'error' && (
            <div className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-xs text-destructive">
              <div className="font-medium">Dashboard not available.</div>
              {/* pre-wrap: the server returns a copy-pasteable CLI command on
                  its own line when the dashboard exists but is unpublished. */}
              <div className="mt-1 whitespace-pre-wrap break-words font-mono opacity-80">{error}</div>
              <div className="mt-2 text-foreground opacity-70">
                <div className="mb-1">
                  Most common cause after a fresh deploy: the app SP lacks CAN_READ on the Lakeview dashboard (or its
                  parent folder), or the dashboard was not published with <code>embed_credentials=true</code>. Both are
                  non-DAB steps run by <code>scripts/grant_app_jobs_view.sh</code>.
                </div>
                <div>
                  Fix: re-run <code>./scripts/deploy.sh prod</code> (it now invokes the grant script post-deploy), or run{' '}
                  <code>scripts/grant_app_jobs_view.sh</code> manually with <code>DASHBOARD_ID</code> +{' '}
                  <code>WAREHOUSE_ID</code> set, then click <strong>Reload</strong>.
                </div>
              </div>
            </div>
          )}

          {/* Height is sized to the fold: the app chrome, tab bar, page header
              and verdict strip take ~365px above this. */}
          {state === 'ready' && info?.embed_url && (
            <div className="h-[calc(100vh-365px)] min-h-[460px] overflow-hidden rounded-md border border-border">
              {/* If widgets render "Unable to render visualization." inside this iframe,
                  the dashboard isn't published with embed_credentials=true. Run
                  scripts/grant_app_jobs_view.sh (or ./scripts/deploy.sh prod which now
                  invokes it post-deploy) to publish + grant CAN_READ to the app SP. */}
              <iframe
                title="Data Quality dashboard"
                src={info.embed_url}
                className="h-full w-full"
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
        </section>

        <aside className="xl:col-span-2">
          <ReconciliationRail data={reconciliation.data} />
        </aside>
      </div>
    </div>
  );
}
