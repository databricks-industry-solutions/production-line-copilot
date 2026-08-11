/**
 * Shows live and billing-lagged cost observability for app-owned resources and data movement.
 * Used by `App` in the Cost & Obs tab to compare in-app counters with `system.billing.usage`.
 * Automatically switches to live estimation for short windows because billing tables are intentionally delayed.
 */
import { useCallback, useMemo, useState } from 'react';
import { formatTimestamp } from '../lib/utils';

interface CostObservabilityTabProps {
  connected: boolean;
  streamLastUpdateTs: string | null;
  sourceLastEventTs: string | null;
  ingestionLatencyMs: number | null;
  machineCount: number;
  eventCount: number;
}

interface ComponentRow {
  component: string;
  dbu: number;
  estimated_usd: number;
  records: number;
}

interface CostSummaryResponse {
  generated_at?: string;
  window_hours?: number;
  pipeline_mode?: string;
  totals?: { dbu?: number; billing_rows?: number; estimated_usd?: number };
  components?: ComponentRow[];
  app_attribution?: {
    warehouse_id?: string | null;
    warehouse_dbu?: number | null;
    warehouse_estimated_usd?: number | null;
    producer_job_id?: string | null;
    producer_job_dbu?: number | null;
    setup_job_id?: string | null;
    setup_job_dbu?: number | null;
    total_app_dbu?: number;
    total_app_estimated_usd?: number;
  };
  warehouse_share?: {
    available?: boolean;
    warehouse_id?: string;
    attribution_key?: 'identity' | 'identity+tag';
    app_statements?: number;
    other_statements?: number;
    app_duration_ms?: number;
    other_duration_ms?: number;
    app_genie_duration_ms?: number;
    /** null when the warehouse ran no queries at all in the window. */
    app_share?: number | null;
    warehouse_dbu?: number | null;
    app_dbu?: number | null;
    app_estimated_usd?: number | null;
    /** Age of the cached query-time split; the DBU it is applied to is always fresh. */
    split_age_seconds?: number;
    note?: string;
  } | null;
  zerobus?: {
    enabled?: boolean;
    ingested_bytes?: number;
    ingested_gb?: number;
    ingested_human?: string;
    observed_dbu?: number;
    rate_dbu_per_gb?: number;
    estimated_dbu?: number;
    estimated_usd?: number;
  };
  other_resources?: Array<{ component: string; unit: string; quantity: number; records: number }>;
  data_processed?: { records?: number; bytes?: number; bytes_human?: string };
  pricing?: {
    rates_usd_per_dbu?: Record<string, number>;
    note?: string;
  };
  scope?: {
    workspace_id_filter?: string | null;
    attribution_mode?: string;
    app_resources?: {
      warehouse_id?: string | null;
      producer_job_id?: string | null;
      setup_job_id?: string | null;
    };
  };
  freshness?: { latest_usage_ts?: string | null; lag_hours?: number | null };
  details?: {
    trend?: Array<{ hour: string; dbu: number }>;
    matched_dbu?: number;
    unmatched_dbu?: number;
    top_unmatched_rows?: Array<{
      billing_origin_product?: string;
      sku_name?: string;
      usage_unit?: string;
      usage_quantity?: number;
    }>;
    verification_warning?: string | null;
  } | null;
  warning?: string | null;
  error?: string;
}

interface ResourceState {
  id?: string | null;
  state?: string;
  running?: boolean;
}

interface LiveCostResponse {
  generated_at?: string;
  window_minutes?: number;
  window_minutes_effective?: number;
  pipeline_mode?: string;
  app_started_at?: string;
  resources?: {
    warehouse?: ResourceState;
    producer_job?: ResourceState;
    synced_tables?: { count?: number; active?: boolean };
  };
  counters?: {
    // SSE ticks + payload bytes are still in the payload for debugging but
    // intentionally not surfaced — they're heartbeat counters, not cost signal.
    sse_ticks?: number;
    sse_payload_bytes?: number;
    sse_payload_bytes_human?: string;
    sse_payload_rows?: number;
    lakebase_reads?: number;
    agent_invocations?: number;
    llm_calls?: number;
    llm_input_tokens?: number;
    llm_output_tokens?: number;
    events_published?: number;
    vector_search_calls?: number;
    genie_queries?: number;
  };
  data_in_app?: {
    events_in_window?: number;
    rows_in_window?: number;
  };
  zerobus_estimate?: {
    enabled?: boolean;
    producer_running?: boolean;
    estimated_bytes?: number;
    estimated_bytes_human?: string;
    estimated_gb?: number;
    rate_usd_per_gb?: number;
    estimated_usd?: number;
    producer_throughput_gb_per_hour?: number;
    note?: string;
  };
  bronze_estimate?: {
    producer_running?: boolean;
    rows_per_tick_total?: number;
    rows_per_tick_per_table?: Record<string, number>;
    tick_hz?: number;
    window_minutes_effective?: number;
    estimated_rows_in_window?: number;
    per_table_in_window?: Record<string, number>;
    note?: string;
  };
  tool_callers?: {
    vector_search?: string[];
    genie?: string[];
    examples?: { genie?: string; vector_search?: string };
  };
  estimated_dbu?: { llm?: number };
  estimated_usd?: {
    total?: number;
    components?: Array<{ component: string; usd: number; detail?: string; status?: 'running' | 'idle' }>;
  };
  rates?: Record<string, number>;
  excluded?: string[];
  note?: string;
  error?: string;
}

const WINDOW_OPTIONS: Array<{ label: string; hours: number }> = [
  { label: '5m', hours: 5 / 60 },
  { label: '15m', hours: 15 / 60 },
  { label: '1h', hours: 1 },
  { label: '6h', hours: 6 },
  { label: '24h', hours: 24 },
  { label: '7d', hours: 24 * 7 },
  { label: '14d', hours: 24 * 14 },
];

function fmtMs(ms: number | null): string {
  if (ms == null) return '—';
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

function fmtDbu(v: number | null | undefined): string {
  if (v == null) return '—';
  return v.toFixed(3);
}

/**
 * Every dollar figure here is DBU multiplied by a rounded list-price guess, so
 * four decimals advertised a precision the number never had — `$341.6100` reads
 * like a settled invoice rather than an estimate that is comfortably ±20%.
 * Sub-cent live line items (an AI Search query is $0.0001) still need the
 * extra digits or they render as a misleading `$0.00`.
 */
function fmtUsd(v: number | null | undefined): string {
  if (v == null) return '—';
  if (v !== 0 && Math.abs(v) < 0.01) return `$${v.toFixed(4)}`;
  return `$${v.toFixed(2)}`;
}

function statusClass(connected: boolean): string {
  return connected
    ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
    : 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400';
}

function tip(text: string): { title: string; 'aria-label': string } {
  return { title: text, 'aria-label': text };
}

function fmtLag(h: number | null | undefined): string {
  if (h == null) return '—';
  if (h < 1) return `${Math.round(h * 60)} min ago`;
  return `${h.toFixed(1)} h ago`;
}

export default function CostObservabilityTab({
  connected,
  streamLastUpdateTs,
  sourceLastEventTs,
  ingestionLatencyMs,
  machineCount,
  eventCount,
}: CostObservabilityTabProps) {
  const [costData, setCostData] = useState<CostSummaryResponse | null>(null);
  const [liveData, setLiveData] = useState<LiveCostResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [verifyLoading, setVerifyLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastRefreshedTs, setLastRefreshedTs] = useState<string | null>(null);
  const [windowHours, setWindowHours] = useState<number>(24);
  const [lastFetchedWindowHours, setLastFetchedWindowHours] = useState<number | null>(null);
  const [showCalcNote, setShowCalcNote] = useState(false);
  const [showVerification, setShowVerification] = useState(false);

  const isLiveMode = windowHours <= 1;

  const fetchCost = useCallback(
    async (includeDetails = false) => {
      if (includeDetails) setVerifyLoading(true);
      else setLoading(true);
      try {
        if (isLiveMode && !includeDetails) {
          const minutes = Math.max(1, Math.round(windowHours * 60));
          const res = await fetch(`/api/ops/cost-live?minutes=${minutes}`);
          const data = (await res.json()) as LiveCostResponse;
          if (!res.ok || data.error) {
            setError(data.error ?? `HTTP ${res.status}`);
            setLiveData(null);
          } else {
            setError(null);
            setLiveData(data);
            setLastRefreshedTs(new Date().toISOString());
            setLastFetchedWindowHours(windowHours);
          }
          return;
        }
        const res = await fetch(
          `/api/ops/cost-summary?hours=${windowHours}&include_details=${includeDetails ? 'true' : 'false'}`
        );
        const data = (await res.json()) as CostSummaryResponse;
        if (!res.ok || data.error) {
          setError(data.error ?? `HTTP ${res.status}`);
          if (!includeDetails) setCostData(null);
        } else {
          setError(null);
          setCostData((prev) => {
            if (includeDetails && prev) {
              return { ...prev, details: data.details ?? null };
            }
            return data;
          });
          if (!includeDetails) {
            setLastRefreshedTs(new Date().toISOString());
            setLastFetchedWindowHours(windowHours);
          }
          if (includeDetails) setShowVerification(true);
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Cost endpoint unavailable');
        if (!includeDetails) {
          if (isLiveMode) setLiveData(null);
          else setCostData(null);
        }
      } finally {
        setLoading(false);
        setVerifyLoading(false);
      }
    },
    [windowHours, isLiveMode]
  );

  const pipelineModeRaw = costData?.pipeline_mode ?? liveData?.pipeline_mode;
  const pipelineModeText = pipelineModeRaw ? pipelineModeRaw.toUpperCase() : 'Detecting…';
  const zerobusMode = costData?.pipeline_mode === 'zerobus';
  const windowMismatch =
    (costData != null || liveData != null) && lastFetchedWindowHours != null && lastFetchedWindowHours !== windowHours;
  const components = useMemo(() => costData?.components ?? [], [costData]);
  const appAttr = costData?.app_attribution;
  const whShare = costData?.warehouse_share;
  const zerobus = costData?.zerobus;
  const trend = useMemo(() => costData?.details?.trend ?? [], [costData]);
  const topUnmatched = costData?.details?.top_unmatched_rows ?? [];
  const trendMaxDbu = useMemo(() => trend.reduce((m, t) => (t.dbu > m ? t.dbu : m), 0) || 1, [trend]);
  const matchedDbu = costData?.details?.matched_dbu ?? null;
  const unmatchedDbu = costData?.details?.unmatched_dbu ?? null;
  const matchedRatio =
    matchedDbu != null && unmatchedDbu != null && matchedDbu + unmatchedDbu > 0
      ? matchedDbu / (matchedDbu + unmatchedDbu)
      : null;

  const totalUsd = isLiveMode ? liveData?.estimated_usd?.total : costData?.totals?.estimated_usd;
  const appUsd = appAttr?.total_app_estimated_usd;
  // "account" is the fallback when the workspace id could not be resolved, in
  // which case these totals span every workspace the account bills for.
  const scopeLabel = costData?.scope?.attribution_mode === 'workspace-scoped' ? 'workspace' : 'account';

  const isBusy = loading || verifyLoading;
  const noData = !loading && !error && (isLiveMode ? !liveData : !costData);
  const liveCounters = liveData?.counters;
  const liveComponents = liveData?.estimated_usd?.components ?? [];
  const windowLabel = windowHours < 1 ? `${Math.round(windowHours * 60)}m` : `${windowHours}h`;

  const bronzeEstimate = liveData?.bronze_estimate;
  const winMinEff = bronzeEstimate?.window_minutes_effective ?? null;
  const bronzeWindowLabel =
    winMinEff != null
      ? winMinEff < 60
        ? `${Math.round(winMinEff)}m`
        : `${Math.floor(winMinEff / 60)}h ${Math.round(winMinEff % 60)}m`
      : windowLabel;
  const bronzePerTable = bronzeEstimate?.per_table_in_window ?? {};
  const bronzeBreakdownLines = Object.entries(bronzePerTable)
    .sort((a, b) => (b[1] ?? 0) - (a[1] ?? 0))
    .map(([t, n]) => `${t}: ${n.toLocaleString()}`)
    .join(' · ');
  const bronzeTooltip = bronzeEstimate
    ? `Estimated rows landing in *_bronze tables over the last ${bronzeWindowLabel} ` +
      `(auto-clipped to app uptime so the count never claims more time than the app has been up). ` +
      `${bronzeEstimate.rows_per_tick_total ?? 120} rows/tick × ` +
      `${bronzeEstimate.tick_hz ?? 1} tick/s × producer-running seconds. ` +
      `Per table — ${bronzeBreakdownLines || 'no breakdown'}. ` +
      `Authoritative actuals: SELECT COUNT(*) FROM <catalog>.<schema>.<table>_bronze in Databricks SQL.`
    : `Estimated rows landing in *_bronze tables over the last ${windowLabel}. Waiting for live cost data…`;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-5 gap-3">
        <div
          className="rounded-lg border border-border bg-card p-3"
          {...tip('Inferred from ZEROBUS_INGEST_ENABLED env. Detected after first cost refresh.')}
        >
          <p className="text-[11px] text-muted-foreground">Pipeline Mode</p>
          <p className={`text-sm font-semibold mt-1 ${pipelineModeRaw ? 'uppercase' : 'text-muted-foreground'}`}>
            {pipelineModeText}
          </p>
        </div>
        <div
          className="rounded-lg border border-border bg-card p-3"
          {...tip('Live SSE connection from app to Lakebase.')}
        >
          <p className="text-[11px] text-muted-foreground">Stream Health</p>
          <span className={`mt-1 inline-flex rounded-full px-2 py-0.5 text-xs font-semibold ${statusClass(connected)}`}>
            {connected ? 'Connected' : 'Reconnecting'}
          </span>
        </div>
        <div
          className="rounded-lg border border-border bg-card p-3"
          {...tip('Time between source event and SSE delivery.')}
        >
          <p className="text-[11px] text-muted-foreground">Ingestion Latency</p>
          <p className="text-sm font-semibold mt-1">{fmtMs(ingestionLatencyMs)}</p>
        </div>
        <div
          className="rounded-lg border border-border bg-card p-3"
          {...tip(
            isLiveMode ? bronzeTooltip : 'DBUs in selected window. NOTE: DBUs across SKUs are not directly comparable.'
          )}
        >
          <p className="text-[11px] text-muted-foreground">
            {isLiveMode ? (
              <>
                Rows ingested (Bronze) <span className="opacity-70">· last {bronzeWindowLabel}</span>
              </>
            ) : (
              'Window DBU'
            )}
          </p>
          <p className="text-sm font-semibold mt-1">
            {isLiveMode
              ? (liveData?.bronze_estimate?.estimated_rows_in_window ?? 0).toLocaleString()
              : fmtDbu(costData?.totals?.dbu)}
          </p>
          {isLiveMode && liveData?.bronze_estimate?.producer_running === false && (
            <p className="text-[10px] text-amber-600 dark:text-amber-400 mt-0.5">Producer idle — 0 rows landing</p>
          )}
        </div>
        <div
          className="rounded-lg border border-border bg-card p-3"
          {...tip(
            isLiveMode
              ? 'Approximate USD using fixed list-price rates per component. Estimate only.'
              : `Everything billed in this ${scopeLabel} for the window — not just this app. ` +
                'The App attribution card below shows the app\'s own share. ' +
                'Approximate USD using fixed list-price rates per component; estimate only.',
          )}
        >
          {/* The panel shows two dollar figures — this one and "Total app est.
              USD" — and they legitimately differ by however much other work
              runs alongside the app. Labelled only "Estimated USD", the pair
              read as a contradiction rather than as two scopes. */}
          <p className="text-[11px] text-muted-foreground">
            {isLiveMode ? 'Estimated USD (app)' : `Estimated USD (${scopeLabel})`}
          </p>
          <p className="text-sm font-semibold mt-1">{fmtUsd(totalUsd)}</p>
        </div>
      </div>

      <div className="rounded-lg border border-border bg-card p-4 space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <h3 className="text-sm font-semibold">
              Cost & Usage{' '}
              <span
                className={`ml-2 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${
                  isLiveMode
                    ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300'
                    : 'bg-slate-100 text-slate-600 dark:bg-slate-800/40 dark:text-slate-300'
                }`}
              >
                {isLiveMode ? 'Live (in-app)' : 'system.billing.usage'}
              </span>
            </h3>
            <p className="text-[11px] text-muted-foreground">
              Window: {windowHours < 1 ? `${Math.round(windowHours * 60)}m` : `${windowHours}h`} · Last refreshed:{' '}
              {lastRefreshedTs ? formatTimestamp(lastRefreshedTs) : '—'}
              {!isLiveMode && costData?.freshness?.latest_usage_ts && (
                <>
                  {' · '}Source data: {fmtLag(costData.freshness.lag_hours ?? null)}
                </>
              )}
              {isLiveMode && liveData?.app_started_at && (
                <>
                  {' · '}App started: {formatTimestamp(liveData.app_started_at)}
                </>
              )}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-1">
            <div className="flex items-center gap-0.5 rounded border border-border p-0.5">
              {WINDOW_OPTIONS.map((opt) => (
                <button
                  key={opt.hours}
                  type="button"
                  onClick={() => setWindowHours(opt.hours)}
                  className={`rounded px-2 py-0.5 text-[11px] font-medium transition-colors ${
                    windowHours === opt.hours ? 'bg-primary text-primary-foreground' : 'hover:bg-muted'
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
            <button
              type="button"
              onClick={() => setShowCalcNote((v) => !v)}
              className="rounded border border-border px-2 py-1 text-[11px] font-medium text-foreground hover:bg-muted"
              {...tip('Show methodology and rate assumptions.')}
            >
              {showCalcNote ? 'Hide notes' : 'How calculated'}
            </button>
            <button
              type="button"
              onClick={() => {
                void fetchCost(false);
              }}
              disabled={isBusy}
              className="rounded border border-border px-2 py-1 text-[11px] font-medium text-foreground hover:bg-muted disabled:opacity-60 disabled:cursor-not-allowed"
            >
              {loading ? 'Refreshing...' : 'Refresh'}
            </button>
            {!isLiveMode && (
              <button
                type="button"
                onClick={() => {
                  void fetchCost(true);
                }}
                disabled={isBusy}
                className="rounded border border-border px-2 py-1 text-[11px] font-medium text-foreground hover:bg-muted disabled:opacity-60 disabled:cursor-not-allowed"
                {...tip('Pull raw matched/unmatched detail and hourly trend from system.billing.usage.')}
              >
                {verifyLoading ? 'Verifying...' : 'Run verification'}
              </button>
            )}
          </div>
        </div>

        {showCalcNote && (
          <div className="rounded border border-border bg-muted/30 p-2 text-[11px] text-muted-foreground space-y-1">
            {isLiveMode ? (
              <>
                <p>
                  Live mode is active for windows ≤ 1 h because <code>system.billing.usage</code> typically lags 1–4 h.
                  Numbers are derived from in-app counters captured by this server process — they do not query any
                  system table.
                </p>
                <p>
                  Counters tracked: rows delivered, Lakebase reads, agent / LLM calls, AI Search & Genie tool
                  invocations, events surfaced. LLM tokens use the model-reported usage when available, otherwise
                  estimated as chars / 4.
                </p>
                <p>
                  USD breakdown: per-token LLM × list rates; amortised hourly rate for App / Lakebase / Warehouse /
                  Producer / Synced / AI Search endpoint, charged only while the resource is RUNNING; Zerobus ingest
                  at $0.079/GB (0.143 DBU/GB × $0.55/DBU Jobs Serverless), estimated from in-app event counts × ~1
                  KB/event.
                </p>
                <p>
                  <strong>Producer job:</strong> the continuous Producer job is the sole writer of live telemetry
                  (cascade → Zerobus → bronze → streaming sync → <code>ai_serving.*</code>), charged only while
                  RUNNING. The App reads <code>ai_serving.*</code> over SSE and never generates telemetry itself.
                </p>
                <p>
                  Switch to a 6h+ window to compare against billing-derived values once{' '}
                  <code>system.billing.usage</code> has caught up.
                </p>
              </>
            ) : (
              <>
                <p>
                  DBU is read from <code>system.billing.usage</code>; component buckets are matched by product/SKU
                  keywords with precedence: Zerobus → Lakebase → Pipelines → Model Serving → SQL Warehouse → Jobs → Apps
                  → Other.
                </p>
                <p>
                  USD is an estimate using fixed approximate list-prices per component (Jobs $0.55, SQL $0.70, Pipelines
                  $0.36, Model Serving $0.07, others $0.55). Real $ depends on cloud, tier, and discounts.
                </p>
                <p>
                  Zerobus charges (Lakeflow Connect) are translated into DBUs and billed as
                  <span className="font-medium"> Jobs Serverless</span> /{' '}
                  <span className="font-medium">Automated Serverless</span> SKUs in
                  <code> system.billing.usage</code>. There is no separate Zerobus DBU SKU, so the dedicated Zerobus
                  block only appears when ingest volume bytes are exposed in your usage rows.
                </p>
                <p>
                  App-scoped attribution sums DBUs from <code>usage_metadata.warehouse_id / job_id</code> for the
                  resources this app owns.
                </p>
                <p>
                  <code>system.billing.usage</code> typically lags 1–4h; for shorter windows the panel switches to Live
                  mode automatically.
                </p>
              </>
            )}
          </div>
        )}

        {error && <p className="text-xs text-destructive">Cost query failed: {error}</p>}
        {noData && (
          <p className="text-xs text-muted-foreground">
            Manual mode: pick a window and click Refresh to load cost data.
          </p>
        )}
        {windowMismatch && (
          <p className="text-[11px] text-muted-foreground">
            Window changed to {windowHours < 1 ? `${Math.round(windowHours * 60)}m` : `${windowHours}h`} — current data
            is for{' '}
            {lastFetchedWindowHours != null && lastFetchedWindowHours < 1
              ? `${Math.round(lastFetchedWindowHours * 60)}m`
              : `${lastFetchedWindowHours}h`}
            . Click Refresh to reload.
          </p>
        )}
        {!isLiveMode && costData?.warning && (
          <p className="text-[11px] text-amber-600 dark:text-amber-400">{costData.warning}</p>
        )}

        {/* Live mode body */}
        {isLiveMode && !noData && !error && (
          <>
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
              <div className="rounded-lg border border-border p-3 space-y-2 lg:col-span-2">
                <div className="flex items-center justify-between">
                  <h4 className="text-xs font-semibold uppercase text-muted-foreground">Live USD breakdown</h4>
                  <span className="text-[10px] text-muted-foreground">
                    last{' '}
                    {liveData?.window_minutes_effective != null
                      ? `${liveData.window_minutes_effective.toFixed(1)} min`
                      : `${windowHours * 60} min`}
                  </span>
                </div>
                <p className="text-[11px] text-muted-foreground">
                  Per-token rates for LLM. Hourly rates amortised over the window for compute lines — charged only when
                  the resource is currently RUNNING.
                </p>
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-[11px] uppercase text-muted-foreground">
                        <th className="text-left font-medium pb-1">Component</th>
                        <th className="text-left font-medium pb-1">State</th>
                        <th className="text-right font-medium pb-1">est. USD</th>
                        <th className="text-left font-medium pb-1 pl-3">Detail</th>
                      </tr>
                    </thead>
                    <tbody>
                      {liveComponents.map((c) => {
                        const stateLabel = c.status === 'running' ? '● running' : c.status === 'idle' ? '○ idle' : '';
                        const stateClass =
                          c.status === 'running'
                            ? 'text-green-600 dark:text-green-400'
                            : c.status === 'idle'
                              ? 'text-muted-foreground'
                              : 'text-muted-foreground';
                        return (
                          <tr key={c.component} className="border-t border-border/50">
                            <td className="py-1">{c.component}</td>
                            <td className={`py-1 text-[11px] ${stateClass}`}>{stateLabel}</td>
                            <td
                              className={`py-1 text-right tabular-nums ${c.usd === 0 && c.status === 'idle' ? 'text-muted-foreground' : ''}`}
                            >
                              {fmtUsd(c.usd)}
                            </td>
                            <td className="py-1 pl-3 text-muted-foreground">{c.detail ?? ''}</td>
                          </tr>
                        );
                      })}
                      <tr className="border-t border-border">
                        <td className="py-1 font-semibold">Total</td>
                        <td />
                        <td className="py-1 text-right tabular-nums font-semibold">
                          {fmtUsd(liveData?.estimated_usd?.total)}
                        </td>
                        <td />
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>

              <div className="rounded-lg border border-border p-3 space-y-2">
                <h4 className="text-xs font-semibold uppercase text-muted-foreground">In-app counters</h4>
                <ul className="text-xs space-y-1">
                  <li
                    className="flex items-center justify-between"
                    {...tip('Lakebase read calls during SSE generation.')}
                  >
                    <span className="text-muted-foreground">Lakebase reads</span>
                    <span className="font-medium tabular-nums">{liveCounters?.lakebase_reads ?? 0}</span>
                  </li>
                  <li
                    className="flex items-center justify-between"
                    {...tip('Sum of rows (machines + events + inventory + commands) delivered to clients.')}
                  >
                    <span className="text-muted-foreground">Rows delivered</span>
                    <span className="font-medium tabular-nums">{liveCounters?.sse_payload_rows ?? 0}</span>
                  </li>
                  <li className="flex items-center justify-between" {...tip('Agent / chat invocations served.')}>
                    <span className="text-muted-foreground">Agent calls</span>
                    <span className="font-medium tabular-nums">{liveCounters?.agent_invocations ?? 0}</span>
                  </li>
                  <li className="flex items-center justify-between" {...tip('LLM round-trips.')}>
                    <span className="text-muted-foreground">LLM calls</span>
                    <span className="font-medium tabular-nums">{liveCounters?.llm_calls ?? 0}</span>
                  </li>
                  <li
                    className="flex items-center justify-between"
                    {...tip('Tokens reported by the model, or estimated as chars/4 if the model omits usage.')}
                  >
                    <span className="text-muted-foreground">LLM tokens (in/out)</span>
                    <span className="font-medium tabular-nums">
                      {(liveCounters?.llm_input_tokens ?? 0) + ' / ' + (liveCounters?.llm_output_tokens ?? 0)}
                    </span>
                  </li>
                  <li
                    className="flex items-center justify-between"
                    {...tip('AI Search index queries — used by specialists for similar-incident retrieval.')}
                  >
                    <span className="text-muted-foreground">AI Search queries</span>
                    <span className="font-medium tabular-nums">{liveCounters?.vector_search_calls ?? 0}</span>
                  </li>
                  <li
                    className="flex items-center justify-between"
                    {...tip('Genie Conversation API calls — used by Strategic Advisor and OEE Coach.')}
                  >
                    <span className="text-muted-foreground">Genie queries</span>
                    <span className="font-medium tabular-nums">{liveCounters?.genie_queries ?? 0}</span>
                  </li>
                  {liveData?.zerobus_estimate?.enabled && (
                    <li
                      className="flex items-center justify-between border-t border-border pt-1"
                      {...tip(liveData.zerobus_estimate.note ?? 'Zerobus only ingests while Producer Job is running.')}
                    >
                      <span className="text-muted-foreground">Zerobus est. bytes</span>
                      <span
                        className={`font-medium tabular-nums ${liveData.zerobus_estimate.producer_running ? '' : 'text-muted-foreground'}`}
                      >
                        {liveData.zerobus_estimate.producer_running
                          ? (liveData.zerobus_estimate.estimated_bytes_human ?? '—')
                          : 'producer idle'}
                      </span>
                    </li>
                  )}
                </ul>

                {liveData?.tool_callers && (
                  <div className="mt-2 rounded border border-border bg-muted/20 p-2 text-[10px] leading-snug">
                    <p className="font-semibold text-muted-foreground mb-1">How to trigger VS / Genie counters</p>
                    <p className="text-muted-foreground">
                      <strong>AI Search</strong> ({(liveData.tool_callers.vector_search ?? []).join(', ')})
                      <br />
                      Try:{' '}
                      <em>
                        &ldquo;{liveData.tool_callers.examples?.vector_search ?? 'Have we seen this issue before?'}
                        &rdquo;
                      </em>
                    </p>
                    <p className="text-muted-foreground mt-1">
                      <strong>Genie</strong> ({(liveData.tool_callers.genie ?? []).join(', ')})
                      <br />
                      Try: <em>&ldquo;{liveData.tool_callers.examples?.genie ?? 'OEE trend last 5 shifts?'}&rdquo;</em>
                    </p>
                  </div>
                )}
              </div>
            </div>

            {liveData?.excluded && liveData.excluded.length > 0 && (
              <div className="rounded-lg border border-border bg-muted/40 p-3">
                <h4 className="text-xs font-semibold uppercase text-muted-foreground mb-1">
                  Not counted in this estimate
                </h4>
                <ul className="text-[11px] text-muted-foreground space-y-0.5 list-disc pl-4">
                  {liveData.excluded.map((x) => (
                    <li key={x}>{x}</li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}

        {/* Billing-mode body */}
        {!isLiveMode && !noData && !error && (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
            {/* Components */}
            <div className="rounded-lg border border-border p-3 space-y-2 lg:col-span-2">
              <h4 className="text-xs font-semibold uppercase text-muted-foreground">Components</h4>
              {components.length === 0 ? (
                <p className="text-xs text-muted-foreground">No DBU rows.</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-[11px] uppercase text-muted-foreground">
                        <th className="text-left font-medium pb-1">Component</th>
                        <th className="text-right font-medium pb-1">DBU</th>
                        <th className="text-right font-medium pb-1">est. USD</th>
                        <th className="text-right font-medium pb-1">rows</th>
                      </tr>
                    </thead>
                    <tbody>
                      {components.map((c) => (
                        <tr key={c.component} className="border-t border-border/50">
                          <td className="py-1">{c.component}</td>
                          <td className="py-1 text-right tabular-nums">{c.dbu.toFixed(3)}</td>
                          <td className="py-1 text-right tabular-nums">{fmtUsd(c.estimated_usd)}</td>
                          <td className="py-1 text-right tabular-nums">{c.records}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            {/* Other (non-DBU) resources */}
            <div className="rounded-lg border border-border p-3 space-y-2">
              <h4 className="text-xs font-semibold uppercase text-muted-foreground">Other resources (non-DBU)</h4>
              <p className="text-[11px] text-muted-foreground">
                Storage, network, and other non-DBU billed quantities (Lakebase storage GB, etc.).
              </p>
              {(costData?.other_resources?.length ?? 0) === 0 ? (
                <p className="text-xs text-muted-foreground">None in window.</p>
              ) : (
                <ul className="text-xs space-y-1">
                  {(costData?.other_resources ?? []).map((r) => (
                    <li key={`${r.component}-${r.unit}-${r.quantity}`} className="flex items-center justify-between">
                      <span className="text-muted-foreground truncate" title={r.component}>
                        {r.component}
                      </span>
                      <span className="font-medium tabular-nums whitespace-nowrap">
                        {r.quantity.toFixed(2)} {r.unit}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            {/* App attribution */}
            <div className="rounded-lg border border-border p-3 space-y-2 lg:col-span-3">
              <h4 className="text-xs font-semibold uppercase text-muted-foreground">App-attributed DBU</h4>
              <p className="text-[11px] text-muted-foreground">From usage_metadata for resources owned by this app.</p>
              <ul className="text-xs space-y-1">
                <li
                  className="flex items-center justify-between"
                  {...tip('SQL warehouse used by this app for queries.')}
                >
                  <span className="text-muted-foreground">Warehouse</span>
                  <span className="font-medium tabular-nums">{fmtDbu(appAttr?.warehouse_dbu)}</span>
                </li>
                <li
                  className="flex items-center justify-between"
                  {...tip('Producer job (Zerobus path) — null if not configured/visible.')}
                >
                  <span className="text-muted-foreground">Producer job</span>
                  <span className="font-medium tabular-nums">{fmtDbu(appAttr?.producer_job_dbu)}</span>
                </li>
                <li className="flex items-center justify-between" {...tip('Setup / maintenance job DBU.')}>
                  <span className="text-muted-foreground">Setup job</span>
                  <span className="font-medium tabular-nums">{fmtDbu(appAttr?.setup_job_dbu)}</span>
                </li>
                <li className="flex items-center justify-between border-t border-border pt-1">
                  <span className="text-muted-foreground">Total app DBU</span>
                  <span className="font-semibold tabular-nums">{fmtDbu(appAttr?.total_app_dbu)}</span>
                </li>
                <li className="flex items-center justify-between">
                  <span className="text-muted-foreground">Total app est. USD</span>
                  <span className="font-semibold tabular-nums">{fmtUsd(appUsd)}</span>
                </li>
              </ul>
            </div>

            {/* Warehouse share, from query history. The card above credits the
                app with every DBU the warehouse burned, which is only right if
                nothing else uses it. */}
            {whShare && (
              <div className="rounded-lg border border-border p-3 space-y-2 lg:col-span-3">
                <h4 className="text-xs font-semibold uppercase text-muted-foreground">
                  Warehouse share (from query history)
                </h4>
                {whShare.available === false ? (
                  <p className="text-[11px] text-amber-600 dark:text-amber-400">{whShare.note}</p>
                ) : (
                  <>
                    <p className="text-[11px] text-muted-foreground">
                      The card above assumes the warehouse is this app&apos;s alone. If it is shared,
                      this apportions its DBU by each tenant&apos;s share of query time.
                    </p>
                    <ul className="text-xs space-y-1">
                      <li
                        className="flex items-center justify-between"
                        {...tip(
                          `Matched on ${whShare.attribution_key === 'identity+tag'
                            ? "the app service principal's identity and the app:prodline-copilot query tag"
                            : "the app service principal's identity (query tags unavailable on this metastore)"}.`,
                        )}
                      >
                        <span className="text-muted-foreground">App statements</span>
                        <span className="font-medium tabular-nums">
                          {whShare.app_statements ?? '—'}
                          {whShare.app_genie_duration_ms != null && whShare.app_genie_duration_ms > 0 && (
                            <span className="text-muted-foreground"> (incl. Genie)</span>
                          )}
                        </span>
                      </li>
                      <li className="flex items-center justify-between">
                        <span className="text-muted-foreground">Other statements</span>
                        <span className="font-medium tabular-nums">{whShare.other_statements ?? '—'}</span>
                      </li>
                      <li
                        className="flex items-center justify-between"
                        {...tip('App query time ÷ all query time on this warehouse, by total_duration_ms.')}
                      >
                        <span className="text-muted-foreground">
                          App share of query time
                          {whShare.split_age_seconds != null && whShare.split_age_seconds > 60 && (
                            <span className="text-muted-foreground/70">
                              {' '}
                              (cached {Math.round(whShare.split_age_seconds / 60)}m)
                            </span>
                          )}
                        </span>
                        <span className="font-medium tabular-nums">
                          {whShare.app_share == null ? '—' : `${(whShare.app_share * 100).toFixed(1)}%`}
                        </span>
                      </li>
                      <li className="flex items-center justify-between border-t border-border pt-1">
                        <span className="text-muted-foreground">App share of warehouse DBU</span>
                        <span className="font-semibold tabular-nums">{fmtDbu(whShare.app_dbu)}</span>
                      </li>
                      <li className="flex items-center justify-between">
                        <span className="text-muted-foreground">App share est. USD</span>
                        <span className="font-semibold tabular-nums">{fmtUsd(whShare.app_estimated_usd)}</span>
                      </li>
                    </ul>
                    <p className="text-[11px] text-muted-foreground">
                      {whShare.app_share == null
                        ? 'No queries ran on this warehouse in the window, so its DBU cannot be attributed to anyone.'
                        : whShare.note}
                    </p>
                  </>
                )}
              </div>
            )}
          </div>
        )}

        {/* Zerobus row — only in zerobus mode */}
        {!noData && !error && zerobusMode && (
          <div className="rounded-lg border border-border p-3 space-y-2">
            <h4 className="text-xs font-semibold uppercase text-muted-foreground">Zerobus Ingest</h4>
            {(zerobus?.ingested_bytes ?? 0) > 0 ? (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
                <div>
                  <p className="text-muted-foreground">Ingested (window)</p>
                  <p className="font-medium tabular-nums">{zerobus?.ingested_human ?? '—'}</p>
                </div>
                <div>
                  <p className="text-muted-foreground">Estimated DBU</p>
                  <p
                    className="font-medium tabular-nums"
                    {...tip('ingested_GB × 0.143 DBU/GB (Lakeflow Connect standard list rate).')}
                  >
                    {fmtDbu(zerobus?.estimated_dbu)}
                  </p>
                </div>
                <div>
                  <p className="text-muted-foreground">Observed DBU (billing)</p>
                  <p className="font-medium tabular-nums">{fmtDbu(zerobus?.observed_dbu)}</p>
                </div>
                <div>
                  <p className="text-muted-foreground">Estimated USD</p>
                  <p className="font-medium tabular-nums">{fmtUsd(zerobus?.estimated_usd)}</p>
                </div>
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">
                Zerobus is enabled, but ingest volume is not exposed as its own row in
                <code> system.billing.usage</code>. Per Lakeflow Connect pricing, Zerobus charges show up as{' '}
                <span className="font-medium">Jobs Serverless</span> /{' '}
                <span className="font-medium">Automated Serverless</span> DBUs — see them under Components → Jobs / Apps
                above.
              </p>
            )}
          </div>
        )}

        {/* Data processed + scope (billing only) */}
        {!isLiveMode && !noData && !error && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div className="rounded-lg border border-border p-3 space-y-1 text-xs">
              <h4 className="text-xs font-semibold uppercase text-muted-foreground">Data Processed (window)</h4>
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">Total bytes</span>
                <span className="font-medium tabular-nums">{costData?.data_processed?.bytes_human ?? '—'}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">Lines (rows)</span>
                <span className="font-medium tabular-nums">{costData?.data_processed?.records ?? 0}</span>
              </div>
            </div>
            <div className="rounded-lg border border-border p-3 space-y-1 text-xs">
              <h4 className="text-xs font-semibold uppercase text-muted-foreground">Scope</h4>
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">Attribution mode</span>
                <span className="font-medium">{costData?.scope?.attribution_mode ?? '—'}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">Workspace filter</span>
                <span className="font-medium tabular-nums">{costData?.scope?.workspace_id_filter ?? 'none'}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">Warehouse</span>
                <span className="font-medium tabular-nums">{costData?.scope?.app_resources?.warehouse_id ?? '—'}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">Producer job</span>
                <span className="font-medium tabular-nums">
                  {costData?.scope?.app_resources?.producer_job_id ?? '—'}
                </span>
              </div>
            </div>
          </div>
        )}

        {/* Stream observability */}
        <div className="rounded-lg border border-border p-3 space-y-1 text-xs">
          <h4 className="text-xs font-semibold uppercase text-muted-foreground">Stream Observability</h4>
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Last stream payload</span>
            <span className="font-medium tabular-nums">
              {streamLastUpdateTs ? formatTimestamp(streamLastUpdateTs) : '—'}
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Last source event</span>
            <span className="font-medium tabular-nums">
              {sourceLastEventTs ? formatTimestamp(sourceLastEventTs) : '—'}
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Live entities</span>
            <span className="font-medium tabular-nums">
              {machineCount} machines · {eventCount} events
            </span>
          </div>
        </div>
      </div>

      {/* Verification (collapsible) */}
      {costData?.details && (
        <div className="rounded-lg border border-border bg-card p-4 space-y-3">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-sm font-semibold">Verification (raw system.billing.usage)</h3>
              <p className="text-[11px] text-muted-foreground">
                Run on demand. Useful to sanity-check matched bucketing and find unmatched SKUs.
              </p>
            </div>
            <button
              type="button"
              onClick={() => setShowVerification((v) => !v)}
              className="rounded border border-border px-2 py-1 text-[11px] font-medium hover:bg-muted"
            >
              {showVerification ? 'Hide' : 'Show'}
            </button>
          </div>

          {showVerification && (
            <>
              {costData.details.verification_warning && (
                <p className="text-[11px] text-amber-600 dark:text-amber-400">
                  {costData.details.verification_warning}
                </p>
              )}

              <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                <div className="rounded border border-border p-3 space-y-2">
                  <h4 className="text-xs font-semibold uppercase text-muted-foreground">Matched vs Unmatched DBU</h4>
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-muted-foreground">Matched</span>
                    <span className="font-medium tabular-nums">{fmtDbu(matchedDbu)}</span>
                  </div>
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-muted-foreground">Unmatched</span>
                    <span className="font-medium tabular-nums">{fmtDbu(unmatchedDbu)}</span>
                  </div>
                  {matchedRatio != null && (
                    <div className="h-2 rounded bg-muted overflow-hidden">
                      <div className="h-2 bg-primary" style={{ width: `${Math.round(matchedRatio * 100)}%` }} />
                    </div>
                  )}
                  {topUnmatched.length > 0 && (
                    <div className="space-y-1 pt-1">
                      <p className="text-[11px] text-muted-foreground">Top unmatched SKU/unit rows</p>
                      {topUnmatched.map((row) => (
                        <div
                          key={`${row.sku_name}-${row.billing_origin_product}-${row.usage_unit}`}
                          className="rounded border border-border px-2 py-1 text-[11px]"
                        >
                          <p className="font-medium truncate">{row.sku_name || '—'}</p>
                          <p className="text-muted-foreground truncate">
                            {row.billing_origin_product || '—'} · {row.usage_unit || '—'} ·{' '}
                            {(row.usage_quantity ?? 0).toFixed(3)}
                          </p>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                <div className="rounded border border-border p-3 space-y-2">
                  <h4 className="text-xs font-semibold uppercase text-muted-foreground">Hourly DBU trend</h4>
                  {trend.length === 0 ? (
                    <p className="text-xs text-muted-foreground">No hourly data.</p>
                  ) : (
                    <div className="flex items-end gap-0.5 h-24">
                      {trend.map((t) => (
                        <div
                          key={t.hour}
                          className="flex-1 bg-primary/70 rounded-t"
                          style={{ height: `${Math.max(2, (t.dbu / trendMaxDbu) * 100)}%` }}
                          title={`${t.hour}:00 — ${t.dbu.toFixed(3)} DBU`}
                        />
                      ))}
                    </div>
                  )}
                  {trend.length > 0 && (
                    <div className="flex items-center justify-between text-[10px] text-muted-foreground">
                      <span>{trend[0].hour}:00</span>
                      <span>{trend[trend.length - 1].hour}:00</span>
                    </div>
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
