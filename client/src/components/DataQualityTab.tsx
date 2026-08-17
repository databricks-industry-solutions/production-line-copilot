/**
 * Data Integrity: the single "can I trust these numbers?" surface.
 * Used by `App`. Combines the two halves of that question — what the quality
 * gate caught, and whether anything slipped past it — because either one alone
 * answers it only partially.
 *
 * The gate half used to be an embedded AI/BI dashboard. The iframe needed the
 * dashboard published with `embed_credentials`, which runs its queries as the
 * publisher rather than as the viewer, plus CAN_READ for the app service
 * principal on both the dashboard and its parent folder — and it still failed
 * on browsers that decline third-party cookies. It is drawn here instead, from
 * the same SQL, and the dashboard stays one click away for anyone who wants to
 * explore rather than glance.
 */
import { useEffect, useState } from 'react';

import { useQualityMetrics } from '../lib/useQualityMetrics';
import { useReconciliation } from '../lib/useReconciliation';
import QualityGatePanel from './QualityGatePanel';
import { ReconciliationRail, ReconciliationVerdict } from './Reconciliation';

type DashboardInfo = {
  id: string;
  display_name: string;
  host: string | null;
  embed_url: string | null;
};

export default function DataQualityTab() {
  const [dashboardUrl, setDashboardUrl] = useState<string | null>(null);
  const reconciliation = useReconciliation();
  const quality = useQualityMetrics();

  // Only to offer the link. The tab no longer depends on the dashboard being
  // reachable, published or readable, so a failure here is silent by design:
  // the deep link disappears and every figure on the page still renders.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const res = await fetch('/api/ops/dashboard-info');
        if (!res.ok) return;
        const json = (await res.json()) as DashboardInfo;
        if (cancelled || !json.embed_url) return;
        setDashboardUrl(json.embed_url.replace('/embed/dashboardsv3/', '/dashboardsv3/'));
      } catch {
        // No link, no problem.
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
          Two independent checks on the same question. The left shows what the streaming quality
          gate caught — pass rate, per-table freshness, reject reasons. The panel beside it checks
          the live and analytics planes against each other, so anything that slipped past the gate
          unnoticed still shows up.
        </p>
      </div>

      <ReconciliationVerdict
        data={reconciliation.data}
        loading={reconciliation.loading}
        error={reconciliation.error}
        onReload={reconciliation.reload}
      />

      {/* Split only at xl: the gate panel carries four counters and two charts
          side by side, and below ~1280px they stack into a column too narrow to
          read next to the reconciliation rail. */}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-5">
        <section className="xl:col-span-3">
          <div className="mb-2 flex items-center justify-between gap-2">
            <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              Quality gate — last {quality.data?.window_hours ?? 24} hours
            </h3>
            <div className="flex items-center gap-2">
              {dashboardUrl && (
                <a
                  className="text-xs underline text-muted-foreground hover:text-foreground"
                  href={dashboardUrl}
                  target="_blank"
                  rel="noreferrer"
                >
                  Explore in Databricks
                </a>
              )}
              <button
                type="button"
                className="rounded border border-border bg-background px-2 py-1 text-xs hover:bg-accent"
                onClick={quality.reload}
              >
                Reload
              </button>
            </div>
          </div>

          <QualityGatePanel
            data={quality.data}
            loading={quality.loading}
            error={quality.error}
          />
        </section>

        <aside className="xl:col-span-2">
          <ReconciliationRail data={reconciliation.data} />
        </aside>
      </div>
    </div>
  );
}
